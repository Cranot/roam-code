"""Build NetworkX graphs from the Roam SQLite index."""

from __future__ import annotations

import sqlite3

import networkx as nx


def build_symbol_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """Build a directed graph from symbol edges.

    Nodes are symbol IDs with attributes: name, kind, file_path, qualified_name.
    Edges carry a ``kind`` attribute (calls, imports, inherits, etc.).
    """
    G = nx.DiGraph()

    # Load nodes (ORDER BY id for deterministic graph construction)
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.qualified_name, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY s.id"
    ).fetchall()
    G.add_nodes_from(
        (row[0], {"name": row[1], "kind": row[2],
                  "qualified_name": row[3], "file_path": row[4]})
        for row in rows
    )

    # Load edges — pre-build node set for O(1) membership checks
    # ORDER BY for deterministic edge insertion order
    node_set = set(G)
    rows = conn.execute(
        "SELECT source_id, target_id, kind FROM edges "
        "ORDER BY source_id, target_id"
    ).fetchall()
    G.add_edges_from(
        (source_id, target_id, {"kind": kind})
        for source_id, target_id, kind in rows
        if source_id in node_set and target_id in node_set
    )

    return G


def build_file_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """Build a directed graph from file-level edges.

    Nodes are file IDs with attributes: path, language.
    Edges carry ``kind`` and ``symbol_count`` attributes.
    """
    G = nx.DiGraph()

    rows = conn.execute(
        "SELECT id, path, language FROM files ORDER BY id"
    ).fetchall()
    for fid, path, language in rows:
        G.add_node(fid, path=path, language=language)

    rows = conn.execute(
        "SELECT source_file_id, target_file_id, kind, symbol_count "
        "FROM file_edges ORDER BY source_file_id, target_file_id"
    ).fetchall()
    for src, tgt, kind, sym_count in rows:
        if src in G and tgt in G:
            G.add_edge(src, tgt, kind=kind, symbol_count=sym_count)

    return G
