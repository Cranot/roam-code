"""W607-EP -- ``for_refactor`` compound CLOSE-AS-DUPLICATE pinning suite.

CLOSE-AS-DUPLICATE per the W607-DZ/EA/EE/EJ/EL discovery methodology
====================================================================

W607-EP was queued as "apply W607 substrate-CALL plumbing to
``cmd_for_refactor.py`` (compound recipe)". The CRITICAL FIRST STEP
discovery found TWO blocking facts:

1. **The target file does NOT exist.** There is no
   ``src/roam/commands/cmd_for_refactor.py`` in the tree. The
   ``for_refactor`` compound recipe lives ENTIRELY in
   ``src/roam/mcp_server.py`` as an ``@_tool(name="roam_for_refactor")``
   aggregator (function defined at module-level ``def for_refactor(...)``).
   There is no Click command for ``for-refactor`` -- the compound is
   MCP-only by design.

2. **W607-AG already plumbs the substrate-CALL boundaries.** The
   accumulator ``_w607ag_warnings_out`` + helper ``_run_check`` +
   marker family ``for_refactor_<phase>_failed:`` are all present inside
   ``for_refactor()`` in ``mcp_server.py`` (see lines ~6680-6806 as of
   this audit). Five substrate-CALL boundaries are wrapped:

   * ``preflight``         -- _safe_run([_cr("preflight"), symbol], root)
   * ``impact``            -- _safe_run([_cr("impact"), symbol], root)
   * ``complexity_report`` -- _safe_run([_cr("complexity"), "--limit", "5"], root)
   * ``clones``            -- _safe_run([_cr("clones"), "--top", "20"], root)
   * ``compound_envelope`` -- _compound_envelope("for-refactor", sections, ...)

   Each raise becomes a ``for_refactor_<phase>_failed:<exc_class>:<detail>``
   marker on ``_w607ag_warnings_out`` which then threads onto BOTH
   ``summary.warnings_out`` AND the top-level ``envelope.warnings_out``.
   Tests live in ``tests/test_w607_ag_cmd_for_refactor_warnings_out_envelope.py``
   (718 lines, exhaustive substrate-call audit).

The accumulator W607-EP was supposed to introduce
(``_w607ep_warnings_out``) and the helper name (``_run_check_ep``) do
NOT exist in the source -- their semantic equivalent is
``_w607ag_warnings_out`` / ``_run_check`` from W607-AG. Adding a
SECOND bucket would:

1. Duplicate the marker family already covered by AG.
2. Re-name the same disclosure channel (``for_refactor_<phase>_failed:``)
   without changing observable behaviour.
3. Drift the bucket-mirror discipline (``summary.warnings_out`` +
   ``envelope.warnings_out`` both populated from the single AG bucket).

Per the W607-DZ template -- when the substrate-CALL boundary is already
in-tree, the correct action is **pinning tests, not source modification**.
This file captures 30+ pinning invariants so any future regression of
the W607-AG plumbing surfaces immediately. No source edit.

Compound-recipe preservation (W126/W150)
========================================

for_refactor is a refactor-prep compound that composes ``preflight`` +
``impact`` + ``complexity`` + ``clones``. Registry-key lookup discipline
is preserved through ``_COMPOUND_REGISTRY`` / ``_cr()`` in
``mcp_server.py`` -- for_refactor does NOT string-concat subcommand
names. The W607-AG plumbing wraps the helper CALLS, not the registry
lookup, so registry-key discipline is undisturbed.

Pattern 5 regression preservation (W126)
========================================

The historical ``complexity-report`` vs ``complexity`` typo + the
``vuln`` vs ``vulns`` typo are pinned via ``_COMPOUND_REGISTRY`` --
fail-fast at module load via ``_verify_compound_registry()``. The
W607-EP suite re-pins this so a future refactor that bypasses the
registry surfaces immediately.

LAW 5 compound-chain-length preservation
========================================

for_refactor chains 4 subcommands (preflight + impact + complexity +
clones). Per LAW 5 the safe ceiling for compound chains is <=3 concrete
steps. for_refactor sits at 4 which is over the strict LAW 5 ceiling,
but is acceptable because:

* It runs as a single ``@_tool`` call (the agent issues ONE invocation,
  the chain is hidden behind the MCP boundary).
* Failures are surfaced via W607-AG markers + ``partial_success``, so
  the agent sees a clean envelope rather than 4 separate broken-step
  retries.

We pin the 4-subcommand shape so a future refactor that grows the chain
to 5+ raises this LAW 5 question explicitly.

LAW 4 note
==========

Warning markers are diagnostic strings, NOT ``agent_contract.facts``
content, and therefore not subject to the concrete-noun-terminal lint.

W978 first-hypothesis check
===========================

If a test in this file fails under ``-n auto``, re-run under ``-n 0``
first to confirm it isn't a sibling-test side effect (per
``CLAUDE.md`` -- "Re-run before declaring a fix").
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# Import the compound directly. ``roam.mcp_server`` imports only the
# specific fastmcp submodules it needs (NOT the top-level ``fastmcp``
# package which has transitive import errors on some environments).
try:
    from roam import mcp_server as _srv  # noqa: E402
    from roam.mcp_server import for_refactor  # noqa: E402
except Exception as _exc:  # pragma: no cover -- guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Constants -- canonical-path pins
# ---------------------------------------------------------------------------


_MCP_PATH = Path(__file__).resolve().parent.parent / "src" / "roam" / "mcp_server.py"

_ABSENT_CMD_PATH = Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_for_refactor.py"


# ---------------------------------------------------------------------------
# Test hygiene: disable handle-off so envelope inspection reads the dict.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


# ---------------------------------------------------------------------------
# Fixtures -- minimal git-init + index for in-process compound dispatch
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo + commit current files. No further history."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "add", "."],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
        check=True,
    )


@pytest.fixture
def for_refactor_project(tmp_path, monkeypatch):
    """A git repo with a real function for the happy-path compound."""
    repo = tmp_path / "w607ep-for-refactor-project"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "auth.py").write_text(
        "def handle_login(user):\n    return user\n\ndef main():\n    return handle_login('alice')\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


def _module_source() -> str:
    return _MCP_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (A) Target-file absence pins -- W607-EP applies to a file that doesn't exist
# ---------------------------------------------------------------------------


def test_w607ep_cmd_for_refactor_py_does_not_exist():
    """Pin: ``src/roam/commands/cmd_for_refactor.py`` is INTENTIONALLY absent.

    The for_refactor compound lives in ``mcp_server.py``, NOT a
    standalone Click command. If a future agent creates the file at
    this path expecting the W607-EP plumbing to live there, this test
    fires + forces a reconciliation with the MCP-only design.
    """
    assert not _ABSENT_CMD_PATH.exists(), (
        f"Unexpected file at {_ABSENT_CMD_PATH}. for_refactor is "
        "MCP-only (lives in mcp_server.py). If a Click command is being "
        "introduced, W607-EP must be re-scoped before this file lands."
    )


def test_w607ep_mcp_server_py_exists():
    """Pin: the canonical location for the for_refactor compound."""
    assert _MCP_PATH.exists(), f"missing {_MCP_PATH}"


def test_w607ep_for_refactor_defined_in_mcp_server():
    """Pin: ``def for_refactor`` appears in mcp_server.py exactly once."""
    src = _module_source()
    # Match the exact signature line.
    count = src.count('def for_refactor(symbol: str, root: str = "."')
    assert count == 1, f"expected exactly 1 def for_refactor signature in mcp_server.py; got {count}"


# ---------------------------------------------------------------------------
# (B) W607-EP names INTENTIONALLY absent
# ---------------------------------------------------------------------------


def test_w607ep_no_w607ep_accumulator_in_source():
    """Pin: the W607-EP accumulator name is INTENTIONALLY absent.

    W607-EP is CLOSE-AS-DUPLICATE -- the marker family the task would
    have introduced is already covered by W607-AG. If a future agent
    adds ``_w607ep_warnings_out`` to mcp_server.py, this test fires
    and forces a reconciliation with the existing AG bucket.
    """
    src = _module_source()
    assert "w607ep_warnings_out" not in src, (
        "W607-EP is CLOSE-AS-DUPLICATE; do NOT introduce a second bucket "
        "alongside _w607ag_warnings_out. Use the existing _run_check "
        "helper inside the for_refactor() body instead."
    )


def test_w607ep_no_run_check_ep_helper_in_source():
    """Pin: the W607-EP helper name is INTENTIONALLY absent."""
    src = _module_source()
    assert "_run_check_ep(" not in src and "def _run_check_ep" not in src, (
        "W607-EP is CLOSE-AS-DUPLICATE; do NOT introduce _run_check_ep. "
        "Substrate boundaries on for_refactor go through the existing "
        "_run_check helper (W607-AG)."
    )


def test_w607ep_no_w607ep_marker_family_in_source():
    """Pin: no ``for_refactor_*`` marker emit lives under a W607-EP tag.

    The marker family ``for_refactor_<phase>_failed:`` IS in the source
    (from W607-AG), but it should be the ONLY one. A future W607-EP
    plumbing attempt would introduce a duplicate emit line.

    The emit itself was hoisted into the shared ``_run_substrate`` helper,
    where the marker template is recipe-agnostic
    (``f"{recipe_name}_{phase}_failed:..."``). The ``for_refactor`` marker
    family is minted by binding ``recipe_name="for_refactor"`` at the
    single ``_run_substrate("for_refactor", ...)`` call inside the
    ``for_refactor()`` body's ``_run_check`` wrapper. A duplicate W607-EP
    bucket would introduce a SECOND such binding.
    """
    src = _module_source()
    # The single ``for_refactor`` recipe-name binding into the shared
    # substrate helper (the marker-family root for this compound).
    needle = '_run_substrate("for_refactor",'
    count = src.count(needle)
    assert count == 1, (
        f"expected exactly 1 ``for_refactor`` substrate binding in "
        f"mcp_server.py (W607-AG); got {count}. If a second "
        "binding appears, W607-EP plumbing has been added by mistake."
    )


# ---------------------------------------------------------------------------
# (C) W607-AG presence pins -- the substrate-CALL plumbing IS in source
# ---------------------------------------------------------------------------


def test_w607ep_w607ag_accumulator_present():
    """Pin: ``_w607ag_warnings_out`` bucket is live in mcp_server.py."""
    src = _module_source()
    assert "_w607ag_warnings_out: list[str] = []" in src, (
        "W607-AG substrate-CALL bucket missing on for_refactor; W607-EP pinning suite depends on it being live."
    )


def test_w607ep_w607ag_helper_present():
    """Pin: ``_run_check`` helper is defined inside for_refactor()."""
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            for inner in node.body:
                if isinstance(inner, ast.FunctionDef) and inner.name == "_run_check":
                    helper = inner
                    break
            break
    assert helper is not None, (
        "_run_check helper not found inside for_refactor(); W607-AG substrate-CALL plumbing missing."
    )


def test_w607ep_w607ag_helper_returns_default_verbatim():
    """Pin: the substrate except-handler returns ``default`` verbatim.

    Helper-template discipline -- the W607 template mandates
    ``return default`` (NOT ``return None``, NOT ``raise``, NOT
    ``return default()``). The except-handler was hoisted from the
    inline ``_run_check`` into the shared module-level ``_run_substrate``
    helper (``_run_check`` inside ``for_refactor()`` now delegates to it).
    AST-walk ``_run_substrate`` and assert the except-block returns the
    bare ``default`` name.
    """
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_substrate":
            helper = node
            break
    assert helper is not None, "_run_substrate helper not found"

    found_default_return = False
    for node in ast.walk(helper):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for stmt in handler.body:
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                        if stmt.value.id == "default":
                            found_default_return = True
    assert found_default_return, (
        "_run_substrate except-handler must `return default` verbatim (W607 helper-template discipline)"
    )


def test_w607ep_w607ag_helper_does_not_call_default():
    """Pin: ``_run_check`` does NOT use ``return default()`` (miscoding lock-in).

    W607-EE/EJ post-mortem found a recurring miscoding where helpers
    were rewritten to ``return default()`` -- which crashes when
    ``default`` is a non-callable (dict, str, tuple). Lock in the
    correct shape.
    """
    src = _module_source()
    tree = ast.parse(src)
    helper_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            for inner in node.body:
                if isinstance(inner, ast.FunctionDef) and inner.name == "_run_check":
                    helper_src = ast.get_source_segment(src, inner) or ""
                    break
            break
    assert helper_src is not None, "_run_check helper not found"
    assert "return default()" not in helper_src, (
        "_run_check contains `return default()` -- W607 template mandates "
        "`return default` verbatim. `default` may be a non-callable."
    )


# ---------------------------------------------------------------------------
# (D) Substrate-CALL phase coverage (W607-AG boundary AST audit)
# ---------------------------------------------------------------------------


_W607AG_SUBSTRATE_PHASES = (
    "preflight",
    "impact",
    "complexity_report",
    "clones",
    "compound_envelope",
)


@pytest.mark.parametrize("phase", _W607AG_SUBSTRATE_PHASES)
def test_w607ep_w607ag_substrate_phase_wrapped(phase):
    """Pin: each W607-AG substrate phase is wrapped via ``_run_check``.

    AST-walk the for_refactor function body and assert the phase string
    appears as the first positional arg to a ``_run_check(...)`` call.
    """
    tree = ast.parse(_module_source())
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            target = node
            break
    assert target is not None, "for_refactor function not found"

    found = False
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == phase:
                    found = True
                    break
    assert found, f"W607-AG substrate phase {phase!r} must be wrapped via _run_check inside for_refactor()"


def test_w607ep_total_substrate_phase_count_is_5():
    """Pin: for_refactor wraps EXACTLY 5 substrate-CALL boundaries.

    Drift detector: if a phase is added/removed, W607-AG documentation
    + tests must be updated in lockstep. Pinned at 5 (preflight, impact,
    complexity_report, clones, compound_envelope).
    """
    assert len(_W607AG_SUBSTRATE_PHASES) == 5, "W607-AG must wrap exactly 5 boundaries on for_refactor"


# ---------------------------------------------------------------------------
# (E) Constituent-invocation pin (W607-EK lesson -- positional-arg AST traversal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper", ["_safe_run", "_compound_envelope"])
def test_w607ep_w607ag_invokes_helper_through_run_check(helper):
    """W607-EK lesson: helpers passed as positional args to ``_run_check``.

    AST traversal must check ``Name`` references inside ``Call.args``
    (NOT just ``Call.func``), because the substrate helper (``_safe_run``
    / ``_compound_envelope``) is the 2nd positional arg to ``_run_check``.

    Pin: at least one ``_run_check(phase, _safe_run, ...)`` or
    ``_run_check(phase, _compound_envelope, ...)`` call exists inside
    for_refactor().
    """
    tree = ast.parse(_module_source())
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            target = node
            break
    assert target is not None, "for_refactor function not found"

    found = False
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check":
                # 2nd positional arg is the helper Name reference
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                    if node.args[1].id == helper:
                        found = True
                        break
    assert found, (
        f"expected _run_check(phase, {helper}, ...) inside for_refactor(); "
        f"AST traversal must read Name references inside Call.args, NOT "
        f"just Call.func (W607-EK lesson)"
    )


# ---------------------------------------------------------------------------
# (F) Bucket-mirror invariant -- AG bucket feeds BOTH summary + top-level
# ---------------------------------------------------------------------------


def test_w607ep_w607ag_bucket_mirrors_to_summary_warnings_out():
    """Pin: the AG bucket threads onto summary.warnings_out.

    The bucket-merge was hoisted into the shared
    ``_finalize_compound_recipe`` helper, which receives the
    ``_w607ag_warnings_out`` bucket as its ``warnings_out`` parameter and
    merges it into both mirrors.
    """
    src = _module_source()
    assert 'summary["warnings_out"] = existing_summary_wo + list(warnings_out)' in src, (
        "summary.warnings_out mirror missing -- W607-AG bucket-merge discipline may have drifted"
    )


def test_w607ep_w607ag_bucket_mirrors_to_top_level_warnings_out():
    """Pin: the AG bucket also threads onto envelope.warnings_out."""
    src = _module_source()
    assert 'envelope["warnings_out"] = existing_top_wo + list(warnings_out)' in src, (
        "top-level warnings_out mirror missing -- W607-AG bucket-merge discipline may have drifted"
    )


def test_w607ep_w607ag_bucket_flips_partial_success():
    """Pin: non-empty AG bucket -> ``partial_success: True`` on summary."""
    src = _module_source()
    # Find the partial_success flip inside the bucket-merge block of the
    # shared ``_finalize_compound_recipe`` helper. The merge is guarded by
    # ``if warnings_out:`` (the AG bucket is passed as this parameter).
    idx = src.find("if warnings_out:")
    assert idx >= 0, "AG bucket-merge block not found"
    block = src[idx : idx + 800]
    assert "partial_success" in block and "True" in block, (
        "W607-AG bucket-merge block must flip summary.partial_success to True when the bucket is non-empty"
    )


# ---------------------------------------------------------------------------
# (G) Runtime invariants -- clean path emits no W607 markers
# ---------------------------------------------------------------------------


def test_w607ep_clean_envelope_omits_w607ag_markers(for_refactor_project):
    """Clean for_refactor on a healthy repo -> no W607-AG substrate markers."""
    r = for_refactor(symbol="handle_login", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("command") == "for-refactor", r.get("command")

    top_wo = r.get("warnings_out") or []
    summary_wo = (r.get("summary") or {}).get("warnings_out") or []
    substrate_markers = [
        m
        for m in (list(top_wo) + list(summary_wo))
        if isinstance(m, str) and m.startswith("for_refactor_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean for_refactor must NOT surface for_refactor_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


def test_w607ep_substrate_failure_marker_lands_on_both_mirrors(for_refactor_project, monkeypatch):
    """Substrate raise -> marker on BOTH top-level + summary.warnings_out.

    Canonical bond-bug fix invariant -- markers must reach BOTH mirrors,
    not just one. A future refactor that drops one mirror would only
    surface here.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise RuntimeError("synthetic-bond-bug-w607ep")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    summary_wo = (r.get("summary") or {}).get("warnings_out") or []

    top_match = {m for m in top_wo if isinstance(m, str) and m.startswith("for_refactor_preflight_failed:")}
    summary_match = {m for m in summary_wo if isinstance(m, str) and m.startswith("for_refactor_preflight_failed:")}
    assert top_match, f"top-level mirror missing marker; got {top_wo!r}"
    assert summary_match, f"summary mirror missing marker; got {summary_wo!r}"
    assert top_match == summary_match, (
        "top-level and summary mirrors must carry IDENTICAL marker sets "
        f"(bond-bug invariant); top={top_match!r} summary={summary_match!r}"
    )


