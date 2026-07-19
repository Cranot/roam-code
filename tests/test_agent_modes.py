"""Tests for R16 agent-mode policy substrate.

Covers:
  * Mode resolution priority (explicit > env > file > default)
  * Active-mode persistence (``.roam/active_mode``)
  * Allow-list lookups under each mode
  * Cumulative inheritance (read_only ⊆ safe_edit ⊆ migration ⊆ autonomous_pr)
  * CLI surface (``roam mode``, ``roam mode <name>``, ``roam mode --check``,
    ``roam intent-check``)

The tests use a throwaway tmp_path repo and clear the env var per-test so
nothing leaks between cases. None of the tests need a real index — modes
are pure policy, no DB required.
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
    """A bare repo dir with a .git marker and no constitution."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    # Ensure no stray env var leaks in from the surrounding process.
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def runner():
    return CliRunner()


def _invoke_mode(runner, args, repo, env=None):
    """Run roam <args> in repo, returning the click result."""
    from roam.cli import cli

    full_env = os.environ.copy()
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
# 1. resolve_mode — priority order
# ---------------------------------------------------------------------------


def test_resolve_mode_default_is_safe_edit(fresh_repo):
    """No env, no file, no constitution → safe_edit."""
    from roam.modes import resolve_mode

    policy = resolve_mode(fresh_repo)
    assert policy.name == "safe_edit"
    assert "diff" in policy.allowed_commands
    assert "preflight" in policy.allowed_commands


def test_resolve_mode_env_var_wins(fresh_repo, monkeypatch):
    """ROAM_AGENT_MODE=read_only beats default."""
    from roam.modes import resolve_mode

    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    policy = resolve_mode(fresh_repo)
    assert policy.name == "read_only"
    # diff is NOT in read_only
    assert "diff" not in policy.allowed_commands
    assert "preflight" in policy.allowed_commands


def test_resolve_mode_file_wins_over_default(fresh_repo):
    """.roam/active_mode beats default."""
    from roam.modes import resolve_mode, set_active_mode

    set_active_mode(fresh_repo, "read_only")
    policy = resolve_mode(fresh_repo)
    assert policy.name == "read_only"


def test_resolve_mode_explicit_wins_over_env_and_file(fresh_repo, monkeypatch):
    """Explicit arg > env > file."""
    from roam.modes import resolve_mode, set_active_mode

    set_active_mode(fresh_repo, "read_only")
    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    policy = resolve_mode(fresh_repo, mode_name="autonomous_pr")
    assert policy.name == "autonomous_pr"


def test_resolve_mode_env_wins_over_file(fresh_repo, monkeypatch):
    """Env > file (file is only consulted if env is unset)."""
    from roam.modes import resolve_mode, set_active_mode

    set_active_mode(fresh_repo, "read_only")
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")
    policy = resolve_mode(fresh_repo)
    assert policy.name == "autonomous_pr"


def test_resolve_mode_unknown_env_falls_through(fresh_repo, monkeypatch):
    """An invalid env var value should not lock the agent out — fall through."""
    from roam.modes import resolve_mode

    monkeypatch.setenv("ROAM_AGENT_MODE", "godmode")
    policy = resolve_mode(fresh_repo)
    assert policy.name == "safe_edit"  # default


# ---------------------------------------------------------------------------
# 2. set_active_mode / get_active_mode
# ---------------------------------------------------------------------------


def test_set_active_mode_writes_file(fresh_repo):
    from roam.modes import get_active_mode, set_active_mode

    set_active_mode(fresh_repo, "migration")
    assert (fresh_repo / ".roam" / "active_mode").exists()
    assert get_active_mode(fresh_repo) == "migration"


def test_set_active_mode_rejects_invalid(fresh_repo):
    from roam.modes import set_active_mode

    with pytest.raises(ValueError):
        set_active_mode(fresh_repo, "godmode")


def test_get_active_mode_returns_none_when_missing(fresh_repo):
    from roam.modes import get_active_mode

    assert get_active_mode(fresh_repo) is None


def test_get_active_mode_treats_invalid_file_as_missing(fresh_repo):
    """A corrupted ``.roam/active_mode`` file should return None, not error."""
    from roam.modes import get_active_mode

    (fresh_repo / ".roam").mkdir(exist_ok=True)
    (fresh_repo / ".roam" / "active_mode").write_text("godmode\n", encoding="utf-8")
    assert get_active_mode(fresh_repo) is None


# ---------------------------------------------------------------------------
# 3. check_command_allowed
# ---------------------------------------------------------------------------


