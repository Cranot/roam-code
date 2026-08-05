"""W1510 — a light reindex must not silently desynchronise ``symbol_fts``.

Measured defect (2026-08-06, roam 13.10.0). ``Indexer.run(light=True)`` —
the path ``roam verify`` takes after every edit — refreshes ``symbols``
and ``edges`` but skips phase 7 (``search_indexes``). The modified files'
symbol rows are CASCADE-deleted and re-inserted under NEW autoincrement
ids, while their ``symbol_fts`` rows keep the OLD rowids. The result:

* the new symbol ids have no FTS row at all → those symbols can never be
  returned by lexical search (``_FTS5_SEARCH_SQL`` inner-joins
  ``symbols ON sf.rowid = s.id``, so orphan FTS rows are dropped);
* the old FTS rowids linger as orphans holding pre-edit text.

On the roam-code index this had reached 5,174 symbols missing from FTS and
4,012 orphan FTS rows — 11.4% of the corpus lexically invisible, costing
~13 points of recall@20 with no error and no degraded-mode disclosure.

The row COUNTS are not a sufficient signal: a light run that neither adds
nor removes symbols leaves ``COUNT(symbols) == COUNT(symbol_fts)`` while
every single FTS row is an orphan describing deleted text. These tests
therefore assert on the ROWID SETS and on retrievability, never on counts.
"""

from __future__ import annotations

import sqlite3

import pytest

ORIGINAL = "ZZORIGINALTOKEN"
MUTATED = "ZZMUTATEDTOKEN"


def _module_source(i: int, marker: str) -> str:
    return f'''"""Module {i}."""


def alpha_handler_{i}(request):
    """{marker} handles the inbound payload for shard {i}."""
    return request


class BetaService_{i}:
    """{marker} coordinates the shard {i} lifecycle."""

    def gamma_method_{i}(self, value):
        """{marker} normalizes the shard {i} value."""
        return value
'''


N_FILES = 3


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    """An isolated project root with its own index DB.

    ``ROAM_DB_DIR`` is cleared so ``get_db_path`` resolves to
    ``<tmp_path>/.roam/index.db`` and a developer's ambient env cannot
    redirect the test onto a shared index.
    """
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    for i in range(N_FILES):
        (tmp_path / f"mod_{i}.py").write_text(_module_source(i, ORIGINAL), encoding="utf-8")
    return tmp_path


def _write_marker(root, marker: str) -> None:
    """Rewrite every module IN PLACE — same symbol names, same symbol count."""
    for i in range(N_FILES):
        (root / f"mod_{i}.py").write_text(_module_source(i, marker), encoding="utf-8")


def _index(root, **kw) -> None:
    from roam.index.indexer import Indexer

    assert Indexer(project_root=root).run(quiet=True, progress_bar=False, **kw) is True


def _open(root) -> sqlite3.Connection:
    from roam.db.connection import get_db_path

    conn = sqlite3.connect("file:" + str(get_db_path(root)) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rowid_sets(conn) -> tuple[set[int], set[int]]:
    syms = {r[0] for r in conn.execute("SELECT id FROM symbols")}
    fts = {r[0] for r in conn.execute("SELECT rowid FROM symbol_fts")}
    return syms, fts


def _retrievable(conn, token: str) -> int:
    """Rows a real lexical search would return — the JOIN drops orphans."""
    return conn.execute(
        "SELECT COUNT(*) FROM symbol_fts sf JOIN symbols s ON sf.rowid = s.id WHERE symbol_fts MATCH ?",
        (token,),
    ).fetchone()[0]


def test_light_reindex_keeps_fts_rowids_in_sync(fixture_repo):
    """The negative control: FTS must describe the tree after a light run."""
    _index(fixture_repo, force=True)

    conn = _open(fixture_repo)
    syms, fts = _rowid_sets(conn)
    assert syms == fts, "precondition: a full index must leave FTS in lockstep"
    assert _retrievable(conn, ORIGINAL) == len(syms)
    conn.close()

    _write_marker(fixture_repo, MUTATED)
    _index(fixture_repo, light=True)

    conn = _open(fixture_repo)
    syms, fts = _rowid_sets(conn)
    assert syms == fts, (
        f"light reindex desynchronised symbol_fts: "
        f"{len(syms - fts)} symbols missing from FTS, {len(fts - syms)} orphan FTS rows"
    )
    assert _retrievable(conn, MUTATED) == len(syms), (
        "symbols edited by a light reindex are not retrievable by their new text"
    )
    assert _retrievable(conn, ORIGINAL) == 0, "pre-edit text is still retrievable after a light reindex"
    conn.close()


def test_light_reindex_desync_is_invisible_to_a_count_check(fixture_repo):
    """Counts must never be used as the staleness signal.

    An in-place edit keeps the symbol count constant, so ``COUNT(symbols)
    == COUNT(symbol_fts)`` holds even when every FTS row is an orphan.
    This pins WHY the fix asserts on rowid sets.
    """
    _index(fixture_repo, force=True)
    _write_marker(fixture_repo, MUTATED)
    _index(fixture_repo, light=True)

    conn = _open(fixture_repo)
    syms, fts = _rowid_sets(conn)
    conn.close()
    # Whatever the sync state, equal counts must not be read as "healthy".
    if len(syms) == len(fts):
        assert syms == fts, (
            "counts agree but rowid sets differ — a count-based health check "
            "would report this corrupted index as healthy"
        )


def test_fts_sync_state_reports_desync(fixture_repo):
    """A consumer must be able to LEARN the index is degraded (disclosure)."""
    from roam.search.index_embeddings import fts_sync_state

    _index(fixture_repo, force=True)
    _write_marker(fixture_repo, MUTATED)
    _index(fixture_repo, light=True)

    conn = _open(fixture_repo)
    state = fts_sync_state(conn)
    conn.close()
    assert state["verified"] is True
    assert state["in_sync"] is True, f"index reports degraded after fix: {state}"
    assert state["missing_from_fts"] == 0
    assert state["orphan_fts_rows"] == 0


def test_fts_sync_state_absent_table_reads_as_unknown_not_healthy():
    """No FTS table = UNKNOWN, never a benign 'in sync'."""
    from roam.search.index_embeddings import fts_sync_state

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY)")
    state = fts_sync_state(conn)
    conn.close()
    assert state["verified"] is False, "an unverifiable index must not report verified"
    assert state["in_sync"] is None, "absent measurement must read as UNKNOWN, not True"
