"""ROADMAP B8 — docstring column on the FTS5 symbol_fts virtual table.

Before B8: ``symbol_fts`` was ``(name, qualified_name, signature, kind,
file_path)`` — the BM25 ranker never saw docstring text, so a natural-
language query like ``"trace login flow"`` returned only symbols whose
*name* contained those tokens. After B8 the column list is
``(name, qualified_name, signature, docstring, kind, file_path)`` and
BM25 weights docstring at 4.0 (high enough that natural-language
queries hit, lower than name=10/qname=5 so exact-name still ranks
first).

This file pins four invariants:

1. The FTS5 schema includes a ``docstring`` column after migration.
2. ``build_fts_index`` populates the docstring column on INSERT.
3. A query that only appears in the docstring returns the symbol —
   proves the BM25 path actually reads the new column.
4. The BM25 weight vector has docstring at 4.0 in the correct slot
   (catches column-order regressions silently passing).
"""

from __future__ import annotations

import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Fixture: minimal DB whose schema matches the production one closely
# enough to exercise the FTS5 docstring path. We deliberately use the
# *real* ``_ensure_fts5_table`` to catch column-list drift in connection.py.
# ---------------------------------------------------------------------------


@pytest.fixture
def fts_db(monkeypatch):
    """In-memory connection seeded with symbols + the real FTS5 table.

    Patches the optional vector builders to no-ops so we exercise just
    the FTS5 sync path.
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
        """
    )

    # Use the production helper to create symbol_fts — this is the whole
    # point of the test, we want column drift in _ensure_fts5_table to
    # break this fixture.
    from roam.db.connection import _ensure_fts5_table

    _ensure_fts5_table(conn)

    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/auth.py')")

    import roam.search.index_embeddings as ie

    monkeypatch.setattr(ie, "build_and_store_tfidf", lambda c: None)
    monkeypatch.setattr(ie, "build_and_store_onnx_embeddings", lambda *a, **kw: None)

    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Schema invariant — docstring column lives on symbol_fts.
# ---------------------------------------------------------------------------


def test_symbol_fts_has_docstring_column(fts_db):
    """FTS5 virtual table must include ``docstring`` after migration.

    Regression guard: if someone reorders _FTS5_SCHEMA_COLUMNS or drops
    docstring, this fails immediately rather than silently degrading
    retrieve recall@20.
    """
    cols = {r[1] for r in fts_db.execute("PRAGMA table_info(symbol_fts)").fetchall()}
    assert "docstring" in cols, (
        f"symbol_fts is missing the docstring column; got {sorted(cols)}. "
        "Audit B8 requires docstring in the FTS5 schema."
    )


def test_fts5_schema_columns_order_is_stable(fts_db):
    """The column order must match the BM25 weight vector.

    BM25 weights are positional in SQLite — if a column moves, the
    weight applied to it changes silently. This test pins the order.
    """
    rows = fts_db.execute("PRAGMA table_info(symbol_fts)").fetchall()
    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk).
    cols_in_order = [r[1] for r in sorted(rows, key=lambda r: r[0])]
    assert cols_in_order == [
        "name",
        "qualified_name",
        "signature",
        "docstring",
        "kind",
        "file_path",
    ], (
        f"FTS5 column order drifted to {cols_in_order}; this silently "
        "changes which column each BM25 weight applies to. Update "
        "_BM25_WEIGHTS in search/index_embeddings.py in lockstep."
    )


# ---------------------------------------------------------------------------
# 2. Population invariant — build_fts_index writes docstring into FTS5.
# ---------------------------------------------------------------------------


def test_docstring_indexed_on_insert(fts_db):
    """Inserting a symbol with a docstring populates the FTS5 column.

    The fixture inserts a symbol whose docstring is the *only* place
    a sentinel phrase appears. After build_fts_index, the docstring
    column must contain that phrase verbatim.
    """
    fts_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
        "docstring, kind) VALUES (1, 1, 'authenticate', 'auth.authenticate', "
        "'def authenticate(creds)', 'Verify user credentials against the "
        "auth backend.', 'function')"
    )
    fts_db.commit()

    from roam.search.index_embeddings import build_fts_index

    build_fts_index(fts_db)

    row = fts_db.execute("SELECT docstring FROM symbol_fts WHERE rowid = 1").fetchone()
    assert row is not None, "symbol was not inserted into symbol_fts"
    assert "Verify user credentials" in row["docstring"], (
        f"docstring not populated in symbol_fts; got {row['docstring']!r}"
    )


