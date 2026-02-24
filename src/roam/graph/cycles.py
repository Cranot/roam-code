"""Tarjan SCC / cycle detection for the symbol graph."""

from __future__ import annotations

import sqlite3
from collections import Counter

import networkx as nx

from roam.db.connection import batched_in


def algebraic_connectivity(G: nx.DiGraph) -> float:
    """Compute the algebraic connectivity (Fiedler value) of the graph.

    The Fiedler value is the second-smallest eigenvalue of the graph
    Laplacian.  It measures how well-connected the graph is:
      0     → graph is disconnected
      low   → fragile architecture with bridge dependencies
      high  → robust, well-connected structure

    Uses the undirected projection of the dependency graph.
    Returns 0.0 if scipy is unavailable or the graph is too small.

    Reference: Fiedler (1973), "Algebraic connectivity of graphs."
    """
    if len(G) < 3:
        return 0.0
    try:
        undirected = G.to_undirected()
        # Only compute on the largest connected component
        if not nx.is_connected(undirected):
            largest_cc = max(nx.connected_components(undirected), key=len)
            undirected = undirected.subgraph(largest_cc).copy()
        if len(undirected) < 3:
            return 0.0
        return round(nx.algebraic_connectivity(undirected), 6)
    except Exception:
        return 0.0


def find_cycles(G: nx.DiGraph, min_size: int = 2) -> list[list[int]]:
    """Return strongly connected components with at least *min_size* members.

    Components are sorted by size descending, and each component's node list
    is sorted for deterministic output.
    """
    if len(G) == 0:
        return []

    sccs = [
        sorted(c)
        for c in nx.strongly_connected_components(G)
        if len(c) >= min_size
    ]
    sccs.sort(key=len, reverse=True)
    return sccs


def format_cycles(
    cycles: list[list[int]], conn: sqlite3.Connection
) -> list[dict]:
    """Annotate each cycle with symbol names and file paths.

    Returns a list of dicts::

        [
            {
                "symbols": [{"id": 1, "name": "foo", "kind": "function", "file_path": "..."}],
                "files": ["src/a.py", "src/b.py"],
                "size": 3,
            },
            ...
        ]
    """
    if not cycles:
        return []

    # Pre-fetch all symbol IDs we need in one query per cycle (batch)
    all_ids = {sid for cycle in cycles for sid in cycle}
    if not all_ids:
        return []

    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        list(all_ids),
    )
    lookup: dict[int, dict] = {}
    for sid, name, kind, fpath in rows:
        lookup[sid] = {"id": sid, "name": name, "kind": kind, "file_path": fpath}

    result = []
    for cycle in cycles:
        symbols = [lookup[sid] for sid in cycle if sid in lookup]
        files = sorted({s["file_path"] for s in symbols})
        result.append({"symbols": symbols, "files": files, "size": len(cycle)})
    return result


_PROPAGATION_COST_NODE_LIMIT = 500


def propagation_cost(G: nx.DiGraph) -> float:
    """Compute the Propagation Cost metric (MacCormack et al. 2006).

    PC = fraction of the system potentially affected by a change to any
    single component.  Computed as ``sum(V) / n²`` where V is the
    transitive closure (visibility) matrix.

    Returns a value in [0, 1]:
      0 → no transitive dependencies at all (fully decoupled)
      1 → every component can reach every other (fully coupled)

    For graphs larger than ``_PROPAGATION_COST_NODE_LIMIT`` nodes the
    full transitive closure (O(V*(V+E))) is prohibitively expensive.
    In that case we use a BFS-sampled approximation: compute reachable
    set sizes for a random sample of nodes and extrapolate.

    Reference: MacCormack, Rusnak & Baldwin (2006),
    "Exploring the Structure of Complex Software Designs."
    """
    n = len(G)
    if n <= 1:
        return 0.0

    if n > _PROPAGATION_COST_NODE_LIMIT:
        return _propagation_cost_sampled(G, n)

    # Transitive closure: V[i][j] = 1 iff j is reachable from i
    TC = nx.transitive_closure(G, reflexive=False)
    return round(TC.number_of_edges() / (n * (n - 1)), 4)


def _propagation_cost_sampled(
    G: nx.DiGraph, n: int, sample_size: int = 200
) -> float:
    """BFS-sampled approximation of propagation cost for large graphs.

    Picks up to *sample_size* nodes, computes the number of reachable
    nodes from each via BFS (``descendants``), and averages.  This is
    O(sample_size * (V+E)) instead of O(V * (V+E)).

    Uses a fixed seed for deterministic output across runs.
    """
    import random

    nodes = sorted(G.nodes())
    k = min(sample_size, n)
    rng = random.Random(42)
    sample = rng.sample(nodes, k)

    total_reach = sum(len(nx.descendants(G, v)) for v in sample)
    avg_reach = total_reach / k
    return round(avg_reach / (n - 1), 4) if n > 1 else 0.0


def find_weakest_edge(
    G: nx.DiGraph, scc_members: list[int]
) -> tuple[int, int, str] | None:
    """Find the single edge in an SCC whose removal most likely breaks the cycle.

    Uses edge betweenness centrality on the SCC subgraph: the edge with
    the highest betweenness carries the most shortest paths and is thus
    the most "critical" bridge in the cycle.  Removing it is most likely
    to break the cycle into acyclic components.

    Falls back to degree-based heuristic for very large SCCs (>500 nodes)
    where edge betweenness is too expensive (O(VE)).

    Returns ``(source_id, target_id, reason_string)`` or ``None`` if the SCC
    has fewer than 2 members or no internal edges.
    """
    member_set = set(scc_members)
    if len(member_set) < 2:
        return None

    # Collect internal edges (both endpoints inside the SCC)
    internal_edges = [
        (u, v) for u, v in G.edges()
        if u in member_set and v in member_set
    ]
    if not internal_edges:
        return None

    # Build SCC subgraph
    sub = G.subgraph(member_set)

    # For small-to-moderate SCCs, use edge betweenness centrality (Brandes)
    if len(member_set) <= 500:
        ebc = nx.edge_betweenness_centrality(sub)
        if ebc:
            best_edge = max(ebc, key=ebc.get)
            u, v = best_edge
            bw = ebc[best_edge]
            reason = f"highest edge betweenness in cycle ({bw:.3f})"
            return (u, v, reason)

    # Fallback for large SCCs: degree-based heuristic
    out_deg: dict[int, int] = Counter()
    in_deg: dict[int, int] = Counter()
    for u, v in internal_edges:
        out_deg[u] += 1
        in_deg[v] += 1

    best_edge = None
    best_score = (-1, -1)
    for u, v in internal_edges:
        score = (out_deg[u], in_deg[v])
        if score > best_score:
            best_score = score
            best_edge = (u, v)

    if best_edge is None:
        return None

    u, v = best_edge
    src_out = out_deg[u]
    tgt_in = in_deg[v]
    reason = (
        f"source has {src_out} outgoing edge{'s' if src_out != 1 else ''} in cycle, "
        f"target has {tgt_in} incoming"
    )
    return (u, v, reason)
