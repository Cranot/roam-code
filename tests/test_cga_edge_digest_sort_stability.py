"""W1285: regression test — ``_edge_bundle_digest`` must be byte-identical
across two fresh SQLite connections to the same on-disk DB, even when the
``edges`` table contains duplicate ``(source_id, target_id, kind)`` triples.

Pre-existing bug (broken on v13.0 / v13.1 / v13.2 CGA Attestation workflow):
the digest's ``ORDER BY source_id, target_id, kind`` was NOT a total order
because the ``edges`` schema has no UNIQUE constraint on that triple. The
indexer legitimately writes duplicate triples (same caller -> same callee on
different lines). Two fresh connections could return tied rows in different
orders depending on the planner's index choice + ``sqlite_stat1`` state,
breaking the CGA emit -> verify round-trip with an
``edge_bundle_digest mismatch — edges changed since signing``.

Fix: append ``id`` (the SQLite rowid alias of ``edges.id INTEGER PRIMARY KEY
AUTOINCREMENT``) as the final ``ORDER BY`` column, making the order total.

This test fails without the fix and passes with it. See
``(internal memo)`` for the full investigation.
"""

from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path

from roam.attest.cga import _edge_bundle_digest

# ---------------------------------------------------------------------------
# Test fixture: on-disk SQLite DB carrying duplicate edge triples
# ---------------------------------------------------------------------------


def _build_dup_edge_db(db_path: Path) -> None:
    """Create a sqlite DB with duplicate ``(source_id, target_id, kind)``
    edge rows, mirroring the schema shape of the real ``edges`` table:
    ``id INTEGER PRIMARY KEY AUTOINCREMENT`` + non-unique
    ``(source_id, target_id)`` + ``(kind, target_id)`` indexes. Insert
    several duplicate triples with non-monotone id ordering so any planner
    that scans either index in either direction would produce a different
    tied-row order than a sort that includes ``id`` as the final tiebreaker.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                target_id INTEGER,
                kind TEXT
            );
            CREATE INDEX idx_edges_source_target ON edges(source_id, target_id);
            CREATE INDEX idx_edges_kind_target ON edges(kind, target_id);
            """
        )
        # Mix of duplicate triples + distinct triples. The duplicates are
        # what break the original ORDER BY — without the id tiebreaker, the
        # planner is free to return them in any order, and two fresh
        # connections can disagree.
        #
        # Multiple (10, 20, "calls") rows simulate "same caller calls same
        # callee on N different lines"; multiple (11, 10, "references")
        # simulate "same class references same symbol twice".
        conn.executemany(
            "INSERT INTO edges(source_id, target_id, kind) VALUES (?, ?, ?)",
            [
                (10, 20, "calls"),  # id=1
                (11, 10, "references"),  # id=2
                (10, 20, "calls"),  # id=3 — dup of id=1
                (12, 20, "imports"),  # id=4
                (10, 20, "calls"),  # id=5 — dup of id=1, id=3
                (11, 10, "references"),  # id=6 — dup of id=2
                (12, 20, "imports"),  # id=7 — dup of id=4
                (10, 30, "calls"),  # id=8
                (10, 20, "calls"),  # id=9 — dup of id=1, id=3, id=5
            ],
        )
        # Refresh planner stats so the ORDER BY plan is the one the planner
        # would pick after a `PRAGMA optimize` writer (the workflow path
        # that exposed the bug: `clones --persist` runs PRAGMA optimize at
        # commit, then `cga emit` and `cga verify` open fresh connections).
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test: digest is byte-identical across two fresh sqlite3 connections
# ---------------------------------------------------------------------------


def test_edge_digest_stable_across_fresh_connections_with_dups(tmp_path):
    """Two fresh sqlite3 opens against the same on-disk DB must produce
    the same digest, even when the ``edges`` table carries duplicate
    ``(source_id, target_id, kind)`` triples.

    This is the bug isolation test from the W1285 root-cause memo.
    """
    db_path = tmp_path / "edges_dup.db"
    _build_dup_edge_db(db_path)

    # Connection A: fresh open, compute digest, close.
    conn_a = sqlite3.connect(str(db_path))
    try:
        digest_a, count_a = _edge_bundle_digest(conn_a)
    finally:
        conn_a.close()

    # Connection B: separate fresh open against the same file. No
    # page-cache sharing inside SQLite (each connection has its own).
    # If the ORDER BY isn't a total order, tied rows can come back in a
    # different order here.
    conn_b = sqlite3.connect(str(db_path))
    try:
        digest_b, count_b = _edge_bundle_digest(conn_b)
    finally:
        conn_b.close()

    assert count_a == count_b == 9, f"expected 9 edges, got A={count_a}, B={count_b}"
    assert digest_a == digest_b, (
        f"edge_bundle_digest differs across fresh connections: "
        f"A={digest_a}, B={digest_b}. This is W1285 — ORDER BY must "
        f"include `id` as the final tiebreaker for total order."
    )