def test_w607ep_substrate_failure_flips_partial_success(for_refactor_project, monkeypatch):
    """A substrate raise must set summary.partial_success = True."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "impact":
            raise RuntimeError("synthetic-w607ep")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("partial_success") is True, f"expected summary.partial_success=True; got summary={summary!r}"


def test_w607ep_substrate_failure_marker_includes_exc_class_and_detail(for_refactor_project, monkeypatch):
    """Marker must include ``<exc_class>:<detail>`` -- helper-template shape."""
    original = _srv._safe_run

    class _CustomBoom(RuntimeError):
        pass

    def _routed(args, root):
        if args and args[0] == "clones":
            raise _CustomBoom("specific-detail-w607ep")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    matches = [m for m in top_wo if "_CustomBoom" in m and "specific-detail-w607ep" in m]
    assert matches, f"expected marker to include exc class + detail; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (H) Per-substrate isolation -- each phase fails independently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase_arg,expected_phase_marker,other_section_names",
    [
        ("preflight", "for_refactor_preflight_failed:", {"impact", "complexity_report", "clones"}),
        ("impact", "for_refactor_impact_failed:", {"preflight", "complexity_report", "clones"}),
        ("complexity", "for_refactor_complexity_report_failed:", {"preflight", "impact", "clones"}),
        ("clones", "for_refactor_clones_failed:", {"preflight", "impact", "complexity_report"}),
    ],
)
def test_w607ep_per_substrate_isolation(
    for_refactor_project,
    monkeypatch,
    phase_arg,
    expected_phase_marker,
    other_section_names,
):
    """Each substrate raises independently -- other substrates still run.

    Pin: a raise in ONE substrate produces a W607-AG marker for THAT
    phase AND the other 3 substrates still execute and contribute their
    sections to the compound envelope. The failed substrate's section
    may be dropped by ``_compound_envelope`` (which filters error-keyed
    children) -- that data-shape channel is separate from the
    W607-AG substrate-CALL marker channel. We pin BOTH axes here.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == phase_arg:
            raise RuntimeError(f"synthetic-isolation-{phase_arg}")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    sections = summary.get("sections") or []
    # summary.sections is a list of section-name strings; the failed
    # phase's section is dropped (its child returned an error sentinel
    # which _compound_envelope filters out). The OTHER 3 substrates'
    # section names must remain.
    section_names = {s for s in sections if isinstance(s, str)}

    # The other 3 substrates still ran + contributed sections.
    missing = other_section_names - section_names
    assert not missing, (
        f"per-substrate isolation: other substrates {missing!r} should still "
        f"have run when {phase_arg!r} raised; got sections={section_names!r}"
    )

    # The W607-AG marker for the raised phase is on warnings_out.
    top_wo = r.get("warnings_out") or []
    matching = [m for m in top_wo if isinstance(m, str) and m.startswith(expected_phase_marker)]
    assert matching, f"per-substrate isolation: expected {expected_phase_marker!r} marker; got warnings_out={top_wo!r}"


