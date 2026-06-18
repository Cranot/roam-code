"""Graph cloning, transforms, and metric recomputation for architecture simulation."""

from __future__ import annotations

import math

import networkx as nx

# ---------------------------------------------------------------------------
# Higher-is-better map for direction logic
# ---------------------------------------------------------------------------

_HIGHER_IS_BETTER = {
    "health_score": True,
    "nodes": True,
    "edges": True,
    "modularity": True,
    "fiedler": True,
    "cycles": False,
    "tangle_ratio": False,
    "layer_violations": False,
    "propagation_cost": False,
    "god_components": False,
    "bottlenecks": False,
}


# ---------------------------------------------------------------------------
# Health score approximation
# ---------------------------------------------------------------------------


def _hf(value: float, scale: float) -> float:
    """Exponential decay helper for health scoring."""
    return math.exp(-value / scale) if scale > 0 else 1.0


def _approx_health(
    tangle_ratio: float,
    god_count: int,
    bn_count: int,
    layer_violations: int,
    node_count: int = 0,
) -> int:
    """Compute an approximate health score from graph-derived signals.

    Uses exponential decay (same family as metrics_history._compute_health_score)
    so that the *delta* between before/after is directionally accurate.

    The ``god_count`` and ``bn_count`` terms are normalised to *percentages
    of the graph* before decay. The previous formulation decayed the raw
    counts (``_hf(god_count * 3, 5)`` == ``exp(-god_count * 0.6)``), which
    saturates to 0 on any non-trivial repo — roam-code itself has ~290 god
    components, so ``exp(-174)`` floored the absolute baseline to 0 and made
    the LAW-6 verdict ("health unchanged at 0") meaningless. Normalising by
    ``node_count`` keeps the score a plausible 0..100 value on large graphs
    while preserving delta direction. ``node_count == 0`` reproduces the
    all-perfect case (every signal decays from 0 -> 1.0 -> score 100).
    """
    # Percentage of the graph that is a god component / bottleneck. Decay is
    # calibrated against percentages so a fixed repo size no longer drives
    # the score to 0 just because the absolute count is large.
    god_pct = (100.0 * god_count / node_count) if node_count > 0 else 0.0
    bn_pct = (100.0 * bn_count / node_count) if node_count > 0 else 0.0

    t = _hf(tangle_ratio, 10)  # weight 0.30
    g = _hf(god_pct, 12)  # weight 0.25
    b = _hf(bn_pct, 10)  # weight 0.20
    lv = _hf(layer_violations, 5)  # weight 0.25

    weights = [0.30, 0.25, 0.20, 0.25]
    signals = [t, g, b, lv]

    log_sum = sum(w * math.log(max(s, 1e-9)) for w, s in zip(weights, signals))
    raw = 100 * math.exp(log_sum)
    return max(0, min(100, int(raw)))


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _modularity_only(G: nx.DiGraph, clusters: dict[int, int]) -> float:
    """Compute Newman's modularity Q-score without the conductance pass.

    ``compute_graph_metrics`` consumes only the ``modularity`` field of
    ``cluster_quality``'s result. The per-cluster conductance loop in
    ``cluster_quality`` re-iterates ``undirected.edges()`` once per cluster
    -- O(clusters * edges) -- and on roam-code itself that single loop costs
    ~25s out of a ~50s ``compute_graph_metrics`` call. Computing modularity
    directly here is byte-identical to ``round(cluster_quality(...)["modularity"], 4)``
    (same ``nx.community.modularity`` call, same rounding, same fallback)
    while skipping the unused conductance work entirely.
    """
    if not clusters or len(G) == 0:
        return 0.0
    from collections import defaultdict

    undirected = G.to_undirected()
    node_set = set(undirected.nodes())
    # Repair the partition (singleton per uncovered in-graph node) so
    # nx.community.modularity does not raise NotAPartition and silently floor Q
    # to 0.0 — mirrors cluster_quality / _fast_cluster_quality.
    partition_groups: dict[int, set] = defaultdict(set)
    for node_id, cid in clusters.items():
        if node_id in node_set:
            partition_groups[cid].add(node_id)
    covered_nodes: set = set()
    for members in partition_groups.values():
        covered_nodes |= members
    communities = list(partition_groups.values()) + [{n} for n in node_set - covered_nodes]
    try:
        q = nx.community.modularity(undirected, communities) if communities else 0.0
    except Exception:
        q = 0.0
    return round(q, 4)


def compute_graph_metrics(G: nx.DiGraph) -> dict:
    """Compute all graph-derivable metrics on any DiGraph."""
    from roam.graph.clusters import detect_clusters
    from roam.graph.cycles import algebraic_connectivity, find_cycles, propagation_cost
    from roam.graph.layers import detect_layers, find_violations

    n = len(G)
    e = G.number_of_edges()

    # Cycles / tangle
    sccs = find_cycles(G)
    cycle_count = len(sccs)
    scc_nodes = sum(len(c) for c in sccs)
    tangle = round(100 * scc_nodes / n, 2) if n > 0 else 0.0

    # Layer violations
    layers = detect_layers(G)
    violations = find_violations(G, layers)
    lv_count = len(violations)

    # Modularity -- direct Q-score; skips cluster_quality's unused
    # O(clusters * edges) conductance loop (output-identical).
    clusters = detect_clusters(G)
    modularity = _modularity_only(G, clusters)

    # Fiedler
    fiedler = algebraic_connectivity(G)

    # Propagation cost
    pc = propagation_cost(G) if n <= 500 else 0.0

    # God components: nodes with total degree > 20
    god_count = sum(1 for nd in G.nodes if G.degree(nd) > 20)

    # Bottlenecks: nodes with betweenness > 90th percentile
    bn_count = 0
    if n > 2:
        k = min(n, max(50, int(n**0.5 * 3)))
        bc = nx.betweenness_centrality(G, k=k)
        if bc:
            vals = sorted(bc.values())
            p90 = vals[int(len(vals) * 0.9)] if vals else 0
            bn_count = sum(1 for v in bc.values() if v > p90) if p90 > 0 else 0

    health = _approx_health(tangle, god_count, bn_count, lv_count, node_count=n)

    return {
        "health_score": health,
        "nodes": n,
        "edges": e,
        "cycles": cycle_count,
        "tangle_ratio": tangle,
        "layer_violations": lv_count,
        "modularity": round(modularity, 4),
        "fiedler": round(fiedler, 6),
        "propagation_cost": pc,
        "god_components": god_count,
        "bottlenecks": bn_count,
    }


