"""Tests for the ``roam health --baseline <ref>`` mode.

Covers:
- Baseline omission: existing health behaviour is unchanged.
- Missing snapshot: friendly DEGRADED message + clean exit.
- Synthetic snapshot: delta calculation against a seeded baseline row.
- JSON envelope: documented ``delta`` block shape.
- Text mode: shows the "Δ +N findings, M fixed, K regressed" line.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Helpers — seed synthetic snapshot rows
# ---------------------------------------------------------------------------


def _seed_snapshot(
    project_path: Path,
    *,
    git_branch: str = "main",
    git_commit: str | None = "abc1234",
    tag: str | None = None,
    timestamp: int | None = None,
    health_score: int = 80,
    cycles: int = 0,
    god_components: int = 0,
    bottlenecks: int = 0,
    dead_exports: int = 0,
    layer_violations: int = 0,
    files: int = 3,
    symbols: int = 10,
    edges: int = 5,
) -> int:
    """Insert a snapshot row directly into the DB. Returns the row id."""
    from roam.db.connection import open_db

    ts = timestamp if timestamp is not None else int(time.time()) - 3600
    with open_db() as conn:
        cur = conn.execute(
            """INSERT INTO snapshots
               (timestamp, tag, source, git_branch, git_commit,
                files, symbols, edges, cycles, god_components,
                bottlenecks, dead_exports, layer_violations, health_score,
                tangle_ratio, avg_complexity, brain_methods)
               VALUES (?, ?, 'snapshot', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts,
                tag,
                git_branch,
                git_commit,
                files,
                symbols,
                edges,
                cycles,
                god_components,
                bottlenecks,
                dead_exports,
                layer_violations,
                health_score,
                0.0,
                0.0,
                0,
            ),
        )
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_no_baseline_clean_run(cli_runner, indexed_project, monkeypatch):
    """Without --baseline, health output is unchanged.

    Sentinel: the regular VERDICT line + Health Score + Cycles section appear,
    and the new "Δ" delta marker is absent.
    """
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
    assert result.exit_code == 0, f"health failed: {result.output}"
    assert "VERDICT:" in result.output
    assert "Health Score:" in result.output
    # Baseline-mode line should NOT appear in normal runs.
    assert "Δ +" not in result.output
    assert "DEGRADED" not in result.output


def test_health_baseline_no_snapshot(cli_runner, indexed_project, monkeypatch):
    """--baseline main with no stored snapshot produces DEGRADED + clean exit."""
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project)
    assert result.exit_code == 0, f"health --baseline failed: {result.output}"
    assert "DEGRADED" in result.output
    assert "No baseline snapshot found" in result.output
    # Suggested next-step is to seed one.
    assert "roam trends --save" in result.output


def test_health_baseline_no_snapshot_json(cli_runner, indexed_project, monkeypatch):
    """--baseline + --json with no snapshot returns the documented degraded envelope."""
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "health")
    assert_json_envelope(data, "health")
    summary = data["summary"]
    assert summary["verdict"] == "DEGRADED"
    assert summary["reason"] == "no_baseline_snapshot"
    assert summary["baseline_ref"] == "main"


def test_health_baseline_with_snapshot(cli_runner, indexed_project, monkeypatch):
    """Seed a synthetic baseline snapshot and assert the delta calculation runs."""
    monkeypatch.chdir(indexed_project)
    # Seed a "pristine past": zero issues, perfect score. The current state of
    # indexed_project will have at least 1 dead export (unused_helper +
    # UNUSED_CONSTANT in conftest's python_project), so the current run should
    # report at least one new finding.
    _seed_snapshot(
        indexed_project,
        git_branch="main",
        git_commit="baseline0",
        health_score=100,
        cycles=0,
        god_components=0,
        bottlenecks=0,
        dead_exports=0,
        layer_violations=0,
    )

    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "health")
    assert_json_envelope(data, "health")
    # Verdict should be one of the documented baseline-mode verdicts.
    assert data["summary"]["verdict"] in {"OK", "REVIEW", "BAD"}
    delta = data["delta"]
    # Score regressed from 100 -> something <= 100, so health_score delta
    # should be <= 0.
    assert "score_delta" in delta
    assert delta["score_delta"]["health_score"] <= 0
    # baseline_ref + baseline_taken_at are echoed back.
    assert delta["baseline_ref"] == "main"
    assert delta["baseline_taken_at"] is not None


def test_health_baseline_json_envelope_shape(cli_runner, indexed_project, monkeypatch):
    """--json + --baseline produces the documented delta block keys."""
    monkeypatch.chdir(indexed_project)
    _seed_snapshot(
        indexed_project,
        git_branch="main",
        health_score=50,  # baseline was lower, so current is likely >= and verdict OK
        cycles=5,
        god_components=5,
        bottlenecks=5,
        dead_exports=5,
        layer_violations=5,
    )

    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "health")
    assert_json_envelope(data, "health")

    assert "delta" in data, f"Missing 'delta' block in envelope: {list(data.keys())}"
    delta = data["delta"]

    # Documented keys.
    for key in (
        "new_findings",
        "fixed_findings",
        "regressed",
        "score_delta",
        "baseline_ref",
        "baseline_taken_at",
    ):
        assert key in delta, f"Missing delta key '{key}'. Got: {list(delta.keys())}"

    # Each finding follows the documented {kind, target, severity, was, now} shape.
    for collection in ("new_findings", "fixed_findings", "regressed"):
        assert isinstance(delta[collection], list)
        for f in delta[collection]:
            for fkey in ("kind", "target", "severity", "was", "now"):
                assert fkey in f, f"Finding in {collection} missing key '{fkey}': {f}"

    # score_delta keys are snake_case metric names.
    for metric_key in ("health_score", "cycles", "god_components"):
        assert metric_key in delta["score_delta"]


