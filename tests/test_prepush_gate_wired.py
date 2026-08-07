"""Drift guard: the pre-push structural-gate bundle stays wired.

``scripts/prepush_check.py`` runs the repo-wide structural drift-guards
locally before ``git push`` — the gate that would have prevented this
session's ~14 CI fix-forward cascade (design:
``(internal memo)``). The value of that gate is
entirely in the SET of guards it bundles. If a guard test is renamed or
deleted and silently drops out of the FAST tuple, the pre-push gate quietly
stops catching that drift class — exactly the silent-rot failure mode the
"ship a structural guard with the campaign" rule warns against.

This module pins the contract, and it runs in the FAST bundle itself:

1. ``scripts/prepush_check.py`` exists and parses as valid Python.
2. It exposes the ``FAST_PYTEST_GUARDS`` and ``FULL_PYTEST_GUARDS`` tuples,
   a ``main`` callable, and a ``repo_root`` resolver.
3. Every guard named in either tuple resolves to a real ``tests/`` file —
   so a renamed/deleted guard fails this test instead of silently leaving
   the bundle.
4. The expected high-frequency FAST guards (the dominant fix-forward class)
   are all present.
5. The RELEASE note never disclaims a gate the push path actually ran.

Items 1-4 are pure AST inspection: no subprocess, no index build. Item 5 is
NOT, and the reason is written out at length at its own section below — it was
AST inspection twice, both times bounded by syntax the reader happened to
recognise, and the second bound could not be widened away at all. It now runs
``main()`` with ``subprocess.run`` stubbed and reads the gates that ACTUALLY
ran. Measured cost of that change: three tiers of dry-run, 0.4s wall.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCRIPT_PATH = REPO_ROOT / "scripts" / "prepush_check.py"
TESTS_DIR = REPO_ROOT / "tests"

# The FAST guards the design memo's back-test proved catch the dominant
# structural-drift fix-forward class. The script may carry MORE than these,
# but never fewer — dropping one of these is the regression this guards.
_EXPECTED_FAST_GUARDS = frozenset(
    {
        "test_w547_severity_drift.py",
        "test_law4_lint.py",
        "test_law4_anchor_counts.py",
        "test_w588_fragile_path_drift.py",
        "test_w662_bare_except_drift.py",
        "test_optional_imports_guarded.py",
        "test_findings_detector_count_drift.py",
        "test_detector_registry.py",
        "test_w444_mcp_tool_names_no_dedupe.py",
        "test_w462_landing_page_tool_count_drift.py",
        "test_mcp_server_card_hash.py",
        "test_compound_recipe_registry.py",
        # 2026-07-28: the closed-set registry inventories. Four commits passed
        # the pre-push hook and then took all four CI lanes red because files
        # were added without being registered in these. They are pure
        # file/AST assertions with no index dependency and measured +0.26s on
        # the bundle, so there is no performance argument for dropping them.
        "test_public_allowlist.py",
        "test_workflow_dependency_lock_policy.py",
        "test_publish_provenance_workflow.py",
        "test_composite_action_security.py",
        # 2026-08-07: this file, pinning its own membership. Its docstring
        # claimed it "runs in the FAST bundle itself"; measured, a collect-only
        # over FAST_PYTEST_GUARDS returned 0 node ids from it, so every
        # contract it pins was enforced only by CI, after the push. A guard on
        # the pre-push script that the pre-push script does not run is a claim
        # of local coverage that does not exist.
        "test_prepush_gate_wired.py",
    }
)

HOOK_PATH = REPO_ROOT / ".githooks" / "pre-push"


def _strip_shell_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _parse_script() -> ast.Module:
    assert SCRIPT_PATH.exists(), (
        f"Expected the pre-push gate at {SCRIPT_PATH}. This script runs the "
        "structural drift-guards locally before push; without it the ~14 "
        "fix-forward cascade class is uncaught. See "
        "(internal memo)."
    )
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(SCRIPT_PATH))


def _extract_tuple_strings(tree: ast.Module, name: str) -> list[str]:
    """Return the string literals assigned to module-level ``name`` (a tuple)."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Name) and t.id == name]
        if not targets:
            # Also accept annotated assignment (name: type = (...)).
            continue
        if isinstance(node.value, (ast.Tuple, ast.List)):
            return [
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    # AnnAssign path (FAST_PYTEST_GUARDS: tuple[str, ...] = (...)).
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if isinstance(node.value, (ast.Tuple, ast.List)):
                return [
                    elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    raise AssertionError(f"Could not find a module-level tuple/list literal named {name!r} in {SCRIPT_PATH.name}.")


def test_prepush_script_exists_and_parses() -> None:
    """The script exists and is valid, parseable Python."""
    tree = _parse_script()
    assert isinstance(tree, ast.Module)


def test_prepush_script_exposes_expected_symbols() -> None:
    """The gate exposes the contract symbols other surfaces depend on."""
    tree = _parse_script()
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level_names.add(node.name)
        elif isinstance(node, ast.Assign):
            top_level_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top_level_names.add(node.target.id)

    for required in ("FAST_PYTEST_GUARDS", "FULL_PYTEST_GUARDS", "main", "repo_root"):
        assert required in top_level_names, (
            f"scripts/prepush_check.py must define {required!r}; the .githooks/pre-push "
            f"shim and this drift guard depend on it. Found: {sorted(top_level_names)}"
        )


def test_release_tier_registered() -> None:
    """The pre-tag preflight tier (--release) must stay wired.

    CONTRIBUTING.md + .githooks/pre-push document `prepush_check.py --release`
    as the gate that runs what CI runs before a tag. If the flag is renamed or
    dropped, that documented preflight silently no-ops — the exact gap that let
    the 13.8.0 tag take 8 sequential CI rounds. Import-light source assertion
    (mirrors the other guards in this file — no module import needed).
    """
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    for flag in ("--release", "--full", "--fast"):
        assert flag in src, (
            f"scripts/prepush_check.py must register the {flag!r} tier; "
            ".githooks/pre-push + CONTRIBUTING.md document it as a release gate."
        )


def test_fast_bundle_contains_expected_guards() -> None:
    """Every high-frequency FAST guard from the design back-test is bundled."""
    tree = _parse_script()
    fast = set(_extract_tuple_strings(tree, "FAST_PYTEST_GUARDS"))
    missing = _EXPECTED_FAST_GUARDS - fast
    assert not missing, (
        f"FAST_PYTEST_GUARDS dropped expected guard(s): {sorted(missing)}. "
        "These are the dominant structural-drift fix-forward class (severity-rank, "
        "LAW-4, fragile-path, card-hash, detector-count, compound-recipe). Re-add "
        "them or update (internal memo) if intentionally removed."
    )


def _driver_whole_tree_leak_invocations(tree: ast.Module) -> int:
    """Count subprocess argv literals that run the scanner in ``--all`` mode."""
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        strings = {elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
        if "--all" in strings and any(s.endswith("scan_internal_language.py") for s in strings):
            found += 1
    return found


def test_whole_tree_leak_scan_runs_exactly_once_in_the_push_path() -> None:
    """The whole-tree anti-leak scan must run — and must run only once.

    Both halves of this matter and they pull in opposite directions:

    * It must RUN. The hook's other two leak gates scope themselves to the
      pushed ref updates, which by construction cannot see a leak sitting in
      a file this push does not touch — the case a GROWN pattern catalogue
      creates. Whole-tree is the only scope that catches that, so this gate
      may never be rescoped to a range to save time. The 2026-05-20 incident
      (a customer name reaching the public repo, history purged by
      force-push) is what it exists to prevent.
    * It must run ONCE. Until 2026-07-28 ``.githooks/pre-push`` ran a
      byte-identical ``--all`` invocation of its own moments before handing
      off to ``prepush_check.py``, over the same unchanged tree. Measured:
      36.6s, roughly a third of the whole hook, for zero extra coverage.

    Pinning both ends means neither a well-meaning "add a backstop" nor a
    well-meaning "make it faster" can quietly restore the waste or remove
    the protection.
    """
    driver_count = _driver_whole_tree_leak_invocations(_parse_script())
    assert driver_count == 1, (
        f"scripts/prepush_check.py should run the whole-tree "
        f"`scan_internal_language.py --all` gate exactly once; found {driver_count}. "
        "It is the single whole-tree pass in the push path (see _run_leak_gate)."
    )

    hook_src = _strip_shell_comments(HOOK_PATH.read_text(encoding="utf-8"))
    offenders = [
        line.strip() for line in hook_src.splitlines() if "scan_internal_language.py" in line and "--all" in line
    ]
    assert not offenders, (
        "`.githooks/pre-push` must NOT run `scan_internal_language.py --all` itself. "
        f"Found: {offenders}. That invocation is byte-identical to the one "
        "scripts/prepush_check.py already runs seconds later over the same tree, "
        "and cost a measured 36.6s per push for no additional coverage. The hook's "
        "own leak gates are the sub-second --pre-push-updates ones."
    )


def test_hook_still_delegates_the_whole_tree_scan() -> None:
    """Removing the duplicate must not have removed the protection.

    The dedupe above is only safe because the hook still runs the driver,
    which still runs the whole-tree scan. If the hook ever stops delegating,
    the push path loses the whole-tree leak gate entirely — a silent
    downgrade that would look like a speed-up.
    """
    hook_src = _strip_shell_comments(HOOK_PATH.read_text(encoding="utf-8"))
    assert "scripts/prepush_check.py" in hook_src, (
        ".githooks/pre-push must still invoke scripts/prepush_check.py: that is now "
        "the ONLY thing running the whole-tree anti-leak scan on the push path."
    )


# ---------------------------------------------------------------------------
# The RELEASE note must not disclaim a gate the push path just ran
# ---------------------------------------------------------------------------
#
# WHAT REPLACED WHAT, AND WHY, because the previous mechanism was not wrong so
# much as bounded, and the bound was invisible from inside it.
#
# The note printed on a green RELEASE run lists CI lanes that "are NOT run by
# any tier and stay unproven here". One entry was false: it named a gate
# `main()` runs unconditionally. The first guard read `main()` with the AST,
# collected the labels of the gates it could see, and asserted none of them
# appeared in a bullet. It was widened twice. It was still bounded by syntax:
#
#   * a keyword-argument call, an aliased receiver, a starred argv, a
#     non-literal label, a module-level helper, and an inline
#     `results.append(...)` all evaded it -- six shapes found in ONE probing
#     session, with no terminating condition on the search;
#   * two of those shapes are LIVE in this tree. `run_pytest_bundle` builds its
#     label with an f-string, so a live `--fast` run recorded 8 gates and the
#     extractor could name 7. `run_release_temp_capacity_gate` appends its
#     GateResult inline and was never readable at all;
#   * and the shape that ends the argument: a correctly-written positional
#     literal placed under `if release:`. The extractor deliberately skips
#     tier-conditional statements, so it reads that as "not unconditional" and
#     passes -- while the note it certifies is printed ONLY in the RELEASE
#     tier. No widening of a READER fixes that, because the defect is in the
#     predicate ("unconditional") rather than in the parsing. That is also the
#     most likely real commit: `roam compatibility --ci` is a heavyweight lane,
#     so the natural place to wire it is the release branch.
#
# `runner.results` holds the answer exactly, with zero syntactic knowledge, at
# the moment `_print_summary` prints the note -- it is already that function's
# first parameter. So the note is now DERIVED from it (see
# `_RELEASE_UNPROVEN_LANES` and `_release_note_lines` in the script) rather
# than detected after the fact, and this module tests the derivation by RUNNING
# the script with `subprocess.run` stubbed. Measured: all eight wiring shapes
# above self-correct, including the tier-placement one; three tiers of dry-run
# cost 0.4s wall.
#
# RESIDUAL, stated rather than implied, because "narrow the claim" repeated is
# not a fix:
#
#   1. A bullet is related to a gate by SUBSTRING. That relation is still
#      hand-maintained -- it is now one explicit probe string per bullet
#      instead of an open-ended AST grammar, which is smaller but not zero.
#   2. This closes only the FALSE-DISCLAIMER direction. A CI lane added to
#      .github/workflows and never disclaimed at all is a MISSING-disclaimer
#      defect that no runtime record can see, because the gate never ran. That
#      is a genuinely different guard, over workflow YAML, and it does not
#      exist. `_RELEASE_UNPROVEN_LANES` is still enumerated by hand against
#      .github/workflows (last checked 2026-08-07).
#   3. A red gate truncates the record. That is correct for a note describing
#      what THIS run proved, but it means the note must be computed in-process
#      at print time and cannot be reconstructed later.
#   4. AGENTS.md holds a fourth hand-maintained copy of the lane list.


def _load_script_module(source: str | None = None, name: str = "prepush_under_test"):
    """Import ``scripts/prepush_check.py`` (or a mutated copy) as a module.

    Loaded from a temp path rather than imported normally so a mutation can be
    exercised without touching the tree. ``sys.modules`` registration is
    required: the module defines a ``@dataclass``, and dataclasses resolves
    annotations through ``sys.modules[cls.__module__]``.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8") if source is None else source
    tmp = Path(tempfile.mkdtemp(prefix="prepush-guard-")) / f"{name}.py"
    tmp.write_text(text, encoding="utf-8", newline="")
    spec = importlib.util.spec_from_file_location(name, tmp)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _dry_run_environment():
    """Every gate reports rc=0 and the capacity gate sees infinite disk.

    Stubbing ``subprocess.run`` cannot bias which gate NAMES appear:
    ``GateRunner._run`` appends its ``GateResult`` unconditionally, BEFORE any
    branch on ``returncode``. Only the pass flag depends on the subprocess.

    ``shutil.disk_usage`` is stubbed for a different reason: the release
    capacity gate is not a subprocess, and on a host below its threshold it
    fails and short-circuits ``main()`` before the note is ever printed -- so
    without this the test would silently measure nothing on exactly the boxes
    most likely to run it.
    """
    real_run, real_usage = subprocess.run, shutil.disk_usage
    subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
        a[0] if a else [], returncode=0, stdout=b"", stderr=b""
    )
    shutil.disk_usage = lambda *_a, **_k: os.terminal_size((0, 0)) and _FakeUsage()
    try:
        yield
    finally:
        subprocess.run, shutil.disk_usage = real_run, real_usage


class _FakeUsage:
    total = 1 << 41
    used = 0
    free = 1 << 40


def _run_tier(module, argv: list[str]) -> str:
    buf = io.StringIO()
    with _dry_run_environment(), contextlib.redirect_stdout(buf):
        module.main(argv)
    return buf.getvalue()


def _gate_names(output: str) -> list[str]:
    return [line.split(": ", 1)[0].removeprefix("[prepush] ") for line in output.splitlines() if ": PASS" in line]


_COMPAT_BULLET = "roam compatibility --ci / --require-coverage"
_ANCHOR = "    runner._run_leak_gate()\n"
_COMPAT_CALL = '    runner._run("roam compatibility --ci", [sys.executable, "-c", ""], fix_hint="")\n'

# One entry per wiring shape the retired AST extractor could not read. The
# names are the point: each is a real way to wire a gate, and the guard must
# not care which was used.
_EVADING_WIRINGS: dict[str, str] = {
    "positional-literal": _COMPAT_CALL,
    "keyword-arguments": (
        '    runner._run(name="roam compatibility --ci", argv=[sys.executable, "-c", ""], fix_hint="")\n'
    ),
    "aliased-receiver": "    _r = runner\n" + _COMPAT_CALL.replace("runner.", "_r."),
    "starred-argv": (
        '    _argv = ("roam compatibility --ci", [sys.executable, "-c", ""])\n    runner._run(*_argv, fix_hint="")\n'
    ),
    "non-literal-label": (
        '    _label = "roam compatibility" + " --ci"\n'
        '    runner._run(_label, [sys.executable, "-c", ""], fix_hint="")\n'
    ),
    "inline-results-append": (
        '    runner.results.append(GateResult(name="roam compatibility --ci", passed=True, seconds=0.0))\n'
    ),
}


def test_release_note_is_honest_on_the_real_script() -> None:
    """Positive control: the note still discloses the lanes that ARE unproven.

    Without this, a derivation that suppressed everything would pass every
    test below. The disclosure is the product; the suppression is the
    correction.
    """
    module = _load_script_module()
    out = _run_tier(module, ["--release"])
    assert "NOT run by any tier" in out
    assert f"- {_COMPAT_BULLET}" in out, "the compatibility lane genuinely does not run in any tier today"
    assert "no longer unproven" not in out, "nothing should be suppressed on the unmutated tree"


def test_no_disclaimed_bullet_names_a_gate_that_ran(monkeypatch) -> None:
    """The whole claim, on every tier, from the runtime record.

    This is the assertion the AST guard was approximating. It reads the gates
    that actually ran rather than the gates a parser could recognise, so it
    holds for wirings nobody has thought of yet.
    """
    module = _load_script_module()
    for argv, tier in (([], "FAST"), (["--full"], "FULL"), (["--release"], "RELEASE")):
        out = _run_tier(module, argv)
        ran = _gate_names(out)
        assert ran, f"{tier}: no gates recorded -- the dry-run harness measured nothing"
        disclaimed = [line.split("- ", 1)[1].strip() for line in out.splitlines() if line.strip().startswith("- ")]
        for bullet in disclaimed:
            for probe, text in module._RELEASE_UNPROVEN_LANES:
                if text != bullet:
                    continue
                hit = next((name for name in ran if probe in name), None)
                assert hit is None, f"{tier}: note disclaims {bullet!r} but {hit!r} ran"


@pytest.mark.parametrize("shape", sorted(_EVADING_WIRINGS))
def test_note_self_corrects_for_every_wiring_shape(shape: str) -> None:
    """Each shape the retired extractor could not read must self-correct.

    Not "must be detected" -- must be IMPOSSIBLE. The bullet is not printed as
    unproven because the gate ran, and the correction is printed so the
    operator sees a measurement rather than a shortened list.
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert _ANCHOR in source, "main() no longer calls _run_leak_gate() unconditionally; re-anchor this mutation"
    module = _load_script_module(
        source.replace(_ANCHOR, _ANCHOR + _EVADING_WIRINGS[shape], 1), name=f"prepush_{shape.replace('-', '_')}"
    )
    out = _run_tier(module, ["--release"])
    assert f"- {_COMPAT_BULLET}" not in out, f"{shape}: the note still disclaims a gate this run executed"
    assert "no longer unproven" in out and _COMPAT_BULLET in out, f"{shape}: suppressed silently, with no stated cause"


def test_note_self_corrects_for_a_gate_wired_under_the_release_branch() -> None:
    """The shape that ends the treadmill argument.

    Correct syntax, correct label, wrong TIER. The retired extractor skipped
    tier-conditional statements by design, so it read this as "not
    unconditional" and passed -- while the note it certified is printed only
    in the RELEASE tier. No amount of widening a source reader reaches this;
    the runtime record has it for free.
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    anchor = '        runner._run(\n            "commit-message leak scan'
    assert anchor in source, "the release branch's first gate moved; re-anchor this mutation"
    module = _load_script_module(
        source.replace(anchor, _COMPAT_CALL.replace("    runner", "        runner") + anchor, 1),
        name="prepush_release_conditional",
    )
    out = _run_tier(module, ["--release"])
    assert f"- {_COMPAT_BULLET}" not in out
    assert "no longer unproven" in out


def test_every_disclaimed_lane_has_a_distinct_probe() -> None:
    """The residual is a substring relation; keep it from silently overlapping.

    Two bullets whose probes match the same gate name would suppress together,
    which is a false clean on one of them. Cheap to assert, and it is the one
    hand-maintained part of the new mechanism.
    """
    module = _load_script_module()
    probes = [probe for probe, _ in module._RELEASE_UNPROVEN_LANES]
    assert len(probes) == len(set(probes)), f"duplicate probes: {probes}"
    for a in probes:
        overlapping = [b for b in probes if b != a and (a in b or b in a)]
        assert not overlapping, f"probe {a!r} overlaps {overlapping} -- one gate would suppress two bullets"


def test_every_bundled_guard_file_exists() -> None:
    """No bundled guard may reference a renamed/deleted test file."""
    tree = _parse_script()
    bundled = set(_extract_tuple_strings(tree, "FAST_PYTEST_GUARDS"))
    bundled |= set(_extract_tuple_strings(tree, "FULL_PYTEST_GUARDS"))
    missing = sorted(name for name in bundled if not (TESTS_DIR / name).exists())
    assert not missing, (
        f"prepush_check.py bundles test file(s) that do not exist: {missing}. "
        "A renamed or deleted guard silently drops out of the pre-push bundle. "
        "Update FAST_PYTEST_GUARDS / FULL_PYTEST_GUARDS to the new name."
    )
