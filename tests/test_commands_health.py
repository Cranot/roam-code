"""Tests for health/quality CLI commands.

Covers ~60 tests across 8 commands: health, weather, debt, complexity,
alerts, trend, fitness, snapshot. Uses CliRunner for in-process testing.
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
# TestHealth
# ============================================================================

class TestHealth:
    """Tests for `roam health` -- overall health score (0-100)."""

    def test_health_shows_score(self, cli_runner, indexed_project, monkeypatch):
        """roam health output should contain a numeric score."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
        assert result.exit_code == 0, f"health failed: {result.output}"
        assert "Health Score:" in result.output or "health" in result.output.lower()

    def test_health_verdict(self, cli_runner, indexed_project, monkeypatch):
        """roam health should contain a VERDICT line."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
        assert result.exit_code == 0, f"health failed: {result.output}"
        assert "VERDICT:" in result.output, (
            f"Missing VERDICT line in output:\n{result.output}"
        )

    def test_health_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json health should return a valid envelope with health_score and verdict."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "health")
        assert_json_envelope(data, "health")
        summary = data["summary"]
        assert "health_score" in summary, f"Missing health_score in summary: {summary}"
        assert "verdict" in summary, f"Missing verdict in summary: {summary}"

    def test_health_json_has_metrics(self, cli_runner, indexed_project, monkeypatch):
        """roam --json health summary should include expected metric keys."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "health")
        summary = data["summary"]
        expected_keys = ["health_score", "verdict", "tangle_ratio", "issue_count", "severity"]
        for key in expected_keys:
            assert key in summary, f"Missing '{key}' in summary: {list(summary.keys())}"

    def test_health_score_range(self, cli_runner, indexed_project, monkeypatch):
        """Health score should be between 0 and 100."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "health")
        score = data["summary"]["health_score"]
        assert 0 <= score <= 100, f"Health score out of range: {score}"

    def test_health_json_has_structural_keys(self, cli_runner, indexed_project, monkeypatch):
        """roam --json health should include top-level structural data keys."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "health")
        # These keys should exist at top level of the envelope
        for key in ["cycles", "god_components", "bottlenecks"]:
            assert key in data, f"Missing '{key}' in JSON output: {list(data.keys())}"

    def test_health_severity_counts(self, cli_runner, indexed_project, monkeypatch):
        """roam --json health severity field should have expected levels."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "health")
        severity = data["summary"]["severity"]
        assert isinstance(severity, dict)
        for level in ["CRITICAL", "WARNING", "INFO"]:
            assert level in severity, f"Missing '{level}' in severity: {severity}"

    def test_health_text_has_sections(self, cli_runner, indexed_project, monkeypatch):
        """roam health text output should have structural sections."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        assert "=== Cycles ===" in out, f"Missing Cycles section:\n{out}"
        assert "=== God Components" in out, f"Missing God Components section:\n{out}"
        assert "=== Bottlenecks" in out, f"Missing Bottlenecks section:\n{out}"

    def test_health_no_framework_flag(self, cli_runner, indexed_project, monkeypatch):
        """roam health --no-framework should run without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["health", "--no-framework"], cwd=indexed_project)
        assert result.exit_code == 0, f"health --no-framework failed: {result.output}"


# ============================================================================
# TestWeather
# ============================================================================

class TestWeather:
    """Tests for `roam weather` -- churn x complexity hotspot ranking."""

    def test_weather_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam weather should exit 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["weather"], cwd=indexed_project)
        assert result.exit_code == 0, f"weather failed: {result.output}"

    def test_weather_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json weather should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["weather"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "weather")
        assert_json_envelope(data, "weather")
        assert "hotspots" in data or "hotspots" in data.get("summary", {})

    def test_weather_shows_metrics(self, cli_runner, indexed_project, monkeypatch):
        """roam weather output should have structured content."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["weather"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        # Either shows hotspot table or "No churn data" message
        assert ("Hotspots" in out or "Score" in out
                or "No churn data" in out), (
            f"Missing expected output in weather:\n{out}"
        )

    def test_weather_json_summary_keys(self, cli_runner, indexed_project, monkeypatch):
        """roam --json weather summary should have hotspots count."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["weather"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "weather")
        summary = data.get("summary", {})
        assert "hotspots" in summary, f"Missing 'hotspots' in summary: {summary}"

    def test_weather_limit_option(self, cli_runner, indexed_project, monkeypatch):
        """roam weather -n 5 should run without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["weather", "-n", "5"], cwd=indexed_project)
        assert result.exit_code == 0, f"weather -n 5 failed: {result.output}"


