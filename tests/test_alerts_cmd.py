"""Tests for the `roam alerts` command.

Covers:
- Smoke: fresh indexed project (zero snapshots) exits 0.
- JSON envelope structure and required summary fields.
- VERDICT line in text output.
- Threshold-based alert logic (live metrics, no snapshot history required).
- Alert structure in JSON output when alerts are present.
- Unit tests for internal alert helpers (threshold checks, trend detection).

Named test_alerts_cmd.py to avoid collision with the TestAlerts class in
test_commands_health.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def fresh_project(project_factory):
    """A minimal indexed Python project with no snapshot history.

    alerts runs threshold checks against live metrics when no snapshots exist,
    so this exercises the zero-snapshot code path.
    """
    return project_factory(
        {
            "main.py": (
                "def main():\n"
                '    """Application entry point."""\n'
                "    result = compute()\n"
                "    return result\n"
                "\n"
                "\n"
                "def compute():\n"
                '    """Perform the main computation."""\n'
                "    return 42\n"
            ),
            "helpers.py": (
                "def format_result(value):\n"
                '    """Format a numeric result as a string."""\n'
                '    return f"Result: {value}"\n'
                "\n"
                "\n"
                "def clamp(value, low, high):\n"
                '    """Clamp value into [low, high]."""\n'
                "    return max(low, min(high, value))\n"
            ),
        }
    )


@pytest.fixture
def multi_snapshot_project(tmp_path):
    """A project that accumulates several re-indexes to build snapshot history.

    Each re-index may record a snapshot row, giving the alerts command trend
    data to analyse.
    """
    proj = tmp_path / "alerts_snap"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def start():\n"
        '    """Start the application."""\n'
        "    return run()\n"
        "\n"
        "def run():\n"
        '    """Run the main loop."""\n'
        "    return 0\n"
    )
    git_init(proj)

    # Initial index
    out, rc = index_in_process(proj)
    assert rc == 0, f"Initial roam index failed:\n{out}"

    # Add more files and re-index a few times to build history
    for i in range(1, 5):
        (proj / f"module_{i}.py").write_text(f'def func_{i}():\n    """Module {i} function."""\n    return {i}\n')
        git_commit(proj, f"add module_{i}")
        out, rc = index_in_process(proj)
        assert rc == 0, f"roam index iteration {i} failed:\n{out}"

    return proj


# ---------------------------------------------------------------------------
# Smoke tests — basic invocation
# ---------------------------------------------------------------------------


class TestAlertsCmdSmoke:
    """Basic invocation tests: exit codes and presence of key output tokens."""

    def test_exits_zero_fresh_project(self, fresh_project, cli_runner):
        """alerts exits 0 on a freshly indexed project with no snapshots."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project)
        assert result.exit_code == 0, f"alerts failed:\n{result.output}"

    def test_verdict_line_present(self, fresh_project, cli_runner):
        """Text output contains a VERDICT line."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output, f"Expected VERDICT: in output:\n{result.output}"

    def test_output_is_non_empty(self, fresh_project, cli_runner):
        """alerts produces non-empty output."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project)
        assert result.output.strip(), "Expected non-empty output from alerts"

    def test_help_flag(self, cli_runner):
        """alerts --help exits 0 and mentions relevant terms."""
        from roam.cli import cli

        result = cli_runner.invoke(cli, ["alerts", "--help"])
        assert result.exit_code == 0
        out_lower = result.output.lower()
        assert "alert" in out_lower or "trend" in out_lower or "threshold" in out_lower

    def test_exits_zero_with_snapshots(self, multi_snapshot_project, cli_runner):
        """alerts exits 0 when snapshot history is available."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=multi_snapshot_project)
        assert result.exit_code == 0, f"alerts with snapshots failed:\n{result.output}"

    def test_verdict_present_with_snapshots(self, multi_snapshot_project, cli_runner):
        """Text output contains VERDICT when snapshot history exists."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=multi_snapshot_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output, f"Expected VERDICT: in snapshot output:\n{result.output}"


# ---------------------------------------------------------------------------
# JSON envelope and summary tests
# ---------------------------------------------------------------------------


