"""Community detection for the symbol graph (Leiden → Louvain → greedy)."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict
from typing import TypedDict

import networkx as nx
from networkx.algorithms.community.quality import NotAPartition

from roam.db.connection import batched_in

_ANCHOR_KINDS = {"class", "struct", "interface", "enum", "trait", "module"}


class _SymbolLabelInfo(TypedDict):
    name: str
    kind: str
    pagerank: float
    path: str


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


def _group_cluster_members(clusters: dict[int, int]) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = defaultdict(list)
    for node_id, cid in clusters.items():
        groups[cid].append(node_id)
    return groups


def _fetch_cluster_symbol_info(conn: sqlite3.Connection, symbol_ids: list[int]) -> dict[int, _SymbolLabelInfo]:
    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, f.path, COALESCE(gm.pagerank, 0) as pagerank "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE s.id IN ({ph})",
        symbol_ids,
    )
    return {
        r["id"]: {
            "name": r["name"],
            "kind": r["kind"],
            "pagerank": r["pagerank"],
            "path": r["path"],
        }
        for r in rows
    }


def _short_dir(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] if path else ""


def _cluster_directory_summary(
    members: list[int],
    symbol_info: dict[int, _SymbolLabelInfo],
) -> tuple[Counter[str], str]:
    dirs = [os.path.dirname(symbol_info[m]["path"]).replace("\\", "/") for m in members if m in symbol_info]
    dir_counts = Counter(dirs)
    most_common_dir = dir_counts.most_common(1)[0][0] if dir_counts else ""
    return dir_counts, _short_dir(most_common_dir)


def _is_mega_cluster(member_count: int, total_nodes: int) -> bool:
    return member_count > 100 or (total_nodes > 0 and member_count > total_nodes * 0.4)


def _mega_cluster_label(dir_counts: Counter[str], member_count: int) -> str:
    parts = []
    for directory, count in dir_counts.most_common(3):
        d_short = _short_dir(directory) or "."
        pct = count * 100 / member_count if member_count else 0
        parts.append(f"{d_short} {pct:.0f}%")
    return " + ".join(parts)


def _highest_pagerank_symbol_name(
    members: list[int],
    symbol_info: dict[int, _SymbolLabelInfo],
    *,
    anchor_only: bool,
) -> str | None:
    best_name = None
    best_pr = -1
    for member in members:
        info = symbol_info.get(member)
        if not info or (anchor_only and info["kind"] not in _ANCHOR_KINDS):
            continue
        if info["pagerank"] > best_pr:
            best_pr = info["pagerank"]
            best_name = info["name"]
    return best_name


def _most_common_symbol_name(
    members: list[int],
    symbol_info: dict[int, _SymbolLabelInfo],
    *,
    anchor_only: bool,
) -> str | None:
    names = [
        symbol_info[m]["name"]
        for m in members
        if m in symbol_info and (not anchor_only or symbol_info[m]["kind"] in _ANCHOR_KINDS)
    ]
    return Counter(names).most_common(1)[0][0] if names else None


def _best_cluster_symbol_name(
    members: list[int],
    symbol_info: dict[int, _SymbolLabelInfo],
) -> str | None:
    candidate_sources = (
        (_highest_pagerank_symbol_name, True),
        (_highest_pagerank_symbol_name, False),
        (_most_common_symbol_name, True),
        (_most_common_symbol_name, False),
    )
    for choose_symbol_name, anchor_only in candidate_sources:
        best_name = choose_symbol_name(members, symbol_info, anchor_only=anchor_only)
        if best_name is not None:
            return best_name
    return None


def _format_cluster_label(best_name: str | None, short_dir: str, cid: int) -> str:
    if best_name and short_dir:
        return f"{short_dir}/{best_name}"
    if best_name:
        return best_name
    if short_dir:
        return short_dir
    return f"cluster-{cid}"


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

    groups = _group_cluster_members(clusters)
    symbol_info = _fetch_cluster_symbol_info(conn, list(clusters.keys()))
    labels: dict[int, str] = {}
    total_nodes = len(clusters)

    for cid, members in groups.items():
        dir_counts, short_dir = _cluster_directory_summary(members, symbol_info)
        if _is_mega_cluster(len(members), total_nodes) and len(dir_counts) > 1:
            labels[cid] = _mega_cluster_label(dir_counts, len(members))
            continue

        best_name = _best_cluster_symbol_name(members, symbol_info)
        labels[cid] = _format_cluster_label(best_name, short_dir, cid)
    return labels


def _cluster_groups_by_assignment(cluster_map: dict[int, int]) -> dict[int, set[int]]:
    groups: dict[int, set[int]] = defaultdict(set)
    for node_id, cid in cluster_map.items():
        groups[cid].add(node_id)
    return groups


def _complete_partition_for_modularity(undirected: nx.Graph, cluster_map: dict[int, int]) -> list[set[int]]:
    """Repair sparse cluster assignments into a full NetworkX partition."""
    node_set = set(undirected.nodes())
    partition_groups: dict[int, set[int]] = defaultdict(set)
    for node_id, cid in cluster_map.items():
        if node_id in node_set:
            partition_groups[cid].add(node_id)

    covered_nodes: set[int] = set()
    for members in partition_groups.values():
        covered_nodes |= members
    return list(partition_groups.values()) + [{n} for n in node_set - covered_nodes]


def _modularity_preserving_partition_completeness(undirected: nx.Graph, cluster_map: dict[int, int]) -> float:
    """Compute Q while preserving NetworkX's complete-partition invariant."""
    communities = _complete_partition_for_modularity(undirected, cluster_map)
    try:
        q = (
            nx.community.modularity(undirected, communities)
            if communities and undirected.number_of_edges() > 0
            else 0.0
        )
    except NotAPartition:
        q = 0.0
    return round(q, 4)