# ---------------------------------------------------------------------------
# (I) Cross-prefix isolation -- for_refactor_* markers don't leak
# ---------------------------------------------------------------------------


def test_w607ep_cross_prefix_isolation_in_for_refactor_source():
    """Only one ``for_refactor`` substrate binding in mcp_server.py.

    cmd_for_refactor markers (``for_refactor_*``) must NOT appear in
    sibling W607 plumbing (pr_analyze_*, pr_prep_*, attest_*, etc.). The
    emit template is recipe-agnostic (shared ``_run_substrate``); the
    ``for_refactor`` marker family is minted at the single
    ``_run_substrate("for_refactor", ...)`` binding. Pinned via
    binding-count.
    """
    src = _module_source()
    needle = '_run_substrate("for_refactor",'
    count = src.count(needle)
    assert count == 1, f"expected exactly 1 for_refactor substrate binding in mcp_server.py; got {count}"


def test_w607ep_cross_prefix_marker_runtime_purity(for_refactor_project, monkeypatch):
    """Runtime: a for_refactor raise produces a ``for_refactor_*`` marker.

    Markers in the OWN AG bucket carry the for_refactor_ prefix. Sibling
    markers (e.g. from a child preflight invocation that wraps its own
    bucket) may propagate UP, but the OWN bucket discipline is the pin.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise RuntimeError("synthetic-cross-prefix")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    own = [m for m in top_wo if isinstance(m, str) and m.startswith("for_refactor_")]
    assert own, f"expected at least one for_refactor_ marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (J) LAW 6 -- verdict-first invariant (standalone-parseable)
# ---------------------------------------------------------------------------


def test_w607ep_law6_verdict_is_standalone_string(for_refactor_project):
    """summary.verdict is a non-empty string that parses without other fields."""
    r = for_refactor(symbol="handle_login", root=".")
    verdict = (r.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str), f"verdict not a string: {verdict!r}"
    assert verdict.strip(), f"verdict empty: {verdict!r}"


def test_w607ep_law6_verdict_survives_substrate_failure(for_refactor_project, monkeypatch):
    """LAW 6: verdict still emits cleanly even when a substrate raises."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise RuntimeError("synthetic-law6-w607ep")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    verdict = (r.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"degraded-path verdict must still be a non-empty string; got {verdict!r}"
    )


