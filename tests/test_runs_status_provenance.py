"""Tests for W-SEC: run status provenance (derived vs asserted).

Closes a hole in the security model: ``roam runs end --status <x>`` used
to accept ``status`` as a bare, type-checked-only claim (any value in
``VALID_STATUSES`` was honoured verbatim), fed straight into
``build_run_ledger_root_predicate`` and cosign-signed. An agent whose own
recorded events showed a failing check could still close the run
``completed`` and obtain an externally-verifiable, transparency-logged
attestation asserting success. The HMAC chain + cosign signature only
prove the record wasn't altered AFTER signing -- they prove nothing
about whether the claim was ever true.

The fix: ``end_run`` now derives a status from the run's own recorded
check outcomes (every logged event carries ``partial_success``) and
refuses an explicit or default ``--status`` that contradicts a recorded
failure. Where nothing was recorded to derive from, the flag is honoured
as given, but the resulting ``RunMeta.status_source`` (and the sibling
``status_source`` field on the signed RunLedgerRoot/v1 predicate) records
plainly whether the value was ``"derived"`` from evidence or merely
``"asserted"``.

Covers the required matrix:
  (a) failing recorded checks + --status completed -> REFUSED
  (b) failing recorded checks + default flag        -> REFUSED
  (c) no recorded checks                             -> still works, asserted
  (d) passing recorded checks                        -> derived
  (e) --status abandoned is still reachable over failing checks
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)

from roam.attest.vsa import (  # noqa: E402
    build_run_ledger_root_predicate,
    build_run_ledger_root_statement,
)
from roam.runs.ledger import (  # noqa: E402
    STATUS_SOURCE_ASSERTED,
    STATUS_SOURCE_DERIVED,
    end_run,
    log_event,
    read_run_meta,
    start_run,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_project(tmp_path):
    """A minimal git-initialised project with no runs yet."""
    proj = tmp_path / "runproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# (a) failing recorded checks + --status completed -> REFUSED
# ---------------------------------------------------------------------------


def test_explicit_completed_refused_when_a_check_failed(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="preflight", partial_success=False)
    log_event(runs_project, meta.run_id, action="verify", partial_success=True)

    with pytest.raises(ValueError, match="refusing to end run"):
        end_run(runs_project, meta.run_id, status="completed")

    # Refusal must not have silently closed the run as completed anyway.
    fresh = read_run_meta(runs_project, meta.run_id)
    assert fresh.status == "in_progress"
    assert fresh.ended_at is None


def test_cli_refuses_completed_when_a_check_failed(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)
    r = invoke_cli(cli_runner, ["runs", "start", "--agent", "claude-code"], cwd=runs_project, json_mode=True)
    run_id = parse_json_output(r, "runs-start")["summary"]["run_id"]

    invoke_cli(
        cli_runner,
        ["runs", "log", "--run-id", run_id, "--action", "verify", "--partial-success"],
        cwd=runs_project,
        json_mode=True,
    )

    result = invoke_cli(
        cli_runner,
        ["runs", "end", "--run-id", run_id, "--status", "completed"],
        cwd=runs_project,
        json_mode=True,
    )
    assert result.exit_code == 2, result.output
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert_json_envelope(data, "runs-end")
    assert data["summary"]["ended"] is False
    assert data["summary"]["partial_success"] is True
    assert "refus" in data["summary"]["verdict"].lower()

    # Run is still in_progress -- refusal must not have silently closed it.
    fresh = read_run_meta(runs_project, run_id)
    assert fresh.status == "in_progress"


# ---------------------------------------------------------------------------
# (b) failing recorded checks + default flag -> REFUSED
# ---------------------------------------------------------------------------


def test_default_flag_also_refused_when_a_check_failed(runs_project):
    """Design choice, asserted explicitly: the default (``--status``
    omitted -> click default ``"completed"``) gets the SAME contradiction
    check as an explicit ``--status completed``. Silently flipping the
    status to ``"failed"`` behind the caller's back would be a different
    kind of surprise; refusing forces an explicit, auditable choice
    (``--status failed`` or ``--status abandoned``) instead.
    """
    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="critique", partial_success=True)

    with pytest.raises(ValueError, match="refusing to end run"):
        end_run(runs_project, meta.run_id)  # status defaults to "completed"

    fresh = read_run_meta(runs_project, meta.run_id)
    assert fresh.status == "in_progress"


def test_status_failed_matching_recorded_failure_is_derived(runs_project):
    """The honest way to close the run above: assert --status failed,
    which MATCHES the recorded evidence and is therefore accepted and
    labelled 'derived' rather than merely 'asserted'."""
    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="attest", partial_success=True)

    ended = end_run(runs_project, meta.run_id, status="failed")
    assert ended.status == "failed"
    assert ended.status_source == STATUS_SOURCE_DERIVED


# ---------------------------------------------------------------------------
# (c) no recorded checks -> still works, predicate labels the claim asserted
# ---------------------------------------------------------------------------


def test_no_events_recorded_status_is_asserted(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    # No log_event calls at all -- nothing recorded to derive a status from.
    ended = end_run(runs_project, meta.run_id, status="completed")
    assert ended.status == "completed"
    assert ended.status_source == STATUS_SOURCE_ASSERTED

    fresh = read_run_meta(runs_project, meta.run_id)
    assert fresh.status_source == STATUS_SOURCE_ASSERTED

    # NOTE: a run with zero logged events also has no HMAC chain to root a
    # RunLedgerRoot/v1 statement in (final_signature stays None -- see
    # test_build_statement_returns_none_when_chain_unsigned in
    # test_attest_vsa.py, an existing/orthogonal behaviour), so
    # build_run_ledger_root_statement legitimately returns None here. The
    # predicate-carries-status_source contract for a real signed chain is
    # covered below via a stub meta (mirrors test_attest_vsa.py's own
    # convention for isolating the predicate shape from chain-signing).
    assert build_run_ledger_root_statement(runs_project, meta.run_id) is None


def test_predicate_reflects_meta_status_source_via_signed_chain(runs_project, monkeypatch):
    """Isolates the predicate-carries-provenance contract from the
    "zero events -> no signed chain" quirk above by stubbing meta the
    same way test_attest_vsa.py's TestRunLedgerRootStatement does."""

    class _StubMeta:
        run_id = "r_asserted"
        agent = "claude-code"
        started_at = "2026-05-14T00:00:00+00:00"
        ended_at = "2026-05-14T00:05:00+00:00"
        status = "completed"
        status_source = STATUS_SOURCE_ASSERTED
        final_signature = "ab" * 32
        event_count = 0

    monkeypatch.setattr("roam.runs.ledger.read_run_meta", lambda root, run_id: _StubMeta())
    stmt = build_run_ledger_root_statement(runs_project, "r_asserted")
    assert stmt is not None
    assert stmt["predicate"]["status"] == "completed"
    assert stmt["predicate"]["status_source"] == STATUS_SOURCE_ASSERTED


