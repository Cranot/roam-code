"""Build NetworkX graphs from the Roam SQLite index."""

from __future__ import annotations

import sqlite3

import networkx as nx

# redactedprocess-wide cache. Keyed by (id(conn), 'symbol'/'file').
# Compound commands like ``pr-prep`` (and any command that calls a
# helper that itself calls ``build_*_graph``) used to rebuild the
# graph multiple times per invocation. Caching by connection identity
# is safe because connections are short-lived and never reused after
# their context manager exits.
_GRAPH_CACHE: dict[tuple[int, str], nx.DiGraph] = {}


def _cache_get(conn: sqlite3.Connection, kind: str) -> nx.DiGraph | None:
    return _GRAPH_CACHE.get((id(conn), kind))


def _cache_set(conn: sqlite3.Connection, kind: str, G: nx.DiGraph) -> None:
    _GRAPH_CACHE[(id(conn), kind)] = G


def clear_graph_cache() -> None:
    """Drop every memoised graph (test-only helper)."""
    _GRAPH_CACHE.clear()


def build_symbol_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """Build a directed graph from symbol edges.

    Nodes are symbol IDs with attributes: name, kind, file_path, qualified_name.
    Edges carry a ``kind`` attribute (calls, imports, inherits, etc.).

    redactedmemoised by ``id(conn)``.
    """
    cached = _cache_get(conn, "symbol")
    if cached is not None:
        return cached
    G = nx.DiGraph()

    # Load nodes (ORDER BY id for deterministic graph construction)
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.qualified_name, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY s.id"
    ).fetchall()
    G.add_nodes_from(
        (row[0], {"name": row[1], "kind": row[2], "qualified_name": row[3], "file_path": row[4]}) for row in rows
    )

    # Load edges — pre-build node set for O(1) membership checks
    # ORDER BY for deterministic edge insertion order
    node_set = set(G)
    rows = conn.execute("SELECT source_id, target_id, kind FROM edges ORDER BY source_id, target_id").fetchall()
    G.add_edges_from(
        (source_id, target_id, {"kind": kind})
        for source_id, target_id, kind in rows
        if source_id in node_set and target_id in node_set
    )

    _cache_set(conn, "symbol", G)
    return G


def build_file_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """Build a directed graph from file-level edges.

    Nodes are file IDs with attributes: path, language.
    Edges carry ``kind`` and ``symbol_count`` attributes.

    redactedmemoised by ``id(conn)``.
    """
    cached = _cache_get(conn, "file")
    if cached is not None:
        return cached
    G = nx.DiGraph()

    rows = conn.execute("SELECT id, path, language FROM files ORDER BY id").fetchall()
    for fid, path, language in rows:
        G.add_node(fid, path=path, language=language)

    rows = conn.execute(
        "SELECT source_file_id, target_file_id, kind, symbol_count "
        "FROM file_edges ORDER BY source_file_id, target_file_id"
    ).fetchall()
    for src, tgt, kind, sym_count in rows:
        if src in G and tgt in G:
            G.add_edge(src, tgt, kind=kind, symbol_count=sym_count)

    _cache_set(conn, "file", G)
    return G