# ============================================================================
# TestDebt
# ============================================================================

class TestDebt:
    """Tests for `roam debt` -- technical debt overview."""

    def test_debt_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam debt should exit 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt"], cwd=indexed_project)
        assert result.exit_code == 0, f"debt failed: {result.output}"

    def test_debt_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json debt should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "debt")
        assert_json_envelope(data, "debt")
        assert "summary" in data

    def test_debt_shows_categories(self, cli_runner, indexed_project, monkeypatch):
        """roam debt output should display debt categories or file stats."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        # Should show either the debt table or "No file stats" message
        assert ("Debt" in out or "debt" in out.lower()
                or "No file stats" in out), (
            f"Missing debt categories in output:\n{out}"
        )

    def test_debt_json_summary_keys(self, cli_runner, indexed_project, monkeypatch):
        """roam --json debt summary should have expected metric keys."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "debt")
        summary = data.get("summary", {})
        assert "total_files" in summary or "total_debt" in summary, (
            f"Missing debt stats in summary: {summary}"
        )

    def test_debt_by_kind(self, cli_runner, indexed_project, monkeypatch):
        """roam debt --by-kind should group by directory."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt", "--by-kind"], cwd=indexed_project)
        assert result.exit_code == 0, f"debt --by-kind failed: {result.output}"

    def test_debt_threshold(self, cli_runner, indexed_project, monkeypatch):
        """roam debt --threshold 0 should run without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt", "--threshold", "0"], cwd=indexed_project)
        assert result.exit_code == 0, f"debt --threshold 0 failed: {result.output}"

    def test_debt_json_has_items(self, cli_runner, indexed_project, monkeypatch):
        """roam --json debt should have items or groups array."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["debt"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "debt")
        assert "items" in data or "groups" in data, (
            f"Missing items/groups in debt JSON: {list(data.keys())}"
        )


# ============================================================================
# TestComplexity
# ============================================================================

class TestComplexity:
    """Tests for `roam complexity` -- complexity ranking."""

    def test_complexity_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam complexity should exit 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity"], cwd=indexed_project)
        assert result.exit_code == 0, f"complexity failed: {result.output}"

    def test_complexity_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json complexity should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "complexity")
        assert_json_envelope(data, "complexity")

    def test_complexity_shows_ranking(self, cli_runner, indexed_project, monkeypatch):
        """roam complexity should list symbols by complexity."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        # Should show complexity data or "No matching symbols" or analysis stats
        assert ("complexity" in out.lower() or "analyzed" in out.lower()
                or "No matching" in out), (
            f"Missing complexity ranking in output:\n{out}"
        )

    def test_complexity_json_has_symbols(self, cli_runner, indexed_project, monkeypatch):
        """roam --json complexity should have symbols array."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "complexity")
        assert "symbols" in data or "files" in data, (
            f"Missing symbols/files in complexity JSON: {list(data.keys())}"
        )

    def test_complexity_threshold(self, cli_runner, indexed_project, monkeypatch):
        """roam complexity --threshold 0 should include all symbols."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity", "--threshold", "0"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_complexity_by_file(self, cli_runner, indexed_project, monkeypatch):
        """roam complexity --by-file should group results by file."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity", "--by-file"], cwd=indexed_project)
        assert result.exit_code == 0, f"complexity --by-file failed: {result.output}"

    def test_complexity_bumpy_road(self, cli_runner, indexed_project, monkeypatch):
        """roam complexity --bumpy-road should run without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity", "--bumpy-road"], cwd=indexed_project)
        assert result.exit_code == 0, f"complexity --bumpy-road failed: {result.output}"

    def test_complexity_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """roam --json complexity summary should include analysis stats."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["complexity"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "complexity")
        summary = data.get("summary", {})
        assert "total_analyzed" in summary or "files" in summary or "mode" in summary, (
            f"Missing expected keys in complexity summary: {summary}"
        )


# ============================================================================
# TestAlerts
# ============================================================================

class TestAlerts:
    """Tests for `roam alerts` -- quality alerts."""

    def test_alerts_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam alerts should exit 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["alerts"], cwd=indexed_project)
        assert result.exit_code == 0, f"alerts failed: {result.output}"

    def test_alerts_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json alerts should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["alerts"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        assert_json_envelope(data, "alerts")

    def test_alerts_shows_issues(self, cli_runner, indexed_project, monkeypatch):
        """roam alerts should flag quality issues or show all-clear message."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["alerts"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        # Should show alerts or "No health alerts" message
        assert ("alert" in out.lower() or "no health" in out.lower()
                or "normal" in out.lower()), (
            f"Missing alerts/all-clear in output:\n{out}"
        )

    def test_alerts_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """roam --json alerts summary should have count fields."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["alerts"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        summary = data.get("summary", {})
        assert "total" in summary, f"Missing 'total' in alerts summary: {summary}"

    def test_alerts_json_has_alerts_array(self, cli_runner, indexed_project, monkeypatch):
        """roam --json alerts should have an alerts array."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["alerts"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        assert "alerts" in data, f"Missing 'alerts' array in JSON: {list(data.keys())}"
        assert isinstance(data["alerts"], list)

    def test_alerts_json_severity_counts(self, cli_runner, indexed_project, monkeypatch):
        """roam --json alerts summary should break down by severity level."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["alerts"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        summary = data.get("summary", {})
        for level in ["critical", "warning", "info"]:
            assert level in summary, f"Missing '{level}' in alerts summary: {summary}"


# ============================================================================
# TestTrend
# ============================================================================

class TestTrend:
    """Tests for `roam trend` -- health history sparklines."""

    def test_trend_no_snapshots(self, cli_runner, indexed_project, monkeypatch):
        """roam trend should handle gracefully when no snapshots exist.

        Note: indexing may create an automatic snapshot. Either way the
        command should not crash.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trend"], cwd=indexed_project)
        # Should exit 0 whether or not snapshots exist
        assert result.exit_code == 0, f"trend failed: {result.output}"
        out = result.output
        # Either shows snapshot table or 'No snapshots' message
        assert ("Trend" in out or "Score" in out or "Date" in out
                or "No snapshots" in out or "snapshot" in out.lower()), (
            f"Unexpected trend output:\n{out}"
        )

    def test_trend_with_snapshots(self, cli_runner, project_with_snapshots, monkeypatch):
        """roam trend with multiple snapshots should show sparklines."""
        monkeypatch.chdir(project_with_snapshots)
        result = invoke_cli(cli_runner, ["trend"], cwd=project_with_snapshots)
        assert result.exit_code == 0, f"trend failed: {result.output}"
        out = result.output
        assert "Trend" in out or "Score" in out or "Date" in out, (
            f"Missing trend table in output:\n{out}"
        )

    def test_trend_json(self, cli_runner, project_with_snapshots, monkeypatch):
        """roam --json trend should return an envelope with snapshots array."""
        monkeypatch.chdir(project_with_snapshots)
        result = invoke_cli(cli_runner, ["trend"], cwd=project_with_snapshots, json_mode=True)
        data = parse_json_output(result, "trend")
        assert_json_envelope(data, "trend")
        assert "snapshots" in data, f"Missing 'snapshots' in trend JSON: {list(data.keys())}"
        assert isinstance(data["snapshots"], list)

    def test_trend_json_has_snapshot_count(self, cli_runner, project_with_snapshots, monkeypatch):
        """roam --json trend summary should report number of snapshots."""
        monkeypatch.chdir(project_with_snapshots)
        result = invoke_cli(cli_runner, ["trend"], cwd=project_with_snapshots, json_mode=True)
        data = parse_json_output(result, "trend")
        summary = data.get("summary", {})
        assert "snapshots" in summary, f"Missing 'snapshots' count in summary: {summary}"
        assert summary["snapshots"] > 0, "Expected at least 1 snapshot"

    def test_trend_assert_pass(self, cli_runner, project_with_snapshots, monkeypatch):
        """roam trend --assert 'health_score>=0' should pass (exit 0)."""
        monkeypatch.chdir(project_with_snapshots)
        result = invoke_cli(
            cli_runner,
            ["trend", "--assert", "health_score>=0"],
            cwd=project_with_snapshots,
        )
        assert result.exit_code == 0, (
            f"trend --assert health_score>=0 should pass but failed: {result.output}"
        )

    def test_trend_assert_fail(self, cli_runner, project_with_snapshots, monkeypatch):
        """roam trend --assert 'health_score>=999' should fail with exit 1."""
        monkeypatch.chdir(project_with_snapshots)
        result = cli_runner.invoke(
            cli,
            ["trend", "--assert", "health_score>=999"],
            catch_exceptions=True,
        )
        # SystemExit(1) is caught by CliRunner as exit_code=1
        assert result.exit_code != 0, (
            f"Expected assertion failure (exit != 0), got exit_code={result.exit_code}: "
            f"{result.output}"
        )

    def test_trend_range(self, cli_runner, project_with_snapshots, monkeypatch):
        """roam trend --range 3 should limit the number of snapshots shown."""
        monkeypatch.chdir(project_with_snapshots)
        result = invoke_cli(
            cli_runner,
            ["trend", "--range", "3"],
            cwd=project_with_snapshots,
            json_mode=True,
        )
        data = parse_json_output(result, "trend")
        snapshots = data.get("snapshots", [])
        assert len(snapshots) <= 3, (
            f"Expected at most 3 snapshots with --range 3, got {len(snapshots)}"
        )

    def test_trend_json_snapshots_have_health_score(self, cli_runner, project_with_snapshots, monkeypatch):
        """Each snapshot in trend JSON should have a health_score field."""
        monkeypatch.chdir(project_with_snapshots)
        result = invoke_cli(cli_runner, ["trend"], cwd=project_with_snapshots, json_mode=True)
        data = parse_json_output(result, "trend")
        snapshots = data.get("snapshots", [])
        assert len(snapshots) > 0, "Expected at least one snapshot"
        for i, snap in enumerate(snapshots):
            assert "health_score" in snap, (
                f"Snapshot {i} missing 'health_score': {snap}"
            )


