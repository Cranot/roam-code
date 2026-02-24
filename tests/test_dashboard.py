"""Tests for `roam dashboard` -- unified single-screen codebase status.

Covers text output, JSON output, section presence, data ranges,
minimal projects, and graceful handling of missing data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope

from roam.cli import cli


# ============================================================================
# TestDashboard
# ============================================================================

class TestDashboard:
    """Tests for `roam dashboard`."""

    # ---- Text output ----

    def test_dashboard_shows_verdict(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should start with a VERDICT line."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0, f"dashboard failed: {result.output}"
        assert "VERDICT:" in result.output

    def test_dashboard_has_overview_section(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should contain an Overview section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Overview ===" in result.output

    def test_dashboard_has_health_section(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should contain a Health section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Health ===" in result.output

    def test_dashboard_has_risk_areas_section(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should contain a Risk Areas section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Risk Areas ===" in result.output

    def test_dashboard_shows_files_and_symbols(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard overview should mention file and symbol counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "Files:" in result.output
        assert "Symbols:" in result.output

    def test_dashboard_shows_health_score(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard health section should show a numeric score."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "/100" in result.output

    def test_dashboard_shows_details_hint(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard should end with a hint to run detailed commands."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "roam health" in result.output
        assert "roam vibe-check" in result.output

    def test_dashboard_text_is_compact(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard text output should be concise (<50 lines)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) < 50, (
            f"Dashboard output too long ({len(lines)} lines), "
            f"expected < 50"
        )

    # ---- JSON output ----

    def test_dashboard_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dashboard should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        assert_json_envelope(data, "dashboard")

    def test_dashboard_json_summary_has_verdict(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary should include a verdict string."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        summary = data["summary"]
        assert "verdict" in summary
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 10

    def test_dashboard_json_summary_has_health_score(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary should include health_score in valid range."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        summary = data["summary"]
        assert "health_score" in summary
        assert 0 <= summary["health_score"] <= 100

    def test_dashboard_json_has_all_sections(self, cli_runner, indexed_project, monkeypatch):
        """JSON output should include overview, health, hotspots, risks."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        for key in ["overview", "health", "hotspots", "risks"]:
            assert key in data, f"Missing '{key}' in JSON: {list(data.keys())}"

    def test_dashboard_json_overview_has_files(self, cli_runner, indexed_project, monkeypatch):
        """JSON overview section should have file/symbol/edge counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        overview = data["overview"]
        assert overview["files"] > 0
        assert overview["symbols"] > 0

    def test_dashboard_json_health_has_score(self, cli_runner, indexed_project, monkeypatch):
        """JSON health section should have a score and label."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        health = data["health"]
        assert "score" in health
        assert "label" in health
        assert 0 <= health["score"] <= 100

    def test_dashboard_json_risks_has_dead_symbols(self, cli_runner, indexed_project, monkeypatch):
        """JSON risks section should include dead_symbols count."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        risks = data["risks"]
        assert "dead_symbols" in risks
        assert isinstance(risks["dead_symbols"], int)


# ============================================================================
# TestDashboardMinimal
# ============================================================================

class TestDashboardMinimal:
    """Test dashboard with a minimal project."""

    def test_dashboard_minimal_project(self, project_factory):
        """Dashboard should work on a project with a single file."""
        proj = project_factory({
            "main.py": "def main():\n    return 1\n",
        })
        runner = CliRunner()
        result = invoke_cli(runner, ["dashboard"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_dashboard_minimal_json(self, project_factory):
        """JSON mode should work on a minimal project."""
        proj = project_factory({
            "main.py": "def main():\n    return 1\n",
        })
        runner = CliRunner()
        result = invoke_cli(runner, ["dashboard"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "dashboard")
        assert_json_envelope(data, "dashboard")
        assert data["overview"]["files"] >= 1
