"""Shared graph utilities for command modules.

Provides reusable adjacency-list builders and BFS helpers used by
cmd_coverage_gaps, cmd_entry_points, cmd_context, and cmd_safe_zones.
"""

from __future__ import annotations

from collections import defaultdict, deque


def build_forward_adj(conn):
    """Build forward adjacency list from edges table (source -> set of targets)."""
    adj = defaultdict(set)
    for row in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj[row["source_id"]].add(row["target_id"])
    return adj


def bfs_reachable(adj, start_ids, max_depth=None):
    """BFS from *start_ids* through an adjacency dict.

    Parameters
    ----------
    adj : dict[int, set[int]]
        Adjacency list (forward or reverse).
    start_ids : set[int] | list[int]
        Seed node IDs.
    max_depth : int | None
        Maximum BFS depth.  ``None`` means unlimited.

    Returns
    -------
    set[int]
        All reachable node IDs (including seeds).
    """
    visited = set(start_ids)
    queue = deque((sid, 0) for sid in start_ids)

    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for neighbor in adj.get(current, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))

    return visited


def bfs_nx(graph, start_ids, max_depth, direction="forward"):
    """BFS traversal on a NetworkX DiGraph returning visited node IDs with depths.

    Parameters
    ----------
    graph : nx.DiGraph
        The symbol graph.
    start_ids : set[int]
        Seed node IDs.
    max_depth : int
        Maximum BFS depth.
    direction : str
        ``"forward"`` follows outgoing edges (callees),
        ``"backward"`` follows incoming edges (callers).

    Returns
    -------
    dict[int, int]
        Mapping of visited node ID to its BFS depth.
    """
    visited: dict[int, int] = {}
    queue: deque[tuple[int, int]] = deque()

    for sid in start_ids:
        if sid in graph:
            visited[sid] = 0
            queue.append((sid, 0))

    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        if direction == "forward":
            neighbors = graph.successors(node)
        else:
            neighbors = graph.predecessors(node)
        for nb in neighbors:
            if nb not in visited:
                visited[nb] = depth + 1
                queue.append((nb, depth + 1))

    return visited