# ---------------------------------------------------------------------------
# Metric delta
# ---------------------------------------------------------------------------


def metric_delta(before: dict, after: dict) -> dict:
    """Compute per-metric deltas with direction classification."""
    result = {}
    for key in before:
        if key not in after:
            continue
        b = before[key]
        a = after[key]
        delta = a - b
        if b != 0:
            pct = round(100 * delta / abs(b), 1)
        else:
            pct = 0.0 if delta == 0 else 100.0

        higher_better = _HIGHER_IS_BETTER.get(key)
        if delta == 0:
            direction = "unchanged"
        elif higher_better is None:
            direction = "changed"
        elif (higher_better and delta > 0) or (not higher_better and delta < 0):
            direction = "improved"
        else:
            direction = "degraded"

        result[key] = {
            "before": b,
            "after": a,
            "delta": delta if isinstance(delta, int) else round(delta, 4),
            "pct_change": pct,
            "direction": direction,
        }
    return result


# ---------------------------------------------------------------------------
# Graph cloning
# ---------------------------------------------------------------------------


def clone_graph(G: nx.DiGraph) -> nx.DiGraph:
    """Deep-copy a graph preserving all node/edge attributes."""
    return G.copy()


# ---------------------------------------------------------------------------
# Transform functions
# ---------------------------------------------------------------------------


def apply_move(G: nx.DiGraph, node_id: int, target_file: str) -> dict:
    """Move a symbol to a different file. Edges stay the same."""
    data = G.nodes[node_id]
    old_file = data.get("file_path", "")
    name = data.get("name", str(node_id))
    data["file_path"] = target_file
    return {
        "operation": "move",
        "symbol": name,
        "from_file": old_file,
        "to_file": target_file,
        "affected": 1,
    }


def apply_extract(G: nx.DiGraph, node_id: int, target_file: str) -> dict:
    """Extract a symbol and its same-file private callees to a new file."""
    data = G.nodes[node_id]
    source_file = data.get("file_path", "")
    name = data.get("name", str(node_id))

    # Find same-file private callees
    extracted = [node_id]
    for _, callee in G.out_edges(node_id):
        callee_data = G.nodes[callee]
        callee_file = callee_data.get("file_path", "")
        callee_name = callee_data.get("name", "")
        if callee_file == source_file and callee_name.startswith("_"):
            extracted.append(callee)

    extracted_names = []
    for nid in extracted:
        G.nodes[nid]["file_path"] = target_file
        extracted_names.append(G.nodes[nid].get("name", str(nid)))

    return {
        "operation": "extract",
        "symbol": name,
        "from_file": source_file,
        "to_file": target_file,
        "extracted": extracted_names,
        "affected": len(extracted),
    }


def apply_merge(G: nx.DiGraph, file_a: str, file_b: str) -> dict:
    """Merge file_b into file_a by moving all file_b symbols."""
    merged = []
    for nid in G.nodes:
        if G.nodes[nid].get("file_path") == file_b:
            G.nodes[nid]["file_path"] = file_a
            merged.append(G.nodes[nid].get("name", str(nid)))

    return {
        "operation": "merge",
        "target_file": file_a,
        "merged_file": file_b,
        "merged_symbols": merged,
        "affected": len(merged),
    }


def apply_delete(G: nx.DiGraph, node_ids: list[int]) -> dict:
    """Remove nodes and all their edges from the graph."""
    removed_names = []
    edges_before = G.number_of_edges()
    for nid in node_ids:
        if nid in G:
            removed_names.append(G.nodes[nid].get("name", str(nid)))
            G.remove_node(nid)
    edges_after = G.number_of_edges()

    return {
        "operation": "delete",
        "removed": removed_names,
        "removed_edges": edges_before - edges_after,
        "affected": len(removed_names),
    }


# ---------------------------------------------------------------------------
# Resolution helper
# ---------------------------------------------------------------------------


def resolve_target(G: nx.DiGraph, conn, target_str: str) -> tuple:
    """Resolve a CLI argument to node IDs in the graph.

    Returns (node_ids, label) where node_ids is a list of ints.
    """
    from roam.commands.resolve import find_symbol

    # Try symbol lookup first
    row = find_symbol(conn, target_str)
    if row and row["id"] in G:
        name = row["name"] if "name" in row.keys() else target_str
        return ([row["id"]], name)

    # Try file path match
    target_norm = target_str.replace("\\", "/")
    file_nodes = [nid for nid in G.nodes if (G.nodes[nid].get("file_path") or "").replace("\\", "/") == target_norm]
    if file_nodes:
        return (file_nodes, target_str)

    # Try partial file path match
    file_nodes = [nid for nid in G.nodes if target_norm in (G.nodes[nid].get("file_path") or "").replace("\\", "/")]
    if file_nodes:
        return (file_nodes, target_str)

    return ([], "not found")