class TestAlertsCmdJson:
    """JSON output contract validation."""

    def test_json_exits_zero(self, fresh_project, cli_runner):
        """alerts --json exits 0."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        assert result.exit_code == 0, f"alerts --json failed:\n{result.output}"

    def test_json_envelope_contract(self, fresh_project, cli_runner):
        """JSON output follows the roam envelope contract."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        assert_json_envelope(data, "alerts")

    def test_json_summary_has_verdict(self, fresh_project, cli_runner):
        """JSON summary contains a non-empty 'verdict' string."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {summary}"
        assert isinstance(summary["verdict"], str)
        assert summary["verdict"]

    def test_json_summary_has_total(self, fresh_project, cli_runner):
        """JSON summary contains a numeric 'total' field."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        summary = data.get("summary", {})
        assert "total" in summary, f"Missing 'total' in summary: {summary}"
        assert isinstance(summary["total"], int)
        assert summary["total"] >= 0

    def test_json_summary_has_severity_counts(self, fresh_project, cli_runner):
        """JSON summary breaks down alert counts by severity level."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        summary = data.get("summary", {})
        for level in ("critical", "warning", "info"):
            assert level in summary, f"Missing '{level}' count in summary: {summary}"
            assert isinstance(summary[level], int)
            assert summary[level] >= 0

    def test_json_summary_has_snapshots_analyzed(self, fresh_project, cli_runner):
        """JSON summary reports how many snapshots were analyzed."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        summary = data.get("summary", {})
        assert "snapshots_analyzed" in summary, f"Missing 'snapshots_analyzed' in summary: {summary}"
        assert isinstance(summary["snapshots_analyzed"], int)
        assert summary["snapshots_analyzed"] >= 0

    def test_json_has_alerts_array(self, fresh_project, cli_runner):
        """JSON output contains a top-level 'alerts' array."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        assert "alerts" in data, f"Missing 'alerts' array: {list(data.keys())}"
        assert isinstance(data["alerts"], list)

    def test_json_severity_counts_match_array(self, fresh_project, cli_runner):
        """summary.total equals the length of the alerts array."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        total = data["summary"]["total"]
        actual = len(data["alerts"])
        assert actual == total, f"summary.total={total} does not match alerts array length={actual}"

    def test_json_alerts_have_required_fields(self, fresh_project, cli_runner):
        """When alerts are present, each entry has the required fields."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        for alert in data.get("alerts", []):
            assert "level" in alert, f"Missing 'level' in alert: {alert}"
            assert "metric" in alert, f"Missing 'metric' in alert: {alert}"
            assert "message" in alert, f"Missing 'message' in alert: {alert}"
            assert "current_value" in alert, f"Missing 'current_value' in alert: {alert}"

    def test_json_alert_levels_are_valid(self, fresh_project, cli_runner):
        """All alert level values are one of critical, warning, or info.

        W649: canonical roam severity vocabulary is lowercase
        (``critical`` / ``warning`` / ``info``) — see
        :mod:`roam.output._severity`. Pre-W649 these were UPPER-cased and
        out of vocabulary with the rest of the surface.
        """
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        valid_levels = {"critical", "warning", "info"}
        for alert in data.get("alerts", []):
            assert alert["level"] in valid_levels, f"Unexpected level '{alert['level']}' in: {alert}"

    def test_json_envelope_with_snapshots(self, multi_snapshot_project, cli_runner):
        """JSON envelope contract holds when snapshot history exists."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=multi_snapshot_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        assert_json_envelope(data, "alerts")

    def test_json_verdict_no_alerts_text(self, fresh_project, cli_runner):
        """When there are no alerts, verdict mentions normal ranges or 'no alerts'."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "alerts")
        if data["summary"]["total"] == 0:
            verdict_lower = data["summary"]["verdict"].lower()
            assert "no alert" in verdict_lower or "normal" in verdict_lower, (
                f"Expected 'no alert' or 'normal' in zero-alert verdict: {data['summary']['verdict']}"
            )


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestAlertsCmdText:
    """Validate the human-readable text output format."""

    def test_text_all_clear_message(self, fresh_project, cli_runner):
        """When no alerts fire, output contains an all-clear message."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project)
        assert result.exit_code == 0
        out_lower = result.output.lower()
        # Either there are alert lines OR an all-clear / VERDICT with no-alert language
        has_alert_content = "alert" in out_lower
        assert has_alert_content, f"Expected 'alert' to appear in output:\n{result.output}"

    def test_text_verdict_is_first_content_line(self, fresh_project, cli_runner):
        """The first non-empty output line starts with 'VERDICT:'."""
        result = invoke_cli(cli_runner, ["alerts"], cwd=fresh_project)
        assert result.exit_code == 0
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert lines, "Output is empty"
        assert lines[0].startswith("VERDICT:"), f"First non-empty line should start with VERDICT:, got: {lines[0]!r}"


# ---------------------------------------------------------------------------
# Unit tests — internal alert logic
# ---------------------------------------------------------------------------


