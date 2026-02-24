"""Tests for the roam bisect command (architectural blame via snapshot history).

Covers:
- Basic invocation (exit 0)
- JSON envelope structure (command="bisect")
- Presence of deltas list in JSON output
- Required fields on each delta entry
- Default metric is health_score
- Custom metric (--metric cycles)
- Direction filter: degraded (default) vs both
- Threshold filter (--threshold)
- Text output starts with VERDICT:
- Graceful handling of no snapshots
- metric_range shows first and last values
- Deltas are sorted by abs_delta descending
- Top-N truncation
"""

from __future__ import annotations

import json
import os
import sys
import time
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
# Local CLI shim (bisect is not yet wired into cli.py, so we build our own)
# ---------------------------------------------------------------------------

def _make_local_cli():
    """Return a minimal Click group containing only the bisect command."""
    from roam.commands.cmd_bisect import bisect

    @click.group()
    @click.option("--json", "json_out", is_flag=True)
    @click.pass_context
    def _local_cli(ctx, json_out):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_out

    _local_cli.add_command(bisect)
    return _local_cli


_LOCAL_CLI = _make_local_cli()


def _invoke(args, cwd=None, json_mode=False):
    """Invoke the bisect command via the local CLI shim."""
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


def _parse_json(result, cmd="bisect"):
    """Parse JSON from a CliRunner result with a helpful error on failure."""
    assert result.exit_code == 0, (
        f"{cmd} exited {result.exit_code}:\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from {cmd}: {e}\nOutput:\n{result.output[:600]}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bisect_project(tmp_path, monkeypatch):
    """Project with multiple snapshots for bisect analysis.

    Creates 3 snapshots with different file counts so that the
    health_score and files metrics vary across snapshots.
    """
    proj = tmp_path / "bisect_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Initial files
    (proj / "app.py").write_text(
        "def main():\n"
        "    return process()\n\n"
        "def process():\n"
        "    return 42\n"
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"initial index failed: {out}"

    # Create first explicit snapshot (v1)
    from roam.db.connection import open_db
    from roam.commands.metrics_history import append_snapshot
    with open_db() as conn:
        append_snapshot(conn, tag="v1", source="test")

    # Add more code and snapshot again (v2)
    (proj / "utils.py").write_text(
        "def helper_a():\n"
        "    return helper_b()\n\n"
        "def helper_b():\n"
        "    return helper_a()\n"  # creates cycle
    )
    git_commit(proj, "add utils with cycle")
    out, rc = index_in_process(proj)
    assert rc == 0, f"re-index v2 failed: {out}"
    with open_db() as conn:
        append_snapshot(conn, tag="v2", source="test")

    # Add more files and snapshot (v3)
    (proj / "extra.py").write_text(
        "def extra_func():\n"
        "    return 1\n\n"
        "def extra_func2():\n"
        "    return 2\n\n"
        "def extra_func3():\n"
        "    return 3\n"
    )
    git_commit(proj, "add extra")
    out, rc = index_in_process(proj)
    assert rc == 0, f"re-index v3 failed: {out}"
    with open_db() as conn:
        append_snapshot(conn, tag="v3", source="test")

    return proj


@pytest.fixture
def bisect_no_snapshots(tmp_path, monkeypatch):
    """Project that has been indexed but has NO snapshots at all."""
    proj = tmp_path / "no_snap_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 1\n")

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    # Remove all snapshots so we exercise the "< 2 snapshots" path
    from roam.db.connection import open_db
    with open_db() as conn:
        conn.execute("DELETE FROM snapshots")
        conn.commit()

    return proj


@pytest.fixture
def bisect_one_snapshot(tmp_path, monkeypatch):
    """Project with exactly ONE snapshot (not enough for bisect)."""
    proj = tmp_path / "one_snap_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 1\n")

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    # Keep only 1 snapshot
    from roam.db.connection import open_db
    with open_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        if count == 0:
            from roam.commands.metrics_history import append_snapshot
            append_snapshot(conn, tag="only", source="test")
        elif count > 1:
            # Trim to 1
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM snapshots ORDER BY timestamp DESC LIMIT 1"
            ).fetchall()]
            conn.execute(
                f"DELETE FROM snapshots WHERE id NOT IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()

    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBisectCommand:

    def test_bisect_runs(self, bisect_project):
        """Command exits with code 0."""
        result = _invoke(["bisect"], cwd=bisect_project)
        assert result.exit_code == 0, (
            f"bisect exited {result.exit_code}:\n{result.output}"
        )

    def test_bisect_json_envelope(self, bisect_project):
        """JSON output has standard roam envelope keys and command='bisect'."""
        result = _invoke(["bisect"], cwd=bisect_project, json_mode=True)
        data = _parse_json(result)
        assert isinstance(data, dict)
        assert data.get("command") == "bisect", (
            f"Expected command='bisect', got {data.get('command')}"
        )
        assert "version" in data
        assert "timestamp" in data.get("_meta", data)
        assert "summary" in data
        assert isinstance(data["summary"], dict)

    def test_bisect_has_deltas(self, bisect_project):
        """JSON output includes a 'deltas' list."""
        result = _invoke(
            ["bisect", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        assert "deltas" in data, f"Expected 'deltas' key in: {list(data.keys())}"
        assert isinstance(data["deltas"], list)

    def test_bisect_delta_fields(self, bisect_project):
        """Each delta entry has the required fields."""
        result = _invoke(
            ["bisect", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        deltas = data.get("deltas", [])
        if not deltas:
            pytest.skip("No deltas found — project may have identical snapshots")

        required_fields = {
            "snapshot_id", "timestamp", "tag", "git_commit",
            "before", "after", "delta", "abs_delta", "direction",
        }
        for d in deltas:
            missing = required_fields - set(d.keys())
            assert not missing, (
                f"Delta entry missing fields {missing}: {d}"
            )

    def test_bisect_default_metric(self, bisect_project):
        """Default metric is health_score (appears in summary)."""
        result = _invoke(["bisect"], cwd=bisect_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("metric") == "health_score", (
            f"Expected metric='health_score', got {summary.get('metric')}"
        )

    def test_bisect_custom_metric(self, bisect_project):
        """--metric cycles tracks the cycles metric."""
        result = _invoke(
            ["bisect", "--metric", "cycles", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("metric") == "cycles", (
            f"Expected metric='cycles', got {summary.get('metric')}"
        )

    def test_bisect_direction_degraded(self, bisect_project):
        """Default direction='degraded' only returns degraded entries."""
        result = _invoke(["bisect"], cwd=bisect_project, json_mode=True)
        data = _parse_json(result)
        deltas = data.get("deltas", [])
        for d in deltas:
            assert d["direction"] == "degraded", (
                f"Expected direction='degraded' for all entries, got {d['direction']}"
            )

    def test_bisect_direction_both(self, bisect_project):
        """--direction both returns all non-zero changes (improved + degraded)."""
        result = _invoke(
            ["bisect", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("direction_filter") == "both"
        # With 3 snapshots (3 deltas total), direction=both should return >= entries
        # than direction=degraded alone
        result_deg = _invoke(["bisect"], cwd=bisect_project, json_mode=True)
        data_deg = _parse_json(result_deg)
        assert len(data["deltas"]) >= len(data_deg["deltas"]), (
            "direction=both should return at least as many deltas as direction=degraded"
        )

    def test_bisect_threshold(self, bisect_project):
        """--threshold filters out small deltas."""
        # A very large threshold should yield 0 results
        result = _invoke(
            ["bisect", "--threshold", "9999", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        deltas = data.get("deltas", [])
        assert len(deltas) == 0, (
            f"Expected 0 deltas with threshold=9999, got {len(deltas)}"
        )

    def test_bisect_verdict_line(self, bisect_project):
        """Text output begins with 'VERDICT:'."""
        result = _invoke(["bisect"], cwd=bisect_project)
        assert result.exit_code == 0
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:"), (
            f"Expected first line to start with 'VERDICT:', got: {first_line!r}"
        )

    def test_bisect_no_snapshots(self, bisect_no_snapshots):
        """Advisory message when there are no snapshots at all."""
        result = _invoke(["bisect"], cwd=bisect_no_snapshots)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Should mention needing >= 2 snapshots
        assert "snapshot" in result.output.lower()

    def test_bisect_no_snapshots_json(self, bisect_no_snapshots):
        """JSON output when no snapshots is a valid envelope with advisory verdict."""
        result = _invoke(["bisect"], cwd=bisect_no_snapshots, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "bisect"
        summary = data.get("summary", {})
        assert "verdict" in summary
        assert "snapshot" in summary["verdict"].lower()

    def test_bisect_metric_range(self, bisect_project):
        """JSON output includes metric_range with 'first' and 'last' keys."""
        result = _invoke(["bisect"], cwd=bisect_project, json_mode=True)
        data = _parse_json(result)
        assert "metric_range" in data, (
            f"Expected 'metric_range' key in: {list(data.keys())}"
        )
        metric_range = data["metric_range"]
        assert "first" in metric_range, "metric_range should have 'first' key"
        assert "last" in metric_range, "metric_range should have 'last' key"

    def test_bisect_sorted_by_impact(self, bisect_project):
        """Deltas are sorted by abs_delta in descending order."""
        result = _invoke(
            ["bisect", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        deltas = data.get("deltas", [])
        if len(deltas) < 2:
            pytest.skip("Need at least 2 deltas to verify sort order")

        abs_deltas = [d["abs_delta"] for d in deltas]
        assert abs_deltas == sorted(abs_deltas, reverse=True), (
            f"Deltas are not sorted by abs_delta descending: {abs_deltas}"
        )

    def test_bisect_top_n(self, bisect_project):
        """--top N limits output to N entries."""
        result = _invoke(
            ["bisect", "--top", "1", "--direction", "both"],
            cwd=bisect_project,
            json_mode=True,
        )
        data = _parse_json(result)
        deltas = data.get("deltas", [])
        assert len(deltas) <= 1, (
            f"Expected at most 1 delta with --top 1, got {len(deltas)}"
        )

    def test_bisect_summary_has_verdict(self, bisect_project):
        """JSON summary includes a verdict string."""
        result = _invoke(["bisect"], cwd=bisect_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert "verdict" in summary
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 0
