# Tests for spectral bisection (Fiedler vector) module decomposition.
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import networkx as nx
import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process


# Graph helpers

def _two_cluster_graph():
    G = nx.Graph()
    for i in range(6):
        for j in range(i + 1, 6):
            G.add_edge(i, j)
    for i in range(6, 12):
        for j in range(i + 1, 12):
            G.add_edge(i, j)
    G.add_edge(5, 6)
    return G


def _dense_graph(n=10):
    return nx.complete_graph(n)


def _path_graph(n=20):
    return nx.path_graph(n)


def _disconnected_graph():
    G = nx.Graph()
    G.add_edges_from([(0, 1), (1, 2), (2, 3), (3, 4)])
    G.add_edges_from([(10, 11), (11, 12), (12, 13), (13, 14)])
    return G


# CLI helpers

def _invoke_spectral(args, cwd, json_mode=False):
    from roam.commands.cmd_spectral import spectral
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(spectral, args, obj={"json": json_mode, "budget": 0}, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result, label="spectral"):
    assert result.exit_code == 0, f"{label} failed: {result.output[:200]}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"JSON error: {e} -- {result.output[:200]}")


# Fixtures


@pytest.fixture
def spectral_project(tmp_path, monkeypatch):
    proj = tmp_path / "spectral_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "module_a.py").write_text(
        "class Alpha:\n"
        "    def method_a1(self):\n"
        "        return self.method_a2()\n"
        "    def method_a2(self):\n"
        "        return Beta().run()\n"
        "\n"
        "class Beta:\n"
        "    def run(self):\n"
        "        return Gamma().compute()\n"
        "\n"
        "class Gamma:\n"
        "    def compute(self):\n"
        "        return 42\n"
    )
    (proj / "module_b.py").write_text(
        "class Delta:\n"
        "    def run(self):\n"
        "        return Epsilon().process()\n"
        "\n"
        "class Epsilon:\n"
        "    def process(self):\n"
        "        return Zeta().work()\n"
        "\n"
        "class Zeta:\n"
        "    def work(self):\n"
        "        return 99\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def empty_project(tmp_path, monkeypatch):
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "minimal.py").write_text("x = 1\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ===========================================================================
# Unit tests: fiedler_partition
# ===========================================================================


class TestFiedlerPartition:

    def test_two_clusters_produces_partitions(self):
        from roam.graph.spectral import fiedler_partition
        G = _two_cluster_graph()
        result = fiedler_partition(G, max_depth=3)
        assert isinstance(result, dict)
        assert len(result) == 12
        assert len(set(result.values())) >= 2

    def test_two_clusters_correct_assignment(self):
        from roam.graph.spectral import fiedler_partition
        G = _two_cluster_graph()
        result = fiedler_partition(G, max_depth=1)
        assert len(set(result.values())) == 2
        a_parts = {result[i] for i in range(6)}
        b_parts = {result[i] for i in range(6, 12)}
        assert len(a_parts) == 1
        assert len(b_parts) == 1
        assert a_parts != b_parts

    def test_empty_graph_returns_empty_dict(self):
        from roam.graph.spectral import fiedler_partition
        assert fiedler_partition(nx.Graph()) == {}

    def test_single_node_graph(self):
        from roam.graph.spectral import fiedler_partition
        G = nx.Graph()
        G.add_node(0)
        assert fiedler_partition(G) == {0: 0}

    def test_small_graph_stops_at_min_size(self):
        from roam.graph.spectral import fiedler_partition
        G = nx.path_graph(4)
        result = fiedler_partition(G, max_depth=5)
        assert len(set(result.values())) == 1

    def test_disconnected_graph_each_component_handled(self):
        from roam.graph.spectral import fiedler_partition
        G = _disconnected_graph()
        result = fiedler_partition(G)
        assert len(result) == 10
        a_ids = {result[i] for i in range(5)}
        b_ids = {result[i] for i in range(10, 15)}
        assert a_ids.isdisjoint(b_ids)

    def test_depth_limit_controls_partitions(self):
        from roam.graph.spectral import fiedler_partition
        G = _two_cluster_graph()
        r1 = fiedler_partition(G, max_depth=1)
        r3 = fiedler_partition(G, max_depth=3)
        assert len(set(r3.values())) >= len(set(r1.values()))

    def test_directed_graph_uses_undirected_projection(self):
        from roam.graph.spectral import fiedler_partition
        DG = _two_cluster_graph().to_directed()
        result = fiedler_partition(DG, max_depth=1)
        assert len(result) == 12
        assert len(set(result.values())) == 2

    def test_all_nodes_assigned(self):
        from roam.graph.spectral import fiedler_partition
        G = _path_graph(20)
        result = fiedler_partition(G, max_depth=3)
        assert set(result.keys()) == set(G.nodes())

    def test_dense_graph_handled(self):
        from roam.graph.spectral import fiedler_partition
        G = _dense_graph(10)
        result = fiedler_partition(G, max_depth=2)
        assert len(result) == 10

    def test_partition_ids_are_dense_integers(self):
        from roam.graph.spectral import fiedler_partition
        G = _two_cluster_graph()
        result = fiedler_partition(G, max_depth=2)
        ids = sorted(set(result.values()))
        assert ids == list(range(len(ids)))


class TestSpectralGap:

    def test_empty_graph_returns_zero(self):
        from roam.graph.spectral import spectral_gap
        assert spectral_gap(nx.Graph()) == 0.0

    def test_two_cluster_graph_has_low_gap(self):
        from roam.graph.spectral import spectral_gap
        G = _two_cluster_graph()
        gap = spectral_gap(G)
        assert isinstance(gap, float)
        assert gap >= 0.0
        assert gap < 0.5

    def test_complete_graph_high_gap(self):
        from roam.graph.spectral import spectral_gap
        G = nx.complete_graph(10)
        gap = spectral_gap(G)
        assert gap > 1.0

    def test_path_graph_gap_is_positive(self):
        from roam.graph.spectral import spectral_gap
        assert spectral_gap(_path_graph(10)) > 0.0

    def test_disconnected_graph_minimum_gap(self):
        from roam.graph.spectral import spectral_gap
        G = _disconnected_graph()
        gap = spectral_gap(G)
        assert isinstance(gap, float)
        assert gap >= 0.0

    def test_single_node_graph(self):
        from roam.graph.spectral import spectral_gap
        G = nx.Graph()
        G.add_node(0)
        assert spectral_gap(G) == 0.0

    def test_directed_graph_accepted(self):
        from roam.graph.spectral import spectral_gap
        G = _two_cluster_graph().to_directed()
        assert isinstance(spectral_gap(G), float)


class TestSpectralCommunities:

    def test_explicit_k_two(self):
        from roam.graph.spectral import spectral_communities
        G = _two_cluster_graph()
        result = spectral_communities(G, k=2)
        assert len(set(result.values())) == 2
        assert set(result.keys()) == set(G.nodes())

    def test_explicit_k_three(self):
        from roam.graph.spectral import spectral_communities
        G = _path_graph(20)
        result = spectral_communities(G, k=3)
        assert len(set(result.values())) <= 3
        assert set(result.keys()) == set(G.nodes())

    def test_auto_k_returns_sensible_count(self):
        from roam.graph.spectral import spectral_communities
        G = _two_cluster_graph()
        result = spectral_communities(G, k=None)
        assert len(set(result.values())) >= 2
        assert set(result.keys()) == set(G.nodes())

    def test_empty_graph_returns_empty(self):
        from roam.graph.spectral import spectral_communities
        assert spectral_communities(nx.Graph()) == {}

    def test_small_graph_uses_fallback(self):
        from roam.graph.spectral import spectral_communities
        G = nx.path_graph(3)
        result = spectral_communities(G, k=2)
        assert len(result) == 3

    def test_k_larger_than_natural_partitions(self):
        from roam.graph.spectral import spectral_communities
        G = _two_cluster_graph()
        result = spectral_communities(G, k=10)
        assert len(set(result.values())) >= 1
        assert set(result.keys()) == set(G.nodes())

    def test_all_nodes_returned(self):
        from roam.graph.spectral import spectral_communities
        G = _path_graph(15)
        result = spectral_communities(G, k=3)
        assert set(result.keys()) == set(G.nodes())


class TestVerdictFromGap:

    def test_high_gap_well_modularized(self):
        from roam.graph.spectral import verdict_from_gap
        assert verdict_from_gap(0.6) == "Well-modularized"

    def test_medium_gap_moderately_modular(self):
        from roam.graph.spectral import verdict_from_gap
        assert verdict_from_gap(0.3) == "Moderately modular"

    def test_low_gap_poorly_modularized(self):
        from roam.graph.spectral import verdict_from_gap
        assert verdict_from_gap(0.05) == "Poorly modularized"

    def test_exact_boundary_high(self):
        from roam.graph.spectral import verdict_from_gap
        assert verdict_from_gap(0.5) == "Moderately modular"

    def test_exact_boundary_med(self):
        from roam.graph.spectral import verdict_from_gap
        assert verdict_from_gap(0.1) == "Poorly modularized"

    def test_zero_gap(self):
        from roam.graph.spectral import verdict_from_gap
        assert verdict_from_gap(0.0) == "Poorly modularized"


class TestAdjustedRandIndex:

    def test_perfect_agreement(self):
        from roam.graph.spectral import adjusted_rand_index
        labels = [0, 0, 1, 1, 2, 2]
        assert adjusted_rand_index(labels, labels) == 1.0

    def test_completely_different(self):
        from roam.graph.spectral import adjusted_rand_index
        true_labels = [0, 0, 1, 1]
        pred_labels = [0, 1, 0, 1]
        ari = adjusted_rand_index(true_labels, pred_labels)
        assert isinstance(ari, float)
        assert ari <= 0.1

    def test_empty_lists(self):
        from roam.graph.spectral import adjusted_rand_index
        assert adjusted_rand_index([], []) == 1.0

    def test_single_element(self):
        from roam.graph.spectral import adjusted_rand_index
        assert adjusted_rand_index([0], [0]) == 1.0

    def test_mismatched_length_raises(self):
        from roam.graph.spectral import adjusted_rand_index
        with pytest.raises(ValueError):
            adjusted_rand_index([0, 1], [0])

    def test_all_same_cluster_both(self):
        from roam.graph.spectral import adjusted_rand_index
        labels = [0, 0, 0, 0]
        assert adjusted_rand_index(labels, labels) == 1.0

    def test_two_cluster_agreement(self):
        from roam.graph.spectral import adjusted_rand_index
        true_labels = [0, 0, 0, 1, 1, 1]
        pred_labels = [1, 1, 1, 0, 0, 0]
        ari = adjusted_rand_index(true_labels, pred_labels)
        assert abs(ari - 1.0) < 1e-5


# ===========================================================================
# Integration tests: CLI command
# ===========================================================================


class TestSpectralCommand:

    def test_basic_invocation(self, spectral_project):
        result = _invoke_spectral([], spectral_project)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"

    def test_verdict_first_line(self, spectral_project):
        result = _invoke_spectral([], spectral_project)
        assert result.exit_code == 0
        lines = result.output.strip().split(chr(10))
        assert lines[0].startswith("VERDICT:")

    def test_gap_only_flag(self, spectral_project):
        result = _invoke_spectral(["--gap-only"], spectral_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_gap_only_json(self, spectral_project):
        result = _invoke_spectral(["--gap-only"], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "spectral"
        assert "spectral_gap" in data.get("summary", {})
        assert "verdict" in data.get("summary", {})

    def test_json_output_structure(self, spectral_project):
        result = _invoke_spectral([], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "spectral"
        assert "version" in data
        assert "summary" in data
        assert isinstance(data["summary"], dict)

    def test_json_summary_verdict(self, spectral_project):
        result = _invoke_spectral([], spectral_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert "verdict" in summary
        valid = ["Well-modularized", "Moderately modular", "Poorly modularized"]
        assert summary["verdict"] in valid

    def test_json_has_partitions_list(self, spectral_project):
        result = _invoke_spectral([], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert "partitions" in data
        assert isinstance(data["partitions"], list)

    def test_json_partitions_have_required_fields(self, spectral_project):
        result = _invoke_spectral([], spectral_project, json_mode=True)
        data = _parse_json(result)
        for pt in data.get("partitions", []):
            assert "partition_id" in pt
            assert "size" in pt
            assert "sample_members" in pt

    def test_json_summary_spectral_gap(self, spectral_project):
        result = _invoke_spectral([], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert "spectral_gap" in data.get("summary", {})
        assert isinstance(data["summary"]["spectral_gap"], float)

    def test_json_summary_partitions_count(self, spectral_project):
        result = _invoke_spectral([], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert "partitions" in data.get("summary", {})
        assert isinstance(data["summary"]["partitions"], int)

    def test_depth_flag(self, spectral_project):
        result = _invoke_spectral(["--depth", "1"], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("summary", {}).get("depth") == 1

    def test_k_flag(self, spectral_project):
        result = _invoke_spectral(["--k", "2"], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert result.exit_code == 0
        assert len(data.get("partitions", [])) <= 2

    def test_compare_flag_text(self, spectral_project):
        result = _invoke_spectral(["--compare"], spectral_project)
        assert result.exit_code == 0
        assert "Louvain" in result.output or "ARI" in result.output or "Rand" in result.output

    def test_compare_flag_json(self, spectral_project):
        result = _invoke_spectral(["--compare"], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert "comparison" in data
        comp = data["comparison"]
        assert "ari" in comp
        assert "spectral_partitions" in comp
        assert "louvain_partitions" in comp

    def test_empty_project_does_not_crash(self, empty_project):
        result = _invoke_spectral([], empty_project)
        assert result.exit_code == 0

    def test_empty_project_json(self, empty_project):
        result = _invoke_spectral([], empty_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "spectral"

    def test_compare_ari_is_float(self, spectral_project):
        result = _invoke_spectral(["--compare"], spectral_project, json_mode=True)
        data = _parse_json(result)
        ari = data.get("comparison", {}).get("ari", None)
        if ari is not None:
            assert isinstance(ari, float)
            assert -1.0 <= ari <= 1.0

    def test_no_box_drawing_chars(self, spectral_project):
        result = _invoke_spectral([], spectral_project)
        assert result.exit_code == 0
        for char in [chr(0x2502), chr(0x251C), chr(0x2500)]:
            assert char not in result.output

    def test_nodes_compared_in_comparison(self, spectral_project):
        result = _invoke_spectral(["--compare"], spectral_project, json_mode=True)
        data = _parse_json(result)
        comp = data.get("comparison", {})
        if comp:
            assert "nodes_compared" in comp
            assert comp["nodes_compared"] >= 0

    def test_gap_only_summary_has_spectral_gap(self, spectral_project):
        result = _invoke_spectral(["--gap-only"], spectral_project, json_mode=True)
        data = _parse_json(result)
        assert "spectral_gap" in data.get("summary", {})

    def test_text_output_shows_partition_section(self, spectral_project):
        result = _invoke_spectral([], spectral_project)
        assert "Spectral" in result.output or "Partition" in result.output


# ===========================================================================
# Tests for internal helpers
# ===========================================================================


class TestInternalHelpers:

    def test_fiedler_split_two_nodes(self):
        from roam.graph.spectral import _fiedler_split
        G = nx.Graph()
        G.add_edge(0, 1)
        result = _fiedler_split(G)
        if result is not None:
            neg, pos = result
            assert len(neg) + len(pos) == 2

    def test_fiedler_split_single_node_returns_none(self):
        from roam.graph.spectral import _fiedler_split
        G = nx.Graph()
        G.add_node(0)
        assert _fiedler_split(G) is None

    def test_fiedler_split_disconnected_returns_none(self):
        from roam.graph.spectral import _fiedler_split
        G = nx.Graph()
        G.add_edge(0, 1)
        G.add_edge(2, 3)
        assert _fiedler_split(G) is None

    def test_louvain_fallback_returns_all_nodes(self):
        from roam.graph.spectral import _louvain_fallback
        G = nx.path_graph(5)
        result = _louvain_fallback(G)
        assert set(result.keys()) == set(G.nodes())
        for v in result.values():
            assert isinstance(v, int)

    def test_louvain_fallback_empty_graph(self):
        from roam.graph.spectral import _louvain_fallback
        assert _louvain_fallback(nx.Graph()) == {}

    def test_compute_algebraic_connectivity_trivial(self):
        from roam.graph.spectral import _compute_algebraic_connectivity
        G = nx.Graph()
        G.add_node(0)
        assert _compute_algebraic_connectivity(G) == 0.0

    def test_compute_algebraic_connectivity_disconnected(self):
        from roam.graph.spectral import _compute_algebraic_connectivity
        G = _disconnected_graph()
        assert _compute_algebraic_connectivity(G) == 0.0


# ===========================================================================
# MCP tool tests
# ===========================================================================


class TestSpectralMCP:

    def test_mcp_tool_callable(self, spectral_project, monkeypatch):
        monkeypatch.chdir(spectral_project)
        try:
            from roam.mcp_server import roam_spectral
            result = roam_spectral(root=str(spectral_project))
            assert isinstance(result, dict)
        except ImportError:
            pytest.skip("fastmcp not installed")

    def test_mcp_gap_only(self, spectral_project, monkeypatch):
        monkeypatch.chdir(spectral_project)
        try:
            from roam.mcp_server import roam_spectral
            result = roam_spectral(gap_only=True, root=str(spectral_project))
            assert isinstance(result, dict)
        except ImportError:
            pytest.skip("fastmcp not installed")
