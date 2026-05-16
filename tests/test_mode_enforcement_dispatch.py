"""Dispatch-time mode-enforcement tests (W13.2 follow-through).

The R16 substrate (``roam.modes.policy``) only answered "is X allowed
in mode Y?" until W13.2. These tests cover the new LazyGroup-level
gate that turns the substrate into a hard block at dispatch time when
``ROAM_MODE_ENFORCEMENT=1`` is set.

Six cases:

  1. command in mode -> runs normally
  2. command not in mode -> exits 5 + stderr message
  3. ``--override-mode`` lets a blocked command through with a warning
  4. meta-commands (mode/intent-check/help/version/surface/doctor) are
     always allowed regardless of mode
  5. no .roam/active_mode AND no env var -> all commands allowed
  6. override-mode invocations are logged as ``mode-override`` events
     into the active run

Enforcement is opt-in via the env var; the default (no env var) leaves
dispatch untouched. Tests for both halves of that switch live below.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_repo(tmp_path, monkeypatch):
    """Bare repo with a ``.git`` marker. No constitution, no active_mode."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, args, repo, env=None):
    from roam.cli import cli

    full_env = os.environ.copy()
    # Wipe every env var that affects mode resolution so tests stay
    # deterministic regardless of the surrounding shell.
    for k in ("ROAM_AGENT_MODE", "ROAM_MODE_ENFORCEMENT", "ROAM_RUN_ID"):
        full_env.pop(k, None)
    if env:
        full_env.update(env)
    old = os.getcwd()
    try:
        os.chdir(str(repo))
        result = runner.invoke(cli, args, catch_exceptions=False, env=full_env)
    finally:
        os.chdir(old)
    return result


# ---------------------------------------------------------------------------
# 1. Allowed command runs normally
# ---------------------------------------------------------------------------


def test_command_in_mode_allowed(runner, fresh_repo):
    """`mode --check preflight` runs cleanly under default safe_edit.

    We pick `mode --check` as the test command because it does not
    touch the index — keeps the test independent of `roam init`.
    """
    result = _invoke(
        runner,
        ["mode", "--check", "preflight", "--json"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1"},
    )
    assert result.exit_code == 0, result.output
    # mode --check emits a JSON envelope on stdout.
    data = json.loads(result.stdout)
    assert data["summary"]["active_mode"] == "safe_edit"
    assert data["summary"]["allowed"] is True


# ---------------------------------------------------------------------------
# 2. Blocked command exits 5
# ---------------------------------------------------------------------------


def test_command_not_in_mode_blocked(runner, fresh_repo):
    """`attest` is not in `read_only` -> enforcement -> exit 5 + stderr msg.

    `attest` lives in autonomous_pr's allow-list; under read_only the
    gate must reject it before Click ever dispatches. We use `--help`
    on attest to avoid touching the index — the gate fires before any
    subcommand callback runs (so even `--help` is blocked).
    """
    # Put the repo in read_only via env var (sticky session file works
    # too, but env keeps the test self-contained).
    result = _invoke(
        runner,
        ["attest", "--help"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "read_only"},
    )
    assert result.exit_code == 5, (result.exit_code, result.output, result.stderr)
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "BLOCKED" in combined or "not allowed" in combined, combined


# ---------------------------------------------------------------------------
# 3. --override-mode allows a blocked command
# ---------------------------------------------------------------------------


def test_override_mode_flag_allows_blocked_command(runner, fresh_repo):
    """`--override-mode attest --help` runs and emits a stderr warning."""
    result = _invoke(
        runner,
        ["--override-mode", "attest", "--help"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "read_only"},
    )
    # The actual `attest --help` exits 0 (Click help). The gate emits
    # a warning to stderr but does not block.
    assert result.exit_code == 0, (result.exit_code, result.output)
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "WARNING" in combined and "overridden" in combined, combined
    assert "read_only" in combined
    assert "attest" in combined


# ---------------------------------------------------------------------------
# 4. Meta commands are always allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        # Each entry: (argv, expect_combined_substr) — `expect` is a
        # snippet we know lands in stdout/stderr for that command in
        # its no-op form. We avoid `mode --check attest` because its
        # OWN contract (predates our gate) is to exit 5 on a blocked
        # query, which would conflate with the gate's exit 5.
        (["mode"], "active mode"),
        (["intent-check", "preflight"], "preflight"),
        (["surface", "--json"], "command_count"),
        (["doctor", "--help"], "Usage:"),
        (["exit-codes"], "0"),
        (["version"], "."),
    ],
)
def test_meta_commands_always_allowed(runner, fresh_repo, cmd):
    """Meta-commands run regardless of mode — even with enforcement on.

    The critical signal here is "the gate's BLOCKED stderr message
    must not appear". We allow non-zero exits because some commands
    surface gate-style verdicts in their own right (e.g. `intent-check`
    exits 5 when its query is itself a blocked verb — that's NOT the
    gate firing).
    """
    argv, expect = cmd
    result = _invoke(
        runner,
        argv,
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "read_only"},
    )
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    # Our gate's signature message must NOT appear: that's how we
    # distinguish "the gate blocked dispatch" from "the command
    # itself produced a BLOCKED verdict in its own output".
    assert "Pass `--override-mode` to bypass" not in combined, (argv, combined)
    # And the command's own output must be present (proves it ran).
    assert expect in combined, (argv, combined)


