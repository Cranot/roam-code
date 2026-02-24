"""Context propagation through call graph for improved context ranking.

Implements bottom-up propagation: given seed nodes in a directed call graph,
BFS outward through callee edges (downstream) and caller edges (upstream) with
exponentially decaying weights.  Results are merged with existing PageRank
scores to produce a blended context ranking.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Tuple


def propagate_context(
    G,
    seed_nodes: Iterable[int],
    max_depth: int = 3,
    decay: float = 0.5,
) -> Dict[int, float]:
    """BFS from *seed_nodes* through the call graph, scoring reached nodes.

    Callee edges (outgoing / downstream) are traversed with weight
    ``decay ** depth``.  Caller edges (incoming / upstream) are traversed with
    weight ``(decay * 0.5) ** depth`` — upstream context matters but is
    secondary to the transitive dependency chain.

    Handles cycles via a visited set: a node's score is only improved if we
    reach it via a *shorter* path; once a node is settled at the shortest
    callee distance it is not re-expanded through callee edges, and similarly
    for caller edges.

    Parameters
    ----------
    G:
        A ``networkx.DiGraph`` where edges go from caller to callee
        (i.e. ``A -> B`` means A calls B).
    seed_nodes:
        Iterable of node IDs that are the query targets (score = 1.0).
    max_depth:
        Maximum BFS depth (inclusive).  Nodes at depth > max_depth are not
        added.  Defaults to 3.
    decay:
        Per-level decay factor for callee direction.  Caller direction uses
        ``decay * 0.5``.  Defaults to 0.5.

    Returns
    -------
    dict[int, float]
        Mapping of ``{node_id: propagation_score}``.  Seed nodes have score
        1.0; unreachable nodes are absent.
    """
    seeds = set(seed_nodes)
    if not seeds:
        return {}

    scores: Dict[int, float] = {}

    # Seed nodes always score 1.0
    for s in seeds:
        if s in G:
            scores[s] = 1.0

    # --- Callee BFS (forward / downstream direction) ---
    # Queue entries: (node_id, depth)
    # visited_callee tracks the minimum depth at which we reached each node
    visited_callee: Dict[int, int] = {s: 0 for s in seeds if s in G}
    callee_queue: deque[Tuple[int, int]] = deque(
        (s, 0) for s in seeds if s in G
    )

    while callee_queue:
        node, depth = callee_queue.popleft()
        if depth >= max_depth:
            continue
        next_depth = depth + 1
        callee_score = decay ** next_depth
        for neighbor in G.successors(node):
            if neighbor in seeds:
                continue  # Seeds keep score 1.0
            if neighbor not in visited_callee or visited_callee[neighbor] > next_depth:
                visited_callee[neighbor] = next_depth
                prev = scores.get(neighbor, 0.0)
                scores[neighbor] = max(prev, callee_score)
                callee_queue.append((neighbor, next_depth))

    # --- Caller BFS (reverse / upstream direction) ---
    # Callers carry lower weight: decay * 0.5
    caller_decay = decay * 0.5
    visited_caller: Dict[int, int] = {s: 0 for s in seeds if s in G}
    caller_queue: deque[Tuple[int, int]] = deque(
        (s, 0) for s in seeds if s in G
    )

    while caller_queue:
        node, depth = caller_queue.popleft()
        if depth >= max_depth:
            continue
        next_depth = depth + 1
        caller_score = caller_decay ** next_depth
        for neighbor in G.predecessors(node):
            if neighbor in seeds:
                continue
            if neighbor not in visited_caller or visited_caller[neighbor] > next_depth:
                visited_caller[neighbor] = next_depth
                prev = scores.get(neighbor, 0.0)
                scores[neighbor] = max(prev, caller_score)
                caller_queue.append((neighbor, next_depth))

    return scores


def merge_rankings(
    pagerank_scores: Dict[int, float],
    propagation_scores: Dict[int, float],
    alpha: float = 0.6,
) -> Dict[int, float]:
    """Blend propagation scores with PageRank scores.

    Final score = ``alpha * norm_propagation + (1 - alpha) * norm_pagerank``

    Both inputs are normalised to [0, 1] before blending so that the scale
    difference between PageRank (tiny floats ~1e-4) and propagation (0–1)
    does not distort the result.

    Parameters
    ----------
    pagerank_scores:
        ``{node_id: pagerank_value}`` from ``compute_pagerank()``.
    propagation_scores:
        ``{node_id: propagation_score}`` from ``propagate_context()``.
    alpha:
        Weight given to propagation scores (0–1).  Remaining weight
        ``1 - alpha`` goes to PageRank.  Defaults to 0.6.

    Returns
    -------
    dict[int, float]
        ``{node_id: blended_score}`` for every node present in either input.
    """
    if not pagerank_scores and not propagation_scores:
        return {}

    # Normalise pagerank
    max_pr = max(pagerank_scores.values(), default=0.0)
    norm_pr: Dict[int, float] = (
        {k: v / max_pr for k, v in pagerank_scores.items()}
        if max_pr > 0
        else {k: 0.0 for k in pagerank_scores}
    )

    # Propagation scores are already in [0, 1] (decay-based)
    max_prop = max(propagation_scores.values(), default=0.0)
    norm_prop: Dict[int, float] = (
        {k: v / max_prop for k, v in propagation_scores.items()}
        if max_prop > 0
        else {k: 0.0 for k in propagation_scores}
    )

    all_nodes = set(norm_pr) | set(norm_prop)
    result: Dict[int, float] = {}
    for node in all_nodes:
        pr_val = norm_pr.get(node, 0.0)
        prop_val = norm_prop.get(node, 0.0)
        result[node] = alpha * prop_val + (1.0 - alpha) * pr_val

    return result


def callee_chain(
    G,
    node: int,
    max_depth: int = 3,
) -> List[Tuple[int, int]]:
    """Return ordered list of transitive callees with their BFS depth.

    BFS from *node* through outgoing (callee) edges up to *max_depth*.
    Cycles are handled via a visited set — each callee is reported at
    the shallowest depth it is reached.

    Parameters
    ----------
    G:
        Directed call graph (caller -> callee edges).
    node:
        Starting symbol node ID.
    max_depth:
        Maximum callee depth to traverse.  Defaults to 3.

    Returns
    -------
    list of (node_id, depth) tuples
        Ordered by BFS level (shallow callees first), then by node ID for
        determinism.  The seed node itself is NOT included.
    """
    if node not in G:
        return []

    visited: Dict[int, int] = {node: 0}
    queue: deque[Tuple[int, int]] = deque([(node, 0)])
    result: List[Tuple[int, int]] = []

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        next_depth = depth + 1
        for neighbor in sorted(G.successors(current)):  # sorted for determinism
            if neighbor not in visited:
                visited[neighbor] = next_depth
                result.append((neighbor, next_depth))
                queue.append((neighbor, next_depth))

    return result
