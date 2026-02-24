"""Tests for the roam forecast command.

Covers:
- Basic invocation (exit 0)
- JSON envelope structure
- VERDICT line in text output
- aggregate_trends list present
- at_risk_symbols list present
- Field presence for symbols and trends
- Graceful handling of no snapshot history
- --alert-only flag filtering
- --symbol flag filtering
- --horizon parameter acceptance
- Summary field counts
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
# The forecast command is not yet registered in cli.py (done separately).
# We build a minimal Click group here so tests can invoke it in-process.

def _make_local_cli():
    """Return a Click group containing only the forecast command."""
    from roam.commands.cmd_forecast import forecast

    @click.group()
    @click.option("--json", "json_out", is_flag=True)
    @click.pass_context
    def _local_cli(ctx, json_out):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_out

    _local_cli.add_command(forecast)
    return _local_cli


_LOCAL_CLI = _make_local_cli()


def _invoke(args, cwd=None, json_mode=False):
    """Invoke the forecast command via the local CLI shim."""
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

def _parse_json(result, cmd="forecast"):
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


def _assert_envelope(data, cmd="forecast"):
    """Verify standard roam JSON envelope keys."""
    assert isinstance(data, dict)
    assert data.get("command") == cmd, f"Expected command={cmd}, got {data.get('command')}"
    assert "version" in data
    assert "timestamp" in data.get("_meta", data)
    assert "summary" in data
    assert isinstance(data["summary"], dict)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def forecast_project(tmp_path, monkeypatch):
    """Project with multiple snapshots for trend analysis.

    Creates an initial index (1 snapshot) and then 3 additional snapshots
    by adding files and re-indexing, giving 4 snapshots total for Theil-Sen
    (which requires n >= 4 data points).
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # A function with non-trivial cognitive complexity
    (proj / "service.py").write_text(
        'def process(data):\n'
        '    if data.get("a"):\n'
        '        if data.get("b"):\n'
        '            if data.get("c"):\n'
        '                return "complex"\n'
        '    return "simple"\n\n'
        'def helper():\n'
        '    return 42\n'
    )
    (proj / "utils.py").write_text(
        'def add(a, b):\n'
        '    return a + b\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"initial index failed: {out}"

    # Create additional snapshots by modifying and re-indexing
    for i in range(3):
        (proj / f"extra_{i}.py").write_text(
            f'def func_{i}(x):\n'
            f'    if x > {i}:\n'
            f'        return x * {i + 1}\n'
            f'    return {i}\n'
        )
        git_commit(proj, f"add extra_{i}")
        out, rc = index_in_process(proj)
        assert rc == 0, f"re-index {i} failed: {out}"

    return proj


@pytest.fixture
def forecast_no_snapshots(tmp_path, monkeypatch):
    """Project with no snapshot history (snapshots table is empty)."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text('def main():\n    return 1\n')
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    # Delete auto-created snapshot so we can test the empty-history path
    from roam.db.connection import open_db
    with open_db() as conn:
        conn.execute("DELETE FROM snapshots")
        conn.commit()
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestForecastCommand:
    """Integration tests for roam forecast."""

    def test_forecast_runs(self, forecast_project):
        """Command exits with code 0."""
        result = _invoke(["forecast"], cwd=forecast_project)
        assert result.exit_code == 0, (
            f"forecast exited {result.exit_code}:\n{result.output}"
        )

    def test_forecast_json_envelope(self, forecast_project):
        """JSON output follows the standard roam envelope contract."""
        result = _invoke(["forecast"], cwd=forecast_project, json_mode=True)
        data = _parse_json(result)
        _assert_envelope(data)

    def test_forecast_verdict_line(self, forecast_project):
        """Text output starts with a VERDICT: line."""
        result = _invoke(["forecast"], cwd=forecast_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:"), (
            f"Expected first line to start with 'VERDICT:', got: {first_line!r}"
        )

    def test_forecast_has_aggregate_trends(self, forecast_project):
        """JSON envelope contains an aggregate_trends list."""
        result = _invoke(["forecast"], cwd=forecast_project, json_mode=True)
        data = _parse_json(result)
        assert "aggregate_trends" in data, (
            "Expected 'aggregate_trends' key in JSON output"
        )
        assert isinstance(data["aggregate_trends"], list), (
            "aggregate_trends should be a list"
        )

    def test_forecast_has_at_risk_symbols(self, forecast_project):
        """JSON envelope contains an at_risk_symbols list."""
        result = _invoke(["forecast"], cwd=forecast_project, json_mode=True)
        data = _parse_json(result)
        assert "at_risk_symbols" in data, (
            "Expected 'at_risk_symbols' key in JSON output"
        )
        assert isinstance(data["at_risk_symbols"], list), (
            "at_risk_symbols should be a list"
        )

    def test_forecast_symbol_has_fields(self, forecast_project):
        """Each at_risk_symbol entry has the required fields."""
        result = _invoke(
            ["forecast", "--min-slope", "0.0"],
            cwd=forecast_project,
            json_mode=True,
        )
        data = _parse_json(result)
        symbols = data.get("at_risk_symbols", [])
        if not symbols:
            pytest.skip("No at-risk symbols in this project -- skipping field check")
        required = {"name", "file", "cognitive_complexity", "risk_score"}
        for sym in symbols:
            missing = required - set(sym.keys())
            assert not missing, (
                f"Symbol {sym.get('name')} missing fields: {missing}"
            )

    def test_forecast_aggregate_has_fields(self, forecast_project):
        """Each aggregate_trends entry has the required fields."""
        result = _invoke(["forecast"], cwd=forecast_project, json_mode=True)
        data = _parse_json(result)
        trends = data.get("aggregate_trends", [])
        if not trends:
            pytest.skip("No aggregate trends in this project -- skipping field check")
        required = {"metric", "current", "slope", "status"}
        for trend in trends:
            missing = required - set(trend.keys())
            assert not missing, (
                f"Trend {trend.get('metric')} missing fields: {missing}"
            )

    def test_forecast_no_snapshots(self, forecast_no_snapshots):
        """With no snapshot history the command still exits 0 and emits a
        graceful message about insufficient history."""
        result = _invoke(["forecast"], cwd=forecast_no_snapshots)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )
        output = result.output.lower()
        # Should mention the shortage of snapshots
        assert "snapshot" in output, (
            "Expected output to mention 'snapshot' when history is empty"
        )

    def test_forecast_no_snapshots_json(self, forecast_no_snapshots):
        """With no snapshot history, JSON output still has valid envelope and
        snapshots_available == 0."""
        result = _invoke(["forecast"], cwd=forecast_no_snapshots, json_mode=True)
        data = _parse_json(result)
        _assert_envelope(data)
        assert data["summary"]["snapshots_available"] == 0, (
            "Expected snapshots_available=0 when no snapshots exist"
        )
        assert data["aggregate_trends"] == [], (
            "Expected empty aggregate_trends when no snapshot history"
        )

    def test_forecast_alert_only(self, forecast_project):
        """--alert-only flag is accepted and filters out stable metrics."""
        result = _invoke(
            ["forecast", "--alert-only"],
            cwd=forecast_project,
            json_mode=True,
        )
        data = _parse_json(result)
        _assert_envelope(data)
        trends = data.get("aggregate_trends", [])
        # All returned trends must have a non-stable status
        stable_trends = [t for t in trends if t.get("status") == "stable"]
        assert not stable_trends, (
            f"--alert-only should suppress stable trends; got: {stable_trends}"
        )

    def test_forecast_symbol_filter(self, forecast_project):
        """--symbol with a non-existent name returns empty at_risk_symbols."""
        result = _invoke(
            ["forecast", "--symbol", "XYZZY_NONEXISTENT_ZZZ9999"],
            cwd=forecast_project,
            json_mode=True,
        )
        data = _parse_json(result)
        symbols = data.get("at_risk_symbols", [])
        assert symbols == [], (
            "Expected no symbols when filtering by a name that does not exist"
        )

    def test_forecast_symbol_filter_match(self, forecast_project):
        """--symbol with a matching name returns only matching symbols."""
        # 'process' is defined in service.py and has notable complexity
        result = _invoke(
            ["forecast", "--symbol", "process", "--min-slope", "0.0"],
            cwd=forecast_project,
            json_mode=True,
        )
        data = _parse_json(result)
        symbols = data.get("at_risk_symbols", [])
        for sym in symbols:
            name = (sym.get("name") or "").lower()
            qname = (sym.get("qualified_name") or "").lower()
            assert "process" in name or "process" in qname, (
                f"Symbol {sym} does not match filter 'process'"
            )

    def test_forecast_horizon(self, forecast_project):
        """--horizon parameter is accepted and reflected in aggregate trends."""
        result = _invoke(
            ["forecast", "--horizon", "50"],
            cwd=forecast_project,
            json_mode=True,
        )
        data = _parse_json(result)
        assert result.exit_code == 0
        trends = data.get("aggregate_trends", [])
        for t in trends:
            assert t.get("forecast_horizon") == 50, (
                f"Expected forecast_horizon=50 in trend {t}"
            )

    def test_forecast_summary_counts(self, forecast_project):
        """Summary contains snapshots_available, metrics_trending, symbols_at_risk."""
        result = _invoke(["forecast"], cwd=forecast_project, json_mode=True)
        data = _parse_json(result)
        summary = data["summary"]
        required = {"snapshots_available", "metrics_trending", "symbols_at_risk"}
        missing = required - set(summary.keys())
        assert not missing, f"Summary missing keys: {missing}"
        assert isinstance(summary["snapshots_available"], int)
        assert isinstance(summary["metrics_trending"], int)
        assert isinstance(summary["symbols_at_risk"], int)
        # Snapshot count should match what we created in the fixture (4)
        assert summary["snapshots_available"] >= 4, (
            f"Expected >= 4 snapshots, got {summary['snapshots_available']}"
        )