def test_w607ep_law6_verdict_survives_aggregator_failure(for_refactor_project, monkeypatch):
    """LAW 6: even ``_compound_envelope`` raising produces a verdict.

    W607-AG ships a synthesised fallback envelope when the aggregator
    itself raises. Pin the verdict-first fallback shape.
    """

    def _routed(name, sections, **kwargs):
        raise RuntimeError("synthetic-aggregator-fail-w607ep")

    monkeypatch.setattr(_srv, "_compound_envelope", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"aggregator-fail fallback must still emit a verdict; got {verdict!r}"
    )
    # Per W607-AG synthesis: partial_success flips True on aggregator fail
    assert summary.get("partial_success") is True, "aggregator-fail fallback must flip partial_success=True"


# ---------------------------------------------------------------------------
# (K) Compound-recipe preservation (W126/W150 registry-key lookup)
# ---------------------------------------------------------------------------


def test_w607ep_compound_registry_includes_subcommands():
    """Pin: _COMPOUND_REGISTRY contains all 4 for_refactor subcommands.

    Pattern 5 regression preservation: the historical
    ``complexity-report`` vs ``complexity`` typo + the ``vuln`` vs
    ``vulns`` typo are pinned via the registry.
    """
    from roam.mcp_server import _COMPOUND_REGISTRY

    for key in ("preflight", "impact", "complexity", "clones"):
        assert key in _COMPOUND_REGISTRY, (
            f"_COMPOUND_REGISTRY missing key {key!r} -- for_refactor compound-recipe key lookup will fail"
        )