def test_health_baseline_text_output(cli_runner, indexed_project, monkeypatch):
    """Text mode shows the 'Δ +N findings, M fixed' summary line."""
    monkeypatch.chdir(indexed_project)
    # Baseline with low score -> any current state should beat it; we still
    # exercise the text rendering regardless of verdict.
    _seed_snapshot(
        indexed_project,
        git_branch="main",
        health_score=40,
        cycles=10,
        god_components=10,
        bottlenecks=10,
        dead_exports=10,
        layer_violations=10,
    )

    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project)
    assert result.exit_code == 0, f"health --baseline failed: {result.output}"
    out = result.output
    # The Δ line is the documented summary marker.
    assert "Δ +" in out, f"Missing 'Δ +' marker in:\n{out}"
    assert "fixed" in out
    assert "regressed" in out
    # Verdict is one of OK / REVIEW / BAD (baseline mode).
    assert any(token in out for token in ("VERDICT: OK", "VERDICT: REVIEW", "VERDICT: BAD"))


def test_health_baseline_last_resolves_most_recent(cli_runner, indexed_project, monkeypatch):
    """--baseline last picks the most recent snapshot regardless of branch.

    Note: ``roam index`` auto-records a snapshot, so we seed two snapshots
    with timestamps clearly in the future to guarantee they outrank the
    indexer's auto-snapshot.
    """
    monkeypatch.chdir(indexed_project)
    base_ts = int(time.time()) + 1000
    # Older (but still future) snapshot on a feature branch.
    _seed_snapshot(
        indexed_project,
        git_branch="feature/foo",
        git_commit="older0",
        timestamp=base_ts,
        health_score=10,
    )
    # Newer snapshot on a different branch.
    _seed_snapshot(
        indexed_project,
        git_branch="release/v1",
        git_commit="newer0",
        timestamp=base_ts + 60,
        health_score=90,
    )

    result = invoke_cli(cli_runner, ["health", "--baseline", "last"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "health")
    delta = data["delta"]
    # `last` should resolve to the newer (release/v1) snapshot.
    assert delta["baseline_git_branch"] == "release/v1"


def test_health_baseline_auto_filesystem_root_error_falls_back(monkeypatch):
    """--baseline auto falls back to cwd only for filesystem root failures."""
    import roam.db.connection as db_connection
    from roam.commands import cmd_health

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE snapshots (timestamp INTEGER, git_branch TEXT, git_commit TEXT, health_score INTEGER)")
    conn.execute(
        "INSERT INTO snapshots (timestamp, git_branch, git_commit, health_score) VALUES (?, ?, ?, ?)",
        (123, "main", "abc1234", 90),
    )

    def _raise_oserror():
        raise OSError("cwd vanished")

    monkeypatch.setattr(db_connection, "find_project_root", _raise_oserror)
    monkeypatch.setattr(cmd_health, "_resolve_main_branch", lambda _root: "main")

    baseline = cmd_health._find_baseline_snapshot(conn, "auto")

    assert baseline is not None
    assert baseline["git_branch"] == "main"


def test_health_baseline_auto_project_root_programmer_error_propagates(monkeypatch):
    """--baseline auto must not swallow bug-class project-root failures."""
    import roam.db.connection as db_connection
    from roam.commands import cmd_health

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE snapshots (timestamp INTEGER, git_branch TEXT, git_commit TEXT, health_score INTEGER)")

    def _raise_typeerror():
        raise TypeError("bad find_project_root refactor")

    monkeypatch.setattr(db_connection, "find_project_root", _raise_typeerror)
    monkeypatch.setattr(cmd_health, "_resolve_main_branch", lambda _root: "main")

    with pytest.raises(TypeError, match="bad find_project_root refactor"):
        cmd_health._find_baseline_snapshot(conn, "auto")


def test_health_baseline_dead_exports_query_catches_only_sqlite_errors(monkeypatch, capsys):
    """Auxiliary dead-export failures degrade only for expected SQLite errors."""
    from roam.commands import cmd_health

    baseline = {
        "timestamp": int(time.time()) - 3600,
        "git_branch": "main",
        "git_commit": "abc1234",
        "health_score": 100,
        "cycles": 0,
        "god_components": 0,
        "bottlenecks": 0,
        "dead_exports": 2,
        "layer_violations": 0,
    }
    monkeypatch.setattr(cmd_health, "_find_baseline_snapshot", lambda _conn, _ref: baseline)

    class SqliteBrokenConn:
        def execute(self, _query):
            raise sqlite3.OperationalError("legacy schema")

    cmd_health._emit_baseline_diff(
        conn=SqliteBrokenConn(),
        baseline_ref="main",
        health_score=100,
        actionable_cycles=[],
        god_items=[],
        bn_items=[],
        violations=[],
        json_mode=True,
        token_budget=1200,
    )
    data = json.loads(capsys.readouterr().out)
    assert data["delta"]["score_delta"]["dead_exports"] == -2

    class RuntimeBrokenConn:
        def execute(self, _query):
            raise RuntimeError("programmer bug")

    with pytest.raises(RuntimeError, match="programmer bug"):
        cmd_health._emit_baseline_diff(
            conn=RuntimeBrokenConn(),
            baseline_ref="main",
            health_score=100,
            actionable_cycles=[],
            god_items=[],
            bn_items=[],
            violations=[],
            json_mode=True,
            token_budget=1200,
        )
