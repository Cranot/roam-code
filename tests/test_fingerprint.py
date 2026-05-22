"""Tests for roam fingerprint -- Graph-Isomorphism Transfer (P4).

Covers:
1. compute_fingerprint returns a well-structured dict
2. compare_fingerprints handles same/different inputs
3. CLI command: text, compact, JSON, export, compare, help
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli

# ---------------------------------------------------------------------------
# Fixture: small project with clusters
# ---------------------------------------------------------------------------


@pytest.fixture
def fp_project(project_factory):
    return project_factory(
        {
            "api/routes.py": ("from service import handle\ndef get_users(): return handle()\ndef get_orders(): pass\n"),
            "service.py": ("from models import User\ndef handle(): return User()\n"),
            "models.py": ("class User:\n    def save(self): pass\n"),
            "utils.py": ("def format_name(n): return n.title()\n"),
        }
    )


# ---------------------------------------------------------------------------
# Unit tests: compute_fingerprint
# ---------------------------------------------------------------------------


class TestComputeFingerprint:
    def test_compute_fingerprint_returns_dict(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compute_fingerprint

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            fp = compute_fingerprint(conn, G)

        assert isinstance(fp, dict)
        assert "topology" in fp
        assert "clusters" in fp
        assert "hub_bridge_ratio" in fp
        assert "pagerank_gini" in fp
        assert "dependency_direction" in fp
        assert "antipatterns" in fp

    def test_fingerprint_has_topology(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compute_fingerprint

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            fp = compute_fingerprint(conn, G)

        topo = fp["topology"]
        assert "layers" in topo
        assert "layer_distribution" in topo
        assert "fiedler" in topo
        assert "modularity" in topo
        assert "tangle_ratio" in topo
        assert isinstance(topo["layers"], int)
        assert topo["layers"] >= 1

    def test_fingerprint_has_clusters(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compute_fingerprint

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            fp = compute_fingerprint(conn, G)

        assert isinstance(fp["clusters"], list)

    def test_fingerprint_pagerank_gini(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compute_fingerprint

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            fp = compute_fingerprint(conn, G)

        assert 0.0 <= fp["pagerank_gini"] <= 1.0

    def test_fingerprint_tangle_ratio(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compute_fingerprint

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            fp = compute_fingerprint(conn, G)

        assert 0.0 <= fp["topology"]["tangle_ratio"] <= 1.0


# ---------------------------------------------------------------------------
# Unit tests: _fast_cluster_quality (perf optimisation, output-identity guard)
# ---------------------------------------------------------------------------


class TestFastClusterQuality:
    """`_fast_cluster_quality` is the single-pass conductance/modularity
    helper that replaced the O(clusters * edges) `clusters.cluster_quality`
    call inside `compute_fingerprint`. It MUST stay byte-identical to the
    canonical helper — the fingerprint payload (per-cluster conductance +
    pattern classification + modularity) feeds the topology signature and
    the cross-repo comparison verdict, so any drift would silently corrupt
    the fingerprint hash.
    """

    def test_fast_cluster_quality_matches_canonical(self, fp_project, monkeypatch):
        """`_fast_cluster_quality` output is identical to `cluster_quality`."""
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.clusters import cluster_quality, detect_clusters
        from roam.graph.fingerprint import _fast_cluster_quality

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            cluster_map = detect_clusters(G)
            fast = _fast_cluster_quality(G, cluster_map)
            canonical = cluster_quality(G, cluster_map)

        # Modularity, mean conductance, and every per-cluster conductance
        # value must match exactly (same rounding, same formula).
        assert fast["modularity"] == canonical["modularity"]
        assert fast["mean_conductance"] == canonical["mean_conductance"]
        assert set(fast["per_cluster"].keys()) == set(canonical["per_cluster"].keys())
        for cid, conductance in canonical["per_cluster"].items():
            assert fast["per_cluster"][cid] == conductance, (
                f"per-cluster conductance drift for cluster {cid}: "
                f"fast={fast['per_cluster'][cid]} canonical={conductance}"
            )

    def test_fast_cluster_quality_empty_graph(self):
        """Empty graph / empty cluster map returns the zero-floor dict."""
        import networkx as nx

        from roam.graph.fingerprint import _fast_cluster_quality

        empty = _fast_cluster_quality(nx.DiGraph(), {})
        assert empty == {"modularity": 0.0, "per_cluster": {}, "mean_conductance": 0.0}

    def test_fast_cluster_quality_handles_unmapped_nodes(self):
        """A node absent from the cluster map never corrupts conductance.

        The single-pass identity (vol(S_bar) = 2*|E| - vol(S)) must hold
        even when an edge endpoint maps to no cluster — that endpoint is
        counted toward vol(S_bar) for every cluster, matching the
        canonical helper's `u in members` test being False everywhere.
        """
        import networkx as nx

        from roam.graph.clusters import cluster_quality
        from roam.graph.fingerprint import _fast_cluster_quality

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3), (3, 1), (3, 4), (4, 5), (5, 4)])
        # Node 4, 5 form a cluster; nodes 1-3 a cluster; deliberately leave
        # one node out of the map to exercise the .get() -> None path.
        cluster_map = {1: 0, 2: 0, 3: 0, 4: 1}  # node 5 unmapped
        fast = _fast_cluster_quality(G, cluster_map)
        canonical = cluster_quality(G, cluster_map)
        assert fast["modularity"] == canonical["modularity"]
        assert fast["per_cluster"] == canonical["per_cluster"]


# ---------------------------------------------------------------------------
# Unit tests: compare_fingerprints
# ---------------------------------------------------------------------------


class TestCompareFingerprints:
    def test_compare_fingerprints_same(self, fp_project, monkeypatch):
        """Comparing identical fingerprints should give similarity close to 1.0."""
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compare_fingerprints, compute_fingerprint

        with open_db(readonly=True) as conn:
            G = build_symbol_graph(conn)
            fp = compute_fingerprint(conn, G)

        result = compare_fingerprints(fp, fp)
        assert result["similarity"] >= 0.99
        assert result["euclidean_distance"] < 0.01

    def test_compare_fingerprints_different(self):
        """Comparing very different fingerprints should give lower similarity."""
        from roam.graph.fingerprint import compare_fingerprints

        fp1 = {
            "topology": {
                "layers": 5,
                "modularity": 0.8,
                "fiedler": 0.5,
                "tangle_ratio": 0.0,
            },
            "hub_bridge_ratio": 0.1,
            "pagerank_gini": 0.2,
            "dependency_direction": "top-down",
            "antipatterns": {"god_objects": 0, "cyclic_clusters": 0},
        }
        fp2 = {
            "topology": {
                "layers": 15,
                "modularity": 0.1,
                "fiedler": 0.001,
                "tangle_ratio": 0.8,
            },
            "hub_bridge_ratio": 0.6,
            "pagerank_gini": 0.9,
            "dependency_direction": "bottom-up",
            "antipatterns": {"god_objects": 20, "cyclic_clusters": 10},
        }
        result = compare_fingerprints(fp1, fp2)
        assert result["similarity"] < 0.8
        assert result["euclidean_distance"] > 0.0
        assert result["direction_match"] is False


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLIFingerprint:
    def test_cli_fingerprint_runs(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["fingerprint"], cwd=fp_project)
        assert result.exit_code == 0

    def test_cli_fingerprint_compact(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["fingerprint", "--compact"], cwd=fp_project)
        assert result.exit_code == 0
        assert "fingerprint" in result.output
        assert "layers=" in result.output
        assert "mod=" in result.output

    def test_cli_fingerprint_json(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["fingerprint"], cwd=fp_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "fingerprint"
        assert "summary" in data
        assert "fingerprint" in data
        assert "verdict" in data["summary"]

    def test_cli_fingerprint_export(self, fp_project, monkeypatch, tmp_path):
        monkeypatch.chdir(fp_project)
        export_file = str(tmp_path / "fp.json")
        runner = CliRunner()
        result = invoke_cli(runner, ["fingerprint", "--export", export_file], cwd=fp_project)
        assert result.exit_code == 0
        assert os.path.exists(export_file)
        with open(export_file, encoding="utf-8") as _fh:
            data = json.loads(_fh.read())
        assert "topology" in data

    def test_cli_fingerprint_compare(self, fp_project, monkeypatch, tmp_path):
        monkeypatch.chdir(fp_project)
        # First, export a fingerprint
        export_file = str(tmp_path / "fp_compare.json")
        runner = CliRunner()
        result = invoke_cli(runner, ["fingerprint", "--export", export_file], cwd=fp_project)
        assert result.exit_code == 0

        # Then compare with it
        result = invoke_cli(runner, ["fingerprint", "--compare", export_file], cwd=fp_project)
        assert result.exit_code == 0
        assert "similar" in result.output.lower() or "COMPARISON" in result.output

    def test_fingerprint_verdict_line(self, fp_project, monkeypatch):
        monkeypatch.chdir(fp_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["fingerprint"], cwd=fp_project)
        assert result.exit_code == 0
        assert result.output.startswith("VERDICT:")

    def test_fingerprint_help(self):
        runner = CliRunner()
        from roam.cli import cli

        result = runner.invoke(cli, ["fingerprint", "--help"])
        assert result.exit_code == 0
        assert "fingerprint" in result.output.lower() or "topology" in result.output.lower()


# ---------------------------------------------------------------------------
# DOG.3 — large-graph thresholds
# ---------------------------------------------------------------------------


class TestFingerprintScale:
    """The pre-v12 5,000-symbol cap rejected every realistic codebase.

    The new behaviour:
    * Up to ``_WARN_THRESHOLD_SYMBOLS`` — runs silently.
    * Between WARN and HARD — emits a stderr note, still runs.
    * Above ``_HARD_CAP_SYMBOLS`` — refuses with an actionable message.
    """

    def test_thresholds_are_sane(self):
        from roam.commands.cmd_fingerprint import (
            _HARD_CAP_SYMBOLS,
            _WARN_THRESHOLD_SYMBOLS,
        )

        assert _WARN_THRESHOLD_SYMBOLS < _HARD_CAP_SYMBOLS
        # The pre-v12 cap was 5,000 — these floors keep us from regressing.
        assert _WARN_THRESHOLD_SYMBOLS >= 10_000
        assert _HARD_CAP_SYMBOLS >= 50_000

    def test_legacy_constant_removed(self):
        """`_MAX_GRAPH_SYMBOLS` was the v11 constant — must not return."""
        from roam.commands import cmd_fingerprint

        assert not hasattr(cmd_fingerprint, "_MAX_GRAPH_SYMBOLS"), (
            "Old _MAX_GRAPH_SYMBOLS constant should not exist; use _WARN_THRESHOLD_SYMBOLS / _HARD_CAP_SYMBOLS instead."
        )
