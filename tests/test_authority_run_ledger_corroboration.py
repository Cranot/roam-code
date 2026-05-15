"""W294 - writer-side run-ledger corroboration tests.

The W292 collector harvester walks every HMAC-verified run-ledger event
and looks for authority-shaped fields (``mode_to`` / ``lease_id`` /
``approval_id`` / etc.) to build a corroboration set. Matching
``AuthorityRef`` rows get promoted to ``provenance="run_ledger"``.

But W292 left a gap: ``cmd_mode`` / ``cmd_lease`` /
``cmd_pr_bundle add-approval`` weren't emitting those event fields. So
the harvester only ever lit up via ``run-meta.mode`` (run-start time
stamp). W294 wires the writer side:

* ``cmd_mode`` mode switch emits ``mode_to`` (and ``mode_from`` when
  cheap) on the auto-logged event
* ``cmd_lease`` successful claim / release emits ``lease_id``
* ``cmd_pr_bundle add-approval`` emits ``approval_id``

These tests prove each writer site stamps the field correctly AND that
the W292 harvester picks up the value end-to-end (the matching
AuthorityRef earns ``provenance="run_ledger"``).

Plus a whitelist test for the ``auto_log`` ``extra_event_fields``
safety filter - non-whitelisted keys must be silently dropped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from roam.evidence.collector import (  # noqa: E402
    _build_authority_refs,
    _collect_corroborated_authorities_from_runs,
)
from roam.runs.helpers import auto_log  # noqa: E402
from roam.runs.ledger import (  # noqa: E402
    read_run_events,
    start_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_project(tmp_path, monkeypatch):
    """Minimal git-initialised project with no runs yet."""
    proj = tmp_path / "w294_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    # Clear inherited mode env so .roam/active_mode is the only opinion.
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    return proj


# ---------------------------------------------------------------------------
# 1. cmd_mode emits mode_to on switch
# ---------------------------------------------------------------------------


def test_mode_switch_emits_mode_to_in_run_ledger(runs_project, monkeypatch):
    """``roam mode read_only`` with active run emits ``mode_to`` on the event.

    Proves the writer-side wiring lands the corroboration field where
    the W292 harvester reads it. The default resolved mode is
    ``safe_edit`` so switching to ``read_only`` is a real (non-noop)
    change.
    """
    # Open a run; export ROAM_RUN_ID so cmd_mode auto-logs into it.
    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    runner = CliRunner()
    result = invoke_cli(runner, ["mode", "read_only"], cwd=runs_project)
    assert result.exit_code == 0, result.output

    events = list(read_run_events(runs_project, meta.run_id))
    # Find the mode-switch event.
    switch_events = [e for e in events if e.get("action") == "mode-switch"]
    assert switch_events, f"no mode-switch event in {events!r}"
    ev = switch_events[-1]
    assert ev.get("mode_to") == "read_only", (
        f"expected mode_to=read_only on event, got {ev!r}"
    )
    # mode_from is best-effort; when populated it must be a string.
    # In this scenario the pre-switch mode resolved to ``safe_edit`` (the
    # built-in default).
    if "mode_from" in ev:
        assert ev["mode_from"] == "safe_edit"


def test_mode_switch_noop_does_not_emit_mode_to(runs_project, monkeypatch):
    """Switching to the already-active mode is a no-op - skip emission.

    The W294 spec calls this out explicitly: emission only fires on a
    real mode change. A no-op switch would log a corroboration event
    that doesn't actually corroborate anything new (the mode is already
    on run-meta).
    """
    # Persist the mode first, THEN open a run AFTER. start_run captures
    # the current active mode into meta.json.
    from roam.modes import set_active_mode

    set_active_mode(runs_project, "safe_edit")
    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    # Now invoke `roam mode safe_edit` - this is a no-op switch.
    runner = CliRunner()
    result = invoke_cli(runner, ["mode", "safe_edit"], cwd=runs_project)
    assert result.exit_code == 0, result.output

    events = list(read_run_events(runs_project, meta.run_id))
    switch_events = [e for e in events if e.get("action") == "mode-switch"]
    # Event still logged (visibility), but mode_to MUST be absent on the
    # no-op path (the W294 emission is gated on a real change).
    assert switch_events, "expected a mode-switch event even for no-op"
    ev = switch_events[-1]
    assert "mode_to" not in ev, (
        f"no-op mode-switch unexpectedly stamped mode_to: {ev!r}"
    )


# ---------------------------------------------------------------------------
# 2. cmd_lease emits lease_id on successful claim
# ---------------------------------------------------------------------------


def test_lease_claim_emits_lease_id_in_run_ledger(runs_project, monkeypatch):
    """``roam lease claim`` with active run stamps ``lease_id`` on the event."""
    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["lease", "claim", "--agent", "w294-agent", "--files", "app.py"],
        cwd=runs_project,
    )
    assert result.exit_code == 0, result.output

    events = list(read_run_events(runs_project, meta.run_id))
    claim_events = [e for e in events if e.get("action") == "lease-claim"]
    assert claim_events, f"no lease-claim event in {events!r}"
    ev = claim_events[-1]
    assert isinstance(ev.get("lease_id"), str) and ev["lease_id"], (
        f"expected lease_id on event, got {ev!r}"
    )
    # Sanity: target string is the same lease id (set on auto_log call).
    assert ev.get("target") == ev["lease_id"]


# ---------------------------------------------------------------------------
# 3. cmd_pr_bundle add-approval emits approval_id
# ---------------------------------------------------------------------------


def test_add_approval_emits_approval_id_in_run_ledger(runs_project, monkeypatch):
    """``roam pr-bundle add-approval`` with active run emits ``approval_id``."""
    # pr-bundle add-approval requires an initialised bundle on disk.
    runner = CliRunner()
    init = invoke_cli(
        runner,
        ["pr-bundle", "init", "--intent", "w294 corroboration test"],
        cwd=runs_project,
    )
    assert init.exit_code == 0, init.output

    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    result = invoke_cli(
        runner,
        [
            "pr-bundle", "add-approval",
            "--approver", "human:alice@example.com",
            "--scope", "pr-42",
            "--reason", "looks good",
            "--id", "appr_test_001",
        ],
        cwd=runs_project,
    )
    assert result.exit_code == 0, result.output

    events = list(read_run_events(runs_project, meta.run_id))
    # action is "pr-bundle" per _emit_envelope_and_log's hardcoded action.
    appr_events = [e for e in events if e.get("approval_id")]
    assert appr_events, f"no event carrying approval_id in {events!r}"
    ev = appr_events[-1]
    assert ev["approval_id"] == "appr_test_001", (
        f"expected approval_id=appr_test_001, got {ev!r}"
    )


# ---------------------------------------------------------------------------
# 4. End-to-end: corroboration promotes AuthorityRef provenance
# ---------------------------------------------------------------------------


def test_corroboration_promotes_authority_refs_to_run_ledger_provenance(
    runs_project, monkeypatch
):
    """End-to-end: writer-side mode_to emission -> harvester picks it up
    -> AuthorityRef provenance promoted to ``run_ledger``.

    The pipeline:

    1. open a run + persist a mode_to event via cmd_mode
    2. end the run so the chain is final
    3. invoke ``_collect_corroborated_authorities_from_runs`` and confirm
       the (mode, safe_edit) pair appears in the corroborated set
    4. build authority refs with the envelope's mode + that corroboration
       set; assert the mode AuthorityRef carries
       ``provenance="run_ledger"`` (NOT ``producer_envelope(mode)``).
    """
    from roam.runs.ledger import end_run

    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    # Default resolved mode is ``safe_edit`` so switching to
    # ``read_only`` is a real change and the cmd_mode auto-log path
    # emits ``mode_to=read_only`` on the event.
    runner = CliRunner()
    result = invoke_cli(runner, ["mode", "read_only"], cwd=runs_project)
    assert result.exit_code == 0, result.output

    end_run(runs_project, meta.run_id, status="completed")

    warnings: list[str] = []
    corroborated = _collect_corroborated_authorities_from_runs(
        runs_project, warnings
    )
    # The mode_to value (read_only) MUST appear in the corroborated set.
    assert ("mode", "read_only") in corroborated, (
        f"expected ('mode', 'read_only') in corroborated set, got "
        f"{corroborated!r} (warnings={warnings!r})"
    )

    # Build refs with the SAME mode on the envelope AND the corroboration
    # set. Provenance should be ``run_ledger`` (corroboration wins).
    refs = _build_authority_refs(
        pr_bundle_envelope={"mode": "read_only"},
        caller_mode=None,
        corroborated_authorities=corroborated,
    )
    target = next(r for r in refs if r.authority_kind == "mode")
    assert target.extra.get("provenance") == "run_ledger", (
        f"expected provenance=run_ledger after writer-side corroboration, "
        f"got {target.extra!r}"
    )
    # W294 source axis stays distinct and category-correct.
    assert target.source == "mode"


# ---------------------------------------------------------------------------
# 5. auto_log whitelist - non-whitelisted fields are silently dropped
# ---------------------------------------------------------------------------


def test_auto_log_rejects_unknown_event_fields(runs_project, monkeypatch):
    """``auto_log`` silently drops keys NOT in ``_AUTHORITY_EVENT_FIELDS``.

    Defense-in-depth: the kwarg is not an arbitrary-state escape hatch.
    A future caller passing ``{"arbitrary_key": "value"}`` MUST NOT
    end up writing that key into the ledger event. We confirm:

    * the call succeeds (auto_log never raises)
    * the emitted event has no ``arbitrary_key`` field
    * a sibling whitelisted key on the SAME call DOES land
    """
    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    envelope = {
        "command": "test",
        "summary": {"verdict": "ok", "partial_success": False},
        "agent_contract": {"facts": [], "next_commands": []},
    }

    seq = auto_log(
        envelope,
        action="test-action",
        target="t",
        repo_root=runs_project,
        extra_event_fields={
            "arbitrary_key": "should_be_dropped",
            "evil_path": "/etc/passwd",
            # Whitelisted: must survive.
            "lease_id": "lease_w294_test",
            # Empty/None values are filtered too (defense-in-depth).
            "mode_to": "",
        },
    )
    assert seq is not None, "expected event to be logged"

    events = list(read_run_events(runs_project, meta.run_id))
    assert events, "expected at least one event"
    ev = events[-1]
    # Whitelisted survives.
    assert ev.get("lease_id") == "lease_w294_test"
    # Non-whitelisted dropped.
    assert "arbitrary_key" not in ev
    assert "evil_path" not in ev
    # Empty-string whitelisted key dropped (defense-in-depth string filter).
    assert "mode_to" not in ev


def test_auto_log_extra_event_fields_default_is_backward_compat(
    runs_project, monkeypatch
):
    """Existing callers (no ``extra_event_fields`` kwarg) work unchanged.

    Pins the W294 contract that the kwarg is purely additive: an
    omitted kwarg produces the same event shape as before the wave.
    """
    meta = start_run(runs_project, agent="w294-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    envelope = {
        "command": "test",
        "summary": {"verdict": "ok", "partial_success": False},
        "agent_contract": {"facts": [], "next_commands": []},
    }
    seq = auto_log(envelope, action="test-action", target="t", repo_root=runs_project)
    assert seq is not None

    events = list(read_run_events(runs_project, meta.run_id))
    ev = events[-1]
    # None of the authority-shaped fields should be on the event.
    for k in ("mode_to", "mode_from", "lease_id", "approval_id", "permit_id"):
        assert k not in ev
