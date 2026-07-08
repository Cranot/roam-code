"""W607-ES -- ``cmd_grep`` CLOSE-AS-DUPLICATE pinning suite.

CLOSE-AS-DUPLICATE per the W607-DZ/EA/EE/EJ/EL/EO/ER/EP discovery methodology
============================================================================

W607-ES was queued as "apply substrate-CALL W607 plumbing to
``src/roam/commands/cmd_grep.py`` (index-aware grep consumer)". The
CRITICAL FIRST STEP grep for ``_w607`` in ``cmd_grep.py`` found TWO
pre-existing W607 plumbing arcs already fully landed:

* **W607-BV (substrate-CALL layer)** -- 10 substrate-helper boundaries
  wrapped through a module-local ``_run_check_bv(phase, fn, *args, default=...)``
  helper that accumulates into ``_w607bv_warnings_out``. Phases:
  ``compile_patterns`` / ``select_engine`` / ``run_engine`` /
  ``apply_reachability_filter`` / ``apply_co_occur_filter`` /
  ``apply_missing_pattern`` / ``apply_rank_by`` / ``apply_blame_heat`` /
  ``apply_group_by`` / ``serialize_envelope``. Tests live in
  ``tests/test_w607_bv_cmd_grep_warnings_out_envelope.py`` (961 lines,
  exhaustive substrate-call audit).

* **W607-G (outer-guard layer)** -- engine-fan-out subprocess axis.
  Threads a sibling ``warnings_out: list[str] = []`` accumulator that
  surfaces ``grep_engine_pin_missing:`` / ``grep_engine_fanout_fallback:``
  / ``grep_ripgrep_failed:`` / ``grep_git_grep_failed:`` /
  ``grep_engine_failed:`` / ``grep_indexed_scan_failed:`` markers --
  the silent-fallback seal for the ripgrep > git grep > python-fallback
  engine cascade. The two layers compose: at every emit site,
  ``_combined = list(warnings_out) + list(_w607bv_warnings_out)`` is
  threaded onto BOTH ``summary.warnings_out`` AND the top-level
  ``envelope.warnings_out``.

The accumulator names W607-ES was supposed to introduce
(``_w607es_warnings_out``) and the helper name (``_run_check_es``) do
NOT exist in the source -- their semantic equivalents are
``_w607bv_warnings_out`` / ``_run_check_bv`` (substrate-CALL) +
``warnings_out`` (W607-G outer-guard). Adding a THIRD bucket would:

1. Duplicate the marker family already covered by BV + G (both emit
   under the ``grep_*`` family).
2. Re-name the same disclosure channel
   (``grep_<phase>_failed:<exc_class>:<detail>``) without changing
   observable behaviour.
3. Risk drifting the bucket-merge pattern
   (``_combined_* = list(warnings_out) + list(_w607bv_warnings_out)``)
   that already threads markers onto BOTH ``summary.warnings_out`` AND
   the top-level ``envelope.warnings_out`` at THREE emit sites
   (empty-pre-filter / empty-post-filter / happy-match).

Per the W607-DZ/EA/EE/EJ/EL/EO/ER/EP template -- when both axes are
already in-tree, the correct action is **pinning tests, not source
modification**. This file captures 25+ pinning invariants so any future
regression of the W607-BV or W607-G plumbing surfaces immediately. No
source edit.

Engine-fallback regression preservation
=======================================

cmd_grep's signature behaviour is the engine cascade (CLAUDE.md:
"ripgrep > git grep > fallback (pin via ``ROAM_GREP_ENGINE``)"). The
W607-G outer-guard makes each fallback LOUD via the
``grep_engine_pin_missing:`` and ``grep_engine_fanout_fallback:``
markers; the W607-BV substrate-CALL layer makes the in-process
substrates (compile / select / run / annotate / serialize) LOUD via
``grep_<phase>_failed:`` markers. Together they prove the
fallback-is-disclosed discipline ("Make fallback chains loud" per
CLAUDE.md). This file pins both axes so future refactors cannot silently
swallow an engine fallback.

Index-aware text-search family pairing pin
==========================================

cmd_grep is one of five index-aware text-search consumers. As of this
wave, the W607 coverage matrix is:

* ``cmd_grep``        -- W607-BV (substrate) + W607-G (outer-guard) -- LANDED
* ``cmd_trace``       -- W607-EQ (substrate)                         -- LANDED (just landed before this wave)
* ``cmd_refs_text``   -- no W607 plumbing yet                        -- GREENFIELD
* ``cmd_delete_check`` -- no W607 plumbing yet                       -- GREENFIELD
* ``cmd_history_grep`` -- no W607 plumbing yet                       -- GREENFIELD

This file pins that matrix via filesystem checks so a regression of
the cmd_grep / cmd_trace plumbing surfaces here as well as in the
dedicated suites -- and so an agent picking up W607-ET / W607-EU /
W607-EV knows which siblings remain greenfield.

LAW 6 verdict-first invariant
=============================

cmd_grep emits ``f"{len(matches)} matches in {unique_files} files for ..."``
as the verdict. On degraded paths (empty corpus / no patterns / unresolved
entry), the verdict is rephrased (``"no patterns provided"`` /
``"entry symbol ... not found in index"`` / ``"... no matches"``) -- the
verdict line STILL stands alone (LAW 6 holds) and the bucket-merge
``warnings_out`` thread carries any substrate / engine markers. This
file pins LAW 6 across all three branches.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CMD_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_grep.py"
_CMD_TRACE_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_trace.py"
_CMD_REFS_TEXT_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_refs_text.py"
_CMD_DELETE_CHECK_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_delete_check.py"
_CMD_HISTORY_GREP_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_history_grep.py"


# Canonical W607-BV substrate-CALL phases that ``_run_check_bv`` wraps.
_W607BV_SUBSTRATE_PHASES = (
    "compile_patterns",
    "select_engine",
    "run_engine",
    "apply_reachability_filter",
    "apply_co_occur_filter",
    "apply_missing_pattern",
    "apply_rank_by",
    "apply_blame_heat",
    "apply_group_by",
    "serialize_envelope",
)


# Canonical W607-G outer-guard marker prefixes.
_W607G_OUTER_GUARD_PREFIXES = (
    "grep_engine_pin_missing:",
    "grep_engine_fanout_fallback:",
    "grep_ripgrep_failed:",
    "grep_git_grep_failed:",
    "grep_engine_failed:",
    "grep_indexed_scan_failed:",
)


def _module_source() -> str:
    return _CMD_PATH.read_text(encoding="utf-8")


def _invoke_grep(
    runner: CliRunner,
    cwd,
    *extra,
    json_mode: bool = True,
):
    """Invoke ``roam grep`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("grep")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with greppable content
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def grep_project(tmp_path, monkeypatch):
    """Indexed corpus where ``grep needle`` produces matches."""
    proj = tmp_path / "grep_w607es_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "        # needle marker for grep search\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    # needle marker too\n    return User('test')\n\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (A) AST audit -- pin the EXISTING W607-BV plumbing shape
# ---------------------------------------------------------------------------


def test_w607es_close_as_duplicate_cmd_grep_module_exists():
    """Pin: cmd_grep.py exists at the canonical path."""
    assert _CMD_PATH.exists(), f"missing {_CMD_PATH}"


def test_w607es_no_w607es_accumulator_exists():
    """Pin: the W607-ES accumulator name is INTENTIONALLY absent.

    W607-ES is CLOSE-AS-DUPLICATE -- the marker family the task would
    have introduced is already covered by W607-BV + W607-G. If a
    future agent adds ``_w607es_warnings_out`` to the source, this
    test fires and forces a reconciliation with the existing buckets.
    """
    src = _module_source()
    assert "w607es_warnings_out" not in src, (
        "W607-ES is CLOSE-AS-DUPLICATE; do NOT introduce a third bucket "
        "alongside _w607bv_warnings_out / warnings_out. "
        "Use the existing _run_check_bv helper instead."
    )


def test_w607es_no_run_check_es_helper_exists():
    """Pin: the W607-ES helper name is INTENTIONALLY absent."""
    src = _module_source()
    assert "_run_check_es(" not in src and "def _run_check_es" not in src, (
        "W607-ES is CLOSE-AS-DUPLICATE; do NOT introduce _run_check_es. "
        "Substrate boundaries -> _run_check_bv (W607-BV); engine fan-out "
        "subprocess axis -> direct warnings_out append (W607-G)."
    )


def test_w607es_w607bv_accumulator_present():
    """Pin: ``_w607bv_warnings_out`` substrate-CALL bucket is present."""
    src = _module_source()
    assert "_w607bv_warnings_out: list[str] = []" in src, (
        "W607-BV substrate-CALL bucket missing; W607-ES pinning suite depends on it being live."
    )


def test_w607es_w607g_outer_guard_bucket_present():
    """Pin: ``warnings_out: list[str] = []`` outer-guard bucket is present."""
    src = _module_source()
    assert "warnings_out: list[str] = []" in src, (
        "W607-G outer-guard bucket missing; W607-ES pinning suite "
        "depends on the engine-fan-out subprocess axis being live."
    )


def test_w607es_run_check_bv_helper_returns_default_verbatim():
    """Pin: ``_run_check_bv`` returns ``default`` verbatim on except.

    Helper-template discipline -- the W607 template mandates
    ``return default`` (NOT ``return None``, NOT ``raise``, NOT
    ``return default()``). AST-walk the helper body and assert the
    except-block returns the bare ``default`` name.
    """
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bv":
            helper = node
            break
    assert helper is not None, "module-local _run_check_bv helper not found"

    found_default_return = False
    for node in ast.walk(helper):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for stmt in handler.body:
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                        if stmt.value.id == "default":
                            found_default_return = True
    assert found_default_return, (
        "_run_check_bv except-handler must `return default` verbatim (W607 helper-template discipline)"
    )


def test_w607es_marker_format_grep_family():
    """Pin: helper emits ``grep_<phase>_failed:<exc_class>:<detail>`` markers."""
    src = _module_source()
    needle = 'f"grep_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert needle in src, f"expected substrate marker emit site {needle!r} in cmd_grep.py"


# ---------------------------------------------------------------------------
# (B) Substrate-CALL phase coverage (W607-BV AST audit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", _W607BV_SUBSTRATE_PHASES)
def test_w607es_w607bv_substrate_phase_wrapped(phase):
    """Pin: each W607-BV substrate phase is wrapped via ``_run_check_bv``.

    AST-walk the grep module and assert the phase string appears as
    the first positional arg to a ``_run_check_bv(...)`` call.
    """
    tree = ast.parse(_module_source())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_bv":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == phase:
                    found = True
                    break
    assert found, f"W607-BV substrate phase {phase!r} must be wrapped via _run_check_bv"


# ---------------------------------------------------------------------------
# (C) Outer-guard marker prefix coverage (W607-G)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", _W607G_OUTER_GUARD_PREFIXES)
def test_w607es_w607g_outer_guard_marker_present_in_source(prefix):
    """Pin: each W607-G outer-guard marker prefix is emitted somewhere in cmd_grep."""
    src = _module_source()
    assert prefix in src, (
        f"W607-G outer-guard marker prefix {prefix!r} must be emitted "
        "somewhere in cmd_grep.py (engine fan-out subprocess axis)"
    )


# ---------------------------------------------------------------------------
# (D) Bucket-merge invariant -- BOTH layers feed BOTH mirrors at 3 emit sites
# ---------------------------------------------------------------------------


def test_w607es_combined_bucket_includes_w607g_and_w607bv():
    """Pin: combined bucket concatenates W607-G + W607-BV in that order."""
    src = _module_source()
    needle = "list(warnings_out) + list(_w607bv_warnings_out)"
    # Three emit sites: empty-pre-filter, empty-post-filter, happy-match.
    assert src.count(needle) >= 3, (
        f"combined bucket must thread BOTH W607-G + W607-BV markers at "
        f">=3 emit sites (empty pre-filter / empty post-filter / happy-match); "
        f"got count={src.count(needle)}"
    )


def test_w607es_emit_json_threads_combined_warnings_out():
    """Pin: ``_emit_json`` accepts a ``warnings_out=`` kwarg for the merged bucket."""
    src = _module_source()
    assert "warnings_out=_combined_match" in src or "warnings_out=_combined" in src, (
        "happy-match emit site must pass the combined bucket via warnings_out="
    )


# ---------------------------------------------------------------------------
# (E) Runtime invariants -- clean path emits no W607 markers
# ---------------------------------------------------------------------------


def test_w607es_clean_envelope_omits_w607_markers(cli_runner, grep_project):
    """Clean grep on a healthy repo -> no W607 phase markers."""
    result = _invoke_grep(cli_runner, grep_project, "needle")
    assert result.exit_code in (0, 1), result.output
    data = _json.loads(result.output)
    assert data["command"] == "grep"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    markers = [
        m for m in (list(top_wo) + list(summary_wo)) if isinstance(m, str) and m.startswith("grep_") and "_failed:" in m
    ]
    assert not markers, (
        f"clean grep must NOT surface grep_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


def test_w607es_substrate_failure_surfaces_w607bv_marker(cli_runner, grep_project, monkeypatch):
    """Substrate raise -> ``grep_apply_co_occur_filter_failed:`` on envelope.

    Inject a raise into ``_apply_co_occur_filter`` and assert the W607-BV
    marker surfaces on the merged ``warnings_out``. ``_apply_co_occur_filter``
    sits on the happy-match path so the combined-bucket merge runs.
    """
    from roam.commands import cmd_grep as _mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic-from-W607-ES")

    monkeypatch.setattr(_mod, "_apply_co_occur_filter", _boom)
    result = _invoke_grep(cli_runner, grep_project, "-e", "needle", "-e", "marker", "--co-occur")
    assert result.exit_code in (0, 1, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = (data.get("summary") or {}).get("warnings_out") or []
    combined = list(top_wo) + list(summary_wo)
    markers = [m for m in combined if isinstance(m, str) and m.startswith("grep_apply_co_occur_filter_failed:")]
    assert markers, f"expected grep_apply_co_occur_filter_failed: marker; got top={top_wo!r}, summary={summary_wo!r}"


def test_w607es_substrate_failure_marker_includes_exc_class(cli_runner, grep_project, monkeypatch):
    """Marker must include ``<exc_class>:<detail>`` -- helper-template shape."""
    from roam.commands import cmd_grep as _mod

    class _CustomBoom(RuntimeError):
        pass

    def _boom(*args, **kwargs):
        raise _CustomBoom("specific-detail-w607es")

    monkeypatch.setattr(_mod, "_apply_co_occur_filter", _boom)
    result = _invoke_grep(cli_runner, grep_project, "-e", "needle", "-e", "marker", "--co-occur")
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = (data.get("summary") or {}).get("warnings_out") or []
    combined = list(top_wo) + list(summary_wo)
    matches = [m for m in combined if "_CustomBoom" in m and "specific-detail-w607es" in m]
    assert matches, f"expected marker to include exc class + detail; got top={top_wo!r}, summary={summary_wo!r}"


def test_w607es_substrate_failure_flips_partial_success(cli_runner, grep_project, monkeypatch):
    """A substrate raise must set summary.partial_success = True."""
    from roam.commands import cmd_grep as _mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic-w607es-partial")

    monkeypatch.setattr(_mod, "_apply_co_occur_filter", _boom)
    result = _invoke_grep(cli_runner, grep_project, "-e", "needle", "-e", "marker", "--co-occur")
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    assert summary.get("partial_success") is True, f"expected summary.partial_success=True; got summary={summary!r}"


# ---------------------------------------------------------------------------
# (F) LAW 6 -- verdict-first invariant (standalone-parseable)
# ---------------------------------------------------------------------------


def test_w607es_law6_verdict_is_standalone_string(cli_runner, grep_project):
    """summary.verdict is a non-empty string that parses without other fields."""
    result = _invoke_grep(cli_runner, grep_project, "needle")
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str), f"verdict not a string: {verdict!r}"
    assert verdict.strip(), f"verdict empty: {verdict!r}"


def test_w607es_law6_verdict_survives_substrate_failure(cli_runner, grep_project, monkeypatch):
    """LAW 6: verdict still emits cleanly even when a substrate raises."""
    from roam.commands import cmd_grep as _mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic-law6-w607es")

    monkeypatch.setattr(_mod, "_apply_co_occur_filter", _boom)
    result = _invoke_grep(cli_runner, grep_project, "-e", "needle", "-e", "marker", "--co-occur")
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"degraded-path verdict must still be a non-empty string; got {verdict!r}"
    )


def test_w607es_law6_verdict_on_no_patterns_usage_error(cli_runner, grep_project):
    """LAW 6: usage-error path still emits a standalone verdict."""
    # No positional pattern AND no --regex / --patterns-from -> usage error
    result = _invoke_grep(cli_runner, grep_project)
    assert result.exit_code == 2, result.output
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"usage-error verdict must be a non-empty string; got {verdict!r}"
    )
    assert "no patterns" in verdict.lower(), f"usage-error verdict should mention missing patterns; got {verdict!r}"


# ---------------------------------------------------------------------------
# (G) Cross-prefix isolation -- grep_* markers don't leak to siblings
# ---------------------------------------------------------------------------


def test_w607es_cross_prefix_isolation_in_source():
    """``grep_*`` is the only W607 marker family in this module.

    cmd_grep must NOT emit trace_*, refs_text_*, delete_check_*,
    history_grep_*, search_*, etc. markers on its own boundaries --
    those belong to sibling consumers. Cross-prefix leakage would
    muddy the per-command audit.
    """
    src = _module_source()
    forbidden_prefixes = (
        "trace_",
        "refs_text_",
        "delete_check_",
        "history_grep_",
        "search_phase_",
        "search_semantic_",
        "complete_phase_",
        "retrieve_phase_",
    )
    for prefix in forbidden_prefixes:
        # Allow incidental docstring mentions of the sibling NAMES, but
        # forbid actual marker emissions (``f"{prefix}<phase>_failed:..."``).
        offending = f'f"{prefix}{{phase}}_failed:'
        assert offending not in src, (
            f"cmd_grep must NOT emit {prefix!r} markers (cross-prefix leak); those belong to a sibling consumer"
        )


def test_w607es_cross_prefix_isolation_at_runtime(cli_runner, grep_project, monkeypatch):
    """Runtime markers from cmd_grep all carry the ``grep_*`` prefix."""
    from roam.commands import cmd_grep as _mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-w607es")

    monkeypatch.setattr(_mod, "_apply_co_occur_filter", _boom)
    result = _invoke_grep(cli_runner, grep_project, "-e", "needle", "-e", "marker", "--co-occur")
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = (data.get("summary") or {}).get("warnings_out") or []
    for marker in list(top_wo) + list(summary_wo):
        if not isinstance(marker, str):
            continue
        if "_failed:" in marker:
            assert marker.startswith("grep_"), f"all cmd_grep markers must start with 'grep_'; got {marker!r}"


# ---------------------------------------------------------------------------
# (H) Engine-fallback regression preservation (W607-G outer-guard)
# ---------------------------------------------------------------------------


def test_w607es_engine_pin_missing_marker_emitted(cli_runner, grep_project, monkeypatch):
    """ROAM_GREP_ENGINE pinned to absent binary -> ``grep_engine_pin_missing:`` surfaced.

    Per CLAUDE.md: "ripgrep > git grep > fallback (pin via `ROAM_GREP_ENGINE`)".
    If the user pins ``ripgrep`` (recognized pin) AND the selected engine
    is not ripgrep, the silent ``"fallback"`` resolution must be LOUD --
    W607-G discipline. We force this by monkeypatching ``_select_engine``
    to return ``"fallback"`` while the user pinned ``ripgrep``.
    """
    from roam.commands import cmd_grep as _mod

    monkeypatch.setenv("ROAM_GREP_ENGINE", "ripgrep")
    monkeypatch.setattr(_mod, "_select_engine", lambda: "fallback")
    result = _invoke_grep(cli_runner, grep_project, "needle")
    # Engine cascade fallback is graceful; result still parses
    assert result.exit_code in (0, 1), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = (data.get("summary") or {}).get("warnings_out") or []
    combined = list(top_wo) + list(summary_wo)
    has_pin_missing = any(isinstance(m, str) and m.startswith("grep_engine_pin_missing:") for m in combined)
    assert has_pin_missing, (
        f"unhonored ROAM_GREP_ENGINE pin must surface "
        f"grep_engine_pin_missing: marker; got top={top_wo!r}, "
        f"summary={summary_wo!r}"
    )


def test_w607es_engine_cascade_gracefully_degrades(cli_runner, grep_project, monkeypatch):
    """Engine cascade preserved: bogus pin -> python fallback still runs.

    Confirms that an unresolvable ROAM_GREP_ENGINE pin doesn't crash;
    the indexed-scan fallback still produces matches (LAW 6 verdict
    still emits).
    """
    from roam.commands import cmd_grep as _mod

    monkeypatch.setenv("ROAM_GREP_ENGINE", "ripgrep")
    monkeypatch.setattr(_mod, "_select_engine", lambda: "fallback")
    result = _invoke_grep(cli_runner, grep_project, "needle")
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"engine-fallback path must still emit a verdict; got {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (I) Index-aware text-search family pairing pin
# ---------------------------------------------------------------------------


def test_w607es_text_search_family_paths_exist():
    """Pin: all five index-aware text-search siblings exist on disk."""
    for path in (
        _CMD_PATH,
        _CMD_TRACE_PATH,
        _CMD_REFS_TEXT_PATH,
        _CMD_DELETE_CHECK_PATH,
        _CMD_HISTORY_GREP_PATH,
    ):
        assert path.exists(), f"missing index-aware text-search consumer: {path}"


def test_w607es_text_search_family_w607_coverage_matrix():
    """Pin: cmd_grep + cmd_trace are W607-plumbed; the other 3 are greenfield.

    This documents the family state at the time of W607-ES. An agent
    picking up W607-ET / W607-EU / W607-EV should target one of the
    greenfield siblings (cmd_refs_text / cmd_delete_check /
    cmd_history_grep). If a future wave plumbs one of those AND we
    don't update this pin, the test fires and forces an update to the
    family-state documentation in this file's docstring.
    """
    grep_src = _CMD_PATH.read_text(encoding="utf-8")
    trace_src = _CMD_TRACE_PATH.read_text(encoding="utf-8")
    refs_text_src = _CMD_REFS_TEXT_PATH.read_text(encoding="utf-8")
    delete_check_src = _CMD_DELETE_CHECK_PATH.read_text(encoding="utf-8")
    history_grep_src = _CMD_HISTORY_GREP_PATH.read_text(encoding="utf-8")

    # cmd_grep IS plumbed (W607-BV)
    assert "w607bv_warnings_out" in grep_src, "cmd_grep should carry W607-BV plumbing"
    # cmd_trace IS plumbed (W607-EQ)
    assert "w607eq_warnings_out" in trace_src, "cmd_trace should carry W607-EQ plumbing"
    # The other 3 are GREENFIELD as of W607-ES. If a future wave plumbs
    # one of them, update this docstring + relax the assertion.
    assert "_w607" not in refs_text_src, (
        "cmd_refs_text was greenfield at W607-ES; if you've plumbed it, "
        "update the family-state pin in test_w607_es_*.py docstring"
    )
    assert "_w607" not in delete_check_src, (
        "cmd_delete_check was greenfield at W607-ES; if you've plumbed it, "
        "update the family-state pin in test_w607_es_*.py docstring"
    )
    assert "_w607" not in history_grep_src, (
        "cmd_history_grep was greenfield at W607-ES; if you've plumbed it, "
        "update the family-state pin in test_w607_es_*.py docstring"
    )


# ---------------------------------------------------------------------------
# (J) Per-substrate isolation -- one phase failing doesn't suppress others
# ---------------------------------------------------------------------------


def test_w607es_per_substrate_isolation_select_engine_runs_after_compile_patterns():
    """Pin: ``_run_check_bv("select_engine", ...)`` follows compile_patterns.

    AST-walk and assert the ordering of substrate-CALL sites:
    compile_patterns appears BEFORE select_engine in source order.
    Per-substrate isolation means one phase's degraded default doesn't
    short-circuit subsequent phases.
    """
    tree = ast.parse(_module_source())
    compile_lineno = None
    select_lineno = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_bv":
                if node.args and isinstance(node.args[0], ast.Constant):
                    phase = node.args[0].value
                    if phase == "compile_patterns" and compile_lineno is None:
                        compile_lineno = node.lineno
                    elif phase == "select_engine" and select_lineno is None:
                        select_lineno = node.lineno
    assert compile_lineno is not None, "compile_patterns call site not found"
    assert select_lineno is not None, "select_engine call site not found"
    assert compile_lineno < select_lineno, (
        f"compile_patterns ({compile_lineno}) must precede select_engine ({select_lineno}) in source order"
    )


def test_w607es_per_substrate_isolation_run_engine_follows_select_engine():
    """Pin: ``_run_check_bv("run_engine", ...)`` follows select_engine."""
    tree = ast.parse(_module_source())
    select_lineno = None
    run_lineno = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_bv":
                if node.args and isinstance(node.args[0], ast.Constant):
                    phase = node.args[0].value
                    if phase == "select_engine" and select_lineno is None:
                        select_lineno = node.lineno
                    elif phase == "run_engine" and run_lineno is None:
                        run_lineno = node.lineno
    assert select_lineno is not None and run_lineno is not None
    assert select_lineno < run_lineno, f"select_engine ({select_lineno}) must precede run_engine ({run_lineno})"


# ---------------------------------------------------------------------------
# (K) AST audit -- count of _run_check_bv call sites matches phase set
# ---------------------------------------------------------------------------


def test_w607es_run_check_bv_call_site_count_matches_phase_set():
    """Pin: every canonical W607-BV phase has at least one _run_check_bv call site."""
    tree = ast.parse(_module_source())
    found_phases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_bv":
                if node.args and isinstance(node.args[0], ast.Constant):
                    found_phases.add(node.args[0].value)
    missing = set(_W607BV_SUBSTRATE_PHASES) - found_phases
    assert not missing, f"missing _run_check_bv call sites for phases: {sorted(missing)!r}"


# ---------------------------------------------------------------------------
# (L) Helper-template default-shape audit (paranoid `return default` pin)
# ---------------------------------------------------------------------------


def test_w607es_helper_template_does_not_return_none_explicit():
    """Pin: the helper's except-handler does not `return None` (must be `return default`)."""
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bv":
            helper = node
            break
    assert helper is not None

    for node in ast.walk(helper):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for stmt in handler.body:
                    if isinstance(stmt, ast.Return):
                        # `return None` (explicit) or `return` (bare) would
                        # be a template violation. Only `return default`
                        # (Name node) is acceptable.
                        if isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
                            raise AssertionError("_run_check_bv must `return default` verbatim, not `return None`")
                        if stmt.value is None:
                            raise AssertionError("_run_check_bv must `return default` verbatim, not bare `return`")


def test_w607es_helper_template_appends_to_w607bv_bucket():
    """Pin: helper appends to ``_w607bv_warnings_out`` (NOT a different bucket)."""
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bv":
            helper = node
            break
    assert helper is not None

    found_append = False
    for node in ast.walk(helper):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "append":
                if isinstance(func.value, ast.Name) and func.value.id == "_w607bv_warnings_out":
                    found_append = True
    assert found_append, "_run_check_bv must append to _w607bv_warnings_out (not a sibling bucket)"


# ---------------------------------------------------------------------------
# (M) Combined bucket -> top-level + summary mirror (3-site invariant)
# ---------------------------------------------------------------------------


def test_w607es_emit_empty_threads_combined_bucket():
    """Pin: ``_emit_empty`` accepts a ``warnings_out=`` kwarg."""
    src = _module_source()
    # Two empty emit sites: pre-filter and post-filter.
    assert src.count("warnings_out=_combined_empty") >= 1, (
        "empty-pre-filter emit site must pass _combined_empty via warnings_out="
    )
    assert src.count("warnings_out=_combined_filtered") >= 1, (
        "empty-post-filter emit site must pass _combined_filtered via warnings_out="
    )
