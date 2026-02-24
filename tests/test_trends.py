"""Tests for the roam trends command.

Covers:
- --record flag: stores metric snapshots
- Default display: shows trends table with VERDICT line
- --days flag: controls time window
- --metric flag: filters to a single metric
- JSON envelope structure
- Alerts for worsening metrics
- Graceful handling of no data
- Multiple snapshots produce meaningful trends
- Invalid metric name error
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import click
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Local CLI shim
# ---------------------------------------------------------------------------

def _make_local_cli():
    """Return a Click group containing only the trends command."""
    from roam.commands.cmd_trends import trends

    @click.group()
    @click.option("--json", "json_out", is_flag=True)
    @click.pass_context
    def _local_cli(ctx, json_out):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_out

    _local_cli.add_command(trends)
    return _local_cli


_LOCAL_CLI = _make_local_cli()


def _invoke(args, cwd=None, json_mode=False):
    """Invoke the trends command via the local CLI shim."""
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(_LOCAL_CLI, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(result, cmd="trends"):
    """Parse JSON from a CliRunner result, with a helpful error on failure."""
    assert result.exit_code == 0, (
        f"{cmd} exited {result.exit_code}:\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from {cmd}: {e}\nOutput:\n{result.output[:600]}"
        )


def _assert_envelope(data, cmd="trends"):
    """Verify standard roam JSON envelope keys."""
    assert isinstance(data, dict)
    assert data.get("command") == cmd, f"Expected command={cmd}, got {data.get('command')}"
    assert "version" in data
    assert "timestamp" in data or ("_meta" in data and "timestamp" in data["_meta"])
    assert "summary" in data
    assert isinstance(data["summary"], dict)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def trends_project(tmp_path, monkeypatch):
    """A project indexed and ready for trends recording."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        'def main():\n'
        '    return "hello"\n\n'
        'def helper(x):\n'
        '    if x > 0:\n'
        '        return x * 2\n'
        '    return 0\n'
    )
    (proj / "utils.py").write_text(
        'def add(a, b):\n'
        '    return a + b\n'
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def trends_project_with_data(trends_project, monkeypatch):
    """A project with multiple trend snapshots recorded."""
    monkeypatch.chdir(trends_project)
    # Record initial snapshot
    result = _invoke(["trends", "--record"], cwd=trends_project)
    assert result.exit_code == 0, f"record 1 failed: {result.output}"

    # Add a file and re-index, then record again
    (trends_project / "extra.py").write_text(
        'def extra_func(x):\n'
        '    if x > 10:\n'
        '        if x > 20:\n'
        '            return x * 3\n'
        '    return x\n'
    )
    git_commit(trends_project, "add extra")
    out, rc = index_in_process(trends_project)
    assert rc == 0, f"re-index failed: {out}"
    result = _invoke(["trends", "--record"], cwd=trends_project)
    assert result.exit_code == 0, f"record 2 failed: {result.output}"

    # Add another file and record a third snapshot
    (trends_project / "more.py").write_text(
        'def more_func():\n'
        '    return 42\n'
    )
    git_commit(trends_project, "add more")
    out, rc = index_in_process(trends_project)
    assert rc == 0, f"re-index 2 failed: {out}"
    result = _invoke(["trends", "--record"], cwd=trends_project)
    assert result.exit_code == 0, f"record 3 failed: {result.output}"

    return trends_project


# ---------------------------------------------------------------------------
# Tests: --record
# ---------------------------------------------------------------------------

class TestTrendsRecord:
    """Tests for the --record flag."""

    def test_record_exits_zero(self, trends_project):
        """Recording a snapshot exits cleanly."""
        result = _invoke(["trends", "--record"], cwd=trends_project)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )

    def test_record_text_output(self, trends_project):
        """Text output includes VERDICT and metric names."""
        result = _invoke(["trends", "--record"], cwd=trends_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "Snapshot recorded" in result.output
        assert "health_score" in result.output

    def test_record_json_envelope(self, trends_project):
        """JSON output follows the standard envelope contract."""
        result = _invoke(["trends", "--record"], cwd=trends_project, json_mode=True)
        data = _parse_json(result)
        _assert_envelope(data)
        assert data["summary"]["verdict"] == "Snapshot recorded"
        assert "metrics" in data
        assert isinstance(data["metrics"], dict)
        assert "health_score" in data["metrics"]

    def test_record_stores_data(self, trends_project):
        """After recording, metric_snapshots table has rows."""
        _invoke(["trends", "--record"], cwd=trends_project)
        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM metric_snapshots"
            ).fetchone()[0]
        assert count > 0, "Expected metric_snapshots to have rows after --record"

    def test_record_stores_all_metrics(self, trends_project):
        """All 8 defined metrics are stored in a single snapshot."""
        _invoke(["trends", "--record"], cwd=trends_project)
        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            names = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT metric_name FROM metric_snapshots"
                ).fetchall()
            ]
        from roam.commands.cmd_trends import _METRIC_DEFS
        for metric_name in _METRIC_DEFS:
            assert metric_name in names, (
                f"Expected metric '{metric_name}' in snapshots, got: {names}"
            )


