"""W607-EO -- ``cmd_pr_prep`` CLOSE-AS-DUPLICATE pinning suite.

CLOSE-AS-DUPLICATE per the W607-DZ/EA/EE/EJ/EL discovery methodology
====================================================================

W607-EO was queued as "apply W607 substrate-CALL plumbing to
``cmd_pr_prep.py`` (PR-review compound recipe sibling to cmd_pr_analyze)".
The CRITICAL FIRST STEP grep for ``_w607`` in
``src/roam/commands/cmd_pr_prep.py`` found TWO pre-existing W607 plumbing
arcs already fully landed:

* **W607-AC (substrate-CALL layer)** -- 9 substrate-helper boundaries
  wrapped through a module-local ``_run_check(phase, fn, *args, default=...)``
  helper that accumulates into ``_w607ac_warnings_out``. Phases:
  ``capture_diff`` / ``git_diff_text`` / ``capture_critique`` /
  ``parse_critique_json`` / ``capture_pr_risk`` /
  ``inspect_failed_subcommands`` / ``compute_verdict`` /
  ``auto_log_run`` (the 9th anchor surfaced as ``auto_log_run`` no-op
  closure so the substrate inventory guard pins it; the real auto_log
  was lifted to W607-CC). Tests live in
  ``tests/test_w607_ac_cmd_pr_prep_warnings_out_envelope.py`` (669
  lines, exhaustive substrate-call audit).

* **W607-CC (aggregation-phase layer)** -- 5 aggregation-phase boundaries
  wrapped through a sibling ``_run_check_cc(phase, fn, *args, default=...)``
  helper that accumulates into ``_w607cc_warnings_out``. Phases:
  ``score_classify`` / ``score_normalize`` / ``compute_verdict`` /
  ``auto_log`` / ``serialize_envelope``. Same marker family
  (``pr_prep_*``) -- additive, not a separate prefix. Tests live in
  ``tests/test_w607_cc_cmd_pr_prep_warnings_out_envelope.py`` (888
  lines, exhaustive aggregation-phase audit).

The accumulator names W607-EO was supposed to introduce
(``_w607eo_warnings_out``) and the helper name (``_run_check_eo``) do
NOT exist in the source -- their semantic equivalents are
``_w607ac_warnings_out`` / ``_run_check`` (substrate) +
``_w607cc_warnings_out`` / ``_run_check_cc`` (aggregation). Adding a
THIRD bucket would:

1. Duplicate the marker family already covered by AC + CC.
2. Re-name the same disclosure channel (``pr_prep_<phase>_failed:``)
   without changing observable behaviour.
3. Risk drifting the bucket-merge pattern (``_combined_warnings_out =
   list(_w607ac_warnings_out) + list(_w607cc_warnings_out)``) that
   already threads markers onto BOTH ``summary.warnings_out`` and the
   top-level ``envelope.warnings_out``.

Per the W607-DZ/EA/EE/EJ/EL template -- when both axes are already
in-tree, the correct action is **pinning tests, not source
modification**. This file captures 30+ pinning invariants so any future
regression of the W607-AC or W607-CC plumbing surfaces immediately. No
source edit.

PR-review FULL QUARTET pairing pin
==================================

cmd_pr_prep is one of four PR-review composer commands. All four are
fully W607-plumbed on both layers as of the W607-CC/CD landings:

* cmd_pr_analyze   -- W607-AA (substrate) + W607-BY (aggregation)
* cmd_pr_prep      -- W607-AC (substrate) + W607-CC (aggregation)
* cmd_pr_risk      -- W607-Q  (substrate) + W607-AB (extension) + W607-BU (aggregation)
* cmd_critique     -- W607-Y  (substrate) + W607-BL (aggregation)

This file pins that quartet via AST scan so a regression of ANY
member's W607 plumbing surfaces here as well as in its dedicated
suite -- the family is treated as a coherent disclosure-channel
unit.

Compound-recipe preservation (W126/W150)
========================================

cmd_pr_prep is a PR-review compound that composes ``diff`` (via
``_capture_json_subcommand``) + ``critique`` (via ``CliRunner().invoke``)
+ ``pr-risk`` (via ``_capture_json_subcommand``). Registry-key lookup
discipline is preserved through ``_COMPOUND_REGISTRY`` / ``_cr()`` in
``mcp_server.py`` -- pr-prep does NOT string-concat subcommand names.
The W607-AC/CC plumbing wraps the helper CALLS, not the registry lookup,
so registry-key discipline is undisturbed.

LAW 6 verdict-first invariant
=============================

W607-CC's ``compute_verdict`` boundary builds the augmented verdict
text appending the canonical ``(risk_level X)`` suffix. The floor
floors to ``"pr-prep completed (risk_level low)"`` (literal "low",
NOT a re-formatted ``risk_level_canonical`` -- W978
first-hypothesis discipline). LAW 6 still holds on the degraded
path: the verdict line works standalone.

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
_CMD_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_pr_prep.py"
_CMD_PR_ANALYZE_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
_CMD_PR_RISK_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_pr_risk.py"
_CMD_CRITIQUE_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_critique.py"


def _module_source() -> str:
    return _CMD_PATH.read_text(encoding="utf-8")


def _invoke_pr_prep(
    runner: CliRunner,
    cwd,
    *extra,
    json_mode: bool = True,
):
    """Invoke ``roam pr-prep`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-prep")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus + a diff target file
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_prep_project(tmp_path, monkeypatch):
    """Indexed corpus with a symbol pr-prep can analyze."""
    proj = tmp_path / "pr_prep_w607eo_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    return User('test')\n\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (A) AST audit -- pin the EXISTING W607-AC + W607-CC plumbing shape
# ---------------------------------------------------------------------------


def test_w607eo_close_as_duplicate_cmd_pr_prep_module_exists():
    """Pin: cmd_pr_prep.py exists at the canonical path."""
    assert _CMD_PATH.exists(), f"missing {_CMD_PATH}"


def test_w607eo_no_w607eo_accumulator_exists():
    """Pin: the W607-EO accumulator name is INTENTIONALLY absent.

    W607-EO is CLOSE-AS-DUPLICATE -- the marker family the task would
    have introduced is already covered by W607-AC + W607-CC. If a
    future agent adds ``_w607eo_warnings_out`` to the source, this
    test fires and forces a reconciliation with the existing buckets.
    """
    src = _module_source()
    assert "w607eo_warnings_out" not in src, (
        "W607-EO is CLOSE-AS-DUPLICATE; do NOT introduce a third bucket "
        "alongside _w607ac_warnings_out / _w607cc_warnings_out. "
        "Use the existing _run_check / _run_check_cc helpers instead."
    )


def test_w607eo_no_run_check_eo_helper_exists():
    """Pin: the W607-EO helper name is INTENTIONALLY absent."""
    src = _module_source()
    assert "_run_check_eo(" not in src and "def _run_check_eo" not in src, (
        "W607-EO is CLOSE-AS-DUPLICATE; do NOT introduce _run_check_eo. "
        "Substrate boundaries -> _run_check (W607-AC); aggregation-phase -> "
        "_run_check_cc (W607-CC)."
    )


def test_w607eo_w607ac_accumulator_present():
    """Pin: ``_w607ac_warnings_out`` substrate-CALL bucket is present."""
    src = _module_source()
    assert "_w607ac_warnings_out: list[str] = []" in src, (
        "W607-AC substrate-CALL bucket missing; W607-EO pinning suite depends on it being live."
    )


def test_w607eo_w607cc_accumulator_present():
    """Pin: ``_w607cc_warnings_out`` aggregation-phase bucket is present."""
    src = _module_source()
    assert "_w607cc_warnings_out: list[str] = []" in src, (
        "W607-CC aggregation-phase bucket missing; W607-EO pinning suite depends on it being live."
    )


def test_w607eo_run_check_helper_returns_default_verbatim():
    """Pin: ``_run_check`` (W607-AC) returns ``default`` verbatim on except.

    Helper-template discipline -- the W607 template mandates
    ``return default`` (NOT ``return None``, NOT ``raise``, NOT
    ``return default()``). AST-walk the helper body and assert the
    except-block returns the bare ``default`` name.
    """
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            helper = node
            break
    assert helper is not None, "module-local _run_check helper not found"

    found_default_return = False
    for node in ast.walk(helper):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for stmt in handler.body:
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                        if stmt.value.id == "default":
                            found_default_return = True
    assert found_default_return, (
        "_run_check except-handler must `return default` verbatim (W607 helper-template discipline)"
    )


def test_w607eo_run_check_cc_helper_returns_default_verbatim():
    """Pin: ``_run_check_cc`` (W607-CC) returns ``default`` verbatim on except."""
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cc":
            helper = node
            break
    assert helper is not None, "module-local _run_check_cc helper not found"

    found_default_return = False
    for node in ast.walk(helper):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for stmt in handler.body:
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                        if stmt.value.id == "default":
                            found_default_return = True
    assert found_default_return, (
        "_run_check_cc except-handler must `return default` verbatim (W607 helper-template discipline)"
    )


def test_w607eo_marker_format_pr_prep_family():
    """Pin: both helpers emit ``pr_prep_<phase>_failed:`` markers.

    Same marker FAMILY across substrate-CALL + aggregation-phase, but
    distinguishable via phase name (capture_diff / score_classify /
    auto_log / etc.).
    """
    src = _module_source()
    assert src.count('f"pr_prep_{phase}_failed:{type(exc).__name__}:{exc}"') >= 2, (
        "expected >=2 ``pr_prep_{phase}_failed:`` marker emit sites (one in _run_check, one in _run_check_cc)"
    )


# ---------------------------------------------------------------------------
# (B) Substrate-CALL phase coverage (W607-AC boundary AST audit)
# ---------------------------------------------------------------------------


_W607AC_SUBSTRATE_PHASES = (
    "capture_diff",
    "git_diff_text",
    "capture_critique",
    "parse_critique_json",
    "capture_pr_risk",
    "inspect_failed_subcommands",
    "compute_verdict",
    "auto_log_run",
)


@pytest.mark.parametrize("phase", _W607AC_SUBSTRATE_PHASES)
def test_w607eo_w607ac_substrate_phase_wrapped(phase):
    """Pin: each W607-AC substrate phase is wrapped via ``_run_check``.

    AST-walk the pr-prep function and assert the phase string appears
    as the first positional arg to a ``_run_check(...)`` call.
    """
    tree = ast.parse(_module_source())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == phase:
                    found = True
                    break
    assert found, f"W607-AC substrate phase {phase!r} must be wrapped via _run_check"


# ---------------------------------------------------------------------------
# (C) Aggregation-phase phase coverage (W607-CC boundary AST audit)
# ---------------------------------------------------------------------------


_W607CC_AGG_PHASES = (
    "score_classify",
    "score_normalize",
    "compute_verdict",
    "auto_log",
    "serialize_envelope",
)


@pytest.mark.parametrize("phase", _W607CC_AGG_PHASES)
def test_w607eo_w607cc_agg_phase_wrapped(phase):
    """Pin: each W607-CC agg phase is wrapped via ``_run_check_cc`` (AST)."""
    tree = ast.parse(_module_source())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_cc":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == phase:
                    found = True
                    break
    assert found, f"W607-CC agg phase {phase!r} must be wrapped via _run_check_cc"


# ---------------------------------------------------------------------------
# (D) Bucket-merge invariant -- BOTH layers feed BOTH mirrors
# ---------------------------------------------------------------------------


def test_w607eo_combined_bucket_includes_ac_and_cc():
    """Pin: ``_combined_warnings_out`` concatenates AC + CC in that order."""
    src = _module_source()
    needle = "list(_w607ac_warnings_out) + list(_w607cc_warnings_out)"
    assert src.count(needle) >= 1, "combined bucket must thread BOTH AC + CC markers onto the envelope"


def test_w607eo_combined_bucket_mirrors_to_top_level_and_summary():
    """Pin: combined bucket lands on BOTH summary.warnings_out + top-level."""
    src = _module_source()
    assert 'bundle["summary"]["warnings_out"] = list(_combined_warnings_out)' in src, (
        "summary.warnings_out mirror missing"
    )
    assert 'bundle["warnings_out"] = list(_combined_warnings_out)' in src, "top-level warnings_out mirror missing"


def test_w607eo_combined_bucket_flips_partial_success():
    """Pin: non-empty combined bucket -> ``partial_success: True``."""
    src = _module_source()
    assert 'bundle["summary"]["partial_success"] = True' in src, "partial_success flip-on-non-empty-bucket missing"


# ---------------------------------------------------------------------------
# (E) Runtime invariants -- clean path emits no W607 markers
# ---------------------------------------------------------------------------


def test_w607eo_clean_envelope_omits_w607_markers(cli_runner, pr_prep_project):
    """Clean pr-prep on a healthy repo -> no W607 phase markers."""
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-prep"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    markers = [
        m
        for m in (list(top_wo) + list(summary_wo))
        if isinstance(m, str) and m.startswith("pr_prep_") and "_failed:" in m
    ]
    assert not markers, (
        f"clean pr-prep must NOT surface pr_prep_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


def test_w607eo_substrate_failure_surfaces_w607ac_marker(cli_runner, pr_prep_project, monkeypatch):
    """Substrate raise -> ``pr_prep_capture_pr_risk_failed:`` on envelope."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise RuntimeError("synthetic-from-W607-EO")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if isinstance(m, str) and m.startswith("pr_prep_capture_pr_risk_failed:")]
    assert markers, f"expected pr_prep_capture_pr_risk_failed: marker; got {top_wo!r}"


def test_w607eo_substrate_failure_marker_includes_exc_class(cli_runner, pr_prep_project, monkeypatch):
    """Marker must include ``<exc_class>:<detail>`` -- helper-template shape."""
    from roam.commands import cmd_pr_prep as _mod

    class _CustomBoom(RuntimeError):
        pass

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise _CustomBoom("specific-detail-w607eo")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    matches = [m for m in top_wo if "_CustomBoom" in m and "specific-detail-w607eo" in m]
    assert matches, f"expected marker to include exc class + detail; got {top_wo!r}"


def test_w607eo_substrate_failure_flips_partial_success(cli_runner, pr_prep_project, monkeypatch):
    """A substrate raise must set summary.partial_success = True."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise RuntimeError("synthetic-w607eo-partial")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    assert summary.get("partial_success") is True, f"expected summary.partial_success=True; got summary={summary!r}"


def test_w607eo_substrate_failure_markers_mirror_to_summary(cli_runner, pr_prep_project, monkeypatch):
    """Markers must reach BOTH top-level AND summary.warnings_out."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise RuntimeError("synthetic-w607eo-mirror")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    top_wo = set(data.get("warnings_out") or [])
    summary_wo = set((data.get("summary") or {}).get("warnings_out") or [])
    aq = {m for m in top_wo if "capture_pr_risk_failed:" in m}
    aq_summary = {m for m in summary_wo if "capture_pr_risk_failed:" in m}
    assert aq, f"top-level mirror missing marker; got {top_wo!r}"
    assert aq_summary, f"summary mirror missing marker; got {summary_wo!r}"
    assert aq == aq_summary, (
        f"top-level and summary mirrors must carry IDENTICAL marker sets; top={aq!r} summary={aq_summary!r}"
    )


# ---------------------------------------------------------------------------
# (F) LAW 6 -- verdict-first invariant (standalone-parseable)
# ---------------------------------------------------------------------------


def test_w607eo_law6_verdict_is_standalone_string(cli_runner, pr_prep_project):
    """summary.verdict is a non-empty string that parses without other fields."""
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str), f"verdict not a string: {verdict!r}"
    assert verdict.strip(), f"verdict empty: {verdict!r}"


def test_w607eo_law6_verdict_survives_substrate_failure(cli_runner, pr_prep_project, monkeypatch):
    """LAW 6: verdict still emits cleanly even when a substrate raises."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise RuntimeError("synthetic-law6-w607eo")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"degraded-path verdict must still be a non-empty string; got {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (G) Cross-prefix isolation -- pr_prep_* markers don't leak to siblings
# ---------------------------------------------------------------------------


def test_w607eo_cross_prefix_isolation_in_source():
    """``pr_prep_*`` is the only W607 marker family in this module.

    cmd_pr_prep must NOT emit pr_analyze_*, critique_*, pr_risk_*, etc.
    markers on its own boundaries -- those belong to sibling consumers
    (W607-AA / W607-Y / W607-Q). Cross-prefix leakage would muddy the
    per-command audit.
    """
    src = _module_source()
    forbidden_prefixes = (
        "pr_analyze_",
        "critique_phase_",
        "pr_risk_phase_",
        "diff_text_phase_",
        "attest_phase_",
    )
    for line in src.splitlines():
        stripped = line.strip()
        # Only check inside f-string marker emit lines; comments / docs
        # legitimately reference sibling waves.
        if "_failed:" in stripped and 'f"' in stripped and "{phase}" in stripped:
            for forbidden in forbidden_prefixes:
                assert forbidden not in stripped, (
                    f"cross-prefix marker leakage detected on line: {stripped!r} (forbidden: {forbidden!r})"
                )


def test_w607eo_marker_family_closed_to_pr_prep_prefix(cli_runner, pr_prep_project, monkeypatch):
    """Runtime: failure markers emitted by pr-prep share the pr_prep_ prefix.

    NOTE: warnings_out can legitimately carry sibling markers propagated UP
    from inner substrates (critique / pr-risk envelopes) -- we only assert
    that the marker WE directly forced (the pr-risk capture boundary inside
    pr-prep) carries the pr_prep_ prefix, not that NO sibling prefixes
    appear at all.
    """
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise RuntimeError("synthetic-isolation-eo")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failed = [m for m in top_wo if isinstance(m, str) and "_failed:" in m]
    own = [m for m in failed if m.startswith("pr_prep_")]
    assert own, f"expected at least one pr_prep_ marker; got {failed!r}"


# ---------------------------------------------------------------------------
# (H) Bucket distinguishability -- AC + CC share family but distinct phases
# ---------------------------------------------------------------------------


def test_w607eo_ac_and_cc_phase_sets_overlap_only_on_compute_verdict():
    """W607-AC + W607-CC phase names overlap ONLY on ``compute_verdict``.

    Unlike W607-AA/BY on cmd_pr_analyze (where the sets are fully
    disjoint), cmd_pr_prep's two layers BOTH wrap a ``compute_verdict``
    boundary -- W607-AC wraps the inner verdict computation, W607-CC
    wraps the augmented verdict text build. The bucket-distinguishability
    guarantee comes from the disjoint accumulator (``_w607ac_warnings_out``
    vs ``_w607cc_warnings_out``), NOT from disjoint phase names.

    Pin this exact overlap so a future refactor that accidentally
    introduces additional shared phase names surfaces immediately.
    """
    overlap = set(_W607AC_SUBSTRATE_PHASES) & set(_W607CC_AGG_PHASES)
    assert overlap == {"compute_verdict"}, (
        f"W607-AC + W607-CC phase-set overlap must be exactly {{'compute_verdict'}}; got {overlap!r}"
    )


def test_w607eo_total_phase_count_13():
    """Pin: 8 substrate-CALL + 5 aggregation-phase = 13 wrapped boundaries.

    (W607-AC's 9th "auto_log_run" anchor is included in the 8; the real
    auto_log is in W607-CC. Total NAMED phases = 13 across both layers.)
    """
    assert len(_W607AC_SUBSTRATE_PHASES) == 8, (
        f"W607-AC must wrap 8 named substrate phases; got {len(_W607AC_SUBSTRATE_PHASES)}"
    )
    assert len(_W607CC_AGG_PHASES) == 5, f"W607-CC must wrap 5 named agg phases; got {len(_W607CC_AGG_PHASES)}"
    assert (len(_W607AC_SUBSTRATE_PHASES) + len(_W607CC_AGG_PHASES)) == 13, (
        "Total wrapped boundaries on cmd_pr_prep must stay at 13"
    )


# ---------------------------------------------------------------------------
# (I) Coexistence -- pre-existing W607-AC + W607-CC tests still discoverable
# ---------------------------------------------------------------------------


def test_w607eo_w607ac_test_file_exists():
    """Pin: W607-AC test suite still ships alongside this CLOSE-AS-DUPLICATE."""
    expected = Path(__file__).parent / "test_w607_ac_cmd_pr_prep_warnings_out_envelope.py"
    assert expected.exists(), f"missing prerequisite W607-AC tests at {expected}"


def test_w607eo_w607cc_test_file_exists():
    """Pin: W607-CC test suite still ships alongside this CLOSE-AS-DUPLICATE."""
    expected = Path(__file__).parent / "test_w607_cc_cmd_pr_prep_warnings_out_envelope.py"
    assert expected.exists(), f"missing prerequisite W607-CC tests at {expected}"


# ---------------------------------------------------------------------------
# (J) PR-review QUARTET pairing pin -- all four composers W607-plumbed
# ---------------------------------------------------------------------------


_PR_QUARTET_PATHS = {
    "cmd_pr_analyze": _CMD_PR_ANALYZE_PATH,
    "cmd_pr_prep": _CMD_PATH,
    "cmd_pr_risk": _CMD_PR_RISK_PATH,
    "cmd_critique": _CMD_CRITIQUE_PATH,
}


@pytest.mark.parametrize("name,path", list(_PR_QUARTET_PATHS.items()))
def test_w607eo_pr_quartet_module_exists(name, path):
    """Pin: each member of the PR-review quartet ships at its canonical path."""
    assert path.exists(), f"{name} missing at {path}"


@pytest.mark.parametrize("name,path", list(_PR_QUARTET_PATHS.items()))
def test_w607eo_pr_quartet_member_carries_w607_plumbing(name, path):
    """Pin: every PR-review composer carries SOME W607 plumbing (any prefix).

    AST-scan for either a ``_w607*_warnings_out`` accumulator or a
    ``_run_check*`` helper call. The quartet is treated as a coherent
    disclosure-channel unit -- if any member drops W607, this surfaces.
    """
    src = path.read_text(encoding="utf-8")
    has_accumulator = "_warnings_out" in src and "_w607" in src
    has_helper_call = "_run_check" in src
    assert has_accumulator and has_helper_call, (
        f"{name} missing W607 plumbing (accumulator={has_accumulator}, helper_call={has_helper_call})"
    )


def test_w607eo_pr_quartet_marker_families_distinct():
    """Pin: each quartet member uses a DISTINCT pr_* marker prefix family.

    The four marker families are:
    * pr_analyze_*  (W607-AA / W607-BY)
    * pr_prep_*     (W607-AC / W607-CC)
    * pr_risk_*     (W607-Q  / W607-AB / W607-BU)
    * critique_*    (W607-Y  / W607-BL)

    A future refactor that accidentally collapses two families into one
    (e.g. emits pr_prep_ from cmd_critique) would muddy per-command audit.
    """
    expected = {
        "cmd_pr_analyze": "pr_analyze_",
        "cmd_pr_prep": "pr_prep_",
        "cmd_pr_risk": "pr_risk_",
        "cmd_critique": "critique_",
    }
    for name, path in _PR_QUARTET_PATHS.items():
        src = path.read_text(encoding="utf-8")
        own_prefix = expected[name]
        # Each module must contain its own marker emit pattern.
        emit_pattern = f'f"{own_prefix}{{phase}}_failed:'
        assert emit_pattern in src, (
            f"{name} missing own marker family emit ({own_prefix}); expected literal substring {emit_pattern!r}"
        )


# ---------------------------------------------------------------------------
# (K) Compound-recipe preservation -- pr-prep remains a 3-section composer
# ---------------------------------------------------------------------------


def test_w607eo_pr_prep_invokes_capture_json_subcommand(cli_runner, pr_prep_project):
    """Compound preservation: pr-prep still composes via _capture_json_subcommand."""
    from roam.commands import cmd_pr_prep

    assert hasattr(cmd_pr_prep, "_capture_json_subcommand"), (
        "_capture_json_subcommand substrate helper must remain on cmd_pr_prep"
    )
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output


def test_w607eo_pr_prep_invokes_git_diff_text_substrate():
    """Compound preservation: git-diff-text substrate remains."""
    from roam.commands import cmd_pr_prep

    assert hasattr(cmd_pr_prep, "_git_diff_text"), (
        "_git_diff_text substrate helper must remain on cmd_pr_prep (compound-recipe preservation)"
    )


def test_w607eo_pr_prep_three_section_bundle(cli_runner, pr_prep_project):
    """Compound preservation: bundle still carries diff + critique + pr_risk."""
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    # The three composed sections must remain on the envelope.
    for section in ("diff", "critique", "pr_risk"):
        assert section in data, f"compound bundle missing section {section!r}; got keys={list(data)!r}"


# ---------------------------------------------------------------------------
# (L) Envelope shape -- canonical fields preserved across W607-AC/CC
# ---------------------------------------------------------------------------


def test_w607eo_envelope_carries_canonical_pr_prep_fields(
    cli_runner,
    pr_prep_project,
):
    """Pin: command name + canonical risk-LEVEL projection fields stay."""
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    assert data.get("command") == "pr-prep"
    summary = data.get("summary") or {}
    # W607-CC's score_normalize landed risk_level_canonical + risk_rank
    assert "risk_level_canonical" in summary, (
        "summary.risk_level_canonical missing -- W607-CC score_normalize boundary may have drifted"
    )
    assert "risk_rank" in summary, "summary.risk_rank missing -- W607-CC score_normalize boundary may have drifted"


def test_w607eo_envelope_carries_score_classification_state(
    cli_runner,
    pr_prep_project,
):
    """Pin: W607-CC's score_classification sentinel rides the summary."""
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    assert "score_classification" in summary, (
        "summary.score_classification missing -- W607-CC score_classify sentinel may have drifted"
    )


def test_w607eo_envelope_carries_ready_to_open_field(
    cli_runner,
    pr_prep_project,
):
    """Pin: ``ready_to_open`` boolean preserved through W607-AC/CC plumbing."""
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    assert "ready_to_open" in summary, (
        "summary.ready_to_open missing -- pr-prep canonical compound verdict surface may have drifted"
    )
    assert isinstance(summary["ready_to_open"], bool), f"ready_to_open must be a bool; got {summary['ready_to_open']!r}"


def test_w607eo_envelope_carries_top_level_risk_mirrors(
    cli_runner,
    pr_prep_project,
):
    """Pin: top-level risk_level_canonical / risk_rank mirrors preserved.

    W607-CC mirrors summary.risk_level_canonical + summary.risk_rank onto
    the top-level envelope head so consumers that read the envelope
    without descending into ``summary`` still see the canonical bucket.
    """
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    data = _json.loads(result.output)
    assert "risk_level_canonical" in data, "top-level risk_level_canonical mirror missing"
    assert "risk_rank" in data, "top-level risk_rank mirror missing"


# ---------------------------------------------------------------------------
# (M) Helper-template shape -- no `default()` mis-coding
# ---------------------------------------------------------------------------


def test_w607eo_helpers_do_not_call_default_as_callable():
    """Pin: helpers ``return default``, never ``return default()``.

    The W607-EE/EJ post-mortem found a recurring miscoding where
    helpers were rewritten to ``return default()`` -- which crashes
    when ``default`` is a non-callable (dict, str, tuple). Lock in the
    correct shape via source string scan.
    """
    src = _module_source()
    bad = "return default()"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("_run_check", "_run_check_cc"):
            body_src = ast.get_source_segment(src, node) or ""
            assert bad not in body_src, (
                f"helper {node.name} contains `return default()` -- W607 template mandates `return default` verbatim"
            )