# ---------------------------------------------------------------------------
# 3. Behavioural invariant — querying a docstring-only term finds the symbol.
# ---------------------------------------------------------------------------


def test_retrieve_uses_docstring_for_match(fts_db):
    """Phrase that exists only in the docstring must surface the symbol.

    The symbol's *name* is intentionally unrelated to the query.
    Without B8's docstring column, FTS5 would never see the phrase
    and the symbol would not match. This is the test that pins the
    15-25% recall@20 improvement claim from the ROADMAP.
    """
    # name=foo, docstring contains the phrase "trace login flow"
    fts_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
        "docstring, kind) VALUES (1, 1, 'foo', 'pkg.foo', 'def foo()', "
        "'Trace the login flow end to end for debugging.', 'function')"
    )
    # A second symbol with NO docstring match — control.
    fts_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
        "docstring, kind) VALUES (2, 1, 'bar', 'pkg.bar', 'def bar()', "
        "'Unrelated helper.', 'function')"
    )
    fts_db.commit()

    from roam.search.index_embeddings import build_fts_index, search_fts

    build_fts_index(fts_db)

    hits = search_fts(fts_db, "trace login flow", top_k=10)
    names = [h["name"] for h in hits]
    assert "foo" in names, (
        f"Docstring-only match failed: query 'trace login flow' should "
        f"return 'foo' (its docstring contains the phrase) but got {names}. "
        "This is the core B8 regression — FTS5 is not seeing docstrings."
    )


# ---------------------------------------------------------------------------
# 4. BM25-weight invariant — docstring weight = 4.0 in the correct slot.
# ---------------------------------------------------------------------------


def test_bm25_weights_docstring_at_4():
    """The BM25 weight vector must place 4.0 at the docstring slot.

    Audit B8 chose 4.0 deliberately: high enough that natural-language
    queries hit docstring text, lower than name=10/qname=5 so a query
    that also matches the name still ranks the name-match higher.
    """
    from roam.db.connection import _FTS5_SCHEMA_COLUMNS
    from roam.search.index_embeddings import _BM25_WEIGHTS

    weights = [float(w.strip()) for w in _BM25_WEIGHTS.split(",")]
    assert len(weights) == len(_FTS5_SCHEMA_COLUMNS), (
        f"_BM25_WEIGHTS has {len(weights)} entries but _FTS5_SCHEMA_COLUMNS "
        f"has {len(_FTS5_SCHEMA_COLUMNS)} — these must match in length."
    )
    docstring_idx = _FTS5_SCHEMA_COLUMNS.index("docstring")
    assert weights[docstring_idx] == 4.0, (
        f"docstring BM25 weight = {weights[docstring_idx]}, expected 4.0. "
        f"Full vector: {weights}, columns: {_FTS5_SCHEMA_COLUMNS}. "
        "Audit B8 mandates a 4.0 weight on docstring."
    )
    # Sanity: name still outranks docstring (so exact-name matches win).
    name_idx = _FTS5_SCHEMA_COLUMNS.index("name")
    assert weights[name_idx] > weights[docstring_idx], (
        f"name weight ({weights[name_idx]}) should outrank docstring "
        f"({weights[docstring_idx]}) so exact-name still ranks first."
    )


def test_name_match_outranks_docstring_match(fts_db):
    """When the query matches both name and docstring, name wins.

    Symbol A has the query as its name; symbol B only has it in its
    docstring. The BM25 weighting (name=10 vs docstring=4) must make A
    rank above B.
    """
    fts_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
        "docstring, kind) VALUES (1, 1, 'login', 'auth.login', 'def login()', "
        "'', 'function')"
    )
    fts_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, signature, "
        "docstring, kind) VALUES (2, 1, 'helper', 'auth.helper', "
        "'def helper()', 'Implements the login flow.', 'function')"
    )
    fts_db.commit()

    from roam.search.index_embeddings import build_fts_index, search_fts

    build_fts_index(fts_db)

    hits = search_fts(fts_db, "login", top_k=10)
    assert len(hits) >= 2, f"expected both symbols to match, got {hits}"
    names = [h["name"] for h in hits]
    assert names.index("login") < names.index("helper"), f"name match should outrank docstring match; got order {names}"
