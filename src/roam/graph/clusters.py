"""Community detection for the symbol graph (Leiden → Louvain → greedy)."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict

import networkx as nx

from roam.db.connection import batched_in


def _try_leiden_communities(undirected: nx.Graph, out: list[set[int]]) -> bool:
    """Run Leiden via the optional ``leidenalg`` + ``igraph`` extras.

    Returns ``True`` and fills *out* in place when Leiden succeeded, else
    ``False`` so the caller can fall back to NetworkX Louvain. Leiden is
    strictly better than Louvain per Traag et al. 2019 (Nature Sci. Rep.) —
    no badly-connected communities, fully deterministic with the seed.

    Install with::

        pip install "roam-code[leiden]"

    Skip with ``ROAM_LEIDEN=0`` (e.g., for offline / ABI-pin environments
    where the C extension fails to load).
    """
    if os.environ.get("ROAM_LEIDEN", "1") == "0":
        return False
    try:
        import igraph as ig  # type: ignore
        import leidenalg  # type: ignore
    except ImportError:
        return False
    # Build an igraph from the undirected NetworkX graph. Map node ids
    # so we can translate back. Edge weights default to 1.0.
    node_list = list(undirected.nodes())
    index_for: dict[int, int] = {n: i for i, n in enumerate(node_list)}
    edges = [(index_for[u], index_for[v]) for u, v in undirected.edges() if u in index_for and v in index_for]

    leiden_runtime_errors: tuple[type[BaseException], ...] = (MemoryError, RuntimeError, ValueError)
    igraph_internal_error = getattr(ig, "InternalError", None)
    if isinstance(igraph_internal_error, type) and issubclass(igraph_internal_error, BaseException):
        leiden_runtime_errors = (*leiden_runtime_errors, igraph_internal_error)

    try:
        ig_graph = ig.Graph(n=len(node_list), edges=edges, directed=False)
        partition = leidenalg.find_partition(
            ig_graph,
            leidenalg.ModularityVertexPartition,
            seed=42,
        )
        for community in partition:
            out.append({node_list[idx] for idx in community})
        return True
    except leiden_runtime_errors:
        # leidenalg / igraph ABI mismatch or memory pressure — fall back.
        out.clear()
        return False


def detect_clusters(G: nx.DiGraph) -> dict[int, int]:
    """Detect communities on the undirected projection of *G*.

    Algorithm preference: **Leiden** (via optional ``[leiden]`` extra) →
    seeded **Louvain** (NetworkX) → **greedy modularity** (NetworkX
    classic, last-resort).

    Returns ``{node_id: cluster_id}``.
    """
    if len(G) == 0:
        return {}

    undirected = G.to_undirected()

    # Remove isolates -- community detection on disconnected singletons just
    # assigns each its own community, which is not useful.
    #
    # Algorithm preference (best → fallback):
    #   1. Leiden (via leidenalg + igraph extras): no badly-connected
    #      communities, fully deterministic with seed. Strictly better than
    #      Louvain per Traag et al. 2019 (Nature Sci. Rep.).
    #   2. Louvain (NetworkX) with seed=42: deterministic but may produce
    #      badly-connected communities — the Leiden paper's main critique.
    #   3. Greedy modularity: very old NetworkX fallback for offline envs.
    communities: list[set[int]] = []
    if not _try_leiden_communities(undirected, communities):
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


def label_clusters(clusters: dict[int, int], conn: sqlite3.Connection) -> dict[int, str]:
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
            "name": r["name"],
            "kind": r["kind"],
            "pagerank": r["pagerank"],
        }

    labels: dict[int, str] = {}
    total_nodes = len(clusters)

    for cid, members in groups.items():
        # Determine directory distribution
        dirs = [os.path.dirname(id_to_path[m]).replace("\\", "/") for m in members if m in id_to_path]
        dir_counts = Counter(dirs) if dirs else Counter()
        most_common_dir = dir_counts.most_common(1)[0][0] if dir_counts else ""
        short_dir = most_common_dir.rstrip("/").rsplit("/", 1)[-1] if most_common_dir else ""

        # Large clusters (>100 symbols or >40% of graph): use directory breakdown
        is_mega = len(members) > 100 or (total_nodes > 0 and len(members) > total_nodes * 0.4)
        if is_mega and len(dir_counts) > 1:
            top_dirs = dir_counts.most_common(3)
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
            stems = [
                id_to_info[m]["name"] for m in members if m in id_to_info and id_to_info[m]["kind"] in _ANCHOR_KINDS
            ]
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


def cluster_quality(G: nx.DiGraph, clusters: dict[int, int]) -> dict:
    """Compute quality metrics for the detected community structure.

    Returns a dict with:
    - ``modularity``: Newman's modularity Q-score [-0.5, 1.0].
      Q > 0.3 indicates meaningful community structure.
      Reference: Newman (2004).
    - ``per_cluster``: dict mapping cluster_id to its conductance.
      Conductance phi(S) = cut(S, S_bar) / min(vol(S), vol(S_bar)).
      Lower = tighter cluster.  Reference: Yang & Leskovec.
    - ``mean_conductance``: average conductance across all clusters.
    """
    if not clusters or len(G) == 0:
        return {"modularity": 0.0, "per_cluster": {}, "mean_conductance": 0.0}

    undirected = G.to_undirected()
    node_set = set(undirected.nodes())

    # Build community list-of-sets for NetworkX. `groups` (the real cluster
    # assignment) drives per-cluster conductance below and is reported as-is.
    groups: dict[int, set] = defaultdict(set)
    for node_id, cid in clusters.items():
        groups[cid].add(node_id)

    # Modularity Q-score (Newman 2004). nx.community.modularity requires a
    # PARTITION covering EVERY node of `undirected`. The clusters table routinely
    # omits nodes (isolated symbols never assigned a community, or nodes added to
    # the graph after the clustering pass), so passing the raw cluster groups
    # raised `NotAPartition` — and the bare `except` silently floored Q to 0.0.
    # That made a strongly-modular codebase (real Q ~0.8) report 0.0, i.e. "no
    # community structure" (Newman: Q>0.3 is meaningful). Build a valid partition
    # for the Q computation: in-graph clustered nodes by community + one singleton
    # community per uncovered in-graph node. (conductance below is edge-driven and
    # unaffected; it keys off `clusters` per-edge, so it needs no partition.)
    partition_groups: dict[int, set] = defaultdict(set)
    for node_id, cid in clusters.items():
        if node_id in node_set:
            partition_groups[cid].add(node_id)
    covered_nodes: set = set()
    for members in partition_groups.values():
        covered_nodes |= members
    modularity_communities = list(partition_groups.values()) + [{n} for n in node_set - covered_nodes]

    # Q-score over the repaired partition. The catch is a defensive floor for a
    # genuine NetworkX failure only — the NotAPartition path that previously hid
    # the real value is now structurally impossible.
    try:
        q = nx.community.modularity(undirected, modularity_communities) if modularity_communities else 0.0
    except Exception:
        q = 0.0

    # Per-cluster conductance: phi(S) = cut(S, S_bar) / min(vol(S), vol(S_bar))
    #
    # Single-pass formulation (output-identical to the prior per-cluster
    # nested edge scan, just O(E + C) instead of O(E * C)):
    #   vol(S)   = sum over edges of (#endpoints in S)
    #   vol(Sbar)= 2 * E - vol(S)   -- every endpoint is in S or not
    #   cut(S)   = #edges with exactly one endpoint in S
    # The old loop incremented vol_s/vol_sbar per endpoint and cut per
    # boundary edge; accumulating those counters per cluster in ONE edge
    # walk reproduces the exact same integers (self-loops add 2 to vol(S)
    # and 0 to cut, identically in both forms).
    edge_count = undirected.number_of_edges()
    two_e = 2 * edge_count
    vol: dict[int, int] = defaultdict(int)
    cut: dict[int, int] = defaultdict(int)
    for u, v in undirected.edges():
        cu = clusters.get(u)
        cv = clusters.get(v)
        if cu is not None:
            vol[cu] += 1
        if cv is not None:
            vol[cv] += 1
        if cu != cv:
            if cu is not None:
                cut[cu] += 1
            if cv is not None:
                cut[cv] += 1

    per_cluster: dict[int, float] = {}
    for cid, members in groups.items():
        if len(members) < 2:
            per_cluster[cid] = 0.0
            continue
        vol_s = vol.get(cid, 0)
        vol_sbar = two_e - vol_s
        min_vol = vol_sbar if vol_sbar < vol_s else vol_s
        per_cluster[cid] = round(cut.get(cid, 0) / min_vol, 4) if min_vol > 0 else 0.0

    conductances = list(per_cluster.values())
    mean_cond = round(sum(conductances) / len(conductances), 4) if conductances else 0.0

    return {
        "modularity": round(q, 4),
        "per_cluster": per_cluster,
        "mean_conductance": mean_cond,
    }


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

    rows = [(node_id, cid, labels.get(cid, f"cluster-{cid}")) for node_id, cid in clusters.items()]
    conn.executemany(
        "INSERT OR REPLACE INTO clusters (symbol_id, cluster_id, cluster_label) VALUES (?, ?, ?)",
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
            result.append(
                {
                    "cluster_id": cid,
                    "cluster_label": info["label"],
                    "directories": unique_dirs,
                    "mismatch_count": mismatch_count,
                }
            )

    result.sort(key=lambda r: r["mismatch_count"], reverse=True)
    return result
