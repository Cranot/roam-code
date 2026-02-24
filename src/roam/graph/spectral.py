"""Spectral bisection using the Fiedler vector for module decomposition.

Provides an alternative to Louvain community detection by leveraging
the algebraic connectivity of the graph Laplacian.

Key functions:
  - fiedler_partition(G, max_depth)  -- recursive spectral bisection
  - spectral_gap(G)                  -- lambda2 as modularity indicator
  - spectral_communities(G, k)       -- partition into k groups (auto-k if None)
"""

from __future__ import annotations

import math
from collections import defaultdict

import networkx as nx


_MIN_PARTITION_SIZE = 5
_GAP_STOP_THRESHOLD = 1e-6
_HIGH_GAP = 0.5
_MED_GAP = 0.1


def _fiedler_split(G: nx.Graph):
    """Split G into two groups using the Fiedler vector.

    Returns (group_neg, group_pos) or None if bisection fails.
    """
    nodes = list(G.nodes())
    if len(nodes) < 2:
        return None
    if not nx.is_connected(G):
        return None
    try:
        fv = nx.linalg.algebraicconnectivity.fiedler_vector(G, method="tracemin_pcg")
    except Exception:
        try:
            fv = nx.linalg.algebraicconnectivity.fiedler_vector(G, method="lobpcg")
        except Exception:
            return None
    node_arr = list(G.nodes())
    neg = [node_arr[i] for i, v in enumerate(fv) if v < 0]
    pos = [node_arr[i] for i, v in enumerate(fv) if v >= 0]
    if not neg or not pos:
        return None
    return neg, pos


def _louvain_fallback(G: nx.Graph):
    """Partition G using Louvain when spectral bisection is not available."""
    if len(G) == 0:
        return {}
    try:
        communities = list(nx.community.louvain_communities(G, seed=42))
    except (AttributeError, TypeError):
        communities = list(nx.community.greedy_modularity_communities(G))
    mapping = {}
    for cid, members in enumerate(communities):
        for node in members:
            mapping[node] = cid
    return mapping


def fiedler_partition(G, max_depth: int = 3):
    """Recursively bisect G using the Fiedler vector.

    Parameters
    ----------
    G:
        A NetworkX graph (directed or undirected).  If directed, the
        undirected projection is used for bisection.
    max_depth:
        Maximum recursion depth.  Each level doubles the number of
        potential partitions.

    Returns
    -------
    {node: partition_id} mapping.  Partition IDs are dense integers starting at 0.
    """
    if len(G) == 0:
        return {}
    UG = G.to_undirected() if G.is_directed() else G
    components = list(nx.connected_components(UG))
    if len(components) > 1:
        result = {}
        next_id = 0
        for comp in components:
            sub = UG.subgraph(comp).copy()
            sub_map = fiedler_partition(sub, max_depth=max_depth)
            local_ids = sorted(set(sub_map.values()))
            id_remap = {old: next_id + i for i, old in enumerate(local_ids)}
            for node, pid in sub_map.items():
                result[node] = id_remap[pid]
            next_id += len(local_ids)
        return result
    partition_map = {}
    _bisect_recursive(UG, list(UG.nodes()), max_depth, 0, partition_map, [0])
    return partition_map


def _bisect_recursive(UG, nodes, max_depth, depth, partition_map, counter):
    """Recursively bisect nodes within UG, updating partition_map."""
    if depth >= max_depth or len(nodes) < _MIN_PARTITION_SIZE:
        pid = counter[0]
        counter[0] += 1
        for n in nodes:
            partition_map[n] = pid
        return
    sub = UG.subgraph(nodes).copy()
    gap = _compute_algebraic_connectivity(sub)
    if gap < _GAP_STOP_THRESHOLD:
        pid = counter[0]
        counter[0] += 1
        for n in nodes:
            partition_map[n] = pid
        return
    split = _fiedler_split(sub)
    if split is None:
        pid = counter[0]
        counter[0] += 1
        for n in nodes:
            partition_map[n] = pid
        return
    neg_nodes, pos_nodes = split
    _bisect_recursive(UG, neg_nodes, max_depth, depth + 1, partition_map, counter)
    _bisect_recursive(UG, pos_nodes, max_depth, depth + 1, partition_map, counter)


def _compute_algebraic_connectivity(G: nx.Graph) -> float:
    """Return algebraic connectivity (lambda2) of G, or 0.0 on failure."""
    if len(G) < 2:
        return 0.0
    if not nx.is_connected(G):
        return 0.0
    try:
        return float(nx.linalg.algebraicconnectivity.algebraic_connectivity(G))
    except Exception:
        return 0.0