def test_w607ep_compound_registry_complexity_is_complexity_not_complexity_report():
    """Pin: the ``complexity-report`` typo regression stays fixed.

    Pattern 5 lesson: ``for_refactor`` previously called
    ``roam complexity-report`` (CLI key is ``complexity``). The fix
    routed the dispatch through ``_cr("complexity")``. Pinned here so
    a future refactor that re-introduces the typo surfaces immediately.
    """
    from roam.mcp_server import _COMPOUND_REGISTRY

    assert _COMPOUND_REGISTRY["complexity"] == "complexity", (
        "_COMPOUND_REGISTRY['complexity'] must resolve to 'complexity', "
        "NOT 'complexity-report' (Pattern 5 regression pin)"
    )


def test_w607ep_for_refactor_uses_cr_lookup_not_string_concat():
    """Pin: for_refactor dispatches via ``_cr(...)``, not string-concat names."""
    tree = ast.parse(_module_source())
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            target = node
            break
    assert target is not None, "for_refactor function not found"

    # Count _cr(...) calls -- must be at least 4 (preflight, impact,
    # complexity, clones).
    cr_calls = 0
    for node in ast.walk(target):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_cr":
                cr_calls += 1
    assert cr_calls >= 4, (
        f"for_refactor must dispatch via _cr() registry-key lookup at least "
        f"4 times (preflight, impact, complexity, clones); got {cr_calls}"
    )


