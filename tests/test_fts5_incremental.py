"""Pin the incremental-sync behaviour of ``build_fts_index`` (R9.B7).

Pre-fix: every ``roam index`` issued ``DELETE FROM symbol_fts`` + a
full INSERT of every symbol — O(N) FTS5 cost on every reindex.

Post-fix: the build diffs ``symbols`` against ``symbol_fts`` and only
applies the delta. Three properties matter and each gets a test:

1. **Convergence** — after a build, FTS5 contains exactly the same
   rowid set as the symbols table.
2. **Incremental cost** — on a no-op build, FTS5 receives ZERO INSERT
   or DELETE operations.
3. **Force flag** — passing ``force=True`` reverts to the full
   DELETE+INSERT path (used by ``roam index --rebuild``).
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Fixture: a minimal in-memory DB matching the production schema enough to
# exercise the FTS5 sync path. We don't need the full index because B7 only
# touches symbol_fts <-> symbols sync.
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_db(monkeypatch):
    """A throwaway sqlite connection seeded with N synthetic symbols.

    Patches the TF-IDF and ONNX builders to no-ops so we time pure
    FTS5 work — those are tested independently.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            signature TEXT,
            docstring TEXT,
            kind TEXT,
            parent_id INTEGER,
            line_start INTEGER,
            line_end INTEGER
        );
        CREATE VIRTUAL TABLE symbol_fts USING fts5(
            name, qualified_name, signature, docstring, kind, file_path,
            tokenize='porter unicode61'
        );
        """
    )
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/main.py')")
    for i in range(50):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
            "docstring, kind) VALUES (?, 1, ?, ?, ?, ?, 'function')",
            (i + 1, f"fn_{i}", f"mod.fn_{i}", f"def fn_{i}()", ""),
        )
    conn.commit()

    # Disable the optional pieces — they're tested elsewhere and unrelated to
    # the incremental-sync property we care about here.
    import roam.search.index_embeddings as ie
    monkeypatch.setattr(ie, "build_and_store_tfidf", lambda c: None)
    monkeypatch.setattr(ie, "build_and_store_onnx_embeddings", lambda *a, **kw: None)

    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Convergence — every build leaves symbols and symbol_fts in lockstep.
# ---------------------------------------------------------------------------


def test_cold_build_populates_fts(synthetic_db):
    from roam.search.index_embeddings import build_fts_index

    build_fts_index(synthetic_db)
    sym_rowids = {r[0] for r in synthetic_db.execute("SELECT id FROM symbols")}
    fts_rowids = {r[0] for r in synthetic_db.execute("SELECT rowid FROM symbol_fts")}
    assert sym_rowids == fts_rowids, "FTS5 didn't fully populate from a cold start"


def test_incremental_handles_inserts_and_deletes(synthetic_db):
    from roam.search.index_embeddings import build_fts_index

    # Cold build to seed FTS5.
    build_fts_index(synthetic_db)
    assert synthetic_db.execute("SELECT COUNT(*) FROM symbol_fts").fetchone()[0] == 50

    # Mutate symbols: drop 5, add 5 new.
    synthetic_db.execute("DELETE FROM symbols WHERE id <= 5")
    for i in range(5):
        new_id = 100 + i
        synthetic_db.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
            "docstring, kind) VALUES (?, 1, ?, ?, '', '', 'function')",
            (new_id, f"fn_new_{i}", f"mod.fn_new_{i}"),
        )
    synthetic_db.commit()

    # Incremental build should converge.
    build_fts_index(synthetic_db)
    sym_rowids = {r[0] for r in synthetic_db.execute("SELECT id FROM symbols")}
    fts_rowids = {r[0] for r in synthetic_db.execute("SELECT rowid FROM symbol_fts")}
    assert sym_rowids == fts_rowids, (
        f"FTS5 out of sync after incremental: "
        f"missing={sym_rowids - fts_rowids}, stale={fts_rowids - sym_rowids}"
    )


# ---------------------------------------------------------------------------
# 2. Incremental cost — no-op builds issue zero INSERT/DELETE on symbol_fts.
# ---------------------------------------------------------------------------


class _CountingConn:
    """Wraps a sqlite3.Connection and counts how many statements are
    issued against ``symbol_fts`` (filters out the SELECT diff queries
    used by the sync).
    """

    def __init__(self, real):
        self._conn = real
        self.fts_writes: list[str] = []

    def execute(self, sql, *a, **kw):
        s = " ".join(sql.split()).lower()
        if "symbol_fts" in s and ("insert " in s or "delete " in s):
            self.fts_writes.append(s[:80])
        return self._conn.execute(sql, *a, **kw)

    def executemany(self, sql, seq):
        s = " ".join(sql.split()).lower()
        if "symbol_fts" in s and ("insert " in s or "delete " in s):
            self.fts_writes.append(s[:80])
        return self._conn.executemany(sql, seq)

    def __getattr__(self, n):
        return getattr(self._conn, n)


def test_noop_incremental_issues_no_fts_writes(synthetic_db):
    from roam.search.index_embeddings import build_fts_index

    # Seed FTS5.
    build_fts_index(synthetic_db)

    # Wrap connection for the second build — should be all SELECT.
    counting = _CountingConn(synthetic_db)
    build_fts_index(counting)
    assert counting.fts_writes == [], (
        "no-op incremental should issue 0 INSERT/DELETE on symbol_fts; got: "
        + str(counting.fts_writes)
    )


def test_diff_only_writes_changed_rows(synthetic_db):
    """When 5 symbols are added and 5 removed, FTS5 should see exactly
    one INSERT batch (5 rows) and one DELETE — not a full rebuild.
    """
    from roam.search.index_embeddings import build_fts_index

    build_fts_index(synthetic_db)

    # Mutate.
    synthetic_db.execute("DELETE FROM symbols WHERE id <= 5")
    for i in range(5):
        synthetic_db.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
            "docstring, kind) VALUES (?, 1, ?, ?, '', '', 'function')",
            (200 + i, f"new_{i}", f"mod.new_{i}"),
        )
    synthetic_db.commit()

    counting = _CountingConn(synthetic_db)
    build_fts_index(counting)

    inserts = [w for w in counting.fts_writes if "insert " in w]
    deletes = [w for w in counting.fts_writes if "delete " in w]
    # One DELETE statement (chunked) + one INSERT batch — never a full
    # ``DELETE FROM symbol_fts``.
    assert any("delete from symbol_fts where rowid in" in w for w in deletes), (
        "expected scoped DELETE-by-rowid, got: " + str(deletes)
    )
    assert not any(w.strip() == "delete from symbol_fts" for w in deletes), (
        "incremental should NEVER do an unscoped DELETE FROM symbol_fts"
    )
    assert len(inserts) >= 1


# ---------------------------------------------------------------------------
# 3. Force flag — falls back to full rebuild for `roam index --rebuild`.
# ---------------------------------------------------------------------------


def test_force_does_full_rebuild(synthetic_db):
    from roam.search.index_embeddings import build_fts_index

    build_fts_index(synthetic_db)

    counting = _CountingConn(synthetic_db)
    build_fts_index(counting, force=True)

    # force=True must issue an unscoped DELETE FROM symbol_fts followed by
    # the full INSERT batch.
    deletes = [w for w in counting.fts_writes if "delete " in w]
    assert any(w.strip() == "delete from symbol_fts" for w in deletes), (
        "force rebuild should issue an unscoped DELETE; got: " + str(deletes)
    )
