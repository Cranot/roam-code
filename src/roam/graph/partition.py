"""Graph partitioning engine for multi-agent swarm orchestration."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict

import networkx as nx

from roam.db.connection import batched_in
from roam.graph.clusters import detect_clusters, cluster_quality
from roam.graph.layers import detect_layers


def partition_for_agents(
    G: nx.DiGraph,
    conn: sqlite3.Connection,
    n_agents: int,
    target_files: list[str] | None = None,
) -> dict:
    """Partition the symbol graph into non-overlapping agent work zones.

    Algorithm:
    1. If *target_files* is given, extract the subgraph for those files.
    2. Run Louvain community detection.
    3. Merge or split clusters to match *n_agents*.
    4. For each partition compute write files, read-only files,
       shared interfaces, and contracts.

    Returns a dict with ``agents``, ``merge_order``, ``conflict_probability``,
    and ``shared_interfaces``.
    """
    if n_agents < 1:
        n_agents = 1

    # ── 1. Scope the graph ────────────────────────────────────────
    if target_files:
        target_set = set(target_files)
        keep = {
            n for n in G.nodes
            if G.nodes[n].get("file_path", "") in target_set
        }
        if not keep:
            # Try prefix matching (directories)
            keep = {
                n for n in G.nodes
                if any(
                    G.nodes[n].get("file_path", "").startswith(t)
                    for t in target_set
                )
            }
        if keep:
            G = G.subgraph(keep).copy()

    if len(G) == 0:
        return _empty_result(n_agents)

    # ── 2. Detect clusters ────────────────────────────────────────
    cluster_map = detect_clusters(G)
    if not cluster_map:
        # Assign every node to cluster 0
        cluster_map = {n: 0 for n in G.nodes}

    # Group nodes by cluster
    groups: dict[int, set[int]] = defaultdict(set)
    for node_id, cid in cluster_map.items():
        groups[cid].add(node_id)

    # ── 3. Adjust cluster count to match n_agents ─────────────────
    partitions = _adjust_cluster_count(G, groups, n_agents)

    # ── 4. Build agent descriptors ────────────────────────────────
    agents = _build_agent_descriptors(G, conn, partitions)

    # ── 5. Shared interfaces ──────────────────────────────────────
    shared_interfaces = _find_shared_interfaces(G, conn, partitions)

    # ── 6. Conflict probability ───────────────────────────────────
    conflict_prob = compute_conflict_probability(G, partitions)

    # ── 7. Merge order ────────────────────────────────────────────
    merge_order = compute_merge_order(G, partitions)

    # Count write conflicts (files appearing in multiple write lists)
    all_write_files: list[str] = []
    for a in agents:
        all_write_files.extend(a["write_files"])
    file_counts = Counter(all_write_files)
    write_conflicts = sum(1 for c in file_counts.values() if c > 1)

    return {
        "agents": agents,
        "merge_order": merge_order,
        "conflict_probability": round(conflict_prob, 4),
        "shared_interfaces": [si["symbol"] for si in shared_interfaces],
        "write_conflicts": write_conflicts,
    }


def compute_conflict_probability(
    G: nx.DiGraph, partitions: list[dict[str, set[int]]]
) -> float:
    """Ratio of cross-partition edges to total edges."""
    if len(G.edges) == 0:
        return 0.0

    # Build node -> partition index
    node_part: dict[int, int] = {}
    for idx, p in enumerate(partitions):
        for n in p["nodes"]:
            node_part[n] = idx

    cross = 0
    total = 0
    for u, v in G.edges:
        if u in node_part and v in node_part:
            total += 1
            if node_part[u] != node_part[v]:
                cross += 1

    return cross / total if total > 0 else 0.0


def compute_merge_order(
    G: nx.DiGraph, partitions: list[dict[str, set[int]]]
) -> list[int]:
    """Topological sort of partitions by dependency direction (leaves first).

    Returns a list of 1-based agent IDs in merge order.
    """
    if not partitions:
        return []

    # Build node -> partition index
    node_part: dict[int, int] = {}
    for idx, p in enumerate(partitions):
        for n in p["nodes"]:
            node_part[n] = idx

    # Build partition dependency graph
    PG = nx.DiGraph()
    for idx in range(len(partitions)):
        PG.add_node(idx)

    for u, v in G.edges:
        pu = node_part.get(u)
        pv = node_part.get(v)
        if pu is not None and pv is not None and pu != pv:
            PG.add_edge(pu, pv)

    # Topological sort on condensed version (handles cycles)
    cond = nx.condensation(PG)
    scc_map = cond.graph["mapping"]

    scc_order = list(nx.topological_sort(cond))
    # Reverse: leaves first (SCCs with no outgoing edges come first)
    scc_order = list(reversed(scc_order))

    # Map SCC order back to partition IDs (1-based)
    order: list[int] = []
    for scc_id in scc_order:
        members = [pid for pid, sid in scc_map.items() if sid == scc_id]
        members.sort()
        for pid in members:
            order.append(pid + 1)  # 1-based

    return order


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_result(n_agents: int) -> dict:
    """Return a valid but empty result."""
    agents = [
        {
            "id": i + 1,
            "write_files": [],
            "read_only_files": [],
            "symbols_owned": 0,
            "contracts": [],
            "cluster_label": f"empty-{i + 1}",
        }
        for i in range(n_agents)
    ]
    return {
        "agents": agents,
        "merge_order": list(range(1, n_agents + 1)),
        "conflict_probability": 0.0,
        "shared_interfaces": [],
        "write_conflicts": 0,
    }


def _adjust_cluster_count(
    G: nx.DiGraph,
    groups: dict[int, set[int]],
    n_agents: int,
) -> list[dict[str, set[int]]]:
    """Merge or split clusters until we have exactly *n_agents* partitions.

    Returns a list of dicts with key ``nodes`` (set of node IDs).
    """
    # Start with sorted cluster groups (biggest first)
    sorted_groups = sorted(groups.values(), key=len, reverse=True)

    # If we already have the right number, done
    if len(sorted_groups) == n_agents:
        return [{"nodes": s} for s in sorted_groups]

    # If more clusters than agents: merge smallest
    if len(sorted_groups) > n_agents:
        while len(sorted_groups) > n_agents:
            # Merge the two smallest
            sorted_groups.sort(key=len, reverse=True)
            smallest = sorted_groups.pop()
            second_smallest = sorted_groups.pop()
            merged = smallest | second_smallest
            sorted_groups.append(merged)
            sorted_groups.sort(key=len, reverse=True)
        return [{"nodes": s} for s in sorted_groups]

    # If fewer clusters than agents: split largest
    while len(sorted_groups) < n_agents:
        sorted_groups.sort(key=len, reverse=True)
        largest = sorted_groups.pop(0)
        if len(largest) < 2:
            # Cannot split a singleton; add it back and create an empty one
            sorted_groups.append(largest)
            sorted_groups.append(set())
            continue
        # Split by betweenness centrality cut
        sub = G.subgraph(largest)
        try:
            bc = nx.betweenness_centrality(sub)
            # Remove the highest-betweenness node to split
            cut_node = max(bc, key=bc.get)
            remaining = largest - {cut_node}
            # Get connected components of the subgraph without cut_node
            sub_remaining = G.subgraph(remaining).to_undirected()
            components = list(nx.connected_components(sub_remaining))
            if len(components) >= 2:
                # Take the two largest components; assign cut_node to the first
                components.sort(key=len, reverse=True)
                first = components[0] | {cut_node}
                rest = set()
                for c in components[1:]:
                    rest |= c
                sorted_groups.append(first)
                sorted_groups.append(rest)
            else:
                # Could not split by removing cut node; split by bisection
                half = len(largest) // 2
                nodes_list = sorted(largest)
                sorted_groups.append(set(nodes_list[:half]))
                sorted_groups.append(set(nodes_list[half:]))
        except Exception:
            # Fallback: simple bisection
            half = len(largest) // 2
            nodes_list = sorted(largest)
            sorted_groups.append(set(nodes_list[:half]))
            sorted_groups.append(set(nodes_list[half:]))

    # Trim excess (shouldn't happen but safety)
    partitions = [{"nodes": s} for s in sorted_groups[:n_agents]]
    return partitions


def _build_agent_descriptors(
    G: nx.DiGraph,
    conn: sqlite3.Connection,
    partitions: list[dict[str, set[int]]],
) -> list[dict]:
    """Build per-agent descriptor dicts.

    File ownership is determined by majority vote: each file is assigned
    exclusively to the partition that owns the most of its symbols.
    This guarantees no write overlap between agents.
    """
    # Build node -> partition index
    node_part: dict[int, int] = {}
    for idx, p in enumerate(partitions):
        for n in p["nodes"]:
            node_part[n] = idx

    # Fetch file paths for all nodes
    all_node_ids = list(node_part.keys())
    node_to_file: dict[int, str] = {}
    node_to_name: dict[int, str] = {}
    node_to_sig: dict[int, str] = {}
    if all_node_ids:
        rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.signature, f.path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            all_node_ids,
        )
        for r in rows:
            node_to_file[r["id"]] = r["path"].replace("\\", "/")
            node_to_name[r["id"]] = r["name"]
            node_to_sig[r["id"]] = r["signature"] or r["name"]

    # ── Determine exclusive file ownership by majority vote ──────
    # Count how many symbols each partition contributes to each file
    file_partition_counts: dict[str, Counter] = defaultdict(Counter)
    for n, pidx in node_part.items():
        fp = node_to_file.get(n)
        if fp:
            file_partition_counts[fp][pidx] += 1

    # Assign each file to the partition with the most symbols in it
    file_owner: dict[str, int] = {}
    for fp, counts in file_partition_counts.items():
        file_owner[fp] = counts.most_common(1)[0][0]

    # Build per-partition write file sets (exclusive, no overlap)
    partition_write_files: dict[int, set[str]] = defaultdict(set)
    for fp, pidx in file_owner.items():
        partition_write_files[pidx].add(fp)

    # ── Build agent descriptors ──────────────────────────────────
    agents = []
    for idx, p in enumerate(partitions):
        nodes = p["nodes"]
        if not nodes:
            agents.append({
                "id": idx + 1,
                "write_files": [],
                "read_only_files": [],
                "symbols_owned": 0,
                "contracts": [],
                "cluster_label": f"partition-{idx + 1}",
            })
            continue

        write_files_set = partition_write_files.get(idx, set())

        # Read-only files: files with edges into/from this partition
        # but owned by another partition
        read_only_set: set[str] = set()
        for n in nodes:
            for pred in G.predecessors(n):
                if pred not in nodes and pred in node_part:
                    fp = node_to_file.get(pred)
                    if fp and fp not in write_files_set:
                        read_only_set.add(fp)
            for succ in G.successors(n):
                if succ not in nodes and succ in node_part:
                    fp = node_to_file.get(succ)
                    if fp and fp not in write_files_set:
                        read_only_set.add(fp)

        # Contracts: symbols at boundaries that this agent must NOT modify
        contracts = []
        for n in nodes:
            for succ in G.successors(n):
                if succ not in nodes and succ in node_part:
                    name = node_to_name.get(succ)
                    sig = node_to_sig.get(succ)
                    if name and sig:
                        contract = f"do NOT modify {sig} signature"
                        if contract not in contracts:
                            contracts.append(contract)
            for pred in G.predecessors(n):
                if pred not in nodes and pred in node_part:
                    name = node_to_name.get(pred)
                    sig = node_to_sig.get(pred)
                    if name and sig:
                        contract = f"do NOT modify {sig} signature"
                        if contract not in contracts:
                            contracts.append(contract)

        # Cluster label from directory majority
        dirs = [
            os.path.dirname(node_to_file.get(n, "")).replace("\\", "/")
            for n in nodes
            if n in node_to_file
        ]
        dir_counts = Counter(dirs) if dirs else Counter()
        if dir_counts:
            label = dir_counts.most_common(1)[0][0]
            label = label.rstrip("/").rsplit("/", 1)[-1] if label else f"partition-{idx + 1}"
            if not label:
                label = f"root-{idx + 1}"
        else:
            label = f"partition-{idx + 1}"

        agents.append({
            "id": idx + 1,
            "write_files": sorted(write_files_set),
            "read_only_files": sorted(read_only_set),
            "symbols_owned": len(nodes),
            "contracts": contracts[:10],  # cap at 10
            "cluster_label": label,
        })

    return agents


def _find_shared_interfaces(
    G: nx.DiGraph,
    conn: sqlite3.Connection,
    partitions: list[dict[str, set[int]]],
) -> list[dict]:
    """Find symbols at partition boundaries (high betweenness in cross-partition edges)."""
    node_part: dict[int, int] = {}
    for idx, p in enumerate(partitions):
        for n in p["nodes"]:
            node_part[n] = idx

    # Count cross-partition edges per node
    boundary_counts: Counter = Counter()
    for u, v in G.edges:
        pu = node_part.get(u)
        pv = node_part.get(v)
        if pu is not None and pv is not None and pu != pv:
            boundary_counts[u] += 1
            boundary_counts[v] += 1

    if not boundary_counts:
        return []

    # Take top boundary symbols
    top_ids = [nid for nid, _ in boundary_counts.most_common(20)]

    # Fetch symbol info
    result = []
    if top_ids:
        rows = batched_in(
            conn,
            "SELECT s.id, s.name, f.path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            top_ids,
        )
        seen = set()
        for r in rows:
            sym_label = f"{r['path'].replace(chr(92), '/')}::{r['name']}"
            if sym_label not in seen:
                seen.add(sym_label)
                result.append({
                    "symbol": sym_label,
                    "boundary_edges": boundary_counts.get(r["id"], 0),
                })

    result.sort(key=lambda x: -x["boundary_edges"])
    return result[:10]