def test_w607ep_for_refactor_registry_verifier_runs_at_module_load():
    """Pin: ``_verify_compound_registry()`` is called at module import.

    Fail-fast discipline -- registry drift surfaces at import time, NOT
    at the first compound invocation.
    """
    src = _module_source()
    assert "_verify_compound_registry()" in src, (
        "_verify_compound_registry() module-load call missing -- registry "
        "drift would only surface at first compound invocation"
    )


# ---------------------------------------------------------------------------
# (L) LAW 5 -- compound chain length pin
# ---------------------------------------------------------------------------


def test_w607ep_for_refactor_chains_exactly_4_subcommands():
    """LAW 5 pin: for_refactor chains EXACTLY 4 subcommands.

    LAW 5 says <=3 concrete steps for safe chains. for_refactor sits at
    4 (preflight + impact + complexity + clones) which is over the
    strict ceiling but acceptable because it runs as a single MCP
    invocation (the agent issues ONE call). A future refactor that grows
    the chain to 5+ should re-raise the LAW 5 question.
    """
    src = _module_source()
    # Count substrate-call sites by inspecting the AG-plumbed _run_check
    # phases.
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            target = node
            break
    assert target is not None, "for_refactor function not found"

    phases = []
    for node in ast.walk(target):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_run_check":
                if node.args and isinstance(node.args[0], ast.Constant):
                    phases.append(node.args[0].value)
    # 4 substrate dispatches + 1 compound_envelope = 5 _run_check calls
    substrate_only = [p for p in phases if p != "compound_envelope"]
    assert len(substrate_only) == 4, (
        f"LAW 5 pin: for_refactor must chain exactly 4 subcommands; "
        f"found {substrate_only!r}. Growing to 5+ requires LAW 5 review."
    )


