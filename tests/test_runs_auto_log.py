"""Tests for the R20 auto-logging helper (Phase 2).

The helper wires gate commands (preflight / diff / critique / pr-prep /
pr-analyze / attest / verify) into the active run-ledger so an agent
that opened a run gets a structured timeline for free.

Covered here:

  1. ``auto_log`` is a silent no-op when no run is active
  2. ``auto_log`` with ``ROAM_RUN_ID`` env writes into that run
  3. ``auto_log`` without env falls through to the newest in-progress run
  4. ``auto_log`` never raises on a malformed envelope (defensive design)
  5. End-to-end: ``roam preflight`` emits an event when ROAM_RUN_ID is set
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

from roam.runs.helpers import auto_log, get_active_run_id  # noqa: E402
from roam.runs.ledger import (  # noqa: E402
    read_run_events,
    run_dir,
    start_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_project(tmp_path):
    """Minimal git-initialised project with no runs yet."""
    proj = tmp_path / "runproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


@pytest.fixture(autouse=True)
def _clear_roam_run_id_env(monkeypatch):
    """Ensure ROAM_RUN_ID never leaks between tests."""
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)


# ---------------------------------------------------------------------------
# 1. No active run → silent no-op
# ---------------------------------------------------------------------------


def test_auto_log_no_active_run_returns_none(runs_project):
    """No ROAM_RUN_ID env + no in-progress run on disk → auto_log returns None.

    This is the gate-command default path: ``roam preflight`` runs all
    the time in CI; we must never write an event in that case.
    """
    # Sanity: confirm no run id is resolvable.
    assert get_active_run_id(runs_project) is None

    envelope = {
        "command": "preflight",
        "summary": {"verdict": "Safe to proceed — LOW risk for foo", "partial_success": False},
        "agent_contract": {"facts": ["foo has 0 callers"], "next_commands": ["roam impact foo"]},
    }
    seq = auto_log(envelope, action="preflight", target="foo", repo_root=runs_project)
    assert seq is None


# ---------------------------------------------------------------------------
# 2. ROAM_RUN_ID env → event lands in that run
# ---------------------------------------------------------------------------


def test_auto_log_with_env_run_id_logs_event(runs_project, monkeypatch):
    meta = start_run(runs_project, agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    envelope = {
        "command": "preflight",
        "summary": {
            "verdict": "Safe to proceed — LOW risk for useFoo",
            "partial_success": False,
        },
        "agent_contract": {
            "facts": ["useFoo has 3 callers"],
            "next_commands": ["roam impact useFoo"],
        },
    }
    seq = auto_log(envelope, action="preflight", target="useFoo", repo_root=runs_project)
    assert seq == 1, f"expected seq=1 for first event, got {seq}"

    events_path = run_dir(runs_project, meta.run_id) / "events.jsonl"
    assert events_path.exists()
    lines = [ln for ln in events_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 event line, got {len(lines)}"
    ev = json.loads(lines[0])
    assert ev["action"] == "preflight"
    assert ev["target"] == "useFoo"
    assert ev["envelope_command"] == "preflight"
    assert ev["summary_verdict"] == "Safe to proceed — LOW risk for useFoo"
    assert ev["partial_success"] is False
    # Signals are nested under signals.{facts, next_commands}
    assert ev["signals"]["facts"] == ["useFoo has 3 callers"]
    assert ev["signals"]["next_commands"] == ["roam impact useFoo"]


# ---------------------------------------------------------------------------
# 3. Latest in-progress run is picked up when env is unset
# ---------------------------------------------------------------------------


def test_auto_log_with_latest_in_progress_run(runs_project):
    """No env var, one in-progress run on disk → event lands there.

    Mirrors the dev-loop ergonomic: agent calls ``roam runs start`` then
    keeps invoking gate commands; we should not require them to thread
    ``ROAM_RUN_ID`` through every invocation.
    """
    meta = start_run(runs_project, agent="test-agent")
    # No env var set — sole signal is the in_progress meta on disk.
    assert "ROAM_RUN_ID" not in os.environ

    envelope = {
        "command": "diff",
        "summary": {
            "verdict": "2 files changed, 5 symbols affected, 3 files in blast radius",
            "partial_success": False,
        },
    }
    seq = auto_log(envelope, action="diff", target="staged", repo_root=runs_project)
    assert seq == 1

    events_path = run_dir(runs_project, meta.run_id) / "events.jsonl"
    events = list(read_run_events(runs_project, meta.run_id))
    assert len(events) == 1, f"expected event on disk, got {events}"
    assert events[0]["action"] == "diff"
    assert events[0]["target"] == "staged"
    assert events[0]["summary_verdict"].startswith("2 files changed")
    # File-system sanity (independent of read_run_events)
    assert events_path.exists()


# ---------------------------------------------------------------------------
# 4. Malformed envelope → returns None, never raises
# ---------------------------------------------------------------------------


def test_auto_log_never_raises(runs_project, monkeypatch):
    meta = start_run(runs_project, agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    # Each of these should be handled without raising:
    # - missing summary (None envelope)
    # - non-dict envelope
    # - summary that's not a dict
    # - agent_contract that's a string
    cases = [
        None,
        "this is not a dict",
        42,
        {},  # empty envelope
        {"summary": "string-summary"},  # summary wrong type
        {"summary": {"verdict": None}},  # verdict None
        {"summary": {"verdict": "ok"}, "agent_contract": "string-contract"},
        {"command": "preflight", "summary": {"verdict": "ok", "partial_success": "yes"}},
    ]
    for env in cases:
        # Should not raise. Return value may be None or an int.
        seq = auto_log(env, action="preflight", target="x", repo_root=runs_project)
        assert seq is None or isinstance(seq, int)

    # After all the noise the run is still intact and the events file is
    # readable. We don't assert a specific count because well-formed-
    # enough envelopes are allowed to log.
    events = list(read_run_events(runs_project, meta.run_id))
    assert isinstance(events, list)


def test_auto_log_swallows_invalid_run_id_env(runs_project, monkeypatch):
    """Pointing ROAM_RUN_ID at a non-existent run id should not crash.

    ``log_event`` itself raises FileNotFoundError when the run dir
    doesn't exist; the helper must absorb that.
    """
    monkeypatch.setenv("ROAM_RUN_ID", "run_99990101_deadbeef")
    envelope = {"command": "preflight", "summary": {"verdict": "ok", "partial_success": False}}
    seq = auto_log(envelope, action="preflight", target="x", repo_root=runs_project)
    assert seq is None


# ---------------------------------------------------------------------------
# 5. End-to-end: ``roam impact`` emits an auto-log event (W15.2 followup)
# ---------------------------------------------------------------------------


def test_impact_auto_logs_to_active_run(
    cli_runner,
    indexed_project,
    monkeypatch,
):
    """A real ``roam impact`` invocation with ROAM_RUN_ID set must auto-log.

    W15.2 followup: ``impact`` was missing from the auto_log allowlist —
    breaking ``replay`` timeline reconstruction for the documented loop
    step ``preflight → impact → critique``. This test pins down the new
    wiring so a future refactor can't silently drop it.
    """
    # The indexed_project fixture has already cd'd us into the repo and the
    # ``User`` class exists (with at least one caller via service.create_user).
    meta = start_run(Path(indexed_project), agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project, json_mode=True)
    assert result.exit_code == 0, f"impact failed:\n{result.output}"

    events = list(read_run_events(Path(indexed_project), meta.run_id))
    impact_events = [e for e in events if e.get("action") == "impact"]
    assert impact_events, (
        f"no impact event found — W15.2 auto-log wiring broken; events: {events}"
    )

    ev = impact_events[0]
    assert ev["envelope_command"] == "impact"
    assert ev["target"] == "User", f"target should be 'User', got {ev.get('target')!r}"
    # Verdict must carry signal — not be empty.
    assert ev.get("summary_verdict"), f"empty verdict in impact event: {ev}"


def test_impact_auto_logs_not_found_path(
    cli_runner,
    indexed_project,
    monkeypatch,
):
    """``roam impact <missing>`` should still auto-log a not-found event.

    The not-found path is a real decision boundary (the agent tried to
    probe a symbol that doesn't exist) and must surface in the replay
    timeline so the agent's misstep is visible.
    """
    meta = start_run(Path(indexed_project), agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    result = invoke_cli(
        cli_runner, ["impact", "DefinitelyDoesNotExist_xyzzy"],
        cwd=indexed_project, json_mode=True,
    )
    # Exit 1 is expected on not-found — that's fine.
    assert result.exit_code in (0, 1), result.output

    events = list(read_run_events(Path(indexed_project), meta.run_id))
    impact_events = [e for e in events if e.get("action") == "impact"]
    assert impact_events, (
        f"impact not-found path failed to auto-log; events: {events}"
    )
    ev = impact_events[0]
    assert "not found" in (ev.get("summary_verdict") or "").lower(), ev


# ---------------------------------------------------------------------------
# 6. End-to-end: ``roam preflight`` emits an auto-log event
# ---------------------------------------------------------------------------


def test_preflight_emits_auto_log_event_when_run_active(
    cli_runner,
    indexed_project,
    monkeypatch,
):
    """A real ``roam preflight`` invocation with ROAM_RUN_ID set should
    append exactly one event to that run, with action=preflight and the
    verdict from the actual run.
    """
    # The indexed_project fixture has already cd'd us into the repo.
    meta = start_run(Path(indexed_project), agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    # ``User`` is a class defined by the python_project fixture — it has
    # callers via ``service.create_user``, so preflight will produce a
    # real, non-empty verdict.
    result = invoke_cli(cli_runner, ["preflight", "User"], cwd=indexed_project, json_mode=True)
    assert result.exit_code == 0, f"preflight failed:\n{result.output}"

    events = list(read_run_events(Path(indexed_project), meta.run_id))
    preflight_events = [e for e in events if e.get("action") == "preflight"]
    assert preflight_events, f"no preflight event found; events: {events}"

    ev = preflight_events[0]
    assert ev["envelope_command"] == "preflight"
    # Verdict should mention either "risk" (success path) or "not found"
    # (resolver miss path); both shapes are valid envelope outputs.
    verdict = ev.get("summary_verdict", "")
    assert verdict, f"empty verdict in event: {ev}"
    # The auto-log target should match the resolved symbol name (no
    # "(file:line)" suffix from the resolver label).
    assert "(" not in ev.get("target", ""), (
        f"target should not contain resolver suffix; got {ev.get('target')!r}"
    )