# ---------------------------------------------------------------------------
# 5. No active_mode file + no env var -> nothing blocks (default permissive)
# ---------------------------------------------------------------------------


def test_no_active_mode_file_does_not_block(runner, fresh_repo):
    """No env var, no .roam/active_mode -> enforcement leaves us on default.

    The default mode is `safe_edit`. `attest` is NOT in safe_edit, so
    enforcement would still block it. The relevant check here is the
    *opposite* case: with ROAM_MODE_ENFORCEMENT unset, NOTHING is
    blocked even when the active mode is read_only. That's the
    "opt-in" half of the rollout contract.
    """
    assert not (fresh_repo / ".roam" / "active_mode").exists()
    # Enforcement off -> attest is allowed through the gate even from
    # read_only env (the env var has no effect without enforcement).
    result = _invoke(
        runner,
        ["attest", "--help"],
        fresh_repo,
        env={"ROAM_AGENT_MODE": "read_only"},  # no ROAM_MODE_ENFORCEMENT
    )
    # attest --help is a benign Click help dump -> exit 0. The point
    # is that we did NOT exit 5.
    assert result.exit_code != 5, (result.exit_code, result.output)


# ---------------------------------------------------------------------------
# 6. Override events land in the active run's ledger
# ---------------------------------------------------------------------------


def test_override_logged_to_active_run(runner, fresh_repo, monkeypatch):
    """`--override-mode` writes a `mode-override` event into the active run.

    Start a run, set the run id via ROAM_RUN_ID, invoke a blocked
    command with `--override-mode`, then assert the run's
    `events.jsonl` contains an event with action=mode-override.
    """
    from roam.runs.ledger import read_run_events, start_run

    meta = start_run(fresh_repo, agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    result = _invoke(
        runner,
        ["--override-mode", "attest", "--help"],
        fresh_repo,
        env={
            "ROAM_MODE_ENFORCEMENT": "1",
            "ROAM_AGENT_MODE": "read_only",
            "ROAM_RUN_ID": meta.run_id,
        },
    )
    assert result.exit_code == 0, (result.exit_code, result.output)

    events = list(read_run_events(fresh_repo, meta.run_id))
    overrides = [e for e in events if e.get("action") == "mode-override"]
    assert overrides, (
        f"expected at least one mode-override event in run {meta.run_id}; saw {[e.get('action') for e in events]}"
    )
    assert overrides[0].get("target") == "attest"
