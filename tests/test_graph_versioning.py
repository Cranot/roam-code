"""Tests for roam/graph/versioning.py + `roam graph-diff` CLI (R23).

Covers:
1. ``snapshot_graph`` returns the full shape (symbols, edges, cycles, layers).
2. ``diff_graphs`` detects added symbols.
3. ``diff_graphs`` detects removed symbols.
4. ``diff_graphs`` detects in_degree shifts using the hybrid threshold.
5. ``diff_graphs`` detects new cycles.
6. ``diff_graphs`` detects likely moves (same name+kind across files).
7. CLI returns ``state: no_baseline_snapshot`` cleanly (NOT a crash) when
   nothing exists on disk.
8. CLI returns a diff envelope when a snapshot exists.
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def small_project(project_factory):
    """A small Python project with imports + a call chain. Indexed."""
    return project_factory(
        {
            "app.py": ("from service import handle\ndef main():\n    return handle()\n"),
            "service.py": ("from models import User\ndef handle():\n    return User().name\n"),
            "models.py": ("class User:\n    def __init__(self):\n        self.name = 'x'\n"),
        }
    )


# ---------------------------------------------------------------------------
# Unit: snapshot_graph
# ---------------------------------------------------------------------------


class TestSnapshotGraph:
    def test_snapshot_graph_returns_complete_shape(self, small_project, monkeypatch):
        monkeypatch.chdir(small_project)
        from roam.db.connection import open_db
        from roam.graph.versioning import snapshot_graph

        with open_db(readonly=True) as conn:
            snap = snapshot_graph(conn)

        assert isinstance(snap, dict)
        assert "symbols" in snap
        assert "edges" in snap
        assert "cycles" in snap
        assert "layers" in snap
        assert "metrics" in snap

        # Symbol entries carry name + kind + file + degree counts.
        assert snap["symbols"], "expected at least one symbol"
        first_key, first_meta = next(iter(snap["symbols"].items()))
        assert "name" in first_meta
        assert "kind" in first_meta
        assert "file" in first_meta
        assert "in_degree" in first_meta
        assert "out_degree" in first_meta

        # Edges are list-of-dicts with source/target/kind.
        assert isinstance(snap["edges"], list)
        if snap["edges"]:
            e = snap["edges"][0]
            assert "source" in e and "target" in e and "kind" in e

        # Metrics totals match the contents.
        assert snap["metrics"]["symbol_count"] == len(snap["symbols"])
        assert snap["metrics"]["edge_count"] == len(snap["edges"])

    def test_snapshot_graph_cycle_enrichment_degrades_on_networkx_error(self, small_project, monkeypatch):
        monkeypatch.chdir(small_project)
        import networkx as nx

        from roam.db.connection import open_db
        from roam.graph import builder as builder_module
        from roam.graph.versioning import snapshot_graph

        def fail_graph(_conn):
            raise nx.NetworkXException("synthetic cycle failure")

        monkeypatch.setattr(builder_module, "build_symbol_graph", fail_graph)

        with open_db(readonly=True) as conn:
            snap = snapshot_graph(conn)

        assert snap["cycles"] == []
        assert snap["metrics"]["cycle_count"] == 0

    def test_snapshot_graph_layer_enrichment_degrades_on_networkx_error(self, small_project, monkeypatch):
        monkeypatch.chdir(small_project)
        import networkx as nx

        from roam.db.connection import open_db
        from roam.graph import layers as layers_module
        from roam.graph.versioning import snapshot_graph

        called = False

        def fail_layers(_graph):
            nonlocal called
            called = True
            raise nx.NetworkXException("synthetic layer failure")

        monkeypatch.setattr(layers_module, "detect_layers", fail_layers)

        with open_db(readonly=True) as conn:
            snap = snapshot_graph(conn)

        assert called
        assert snap["layers"] == {}
        assert snap["metrics"]["layer_count"] == 0

    def test_snapshot_graph_cycle_enrichment_propagates_programmer_errors(self, small_project, monkeypatch):
        monkeypatch.chdir(small_project)
        from roam.db.connection import open_db
        from roam.graph import builder as builder_module
        from roam.graph.versioning import snapshot_graph

        def fail_graph(_conn):
            raise RuntimeError("synthetic programmer error")

        monkeypatch.setattr(builder_module, "build_symbol_graph", fail_graph)

        with open_db(readonly=True) as conn:
            with pytest.raises(RuntimeError, match="synthetic programmer error"):
                snapshot_graph(conn)


# ---------------------------------------------------------------------------
# Unit: diff_graphs
# ---------------------------------------------------------------------------


def _mk_snap(symbols, edges=None, cycles=None, layers=None):
    """Tiny helper: build a snapshot dict from human-friendly inputs."""
    syms = {}
    for s in symbols:
        key = f"{s['name']}::{s['kind']}::{s['file']}"
        syms[key] = {
            "name": s["name"],
            "kind": s["kind"],
            "file": s["file"],
            "qualified_name": None,
            "db_id": s.get("db_id", -1),
            "in_degree": int(s.get("in_degree", 0)),
            "out_degree": int(s.get("out_degree", 0)),
        }
    edge_list = []
    for e in edges or []:
        edge_list.append({"source": e[0], "target": e[1], "kind": e[2]})
    return {
        "symbols": syms,
        "edges": edge_list,
        "cycles": cycles or [],
        "layers": layers or {},
        "metrics": {
            "symbol_count": len(syms),
            "edge_count": len(edge_list),
            "cycle_count": len(cycles or []),
            "layer_count": (max(layers.values()) + 1) if layers else 0,
        },
    }


class TestDiffGraphs:
    def test_diff_graphs_detects_added_symbols(self):
        from roam.graph.versioning import diff_graphs

        before = _mk_snap([{"name": "a", "kind": "function", "file": "x.py"}])
        after = _mk_snap(
            [
                {"name": "a", "kind": "function", "file": "x.py"},
                {"name": "b", "kind": "function", "file": "y.py"},
            ]
        )
        d = diff_graphs(before, after)
        assert any(k.startswith("b::") for k in d.symbols_added)
        assert d.symbols_removed == []
        assert d.total_signal_count >= 1

    def test_diff_graphs_detects_removed_symbols(self):
        from roam.graph.versioning import diff_graphs

        before = _mk_snap(
            [
                {"name": "a", "kind": "function", "file": "x.py"},
                {"name": "gone", "kind": "function", "file": "z.py"},
            ]
        )
        after = _mk_snap([{"name": "a", "kind": "function", "file": "x.py"}])
        d = diff_graphs(before, after)
        assert any(k.startswith("gone::") for k in d.symbols_removed)
        assert d.symbols_added == []

    def test_diff_graphs_detects_in_degree_shifts(self):
        """Both abs (>= 2) AND relative (>= 25%) thresholds must clear."""
        from roam.graph.versioning import diff_graphs

        # busy: 40 -> 50 (delta 10, rel 25%) -> SHIFT
        # quiet: 1 -> 3 (delta 2, rel 200%)  -> SHIFT
        # noise: 100 -> 101 (delta 1, rel 1%) -> NOT a shift (fails abs >= 2)
        before = _mk_snap(
            [
                {"name": "busy", "kind": "function", "file": "x.py", "in_degree": 40},
                {"name": "quiet", "kind": "function", "file": "y.py", "in_degree": 1},
                {"name": "noise", "kind": "function", "file": "z.py", "in_degree": 100},
            ]
        )
        after = _mk_snap(
            [
                {"name": "busy", "kind": "function", "file": "x.py", "in_degree": 50},
                {"name": "quiet", "kind": "function", "file": "y.py", "in_degree": 3},
                {"name": "noise", "kind": "function", "file": "z.py", "in_degree": 101},
            ]
        )
        d = diff_graphs(before, after)
        names = {s["symbol"].split("::")[0] for s in d.in_degree_shifts}
        assert "busy" in names
        assert "quiet" in names
        assert "noise" not in names

    def test_diff_graphs_detects_new_cycles(self):
        from roam.graph.versioning import diff_graphs

        before = _mk_snap(
            [
                {"name": "a", "kind": "function", "file": "x.py"},
                {"name": "b", "kind": "function", "file": "y.py"},
            ]
        )
        a_key = "a::function::x.py"
        b_key = "b::function::y.py"
        after = _mk_snap(
            [
                {"name": "a", "kind": "function", "file": "x.py"},
                {"name": "b", "kind": "function", "file": "y.py"},
            ],
            cycles=[[a_key, b_key]],
        )
        d = diff_graphs(before, after)
        assert len(d.new_cycles) == 1
        assert set(d.new_cycles[0]) == {a_key, b_key}

    def test_diff_graphs_detects_likely_moves(self):
        """A symbol with the same name + kind disappearing from A and
        appearing in B should be flagged as a HIGH-confidence move."""
        from roam.graph.versioning import diff_graphs

        before = _mk_snap([{"name": "handler", "kind": "function", "file": "old/dir/api.py"}])
        after = _mk_snap([{"name": "handler", "kind": "function", "file": "new/dir/api.py"}])
        d = diff_graphs(before, after)
        assert len(d.likely_moves) == 1
        mv = d.likely_moves[0]
        assert mv["symbol"] == "handler"
        assert mv["kind"] == "function"
        assert mv["from_file"] == "old/dir/api.py"
        assert mv["to_file"] == "new/dir/api.py"
        assert mv["confidence"] == "high"
        # HIGH moves prune the underlying add/remove so we don't triple-count.
        assert d.symbols_added == []
        assert d.symbols_removed == []

    def test_diff_graphs_medium_confidence_when_kind_differs(self):
        from roam.graph.versioning import diff_graphs

        before = _mk_snap([{"name": "handler", "kind": "function", "file": "old.py"}])
        after = _mk_snap([{"name": "handler", "kind": "method", "file": "new.py"}])
        d = diff_graphs(before, after)
        assert len(d.likely_moves) == 1
        assert d.likely_moves[0]["confidence"] == "medium"

    def test_diff_graphs_layer_changes(self):
        from roam.graph.versioning import diff_graphs

        a_key = "a::function::x.py"
        before = _mk_snap(
            [{"name": "a", "kind": "function", "file": "x.py"}],
            layers={a_key: 0},
        )
        after = _mk_snap(
            [{"name": "a", "kind": "function", "file": "x.py"}],
            layers={a_key: 3},
        )
        d = diff_graphs(before, after)
        assert len(d.layer_changes) == 1
        assert d.layer_changes[0]["layer_before"] == 0
        assert d.layer_changes[0]["layer_after"] == 3


# ---------------------------------------------------------------------------
# CLI: no-baseline envelope
# ---------------------------------------------------------------------------


class TestGraphDiffCli:
    def test_graph_diff_command_returns_clean_envelope_with_no_baseline(self, small_project, monkeypatch):
        """No .roam/snapshots/ entries should yield state: no_baseline_snapshot,
        NEVER a crash or empty stdout."""
        monkeypatch.chdir(small_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["graph-diff"], cwd=small_project, json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "graph-diff"
        assert data["summary"]["state"] == "no_baseline_snapshot"
        assert data["summary"]["partial_success"] is True
        # Pattern 1: clean envelope means body keys exist as empty.
        assert data["symbols_added"] == []
        assert data["symbols_removed"] == []
        assert data["agent_contract"]["facts"]
        assert any("save-snapshot" in c for c in data["agent_contract"]["next_commands"])

    def test_graph_diff_with_snapshot_returns_diff_envelope(self, small_project, monkeypatch):
        monkeypatch.chdir(small_project)
        runner = CliRunner()

        # 1. Save a baseline snapshot.
        result = invoke_cli(
            runner,
            ["graph-diff", "--save-snapshot", "baseline"],
            cwd=small_project,
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        save_data = json.loads(result.output)
        assert save_data["summary"]["state"] == "ok"
        snap_path = Path(save_data["snapshot_path"])
        assert snap_path.exists()

        # 2. Diff current (== baseline) -> 0 signals, clean envelope.
        result = invoke_cli(runner, ["graph-diff"], cwd=small_project, json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "graph-diff"
        assert data["summary"]["state"] == "ok"
        assert data["summary"]["total_signals"] == 0
        assert data["summary"]["partial_success"] is False
        assert data["baseline_label"] == "baseline"
        assert data["head_label"] == "(current)"
        # Verdict survives compression (LAW 6) — a string we can read on its own.
        assert isinstance(data["summary"]["verdict"], str)
        assert data["summary"]["verdict"]

    def test_graph_diff_text_mode_emits_verdict_first(self, small_project, monkeypatch):
        monkeypatch.chdir(small_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["graph-diff"], cwd=small_project)
        assert result.exit_code == 0
        # No baseline yet — verdict line must come first regardless.
        assert result.output.startswith("VERDICT:")

    def test_graph_diff_help_renders(self):
        runner = CliRunner()
        from roam.cli import cli

        result = runner.invoke(cli, ["graph-diff", "--help"])
        assert result.exit_code == 0
        # Imperative voice for tool description (LAW 2).
        assert "Structural diff" in result.output or "graph-diff" in result.output.lower()
