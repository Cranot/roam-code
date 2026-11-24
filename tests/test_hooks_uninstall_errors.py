"""Uninstall summaries must disclose hooks that could not be removed."""

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_hooks import hooks


@pytest.fixture(autouse=True)
def _pin_index_freshness_budget(monkeypatch):
    monkeypatch.setenv("ROAM_INDEX_FRESHNESS_BUDGET_S", "3600")


_FAILURE = re.compile(
    r"error|fail|denied|permission|unable|could\s+not|couldn't|cannot|can't|"
    r"unsuccessful|incomplete|not\b.*\bremoved|remain|warning",
    re.IGNORECASE,
)


def _prepare_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    # Use roam's local .git fallback, avoiding host git configuration entirely.
    monkeypatch.setenv("PATH", "")
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    return hooks_dir


def _verdict(result, json_mode):
    if json_mode:
        return json.loads(result.output)["summary"]["verdict"]
    return next(line.partition(":")[2].strip() for line in result.output.splitlines() if line.startswith("VERDICT:"))


@pytest.mark.parametrize("json_mode", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("partial_success", [False, True], ids=["all-failed", "partial"])
def test_uninstall_verdict_discloses_removal_failure(tmp_path, monkeypatch, json_mode, partial_success):
    hooks_dir = _prepare_repo(tmp_path, monkeypatch)
    runner = CliRunner()
    installed = runner.invoke(hooks, ["install"], catch_exceptions=False)
    assert installed.exit_code == 0
    blocked = hooks_dir / "post-merge"
    if not partial_success:
        for path in hooks_dir.iterdir():
            if path != blocked:
                path.unlink()
    original = blocked.read_bytes()
    real_unlink = Path.unlink

    def deny_one_unlink(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("permission denied removing hook")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_one_unlink)
    result = runner.invoke(hooks, ["uninstall"], obj={"json": json_mode})
    assert blocked.read_bytes() == original
    if partial_success:
        assert not (hooks_dir / "post-checkout").exists()
        assert not (hooks_dir / "post-rewrite").exists()
    if json_mode:
        summary = json.loads(result.output)["summary"]
        assert any(error["hook"] == blocked.name for error in summary["errors"])
    verdict = _verdict(result, json_mode)
    assert _FAILURE.search(verdict), f"Verdict hides failed hook removal: {verdict!r}"


@pytest.mark.parametrize("install_first", [False, True], ids=["absent", "removed"])
def test_successful_uninstall_has_no_failure_verdict(tmp_path, monkeypatch, install_first):
    hooks_dir = _prepare_repo(tmp_path, monkeypatch)
    runner = CliRunner()
    if install_first:
        assert runner.invoke(hooks, ["install"], catch_exceptions=False).exit_code == 0
    result = runner.invoke(hooks, ["uninstall"], obj={"json": True}, catch_exceptions=False)
    assert result.exit_code == 0
    summary = json.loads(result.output)["summary"]
    assert not summary["errors"]
    assert bool(summary["removed"]) == install_first
    assert not list(hooks_dir.iterdir())
    assert not _FAILURE.search(summary["verdict"])


@pytest.mark.parametrize("error", [False, True])
def test_uninstall_partial_success_tracks_errors(tmp_path, monkeypatch, error):
    from roam.commands import cmd_hooks

    hooks_dir = _prepare_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(cmd_hooks, "_find_git_hooks_dir", lambda: hooks_dir)
    monkeypatch.setattr(
        cmd_hooks, "_uninstall_hook", lambda path: ("error", "permission denied") if error else ("removed", "")
    )
    result = CliRunner().invoke(hooks, ["uninstall"], obj={"json": True})
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["partial_success"] is error