# ---------------------------------------------------------------------------
# (d) passing recorded checks -> derived, labelled derived
# ---------------------------------------------------------------------------


def test_all_clean_events_status_is_derived(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="preflight", partial_success=False)
    log_event(runs_project, meta.run_id, action="diff", partial_success=False)
    log_event(runs_project, meta.run_id, action="verify", partial_success=False)

    ended = end_run(runs_project, meta.run_id, status="completed")
    assert ended.status == "completed"
    assert ended.status_source == STATUS_SOURCE_DERIVED

    stmt = build_run_ledger_root_statement(runs_project, meta.run_id)
    assert stmt is not None
    assert stmt["predicate"]["status_source"] == STATUS_SOURCE_DERIVED


# ---------------------------------------------------------------------------
# (e) --status abandoned is still reachable, even over failing checks
# ---------------------------------------------------------------------------


def test_abandoned_always_reachable_even_over_failing_checks(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="verify", partial_success=True)

    ended = end_run(runs_project, meta.run_id, status="abandoned")
    assert ended.status == "abandoned"
    # "abandoned" is never a claim the recorded checks could corroborate
    # (it isn't a pass/fail claim at all), so it is always labelled
    # asserted -- even though a real recorded failure exists alongside it.
    assert ended.status_source == STATUS_SOURCE_ASSERTED


def test_abandoned_reachable_with_no_events_too(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    ended = end_run(runs_project, meta.run_id, status="abandoned")
    assert ended.status == "abandoned"
    assert ended.status_source == STATUS_SOURCE_ASSERTED


def test_cli_abandoned_succeeds_over_recorded_failure(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)
    r = invoke_cli(cli_runner, ["runs", "start", "--agent", "claude-code"], cwd=runs_project, json_mode=True)
    run_id = parse_json_output(r, "runs-start")["summary"]["run_id"]

    invoke_cli(
        cli_runner,
        ["runs", "log", "--run-id", run_id, "--action", "verify", "--partial-success"],
        cwd=runs_project,
        json_mode=True,
    )

    result = invoke_cli(
        cli_runner,
        ["runs", "end", "--run-id", run_id, "--status", "abandoned"],
        cwd=runs_project,
        json_mode=True,
    )
    data = parse_json_output(result, "runs-end")
    assert_json_envelope(data, "runs-end")
    assert data["summary"]["ended"] is True
    assert data["summary"]["state"] == "abandoned"
    assert data["summary"]["status_source"] == STATUS_SOURCE_ASSERTED


# ---------------------------------------------------------------------------
# Predicate-level unit coverage (no run on disk)
# ---------------------------------------------------------------------------


def test_predicate_omits_status_source_when_not_supplied():
    pred = build_run_ledger_root_predicate(run_id="r1", final_signature="aa" * 32, event_count=1)
    assert "status_source" not in pred


def test_predicate_carries_status_source_when_supplied():
    pred = build_run_ledger_root_predicate(
        run_id="r1",
        final_signature="aa" * 32,
        event_count=1,
        status="failed",
        status_source="derived",
    )
    assert pred["status"] == "failed"
    assert pred["status_source"] == "derived"


# ---------------------------------------------------------------------------
# Non-contradiction directions are NOT refused (only false-success claims are)
# ---------------------------------------------------------------------------


def test_self_reported_failed_on_clean_run_is_allowed_but_asserted(runs_project):
    """Marking an otherwise-clean run 'failed' is not a security problem
    (it isn't a false success claim) so it is allowed -- but since it
    doesn't match what the recorded evidence would derive, it is labelled
    'asserted', not 'derived'."""
    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="verify", partial_success=False)

    ended = end_run(runs_project, meta.run_id, status="failed")
    assert ended.status == "failed"
    assert ended.status_source == STATUS_SOURCE_ASSERTED