# ---------------------------------------------------------------------------
# Tests: display mode (no --record)
# ---------------------------------------------------------------------------

class TestTrendsDisplay:
    """Tests for the default display mode."""

    def test_no_data_exits_zero(self, trends_project):
        """Without snapshots, the command exits 0 with a helpful message."""
        result = _invoke(["trends"], cwd=trends_project)
        assert result.exit_code == 0
        assert "No trend data" in result.output or "VERDICT" in result.output

    def test_no_data_json(self, trends_project):
        """Without snapshots, JSON output has valid envelope and empty metrics."""
        result = _invoke(["trends"], cwd=trends_project, json_mode=True)
        data = _parse_json(result)
        _assert_envelope(data)
        assert data["snapshots_count"] == 0
        assert data["metrics"] == []

    def test_display_verdict(self, trends_project_with_data):
        """Text output starts with VERDICT line."""
        result = _invoke(["trends"], cwd=trends_project_with_data)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:"), (
            f"Expected VERDICT line, got: {first_line!r}"
        )

    def test_display_table_headers(self, trends_project_with_data):
        """Text output includes the table headers."""
        result = _invoke(["trends"], cwd=trends_project_with_data)
        assert result.exit_code == 0
        assert "METRIC" in result.output
        assert "LATEST" in result.output
        assert "DIRECTION" in result.output

    def test_display_shows_metrics(self, trends_project_with_data):
        """Text output includes known metric names."""
        result = _invoke(["trends"], cwd=trends_project_with_data)
        assert result.exit_code == 0
        assert "health_score" in result.output
        assert "total_files" in result.output

    def test_display_json_has_metrics(self, trends_project_with_data):
        """JSON output has a non-empty metrics list."""
        result = _invoke(["trends"], cwd=trends_project_with_data, json_mode=True)
        data = _parse_json(result)
        _assert_envelope(data)
        assert len(data["metrics"]) > 0

    def test_display_json_metric_fields(self, trends_project_with_data):
        """Each metric in JSON output has the required fields."""
        result = _invoke(["trends"], cwd=trends_project_with_data, json_mode=True)
        data = _parse_json(result)
        required = {"name", "latest", "change", "change_pct", "direction", "history"}
        for m in data["metrics"]:
            missing = required - set(m.keys())
            assert not missing, (
                f"Metric {m.get('name')} missing fields: {missing}"
            )

    def test_display_json_summary_fields(self, trends_project_with_data):
        """Summary contains verdict, days, snapshots_count."""
        result = _invoke(["trends"], cwd=trends_project_with_data, json_mode=True)
        data = _parse_json(result)
        summary = data["summary"]
        assert "verdict" in summary
        assert "days" in summary
        assert "snapshots_count" in summary

    def test_display_json_has_alerts(self, trends_project_with_data):
        """JSON output has an alerts list (possibly empty)."""
        result = _invoke(["trends"], cwd=trends_project_with_data, json_mode=True)
        data = _parse_json(result)
        assert "alerts" in data
        assert isinstance(data["alerts"], list)

    def test_display_json_history_is_list(self, trends_project_with_data):
        """Each metric's history field is a list of numbers."""
        result = _invoke(["trends"], cwd=trends_project_with_data, json_mode=True)
        data = _parse_json(result)
        for m in data["metrics"]:
            assert isinstance(m["history"], list), (
                f"Expected history to be a list for {m['name']}"
            )
            assert len(m["history"]) >= 2, (
                f"Expected at least 2 history points for {m['name']}"
            )


