"""Louvain community detection for the symbol graph."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict

import networkx as nx

from roam.db.connection import batched_in


def detect_clusters(G: nx.DiGraph) -> dict[int, int]:
    """Detect communities using Louvain on the undirected projection of *G*.

    Falls back to greedy modularity if Louvain is unavailable.
    Returns ``{node_id: cluster_id}``.
    """
    if len(G) == 0:
        return {}

    undirected = G.to_undirected()

    # Remove isolates -- community detection on disconnected singletons just
    # assigns each its own community, which is not useful.
    communities: list[set[int]] = []
    try:
        communities = list(nx.community.louvain_communities(undirected, seed=42))
    except (AttributeError, TypeError):
        # NetworkX < 3.1 or missing optional dependency
        communities = list(nx.community.greedy_modularity_communities(undirected))

    mapping: dict[int, int] = {}
    for cluster_id, members in enumerate(communities):
        for node in members:
            mapping[node] = cluster_id
    return mapping


def label_clusters(
    clusters: dict[int, int], conn: sqlite3.Connection
) -> dict[int, str]:
    """Generate human-readable labels for clusters.

    Strategy:
    1. Find the majority directory for the cluster.
    2. Pick the best representative symbol, preferring architectural
       anchors (class/struct/interface/enum) over functions/variables,
       breaking ties by PageRank.
    3. Label = ``dir/Symbol`` when both exist, or just the directory.

    Returns ``{cluster_id: label}``.
    """
    if not clusters:
        return {}

    # Architectural kinds are better anchors for labeling
    _ANCHOR_KINDS = {"class", "struct", "interface", "enum", "trait", "module"}

    # Group symbols by cluster
    groups: dict[int, list[int]] = defaultdict(list)
    for node_id, cid in clusters.items():
        groups[cid].append(node_id)

    # Fetch file paths, kind, and pagerank for all symbols in one query
    all_ids = list(clusters.keys())
    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, f.path, COALESCE(gm.pagerank, 0) as pagerank "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE s.id IN ({ph})",
        all_ids,
    )
    id_to_path: dict[int, str] = {}
    id_to_info: dict[int, dict] = {}
    for r in rows:
        id_to_path[r["id"]] = r["path"]
        id_to_info[r["id"]] = {
            "name": r["name"], "kind": r["kind"],
            "pagerank": r["pagerank"],
        }

    labels: dict[int, str] = {}
    total_nodes = len(clusters)

    for cid, members in groups.items():
        # Determine directory distribution
        dirs = [
            os.path.dirname(id_to_path[m]).replace("\\", "/")
            for m in members if m in id_to_path
        ]
        dir_counts = Counter(dirs) if dirs else Counter()
        most_common_dir = dir_counts.most_common(1)[0][0] if dir_counts else ""
        short_dir = most_common_dir.rstrip("/").rsplit("/", 1)[-1] if most_common_dir else ""

        # Large clusters (>100 symbols or >40% of graph): use directory breakdown
        is_mega = len(members) > 100 or (total_nodes > 0 and len(members) > total_nodes * 0.4)
        if is_mega and len(dir_counts) > 1:
            top_dirs = dir_counts.most_common(3)
            total = sum(c for _, c in top_dirs)
            parts = []
            for d, c in top_dirs:
                d_short = d.rstrip("/").rsplit("/", 1)[-1] if d else "."
                pct = c * 100 / len(members) if members else 0
                parts.append(f"{d_short} {pct:.0f}%")
            labels[cid] = " + ".join(parts)
            continue

        # Pick best representative symbol:
        # 1st pass: anchor kinds (class/struct/interface) by PageRank
        # 2nd pass: any kind by PageRank
        best_name = None
        best_pr = -1
        for m in members:
            info = id_to_info.get(m)
            if info and info["kind"] in _ANCHOR_KINDS and info["pagerank"] > best_pr:
                best_pr = info["pagerank"]
                best_name = info["name"]

        if best_name is None:
            best_pr = -1
            for m in members:
                info = id_to_info.get(m)
                if info and info["pagerank"] > best_pr:
                    best_pr = info["pagerank"]
                    best_name = info["name"]

        # Fallback: use most common anchor name
        if best_name is None:
            stems = [id_to_info[m]["name"] for m in members
                     if m in id_to_info and id_to_info[m]["kind"] in _ANCHOR_KINDS]
            if not stems:
                stems = [id_to_info[m]["name"] for m in members if m in id_to_info]
            if stems:
                best_name = Counter(stems).most_common(1)[0][0]

        # Build label
        if best_name and short_dir:
            labels[cid] = f"{short_dir}/{best_name}"
        elif best_name:
            labels[cid] = best_name
        elif short_dir:
            labels[cid] = short_dir
        else:
            labels[cid] = f"cluster-{cid}"
    return labels


def store_clusters(
    conn: sqlite3.Connection,
    clusters: dict[int, int],
    labels: dict[int, str],
) -> int:
    """Persist cluster assignments into the ``clusters`` table.

    Returns the number of rows written.
    """
    if not clusters:
        return 0

    rows = [
        (node_id, cid, labels.get(cid, f"cluster-{cid}"))
        for node_id, cid in clusters.items()
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO clusters (symbol_id, cluster_id, cluster_label) "
        "VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def compare_with_directories(conn: sqlite3.Connection) -> list[dict]:
    """Compare detected clusters with the directory tree.

    A *mismatch* means symbols in the same cluster live in different
    directories.  Returns a list of dicts::

        [
            {
                "cluster_id": 0,
                "cluster_label": "utils",
                "directories": ["src/utils", "src/core"],
                "mismatch_count": 5,
            },
            ...
        ]
    """
    rows = conn.execute(
        "SELECT c.cluster_id, c.cluster_label, f.path "
        "FROM clusters c "
        "JOIN symbols s ON c.symbol_id = s.id "
        "JOIN files f ON s.file_id = f.id"
    ).fetchall()

    if not rows:
        return []

    cluster_dirs: dict[int, dict] = defaultdict(lambda: {"label": "", "dirs": []})
    for cid, label, path in rows:
        d = os.path.dirname(path).replace("\\", "/")
        entry = cluster_dirs[cid]
        entry["label"] = label
        entry["dirs"].append(d)

    result = []
    for cid, info in sorted(cluster_dirs.items()):
        unique_dirs = sorted(set(info["dirs"]))
        if len(unique_dirs) > 1:
            # Count symbols that are NOT in the majority directory
            dir_counts = Counter(info["dirs"])
            majority_count = dir_counts.most_common(1)[0][1]
            mismatch_count = len(info["dirs"]) - majority_count
            result.append({
                "cluster_id": cid,
                "cluster_label": info["label"],
                "directories": unique_dirs,
                "mismatch_count": mismatch_count,
            })

    result.sort(key=lambda r: r["mismatch_count"], reverse=True)
    return result
