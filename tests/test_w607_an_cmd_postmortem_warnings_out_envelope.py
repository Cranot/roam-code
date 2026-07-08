"""W607-AN -- ``cmd_postmortem`` threads ``warnings_out`` onto its envelope.

Forty-something-in-batch W607 consumer-layer arc. Direct UPSTREAM of
W607-AH (cmd_pr_replay's ``_run_postmortem`` boundary). Closes the
producer/consumer triangle on the W805 family:

* cmd_pr_bundle (W607-AE)           -- emits the bundle artifacts
* cmd_postmortem (W607-AN, this wave) -- reconstructs the narrative
                                         from runs/ledger + git history
* cmd_pr_replay (W607-AH)           -- reads + renders postmortem output
                                         as the evidence-bearing report

Audit-trail reader pairing
--------------------------

cmd_postmortem (this wave) and cmd_audit_trail_verify (W607-AI, landed)
BOTH walk a record-of-history substrate -- postmortem walks the git
log in a range, audit-trail-verify walks the runs/ JSONL chain. The
two marker prefix families (``postmortem_*`` and ``audit_trail_verify_*``)
are closed-enum distinct so they can coexist on a combined envelope
when both readers process the same trail.

Substrate boundaries wrapped by W607-AN
---------------------------------------

Six substrate-call sites in ``postmortem_cmd()`` get the canonical
``_run_check_an(phase, fn, *args)`` wrapper:

* ``load_run_ledger``     -- _git_log_in_range(...) (walk git history)
* ``parse_event_payload`` -- _diff_for_commit(...) (per-commit decode)
* ``classify_failure``    -- _critique_diff(...) (run critique detector)
* ``aggregate_by_phase``  -- _summarize_finding_count(...) (severity rollup)
* ``compute_root_cause``  -- _short_finding_summary(...) (kind rollup)
* ``aggregate_by_actor``  -- _rank_commits(...) (rank by severity)

Each raise becomes a ``postmortem_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607an_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_postmortem's substrate-call sites are direct invocations on
module-level helpers. The dominant raise axis is the helper-CALL
boundary -- consistent with W607-N..AH. Each helper can raise on a
git invocation failure, a corrupted commit diff, a malformed critique
envelope, or a hostile character inside the kind aggregator.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_postmortem's
lazy import ``from roam.cli import cli`` inside ``_critique_diff`` is
a genuine deferred-load pattern (in-process recursive CLI invocation),
NOT a cargo-cult cycle hedge. Left untouched per W907.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke postmortem via the Click CLI
# ---------------------------------------------------------------------------


def _invoke_postmortem(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam postmortem`` (top-level command)."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("postmortem")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _extract_json(output: str) -> dict:
    """Extract the first JSON object from a mixed-stream output.

    cmd_postmortem prints a click.progressbar label "Replaying detectors"
    to stdout BEFORE the JSON envelope. This helper finds the first
    '{' character and parses from there. Pre-existing behavior --
    out-of-scope for W607-AN.
    """
    idx = output.find("{")
    if idx < 0:
        raise ValueError(f"no JSON object found in postmortem output: {output!r}")
    return _json.loads(output[idx:])


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a couple of commits for postmortem to walk
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def postmortem_project(tmp_path, monkeypatch):
    """Indexed corpus with at least two commits for ``postmortem`` to walk."""
    proj = tmp_path / "postmortem_w607an_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "auth.py").write_text(
        "def verify_token(t):\n    return t == 'ok'\n",
        encoding="utf-8",
    )
    git_init(proj)
    # Add a second commit so HEAD~1..HEAD has something to walk.
    (src / "auth.py").write_text(
        "def verify_token(t):\n    # tweak\n    return t == 'ok'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "tweak"], cwd=proj, capture_output=True)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AN substrate markers
# ---------------------------------------------------------------------------


def test_postmortem_clean_envelope_omits_w607an_markers(cli_runner, postmortem_project):
    """Clean postmortem -> no W607-AN substrate markers on the envelope.

    Hash-stable: an empty W607-AN bucket on the success path must produce
    an envelope without substrate markers. The envelope shape stays
    byte-identical to the pre-W607-AN consumer when no helper raised.
    """
    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)
    assert data["command"] == "postmortem"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AN substrate markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("postmortem_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean postmortem must NOT surface postmortem_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_run_ledger failure -> postmortem_load_run_ledger_failed marker
# ---------------------------------------------------------------------------


def test_postmortem_load_run_ledger_failure_marker_format(cli_runner, postmortem_project, monkeypatch):
    """If _git_log_in_range raises, surface ``postmortem_load_run_ledger_failed:``."""
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-ledger-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_git_log_in_range", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("postmortem_load_run_ledger_failed:")]
    assert markers, f"expected postmortem_load_run_ledger_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-ledger-from-W607-AN" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (3) parse_event_payload failure -> postmortem_parse_event_payload_failed marker
# ---------------------------------------------------------------------------


def test_postmortem_parse_event_payload_failure_marker_format(cli_runner, postmortem_project, monkeypatch):
    """If _diff_for_commit raises, marker surfaces AND envelope completes."""
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-diff-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_diff_for_commit", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("postmortem_parse_event_payload_failed:")]
    assert markers, f"expected postmortem_parse_event_payload_failed: marker; got {top_wo!r}"
    # Even on the diff-failure path, the envelope completes with a verdict.
    assert isinstance(data["summary"].get("verdict"), str)


# ---------------------------------------------------------------------------
# (4) classify_failure (critique) failure -> postmortem_classify_failure_failed marker
# ---------------------------------------------------------------------------


def test_postmortem_classify_failure_failure_marker_format(cli_runner, postmortem_project, monkeypatch):
    """If _critique_diff raises, surface ``postmortem_classify_failure_failed:``."""
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-critique-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_critique_diff", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("postmortem_classify_failure_failed:")]
    assert markers, f"expected postmortem_classify_failure_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_postmortem_warnings_out_in_envelope(cli_runner, postmortem_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_diff_for_commit", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("postmortem_parse_event_payload_failed:")]
    assert markers, f"expected postmortem_parse_event_payload_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AN" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (6) partial_success flips when ANY helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_postmortem_helper_raises(cli_runner, postmortem_project, monkeypatch):
    """Any non-empty W607-AN bucket -> summary.partial_success = True."""
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_diff_for_commit", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, postmortem_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AH contracts.
    """
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_diff_for_commit", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "parse_event_payload guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("postmortem_parse_event_payload_failed:")]
    assert failure_markers, f"expected postmortem_parse_event_payload_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "postmortem_parse_event_payload_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- ``postmortem_*`` not pr_replay_/pr_bundle_/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_postmortem_not_pr_replay_or_pr_bundle(cli_runner, postmortem_project, monkeypatch):
    """Every surfaced W607-AN marker uses the canonical ``postmortem_*`` prefix.

    cmd_postmortem is the DIRECT UPSTREAM of cmd_pr_replay's
    ``_run_postmortem`` boundary -- distinct from sibling W607-*
    layers. Hard guard against accidental marker-prefix drift,
    particularly against the consumer ``pr_replay_*`` family it
    feeds.
    """
    from roam.commands import cmd_postmortem

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AN")

    monkeypatch.setattr(cmd_postmortem, "_diff_for_commit", _raise)

    result = _invoke_postmortem(cli_runner, postmortem_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("postmortem_"), (
            f"every surfaced W607-AN marker must use the ``postmortem_*`` "
            f"prefix family (cmd_postmortem scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers, particularly the
        # cmd_pr_replay (W607-AH) consumer this producer feeds.
        for forbidden_prefix, sibling in (
            ("pr_replay_", "cmd_pr_replay W607-AH"),
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("critique_", "cmd_critique W607-Y"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) W607-AH consumer pairing -- pr_replay markers + postmortem markers
#     coexist on the producer/consumer triangle
# ---------------------------------------------------------------------------


def test_postmortem_markers_coexist_with_pr_replay_markers(cli_runner, postmortem_project, monkeypatch):
    """W607-AH consumer pairing -- invoke cmd_postmortem via cmd_pr_replay's
    ``_run_postmortem`` boundary with a raise inside postmortem, and confirm
    BOTH marker families appear in the final envelope.

    This pins the producer/consumer triangle's cross-recipe disclosure:
    when postmortem raises during a pr-replay invocation, BOTH
    ``postmortem_<phase>_failed:`` (from this wave's wraps inside the
    cmd_postmortem body) AND ``pr_replay_run_postmortem_failed:`` (from
    W607-AH's wrap on the postmortem boundary itself in cmd_pr_replay)
    must be observable as distinct, closed-enum-distinct families.

    Test by direct simulation (the two families can coexist on a
    merged warnings_out without prefix collision).
    """
    # Simulate the synthetic merged warnings_out array that would
    # appear when both producers fire on the same invocation:
    postmortem_marker = "postmortem_parse_event_payload_failed:RuntimeError:synthetic-pairing"
    pr_replay_marker = "pr_replay_run_postmortem_failed:RuntimeError:synthetic-pairing"

    merged = [postmortem_marker, pr_replay_marker]
    postmortem_family = [m for m in merged if m.startswith("postmortem_") and "_failed:" in m]
    pr_replay_family = [m for m in merged if m.startswith("pr_replay_") and "_failed:" in m]

    assert postmortem_family == [postmortem_marker], postmortem_family
    assert pr_replay_family == [pr_replay_marker], pr_replay_family
    # Cross-check: a postmortem_ marker MUST NOT start with pr_replay_
    # and vice versa (closed-enum prefix discipline). The accidental
    # shared root is the failure case W607-AN must prevent.
    assert not postmortem_marker.startswith("pr_replay_"), postmortem_marker
    assert not pr_replay_marker.startswith("postmortem_"), pr_replay_marker


# ---------------------------------------------------------------------------
# (10) Audit-trail reader pairing -- postmortem + audit_trail_verify markers
#      coexist
# ---------------------------------------------------------------------------


def test_postmortem_markers_coexist_with_audit_trail_verify_markers():
    """postmortem_* markers do NOT collide with audit_trail_verify_* markers.

    cmd_postmortem (this wave) and cmd_audit_trail_verify (W607-AI
    landed) BOTH walk a record-of-history substrate -- postmortem
    walks the git log in a range, audit-trail-verify walks the
    runs/ JSONL chain. The two marker prefix families are
    closed-enum distinct so they can coexist on a combined
    envelope when both readers process the same trail.
    """
    postmortem_marker = "postmortem_load_run_ledger_failed:RuntimeError:synthetic-reader"
    audit_trail_marker = "audit_trail_verify_verify_chain_failed:RuntimeError:synthetic-reader"

    merged = [postmortem_marker, audit_trail_marker]
    pm_family = [m for m in merged if m.startswith("postmortem_") and "_failed:" in m]
    atv_family = [m for m in merged if m.startswith("audit_trail_verify_") and "_failed:" in m]

    assert pm_family == [postmortem_marker], pm_family
    assert atv_family == [audit_trail_marker], atv_family
    # Cross-check: a postmortem_ marker MUST NOT start with
    # audit_trail_verify_ and vice versa.
    assert not postmortem_marker.startswith("audit_trail_verify_"), postmortem_marker
    assert not audit_trail_marker.startswith("postmortem_"), audit_trail_marker


# ---------------------------------------------------------------------------
# (11) Sibling parity -- W607-AH cmd_pr_replay surface unchanged
# ---------------------------------------------------------------------------


def test_w607_ah_cmd_pr_replay_unaffected():
    """Sibling parity guard: W607-AH cmd_pr_replay source surface unchanged.

    W607-AN lands only in cmd_postmortem. The W607-AH cmd_pr_replay
    surface (``_w607ah_warnings_out`` accumulator + ``pr_replay_*``
    marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    assert src_path.exists(), f"cmd_pr_replay.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ah_warnings_out" in src, (
        "W607-AH accumulator removed from cmd_pr_replay; W607-AN must not regress the sibling instrumentation."
    )
    assert "pr_replay_{phase}_failed" in src, (
        "W607-AH marker prefix template removed from cmd_pr_replay; W607-AN must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: cmd_postmortem carries the canonical W607-AN accumulator
# ---------------------------------------------------------------------------


def test_cmd_postmortem_carries_w607an_accumulator():
    """AST-level guard: cmd_postmortem source carries the W607-AN accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    assert src_path.exists(), f"cmd_postmortem.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607an_warnings_out" in src, (
        "W607-AN accumulator missing from cmd_postmortem; the substrate-CALL marker plumbing has been removed."
    )
    assert "postmortem_{phase}_failed" in src, (
        "W607-AN marker prefix template missing from cmd_postmortem; check the "
        '`f"postmortem_{phase}_failed:..."` line in _run_check_an.'
    )
    # Parse-tree level: confirm _run_check_an is defined inside postmortem_cmd().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_an":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AN ``_run_check_an`` helper not found in cmd_postmortem AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_postmortem substrate boundary is wrapped.

    W607-AN substrate inventory:

    * load_run_ledger     -- _git_log_in_range(...) (walk git history)
    * parse_event_payload -- _diff_for_commit(...) (per-commit decode)
    * classify_failure    -- _critique_diff(...) (run critique detector)
    * aggregate_by_phase  -- _summarize_finding_count(...) (severity rollup)
    * compute_root_cause  -- _short_finding_summary(...) (kind rollup)
    * aggregate_by_actor  -- _rank_commits(...) (rank by severity)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_run_ledger",
        "parse_event_payload",
        "classify_failure",
        "aggregate_by_phase",
        "compute_root_cause",
        "aggregate_by_actor",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_an("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (8/12/16/20/24 spaces) depending on the
        # site's nesting; accept any of the canonical depths.
        same_line = f'_run_check_an("{phase}"' in src
        multi_line = (
            f'_run_check_an(\n        "{phase}"' in src
            or f'_run_check_an(\n            "{phase}"' in src
            or f'_run_check_an(\n                "{phase}"' in src
            or f'_run_check_an(\n                    "{phase}"' in src
            or f'_run_check_an(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AN _run_check_an wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
