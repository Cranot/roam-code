"""Graph-Isomorphism Transfer: topology fingerprint extraction and comparison."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict

import networkx as nx


def _gini_coefficient(values: list[float]) -> float:
    """Compute the Gini coefficient for a list of non-negative values.

    Returns a value in [0, 1] where 0 = perfectly uniform and 1 = maximally
    concentrated.  Uses the mean-difference formulation.
    """
    if not values or len(values) < 2:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    cumsum = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(sorted_vals):
        cumsum += v
        weighted_sum += (2 * (i + 1) - n - 1) * v
    return round(weighted_sum / (n * total), 6)


def _hub_bridge_ratio(G: nx.DiGraph) -> float:
    """Ratio of hub nodes in the graph.

    A hub is defined as a node whose degree exceeds median + 2*MAD
    (Median Absolute Deviation).  This is a robust outlier definition.
    """
    if len(G) < 3:
        return 0.0
    degrees = sorted(G.degree(n) for n in G)
    n = len(degrees)
    median_deg = degrees[n // 2]
    deviations = sorted(abs(d - median_deg) for d in degrees)
    mad = deviations[n // 2]
    threshold = median_deg + 2 * max(mad, 1)
    hub_count = sum(1 for d in degrees if d > threshold)
    return round(hub_count / n, 4) if n > 0 else 0.0


def _dependency_direction(G: nx.DiGraph, layers: dict[int, int]) -> str:
    """Determine whether the dependency graph is top-down or bottom-up.

    Counts edges flowing from lower layers to higher layers (top-down)
    vs. higher to lower (bottom-up).  The majority direction wins.
    """
    down = 0  # lower-layer -> higher-layer (normal dependency direction)
    up = 0    # higher-layer -> lower-layer (reverse)
    for src, tgt in G.edges():
        src_layer = layers.get(src)
        tgt_layer = layers.get(tgt)
        if src_layer is None or tgt_layer is None:
            continue
        if src_layer < tgt_layer:
            down += 1
        elif src_layer > tgt_layer:
            up += 1
    return "top-down" if down >= up else "bottom-up"


def _classify_cluster_pattern(size_pct: float, conductance: float) -> str:
    """Classify a cluster into an architectural pattern label."""
    if size_pct > 40:
        return "monolith"
    if conductance < 0.1:
        return "island"
    if conductance > 0.5:
        return "leaky"
    return "module"


def compute_fingerprint(conn: sqlite3.Connection, G: nx.DiGraph) -> dict:
    """Extract a topology fingerprint from the indexed graph.

    The fingerprint captures the structural signature of the codebase
    without any source code, enabling cross-repo comparison and
    architectural pattern transfer.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open roam database connection.
    G : nx.DiGraph
        Symbol graph built from the database.

    Returns
    -------
    dict
        Fingerprint with topology, clusters, hub_bridge_ratio,
        pagerank_gini, dependency_direction, and antipatterns sections.
    """
    from roam.graph.cycles import find_cycles, algebraic_connectivity
    from roam.graph.clusters import detect_clusters, cluster_quality, label_clusters
    from roam.graph.layers import detect_layers
    from roam.graph.pagerank import compute_pagerank

    n_nodes = len(G)

    # -- Layers --
    layers = detect_layers(G)
    max_layer = max(layers.values(), default=0)
    n_layers = max_layer + 1 if layers else 0

    # Layer distribution (% of nodes per layer)
    layer_counts: dict[int, int] = Counter(layers.values())
    layer_distribution = []
    for i in range(n_layers):
        pct = round(layer_counts.get(i, 0) * 100 / n_nodes, 1) if n_nodes > 0 else 0.0
        layer_distribution.append(pct)

    # -- Algebraic connectivity (Fiedler) --
    fiedler = algebraic_connectivity(G)

    # -- Clusters & modularity --
    cluster_map = detect_clusters(G)
    quality = cluster_quality(G, cluster_map)
    modularity = quality["modularity"]

    # -- Tangle ratio: fraction of nodes in non-trivial SCCs --
    sccs = find_cycles(G, min_size=2)
    tangled_nodes = sum(len(scc) for scc in sccs)
    tangle_ratio = round(tangled_nodes / n_nodes, 4) if n_nodes > 0 else 0.0

    # -- Dependency direction --
    dep_direction = _dependency_direction(G, layers)

    # -- Cluster summaries --
    cluster_labels = label_clusters(cluster_map, conn)
    groups: dict[int, list[int]] = defaultdict(list)
    for nid, cid in cluster_map.items():
        groups[cid].append(nid)

    # Determine which layer each cluster mostly lives in
    cluster_summaries = []
    for cid, members in sorted(groups.items(), key=lambda x: -len(x[1])):
        size_pct = round(len(members) * 100 / n_nodes, 1) if n_nodes > 0 else 0.0
        conductance = quality["per_cluster"].get(cid, 0.0)
        # Most common layer for this cluster
        member_layers = [layers.get(m, 0) for m in members]
        majority_layer = Counter(member_layers).most_common(1)[0][0] if member_layers else 0

        # Roles distribution (kind counts)
        roles: dict[str, int] = Counter()
        for m in members:
            data = G.nodes.get(m, {})
            kind = data.get("kind", "unknown")
            roles[kind] += 1

        pattern = _classify_cluster_pattern(size_pct, conductance)

        cluster_summaries.append({
            "label": cluster_labels.get(cid, f"cluster-{cid}"),
            "layer": majority_layer,
            "size_pct": size_pct,
            "conductance": conductance,
            "roles": dict(roles),
            "pattern": pattern,
        })

    # -- PageRank Gini --
    pr = compute_pagerank(G)
    pagerank_gini = _gini_coefficient(list(pr.values())) if pr else 0.0

    # -- Hub/bridge ratio --
    hb_ratio = _hub_bridge_ratio(G)

    # -- Anti-patterns --
    # God objects: nodes with degree > 2 * average degree
    avg_degree = (sum(G.degree(n) for n in G) / n_nodes) if n_nodes > 0 else 0
    god_threshold = max(avg_degree * 2, 5)
    god_objects = sum(1 for n in G if G.degree(n) > god_threshold)

    # Cyclic clusters: SCCs that span multiple clusters
    cyclic_clusters = 0
    for scc in sccs:
        scc_cluster_ids = {cluster_map.get(n) for n in scc if n in cluster_map}
        scc_cluster_ids.discard(None)
        if len(scc_cluster_ids) > 1:
            cyclic_clusters += 1

    # -- Assemble fingerprint --
    fingerprint = {
        "topology": {
            "layers": n_layers,
            "layer_distribution": layer_distribution,
            "fiedler": fiedler,
            "modularity": modularity,
            "tangle_ratio": tangle_ratio,
        },
        "clusters": cluster_summaries,
        "hub_bridge_ratio": hb_ratio,
        "pagerank_gini": pagerank_gini,
        "dependency_direction": dep_direction,
        "antipatterns": {
            "god_objects": god_objects,
            "cyclic_clusters": cyclic_clusters,
        },
    }
    return fingerprint


def compare_fingerprints(fp1: dict, fp2: dict) -> dict:
    """Compute a vector distance between two fingerprints.

    Parameters
    ----------
    fp1 : dict
        First fingerprint (e.g. from this repo).
    fp2 : dict
        Second fingerprint (e.g. loaded from a file).

    Returns
    -------
    dict
        Comparison result with per-metric deltas, euclidean distance,
        and an overall similarity score in [0, 1].
    """
    t1 = fp1.get("topology", {})
    t2 = fp2.get("topology", {})

    # Metrics to compare with normalization ranges
    metrics = {
        "layers": {"v1": t1.get("layers", 0), "v2": t2.get("layers", 0), "max_range": 20},
        "modularity": {"v1": t1.get("modularity", 0), "v2": t2.get("modularity", 0), "max_range": 1.0},
        "fiedler": {"v1": t1.get("fiedler", 0), "v2": t2.get("fiedler", 0), "max_range": 1.0},
        "tangle_ratio": {"v1": t1.get("tangle_ratio", 0), "v2": t2.get("tangle_ratio", 0), "max_range": 1.0},
        "hub_bridge_ratio": {"v1": fp1.get("hub_bridge_ratio", 0), "v2": fp2.get("hub_bridge_ratio", 0), "max_range": 1.0},
        "pagerank_gini": {"v1": fp1.get("pagerank_gini", 0), "v2": fp2.get("pagerank_gini", 0), "max_range": 1.0},
        "god_objects": {
            "v1": fp1.get("antipatterns", {}).get("god_objects", 0),
            "v2": fp2.get("antipatterns", {}).get("god_objects", 0),
            "max_range": 50,
        },
        "cyclic_clusters": {
            "v1": fp1.get("antipatterns", {}).get("cyclic_clusters", 0),
            "v2": fp2.get("antipatterns", {}).get("cyclic_clusters", 0),
            "max_range": 20,
        },
    }

    per_metric = {}
    squared_diffs = []
    for name, m in metrics.items():
        v1 = float(m["v1"])
        v2 = float(m["v2"])
        delta = round(v1 - v2, 6)
        max_range = float(m["max_range"])
        normalized_diff = abs(delta) / max_range if max_range > 0 else 0.0
        squared_diffs.append(normalized_diff ** 2)
        direction = "higher" if delta > 0 else ("lower" if delta < 0 else "same")
        per_metric[name] = {
            "this": v1,
            "other": v2,
            "delta": delta,
            "direction": direction,
        }

    # Euclidean distance in normalized space
    euclidean = round(math.sqrt(sum(squared_diffs) / len(squared_diffs)), 4) if squared_diffs else 0.0

    # Similarity score: 1 - distance, clamped to [0, 1]
    similarity = round(max(0.0, min(1.0, 1.0 - euclidean)), 4)

    return {
        "similarity": similarity,
        "euclidean_distance": euclidean,
        "per_metric": per_metric,
        "direction_match": fp1.get("dependency_direction") == fp2.get("dependency_direction"),
    }
