"""Dispatch-time mode-enforcement tests (W13.2 follow-through).

The R16 substrate (``roam.modes.policy``) only answered "is X allowed
in mode Y?" until W13.2. These tests cover the new LazyGroup-level
gate that turns the substrate into a hard block when explicitly enabled.

Ten cases:

  1. command in mode -> runs normally
  2. command not in mode -> exits 5 + stderr message
  3. ``--override-mode`` lets a blocked command through with a warning
  4. meta-commands (mode/intent-check/help/version/surface/doctor) are
     always allowed regardless of mode
  5. unset/false ``ROAM_MODE_ENFORCEMENT`` preserves compatibility
  6. override-mode invocations are logged as ``mode-override`` events
     into the active run
  7. policy failures allow declared read-only diagnostics and fail closed
     for write/destructive commands
  8. safe-maintenance Verify runs in safe_edit but not read_only
 9. unclassified declared read-only diagnostics retain default access
 10. sibling-patch replay is intentionally safe_edit-only
 11. option/subcommand-dependent writes escalate while their query forms stay read-only

Enforcement remains opt-in while legacy capability metadata and version-1
constitution migrations are incomplete. Once enabled, the gate fails closed
and the per-call override leaves visible audit evidence.
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


@pytest.mark.parametrize(
    ("argv", "trigger"),
    [
        (["doctor", "--persist", "--help"], "--persist"),
        (["minimap", "--output=AGENTS.md", "--help"], "--output"),
        (["proof-bundle", "-obundle.json", "--help"], "-o"),
        (["compile-daemon", "start", "--help"], "start"),
        (["compile-cache", "evict", "--help"], "evict"),
        (["coverage-gaps", "--import-report=coverage.xml", "--help"], "--import-report"),
        (["describe", "--write", "--help"], "--write"),
        (["tour", "--write", "TOUR.md", "--help"], "--write"),
        (["version", "--check", "--help"], "--check"),
    ],
)
def test_read_only_blocks_only_mutating_invocation_shapes(runner, fresh_repo, argv, trigger):
    """Raw argv evidence raises mixed commands before Click dispatches them."""
    result = _invoke(
        runner,
        argv,
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "read_only"},
    )
    assert result.exit_code == 5, (argv, result.exit_code, result.output, result.stderr)
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert f"uses `{trigger}`" in combined
    assert "requires safe_edit mode" in combined


@pytest.mark.parametrize(
    "argv",
    [
        ["doctor", "--help"],
        ["minimap", "--help"],
        ["proof-bundle", "--help"],
        ["compile-daemon", "status", "--help"],
        ["compile-cache", "stats", "--help"],
        ["coverage-gaps", "--help"],
        ["describe", "--help"],
        ["tour", "--help"],
    ],
)
def test_read_only_keeps_non_mutating_shapes_available(runner, fresh_repo, argv):
    result = _invoke(
        runner,
        argv,
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "read_only"},
    )
    assert result.exit_code == 0, (argv, result.exit_code, result.output, result.stderr)
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "BLOCKED:" not in combined


def test_safe_edit_allows_option_dependent_write(runner, fresh_repo):
    result = _invoke(
        runner,
        ["doctor", "--persist", "--help"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "safe_edit"},
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 5. Unset/false enforcement preserves compatibility without fake overrides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "unknown"])
def test_enforcement_disabled_values_are_permissive(runner, fresh_repo, value):
    """Only documented truthy values enable the CLI dispatch gate."""
    assert not (fresh_repo / ".roam" / "active_mode").exists()
    env = {"ROAM_AGENT_MODE": "read_only"}
    if value is not None:
        env["ROAM_MODE_ENFORCEMENT"] = value
    result = _invoke(
        runner,
        ["attest", "--help"],
        fresh_repo,
        env=env,
    )
    assert result.exit_code == 0, (result.exit_code, result.output)
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "Mode enforcement overridden" not in combined
    assert "BLOCKED" not in combined


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


def test_disabled_enforcement_does_not_forge_override_event(runner, fresh_repo, monkeypatch):
    """A disabled gate is not an override and must not forge audit evidence."""
    from roam.runs.ledger import read_run_events, start_run

    meta = start_run(fresh_repo, agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)
    result = _invoke(
        runner,
        ["attest", "--help"],
        fresh_repo,
        env={
            "ROAM_MODE_ENFORCEMENT": "0",
            "ROAM_AGENT_MODE": "read_only",
            "ROAM_RUN_ID": meta.run_id,
        },
    )
    assert result.exit_code == 0, result.output
    overrides = [e for e in read_run_events(fresh_repo, meta.run_id) if e.get("action") == "mode-override"]
    assert not overrides


def test_policy_failure_allows_declared_read_only_diagnostic(runner, fresh_repo, monkeypatch):
    import roam.cli as cli_module

    monkeypatch.setattr(cli_module, "_mode_gate_dependencies", lambda: None)
    result = _invoke(runner, ["health", "--help"], fresh_repo, env={"ROAM_MODE_ENFORCEMENT": "1"})
    assert result.exit_code == 0, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "allowing declared read-only diagnostic `health`" in combined


def test_policy_failure_blocks_write_command_when_enabled(runner, fresh_repo, monkeypatch):
    import roam.cli as cli_module

    monkeypatch.setattr(cli_module, "_mode_gate_dependencies", lambda: None)
    result = _invoke(runner, ["attest", "--help"], fresh_repo, env={"ROAM_MODE_ENFORCEMENT": "1"})
    assert result.exit_code == 5, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "not declared read-only" in combined
    assert "failed closed" in combined


def test_policy_resolution_failure_blocks_option_write_with_legacy_metadata(runner, fresh_repo, monkeypatch):
    """A legacy false side-effect flag cannot turn policy failure into write authority."""
    import roam.cli as cli_module

    def _broken_check(_repo_root, _command):
        raise RuntimeError("synthetic policy resolution failure")

    monkeypatch.setattr(cli_module, "_mode_gate_dependencies", lambda: (lambda: fresh_repo, _broken_check))
    result = _invoke(runner, ["mutate", "--help"], fresh_repo, env={"ROAM_MODE_ENFORCEMENT": "1"})
    assert result.exit_code == 5, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "mode policy import or resolution failed" in combined
    assert "failed closed" in combined


def test_unclassified_declared_read_only_diagnostic_uses_default_fallback(runner, fresh_repo, monkeypatch):
    """Legacy/plugin diagnostics retain a metadata fallback without policy debt."""
    import roam.cli as cli_module

    synthetic_name = "synthetic-read-only-diagnostic"
    monkeypatch.setitem(cli_module._COMMANDS, synthetic_name, cli_module._COMMANDS["adversarial"])
    result = _invoke(
        runner,
        ["mode", "--check", synthetic_name, "--json"],
        fresh_repo,
        env={"ROAM_AGENT_MODE": "read_only"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["allowed"] is True
    assert "declared read-only diagnostic" in payload["summary"]["reason"]


def test_verify_safe_maintenance_runs_in_safe_edit_but_not_read_only(runner, fresh_repo):
    allowed = _invoke(
        runner,
        ["verify", "--help"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "safe_edit"},
    )
    assert allowed.exit_code == 0, allowed.output

    policy_check = _invoke(
        runner,
        ["mode", "--check", "verify", "--json"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "safe_edit"},
    )
    assert policy_check.exit_code == 0, policy_check.output
    assert json.loads(policy_check.stdout)["summary"]["allowed"] is True

    blocked = _invoke(
        runner,
        ["verify", "--help"],
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1", "ROAM_AGENT_MODE": "read_only"},
    )
    assert blocked.exit_code == 5, blocked.output
    combined = (blocked.output or "") + (getattr(blocked, "stderr", "") or "")
    assert "run `roam mode safe_edit`" in combined


def test_sibling_patch_replay_is_safe_edit_not_read_only(runner, fresh_repo):
    env = {
        "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS": "1",
        "ROAM_MODE_ENFORCEMENT": "1",
        "ROAM_AGENT_MODE": "safe_edit",
    }
    allowed = _invoke(runner, ["sibling-patch", "--help"], fresh_repo, env=env)
    assert allowed.exit_code == 0, allowed.output

    policy_check = _invoke(
        runner,
        ["mode", "--check", "sibling-patch", "--json"],
        fresh_repo,
        env=env,
    )
    assert policy_check.exit_code == 0, policy_check.output
    assert json.loads(policy_check.stdout)["summary"]["allowed"] is True

    blocked = _invoke(
        runner,
        ["sibling-patch", "--help"],
        fresh_repo,
        env={
            "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS": "1",
            "ROAM_MODE_ENFORCEMENT": "1",
            "ROAM_AGENT_MODE": "read_only",
        },
    )
    assert blocked.exit_code == 5, blocked.output
    combined = (blocked.output or "") + (getattr(blocked, "stderr", "") or "")
    assert "run `roam mode safe_edit`" in combined