class TestAlertsInternals:
    """Unit tests for alert detection helpers imported directly."""

    def test_check_thresholds_health_below_60(self):
        """health_score below 60 triggers a critical threshold alert (W649)."""
        from roam.commands.cmd_alerts import _check_thresholds

        alerts = _check_thresholds({"health_score": 45})
        assert len(alerts) >= 1
        levels = {a["level"] for a in alerts}
        assert "critical" in levels

    def test_check_thresholds_health_above_threshold_no_alert(self):
        """health_score above 60 does not trigger a threshold alert."""
        from roam.commands.cmd_alerts import _check_thresholds

        alerts = _check_thresholds({"health_score": 80})
        health_alerts = [a for a in alerts if a["metric"] == "health_score"]
        assert len(health_alerts) == 0

    def test_check_thresholds_cycles_above_10(self):
        """cycles > 10 triggers a warning alert (W649 lowercase)."""
        from roam.commands.cmd_alerts import _check_thresholds

        alerts = _check_thresholds({"cycles": 15})
        cycle_alerts = [a for a in alerts if a["metric"] == "cycles"]
        assert len(cycle_alerts) >= 1
        assert cycle_alerts[0]["level"] == "warning"

    def test_check_thresholds_cycles_at_threshold_no_alert(self):
        """cycles == 10 does not trigger a warning (rule is strictly >, not >=)."""
        from roam.commands.cmd_alerts import _check_thresholds

        alerts = _check_thresholds({"cycles": 10})
        cycle_alerts = [a for a in alerts if a["metric"] == "cycles"]
        assert len(cycle_alerts) == 0

    def test_check_thresholds_missing_metric_skipped(self):
        """Missing metric keys are silently skipped."""
        from roam.commands.cmd_alerts import _check_thresholds

        # Only partial metrics provided — should not raise
        alerts = _check_thresholds({"health_score": 75})
        assert isinstance(alerts, list)

    def test_check_thresholds_empty_metrics(self):
        """Empty metrics dict produces no alerts."""
        from roam.commands.cmd_alerts import _check_thresholds

        alerts = _check_thresholds({})
        assert alerts == []

    def test_check_trends_requires_three_snapshots(self):
        """_check_trends returns no alerts when fewer than 3 snapshots provided."""
        from roam.commands.cmd_alerts import _check_trends

        snaps = [
            {"cycles": 1, "health_score": 80},
            {"cycles": 2, "health_score": 75},
        ]
        alerts = _check_trends(snaps)
        assert alerts == []

    def test_check_trends_monotonic_cycles_increase(self):
        """A clear monotonic increase in cycles over 5 snapshots triggers an alert."""
        from roam.commands.cmd_alerts import _check_trends

        snaps = [
            {
                "cycles": 1,
                "health_score": 90,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
            {
                "cycles": 3,
                "health_score": 88,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
            {
                "cycles": 6,
                "health_score": 85,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
            {
                "cycles": 10,
                "health_score": 80,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
            {
                "cycles": 15,
                "health_score": 74,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
        ]
        alerts = _check_trends(snaps)
        cycle_alerts = [a for a in alerts if a["metric"] == "cycles"]
        assert len(cycle_alerts) >= 1, f"Expected trend alert for rising cycles: {alerts}"

    def test_check_trends_stable_series_no_alert(self):
        """A stable series produces no trend alerts."""
        from roam.commands.cmd_alerts import _check_trends

        snaps = [
            {
                "cycles": 3,
                "health_score": 85,
                "god_components": 1,
                "bottlenecks": 2,
                "dead_exports": 5,
                "layer_violations": 0,
            },
            {
                "cycles": 3,
                "health_score": 85,
                "god_components": 1,
                "bottlenecks": 2,
                "dead_exports": 5,
                "layer_violations": 0,
            },
            {
                "cycles": 3,
                "health_score": 85,
                "god_components": 1,
                "bottlenecks": 2,
                "dead_exports": 5,
                "layer_violations": 0,
            },
        ]
        alerts = _check_trends(snaps)
        assert alerts == [], f"Expected no alerts for stable data, got: {alerts}"

    def test_check_rate_of_change_requires_two_snapshots(self):
        """_check_rate_of_change returns no alerts with fewer than 2 snapshots."""
        from roam.commands.cmd_alerts import _check_rate_of_change

        snaps = [{"cycles": 5, "health_score": 80}]
        alerts = _check_rate_of_change(snaps)
        assert alerts == []

    def test_check_rate_of_change_large_jump(self):
        """A >20% worsening change between two snapshots triggers an alert."""
        from roam.commands.cmd_alerts import _check_rate_of_change

        snaps = [
            {
                "cycles": 5,
                "health_score": 85,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
            {
                "cycles": 20,
                "health_score": 60,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
        ]
        alerts = _check_rate_of_change(snaps)
        # cycles jumped 300% — should fire
        cycle_alerts = [a for a in alerts if a["metric"] == "cycles"]
        assert len(cycle_alerts) >= 1, f"Expected rate-of-change alert for cycles, got: {alerts}"

    def test_check_rate_of_change_small_worsening_no_alert(self):
        """A worsening change under 20% does not trigger a rate-of-change alert."""
        from roam.commands.cmd_alerts import _check_rate_of_change

        snaps = [
            {
                "cycles": 10,
                "health_score": 85,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
            {
                "cycles": 11,
                "health_score": 83,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
        ]
        alerts = _check_rate_of_change(snaps)
        cycle_alerts = [a for a in alerts if a["metric"] == "cycles"]
        assert len(cycle_alerts) == 0, f"Unexpected alert for small change: {cycle_alerts}"

    def test_deduplicate_keeps_highest_severity(self):
        """_deduplicate keeps the highest-severity alert when metric+direction clash."""
        from roam.commands.cmd_alerts import _deduplicate

        alerts = [
            {"level": "info", "metric": "cycles", "message": "msg1", "current_value": 5, "trend_direction": "up"},
            {"level": "warning", "metric": "cycles", "message": "msg2", "current_value": 5, "trend_direction": "up"},
        ]
        deduped = _deduplicate(alerts)
        assert len(deduped) == 1
        assert deduped[0]["level"] == "warning"

    def test_deduplicate_preserves_different_metrics(self):
        """_deduplicate keeps alerts for distinct metrics."""
        from roam.commands.cmd_alerts import _deduplicate

        alerts = [
            {"level": "critical", "metric": "health_score", "message": "low", "current_value": 40},
            {"level": "warning", "metric": "cycles", "message": "high", "current_value": 12, "trend_direction": "up"},
        ]
        deduped = _deduplicate(alerts)
        assert len(deduped) == 2
        metrics = {a["metric"] for a in deduped}
        assert "health_score" in metrics
        assert "cycles" in metrics

    def test_mann_kendall_s_ascending(self):
        """_mann_kendall_s returns positive S for a strictly ascending series."""
        from roam.commands.cmd_alerts import _mann_kendall_s

        s, p = _mann_kendall_s([1, 2, 3, 4, 5])
        assert s > 0

    def test_mann_kendall_s_descending(self):
        """_mann_kendall_s returns negative S for a strictly descending series."""
        from roam.commands.cmd_alerts import _mann_kendall_s

        s, p = _mann_kendall_s([5, 4, 3, 2, 1])
        assert s < 0

    def test_mann_kendall_s_flat(self):
        """_mann_kendall_s returns S=0 for a constant series."""
        from roam.commands.cmd_alerts import _mann_kendall_s

        s, p = _mann_kendall_s([7, 7, 7, 7])
        assert s == 0

    def test_sens_slope_positive(self):
        """_sens_slope returns a positive slope for an increasing series."""
        from roam.commands.cmd_alerts import _sens_slope

        slope = _sens_slope([1, 2, 3, 4, 5])
        assert slope > 0

    def test_sens_slope_zero_for_constant(self):
        """_sens_slope returns 0 for a constant series."""
        from roam.commands.cmd_alerts import _sens_slope

        slope = _sens_slope([5, 5, 5, 5])
        assert slope == 0.0

    def test_sens_slope_empty(self):
        """_sens_slope returns 0.0 for an empty list."""
        from roam.commands.cmd_alerts import _sens_slope

        assert _sens_slope([]) == 0.0

    def test_is_monotonic_worsening_requires_three(self):
        """_is_monotonic_worsening returns False for fewer than 3 values."""
        from roam.commands.cmd_alerts import _is_monotonic_worsening

        assert _is_monotonic_worsening([1, 2], "cycles") is False
        assert _is_monotonic_worsening([], "cycles") is False

    def test_make_alert_structure(self):
        """_make_alert returns a dict with all expected fields."""
        from roam.commands.cmd_alerts import _make_alert

        alert = _make_alert("warning", "cycles", "cycles=15 (above 10 threshold)", 15)
        assert alert["level"] == "warning"
        assert alert["metric"] == "cycles"
        assert alert["current_value"] == 15
        assert "message" in alert
        assert "trend_direction" not in alert  # not supplied

    def test_make_alert_with_trend_direction(self):
        """_make_alert includes trend_direction when supplied."""
        from roam.commands.cmd_alerts import _make_alert

        alert = _make_alert("warning", "cycles", "msg", 15, trend_direction="up")
        assert alert["trend_direction"] == "up"