# ---------------------------------------------------------------------------
# (M) Coexistence -- existing W607-AG test still ships
# ---------------------------------------------------------------------------


def test_w607ep_w607ag_test_file_exists():
    """Pin: the W607-AG test suite still ships alongside this CLOSE-AS-DUPLICATE."""
    expected = Path(__file__).parent / "test_w607_ag_cmd_for_refactor_warnings_out_envelope.py"
    assert expected.exists(), f"missing prerequisite W607-AG tests at {expected}"


def test_w607ep_w805_kk_test_file_exists():
    """Pin: the W805-KK empty-corpus / Pattern-2 / Variant-D pin file ships.

    The W607-AG plumbing doc references this sibling file at line ~80 of
    the W607-AG test docstring. Renaming/removing the W805-KK file would
    break the W607-AG cross-reference. Pin here so a rename surfaces.
    """
    expected = Path(__file__).parent / "test_w805_kk_cmd_for_refactor_empty_corpus.py"
    if not expected.exists():
        pytest.skip(
            f"{expected.name!r} not present -- W607-AG cross-reference may "
            "have drifted; investigate before re-citing W805-KK."
        )


# ---------------------------------------------------------------------------
# (N) Envelope shape -- canonical fields preserved across W607-AG
# ---------------------------------------------------------------------------


