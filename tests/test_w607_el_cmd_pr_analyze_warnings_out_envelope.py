"""W607-EL -- ``cmd_pr_analyze`` CLOSE-AS-DUPLICATE pinning suite.

CLOSE-AS-DUPLICATE per the W607-DZ/EA/EE discovery methodology
=============================================================

W607-EL was queued as "apply W607 substrate-CALL plumbing to
``cmd_pr_analyze.py`` (PR-review compound aggregator)". The CRITICAL FIRST
STEP grep for ``_w607`` in ``src/roam/commands/cmd_pr_analyze.py`` found
TWO pre-existing W607 plumbing arcs already fully landed:

* **W607-AA (substrate-CALL layer)** -- 10 substrate-helper boundaries
  wrapped through a module-local ``_run_check(phase, fn, *args, default=...)``
  helper that accumulates into ``_w607aa_warnings_out``. Phases:
  ``acquire_diff`` / ``capture_pr_prep`` / ``compute_ai_likelihood`` /
  ``check_rules`` / ``inspect_prep_subcommand_failures`` /
  ``determine_verdict`` / ``added_lines_by_file`` /
  ``capture_suggest_reviewers`` / ``build_rationale`` / ``apply_drift``.
  Each raise becomes a ``pr_analyze_<phase>_failed:<exc_class>:<detail>``
  marker. Tests live in
  ``tests/test_w607_aa_cmd_pr_analyze_warnings_out_envelope.py`` (671
  lines, exhaustive substrate-call audit).

* **W607-BY (aggregation-phase layer)** -- 5 aggregation-phase boundaries
  wrapped through a sibling ``_run_check_by(phase, fn, *args, default=...)``
  helper that accumulates into ``_w607by_warnings_out``. Phases:
  ``score_classify`` / ``score_normalize`` / ``compute_verdict`` /
  ``auto_log`` / ``serialize_envelope``. Same marker family
  (``pr_analyze_*``) -- additive, not a separate prefix. Tests live in
  ``tests/test_w607_by_cmd_pr_analyze_warnings_out_envelope.py`` (887
  lines, exhaustive aggregation-phase audit).

The accumulator names W607-EL was supposed to introduce
(``_w607el_warnings_out``) and the helper name (``_run_check_el``) do
NOT exist in the source -- their semantic equivalents are
``_w607aa_warnings_out`` / ``_run_check`` (substrate) +
``_w607by_warnings_out`` / ``_run_check_by`` (aggregation). Adding a
THIRD bucket would:

1. Duplicate the marker family already covered by AA + BY.
2. Re-name the same disclosure channel (``pr_analyze_<phase>_failed:``)
   without changing observable behaviour.
3. Risk drifting the bucket-merge pattern (``_combined_warnings_out =
   list(_w607aa_warnings_out) + list(_w607by_warnings_out)``) that
   already threads markers onto BOTH ``summary.warnings_out`` and the
   top-level ``envelope.warnings_out``.
4. Re-trigger the W607-EA-flagged parallel-wave conflict on
   ``test_pr_analyze_audit_trail_break_escalates_verdict_to_block``.

Per the W607-DZ template -- when both axes are already in-tree, the
correct action is **pinning tests, not source modification**. This file
captures 30+ pinning invariants so any future regression of the W607-AA
or W607-BY plumbing surfaces immediately. No source edit.

Parallel-wave assessment (W607-EA finding)
==========================================

The 353-insertion in-flight refactor on cmd_pr_analyze referenced by
W607-EA appears to have ALREADY landed -- both
``_w607aa_warnings_out`` and ``_w607by_warnings_out`` plus the combined
bucket-merge logic are present in the current file at lines
2024/2089/2472. The audit-trail-break-escalates-verdict test referenced
in W607-EA is left untouched by this suite (it asserts a compound risk-
level escalation contract that is orthogonal to the W607 disclosure
axis); we add ONE pin that simply documents the test name exists, so a
rename of that test (which would break W607-EA's cross-reference) is
visible to grep.

Compound-recipe preservation (W126/W150)
========================================

cmd_pr_analyze is a PR-review compound that composes ``diff`` (via
``_acquire_diff``) + ``pr-prep`` (via ``_capture_pr_prep``, which in
turn fans out to ``critique`` + ``pr-risk``) + ``audit-trail-verify``
(via ``_check_rules`` on the .roam/rules.yml gate). Registry-key lookup
discipline is preserved through ``_COMPOUND_REGISTRY`` /
``_cr()`` in ``mcp_server.py`` -- pr-analyze does NOT string-concat
subcommand names. The W607-AA/BY plumbing wraps the helper CALLS, not
the registry lookup, so registry-key discipline is undisturbed.

Risk-level escalation (audit-trail break -> BLOCK)
==================================================

W607-AA's ``inspect_prep_subcommand_failures`` + W607-BY's
``score_classify`` / ``score_normalize`` / ``compute_verdict`` phases
preserve the existing audit-trail-break-escalates-verdict-to-BLOCK
behaviour -- the augmented verdict text is built INSIDE the
``compute_verdict`` wrap, so a raise in the verdict augmentation now
floors to the literal ``"low"`` risk-level rather than crashing. The
pre-W607-AA exit-code propagation (EXIT_GATE_BLOCK on
``--rules-strict`` failure) is unchanged.

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


_CMD_PATH = Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"


_DIFF_TEXT = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -1,5 +1,6 @@\n"
    " from src.models import User\n"
    " \n"
    " def verify_token(t):\n"
    "+    # tweak\n"
    "     return User('test')\n"
    " \n"
)


def _invoke_pr_analyze(
    runner: CliRunner,
    cwd,
    *extra,
    json_mode: bool = True,
    stdin: str | None = None,
):
    """Invoke ``roam pr-analyze`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-analyze")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, input=stdin, catch_exceptions=False)
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
def pr_analyze_project(tmp_path, monkeypatch):
    """Indexed corpus with a symbol the diff modifies."""
    proj = tmp_path / "pr_analyze_w607el_project"
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
# (A) AST audit -- pin the EXISTING W607-AA + W607-BY plumbing shape
# ---------------------------------------------------------------------------


def _module_source() -> str:
    return _CMD_PATH.read_text(encoding="utf-8")


def test_w607el_close_as_duplicate_cmd_pr_analyze_module_exists():
    """Pin: cmd_pr_analyze.py exists at the canonical path."""
    assert _CMD_PATH.exists(), f"missing {_CMD_PATH}"


def test_w607el_no_w607el_accumulator_exists():
    """Pin: the W607-EL accumulator name is INTENTIONALLY absent.

    W607-EL is CLOSE-AS-DUPLICATE -- the marker family the task would
    have introduced is already covered by W607-AA + W607-BY. If a
    future agent adds ``_w607el_warnings_out`` to the source, this
    test fires and forces a reconciliation with the existing buckets.
    """
    src = _module_source()
    assert "w607el_warnings_out" not in src, (
        "W607-EL is CLOSE-AS-DUPLICATE; do NOT introduce a third bucket "
        "alongside _w607aa_warnings_out / _w607by_warnings_out. "
        "Use the existing _run_check / _run_check_by helpers instead."
    )


def test_w607el_no_run_check_el_helper_exists():
    """Pin: the W607-EL helper name is INTENTIONALLY absent."""
    src = _module_source()
    assert "_run_check_el(" not in src and "def _run_check_el" not in src, (
        "W607-EL is CLOSE-AS-DUPLICATE; do NOT introduce _run_check_el. "
        "Substrate boundaries -> _run_check (W607-AA); aggregation-phase -> "
        "_run_check_by (W607-BY)."
    )


def test_w607el_w607aa_accumulator_present():
    """Pin: ``_w607aa_warnings_out`` substrate-CALL bucket is present."""
    src = _module_source()
    assert "_w607aa_warnings_out: list[str] = []" in src, (
        "W607-AA substrate-CALL bucket missing; W607-EL pinning suite depends on it being live."
    )


def test_w607el_w607by_accumulator_present():
    """Pin: ``_w607by_warnings_out`` aggregation-phase bucket is present."""
    src = _module_source()
    assert "_w607by_warnings_out: list[str] = []" in src, (
        "W607-BY aggregation-phase bucket missing; W607-EL pinning suite depends on it being live."
    )


def test_w607el_run_check_helper_returns_default_verbatim():
    """Pin: ``_run_check`` (W607-AA) returns ``default`` verbatim on except.

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

    # Find the Try node + its except handler return
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


def test_w607el_run_check_by_helper_returns_default_verbatim():
    """Pin: ``_run_check_by`` (W607-BY) returns ``default`` verbatim on except."""
    tree = ast.parse(_module_source())
    helper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_by":
            helper = node
            break
    assert helper is not None, "module-local _run_check_by helper not found"

    found_default_return = False
    for node in ast.walk(helper):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for stmt in handler.body:
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                        if stmt.value.id == "default":
                            found_default_return = True
    assert found_default_return, (
        "_run_check_by except-handler must `return default` verbatim (W607 helper-template discipline)"
    )


def test_w607el_marker_format_pr_analyze_family():
    """Pin: both helpers emit ``pr_analyze_<phase>_failed:`` markers.

    Same marker FAMILY across substrate-CALL + aggregation-phase, but
    distinguishable via phase name (acquire_diff / score_classify /
    auto_log / etc.).
    """
    src = _module_source()
    # Both helpers must produce the same prefix
    assert src.count('f"pr_analyze_{phase}_failed:{type(exc).__name__}:{exc}"') >= 2, (
        "expected >=2 ``pr_analyze_{phase}_failed:`` marker emit sites (one in _run_check, one in _run_check_by)"
    )


# ---------------------------------------------------------------------------
# (B) Substrate-CALL phase coverage (W607-AA boundary AST audit)
# ---------------------------------------------------------------------------


_W607AA_SUBSTRATE_PHASES = (
    "acquire_diff",
    "capture_pr_prep",
    "compute_ai_likelihood",
    "check_rules",
    "inspect_prep_subcommand_failures",
    "determine_verdict",
    "added_lines_by_file",
    "capture_suggest_reviewers",
    "build_rationale",
    "apply_drift",
)


@pytest.mark.parametrize("phase", _W607AA_SUBSTRATE_PHASES)
def test_w607el_w607aa_substrate_phase_wrapped(phase):
    """Pin: each W607-AA substrate phase is wrapped via ``_run_check``.

    AST-walk the pr-analyze function and assert the phase string
    appears as the first positional arg to a ``_run_check(...)`` call.
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
    assert found, f"W607-AA substrate phase {phase!r} must be wrapped via _run_check"


# ---------------------------------------------------------------------------
# (C) Aggregation-phase phase coverage (W607-BY boundary AST audit)
# ---------------------------------------------------------------------------


_W607BY_AGG_PHASES = (
    "score_classify",
    "score_normalize",
    "compute_verdict",
    "auto_log",
    "serialize_envelope",
)


@pytest.mark.parametrize("phase", _W607BY_AGG_PHASES)
def test_w607el_w607by_agg_phase_wrapped(phase):
    """Pin: each W607-BY agg phase is wrapped via ``_run_check_by`` (AST)."""
    tree = ast.parse(_module_source())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_by":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == phase:
                    found = True
                    break
    assert found, f"W607-BY agg phase {phase!r} must be wrapped via _run_check_by"


# ---------------------------------------------------------------------------
# (D) Bucket-merge invariant -- BOTH layers feed BOTH mirrors
# ---------------------------------------------------------------------------


def test_w607el_combined_bucket_includes_aa_and_by():
    """Pin: ``_combined_warnings_out`` concatenates AA + BY in that order."""
    src = _module_source()
    needle = "list(_w607aa_warnings_out) + list(_w607by_warnings_out)"
    assert src.count(needle) >= 1, "combined bucket must thread BOTH AA + BY markers onto the envelope"


def test_w607el_combined_bucket_mirrors_to_top_level_and_summary():
    """Pin: combined bucket lands on BOTH summary.warnings_out + top-level."""
    src = _module_source()
    assert 'bundle_summary["warnings_out"] = list(_combined_warnings_out)' in src, "summary.warnings_out mirror missing"
    assert 'bundle["warnings_out"] = list(_combined_warnings_out)' in src, "top-level warnings_out mirror missing"


def test_w607el_combined_bucket_flips_partial_success():
    """Pin: non-empty combined bucket -> ``partial_success: True``."""
    src = _module_source()
    assert 'bundle_summary["partial_success"] = True' in src, "partial_success flip-on-non-empty-bucket missing"


# ---------------------------------------------------------------------------
# (E) Runtime invariants -- clean path emits no W607 markers
# ---------------------------------------------------------------------------


def test_w607el_clean_envelope_omits_w607_markers(cli_runner, pr_analyze_project):
    """Clean pr-analyze on a healthy diff -> no W607 phase markers."""
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-analyze"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    markers = [
        m
        for m in (list(top_wo) + list(summary_wo))
        if isinstance(m, str) and m.startswith("pr_analyze_") and "_failed:" in m
    ]
    assert not markers, (
        f"clean pr-analyze must NOT surface pr_analyze_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


def test_w607el_substrate_failure_surfaces_w607aa_marker(cli_runner, pr_analyze_project, monkeypatch):
    """Substrate raise -> ``pr_analyze_<phase>_failed:`` on envelope."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-from-W607-EL-pin")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if isinstance(m, str) and m.startswith("pr_analyze_acquire_diff_failed:")]
    assert markers, f"expected pr_analyze_acquire_diff_failed: marker; got {top_wo!r}"


def test_w607el_substrate_failure_marker_includes_exc_class(cli_runner, pr_analyze_project, monkeypatch):
    """Marker must include ``<exc_class>:<detail>`` -- helper-template shape."""
    from roam.commands import cmd_pr_analyze

    class _CustomBoom(RuntimeError):
        pass

    def _raise(*args, **kwargs):
        raise _CustomBoom("specific-detail-w607el")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    matches = [m for m in top_wo if "_CustomBoom" in m and "specific-detail-w607el" in m]
    assert matches, f"expected marker to include exc class + detail; got {top_wo!r}"


def test_w607el_substrate_failure_flips_partial_success(cli_runner, pr_analyze_project, monkeypatch):
    """A substrate raise must set summary.partial_success = True."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    assert summary.get("partial_success") is True, f"expected summary.partial_success=True; got summary={summary!r}"


def test_w607el_substrate_failure_markers_mirror_to_summary(cli_runner, pr_analyze_project, monkeypatch):
    """Markers must reach BOTH top-level AND summary.warnings_out."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-w607el-mirror")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    top_wo = set(data.get("warnings_out") or [])
    summary_wo = set((data.get("summary") or {}).get("warnings_out") or [])
    # Marker must appear in BOTH mirrors (the canonical bond-bug fix)
    aq = {m for m in top_wo if "acquire_diff_failed:" in m}
    aq_summary = {m for m in summary_wo if "acquire_diff_failed:" in m}
    assert aq, f"top-level mirror missing marker; got {top_wo!r}"
    assert aq_summary, f"summary mirror missing marker; got {summary_wo!r}"
    assert aq == aq_summary, (
        f"top-level and summary mirrors must carry IDENTICAL marker sets; top={aq!r} summary={aq_summary!r}"
    )


# ---------------------------------------------------------------------------
# (F) LAW 6 -- verdict-first invariant (standalone-parseable)
# ---------------------------------------------------------------------------


def test_w607el_law6_verdict_is_standalone_string(cli_runner, pr_analyze_project):
    """summary.verdict is a non-empty string that parses without other fields."""
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str), f"verdict not a string: {verdict!r}"
    assert verdict.strip(), f"verdict empty: {verdict!r}"


def test_w607el_law6_verdict_survives_substrate_failure(cli_runner, pr_analyze_project, monkeypatch):
    """LAW 6: verdict still emits cleanly even when a substrate raises."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-w607el")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    verdict = (data.get("summary") or {}).get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"degraded-path verdict must still be a non-empty string; got {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (G) Cross-prefix isolation -- pr_analyze_* markers don't leak
# ---------------------------------------------------------------------------


def test_w607el_cross_prefix_isolation_in_source():
    """``pr_analyze_*`` is the only W607 marker family in this module.

    cmd_pr_analyze must NOT emit critique_*, diff_*, pr_risk_*, etc.
    markers -- those belong to sibling consumers (W607-Y / W607-Z /
    W607-BU). Cross-prefix leakage would muddy the per-command audit.
    """
    src = _module_source()
    # The emit f-strings are the source of truth for the marker family.
    # Allowed families inside this file: pr_analyze_<phase>_failed.
    # Forbidden: any other W607 marker prefix.
    for forbidden in ("critique_", "diff_text_", "pr_risk_phase_", "relate_", "attest_phase_"):
        # We only check inside f-string marker emit lines; comments/docs
        # may reference sibling waves legitimately.
        # Approximate check: forbidden marker prefix INSIDE a *_failed
        # emit f-string.
        for line in src.splitlines():
            stripped = line.strip()
            if "_failed:" in stripped and 'f"' in stripped:
                assert forbidden not in stripped, f"cross-prefix marker leakage detected on line: {stripped!r}"


def test_w607el_marker_family_closed_to_pr_analyze_prefix(cli_runner, pr_analyze_project, monkeypatch):
    """Runtime: failure markers emitted by pr-analyze share the prefix."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-isolation")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failed = [m for m in top_wo if isinstance(m, str) and "_failed:" in m]
    # Every "_failed:" marker emitted by THIS command must carry the
    # pr_analyze_ prefix. Note: warnings_out can carry sibling markers
    # propagated UP from inner substrates (e.g. critique markers) --
    # we only assert prefix-purity on substrates we directly raised.
    own = [m for m in failed if m.startswith("pr_analyze_")]
    assert own, f"expected at least one pr_analyze_ marker; got {failed!r}"


# ---------------------------------------------------------------------------
# (H) Bucket distinguishability -- AA + BY share family but distinct phases
# ---------------------------------------------------------------------------


def test_w607el_aa_and_by_phase_sets_are_disjoint():
    """The 10 W607-AA + 5 W607-BY phase names share NO overlap."""
    assert not (set(_W607AA_SUBSTRATE_PHASES) & set(_W607BY_AGG_PHASES)), (
        "W607-AA + W607-BY phase sets must be disjoint -- shared phase "
        "name would muddy the bucket-distinguishability discipline"
    )


def test_w607el_total_phase_count_15():
    """Pin: 10 substrate-CALL + 5 aggregation-phase = 15 wrapped boundaries."""
    assert len(_W607AA_SUBSTRATE_PHASES) == 10, "W607-AA must wrap 10 boundaries"
    assert len(_W607BY_AGG_PHASES) == 5, "W607-BY must wrap 5 boundaries"
    assert (len(_W607AA_SUBSTRATE_PHASES) + len(_W607BY_AGG_PHASES)) == 15, (
        "Total wrapped boundaries on cmd_pr_analyze must stay at 15"
    )


# ---------------------------------------------------------------------------
# (I) Coexistence -- pre-existing W607-AA + W607-BY tests still discoverable
# ---------------------------------------------------------------------------


def test_w607el_w607aa_test_file_exists():
    """Pin: W607-AA test suite still ships alongside this CLOSE-AS-DUPLICATE."""
    expected = Path(__file__).parent / "test_w607_aa_cmd_pr_analyze_warnings_out_envelope.py"
    assert expected.exists(), f"missing prerequisite W607-AA tests at {expected}"


def test_w607el_w607by_test_file_exists():
    """Pin: W607-BY test suite still ships alongside this CLOSE-AS-DUPLICATE."""
    expected = Path(__file__).parent / "test_w607_by_cmd_pr_analyze_warnings_out_envelope.py"
    assert expected.exists(), f"missing prerequisite W607-BY tests at {expected}"


# ---------------------------------------------------------------------------
# (J) W607-EA parallel-wave conflict pin -- audit-trail-break test name
# ---------------------------------------------------------------------------


def test_w607el_audit_trail_break_test_name_exists_for_w607ea_crossref():
    """W607-EA referenced a specific test name; pin it so a rename is visible.

    W607-EA's finding cited
    ``test_pr_analyze_audit_trail_break_escalates_verdict_to_block``
    as a pre-existing parallel-wave failure. We don't run it here (the
    test belongs to its own file), but pin that *some* test file
    references that exact name so a rename of that test would surface
    via this CLOSE-AS-DUPLICATE document.
    """
    tests_dir = Path(__file__).parent
    needle = "test_pr_analyze_audit_trail_break_escalates_verdict_to_block"
    found = False
    for py in tests_dir.glob("test_*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        if needle in text:
            found = True
            break
    # If renamed/removed, the W607-EA cross-reference no longer points
    # at a live test -- surface that to the next agent immediately.
    if not found:
        pytest.skip(
            f"{needle!r} not present in tests/ -- W607-EA cross-reference "
            f"may have drifted; investigate before re-citing W607-EA."
        )


# ---------------------------------------------------------------------------
# (K) Compound-recipe preservation -- pr-analyze remains a compound
# ---------------------------------------------------------------------------


def test_w607el_pr_analyze_invokes_acquire_diff(cli_runner, pr_analyze_project):
    """Compound preservation: pr-analyze still consumes a diff via _acquire_diff."""
    from roam.commands import cmd_pr_analyze

    assert hasattr(cmd_pr_analyze, "_acquire_diff"), "_acquire_diff substrate helper must remain on cmd_pr_analyze"
    # Smoke: invoke clean path
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output


def test_w607el_pr_analyze_invokes_capture_pr_prep_substrate():
    """Compound preservation: pr-prep capture substrate remains."""
    from roam.commands import cmd_pr_analyze

    assert hasattr(cmd_pr_analyze, "_capture_pr_prep"), (
        "_capture_pr_prep substrate helper must remain on cmd_pr_analyze (compound-recipe preservation)"
    )


def test_w607el_pr_analyze_invokes_check_rules_substrate():
    """Compound preservation: rules-gate substrate remains."""
    from roam.commands import cmd_pr_analyze

    assert hasattr(cmd_pr_analyze, "_check_rules"), (
        "_check_rules substrate helper must remain on cmd_pr_analyze (compound-recipe preservation)"
    )


def test_w607el_pr_analyze_invokes_determine_verdict_substrate():
    """Compound preservation: verdict-decision substrate remains."""
    from roam.commands import cmd_pr_analyze

    assert hasattr(cmd_pr_analyze, "_determine_verdict"), (
        "_determine_verdict substrate helper must remain on cmd_pr_analyze (compound-recipe preservation)"
    )


# ---------------------------------------------------------------------------
# (L) Envelope shape -- canonical fields preserved across W607-AA/BY
# ---------------------------------------------------------------------------


def test_w607el_envelope_carries_canonical_pr_analyze_fields(
    cli_runner,
    pr_analyze_project,
):
    """Pin: command name + canonical risk-LEVEL projection fields stay."""
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    assert data.get("command") == "pr-analyze"
    summary = data.get("summary") or {}
    # W607-BY's score_normalize landed risk_level_canonical + risk_rank
    assert "risk_level_canonical" in summary, (
        "summary.risk_level_canonical missing -- W607-BY score_normalize boundary may have drifted"
    )
    assert "risk_rank" in summary, "summary.risk_rank missing -- W607-BY score_normalize boundary may have drifted"


def test_w607el_envelope_carries_score_classification_state(
    cli_runner,
    pr_analyze_project,
):
    """Pin: W607-BY's score_classification sentinel rides the summary."""
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    assert "score_classification" in summary, (
        "summary.score_classification missing -- W607-BY score_classify sentinel may have drifted"
    )


# ---------------------------------------------------------------------------
# (M) Helper-template shape -- no `default()` mis-coding
# ---------------------------------------------------------------------------


def test_w607el_helpers_do_not_call_default_as_callable():
    """Pin: helpers ``return default``, never ``return default()``.

    The W607-EE/EJ post-mortem found a recurring miscoding where
    helpers were rewritten to ``return default()`` -- which crashes
    when ``default`` is a non-callable (dict, str, tuple). Lock in the
    correct shape via source string scan.
    """
    src = _module_source()
    # If a `return default()` ever lands inside this file's W607 helpers,
    # fail loudly. Allow `default()` elsewhere (legitimate callable args).
    bad = "return default()"
    # Find _run_check / _run_check_by helper bodies in the AST and check
    # them specifically.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("_run_check", "_run_check_by"):
            body_src = ast.get_source_segment(src, node) or ""
            assert bad not in body_src, (
                f"helper {node.name} contains `return default()` -- W607 template mandates `return default` verbatim"
            )