def test_check_command_allowed_in_read_only(fresh_repo):
    """preflight allowed; attest denied under read_only."""
    from roam.modes import check_command_allowed, resolve_mode

    policy = resolve_mode(fresh_repo, mode_name="read_only")

    ok, reason = check_command_allowed(fresh_repo, "preflight", policy)
    assert ok is True
    assert "read_only" in reason

    ok, reason = check_command_allowed(fresh_repo, "attest", policy)
    assert ok is False
    assert "not allowed" in reason
    # Reason should suggest the upgrade path.
    assert "autonomous_pr" in reason


def test_check_command_allowed_in_autonomous_pr(fresh_repo):
    """attest allowed under autonomous_pr."""
    from roam.modes import check_command_allowed, resolve_mode

    policy = resolve_mode(fresh_repo, mode_name="autonomous_pr")
    ok, _ = check_command_allowed(fresh_repo, "attest", policy)
    assert ok is True


def test_check_command_with_unknown_command_returns_clear_reason(fresh_repo):
    """An unknown command (typo) gets a distinguishable reason."""
    from roam.modes import check_command_allowed, resolve_mode

    policy = resolve_mode(fresh_repo, mode_name="autonomous_pr")
    ok, reason = check_command_allowed(fresh_repo, "definitely-not-a-command", policy)
    assert ok is False
    # The reason should mention this isn't in any mode (typo signal).
    assert "not in any mode" in reason or "not allowed" in reason


def test_check_command_with_invalid_mode_returns_clear_error(fresh_repo):
    """Asking for an unknown mode via resolve_mode falls through to default
    rather than raising — but set_active_mode rejects it cleanly."""
    from roam.modes import resolve_mode, set_active_mode

    # set is strict
    with pytest.raises(ValueError) as exc:
        set_active_mode(fresh_repo, "godmode")
    assert "godmode" in str(exc.value)
    assert "valid:" in str(exc.value)

    # resolve is tolerant — falls through to default
    policy = resolve_mode(fresh_repo, mode_name="godmode")
    assert policy.name == "safe_edit"


def test_check_strips_roam_prefix(fresh_repo):
    """check_command_allowed should accept 'roam preflight' as 'preflight'."""
    from roam.modes import check_command_allowed, resolve_mode

    policy = resolve_mode(fresh_repo, mode_name="read_only")
    ok, _ = check_command_allowed(fresh_repo, "roam preflight foo", policy)
    assert ok is True


# ---------------------------------------------------------------------------
# 4. Cumulative-mode semantics
# ---------------------------------------------------------------------------


def test_cumulative_modes_strict_superset(fresh_repo):
    """read_only ⊆ safe_edit ⊆ migration ⊆ autonomous_pr."""
    from roam.modes import list_modes

    policies = list_modes(fresh_repo)
    assert policies["read_only"].allowed_commands <= policies["safe_edit"].allowed_commands
    assert policies["safe_edit"].allowed_commands <= policies["migration"].allowed_commands
    assert policies["migration"].allowed_commands <= policies["autonomous_pr"].allowed_commands
    # Each higher mode must strictly grow (otherwise the mode is pointless).
    assert policies["read_only"].allowed_commands < policies["safe_edit"].allowed_commands
    assert policies["safe_edit"].allowed_commands < policies["migration"].allowed_commands
    assert policies["migration"].allowed_commands < policies["autonomous_pr"].allowed_commands


def test_cumulative_modes_from_constitution(fresh_repo):
    """If the constitution declares modes, list_modes honours them.

    Constitution lists are treated as REPLACEMENTS (the loader's default
    is already cumulative). A fresh digest-bound generated snapshot remains
    complete; partial customized inheritance is pinned separately in
    ``test_constitution.py``.
    """
    from roam.constitution.loader import init_constitution, load_constitution
    from roam.modes import list_modes

    init_constitution(fresh_repo)
    c = load_constitution(fresh_repo)
    assert c is not None
    # Constitution's default modes include read_only with at least preflight.
    assert "read_only" in c.modes
    assert "preflight" in c.modes["read_only"]

    policies = list_modes(fresh_repo)
    assert "preflight" in policies["read_only"].allowed_commands
    assert policies["read_only"].source == "constitution"


# ---------------------------------------------------------------------------
# 5. CLI surface
# ---------------------------------------------------------------------------


def test_cli_mode_show_active(fresh_repo, runner):
    """`roam mode` prints the active mode (default safe_edit)."""
    result = _invoke_mode(runner, ["mode"], fresh_repo)
    assert result.exit_code == 0
    assert "safe_edit" in result.output


def test_cli_mode_switch_persists(fresh_repo, runner):
    """`roam mode read_only` writes the file and subsequent `roam mode` sees it."""
    r1 = _invoke_mode(runner, ["mode", "read_only"], fresh_repo)
    assert r1.exit_code == 0
    assert "read_only" in r1.output
    assert (fresh_repo / ".roam" / "active_mode").exists()

    r2 = _invoke_mode(runner, ["mode"], fresh_repo)
    assert r2.exit_code == 0
    assert "read_only" in r2.output