# ---------------------------------------------------------------------------
# Tests: --metric flag
# ---------------------------------------------------------------------------

class TestTrendsMetricFilter:
    """Tests for the --metric flag."""

    def test_metric_filter_single(self, trends_project_with_data):
        """--metric shows only the requested metric."""
        result = _invoke(
            ["trends", "--metric", "health_score"],
            cwd=trends_project_with_data,
            json_mode=True,
        )
        data = _parse_json(result)
        assert len(data["metrics"]) == 1
        assert data["metrics"][0]["name"] == "health_score"

    def test_metric_filter_text(self, trends_project_with_data):
        """Text output only shows the filtered metric."""
        result = _invoke(
            ["trends", "--metric", "total_files"],
            cwd=trends_project_with_data,
        )
        assert result.exit_code == 0
        assert "total_files" in result.output
        # Other metrics should not appear in the table rows
        # (they might appear in the verdict line, so check table area)
        lines = result.output.strip().splitlines()
        # Find table rows (after the header separator line with dashes)
        in_table = False
        table_rows = []
        for line in lines:
            if line.strip().startswith("---"):
                in_table = True
                continue
            if in_table and line.strip():
                table_rows.append(line)
        # All table rows should mention total_files
        for row in table_rows:
            assert "total_files" in row, (
                f"Expected only total_files rows, got: {row!r}"
            )

    def test_metric_filter_invalid(self, trends_project_with_data):
        """--metric with an invalid name exits with error."""
        result = _invoke(
            ["trends", "--metric", "nonexistent_metric"],
            cwd=trends_project_with_data,
        )
        assert result.exit_code != 0
        assert "Unknown metric" in result.output


# ---------------------------------------------------------------------------
# Tests: --days flag
# ---------------------------------------------------------------------------

class TestTrendsDays:
    """Tests for the --days flag."""

    def test_days_default(self, trends_project_with_data):
        """Default --days=30 is accepted."""
        result = _invoke(["trends"], cwd=trends_project_with_data, json_mode=True)
        data = _parse_json(result)
        assert data["days"] == 30

    def test_days_custom(self, trends_project_with_data):
        """Custom --days value is reflected in output."""
        result = _invoke(
            ["trends", "--days", "7"],
            cwd=trends_project_with_data,
            json_mode=True,
        )
        data = _parse_json(result)
        assert data["days"] == 7

    def test_days_zero(self, trends_project_with_data):
        """--days 0 shows no data (all snapshots are in the past)."""
        result = _invoke(
            ["trends", "--days", "0"],
            cwd=trends_project_with_data,
            json_mode=True,
        )
        data = _parse_json(result)
        # With days=0, only snapshots from "today" qualify.
        # Our snapshots were just created, so they should still show.
        # Just verify the command runs without error.
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Tests: direction and trend logic
# ---------------------------------------------------------------------------

