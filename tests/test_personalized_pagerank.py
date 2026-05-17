"""Tests for A.PG — personalized PageRank in graph/pagerank.py.

Used by the retrieve reranker (A.1) to bias ranking toward query-relevant
seed nodes. Without seeds the function must reproduce global PageRank;
with seeds the seeded nodes must capture more rank than equivalent
non-seeded peers in the same structural position.
"""

from __future__ import annotations

import networkx as nx
import pytest

from roam.graph.pagerank import compute_pagerank, personalized_pagerank


def _star(center: int, leaves: list[int]) -> nx.DiGraph:
    """Star graph: every leaf points at the center."""
    G = nx.DiGraph()
    G.add_node(center)
    for leaf in leaves:
        G.add_node(leaf)
        G.add_edge(leaf, center)
    return G


def _line(nodes: list[int]) -> nx.DiGraph:
    """Linear chain: 1 → 2 → 3 → 4 → ..."""
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n)
    for a, b in zip(nodes, nodes[1:]):
        G.add_edge(a, b)
    return G


class TestPersonalizedPagerank:
    def test_empty_graph_returns_empty_dict(self):
        assert personalized_pagerank(nx.DiGraph(), {1: 1.0}) == {}

    def test_no_seeds_matches_global_pagerank(self):
        """seeds=None must reproduce compute_pagerank exactly."""
        G = _star(0, [1, 2, 3, 4])
        global_pr = compute_pagerank(G)
        ppr = personalized_pagerank(G, None)
        for node, score in global_pr.items():
            assert ppr[node] == pytest.approx(score, abs=1e-6)

    def test_empty_seeds_dict_matches_global_pagerank(self):
        """Empty dict is the same as None — uniform fallback."""
        G = _star(0, [1, 2, 3, 4])
        global_pr = compute_pagerank(G)
        ppr = personalized_pagerank(G, {})
        for node, score in global_pr.items():
            assert ppr[node] == pytest.approx(score, abs=1e-6)

    def test_scores_sum_to_one(self):
        G = _star(0, [1, 2, 3, 4, 5])
        ppr = personalized_pagerank(G, {1: 1.0})
        assert sum(ppr.values()) == pytest.approx(1.0, abs=1e-6)

    def test_single_seed_concentrates_mass(self):
        """A leaf seeded in a star outranks its peer leaves."""
        G = _star(0, [1, 2, 3, 4])
        ppr = personalized_pagerank(G, {1: 1.0})
        # Seeded leaf 1 should get more rank than the non-seeded leaves
        assert ppr[1] > ppr[2]
        assert ppr[1] > ppr[3]
        assert ppr[1] > ppr[4]
        # Non-seeded leaves are structurally identical → equal rank
        assert ppr[2] == pytest.approx(ppr[3], abs=1e-6)
        assert ppr[3] == pytest.approx(ppr[4], abs=1e-6)

    def test_seeds_as_list_equal_weight(self):
        """Passing a list assigns equal weight to each seed."""
        G = _star(0, [1, 2, 3, 4])
        from_list = personalized_pagerank(G, [1, 2])
        from_dict = personalized_pagerank(G, {1: 0.5, 2: 0.5})
        for node in from_list:
            assert from_list[node] == pytest.approx(from_dict[node], abs=1e-6)

    def test_unnormalised_weights_normalise_internally(self):
        """Weights need not sum to 1; the function normalises."""
        G = _star(0, [1, 2, 3, 4])
        a = personalized_pagerank(G, {1: 1.0, 2: 1.0})
        b = personalized_pagerank(G, {1: 7.0, 2: 7.0})
        for node in a:
            assert a[node] == pytest.approx(b[node], abs=1e-6)

    def test_seeds_outside_graph_filtered(self):
        """Seeds that are not nodes in G are silently dropped."""
        G = _star(0, [1, 2, 3])
        # Node 999 does not exist; node 1 does.
        ppr = personalized_pagerank(G, {999: 1.0, 1: 1.0})
        assert sum(ppr.values()) == pytest.approx(1.0, abs=1e-6)
        # Seeded leaf 1 still beats peers
        assert ppr[1] > ppr[2]
        assert ppr[1] > ppr[3]

    def test_all_seeds_outside_graph_falls_back_to_global(self):
        """When *every* seed is missing, fall back to global PageRank."""
        G = _star(0, [1, 2, 3])
        global_pr = compute_pagerank(G)
        ppr = personalized_pagerank(G, {888: 1.0, 999: 1.0})
        for node, score in global_pr.items():
            assert ppr[node] == pytest.approx(score, abs=1e-6)

    def test_zero_weight_seeds_dropped(self):
        """Seeds with weight 0 are equivalent to absent seeds."""
        G = _star(0, [1, 2, 3])
        a = personalized_pagerank(G, {1: 1.0, 2: 0.0})
        b = personalized_pagerank(G, {1: 1.0})
        for node in a:
            assert a[node] == pytest.approx(b[node], abs=1e-6)

    def test_negative_weight_seeds_dropped(self):
        """Seeds with negative weight are dropped (we never invert)."""
        G = _star(0, [1, 2, 3])
        a = personalized_pagerank(G, {1: 1.0, 2: -1.0})
        b = personalized_pagerank(G, {1: 1.0})
        for node in a:
            assert a[node] == pytest.approx(b[node], abs=1e-6)

    def test_seed_mass_propagates_along_chain(self):
        """In a linear chain, seeding the head boosts downstream nodes."""
        G = _line([1, 2, 3, 4, 5])
        seeded = personalized_pagerank(G, {1: 1.0})
        unseeded_peer = _line([10, 20, 30, 40, 50])
        global_pr = compute_pagerank(unseeded_peer)
        # Seeded chain: head should outrank the structurally-identical
        # head in the unseeded chain (same numerics, just renumbered).
        assert seeded[1] > global_pr[10]

    def test_alpha_override_accepted(self):
        """Passing an explicit alpha works and changes the distribution.

        Alpha differentiates scores only on the real ``nx.pagerank`` path,
        which requires numpy/scipy. The degree-based fallback in
        ``personalized_pagerank`` deliberately ignores alpha (see
        ``TestPersonalizedPagerankAlphaIgnoredInFallback`` in
        ``test_fallback_contracts.py`` — that's the documented constraint).
        So this test only fires when the real solver is available; skip it
        otherwise rather than asserting a property the fallback can't honour.
        """
        pytest.importorskip("numpy")
        G = _star(0, [1, 2, 3])
        a = personalized_pagerank(G, {1: 1.0}, alpha=0.5)
        b = personalized_pagerank(G, {1: 1.0}, alpha=0.95)
        # Different damping ⇒ different scores
        assert a != b
        # Both still normalise
        assert sum(a.values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(b.values()) == pytest.approx(1.0, abs=1e-6)

    def test_returns_score_for_every_node(self):
        """No node may be missing from the result."""
        G = _star(0, [1, 2, 3, 4])
        ppr = personalized_pagerank(G, {1: 1.0})
        for node in G.nodes:
            assert node in ppr
            assert ppr[node] >= 0.0

    def test_dangling_isolated_seed_does_not_crash(self):
        """A seed at a node with in-degree 0 and out-degree 0 still works."""
        G = nx.DiGraph()
        G.add_node(0)  # isolated
        G.add_node(1)
        G.add_node(2)
        G.add_edge(1, 2)
        ppr = personalized_pagerank(G, {0: 1.0})
        # Isolated seeded node holds most of its mass; flow stays put.
        assert sum(ppr.values()) == pytest.approx(1.0, abs=1e-6)
        assert ppr[0] >= ppr[1]
        assert ppr[0] >= ppr[2]

    def test_seed_node_alone_in_graph(self):
        """Single-node graph with that node as seed → all mass on that node."""
        G = nx.DiGraph()
        G.add_node(7)
        ppr = personalized_pagerank(G, {7: 1.0})
        assert ppr == {7: pytest.approx(1.0, abs=1e-6)}
