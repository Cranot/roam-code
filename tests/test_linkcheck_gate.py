"""Drift guard: the pre-push landing-page linkcheck gate can actually fail.

``scripts/linkcheck.py`` walks the landing-page tree and reports dead
internal links and dead anchors. Its exit code is opt-in:
``_render_report`` ends ``return 1 if strict else 0``, so WITHOUT
``--strict`` the process prints every issue it found and still exits 0.

``scripts/prepush_check.py`` registers that script as a RELEASE-tier gate,
and ``GateRunner._run`` records PASS on ``returncode == 0``. From the tier's
first commit (2b60ff3a, 2026-06-11) until 2026-08-06 the registered argv
omitted ``--strict``, so the gate could not fail — while CI ran the other
form (``.github/workflows/roam-ci.yml``: ``linkcheck.py --strict``) and the
tier advertised itself as running what CI runs. The divergence survived
because a tree with zero link issues exits 0 under both forms; it is only
visible with an issue present. Measured with one injected dead link:
flagless exited 0, ``--strict`` exited 1.

That is the "gate that cannot fail" shape, and this module pins both halves:

1. The exit-code contract itself — ``_render_report`` with issues returns 1
   under ``strict=True`` and 0 otherwise. If someone "simplifies" that away,
   adding ``--strict`` everywhere would stop meaning anything.
2. The registered argv in ``scripts/prepush_check.py`` still carries
   ``--strict`` (pure AST inspection, no subprocess — mirrors
   ``tests/test_prepush_gate_wired.py``). The AST pin is the half that stops
   the flag being dropped again.
3. The prepush form and the CI form stay the same form, so the tier's
   CI-equivalence claim keeps holding for this gate specifically.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
PREPUSH_PATH = REPO_ROOT / "scripts" / "prepush_check.py"
LINKCHECK_PATH = REPO_ROOT / "scripts" / "linkcheck.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "roam-ci.yml"


def _load_linkcheck() -> ModuleType:
    """Import scripts/linkcheck.py by path (it is a script, not a package)."""
    assert LINKCHECK_PATH.exists(), (
        f"Expected the landing-page linkchecker at {LINKCHECK_PATH}. "
        "It is a RELEASE-tier pre-push gate and a CI doc-hygiene step."
    )
    spec = importlib.util.spec_from_file_location("_linkcheck_under_test", LINKCHECK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _linkcheck_argvs_in_prepush() -> list[list[str]]:
    """Return every subprocess argv literal in prepush that runs linkcheck.py."""
    tree = ast.parse(PREPUSH_PATH.read_text(encoding="utf-8"), filename=str(PREPUSH_PATH))
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        strings = [elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
        if any(s.endswith("scripts/linkcheck.py") or s.endswith("linkcheck.py") for s in strings):
            found.append(strings)
    return found


def test_render_report_exit_code_is_strict_gated() -> None:
    """The exit code is opt-in: issues alone do NOT make the process fail.

    This is the mechanism the argv pin below exists to compensate for. If
    this assertion ever flips (issues fail regardless of ``strict``), the
    flag stops being load-bearing and this guard should be revisited rather
    than silently kept.
    """
    linkcheck = _load_linkcheck()
    pages = [Path("index.html")]

    assert linkcheck._render_report(pages, ["one issue"], strict=True) == 1, (
        "linkcheck._render_report must return 1 when issues exist and strict=True; "
        "that return value is the ONLY thing that can fail the CI doc-hygiene step "
        "and the RELEASE-tier pre-push gate."
    )
    assert linkcheck._render_report(pages, ["one issue"], strict=False) == 0, (
        "Non-strict linkcheck is documented as report-only (scripts/linkcheck.py "
        "usage block). If this now returns non-zero the flag is no longer "
        "load-bearing — re-check whether the argv pin below is still needed."
    )
    assert linkcheck._render_report(pages, [], strict=True) == 0, (
        "A clean tree must exit 0 even under --strict, or the gate fails every push."
    )


def test_prepush_registers_linkcheck_at_all() -> None:
    """The RELEASE tier must still run the linkchecker."""
    argvs = _linkcheck_argvs_in_prepush()
    assert argvs, (
        "scripts/prepush_check.py no longer registers scripts/linkcheck.py. "
        "CONTRIBUTING.md and the pre-push gate design memo document it as a "
        "release gate; CI runs it in the doc-hygiene job either way, so "
        "dropping it locally just moves the failure to after the push."
    )


def test_prepush_linkcheck_argv_passes_strict() -> None:
    """The registered argv must carry --strict, or the gate cannot fail.

    Without the flag ``linkcheck.py`` prints its dead links and exits 0, and
    ``GateRunner._run`` records PASS on ``returncode == 0`` — a gate that
    reports green on a broken tree. This AST pin is what stops the flag
    being dropped again; it was dropped once and survived ~2 months.
    """
    for argv in _linkcheck_argvs_in_prepush():
        assert "--strict" in argv, (
            f"The linkcheck gate argv in scripts/prepush_check.py must include "
            f"'--strict'; found {argv}. Without it linkcheck.py exits 0 no matter "
            "how many dead links it printed (_render_report: `return 1 if strict "
            "else 0`), so the gate records PASS on a broken landing page and the "
            "failure surfaces only in CI, after the push."
        )


def test_prepush_and_ci_run_the_same_linkcheck_form() -> None:
    """Local and CI invocations must not diverge again.

    The RELEASE tier advertises that it runs what CI runs. For this gate that
    claim is only true while both sides pass the same flag, and the two
    drifting apart is precisely the defect this module was written for.
    """
    assert CI_WORKFLOW.exists(), f"Expected the CI workflow at {CI_WORKFLOW}."
    ci_src = CI_WORKFLOW.read_text(encoding="utf-8")
    ci_invocations = re.findall(r"^\s*run:\s*(.*linkcheck\.py.*)$", ci_src, flags=re.MULTILINE)
    assert ci_invocations, (
        "No linkcheck.py invocation found in .github/workflows/roam-ci.yml. If CI "
        "stopped running it, update this guard deliberately — do not delete it."
    )
    for line in ci_invocations:
        assert "--strict" in line, (
            f"CI must run linkcheck.py with --strict, else the CI step cannot fail either: {line!r}"
        )
