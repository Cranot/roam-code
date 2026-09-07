from __future__ import annotations

import json
import subprocess
import sys

from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_hooks as hooks


def test_producer_uses_literal_exec_arguments(tmp_path):
    script = tmp_path / "space $literal 'quote' & hook.py"
    script.write_text("print('EXEC_OK')\n", encoding="utf-8")
    settings = hooks._merge_hook_entry({}, "Stop", hooks._claude_hook_command(script))
    entry = json.loads(json.dumps(settings))["hooks"]["Stop"][0]["hooks"][0]
    assert entry == {"type": "command", "command": sys.executable, "args": [str(script)]}
    result = subprocess.run([entry["command"], *entry["args"]], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "EXEC_OK"
    assert hooks._hook_entry_present(settings, "Stop", script.name)
    assert hooks._remove_hook_entry(settings, "Stop", script.name)


def test_uninstall_preserves_other_hooks_in_same_rule():
    managed = {"type": "command", "command": sys.executable, "args": ["/x/roam-verify-stop.py"]}
    other = {"type": "command", "command": "echo keep"}
    settings = {"hooks": {"Stop": [{"hooks": [managed, other]}]}}
    assert hooks._remove_hook_entry(settings, "Stop", "roam-verify-stop.py")
    assert settings["hooks"]["Stop"] == [{"hooks": [other]}]


def test_serialized_install_migrates_legacy_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["hooks", "claude", "--write"]).exit_code == 0
    path = tmp_path / ".claude/settings.json"
    settings = json.loads(path.read_text())
    for event, filename in (
        ("UserPromptSubmit", hooks._CLAUDE_UPS_HOOK_FILENAME),
        ("Stop", hooks._CLAUDE_STOP_HOOK_FILENAME),
    ):
        settings["hooks"][event][0]["hooks"][0] = {
            "type": "command",
            "command": hooks._legacy_claude_hook_command(path.parent / "hooks" / filename),
        }
        settings["hooks"][event][0]["hooks"].append({"type": "command", "command": "echo keep"})
    original = json.dumps(settings)
    path.write_text(original)
    assert runner.invoke(cli, ["hooks", "claude"]).exit_code == 0
    assert path.read_text() == original
    assert runner.invoke(cli, ["hooks", "claude", "--write"]).exit_code == 0
    updated = json.loads(path.read_text())
    for event in ("UserPromptSubmit", "Stop"):
        entries = updated["hooks"][event][0]["hooks"]
        assert len(entries) == 2
        assert entries[0]["command"] == sys.executable
        assert len(entries[0]["args"]) == 1
        assert entries[1]["command"] == "echo keep"
    before = path.read_bytes()
    assert runner.invoke(cli, ["hooks", "claude", "--write"]).exit_code == 0
    assert path.read_bytes() == before


def test_custom_command_and_body_are_not_migrated_by_substring(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["hooks", "claude", "--write"]).exit_code == 0
    path = tmp_path / ".claude/settings.json"
    settings = json.loads(path.read_text())
    script = path.parent / "hooks" / hooks._CLAUDE_UPS_HOOK_FILENAME
    custom = "# user-owned customization\n"
    script.write_text(custom)
    settings["hooks"]["UserPromptSubmit"][0]["hooks"][0] = {
        "type": "command",
        "command": hooks._legacy_claude_hook_command(script) + " --custom",
    }
    path.write_text(json.dumps(settings))
    before = path.read_bytes()
    result = runner.invoke(cli, ["hooks", "claude", "--write"])
    assert result.exit_code == 0
    assert path.read_bytes() == before
    assert script.read_text() == custom
    assert "attention" in result.output
