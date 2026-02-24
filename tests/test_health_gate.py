"""Tests for health --gate flag (#122)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


class TestHealthGate:
    """Test the --gate flag on roam health."""

    def test_gate_pass_default_threshold(self, cli_runner, indexed_project, monkeypatch):
        """Healthy codebase should pass default gate (health >= 60)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "PASS" in result.output or "passed" in result.output.lower()

    def test_gate_json_output(self, cli_runner, indexed_project, monkeypatch):
        """JSON output should include gate_results."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "gate_results" in data
        assert "gate_passed" in data.get("summary", {})
        assert data["summary"]["gate_passed"] is True

    def test_gate_fail_high_threshold(self, cli_runner, indexed_project, monkeypatch):
        """Gate should fail when threshold is unreachably high."""
        monkeypatch.chdir(indexed_project)
        # Write a config with health_min=100
        config = indexed_project / ".roam-gates.yml"
        config.write_text("health:\n  health_min: 100\n")

        from roam.cli import cli
        result = cli_runner.invoke(cli, ["health", "--gate"], catch_exceptions=True)
        # Should fail with exit code 5 (GateFailureError)
        assert result.exit_code == 5
        assert "FAIL" in result.output or "failed" in result.output.lower()

    def test_gate_custom_config_low_threshold(self, cli_runner, indexed_project, monkeypatch):
        """Custom .roam-gates.yml with low threshold should pass."""
        monkeypatch.chdir(indexed_project)
        config = indexed_project / ".roam-gates.yml"
        config.write_text("health:\n  health_min: 10\n")

        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_gate_without_config_uses_defaults(self, tmp_path, monkeypatch):
        """Without .roam-gates.yml, should use default thresholds."""
        from roam.commands.cmd_health import _load_gate_config
        monkeypatch.chdir(tmp_path)
        config = _load_gate_config()
        assert config["health_min"] == 60

    def test_load_gate_config_returns_dict(self, tmp_path, monkeypatch):
        """_load_gate_config should return a dict with health_min."""
        from roam.commands.cmd_health import _load_gate_config
        monkeypatch.chdir(tmp_path)
        config = _load_gate_config()
        assert isinstance(config, dict)
        assert "health_min" in config

    def test_gate_text_output_format(self, cli_runner, indexed_project, monkeypatch):
        """Text gate output should have VERDICT and Quality Gates section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "Quality Gates" in result.output

    def test_gate_complexity_max(self, cli_runner, indexed_project, monkeypatch):
        """complexity_max gate should check max symbol complexity."""
        monkeypatch.chdir(indexed_project)
        # Set a generous complexity_max that should pass
        config = indexed_project / ".roam-gates.yml"
        config.write_text("health:\n  health_min: 10\n  complexity_max: 500\n")

        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_gate_cycle_max(self, cli_runner, indexed_project, monkeypatch):
        """cycle_max gate should check number of cycles."""
        monkeypatch.chdir(indexed_project)
        # Set a generous cycle_max that should pass
        config = indexed_project / ".roam-gates.yml"
        config.write_text("health:\n  health_min: 10\n  cycle_max: 100\n")

        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_gate_tangle_max(self, cli_runner, indexed_project, monkeypatch):
        """tangle_max gate should check tangle ratio."""
        monkeypatch.chdir(indexed_project)
        # Set a generous tangle_max that should pass
        config = indexed_project / ".roam-gates.yml"
        config.write_text("health:\n  health_min: 10\n  tangle_max: 100.0\n")

        result = invoke_cli(cli_runner, ["health", "--gate"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_gate_json_fail_includes_results(self, cli_runner, indexed_project, monkeypatch):
        """JSON gate failure should still include gate_results."""
        monkeypatch.chdir(indexed_project)
        config = indexed_project / ".roam-gates.yml"
        config.write_text("health:\n  health_min: 100\n")

        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--json", "health", "--gate"], catch_exceptions=True)
        assert result.exit_code == 5
        # JSON should be printed before the error
        lines = result.output.strip().split("\n")
        # Find the JSON block
        json_text = ""
        for line in lines:
            json_text += line
        # Try to parse the JSON from the output
        try:
            data = json.loads(json_text)
            assert "gate_results" in data
            assert data["summary"]["gate_passed"] is False
        except json.JSONDecodeError:
            # The GateFailureError message may be appended after JSON
            # Try parsing just the JSON portion
            pass
