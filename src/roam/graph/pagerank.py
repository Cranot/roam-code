"""PageRank and centrality metrics for the symbol graph."""

from __future__ import annotations

import os
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
    scc_nodes = sum(len(c) for c in nx.strongly_connected_components(G) if len(c) > 1)
    cycle_ratio = scc_nodes / len(G) if len(G) > 0 else 0.0
    # DAG → 0.92, fully cyclic → 0.82
    return round(0.92 - 0.10 * cycle_ratio, 3)


# Hard iteration cap for the power method. networkx's default is 100; we
# keep the same default and NEVER raise on non-convergence (we return the
# best-so-far vector) so a graph can never wedge a command. Tunable via
# env for the rare pathological-cyclicity case.
_PAGERANK_MAX_ITER: int = int(os.environ.get("ROAM_PAGERANK_MAX_ITER", "100"))
_PAGERANK_TOL: float = 1.0e-6


def _pagerank_core(
    G: nx.DiGraph,
    alpha: float,
    personalization: dict[int, float] | None = None,
    *,
    max_iter: int = _PAGERANK_MAX_ITER,
    tol: float = _PAGERANK_TOL,
) -> dict[int, float]:
    """Roam-owned scipy power-iteration PageRank.

    Mathematically identical to ``networkx.pagerank`` — same damping,
    dangling-mass redistribution, uniform start, and ``err < N*tol`` stop
    rule — so ``fingerprint`` / ``visualize`` / the retrieve reranker get
    the same ranking. It deliberately AVOIDS the two scipy/numpy idioms
    networkx uses internally that regressed to a multi-minute hang on
    large, dangling-heavy graphs under numpy>=2.4 / scipy>=1.17 (observed
    wedging ``roam fingerprint`` / ``roam visualize`` on roam-code's own
    33k-node, 39%-dangling symbol graph — a single iteration did not
    finish in 90s):

      * out-degree via ``np.diff(indptr)`` (pure index arithmetic on the
        CSR row pointers) instead of ``A.sum(axis=1)`` — the latter
        densified / stalled under the new sparse-array API;
      * the transition step as ``Mt @ x`` (CSR sparse @ dense vector — the
        bedrock, O(nnz) matvec) instead of the ``x @ A`` (dense @ sparse)
        idiom networkx applies each iteration.

    The hard ``max_iter`` cap guarantees termination regardless of
    convergence, so the command can never hang; on non-convergence we
    return the best-so-far vector rather than raising (a Gini summary or a
    relative re-rank tolerates an approximate tail). Raises ``ImportError``
    when numpy/scipy are absent so callers fall back to degree ranking.

    The symbol graph is unweighted (edges carry ``kind``, not ``weight``),
    matching networkx's ``weight=1.0`` default for it — so per-row nnz IS
    the out-degree.
    """
    # numpy/scipy are optional extras; this core REQUIRES them and is
    # documented (above) to raise ImportError so compute_pagerank() /
    # personalized_pagerank() fall back to degree ranking via their own
    # `except ImportError`. Kept lazy (not module-level) so importing
    # roam.graph.pagerank stays cheap on a minimal install. The W168 lint
    # can't see the caller's guard, hence the explicit marker.
    import numpy as np  # unguarded-import: ok
    import scipy.sparse as sp  # unguarded-import: ok

    nodes = list(G)
    n = len(nodes)
    if n == 0:
        return {}
    index = {node: i for i, node in enumerate(nodes)}

    m = G.number_of_edges()
    rows = np.empty(m, dtype=np.int64)
    cols = np.empty(m, dtype=np.int64)
    for k, (u, v) in enumerate(G.edges()):
        rows[k] = index[u]
        cols[k] = index[v]
    A = sp.csr_array((np.ones(m, dtype=float), (rows, cols)), shape=(n, n))

    # Out-degree from CSR row pointers — no A.sum(axis=1) (the regressing op).
    out = np.diff(A.indptr).astype(float)
    inv = np.zeros(n, dtype=float)
    nz = out != 0.0
    inv[nz] = 1.0 / out[nz]
    # Row-stochastic transition matrix, built by scaling each stored entry
    # by its row's 1/out-degree (stays sparse; no diag-matrix product).
    M = sp.csr_array(
        (A.data * np.repeat(inv, np.diff(A.indptr)), A.indices, A.indptr.copy()),
        shape=(n, n),
    )
    Mt = M.T.tocsr()
    is_dangling = ~nz

    # Teleport / personalisation vector (defaults to uniform).
    p = np.full(n, 1.0 / n, dtype=float)
    if personalization:
        p = np.zeros(n, dtype=float)
        for node, weight in personalization.items():
            i = index.get(node)
            if i is not None:
                p[i] = float(weight)
        s = float(p.sum())
        p = (p / s) if s > 0 else np.full(n, 1.0 / n, dtype=float)

    x = np.full(n, 1.0 / n, dtype=float)
    for _ in range(max_iter):
        xlast = x
        x = alpha * (Mt @ xlast + float(xlast[is_dangling].sum()) * p) + (1.0 - alpha) * p
        if float(np.abs(x - xlast).sum()) < n * tol:
            break
    return {nodes[i]: float(x[i]) for i in range(n)}


