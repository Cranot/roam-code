"""Guard: the symbol-graph cache must key on durable database identity.

``roam.graph.builder`` memoised the built graph on ``id(conn)``. CPython
recycles a freed object's address, so a NEW connection could land on a
CLOSED connection's address and be served ANOTHER PROJECT'S symbol graph --
silently, with no error and a plausible-looking answer. Every graph-derived
command is downstream of this (deps, impact, cut, clusters, dead, pagerank,
health), and the MCP server is a long-lived process that opens and closes a
connection per tool call, so the wrong graph can persist for a whole session.

These tests deliberately do NOT rely on conftest's
``_clear_graph_cache_between_tests`` fixture -- that clearing is exactly what
masked the defect, so each test asserts the cache is still warm at the moment
the aliasing would occur. Without that assertion the guard would pass
vacuously if any fixture ever cleared the cache mid-test.
"""

import sqlite3

from roam.graph import builder
from roam.graph.builder import build_symbol_graph

_SCHEMA = """
CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT);
CREATE TABLE symbols (
    id INTEGER PRIMARY KEY, name TEXT, kind TEXT,
    qualified_name TEXT, file_id INTEGER
);
CREATE TABLE edges (source_id INTEGER, target_id INTEGER, kind TEXT);
"""


def _make_index(path, symbol_names):
    """Write a minimal roam index at *path* containing exactly *symbol_names*."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO files VALUES (1, ?, 'python')", (f"{path.stem}.py",))
        for i, name in enumerate(symbol_names, start=1):
            conn.execute(
                "INSERT INTO symbols VALUES (?, ?, 'function', ?, 1)",
                (i, name, name),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _names(graph):
    return sorted(data["name"] for _, data in graph.nodes(data=True))


ALPHA = ["alpha_one", "alpha_two"]
BETA = ["beta_x", "beta_y", "beta_z"]


def test_recycled_connection_id_does_not_alias_another_project(tmp_path):
    """A second project must get its OWN graph, even when its connection is
    allocated at a just-freed connection's address.

    Fails against the ``id(conn)``-keyed implementation: project B is handed
    project A's graph.
    """
    db_a = _make_index(tmp_path / "a.db", ALPHA)
    db_b = _make_index(tmp_path / "b.db", BETA)

    recycled = 0
    attempts = 60
    for _ in range(attempts):
        conn_a = sqlite3.connect(str(db_a))
        id_a = id(conn_a)
        assert _names(build_symbol_graph(conn_a)) == ALPHA
        conn_a.close()
        del conn_a  # free the object so CPython can reuse its address

        # Defeat conftest: if the cache were empty here the aliasing could
        # not occur and this test would prove nothing.
        assert builder._GRAPH_CACHE, "graph cache was cleared mid-test -- guard is vacuous"

        conn_b = sqlite3.connect(str(db_b))
        if id(conn_b) == id_a:
            recycled += 1
        try:
            # THE INVARIANT: B's connection sits at A's old address, and A's
            # graph is still cached. B must still get B's graph.
            assert _names(build_symbol_graph(conn_b)) == BETA
        finally:
            conn_b.close()

    # Guard against a vacuous pass: if the allocator never recycled an
    # address, the defect was never exercised. Freeing a connection and
    # immediately allocating another reuses the slot with ~50% probability
    # per attempt, so 60 attempts makes a spurious failure vanishingly rare.
    assert recycled > 0, f"no connection id was recycled in {attempts} attempts -- defect not exercised"


def test_same_project_twice_still_hits_the_cache(tmp_path):
    """NEGATIVE CONTROL: caching must survive the fix.

    A second connection to the SAME unchanged database must be served the
    memoised graph OBJECT, not a rebuild. This is what stops "just disable
    the cache" from passing as a fix -- graph construction is ~400ms on a
    real repo and 177 call sites depend on the memoisation.
    """
    db = _make_index(tmp_path / "same.db", ALPHA)

    conn1 = sqlite3.connect(str(db))
    first = build_symbol_graph(conn1)
    conn1.close()
    del conn1

    assert builder._GRAPH_CACHE, "graph cache was cleared mid-test -- control is vacuous"

    conn2 = sqlite3.connect(str(db))
    try:
        second = build_symbol_graph(conn2)
    finally:
        conn2.close()

    assert second is first, "same database rebuilt instead of hitting the cache"


def test_cache_invalidates_when_the_index_changes(tmp_path):
    """A long-lived process must not keep answering from a pre-reindex graph.

    This is the exposure that is live for every MCP user, single-project
    included: the server holds the cache for its whole lifetime while an
    external ``roam reindex`` rewrites the database underneath it. Measured
    against the ``id(conn)`` implementation, this shape served the stale
    graph in 100 of 100 trials -- and mtime/size cannot detect it either
    (three successive commits left both byte-identical on Windows), which is
    why the entry stamps SQLite's own file change counter.

    It also pins the other half of the fix: a cache keyed purely on the
    database path, with no change stamp, would serve the pre-edit graph
    forever.
    """
    db = _make_index(tmp_path / "changing.db", ALPHA)

    conn1 = sqlite3.connect(str(db))
    first = build_symbol_graph(conn1)
    assert _names(first) == ALPHA
    conn1.close()
    del conn1

    writer = sqlite3.connect(str(db))
    writer.execute("INSERT INTO symbols VALUES (99, 'alpha_added', 'function', 'alpha_added', 1)")
    writer.commit()
    writer.close()
    del writer  # free it too, so the reader below lands on a recycled address

    assert builder._GRAPH_CACHE, "graph cache was cleared mid-test -- guard is vacuous"

    conn2 = sqlite3.connect(str(db))
    try:
        second = build_symbol_graph(conn2)
    finally:
        conn2.close()

    assert second is not first, "stale graph served after the index changed"
    assert "alpha_added" in _names(second)
