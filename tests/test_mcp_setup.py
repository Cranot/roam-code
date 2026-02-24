"""Tests for MCP setup command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope


class TestMcpSetup:
    """Test roam mcp-setup command."""

    def test_list_platforms(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup"])
        assert result.exit_code == 0
        assert "claude-code" in result.output
        assert "cursor" in result.output
        assert "vscode" in result.output

    def test_list_platforms_json(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "mcp-setup"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "platforms" in data
        assert "claude-code" in data["platforms"]

    def test_claude_code_config(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup", "claude-code"])
        assert result.exit_code == 0
        assert "claude mcp add" in result.output
        assert "roam" in result.output

    def test_cursor_config(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup", "cursor"])
        assert result.exit_code == 0
        assert "Cursor" in result.output
        assert "roam" in result.output

    def test_vscode_config(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup", "vscode"])
        assert result.exit_code == 0
        assert "roam" in result.output

    def test_vscode_json(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "mcp-setup", "vscode"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "mcp-setup"
        assert data["platform"] == "vscode"
        assert "config" in data

    def test_gemini_config(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup", "gemini-cli"])
        assert result.exit_code == 0

    def test_codex_config(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup", "codex-cli"])
        assert result.exit_code == 0

    def test_windsurf_config(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["mcp-setup", "windsurf"])
        assert result.exit_code == 0

    def test_all_platforms_have_json_config(self):
        from roam.commands.cmd_mcp_setup import _CONFIGS
        for name, cfg in _CONFIGS.items():
            assert "json_config" in cfg, f"{name} missing json_config"
            assert "roam-code" in str(cfg["json_config"]) or "roam" in str(cfg["json_config"])

    def test_config_count(self):
        from roam.commands.cmd_mcp_setup import _CONFIGS
        assert len(_CONFIGS) == 6

    def test_json_envelope_format(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "mcp-setup"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, "mcp-setup")

    def test_platform_json_envelope_format(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "mcp-setup", "claude-code"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, "mcp-setup")
        assert "platform" in data
        assert data["platform"] == "claude-code"

    def test_claude_code_has_setup_command(self, cli_runner):
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "mcp-setup", "claude-code"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("setup_command") is not None
        assert "claude mcp add" in data["setup_command"]

    def test_vscode_config_has_servers_key(self, cli_runner):
        """VS Code config should use 'servers' not 'mcpServers'."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "mcp-setup", "vscode"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        config = data.get("config", {})
        assert "servers" in config, f"VS Code config should use 'servers' key: {config}"
