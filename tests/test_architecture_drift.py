"""Tests for `roam architecture-drift` (R23 companion to graph-diff).

Covers:
1. ``insufficient_snapshots`` when 0 or 1 snapshots exist.
2. Three snapshots in the window produce a trend with metrics + direction.
3. ``directional: improving`` when cycles decrease across snapshots.
4. ``directional: degrading`` when cycles increase across snapshots.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli

# ---------------------------------------------------------------------------
# Fixture: minimal indexed project
# ---------------------------------------------------------------------------


@pytest.fixture
def drift_project(project_factory):
    return project_factory(
        {
            "a.py": "def alpha(): pass\n",
            "b.py": "from a import alpha\ndef beta():\n    return alpha()\n",
        }
    )


def _write_synthetic_snapshot(
    root: Path,
    label: str,
    *,
    symbols=None,
    edges=None,
    cycles=None,
    layers=None,
    age_offset_s: int = 0,
):
    """Write a hand-built snapshot dict to .roam/snapshots/ then back-date its mtime.

    ``age_offset_s`` is **subtracted** from "now", so larger numbers = older.
    """
    import os
    import time

    from roam.graph.versioning import snapshot_dir, write_snapshot

    snap = {
        "symbols": symbols or {},
        "edges": edges or [],
        "cycles": cycles or [],
        "layers": layers or {},
        "metrics": {
            "symbol_count": len(symbols or {}),
            "edge_count": len(edges or []),
            "cycle_count": len(cycles or []),
            "layer_count": (max((layers or {}).values()) + 1) if layers else 0,
        },
    }
    path = write_snapshot(root, snap, label=label)
    # Back-date the mtime so the window filter behaves predictably.
    target = time.time() - age_offset_s
    os.utime(path, (target, target))
    # Confirm snapshot_dir resolves cleanly (smoke).
    assert snapshot_dir(root).exists()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestArchitectureDrift:
    def test_drift_insufficient_snapshots(self, drift_project, monkeypatch):
        """With zero snapshots the command must NOT crash. It emits a clean
        ``state: insufficient_snapshots`` envelope (Pattern 1 + 2)."""
        monkeypatch.chdir(drift_project)
        runner = CliRunner()
        result = invoke_cli(
            runner, ["architecture-drift"], cwd=drift_project, json_mode=True
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "architecture-drift"
        assert data["summary"]["state"] == "insufficient_snapshots"
        assert data["summary"]["partial_success"] is True
        assert data["summary"]["directional"] == "unknown"

    def test_drift_with_3_snapshots_reports_trend(self, drift_project, monkeypatch):
        monkeypatch.chdir(drift_project)
        # Three snapshots, oldest -> newest. Same symbols, growing edges.
        a_key = "a::function::a.py"
        b_key = "b::function::b.py"
        c_key = "c::function::c.py"
        syms = {
            a_key: {
                "name": "a",
                "kind": "function",
                "file": "a.py",
                "qualified_name": None,
                "db_id": 1,
                "in_degree": 0,
                "out_degree": 0,
            },
            b_key: {
                "name": "b",
                "kind": "function",
                "file": "b.py",
                "qualified_name": None,
                "db_id": 2,
                "in_degree": 0,
                "out_degree": 0,
            },
            c_key: {
                "name": "c",
                "kind": "function",
                "file": "c.py",
                "qualified_name": None,
                "db_id": 3,
                "in_degree": 0,
                "out_degree": 0,
            },
        }
        _write_synthetic_snapshot(
            drift_project, "s1", symbols=syms, edges=[], age_offset_s=10_800
        )
        _write_synthetic_snapshot(
            drift_project,
            "s2",
            symbols=syms,
            edges=[{"source": a_key, "target": b_key, "kind": "calls"}],
            age_offset_s=7_200,
        )
        _write_synthetic_snapshot(
            drift_project,
            "s3",
            symbols=syms,
            edges=[
                {"source": a_key, "target": b_key, "kind": "calls"},
                {"source": b_key, "target": c_key, "kind": "calls"},
            ],
            age_offset_s=3_600,
        )

        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["architecture-drift", "--window", "1d"],
            cwd=drift_project,
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["state"] == "ok"
        assert data["summary"]["snapshots_analyzed"] >= 2
        assert "metrics" in data
        assert "edges_growth_rate" in data["metrics"]
        # Two pair-diffs across three snapshots.
        assert len(data["pair_diffs"]) >= 2
        assert data["summary"]["directional"] in {"improving", "degrading", "stable"}

    def test_drift_directional_improving(self, drift_project, monkeypatch):
        """Cycles decreasing across snapshots -> direction == improving."""
        monkeypatch.chdir(drift_project)
        a_key = "a::function::a.py"
        b_key = "b::function::b.py"
        syms = {
            a_key: {
                "name": "a",
                "kind": "function",
                "file": "a.py",
                "qualified_name": None,
                "db_id": 1,
                "in_degree": 0,
                "out_degree": 0,
            },
            b_key: {
                "name": "b",
                "kind": "function",
                "file": "b.py",
                "qualified_name": None,
                "db_id": 2,
                "in_degree": 0,
                "out_degree": 0,
            },
        }
        # Oldest snapshot: 2 cycles. Middle: 1. Newest: 0.
        _write_synthetic_snapshot(
            drift_project,
            "s1",
            symbols=syms,
            cycles=[[a_key, b_key], [a_key, b_key + "#alt"]],
            age_offset_s=10_800,
        )
        _write_synthetic_snapshot(
            drift_project,
            "s2",
            symbols=syms,
            cycles=[[a_key, b_key]],
            age_offset_s=7_200,
        )
        _write_synthetic_snapshot(
            drift_project, "s3", symbols=syms, cycles=[], age_offset_s=3_600
        )

        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["architecture-drift", "--window", "1d"],
            cwd=drift_project,
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["state"] == "ok"
        # Cycles decreasing -> negative cycles_growth_rate -> improving.
        assert data["metrics"]["cycles_growth_rate"] < 0
        assert data["summary"]["directional"] == "improving"

    def test_drift_directional_degrading(self, drift_project, monkeypatch):
        """Cycles increasing across snapshots -> direction == degrading."""
        monkeypatch.chdir(drift_project)
        a_key = "a::function::a.py"
        b_key = "b::function::b.py"
        syms = {
            a_key: {
                "name": "a",
                "kind": "function",
                "file": "a.py",
                "qualified_name": None,
                "db_id": 1,
                "in_degree": 0,
                "out_degree": 0,
            },
            b_key: {
                "name": "b",
                "kind": "function",
                "file": "b.py",
                "qualified_name": None,
                "db_id": 2,
                "in_degree": 0,
                "out_degree": 0,
            },
        }
        # Cycles climbing 0 -> 1 -> 2.
        _write_synthetic_snapshot(
            drift_project, "s1", symbols=syms, cycles=[], age_offset_s=10_800
        )
        _write_synthetic_snapshot(
            drift_project,
            "s2",
            symbols=syms,
            cycles=[[a_key, b_key]],
            age_offset_s=7_200,
        )
        _write_synthetic_snapshot(
            drift_project,
            "s3",
            symbols=syms,
            cycles=[[a_key, b_key], [a_key, b_key + "#alt"]],
            age_offset_s=3_600,
        )

        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["architecture-drift", "--window", "1d"],
            cwd=drift_project,
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["state"] == "ok"
        assert data["metrics"]["cycles_growth_rate"] > 0
        assert data["summary"]["directional"] == "degrading"

    def test_drift_help_renders(self):
        runner = CliRunner()
        from roam.cli import cli

        result = runner.invoke(cli, ["architecture-drift", "--help"])
        assert result.exit_code == 0
        assert "drift" in result.output.lower() or "architecture" in result.output.lower()
