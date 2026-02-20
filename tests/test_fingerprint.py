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
from conftest import index_in_process, git_init, invoke_cli


# ---------------------------------------------------------------------------
# Fixture: small project with clusters
# ---------------------------------------------------------------------------

@pytest.fixture
def fp_project(project_factory):
    return project_factory({
        "api/routes.py": (
            "from service import handle\n"
            "def get_users(): return handle()\n"
            "def get_orders(): pass\n"
        ),
        "service.py": (
            "from models import User\n"
            "def handle(): return User()\n"
        ),
        "models.py": (
            "class User:\n"
            "    def save(self): pass\n"
        ),
        "utils.py": (
            "def format_name(n): return n.title()\n"
        ),
    })


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
# Unit tests: compare_fingerprints
# ---------------------------------------------------------------------------

class TestCompareFingerprints:

    def test_compare_fingerprints_same(self, fp_project, monkeypatch):
        """Comparing identical fingerprints should give similarity close to 1.0."""
        monkeypatch.chdir(fp_project)
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compute_fingerprint, compare_fingerprints

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
        data = json.loads(open(export_file, encoding="utf-8").read())
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