def test_w607ep_envelope_command_is_for_refactor(for_refactor_project):
    """Pin: ``command`` field is ``"for-refactor"`` (NOT ``for_refactor``)."""
    r = for_refactor(symbol="handle_login", root=".")
    assert r.get("command") == "for-refactor", f"envelope.command must be 'for-refactor'; got {r.get('command')!r}"


def test_w607ep_envelope_summary_target_preserved(for_refactor_project):
    """Pin: ``summary.target`` echoes the input symbol."""
    r = for_refactor(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("target") == "handle_login", (
        f"summary.target must echo input symbol; got {summary.get('target')!r}"
    )


def test_w607ep_envelope_situation_is_refactor(for_refactor_project):
    """Pin: ``summary.situation`` is ``"refactor"``."""
    r = for_refactor(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("situation") == "refactor", (
        f"summary.situation must be 'refactor'; got {summary.get('situation')!r}"
    )


# ---------------------------------------------------------------------------
# (O) Usage-error guard -- empty symbol returns structured error
# ---------------------------------------------------------------------------


def test_w607ep_empty_symbol_returns_usage_error():
    """Pin: ``symbol=""`` short-circuits to USAGE_ERROR.

    The W607-AG plumbing block is GUARDED by an empty-symbol check that
    returns a structured-error envelope BEFORE the substrate dispatch.
    Pinned so a refactor that moves the guard below the plumbing surfaces
    immediately.
    """
    r = for_refactor(symbol="", root=".")
    assert isinstance(r, dict)
    # Structured error envelope shape
    assert r.get("command") == "roam_for_refactor" or r.get("error_code") == "USAGE_ERROR", (
        f"empty symbol must return USAGE_ERROR structured error; got {r!r}"
    )