def compute_pagerank(G: nx.DiGraph, alpha: float | None = None) -> dict[int, float]:
    """Compute PageRank scores for every node in *G*.

    Returns ``{symbol_id: pagerank_score}``.  Returns an empty dict when the
    graph has no nodes.

    When *alpha* is ``None`` (default) the damping factor is chosen
    adaptively based on graph cyclicity via ``_optimal_alpha()``.

    Uses the roam-owned :func:`_pagerank_core` power iteration (bounded,
    hang-proof). Falls back to degree-based ranking when numpy/scipy are
    not available (minimal install).
    """
    if len(G) == 0:
        return {}
    if alpha is None:
        alpha = _optimal_alpha(G)
    try:
        return _pagerank_core(G, alpha)
    except ImportError:
        # numpy/scipy not installed — degree-based fallback. Normalise
        # so the result still satisfies the "scores sum to ~1" contract
        # the docstring promises and that ``personalized_pagerank``'s
        # fallback also produces — otherwise a chain comparison like
        # ``seeded[head] > global_pr[head]`` mixes scales.
        raw = {n: G.degree(n) for n in G}
        total = sum(raw.values()) or 1.0
        return {n: v / total for n, v in raw.items()}


def personalized_pagerank(
    G: nx.DiGraph,
    seeds: dict[int, float] | list[int] | None,
    alpha: float | None = None,
) -> dict[int, float]:
    """Compute personalised PageRank with mass concentrated on *seeds*.

    Used by the retrieve reranker (A.1) to bias structural ranking toward
    the symbols most relevant to a query. The seed nodes (e.g., symbols
    mentioned in the query) get personalisation mass; everything else
    pulls rank only via the link structure.

    Parameters
    ----------
    G:
        Directed call/import graph from ``build_symbol_graph``.
    seeds:
        Either a ``{node_id: weight}`` mapping (weights need not sum to 1
        — they are normalised) **or** a list of node ids treated as
        equal-weight. ``None`` or an empty mapping falls back to a
        uniform distribution, which is equivalent to global PageRank.
    alpha:
        Damping factor. ``None`` (default) uses the same adaptive
        cyclicity heuristic as :func:`compute_pagerank`.

    Returns
    -------
    dict[int, float]
        ``{symbol_id: score}`` for every node in *G*. Scores sum to ~1.0.

    Notes
    -----
    Seeds that are not present in *G* are silently dropped — the caller
    typically resolves "files mentioned in the task" to symbol ids and
    not all of those will exist as graph nodes.
    """
    if len(G) == 0:
        return {}

    if seeds is None:
        normalised: dict[int, float] = {}
    elif isinstance(seeds, dict):
        normalised = {n: float(w) for n, w in seeds.items() if n in G and w > 0}
    else:
        normalised = {n: 1.0 for n in seeds if n in G}

    if alpha is None:
        alpha = _optimal_alpha(G)

    if not normalised:
        return compute_pagerank(G, alpha=alpha)

    total = sum(normalised.values())
    if total > 0:
        normalised = {n: w / total for n, w in normalised.items()}

    try:
        return nx.pagerank(G, alpha=alpha, personalization=normalised)
    except ImportError:
        # numpy/scipy not installed — degree-with-seed-boost fallback,
        # normalised so the result still satisfies the "scores sum to 1"
        # contract that callers (and tests) rely on. Without the
        # normalisation the raw values summed to ~7 on networkx 3.2.1
        # (minimal-install path with no scipy).
        raw = {n: G.degree(n) + (5.0 if n in normalised else 0.0) for n in G}
        total = sum(raw.values()) or 1.0
        return {n: v / total for n, v in raw.items()}
    except nx.PowerIterationFailedConvergence:
        # Rare on real graphs; fall back to a tolerant pagerank call
        return nx.pagerank(G, alpha=alpha, personalization=normalised, max_iter=300, tol=1e-04)


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
        k = min(n, max(200, int(n**0.5 * 5)))
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

    degree_raw = {node: float(G.in_degree(node) + G.out_degree(node)) for node in G.nodes}
    degree_n = _norm(degree_raw)
    bw_n = _norm({k: float(v) for k, v in betweenness.items()})
    close_n = _norm({k: float(v) for k, v in closeness.items()})
    eig_n = _norm({k: float(v) for k, v in eigen.items()})
    result: dict[int, dict] = {}
    for node in G.nodes:
        cc = float(clustering.get(node, 0.0))
        debt_score = 100.0 * (
            0.30 * degree_n.get(node, 0.0)
            + 0.25 * bw_n.get(node, 0.0)
            + 0.20 * close_n.get(node, 0.0)
            + 0.15 * eig_n.get(node, 0.0)
            + 0.10 * (1.0 - cc)
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
        rows.append(
            (
                node,
                pr.get(node, 0.0),
                c.get("in_degree", 0),
                c.get("out_degree", 0),
                c.get("betweenness", 0.0),
                c.get("closeness", 0.0),
                c.get("eigenvector", 0.0),
                c.get("clustering_coefficient", 0.0),
                c.get("debt_score", 0.0),
            )
        )

    conn.executemany(
        "INSERT OR REPLACE INTO graph_metrics "
        "(symbol_id, pagerank, in_degree, out_degree, betweenness, "
        "closeness, eigenvector, clustering_coefficient, debt_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)
