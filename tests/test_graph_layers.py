"""Behavioral tests for roam.graph.layers — detect_layers + format_layers.

Closes the W1xxx coverage gap surfaced by `roam coverage-gaps --auto-detect`
dogfooding: `detect_layers` and `format_layers` had zero direct test
references in tests/ despite a PageRank-ranked in-degree of 19 (graph/
clusters/architecture commands all consume them).

Scope of this file is intentionally tight: pure-function semantics on
small NetworkX inputs. The existing `tests/test_deterministic_output.py
::TestLayerViolationsDeterminism` covers find_violations sort-stability
via a source-grep; this file covers actual return-value contracts.
"""

from __future__ import annotations

import sqlite3

import networkx as nx
import pytest

from roam.graph.layers import detect_layers, format_layers


class TestDetectLayers:
    """Verify detect_layers contract: longest-path-from-sources on a DAG of SCCs."""

    def test_empty_graph_returns_empty_dict(self):
        assert detect_layers(nx.DiGraph()) == {}

    def test_single_node_is_layer_zero(self):
        G = nx.DiGraph()
        G.add_node(1)
        assert detect_layers(G) == {1: 0}

    def test_linear_chain_has_monotonic_layers(self):
        """1 -> 2 -> 3 -> 4 assigns each node a strictly increasing layer."""
        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3), (3, 4)])
        layers = detect_layers(G)
        assert layers[1] == 0
        assert layers[2] == 1
        assert layers[3] == 2
        assert layers[4] == 3

    def test_diamond_uses_longest_path(self):
        """1 -> 2 -> 4, 1 -> 3 -> 4: node 4 sits at layer max(pred)+1 = 2."""
        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (1, 3), (2, 4), (3, 4)])
        layers = detect_layers(G)
        assert layers[1] == 0
        assert layers[2] == 1
        assert layers[3] == 1
        assert layers[4] == 2

    def test_cycle_members_share_a_layer(self):
        """SCC {2,3} collapses to one super-node; both get the same layer."""
        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3), (3, 2), (3, 4)])
        layers = detect_layers(G)
        assert layers[1] == 0
        assert layers[2] == layers[3], "cycle members must share a layer"
        assert layers[4] > layers[2], "node after cycle must be downstream"

    def test_every_node_assigned(self):
        """All graph nodes (including isolated ones) appear in the result."""
        G = nx.DiGraph()
        G.add_edges_from([(1, 2)])
        G.add_node(99)  # isolated
        layers = detect_layers(G)
        assert set(layers.keys()) == {1, 2, 99}
        assert layers[99] == 0, "isolated node has no predecessors -> layer 0"


class TestFormatLayers:
    """Verify format_layers groups by layer and annotates with symbol metadata."""

    @pytest.fixture
    def conn_with_symbols(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT
            );
            INSERT INTO files (id, path) VALUES (1, 'src/a.py'), (2, 'src/b.py');
            INSERT INTO symbols (id, file_id, name, kind) VALUES
                (10, 1, 'alpha', 'function'),
                (11, 1, 'beta',  'function'),
                (20, 2, 'gamma', 'class');
            """
        )
        return conn

    def test_empty_layers_returns_empty_list(self, conn_with_symbols):
        assert format_layers({}, conn_with_symbols) == []

    def test_groups_by_layer_and_sorts_by_name(self, conn_with_symbols):
        layers = {10: 0, 11: 0, 20: 1}
        result = format_layers(layers, conn_with_symbols)
        assert [g["layer"] for g in result] == [0, 1]
        layer0_names = [s["name"] for s in result[0]["symbols"]]
        assert layer0_names == sorted(layer0_names) == ["alpha", "beta"]
        assert result[1]["symbols"][0]["name"] == "gamma"
        assert result[1]["symbols"][0]["kind"] == "class"
        assert result[1]["symbols"][0]["file_path"] == "src/b.py"

    def test_skips_node_ids_with_no_db_row(self, conn_with_symbols):
        """Stale graph nodes whose symbols.id is gone must not crash format_layers."""
        layers = {10: 0, 9999: 0}  # 9999 has no symbol row
        result = format_layers(layers, conn_with_symbols)
        # Only alpha (id=10) should appear; the orphan id is dropped.
        all_names = [s["name"] for g in result for s in g["symbols"]]
        assert all_names == ["alpha"]