def _conductance_preserving_sparse_assignments(
    undirected: nx.Graph,
    cluster_map: dict[int, int],
    groups: dict[int, set[int]],
) -> dict[int, float]:
    """Compute conductance without filling sparse cluster assignments."""
    edge_count = undirected.number_of_edges()
    two_e = 2 * edge_count
    vol: dict[int, int] = defaultdict(int)
    cut: dict[int, int] = defaultdict(int)
    for u, v in undirected.edges():
        cu = cluster_map.get(u)
        cv = cluster_map.get(v)
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
    return per_cluster


def _mean_conductance(per_cluster: dict[int, float]) -> float:
    conductances = list(per_cluster.values())
    return round(sum(conductances) / len(conductances), 4) if conductances else 0.0


def _compute_cluster_quality(G: nx.DiGraph, cluster_map: dict[int, int]) -> dict:
    """Shared implementation for cluster-quality metrics.

    Centralises the correct-and-fast computation used by both
    ``cluster_quality`` and ``fingerprint._fast_cluster_quality`` so the
    two call sites stay byte-identical without code duplication.

    Returns a dict with:
    - ``modularity``: Newman's modularity Q-score [-0.5, 1.0].
      Q > 0.3 indicates meaningful community structure.
      Reference: Newman (2004).
    - ``per_cluster``: dict mapping cluster_id to its conductance.
      Conductance phi(S) = cut(S, S_bar) / min(vol(S), vol(S_bar)).
      Lower = tighter cluster.  Reference: Yang & Leskovec.
    - ``mean_conductance``: average conductance across all clusters.
    """
    if not cluster_map or len(G) == 0:
        return {"modularity": 0.0, "per_cluster": {}, "mean_conductance": 0.0}

    undirected = G.to_undirected()

    # Build community list-of-sets for NetworkX. `groups` (the real cluster
    # assignment) drives per-cluster conductance below and is reported as-is.
    groups = _cluster_groups_by_assignment(cluster_map)

    # Modularity Q-score (Newman 2004). nx.community.modularity requires a
    # PARTITION covering EVERY node of `undirected`. The clusters table routinely
    # omits nodes (isolated symbols never assigned a community, or nodes added to
    # the graph after the clustering pass), so passing the raw cluster groups
    # raised `NotAPartition` — and the bare `except` silently floored Q to 0.0.
    # That made a strongly-modular codebase (real Q ~0.8) report 0.0, i.e. "no
    # community structure" (Newman: Q>0.3 is meaningful). Build a valid partition
    # for the Q computation: in-graph clustered nodes by community + one singleton
    # community per uncovered in-graph node. (conductance below is edge-driven and
    # unaffected; it keys off `cluster_map` per-edge, so it needs no partition.)
    modularity = _modularity_preserving_partition_completeness(undirected, cluster_map)

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
    per_cluster = _conductance_preserving_sparse_assignments(undirected, cluster_map, groups)

    return {
        "modularity": modularity,
        "per_cluster": per_cluster,
        "mean_conductance": _mean_conductance(per_cluster),
    }


def cluster_quality(G: nx.DiGraph, clusters: dict[int, int]) -> dict:
    """Compute quality metrics for the detected community structure.

    Delegates to the shared implementation so ``fingerprint`` can reuse
    the same correct-and-fast algorithm without duplication.
    """
    return _compute_cluster_quality(G, clusters)


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