def spectral_gap(G) -> float:
    """Compute the spectral gap of G.

    The spectral gap is the algebraic connectivity (lambda2) of the graph
    Laplacian.  Higher values indicate clearer natural partitions:
      > 0.5  -> Well-modularized
      0.1-0.5 -> Moderately modular
      < 0.1  -> Poorly modularized (highly interconnected)

    Returns 0.0 for trivially small or disconnected graphs.
    """
    if len(G) == 0:
        return 0.0
    UG = G.to_undirected() if G.is_directed() else G
    components = list(nx.connected_components(UG))
    if len(components) > 1:
        gaps = []
        for comp in components:
            if len(comp) >= 2:
                sub = UG.subgraph(comp).copy()
                gaps.append(_compute_algebraic_connectivity(sub))
        return min(gaps) if gaps else 0.0
    return _compute_algebraic_connectivity(UG)


def spectral_communities(G, k=None):
    """Partition G into k communities using spectral methods.

    If k is None, the number of communities is auto-detected from
    the spectral gap: a large gap -> fewer communities, small gap -> more.

    Returns {node: community_id}.
    """
    if len(G) == 0:
        return {}
    UG = G.to_undirected() if G.is_directed() else G
    if k is None:
        gap = spectral_gap(G)
        if gap > _HIGH_GAP:
            k = max(2, int(math.log2(len(G))) - 1)
        elif gap > _MED_GAP:
            k = max(2, int(math.log2(len(G))))
        else:
            k = max(2, int(math.log2(len(G))) + 1)
        k = min(k, max(2, len(G) // _MIN_PARTITION_SIZE))
    if len(UG) < _MIN_PARTITION_SIZE:
        return _louvain_fallback(UG)
    max_depth = max(1, math.ceil(math.log2(max(k, 2))))
    raw = fiedler_partition(UG, max_depth=max_depth)
    if not raw:
        return _louvain_fallback(UG)
    partitions = sorted(set(raw.values()))
    n_parts = len(partitions)
    if n_parts <= k:
        remap = {old: new for new, old in enumerate(partitions)}
        return {node: remap[pid] for node, pid in raw.items()}
    merged = dict(raw)
    while len(set(merged.values())) > k:
        counts = defaultdict(int)
        for pid in merged.values():
            counts[pid] += 1
        smallest = min(counts, key=lambda p: counts[p])
        all_pids = sorted(set(merged.values()))
        idx = all_pids.index(smallest)
        target = all_pids[idx + 1] if idx + 1 < len(all_pids) else all_pids[idx - 1]
        merged = {n: (target if p == smallest else p) for n, p in merged.items()}
    final_pids = sorted(set(merged.values()))
    remap2 = {old: new for new, old in enumerate(final_pids)}
    return {node: remap2[pid] for node, pid in merged.items()}


def verdict_from_gap(gap: float) -> str:
    """Return a human-readable verdict for a given spectral gap value."""
    if gap > _HIGH_GAP:
        return "Well-modularized"
    if gap > _MED_GAP:
        return "Moderately modular"
    return "Poorly modularized"


def adjusted_rand_index(labels_true: list, labels_pred: list) -> float:
    """Compute the Adjusted Rand Index (ARI) between two clusterings.

    Pure Python implementation -- no sklearn dependency.

    ARI = 1.0  -> perfect agreement
    ARI = 0.0  -> random agreement
    ARI < 0.0  -> worse than random
    """
    if len(labels_true) != len(labels_pred):
        raise ValueError("labels_true and labels_pred must have the same length")
    n = len(labels_true)
    if n == 0:
        return 1.0
    classes = sorted(set(labels_true))
    clusters = sorted(set(labels_pred))
    class_idx = {c: i for i, c in enumerate(classes)}
    cluster_idx = {c: i for i, c in enumerate(clusters)}
    contingency = [[0] * len(clusters) for _ in range(len(classes))]
    for t, p in zip(labels_true, labels_pred):
        contingency[class_idx[t]][cluster_idx[p]] += 1

    def _comb2(x):
        return x * (x - 1) // 2

    sum_comb = sum(
        _comb2(contingency[i][j])
        for i in range(len(classes))
        for j in range(len(clusters))
    )
    row_sums = [sum(contingency[i]) for i in range(len(classes))]
    col_sums = [
        sum(contingency[i][j] for i in range(len(classes)))
        for j in range(len(clusters))
    ]
    sum_row_comb = sum(_comb2(r) for r in row_sums)
    sum_col_comb = sum(_comb2(c) for c in col_sums)
    comb_n = _comb2(n)
    if comb_n == 0:
        return 1.0
    expected = sum_row_comb * sum_col_comb / comb_n
    max_comb = (sum_row_comb + sum_col_comb) / 2.0
    denom = max_comb - expected
    if abs(denom) < 1e-10:
        return 1.0 if sum_comb == expected else 0.0
    return round((sum_comb - expected) / denom, 6)
