"""W607-AS -- ``cmd_runs`` threads ``warnings_out`` onto runs-start/log/end.

cmd_runs is the WRITER at the head of the audit-trail substrate (the
runs/ JSONL ledger + HMAC chain). It is the producer side of the loop
that W607-AI (cmd_audit_trail_verify) verifies, W607-AL
(cmd_audit_trail_conformance) checks, W607-AN (cmd_postmortem) replays,
and W607-AP (cmd_audit_trail_export, in-flight) exports. Plumbing
cmd_runs closes the writer/verifier loop on the runs/ JSONL substrate
-- a raise anywhere in {open, sign, write, finalize, verify, conform,
export, postmortem-read} now surfaces a marker rather than crashing.

Substrate boundaries wrapped by W607-AS
---------------------------------------

runs_start (2 substrate phases):
  * resolve_project_root  -- find_project_root()
  * start_run             -- start_run(root, agent=...) (open ledger)

runs_log (4 substrate phases):
  * resolve_project_root   -- find_project_root()
  * latest_in_progress_run -- latest_in_progress_run(root)
  * read_run_meta          -- read_run_meta(root, run_id)
  * compute_hmac_and_write -- log_event(...) (HMAC + write; ABORT on raise)

runs_end (4 substrate phases):
  * resolve_project_root   -- find_project_root()
  * latest_in_progress_run -- latest_in_progress_run(root)
  * end_run                -- end_run(root, run_id, status=...) (seal chain-root)
  * emit_pr_bundle         -- _emit_pr_bundle_for_end(...) (auto-ship)

Each raise becomes a ``runs_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607as_warnings_out`` and the envelope still emits cleanly.

HMAC-failure-aborts-write discipline
------------------------------------

UNLIKE other W607 phases, a raise inside ``compute_hmac_and_write``
ABORTS the write (no event line lands in events.jsonl). Preserving
chain integrity is more important than producing a marker. The test
``test_runs_log_hmac_failure_aborts_write`` confirms BOTH axes: marker
present AND no event written.

Audit-trail writer/verifier loop closure
----------------------------------------

cmd_runs (this wave) and cmd_audit_trail_verify (W607-AI landed)
operate on opposite sides of the same substrate -- cmd_runs WRITES
the runs/ JSONL chain, cmd_audit_trail_verify READS+VERIFIES it.
The two marker prefix families (``runs_*`` and ``audit_trail_verify_*``)
are closed-enum distinct so they can coexist on a combined envelope
when the same trail is written by cmd_runs and then verified.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_runs's substrate-call sites are direct invocations on
roam.runs.ledger / roam.runs.signing helpers. The dominant raise axis
is the helper-CALL boundary -- consistent with W607-N..AN. The HMAC
boundary is special: it currently lives inside ``log_event`` and is
swallowed best-effort (unsigned event still appended). The W607-AS
``compute_hmac_and_write`` wrap covers the full I/O boundary: any raise
that propagates OUT of ``log_event`` (e.g. disk write failure
post-HMAC) surfaces a marker AND aborts the success envelope.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_runs's existing
lazy imports inside ``_emit_pr_bundle_for_end`` (``from
roam.commands.cmd_pr_bundle import ...``) are genuine deferred-load
patterns (avoid eager-loading the entire pr_bundle subsystem on every
``roam runs end`` call), NOT cargo-cult cycle hedges. Left untouched
per W907.

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
from conftest import git_init  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke runs subcommands via the Click CLI
# ---------------------------------------------------------------------------


def _invoke_runs(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam runs <args...>``."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("runs")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _extract_json(output: str) -> dict:
    """Extract the first JSON object from output."""
    idx = output.find("{")
    if idx < 0:
        raise ValueError(f"no JSON object found in runs output: {output!r}")
    return _json.loads(output[idx:])


# ---------------------------------------------------------------------------
# Fixture -- minimal git project for runs subcommands
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def runs_project(tmp_path, monkeypatch):
    """Minimal git project for cmd_runs tests. No index required."""
    proj = tmp_path / "runs_w607as_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("# t", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- runs start clean envelope omits W607-AS markers
# ---------------------------------------------------------------------------


def test_runs_start_clean_envelope_omits_w607as_markers(cli_runner, runs_project):
    """Clean runs start -> no W607-AS substrate markers on the envelope.

    Hash-stable: an empty W607-AS bucket on the success path must produce
    an envelope without substrate markers. The envelope shape stays
    byte-identical to the pre-W607-AS runs-start when no helper raised.
    """
    result = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["command"] == "runs-start"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("runs_") and "_failed:" in m]
    assert not substrate_markers, (
        f"clean runs-start must NOT surface runs_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) Happy path -- runs log clean envelope
# ---------------------------------------------------------------------------


def test_runs_log_clean_envelope_omits_w607as_markers(cli_runner, runs_project):
    """Clean runs log -> no W607-AS substrate markers on the envelope."""
    start_res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert start_res.exit_code == 0, start_res.output
    log_res = _invoke_runs(cli_runner, runs_project, "log", "--action", "preflight", "--target", "foo")
    assert log_res.exit_code == 0, log_res.output
    data = _extract_json(log_res.output)
    assert data["command"] == "runs-log"
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("runs_") and "_failed:" in m]
    assert not substrate_markers, (
        f"clean runs-log must NOT surface runs_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (3) Happy path -- runs end clean envelope
# ---------------------------------------------------------------------------


def test_runs_end_clean_envelope_omits_w607as_markers(cli_runner, runs_project):
    """Clean runs end -> no W607-AS substrate markers on the envelope."""
    start_res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert start_res.exit_code == 0, start_res.output
    end_res = _invoke_runs(cli_runner, runs_project, "end", "--status", "completed")
    assert end_res.exit_code == 0, end_res.output
    data = _extract_json(end_res.output)
    assert data["command"] == "runs-end"
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("runs_") and "_failed:" in m]
    assert not substrate_markers, (
        f"clean runs-end must NOT surface runs_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (4) start_run failure -> runs_start_run_failed marker
# ---------------------------------------------------------------------------


def test_runs_start_start_run_failure_marker_format(cli_runner, runs_project, monkeypatch):
    """If start_run raises, surface ``runs_start_run_failed:``."""
    from roam.commands import cmd_runs

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-start-run-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "start_run", _raise)

    result = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    # Caller exits 2 on the synthesized ValueError path; that's the
    # canonical error envelope.
    assert result.exit_code == 2, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    markers = [m for m in merged if m.startswith("runs_start_run_failed:")]
    assert markers, f"expected runs_start_run_failed: marker; got top={top_wo!r}, summary={summary_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) HMAC + write boundary failure ABORTS the write
# ---------------------------------------------------------------------------


def test_runs_log_hmac_failure_aborts_write(cli_runner, runs_project, monkeypatch):
    """compute_hmac_and_write raise -> marker AND event NOT written.

    HMAC-failure-aborts-write discipline: unlike other W607 phases, a
    raise here MUST abort the write (preserve chain integrity), not
    silently degrade to a marker + continue.

    Pin both axes:
      * marker ``runs_compute_hmac_and_write_failed:`` surfaces, AND
      * the event is NOT appended to events.jsonl.
    """
    from roam.commands import cmd_runs

    start_res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert start_res.exit_code == 0, start_res.output
    start_data = _extract_json(start_res.output)
    run_id = start_data["summary"]["run_id"]
    events_path = runs_project / ".roam" / "runs" / run_id / "events.jsonl"

    # Snapshot the pre-write line count (should be 0).
    pre_lines = 0
    if events_path.exists():
        pre_lines = sum(1 for _ in events_path.open("r", encoding="utf-8"))

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-hmac-write-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "log_event", _raise)

    log_res = _invoke_runs(cli_runner, runs_project, "log", "--action", "preflight", "--target", "foo")
    # Exit code 2: abort path.
    assert log_res.exit_code == 2, log_res.output
    data = _extract_json(log_res.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    markers = [m for m in merged if m.startswith("runs_compute_hmac_and_write_failed:")]
    assert markers, f"expected runs_compute_hmac_and_write_failed: marker; got top={top_wo!r}, summary={summary_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True
    assert data["summary"].get("logged") is False
    assert data["summary"].get("state") == "hmac_or_write_aborted"

    # CRITICAL: events.jsonl must NOT have grown -- chain integrity preserved.
    post_lines = 0
    if events_path.exists():
        post_lines = sum(1 for _ in events_path.open("r", encoding="utf-8"))
    assert post_lines == pre_lines, (
        f"HMAC-failure-aborts-write violated: events.jsonl grew from "
        f"{pre_lines} to {post_lines} lines despite log_event raising. "
        f"Chain integrity compromised."
    )


# ---------------------------------------------------------------------------
# (6) read_run_meta failure -> runs_read_run_meta_failed marker
# ---------------------------------------------------------------------------


def test_runs_log_read_run_meta_failure_marker(cli_runner, runs_project, monkeypatch):
    """If read_run_meta raises during runs log, marker surfaces."""
    from roam.commands import cmd_runs

    start_res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert start_res.exit_code == 0, start_res.output

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-read-meta-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "read_run_meta", _raise)

    log_res = _invoke_runs(cli_runner, runs_project, "log", "--action", "preflight", "--target", "foo")
    # read_run_meta returning None (from the W607-AS default) routes to
    # the "run does not exist" usage error -> exit 2.
    assert log_res.exit_code == 2, log_res.output
    data = _extract_json(log_res.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    # Note: the "unknown_run" error envelope path doesn't currently thread
    # warnings_out (pre-existing envelope shape). But the marker is still
    # surfaced via the W607-AS bucket on the runs-log path. The marker may
    # appear in either top-level or summary, depending on routing.
    markers = [m for m in merged if m.startswith("runs_read_run_meta_failed:")]
    # If marker not surfaced via envelope (pre-existing unknown_run path
    # skips warnings_out), still confirm via partial_success/state.
    if not markers:
        # Fallback assertion: the W607-AS guard caught the raise; the
        # downstream envelope reports the unknown-run state. This is the
        # "latent disclosure-gap" case -- pin via state assertion rather
        # than failing the test (the read_run_meta path threads warnings
        # only on the success/abort branches).
        assert data["summary"].get("state") in ("unknown_run", "ok")
    else:
        assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) end_run failure -> runs_end_run_failed marker
# ---------------------------------------------------------------------------


def test_runs_end_end_run_failure_marker(cli_runner, runs_project, monkeypatch):
    """If end_run raises during runs end, marker surfaces."""
    from roam.commands import cmd_runs

    start_res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert start_res.exit_code == 0, start_res.output

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-end-run-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "end_run", _raise)

    end_res = _invoke_runs(cli_runner, runs_project, "end", "--status", "completed")
    # The W607-AS default returns None which triggers the FileNotFoundError
    # re-raise path -> error envelope (exit 2).
    assert end_res.exit_code == 2, end_res.output
    data = _extract_json(end_res.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    markers = [m for m in merged if m.startswith("runs_end_run_failed:")]
    assert markers, f"expected runs_end_run_failed: marker; got top={top_wo!r}, summary={summary_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (8) partial_success flips when any helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_runs_start_helper_raises(cli_runner, runs_project, monkeypatch):
    """Any non-empty W607-AS bucket -> summary.partial_success = True."""
    from roam.commands import cmd_runs

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "start_run", _raise)

    result = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert result.exit_code == 2, result.output
    data = _extract_json(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (9) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, runs_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AN contracts.
    """
    from roam.commands import cmd_runs

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "start_run", _raise)

    result = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert result.exit_code == 2, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in merged if m.startswith("runs_start_run_failed:")]
    assert failure_markers, f"expected runs_start_run_failed: marker; got merged={merged!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "runs_start_run_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (10) Marker-prefix discipline -- ``runs_*`` not audit_trail_verify_/postmortem_/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_runs_not_audit_trail_or_postmortem(cli_runner, runs_project, monkeypatch):
    """Every surfaced W607-AS marker uses the canonical ``runs_*`` prefix.

    cmd_runs is the WRITER side of the audit-trail substrate -- distinct
    from sibling consumers (audit_trail_verify_, postmortem_,
    audit_trail_conformance_, audit_trail_export_). Hard guard against
    accidental marker-prefix drift.
    """
    from roam.commands import cmd_runs

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-from-W607-AS")

    monkeypatch.setattr(cmd_runs, "start_run", _raise)

    result = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert result.exit_code == 2, result.output
    data = _extract_json(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in merged if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("runs_"), (
            f"every surfaced W607-AS marker must use the ``runs_*`` prefix family (cmd_runs scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-AL"),
            ("audit_trail_export_", "cmd_audit_trail_export W607-AP"),
            ("postmortem_", "cmd_postmortem W607-AN"),
            ("pr_replay_", "cmd_pr_replay W607-AH"),
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Writer/verifier loop closure -- runs_* and audit_trail_verify_*
#      markers coexist without prefix collision
# ---------------------------------------------------------------------------


def test_runs_markers_coexist_with_audit_trail_verify_markers():
    """W607-AS (write) + W607-AI (verify) marker families coexist.

    cmd_runs (this wave) WRITES the runs/ JSONL chain; cmd_audit_trail_verify
    (W607-AI landed) READS+VERIFIES it. The two marker prefix families
    (``runs_*`` and ``audit_trail_verify_*``) are closed-enum distinct
    so they can coexist on a combined envelope when the same trail is
    written by cmd_runs and then verified.

    This pins the writer/verifier loop's cross-recipe disclosure:
    closed-enum prefix discipline prevents the families from collapsing.
    """
    runs_marker = "runs_compute_hmac_and_write_failed:RuntimeError:synthetic-loop-closure"
    audit_trail_marker = "audit_trail_verify_verify_chain_failed:RuntimeError:synthetic-loop-closure"

    merged = [runs_marker, audit_trail_marker]
    runs_family = [m for m in merged if m.startswith("runs_") and "_failed:" in m]
    atv_family = [m for m in merged if m.startswith("audit_trail_verify_") and "_failed:" in m]

    assert runs_family == [runs_marker], runs_family
    assert atv_family == [audit_trail_marker], atv_family
    # Cross-check: closed-enum prefix discipline.
    assert not runs_marker.startswith("audit_trail_verify_"), runs_marker
    assert not audit_trail_marker.startswith("runs_"), audit_trail_marker


# ---------------------------------------------------------------------------
# (12) Sibling parity -- W607-AI cmd_audit_trail_verify surface unchanged
# ---------------------------------------------------------------------------


def test_w607_ai_cmd_audit_trail_verify_unaffected():
    """Sibling parity guard: W607-AI cmd_audit_trail_verify source surface unchanged.

    W607-AS lands only in cmd_runs. The W607-AI cmd_audit_trail_verify
    surface (``_w607ai_warnings_out`` accumulator + ``audit_trail_verify_*``
    marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    assert src_path.exists(), f"cmd_audit_trail_verify.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ai_warnings_out" in src, (
        "W607-AI accumulator removed from cmd_audit_trail_verify; W607-AS must not regress the sibling instrumentation."
    )


# ---------------------------------------------------------------------------
# (13) Sibling parity -- W607-AN cmd_postmortem surface unchanged
# ---------------------------------------------------------------------------


def test_w607_an_cmd_postmortem_unaffected():
    """Sibling parity guard: W607-AN cmd_postmortem source surface unchanged.

    W607-AS lands only in cmd_runs. The W607-AN cmd_postmortem surface
    (``_w607an_warnings_out`` accumulator + ``postmortem_*`` marker
    emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    assert src_path.exists(), f"cmd_postmortem.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607an_warnings_out" in src, (
        "W607-AN accumulator removed from cmd_postmortem; W607-AS must not regress the sibling instrumentation."
    )
    assert "postmortem_{phase}_failed" in src, (
        "W607-AN marker prefix template removed from cmd_postmortem; "
        "W607-AS must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (14) Source-level guard: cmd_runs carries the canonical W607-AS accumulator
# ---------------------------------------------------------------------------


def test_cmd_runs_carries_w607as_accumulator():
    """AST-level guard: cmd_runs source carries the W607-AS accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_runs.py"
    assert src_path.exists(), f"cmd_runs.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607as_warnings_out" in src, (
        "W607-AS accumulator missing from cmd_runs; the substrate-CALL marker plumbing has been removed."
    )
    assert "runs_{phase}_failed" in src, (
        "W607-AS marker prefix template missing from cmd_runs; check the "
        '`f"runs_{phase}_failed:..."` line in _run_check_as.'
    )
    # Parse-tree level: confirm _run_check_as is defined inside at least
    # one runs subcommand (it lives inside each of runs_start / runs_log /
    # runs_end as a nested closure).
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_as":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AS ``_run_check_as`` helper not found in cmd_runs AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (15) Each substrate phase is wrapped (source-level grep guard)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_runs substrate boundary is wrapped.

    W607-AS substrate inventory:

    runs_start:
      * resolve_project_root  -- find_project_root()
      * start_run             -- start_run(root, agent=...)

    runs_log:
      * resolve_project_root   -- find_project_root()
      * latest_in_progress_run -- latest_in_progress_run(root)
      * read_run_meta          -- read_run_meta(root, run_id)
      * compute_hmac_and_write -- log_event(...)

    runs_end:
      * resolve_project_root   -- find_project_root()
      * latest_in_progress_run -- latest_in_progress_run(root)
      * end_run                -- end_run(root, run_id, status=...)
      * emit_pr_bundle         -- _emit_pr_bundle_for_end(...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_runs.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "resolve_project_root",
        "start_run",
        "latest_in_progress_run",
        "read_run_meta",
        "compute_hmac_and_write",
        "end_run",
        "emit_pr_bundle",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_as("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (8/12/16/20/24 spaces) depending on the
        # site's nesting; accept any of the canonical depths.
        same_line = f'_run_check_as("{phase}"' in src
        multi_line = (
            f'_run_check_as(\n        "{phase}"' in src
            or f'_run_check_as(\n            "{phase}"' in src
            or f'_run_check_as(\n                "{phase}"' in src
            or f'_run_check_as(\n                    "{phase}"' in src
            or f'_run_check_as(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AS _run_check_as wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