def test_cli_mode_check_allowed_command(fresh_repo, runner):
    """`roam mode --check preflight` succeeds under default safe_edit."""
    result = _invoke_mode(runner, ["mode", "--check", "preflight"], fresh_repo)
    assert result.exit_code == 0
    assert "preflight" in result.output


def test_cli_mode_check_denied_command(fresh_repo, runner):
    """`roam mode --check attest` exits 5 under default safe_edit."""
    result = _invoke_mode(runner, ["mode", "--check", "attest"], fresh_repo)
    assert result.exit_code == 5
    assert "BLOCKED" in result.output or "not allowed" in result.output


def test_cli_mode_json_envelope(fresh_repo, runner):
    """`roam --json mode` returns a well-shaped envelope."""
    result = _invoke_mode(runner, ["--json", "mode"], fresh_repo)
    assert result.exit_code == 0
    data = json.loads(result.stdout if hasattr(result, "stdout") and result.stdout else result.output)
    assert data["command"] == "mode"
    assert "summary" in data
    assert data["summary"]["active_mode"] == "safe_edit"
    assert data["summary"]["allowed_count"] > 0
    assert isinstance(data.get("allowed_commands"), list)
    assert "agent_contract" in data
    assert "facts" in data["agent_contract"]


def test_cli_mode_list(fresh_repo, runner):
    """`roam mode --list` enumerates all 4 modes."""
    result = _invoke_mode(runner, ["--json", "mode", "--list"], fresh_repo)
    assert result.exit_code == 0
    raw = result.stdout if hasattr(result, "stdout") and result.stdout else result.output
    data = json.loads(raw)
    modes = data.get("modes", [])
    names = [m["mode"] for m in modes]
    assert names == ["read_only", "safe_edit", "migration", "autonomous_pr"]


def test_cli_mode_invalid_mode_arg(fresh_repo, runner):
    """`roam mode godmode` is a usage error (exit 2)."""
    result = _invoke_mode(runner, ["mode", "godmode"], fresh_repo)
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# 6. intent-check
# ---------------------------------------------------------------------------


def test_intent_check_allowed_returns_zero(fresh_repo, runner):
    """`roam intent-check preflight` exits 0 under safe_edit."""
    result = _invoke_mode(runner, ["intent-check", "preflight"], fresh_repo)
    assert result.exit_code == 0
    assert "ALLOWED" in result.output


def test_intent_check_returns_clear_blocker_message(fresh_repo, runner):
    """`roam intent-check attest` under safe_edit exits 5 with upgrade suggestion."""
    result = _invoke_mode(runner, ["intent-check", "attest"], fresh_repo)
    assert result.exit_code == 5
    assert "BLOCKED" in result.output
    assert "autonomous_pr" in result.output


def test_intent_check_no_arg_is_usage_error(fresh_repo, runner):
    """`roam intent-check` with no arg exits 2."""
    result = _invoke_mode(runner, ["intent-check"], fresh_repo)
    assert result.exit_code == 2


def test_intent_check_json_envelope(fresh_repo, runner):
    """`roam --json intent-check attest` produces a well-shaped envelope."""
    result = _invoke_mode(runner, ["--json", "intent-check", "attest"], fresh_repo)
    assert result.exit_code == 5
    raw = result.stdout if hasattr(result, "stdout") and result.stdout else result.output
    data = json.loads(raw)
    assert data["command"] == "intent-check"
    summary = data["summary"]
    assert summary["allowed"] is False
    assert summary["intended_command"] == "attest"
    assert summary["upgrade_mode"] == "autonomous_pr"


def test_intent_check_respects_env_var(fresh_repo, runner):
    """ROAM_AGENT_MODE=read_only blocks 'critique' which IS allowed under safe_edit."""
    result = _invoke_mode(
        runner,
        ["intent-check", "critique"],
        fresh_repo,
        env={"ROAM_AGENT_MODE": "read_only"},
    )
    assert result.exit_code == 5
    assert "BLOCKED" in result.output


# ---------------------------------------------------------------------------
# 7. Public API stability
# ---------------------------------------------------------------------------


def test_public_api_exposes_expected_surface():
    """The public ``roam.modes`` re-exports remain stable."""
    import roam.modes as mods

    assert mods.VALID_MODES == ("read_only", "safe_edit", "migration", "autonomous_pr")
    assert mods.DEFAULT_MODE == "safe_edit"
    assert callable(mods.resolve_mode)
    assert callable(mods.check_command_allowed)
    assert callable(mods.set_active_mode)
    assert callable(mods.get_active_mode)
    assert callable(mods.list_modes)
