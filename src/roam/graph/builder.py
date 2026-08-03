"""Build NetworkX graphs from the Roam SQLite index."""

from __future__ import annotations

import os
import sqlite3
from collections import OrderedDict

import networkx as nx

from roam.observability import log_swallowed

# Process-wide cache of built graphs, keyed by (resolved db path, kind).
#
# KEY DISCIPLINE: the key is the RESOLVED DATABASE PATH -- the identity of
# the thing actually read -- and NEVER ``id(conn)``. CPython recycles a dead
# object's address, so an ``id(conn)`` key let a NEW connection alias a
# CLOSED connection's entry and be served ANOTHER PROJECT'S symbol graph.
# Reproduced in a fresh process: an MCP-shaped request sequence that works
# in one repo for a while and then switches (``AAABBBAABBBB...``) served the
# wrong project's graph on 8 of 16 requests. Strict ``ABAB`` alternation
# happens to pin each project to a stable address and hides it, which is why
# this stayed invisible. Same aliasing class as the 2026-07
# ``_FILE_TEXT_CACHE`` flake in ``roam.catalog.python_idioms``.
#
# ``sqlite3.Connection`` is not weakref-able, so identity-checking the
# connection is impossible; path-keying removes connection identity from the
# equation entirely.
#
# Every graph-derived command is downstream of this (deps, impact, cut,
# clusters, dead, pagerank, health, ...) and the MCP server is a long-lived
# process that opens and closes a connection per tool call, so a wrong entry
# here is one project's code structure answering another project's queries.
#
# INVALIDATION: entries carry a cheap change stamp built from SQLite's own
# file change counter (see ``_db_stamp``), so a re-index rebuilds instead of
# serving a stale graph. Validation costs ~30us against a ~409ms real graph
# build (44k nodes), i.e. ~0.01% -- and path-keying MEASURABLY RAISES the hit
# rate over id-keying, because a new connection to an unchanged project now
# reuses the cached graph deterministically instead of by address luck.
_GRAPH_CACHE: OrderedDict[tuple[str, str], tuple[tuple, nx.DiGraph]] = OrderedDict()

# Bounded so a long-lived MCP server that roams across many projects cannot
# pin an unbounded number of large graphs. Two kinds per project.
_GRAPH_CACHE_MAX = 8


def _db_identity(conn: sqlite3.Connection) -> str | None:
    """Resolved path of the connection's main database, or ``None``.

    ``PRAGMA database_list`` reports the absolute path SQLite actually
    opened, so connections created from different relative paths or from a
    ``file:...?mode=ro`` URI still resolve to the same key. Returns ``None``
    for in-memory databases (which report an empty path and would otherwise
    all collide on one key) -- those are simply never cached.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        # (seq, name, file)
        if row[1] == "main":
            return os.path.normcase(row[2]) if row[2] else None
    return None


def _db_stamp(db_path: str) -> tuple:
    """Cheap signal that changes whenever the database changes.

    Uses SQLite's *file change counter* -- the 4 bytes at offset 24 of the
    database header, which SQLite bumps on every commit. It is exact and
    comparable across connections and processes, because it is file content
    rather than per-connection state (``PRAGMA data_version`` is explicitly
    NOT comparable across connections, so it cannot be used here).

    mtime/size alone are NOT sufficient: measured on Windows, three
    successive committed inserts left both ``st_mtime_ns`` and ``st_size``
    byte-identical while the change counter went 2 -> 3 -> 4. The OS defers
    file-time updates, so an mtime-keyed stamp would serve a stale graph.

    The ``-wal`` leg covers the window where a WAL-mode commit has landed in
    the sidecar but has not yet been checkpointed into the main header.

    Returns ``None`` when the change counter cannot be read. That is NOT the
    same as "unchanged": a stamp built from failed reads would be a CONSTANT,
    so two different database states would share a cache key and the caller
    would serve a stale graph. An unreadable stamp therefore disables caching
    for that database rather than pinning it -- the same fail-closed rule this
    fix exists to enforce, applied to the fix itself. Its absence is also
    logged, because a stamp that silently stops working looks exactly like a
    database that never changes.
    """
    counter = None
    size = None
    try:
        with open(db_path, "rb") as handle:
            header = handle.read(28)
            size = os.fstat(handle.fileno()).st_size
        if len(header) >= 28:
            counter = header[24:28]
    except OSError as exc:
        log_swallowed("graph.builder:db_stamp:header", exc)
    if counter is None or size is None:
        # Cannot prove freshness -> refuse to cache. Never fall back to a
        # partial stamp; a partial stamp is a collision waiting to happen.
        return None
    try:
        wal_st = os.stat(db_path + "-wal")
        wal = (wal_st.st_mtime_ns, wal_st.st_size)
    except FileNotFoundError:
        # No -wal sidecar is the normal rollback-journal case, not an error.
        wal = None
    except OSError as exc:
        log_swallowed("graph.builder:db_stamp:wal", exc)
        return None
    return (counter, size, wal)


def _cache_get(db_path: str | None, kind: str, stamp: tuple) -> nx.DiGraph | None:
    if db_path is None or stamp is None:
        return None
    key = (db_path, kind)
    entry = _GRAPH_CACHE.get(key)
    if entry is None:
        return None
    cached_stamp, G = entry
    if cached_stamp != stamp:
        # Index changed underneath us -- drop the stale graph.
        del _GRAPH_CACHE[key]
        return None
    _GRAPH_CACHE.move_to_end(key)
    return G


def _cache_set(db_path: str | None, kind: str, stamp: tuple, G: nx.DiGraph) -> None:
    if db_path is None or stamp is None:
        return
    key = (db_path, kind)
    _GRAPH_CACHE[key] = (stamp, G)
    _GRAPH_CACHE.move_to_end(key)
    while len(_GRAPH_CACHE) > _GRAPH_CACHE_MAX:
        _GRAPH_CACHE.popitem(last=False)


def clear_graph_cache() -> None:
    """Drop every memoised graph (test-only helper)."""
    _GRAPH_CACHE.clear()


def build_symbol_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """Build a directed graph from symbol edges.

    Nodes are symbol IDs with attributes: name, kind, file_path, qualified_name.
    Edges carry a ``kind`` attribute (calls, imports, inherits, etc.).

    Memoised by resolved database path (never by ``id(conn)`` -- see the
    ``_GRAPH_CACHE`` comment), invalidated when the index changes.
    """
    # Resolve identity + stamp ONCE, before building: stamping after the
    # build would record post-write state for a pre-write graph if the index
    # changed mid-build.
    db_path = _db_identity(conn)
    stamp = _db_stamp(db_path) if db_path is not None else ()
    cached = _cache_get(db_path, "symbol", stamp)
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

    _cache_set(db_path, "symbol", stamp, G)
    return G


def build_file_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """Build a directed graph from file-level edges.

    Nodes are file IDs with attributes: path, language.
    Edges carry ``kind`` and ``symbol_count`` attributes.

    Memoised by resolved database path (never by ``id(conn)`` -- see the
    ``_GRAPH_CACHE`` comment), invalidated when the index changes.
    """
    db_path = _db_identity(conn)
    stamp = _db_stamp(db_path) if db_path is not None else ()
    cached = _cache_get(db_path, "file", stamp)
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

    _cache_set(db_path, "file", stamp, G)
    return G