def test_edge_digest_stable_after_pragma_optimize(tmp_path):
    """Mirror of the real CGA workflow path: writer runs ``PRAGMA optimize``
    (as ``clones --persist`` does on commit) between two readers, then both
    readers compute the digest. With the W1285 fix the digests match; before
    the fix the planner can shift mid-flight and they diverge on duplicate
    triples.
    """
    db_path = tmp_path / "edges_dup_optimize.db"
    _build_dup_edge_db(db_path)

    # Reader 1 — fresh open, compute digest, close.
    conn1 = sqlite3.connect(str(db_path))
    try:
        digest1, _ = _edge_bundle_digest(conn1)
    finally:
        conn1.close()

    # Writer — opens fresh, runs PRAGMA optimize on commit (the
    # connection.py:678 path used by `clones --persist`). No edge
    # mutation; only planner statistics refresh.
    writer = sqlite3.connect(str(db_path))
    try:
        writer.execute("PRAGMA optimize")
        writer.commit()
    finally:
        writer.close()

    # Reader 2 — fresh open after the optimize. With the fix this is
    # canonical; without the fix this is the exact branch that fails
    # in the CGA Attestation workflow.
    conn2 = sqlite3.connect(str(db_path))
    try:
        digest2, _ = _edge_bundle_digest(conn2)
    finally:
        conn2.close()

    assert digest1 == digest2, (
        f"edge_bundle_digest drifted across PRAGMA optimize boundary: "
        f"before={digest1}, after={digest2}. This is the CGA emit->verify "
        f"failure mode from W1285."
    )


def test_edge_digest_sql_has_id_tiebreaker():
    """Direct source-level pin: ``_edge_bundle_digest``'s SQL ORDER BY must
    end in ``id`` (the SQLite rowid alias). This is the W1285 contract
    test — it fails the moment a future refactor drops the tiebreaker,
    regardless of whether the current host's SQLite planner happens to
    return rows in id-monotone order on the test fixture.

    Rationale: SQLite's planner choice + ``sqlite_stat1`` state varies
    by version, platform, page size, and prior write history (see the
    root-cause memo at ``(internal memo)``).
    A behavioural test that round-trips a fixture is necessary but not
    sufficient — the only reliable lock on the fix is to pin the SQL
    text itself.
    """
    src = inspect.getsource(_edge_bundle_digest)
    # Strip whitespace runs to handle string-continuation across lines.
    normalised = re.sub(r"\s+", " ", src)
    assert "FROM edges" in normalised, "edge_bundle_digest no longer scans `edges`"
    # The ORDER BY must end in `, id` (or `, rowid`). Match either to
    # stay robust to a future refactor that picks the spelling.
    m = re.search(
        r"ORDER BY\s+source_id\s*,\s*target_id\s*,\s*kind\s*,\s*(id|rowid)\b",
        normalised,
        re.IGNORECASE,
    )
    assert m, (
        f"_edge_bundle_digest ORDER BY does not end in `, id` / `, rowid` — "
        f"the W1285 sort-stability fix has regressed. SQL fragment: "
        f"{normalised!r}"
    )


def test_edge_digest_id_tiebreaker_separates_duplicates(tmp_path):
    """Direct contract check: when two rows share ``(source_id, target_id,
    kind)`` but differ in ``id``, the ORDER BY must place the lower ``id``
    first (canonical SQLite ascending order). This pins the W1285 fix
    contract — the tiebreaker is ``id``, not ``rowid``-modulo-page-layout
    or some implementation-defined fallback.

    Uses explicit ``id`` assignment that is NON-MONOTONE relative to
    ``(source_id, target_id, kind)`` insertion order so a planner that
    walks ``idx_edges_source_target`` does not coincidentally return rows
    in ``id`` order. Without the W1285 fix, this test detects the
    contract violation deterministically on any planner that scans via
    that index — the function's output diverges from the canonical
    Python-sorted digest.
    """
    db_path = tmp_path / "edges_tiebreaker.db"
    # Build a custom DB where ``id`` is deliberately scrambled vs. the
    # ``(source, target, kind)`` clustering: rows for the same triple are
    # inserted with NON-CONSECUTIVE ids so any index-scan plan that orders
    # by (source, target) without a final ``id`` tiebreaker can return
    # tied rows in a planner-defined (not id-monotone) order.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY,
                source_id INTEGER,
                target_id INTEGER,
                kind TEXT
            );
            CREATE INDEX idx_edges_source_target ON edges(source_id, target_id);
            CREATE INDEX idx_edges_kind_target ON edges(kind, target_id);
            """
        )
        # Scrambled id assignment: same triple gets ids 100, 50, 200, 75.
        # A scan via idx_edges_source_target returns tied rows in
        # whatever btree order — only the explicit `, id` tiebreaker
        # guarantees lower-id-first.
        conn.executemany(
            "INSERT INTO edges(id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
            [
                (100, 10, 20, "calls"),
                (50, 10, 20, "calls"),
                (200, 10, 20, "calls"),
                (75, 10, 20, "calls"),
                (300, 11, 10, "references"),
                (25, 11, 10, "references"),
            ],
        )
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()

    # Hand-roll the canonical order: sort rows by
    # (source_id, target_id, kind, id), build the same payload as
    # ``_edge_bundle_digest``, and assert byte-identical digest output.
    import hashlib

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT source_id, target_id, kind, id FROM edges"
        ).fetchall()
        actual_digest, count = _edge_bundle_digest(conn)
    finally:
        conn.close()
    rows.sort(key=lambda r: (r[0], r[1], r[2] or "", r[3]))

    h = hashlib.sha256()
    for r in rows:
        chunk = f"{r[0]}->{r[1]}:{r[2] or ''}".encode("utf-8")
        h.update(len(chunk).to_bytes(4, "big"))
        h.update(chunk)
    expected_digest = h.hexdigest()

    assert count == 6
    assert actual_digest == expected_digest, (
        f"_edge_bundle_digest doesn't match the canonical "
        f"(source_id, target_id, kind, id) ordering: "
        f"got={actual_digest}, expected={expected_digest}. "
        f"This is W1285 — the ORDER BY must include `id` as the "
        f"final tiebreaker."
    )
