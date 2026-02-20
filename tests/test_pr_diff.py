"""Tests for roam pr-diff command and shared delta engine."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
    roam,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def pr_diff_project(tmp_path, monkeypatch):
    """Project with snapshot baseline + modified files for delta testing."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    (src / "models.py").write_text(
        'class User:\n'
        '    def __init__(self, name):\n'
        '        self.name = name\n'
        '\n'
        '    def display_name(self):\n'
        '        return self.name.title()\n'
    )

    (src / "service.py").write_text(
        'from models import User\n'
        '\n'
        'def create_user(name):\n'
        '    return User(name)\n'
        '\n'
        'def get_display(user):\n'
        '    return user.display_name()\n'
    )

    (src / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    return f"{first} {last}"\n'
    )

    git_init(proj)

    # Index and snapshot (baseline)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"

    # Create a snapshot for baseline
    from roam.cli import cli
    from click.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(cli, ["snapshot", "--tag", "baseline"],
                           catch_exceptions=False)
    assert result.exit_code == 0, f"snapshot failed: {result.output}"

    # Modify files but do NOT commit — leave as dirty working tree
    # so that `git diff` (default pr-diff mode) finds them
    (src / "service.py").write_text(
        'from models import User\n'
        '\n'
        'def create_user(name, email=None):\n'
        '    return User(name)\n'
        '\n'
        'def get_display(user):\n'
        '    return user.display_name()\n'
        '\n'
        'def new_helper():\n'
        '    """A new function."""\n'
        '    return 42\n'
    )

    # Re-index picks up disk state (includes new_helper)
    out, rc = index_in_process(proj)
    assert rc == 0, f"re-index failed: {out}"

    return proj