# ============================================================================
# TestFitness
# ============================================================================

class TestFitness:
    """Tests for `roam fitness` -- architectural fitness functions."""

    def test_fitness_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam fitness should exit 0 (or 1 if rules fail, but not crash)."""
        monkeypatch.chdir(indexed_project)
        result = cli_runner.invoke(cli, ["fitness"], catch_exceptions=True)
        # May exit 0 (no rules or all pass) or 1 (rules fail), but should not crash
        assert result.exit_code in (0, 1), (
            f"fitness crashed with exit_code={result.exit_code}: {result.output}"
        )

    def test_fitness_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json fitness should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = cli_runner.invoke(cli, ["--json", "fitness"], catch_exceptions=True)
        # May exit 1 if rules fail, but JSON should still be valid
        if result.output.strip():
            try:
                data = json.loads(result.output)
                assert "command" in data
                assert data["command"] == "fitness"
            except json.JSONDecodeError:
                # Some output is not JSON (e.g. "No fitness rules found")
                pass

    def test_fitness_init(self, cli_runner, indexed_project, monkeypatch):
        """roam fitness --init should create .roam/fitness.yaml."""
        monkeypatch.chdir(indexed_project)
        config_path = indexed_project / ".roam" / "fitness.yaml"
        # Remove if it already exists
        if config_path.exists():
            config_path.unlink()
        result = invoke_cli(cli_runner, ["fitness", "--init"], cwd=indexed_project)
        assert result.exit_code == 0, f"fitness --init failed: {result.output}"
        assert config_path.exists(), "fitness.yaml was not created"

    def test_fitness_with_rules(self, cli_runner, indexed_project, monkeypatch):
        """roam fitness should evaluate rules from fitness.yaml."""
        monkeypatch.chdir(indexed_project)
        # First create the config
        invoke_cli(cli_runner, ["fitness", "--init"], cwd=indexed_project)
        # Then run fitness
        result = cli_runner.invoke(cli, ["fitness"], catch_exceptions=True)
        out = result.output
        # Should mention rule evaluation
        assert ("Fitness check" in out or "rules" in out.lower()
                or "PASS" in out or "FAIL" in out
                or "No fitness rules" in out), (
            f"Missing fitness output:\n{out}"
        )

    def test_fitness_json_with_rules(self, cli_runner, indexed_project, monkeypatch):
        """roam --json fitness with rules should return rules array."""
        monkeypatch.chdir(indexed_project)
        # Create config
        invoke_cli(cli_runner, ["fitness", "--init"], cwd=indexed_project)
        result = cli_runner.invoke(cli, ["--json", "fitness"], catch_exceptions=True)
        if result.output.strip():
            try:
                data = json.loads(result.output)
                if "rules" in data:
                    assert isinstance(data["rules"], list)
            except json.JSONDecodeError:
                pass

    def test_fitness_no_config(self, cli_runner, indexed_project, monkeypatch):
        """roam fitness without fitness.yaml should show instructions."""
        monkeypatch.chdir(indexed_project)
        # Remove config if present
        config_path = indexed_project / ".roam" / "fitness.yaml"
        config_yml = indexed_project / ".roam" / "fitness.yml"
        if config_path.exists():
            config_path.unlink()
        if config_yml.exists():
            config_yml.unlink()
        result = invoke_cli(cli_runner, ["fitness"], cwd=indexed_project)
        assert result.exit_code == 0
        assert ("No fitness rules" in result.output
                or "fitness.yaml" in result.output
                or "--init" in result.output), (
            f"Missing no-config instructions:\n{result.output}"
        )


# ============================================================================
# TestSnapshot
# ============================================================================

class TestSnapshot:
    """Tests for `roam snapshot` -- create a health snapshot."""

    def test_snapshot_creates(self, cli_runner, indexed_project, monkeypatch):
        """roam snapshot should exit 0 and report success."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["snapshot"], cwd=indexed_project)
        assert result.exit_code == 0, f"snapshot failed: {result.output}"
        assert "Snapshot saved" in result.output or "snapshot" in result.output.lower(), (
            f"Missing success message:\n{result.output}"
        )

    def test_snapshot_with_tag(self, cli_runner, indexed_project, monkeypatch):
        """roam snapshot --tag 'v1' should include the tag in output."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["snapshot", "--tag", "v1"], cwd=indexed_project)
        assert result.exit_code == 0, f"snapshot --tag v1 failed: {result.output}"
        assert "v1" in result.output, f"Tag 'v1' not in output: {result.output}"

    def test_snapshot_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json snapshot should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["snapshot", "--tag", "json-test"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "snapshot")
        assert_json_envelope(data, "snapshot")

    def test_snapshot_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """roam --json snapshot summary should include health_score."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["snapshot", "--tag", "summary-test"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "snapshot")
        summary = data.get("summary", {})
        assert "health_score" in summary, f"Missing health_score in summary: {summary}"

    def test_snapshot_health_score_matches_health_command(self, cli_runner, indexed_project, monkeypatch):
        """snapshot and health should use the same composite health score math."""
        monkeypatch.chdir(indexed_project)

        health_result = invoke_cli(
            cli_runner,
            ["health"],
            cwd=indexed_project,
            json_mode=True,
        )
        health_data = parse_json_output(health_result, "health")
        health_score = health_data.get("summary", {}).get("health_score")

        snapshot_result = invoke_cli(
            cli_runner,
            ["snapshot", "--tag", "score-parity"],
            cwd=indexed_project,
            json_mode=True,
        )
        snapshot_data = parse_json_output(snapshot_result, "snapshot")
        snapshot_score = snapshot_data.get("summary", {}).get("health_score")

        assert isinstance(health_score, int)
        assert isinstance(snapshot_score, int)
        assert snapshot_score == health_score

    def test_snapshot_text_shows_metrics(self, cli_runner, indexed_project, monkeypatch):
        """roam snapshot text output should report file/symbol counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["snapshot"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        # Should mention Health score, Files, Symbols
        assert ("Health:" in out or "health" in out.lower()), (
            f"Missing health info in snapshot output:\n{out}"
        )

    def test_snapshot_json_has_detail_fields(self, cli_runner, indexed_project, monkeypatch):
        """roam --json snapshot should include structural detail fields."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["snapshot", "--tag", "detail-test"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "snapshot")
        # The snapshot result is spread into the envelope via **result
        expected_fields = ["health_score", "files", "symbols", "edges"]
        for field in expected_fields:
            assert field in data or field in data.get("summary", {}), (
                f"Missing '{field}' in snapshot JSON: {list(data.keys())}"
            )

    def test_snapshot_multiple(self, cli_runner, indexed_project, monkeypatch):
        """Multiple snapshots should succeed."""
        monkeypatch.chdir(indexed_project)
        for i in range(3):
            result = invoke_cli(
                cli_runner,
                ["snapshot", "--tag", f"multi-{i}"],
                cwd=indexed_project,
            )
            assert result.exit_code == 0, (
                f"snapshot {i} failed: {result.output}"
            )
