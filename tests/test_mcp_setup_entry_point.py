"""First-use MCP setup selects the running installation."""

import json
import os
import shutil
import sysconfig
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("platform", ["claude-code", "codex-cli"])
@pytest.mark.parametrize("on_path", ["missing", "different", "matching"])
def test_setup_entry_point(tmp_path, monkeypatch, platform, on_path):
    scripts = tmp_path / "my scripts"
    scripts.mkdir()
    launcher = scripts / ("roam.exe" if os.name == "nt" else "roam")
    launcher.touch()
    access = os.access
    monkeypatch.setattr(os, "access", lambda path, mode: path == launcher or access(path, mode))
    matching = str(launcher)
    other_launcher = tmp_path / "other" / launcher.name
    other_launcher.parent.mkdir()
    other_launcher.touch()
    other = str(other_launcher)
    monkeypatch.setattr(
        shutil, "which", lambda name: {"missing": None, "different": other, "matching": matching}[on_path]
    )
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(scripts))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    args = ["mcp-setup", platform, "--write"]
    result = CliRunner().invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    config = data["config"]
    server = next(iter(config.values()))["roam-code"]
    expected = "roam" if on_path == "matching" else matching
    assert server["command"] == expected
    if on_path != "matching":
        assert expected in data["next_step"]
        assert "agents-md" in data["next_step"]
    path = Path(data["write_result"]["path"])
    if platform == "codex-cli":
        from roam.commands.cmd_mcp_setup import _load_toml

        assert _load_toml(path.read_text(encoding="utf-8")) == config
    else:
        assert json.loads(path.read_text(encoding="utf-8")) == config

    text = CliRunner().invoke(cli, [*args[:-1]])
    assert text.exit_code == 0, text.output
    assert ("Using the absolute path" in text.stdout) is (on_path != "matching")


def test_setup_write_prints_next_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / ("roam.exe" if os.name == "nt" else "roam")
    script.touch()
    monkeypatch.setattr(shutil, "which", lambda name: str(script))
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-setup", "claude-code", "--write"])
    assert result.exit_code == 0, result.output
    assert "roam agents-md" in result.stdout
    result = runner.invoke(cli, ["--json", "mcp-setup", "claude-code", "--write"])
    assert result.exit_code == 0, result.output
    assert "roam agents-md" in json.loads(result.stdout)["next_step"]


def test_setup_reports_unavailable_launcher_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))

    result = CliRunner().invoke(cli, ["--json", "mcp-setup", "claude-code", "--write"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["summary"]["resolution"] == "unresolved"
    assert "missing or cannot be executed" in data["write_result"]["error"]
    assert not (tmp_path / ".mcp.json").exists()


@pytest.fixture
def windows_launchers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from roam.commands import cmd_mcp_setup

    tool_bin = tmp_path / "tool bin"
    tool_bin.mkdir()
    scripts = tmp_path / "venvs" / "roam-code" / "Scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "roam.exe"
    launcher.touch()
    exposed = tool_bin / "roam.exe"
    exposed.touch()
    monkeypatch.setattr(cmd_mcp_setup, "_LAUNCHER_NAME", "roam.exe")
    monkeypatch.setattr(
        cmd_mcp_setup, "os", SimpleNamespace(name="nt", access=lambda *args: True, X_OK=os.X_OK, fsdecode=os.fsdecode)
    )
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(scripts))
    monkeypatch.chdir(tmp_path)
    return launcher, exposed


@pytest.mark.parametrize("tool", ["pipx", "uv"])
def test_extensionless_windows_tool_launcher_on_path(windows_launchers, monkeypatch, tool):
    import sys

    launcher, exposed = windows_launchers
    exposed = exposed.parent.rename(exposed.parent.with_name(f"{tool} bin")) / exposed.name
    monkeypatch.setattr(sys, "argv", [str(exposed.with_suffix(""))])
    monkeypatch.setenv("PATH", str(exposed.parent))
    monkeypatch.setattr(shutil, "which", lambda name: str(exposed))

    result = CliRunner().invoke(cli, ["--json", "mcp-setup", "claude-code", "--write"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert (
        json.loads(Path(data["write_result"]["path"]).read_text(encoding="utf-8"))["mcpServers"]["roam-code"]["command"]
        == "roam"
    )
    assert "launcher_warning" not in data


def test_windows_absolute_launcher_discloses_shell_and_shared_config(windows_launchers, monkeypatch):
    launcher, _ = windows_launchers
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    args = ["mcp-setup", "claude-code", "--write"]
    result = CliRunner().invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["config"]["mcpServers"]["roam-code"]["command"] == str(launcher)
    assert data["shell"] == "powershell"
    assert f"'{launcher}'" in data["setup_command"]
    text = CliRunner().invoke(cli, args)
    assert "Quick setup (PowerShell):" in text.stdout


def test_shared_project_config_warns_about_absolute_launcher(windows_launchers, monkeypatch):
    launcher, _ = windows_launchers
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = CliRunner().invoke(cli, ["--json", "mcp-setup", "claude-code", "--write"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["config"]["mcpServers"]["roam-code"]["command"] == str(launcher)
    assert "before committing" in data["launcher_warning"]


def test_windows_path_copy_requires_an_interpreter_bound_launcher(windows_launchers, monkeypatch):
    import sys

    launcher, exposed = windows_launchers
    monkeypatch.setattr(sys, "argv", ["__main__.py"])
    monkeypatch.setenv("PATH", str(exposed.parent))
    monkeypatch.setattr(shutil, "which", lambda name: str(exposed))
    for wrapper, command in (
        (b'MZstub#!"C:\\Python Scripts\\python.exe"\r\nPK\x03\x04script', "roam"),
        (b"MZgeneric shim", str(launcher)),
        (b"MZstub#!python.exe\nPK\x03\x04script", str(launcher)),
    ):
        launcher.write_bytes(wrapper)
        exposed.write_bytes(wrapper)
        result = CliRunner().invoke(cli, ["--json", "mcp-setup", "claude-code"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["config"]["mcpServers"]["roam-code"]["command"] == command
