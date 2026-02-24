"""PageRank and centrality metrics for the symbol graph."""

from __future__ import annotations

import sqlite3

import networkx as nx


def _optimal_alpha(G: nx.DiGraph) -> float:
    """Choose PageRank damping factor based on graph structure.

    Dependency graphs are mostly DAG-like; higher alpha (0.92) better
    captures importance along deep call chains.  Heavily cyclic graphs
    need lower alpha (0.85) for convergence stability.

    Heuristic: cycle_ratio = |nodes in non-trivial SCCs| / |nodes|.
    Linear interpolation between 0.92 (DAG) and 0.82 (fully cyclic).
    """
    scc_nodes = sum(
        len(c) for c in nx.strongly_connected_components(G) if len(c) > 1
    )
    cycle_ratio = scc_nodes / len(G) if len(G) > 0 else 0.0
    # DAG → 0.92, fully cyclic → 0.82
    return round(0.92 - 0.10 * cycle_ratio, 3)


def compute_pagerank(G: nx.DiGraph, alpha: float | None = None) -> dict[int, float]:
    """Compute PageRank scores for every node in *G*.

    Returns ``{symbol_id: pagerank_score}``.  Returns an empty dict when the
    graph has no nodes.

    When *alpha* is ``None`` (default) the damping factor is chosen
    adaptively based on graph cyclicity via ``_optimal_alpha()``.

    Falls back to degree-based ranking when numpy is not available
    (networkx < 3.2 requires numpy for pagerank).
    """
    if len(G) == 0:
        return {}
    if alpha is None:
        alpha = _optimal_alpha(G)
    try:
        return nx.pagerank(G, alpha=alpha)
    except ImportError:
        # numpy not installed — fall back to degree-based ranking
        max_deg = max((G.degree(n) for n in G), default=1) or 1
        return {n: G.degree(n) / max_deg for n in G}


def compute_centrality(G: nx.DiGraph) -> dict[int, dict]:
    """Compute SNA metric vector for each symbol.

    Returns
    -------
    dict
        ``{symbol_id: {in_degree, out_degree, betweenness, closeness,
        eigenvector, clustering_coefficient, debt_score}}``
    """
    if len(G) == 0:
        return {}

    UG = G.to_undirected()

    # Adaptive sampling: exact for small graphs, sqrt-scaled for large.
    # For n < 1000, compute exact betweenness O(n*m).
    # For larger graphs, sample k = max(200, sqrt(n)*5) pivot nodes.
    # sqrt scaling gives diminishing-returns sampling that's well-studied
    # in the betweenness approximation literature (Brandes & Pich, 2007).
    n = len(G)
    if n <= 1000:
        k = n  # exact computation
    else:
        k = min(n, max(200, int(n ** 0.5 * 5)))
    betweenness = nx.betweenness_centrality(G, k=k, normalized=False)

    # Closeness: for very large graphs use degree-based proxy for speed.
    if n <= 3000:
        closeness = nx.closeness_centrality(UG)
    else:
        max_deg = max((UG.degree(v) for v in UG.nodes), default=1) or 1
        closeness = {v: UG.degree(v) / max_deg for v in UG.nodes}

    # Eigenvector centrality on undirected projection.
    try:
        if n <= 2500:
            eigen = nx.eigenvector_centrality(UG, max_iter=300, tol=1e-06)
        else:
            # Large-graph fallback: normalized degree proxy.
            max_deg = max((UG.degree(v) for v in UG.nodes), default=1) or 1
            eigen = {v: UG.degree(v) / max_deg for v in UG.nodes}
    except Exception:
        max_deg = max((UG.degree(v) for v in UG.nodes), default=1) or 1
        eigen = {v: UG.degree(v) / max_deg for v in UG.nodes}

    clustering = nx.clustering(UG) if len(UG) > 0 else {}

    def _norm(metric: dict[int, float]) -> dict[int, float]:
        if not metric:
            return {}
        values = list(metric.values())
        lo = min(values)
        hi = max(values)
        if hi <= lo:
            return {k: 0.0 for k in metric}
        span = hi - lo
        return {k: (float(v) - lo) / span for k, v in metric.items()}

    degree_raw = {
        node: float(G.in_degree(node) + G.out_degree(node))
        for node in G.nodes
    }
    degree_n = _norm(degree_raw)
    bw_n = _norm({k: float(v) for k, v in betweenness.items()})
    close_n = _norm({k: float(v) for k, v in closeness.items()})
    eig_n = _norm({k: float(v) for k, v in eigen.items()})
    result: dict[int, dict] = {}
    for node in G.nodes:
        cc = float(clustering.get(node, 0.0))
        debt_score = (
            100.0
            * (
                0.30 * degree_n.get(node, 0.0)
                + 0.25 * bw_n.get(node, 0.0)
                + 0.20 * close_n.get(node, 0.0)
                + 0.15 * eig_n.get(node, 0.0)
                + 0.10 * (1.0 - cc)
            )
        )
        result[node] = {
            "in_degree": G.in_degree(node),
            "out_degree": G.out_degree(node),
            "betweenness": betweenness.get(node, 0.0),
            "closeness": float(closeness.get(node, 0.0)),
            "eigenvector": float(eigen.get(node, 0.0)),
            "clustering_coefficient": cc,
            "debt_score": max(0.0, min(100.0, debt_score)),
        }
    return result


def store_metrics(conn: sqlite3.Connection, G: nx.DiGraph) -> int:
    """Compute and persist all graph metrics into the ``graph_metrics`` table.

    Returns the number of rows written.
    """
    if len(G) == 0:
        return 0

    pr = compute_pagerank(G)
    centrality = compute_centrality(G)

    rows = []
    for node in G.nodes:
        c = centrality.get(node, {})
        rows.append((
            node,
            pr.get(node, 0.0),
            c.get("in_degree", 0),
            c.get("out_degree", 0),
            c.get("betweenness", 0.0),
            c.get("closeness", 0.0),
            c.get("eigenvector", 0.0),
            c.get("clustering_coefficient", 0.0),
            c.get("debt_score", 0.0),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO graph_metrics "
        "(symbol_id, pagerank, in_degree, out_degree, betweenness, "
        "closeness, eigenvector, clustering_coefficient, debt_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)