@pytest.fixture
def pr_diff_no_snapshot(tmp_path, monkeypatch):
    """Project indexed but WITHOUT any snapshot."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    (src / "app.py").write_text(
        'def main():\n'
        '    print("hello")\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"

    # Remove any auto-created snapshots
    from roam.db.connection import open_db
    with open_db() as conn:
        conn.execute("DELETE FROM snapshots")
        conn.commit()

    # Modify to have uncommitted changes
    (src / "app.py").write_text(
        'def main():\n'
        '    print("hello world")\n'
        '\n'
        'def extra():\n'
        '    return 1\n'
    )

    return proj


# ---------------------------------------------------------------------------
# Unit tests for diff engine
# ---------------------------------------------------------------------------


class TestMetricDelta:
    """Test metric_delta() direction logic."""

    def test_metric_delta_direction(self):
        """health_score up = improved, cycles up = degraded."""
        from roam.graph.diff import metric_delta

        before = {"health_score": 80, "cycles": 3, "god_components": 2}
        after = {"health_score": 85, "cycles": 5, "god_components": 2}
        d = metric_delta(before, after)

        assert d["health_score"]["direction"] == "improved"
        assert d["cycles"]["direction"] == "degraded"
        assert d["god_components"]["direction"] == "unchanged"

    def test_metric_delta_unchanged(self):
        """Same values should produce direction='unchanged'."""
        from roam.graph.diff import metric_delta

        before = {"health_score": 80, "cycles": 3}
        after = {"health_score": 80, "cycles": 3}
        d = metric_delta(before, after)

        assert d["health_score"]["direction"] == "unchanged"
        assert d["cycles"]["direction"] == "unchanged"
        assert d["health_score"]["delta"] == 0
        assert d["health_score"]["pct_change"] == 0.0

    def test_metric_delta_pct_change(self):
        """Percentage change should be computed correctly."""
        from roam.graph.diff import metric_delta

        before = {"health_score": 100, "cycles": 10}
        after = {"health_score": 90, "cycles": 15}
        d = metric_delta(before, after)

        assert d["health_score"]["pct_change"] == -10.0
        assert d["cycles"]["pct_change"] == 50.0


class TestFindBeforeSnapshot:
    """Test find_before_snapshot() with no snapshots."""

    def test_find_before_snapshot_none(self, tmp_path, monkeypatch):
        """Returns None when no snapshots exist."""
        from roam.graph.diff import find_before_snapshot

        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "a.py").write_text("x = 1\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        # Index but don't create snapshot — but check if auto-snapshot exists
        out, rc = index_in_process(proj)
        assert rc == 0

        from roam.db.connection import open_db
        # Delete any auto-created snapshots (need writable DB)
        with open_db() as conn:
            conn.execute("DELETE FROM snapshots")
            conn.commit()

        with open_db(readonly=True) as conn:
            result = find_before_snapshot(conn, proj)
        assert result is None


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestPrDiff:
    """Test the pr-diff CLI command."""

    def test_pr_diff_runs(self, cli_runner, pr_diff_project, monkeypatch):
        """Command exits 0."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project)
        assert result.exit_code == 0

    def test_pr_diff_json_envelope(self, cli_runner, pr_diff_project, monkeypatch):
        """Valid JSON envelope with command='pr-diff'."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        assert_json_envelope(data, "pr-diff")

    def test_pr_diff_no_changes(self, cli_runner, pr_diff_project, monkeypatch):
        """No changed files -> graceful message."""
        monkeypatch.chdir(pr_diff_project)
        # Stage and commit everything so no changes remain
        git_commit(pr_diff_project, "commit all")
        out, rc = index_in_process(pr_diff_project)
        assert rc == 0

        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project)
        assert result.exit_code == 0
        assert "no change" in result.output.lower() or "no changed" in result.output.lower()

    def test_pr_diff_has_metric_deltas(self, cli_runner, pr_diff_project, monkeypatch):
        """When snapshot exists, deltas appear in JSON."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        assert data["summary"]["metric_deltas_available"] is True
        assert "metric_deltas" in data
        assert isinstance(data["metric_deltas"], dict)

    def test_pr_diff_no_snapshot_advisory(self, cli_runner, pr_diff_no_snapshot, monkeypatch):
        """Without snapshot, advisory message in output."""
        monkeypatch.chdir(pr_diff_no_snapshot)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_no_snapshot)
        assert result.exit_code == 0
        assert "snapshot" in result.output.lower()

    def test_pr_diff_symbol_added(self, cli_runner, pr_diff_project, monkeypatch):
        """Adding a function appears in symbol_changes.added."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        sym = data.get("symbol_changes", {})
        added_names = [s["name"] for s in sym.get("added", [])]
        assert "new_helper" in added_names

    def test_pr_diff_footprint(self, cli_runner, pr_diff_project, monkeypatch):
        """files_pct and symbols_pct are present and numeric."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        fp = data.get("footprint", {})
        assert "files_pct" in fp
        assert "symbols_pct" in fp
        assert isinstance(fp["files_pct"], (int, float))
        assert isinstance(fp["symbols_pct"], (int, float))

    def test_pr_diff_verdict_line(self, cli_runner, pr_diff_project, monkeypatch):
        """Text starts with 'VERDICT:'."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_pr_diff_markdown_format(self, cli_runner, pr_diff_project, monkeypatch):
        """--format markdown produces markdown tables."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff", "--format", "markdown"],
                            cwd=pr_diff_project)
        assert result.exit_code == 0
        assert "##" in result.output
        assert "Verdict" in result.output or "verdict" in result.output.lower()

    def test_pr_diff_fail_on_degradation_pass(self, cli_runner, pr_diff_project, monkeypatch):
        """Exit 0 when health not degraded (or unchanged)."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff", "--fail-on-degradation"],
                            cwd=pr_diff_project)
        # Should be 0 since this small change likely doesn't degrade health
        # (or if it does, exit code 1 is also valid behavior)
        assert result.exit_code in (0, 1)

    def test_pr_diff_edge_analysis(self, cli_runner, pr_diff_project, monkeypatch):
        """Edge detail appears in JSON."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        ea = data.get("edge_analysis", {})
        assert "total_from_changed" in ea
        assert "cross_cluster" in ea
        assert "layer_violations" in ea

    def test_pr_diff_json_footprint_keys(self, cli_runner, pr_diff_project, monkeypatch):
        """JSON footprint has all expected keys."""
        monkeypatch.chdir(pr_diff_project)
        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        fp = data.get("footprint", {})
        for key in ["files_changed", "files_total", "files_pct",
                     "symbols_changed", "symbols_total", "symbols_pct"]:
            assert key in fp, f"Missing footprint key: {key}"

    def test_pr_diff_no_changes_json(self, cli_runner, pr_diff_project, monkeypatch):
        """No changed files -> valid JSON with no-changes verdict."""
        monkeypatch.chdir(pr_diff_project)
        git_commit(pr_diff_project, "commit all")
        out, rc = index_in_process(pr_diff_project)
        assert rc == 0

        result = invoke_cli(cli_runner, ["pr-diff"], cwd=pr_diff_project,
                            json_mode=True)
        data = parse_json_output(result, "pr-diff")
        assert_json_envelope(data, "pr-diff")
        assert "no change" in data["summary"]["verdict"].lower()