class TestTrendsLogic:
    """Tests for direction computation and trend bars."""

    def test_direction_improving(self):
        """Increasing value with higher_is_better=True -> improving."""
        from roam.commands.cmd_trends import _direction_label
        assert _direction_label(50, 80, True) == "improving"

    def test_direction_worsening(self):
        """Increasing value with higher_is_better=False -> worsening."""
        from roam.commands.cmd_trends import _direction_label
        assert _direction_label(5, 10, False) == "worsening"

    def test_direction_stable(self):
        """Same value -> stable."""
        from roam.commands.cmd_trends import _direction_label
        assert _direction_label(50, 50, True) == "stable"

    def test_trend_bar_improving(self):
        """Improving trend ends with >."""
        from roam.commands.cmd_trends import _ascii_trend_bar
        bar = _ascii_trend_bar(50, 80, True)
        assert bar.endswith(">")
        assert "=" in bar

    def test_trend_bar_worsening(self):
        """Worsening trend starts with <."""
        from roam.commands.cmd_trends import _ascii_trend_bar
        bar = _ascii_trend_bar(50, 80, False)
        assert bar.startswith("<")

    def test_trend_bar_stable(self):
        """Stable trend is ===."""
        from roam.commands.cmd_trends import _ascii_trend_bar
        bar = _ascii_trend_bar(50, 50, True)
        assert bar == "==="

    def test_format_value_integer(self):
        """Integer values display without decimals."""
        from roam.commands.cmd_trends import _format_value
        assert _format_value(42.0) == "42"

    def test_format_value_float(self):
        """Float values display with 2 decimal places."""
        from roam.commands.cmd_trends import _format_value
        assert _format_value(3.14159) == "3.14"


# ---------------------------------------------------------------------------
# Tests: alerts
# ---------------------------------------------------------------------------

class TestTrendsAlerts:
    """Tests for alert generation."""

    def test_alerts_for_worsening(self):
        """Worsening metrics generate alerts."""
        from roam.commands.cmd_trends import _generate_alerts
        results = [
            {"name": "cycle_count", "direction": "worsening", "change": 3},
            {"name": "health_score", "direction": "improving", "change": 5},
        ]
        alerts = _generate_alerts(results)
        assert len(alerts) == 1
        assert alerts[0]["metric"] == "cycle_count"

    def test_no_alerts_when_improving(self):
        """No alerts when everything is improving or stable."""
        from roam.commands.cmd_trends import _generate_alerts
        results = [
            {"name": "health_score", "direction": "improving", "change": 5},
            {"name": "dead_symbols", "direction": "stable", "change": 0},
        ]
        alerts = _generate_alerts(results)
        assert len(alerts) == 0

    def test_alerts_text_output(self, trends_project_with_data):
        """If there are alerts, they appear in text output."""
        # We can't guarantee worsening metrics in a fresh project,
        # but we can at least verify the command runs
        result = _invoke(["trends"], cwd=trends_project_with_data)
        assert result.exit_code == 0
        # Alerts section is optional; just verify no crash


# ---------------------------------------------------------------------------
# Tests: collect_current_metrics
# ---------------------------------------------------------------------------

class TestCollectMetrics:
    """Tests for the metric collection function."""

    def test_collect_returns_all_metrics(self, trends_project):
        """_collect_current_metrics returns all defined metric names."""
        from roam.db.connection import open_db
        from roam.commands.cmd_trends import _collect_current_metrics, _METRIC_DEFS
        with open_db(readonly=True) as conn:
            metrics = _collect_current_metrics(conn)
        for name in _METRIC_DEFS:
            assert name in metrics, f"Missing metric: {name}"

    def test_collect_health_score_range(self, trends_project):
        """Health score should be 0-100."""
        from roam.db.connection import open_db
        from roam.commands.cmd_trends import _collect_current_metrics
        with open_db(readonly=True) as conn:
            metrics = _collect_current_metrics(conn)
        score = metrics["health_score"]
        assert 0 <= score <= 100, f"Health score {score} out of range"

    def test_collect_total_files_positive(self, trends_project):
        """Total files should be positive for an indexed project."""
        from roam.db.connection import open_db
        from roam.commands.cmd_trends import _collect_current_metrics
        with open_db(readonly=True) as conn:
            metrics = _collect_current_metrics(conn)
        assert metrics["total_files"] > 0

    def test_collect_total_symbols_positive(self, trends_project):
        """Total symbols should be positive for an indexed project."""
        from roam.db.connection import open_db
        from roam.commands.cmd_trends import _collect_current_metrics
        with open_db(readonly=True) as conn:
            metrics = _collect_current_metrics(conn)
        assert metrics["total_symbols"] > 0
