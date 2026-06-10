"""W-CC-SETUP — `roam hooks claude` Claude Code installer (2026-06-10).

Makes the compile-prefix channel available to plain Claude Code CLI users:
a UserPromptSubmit hook running `roam --json compile` per prompt. The
Fable 5 A/B numbers (turns -83%) were measured on plain `claude -p` with
this exact prefix-injection shape, so vanilla-CLI parity is evidence-backed.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.commands.cmd_hooks import (
    _CLAUDE_UPS_HOOK_FILENAME,
    _merge_ups_entry,
    _remove_ups_entry,
    _ups_entry_present,
    hooks,
)


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args):
    return CliRunner().invoke(hooks, list(args), obj={"json": False})


class TestClaudeSetupLifecycle:
    def test_dry_run_writes_nothing(self, in_tmp):
        res = _invoke("claude")
        assert res.exit_code == 0
        assert "Would write" in res.output
        assert not (in_tmp / ".claude").exists()

    def test_write_creates_script_and_settings(self, in_tmp):
        res = _invoke("claude", "--write")
        assert res.exit_code == 0
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        assert hook.exists()
        assert os.access(hook, os.X_OK)
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert _ups_entry_present(settings)

    def test_write_is_idempotent(self, in_tmp):
        _invoke("claude", "--write")
        res = _invoke("claude", "--write")
        assert "already wired" in res.output
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert len(settings["hooks"]["UserPromptSubmit"]) == 1

    def test_write_preserves_existing_settings(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        (cdir / "settings.json").write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(ls:*)"]},
                    "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                }
            )
        )
        _invoke("claude", "--write")
        settings = json.loads((cdir / "settings.json").read_text())
        assert settings["permissions"]["allow"] == ["Bash(ls:*)"]
        assert settings["hooks"]["PreToolUse"]  # untouched
        assert _ups_entry_present(settings)
        # backup was taken
        assert (cdir / "settings.json.bak").exists()

    def test_uninstall_removes_entry_and_script(self, in_tmp):
        _invoke("claude", "--write")
        res = _invoke("claude", "--uninstall", "--write")
        assert "Removed" in res.output
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert not _ups_entry_present(settings)
        assert not (in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME).exists()


class TestSettingsMergeHelpers:
    def test_merge_then_remove_roundtrip(self):
        s = {}
        _merge_ups_entry(s, f"python3 /x/{_CLAUDE_UPS_HOOK_FILENAME}")
        assert _ups_entry_present(s)
        assert _remove_ups_entry(s)
        assert not _ups_entry_present(s)
        assert "UserPromptSubmit" not in s.get("hooks", {})

    def test_remove_keeps_foreign_entries(self):
        s = {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "other-tool"}]},
                    {"hooks": [{"type": "command", "command": f"python3 /x/{_CLAUDE_UPS_HOOK_FILENAME}"}]},
                ]
            }
        }
        assert _remove_ups_entry(s)
        remaining = s["hooks"]["UserPromptSubmit"]
        assert len(remaining) == 1
        assert remaining[0]["hooks"][0]["command"] == "other-tool"


class TestHookScriptFailOpen:
    def test_hook_script_fails_open_on_garbage_stdin(self, in_tmp):
        import subprocess
        import sys

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        proc = subprocess.run([sys.executable, str(hook)], input="NOT JSON", capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        assert proc.stdout == ""

    def test_hook_script_skips_tiny_prompts(self, in_tmp):
        import subprocess
        import sys

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        proc = subprocess.run(
            [sys.executable, str(hook)], input='{"prompt": "hi"}', capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0
        assert proc.stdout == ""


class TestVerifyStopHook:
    """W-CC-VERIFY — the post-edit verify half of the MVP loop."""

    def test_write_installs_both_hooks(self, in_tmp):
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write")
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert set(settings["hooks"].keys()) == {"UserPromptSubmit", "Stop"}
        assert (in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME).exists()

    def test_no_verify_installs_compile_only(self, in_tmp):
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write", "--no-verify")
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert "Stop" not in settings["hooks"]
        assert not (in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME).exists()

    def test_uninstall_sweeps_both(self, in_tmp):
        _invoke("claude", "--write")
        res = _invoke("claude", "--uninstall", "--write")
        assert "Removed" in res.output
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert settings.get("hooks", {}) == {}

    def test_stop_hook_fails_open_and_respects_loop_guard(self, in_tmp):
        import subprocess
        import sys

        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME
        # loop guard: stop_hook_active → instant silent exit
        proc = subprocess.run(
            [sys.executable, str(hook)], input='{"stop_hook_active": true}', capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0 and proc.stdout == ""
        # garbage stdin → fail open
        proc = subprocess.run([sys.executable, str(hook)], input="NOT JSON", capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0 and proc.stdout == ""
