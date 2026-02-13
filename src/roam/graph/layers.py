"""Topological layer detection and violation finding."""

from __future__ import annotations

import sqlite3

import networkx as nx


def detect_layers(G: nx.DiGraph) -> dict[int, int]:
    """Assign a layer number to every node using longest-path from sources.

    * Nodes with no incoming edges get layer 0.
    * Each other node's layer = max(layer of predecessors) + 1.
    * Cycles are handled by first condensing the graph into a DAG of SCCs.

    Returns ``{node_id: layer_number}``.
    """
    if len(G) == 0:
        return {}

    # Condense cycles into super-nodes to get a DAG
    condensation = nx.condensation(G)
    # condensation.graph["mapping"] maps original node -> SCC index
    node_to_scc: dict[int, int] = condensation.graph["mapping"]

    # Compute layers on the condensed DAG
    scc_layers: dict[int, int] = {}
    for scc_node in nx.topological_sort(condensation):
        preds = list(condensation.predecessors(scc_node))
        if not preds:
            scc_layers[scc_node] = 0
        else:
            scc_layers[scc_node] = max(scc_layers[p] for p in preds) + 1

    # Map back to original nodes
    layers: dict[int, int] = {}
    for node in G.nodes:
        scc_id = node_to_scc[node]
        layers[node] = scc_layers[scc_id]

    return layers


def layer_balance(layers: dict[int, int]) -> float:
    """Compute Gini coefficient of layer sizes as a balance metric.

    Returns a value in [0, 1] where 0 = perfectly balanced (all layers
    have the same number of nodes) and 1 = maximally imbalanced (all
    nodes in one layer).

    The Gini coefficient is a standard inequality measure from economics
    (Gini, 1912) applied here to architectural layer distribution.
    """
    if not layers:
        return 0.0
    from collections import Counter
    sizes = sorted(Counter(layers.values()).values())
    n = len(sizes)
    if n <= 1:
        return 0.0
    total = sum(sizes)
    if total == 0:
        return 0.0
    cumulative = 0.0
    weighted_sum = 0.0
    for i, size in enumerate(sizes):
        cumulative += size
        weighted_sum += (2 * (i + 1) - n - 1) * size
    return round(weighted_sum / (n * total), 4)


def find_violations(
    G: nx.DiGraph, layers: dict[int, int]
) -> list[dict]:
    """Find edges that go *upward* from a higher layer to a lower layer.

    In a healthy layered architecture, dependencies should flow downward
    (layer N depends on layer N-1 or lower).  An edge from a node at
    layer L_src to a node at layer L_tgt where L_tgt < L_src is a
    potential violation (a lower layer depending on a higher one).

    Each violation includes a ``severity`` weight proportional to the
    layer distance jumped.  Crossing many layers (e.g., L7 → L1) is
    architecturally worse than a single-layer skip (L2 → L1).

    Returns a list of dicts::

        [{"source": id, "target": id, "source_layer": int, "target_layer": int,
          "layer_distance": int, "severity": float}]
    """
    violations: list[dict] = []
    max_layer = max(layers.values(), default=0) or 1
    for src, tgt in G.edges:
        src_layer = layers.get(src)
        tgt_layer = layers.get(tgt)
        if src_layer is None or tgt_layer is None:
            continue
        if src_layer > tgt_layer:
            distance = src_layer - tgt_layer
            # Severity normalized by max possible distance, so it's in [0, 1]
            severity = round(distance / max_layer, 3)
            violations.append({
                "source": src,
                "target": tgt,
                "source_layer": src_layer,
                "target_layer": tgt_layer,
                "layer_distance": distance,
                "severity": severity,
            })
    return violations


def format_layers(
    layers: dict[int, int], conn: sqlite3.Connection
) -> list[dict]:
    """Annotate layer assignments with symbol metadata.

    Returns a list of dicts sorted by layer::

        [
            {
                "layer": 0,
                "symbols": [
                    {"id": 1, "name": "foo", "kind": "function", "file_path": "..."},
                    ...
                ],
            },
            ...
        ]
    """
    if not layers:
        return []

    all_ids = list(layers.keys())
    lookup: dict[int, dict] = {}
    for i in range(0, len(all_ids), 500):
        batch = all_ids[i:i+500]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT s.id, s.name, s.kind, f.path AS file_path "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({placeholders})",
            batch,
        ).fetchall()
        for sid, name, kind, fpath in rows:
            lookup[sid] = {"id": sid, "name": name, "kind": kind, "file_path": fpath}

    # Group by layer
    layer_groups: dict[int, list[dict]] = {}
    for node_id, layer in layers.items():
        if node_id not in lookup:
            continue
        layer_groups.setdefault(layer, []).append(lookup[node_id])

    result = []
    for layer_num in sorted(layer_groups):
        symbols = sorted(layer_groups[layer_num], key=lambda s: s["name"])
        result.append({"layer": layer_num, "symbols": symbols})
    return result
