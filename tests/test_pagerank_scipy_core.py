"""Roam-owned PageRank power iteration: correctness + no-hang regression.

Guards the fix for the multi-minute `roam fingerprint` / `roam visualize`
hang on large, dangling-heavy graphs under numpy>=2.4 / scipy>=1.17, where
networkx's internal scipy-pagerank setup (``A.sum(axis=1)`` + ``x @ A``)
stalled. ``_pagerank_core`` is mathematically identical to
``networkx.pagerank`` but uses ``np.diff(indptr)`` for out-degree and
``Mt @ x`` for the transition, plus a hard ``max_iter`` cap.

Two axes:
  * CORRECTNESS — ranks match ``networkx.pagerank`` on small graphs
    (where networkx still runs fine), including dangling nodes and a
    personalization vector.
  * TERMINATION — a 5,000-node / ~39%-dangling graph (the shape that
    wedged the real command) completes well under a wall-clock budget and
    yields a valid probability distribution.
"""

from __future__ import annotations

import time

import networkx as nx
import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from roam.graph.pagerank import (  # noqa: E402
    _pagerank_core,
    compute_pagerank,
    personalized_pagerank,
)

_ALPHA = 0.85


def _assert_close(a: dict[int, float], b: dict[int, float], tol: float = 1e-4) -> None:
    assert set(a) == set(b)
    for k in a:
        assert abs(a[k] - b[k]) < tol, f"node {k}: {a[k]} vs {b[k]} (>{tol})"


def test_core_matches_networkx_no_dangling():
    """On a fully-connected-ish cyclic graph (no dangling), ranks match nx."""
    G = nx.DiGraph()
    G.add_edges_from([(0, 1), (1, 2), (2, 0), (2, 3), (3, 1), (0, 3), (3, 0)])
    got = _pagerank_core(G, _ALPHA)
    want = nx.pagerank(G, alpha=_ALPHA)
    _assert_close(got, want)


def test_core_matches_networkx_with_dangling():
    """Dangling nodes (out-degree 0) must redistribute mass exactly like nx."""
    G = nx.DiGraph()
    # 4 and 5 are dangling sinks (no outgoing edges).
    G.add_edges_from([(0, 1), (1, 2), (2, 0), (0, 4), (1, 5), (2, 4), (3, 0)])
    G.add_node(6)  # fully isolated node
    got = _pagerank_core(G, _ALPHA)
    want = nx.pagerank(G, alpha=_ALPHA)
    _assert_close(got, want)


def test_core_matches_networkx_personalized():
    """Personalization vector steers mass the same way nx does."""
    G = nx.DiGraph()
    G.add_edges_from([(0, 1), (1, 2), (2, 0), (2, 3), (3, 1), (4, 0)])
    norm = {0: 1.0 / 3.0, 3: 2.0 / 3.0}  # personalized_pagerank normalises
    got = _pagerank_core(G, _ALPHA, personalization=norm)
    want = nx.pagerank(G, alpha=_ALPHA, personalization=norm)
    _assert_close(got, want)


def test_personalized_pagerank_public_matches_nx():
    """The public personalized_pagerank wrapper matches nx end to end."""
    G = nx.DiGraph()
    G.add_edges_from([(0, 1), (1, 2), (2, 0), (2, 3), (3, 1), (4, 0)])
    got = personalized_pagerank(G, {0: 1.0, 3: 2.0}, alpha=_ALPHA)
    want = nx.pagerank(G, alpha=_ALPHA, personalization={0: 1.0 / 3.0, 3: 2.0 / 3.0})
    _assert_close(got, want)


def test_scores_sum_to_one():
    G = nx.DiGraph()
    G.add_edges_from([(0, 1), (1, 2), (2, 0), (3, 0), (1, 4)])
    pr = _pagerank_core(G, _ALPHA)
    assert abs(sum(pr.values()) - 1.0) < 1e-6


def test_empty_and_edgeless_graphs():
    assert _pagerank_core(nx.DiGraph(), _ALPHA) == {}
    G = nx.DiGraph()
    G.add_nodes_from([1, 2, 3])  # no edges -> uniform
    pr = _pagerank_core(G, _ALPHA)
    assert abs(sum(pr.values()) - 1.0) < 1e-6
    assert all(abs(v - 1.0 / 3.0) < 1e-9 for v in pr.values())


def test_large_dangling_graph_terminates_fast():
    """Regression guard for the numpy2.4/scipy1.17 PageRank hang.

    A 5,000-node graph with ~39% dangling nodes (the topology of
    roam-code's own symbol graph that wedged ``roam fingerprint`` for
    minutes) must finish in well under a second and yield a valid
    distribution. Built directly (NOT via gnp_random_graph, which is
    O(n^2) to *generate*).
    """
    n = 5000
    rng = np.random.default_rng(0)
    src_pool = np.arange(int(n * 0.61))  # only 61% of nodes emit edges
    m = 7500
    src = rng.choice(src_pool, size=m)
    dst = rng.integers(0, n, size=m)
    G = nx.DiGraph()
    G.add_nodes_from(range(n))
    G.add_edges_from(zip(src.tolist(), dst.tolist()))
    dangling = sum(1 for node in G if G.out_degree(node) == 0)
    assert dangling > n * 0.3  # confirm we reproduced the dangling-heavy shape

    t0 = time.monotonic()
    pr = compute_pagerank(G, alpha=0.92)
    elapsed = time.monotonic() - t0

    assert len(pr) == n
    assert abs(sum(pr.values()) - 1.0) < 1e-3
    # The bug made a single iteration exceed 90s; a healthy run is well
    # under a second. Keep a generous ceiling so the guard is about
    # "did not hang", not micro-benchmarking on shared CI.
    assert elapsed < 10.0, f"compute_pagerank took {elapsed:.1f}s on a 5k dangling graph"
