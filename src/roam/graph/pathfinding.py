"""Shortest-path utilities for the ``roam trace`` command."""

from __future__ import annotations

import itertools
import sqlite3

import networkx as nx


_EDGE_WEIGHTS = {
    "call": 1.0,
    "uses_trait": 1.0,
    "implements": 1.0,
    "inherits": 1.0,
    "uses": 1.0,
    "template": 1.0,
    "import": 1.1,
}


def _ensure_weights(G: nx.DiGraph) -> None:
    """Set edge weights based on kind, favoring call edges over imports."""
    for _u, _v, data in G.edges(data=True):
        if "weight" not in data:
            data["weight"] = _EDGE_WEIGHTS.get(data.get("kind", ""), 2)


def find_path(
    G: nx.DiGraph, source_id: int, target_id: int
) -> list[int] | None:
    """Find the shortest path from *source_id* to *target_id*.

    Prefers call edges over import edges via edge-kind weighting.
    Tries the directed graph first; if no directed path exists, falls back to
    the undirected projection.  Returns ``None`` when no path exists at all.
    """
    if source_id not in G or target_id not in G:
        return None

    _ensure_weights(G)

    # Directed attempt (weighted)
    try:
        return list(nx.shortest_path(G, source_id, target_id, weight="weight"))
    except nx.NetworkXNoPath:
        pass

    # Undirected fallback (weighted)
    try:
        undirected = G.to_undirected()
        return list(nx.shortest_path(undirected, source_id, target_id, weight="weight"))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def find_k_paths(
    G: nx.DiGraph, source_id: int, target_id: int, k: int = 3
) -> list[list[int]]:
    """Find up to *k* shortest simple paths from *source_id* to *target_id*.

    Uses Yen's algorithm via ``nx.shortest_simple_paths``.  Falls back to an
    undirected single-path search when no directed path exists.  Returns an
    empty list when the nodes are completely disconnected.
    """
    if source_id not in G or target_id not in G:
        return []

    _ensure_weights(G)

    # Directed: k-shortest simple paths
    try:
        paths = list(itertools.islice(
            nx.shortest_simple_paths(G, source_id, target_id, weight="weight"),
            k,
        ))
        if paths:
            return paths
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass

    # Undirected fallback (single path only)
    try:
        undirected = G.to_undirected()
        return [list(nx.shortest_path(undirected, source_id, target_id, weight="weight"))]
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def find_symbol_id(conn: sqlite3.Connection, name: str) -> list[int]:
    """Find symbol IDs matching *name*.

    Searches by exact ``name`` first, then by ``qualified_name``.  If neither
    matches, performs a ``LIKE`` search.  Returns a (possibly empty) list of
    matching symbol IDs.
    """
    # Exact name match
    rows = conn.execute(
        "SELECT id FROM symbols WHERE name = ?", (name,)
    ).fetchall()
    if rows:
        return [r[0] for r in rows]

    # Exact qualified name match
    rows = conn.execute(
        "SELECT id FROM symbols WHERE qualified_name = ?", (name,)
    ).fetchall()
    if rows:
        return [r[0] for r in rows]

    # Fuzzy / LIKE search
    rows = conn.execute(
        "SELECT id FROM symbols WHERE name LIKE ? OR qualified_name LIKE ? LIMIT 50",
        (f"%{name}%", f"%{name}%"),
    ).fetchall()
    return [r[0] for r in rows]


def format_path(
    path: list[int], conn: sqlite3.Connection
) -> list[dict]:
    """Annotate a node-ID path with symbol metadata.

    Returns::

        [
            {"id": 1, "name": "foo", "kind": "function",
             "file_path": "src/a.py", "line": 42},
            ...
        ]
    """
    if not path:
        return []

    placeholders = ",".join("?" for _ in path)
    rows = conn.execute(
        f"SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start "
        f"FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE s.id IN ({placeholders})",
        path,
    ).fetchall()

    lookup: dict[int, dict] = {}
    for sid, name, kind, fpath, line in rows:
        lookup[sid] = {
            "id": sid,
            "name": name,
            "kind": kind,
            "file_path": fpath,
            "line": line,
        }

    # Preserve path order
    return [lookup[nid] for nid in path if nid in lookup]
