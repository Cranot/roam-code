"""Tests for the symbol-level co-change helpers.

These helpers are the β signal of the retrieve reranker, the
dark-matter check in `roam critique`, and the conflict-edge weighting
in `roam fleet plan`. One module, three downstream consumers — so the
contract has to be tight.
"""

from __future__ import annotations

import sqlite3

from roam.graph.dark_matter import (
    co_change_score,
    co_change_score_to_seed_set,
    file_co_change_score,
)


def _make_db_with_cochange(
    *,
    cochanges_ab: int,
    commits_a: int,
    commits_b: int,
) -> sqlite3.Connection:
    """Build a tiny in-memory DB with two files, optional symbols, and a
    single git_cochange row. Nothing else — the helpers query only what
    they need.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT);
        CREATE TABLE file_stats (file_id INTEGER PRIMARY KEY, commit_count INTEGER);
        CREATE TABLE git_cochange (file_id_a INTEGER, file_id_b INTEGER, cochange_count INTEGER);
        """
    )
    conn.executemany(
        "INSERT INTO files(id, path) VALUES (?, ?)",
        [(1, "src/a.py"), (2, "src/b.py")],
    )
    conn.executemany(
        "INSERT INTO symbols(id, file_id, name) VALUES (?, ?, ?)",
        [(10, 1, "alpha"), (20, 2, "beta")],
    )
    conn.executemany(
        "INSERT INTO file_stats(file_id, commit_count) VALUES (?, ?)",
        [(1, commits_a), (2, commits_b)],
    )
    if cochanges_ab > 0:
        conn.execute(
            "INSERT INTO git_cochange(file_id_a, file_id_b, cochange_count) VALUES (1, 2, ?)",
            (cochanges_ab,),
        )
    conn.commit()
    return conn


class TestFileCoChangeScore:
    def test_zero_when_same_file(self):
        conn = _make_db_with_cochange(cochanges_ab=0, commits_a=10, commits_b=10)
        assert file_co_change_score(conn, 1, 1) == 0.0

    def test_zero_when_no_cochange_row(self):
        conn = _make_db_with_cochange(cochanges_ab=0, commits_a=10, commits_b=10)
        assert file_co_change_score(conn, 1, 2) == 0.0

    def test_perfect_co_change_returns_one(self):
        """Two files that always change together (every commit touched both)."""
        conn = _make_db_with_cochange(cochanges_ab=10, commits_a=10, commits_b=10)
        # union = 10 + 10 - 10 = 10; jaccard = 10/10 = 1.0
        assert file_co_change_score(conn, 1, 2) == 1.0

    def test_partial_co_change_jaccard(self):
        """Files A=20 commits, B=20 commits, 5 shared → 5/(20+20-5)=0.143."""
        conn = _make_db_with_cochange(cochanges_ab=5, commits_a=20, commits_b=20)
        score = file_co_change_score(conn, 1, 2)
        assert 0.13 <= score <= 0.16

    def test_score_is_symmetric(self):
        """Order of arguments must not matter."""
        conn = _make_db_with_cochange(cochanges_ab=4, commits_a=8, commits_b=12)
        assert file_co_change_score(conn, 1, 2) == file_co_change_score(conn, 2, 1)

    def test_score_capped_at_one(self):
        """Even pathological data must stay in [0,1]."""
        conn = _make_db_with_cochange(cochanges_ab=10, commits_a=5, commits_b=5)
        # union = 5 + 5 - 10 = 0 → return 0.0 not negative
        assert file_co_change_score(conn, 1, 2) == 0.0

    def test_unknown_file_id_returns_zero(self):
        conn = _make_db_with_cochange(cochanges_ab=3, commits_a=10, commits_b=10)
        assert file_co_change_score(conn, 1, 999) == 0.0


class TestCoChangeScore:
    def test_resolves_symbol_to_file(self):
        conn = _make_db_with_cochange(cochanges_ab=10, commits_a=10, commits_b=10)
        # symbol 10 in file 1, symbol 20 in file 2
        assert co_change_score(conn, 10, 20) == 1.0

    def test_same_symbol_returns_zero(self):
        conn = _make_db_with_cochange(cochanges_ab=10, commits_a=10, commits_b=10)
        assert co_change_score(conn, 10, 10) == 0.0

    def test_unknown_symbol_returns_zero(self):
        conn = _make_db_with_cochange(cochanges_ab=10, commits_a=10, commits_b=10)
        assert co_change_score(conn, 10, 9999) == 0.0
        assert co_change_score(conn, 9999, 9998) == 0.0

    def test_same_file_symbols_return_zero(self):
        """Two symbols in the same file must not co-change with themselves."""
        conn = _make_db_with_cochange(cochanges_ab=5, commits_a=10, commits_b=10)
        conn.execute(
            "INSERT INTO symbols(id, file_id, name) VALUES (?, ?, ?)",
            (11, 1, "alpha2"),
        )
        conn.commit()
        assert co_change_score(conn, 10, 11) == 0.0


class TestCoChangeScoreToSeedSet:
    def test_empty_seeds_returns_zero(self):
        conn = _make_db_with_cochange(cochanges_ab=5, commits_a=10, commits_b=10)
        assert co_change_score_to_seed_set(conn, 10, []) == 0.0
        assert co_change_score_to_seed_set(conn, 10, set()) == 0.0

    def test_picks_max_across_seeds(self):
        """Multiple seeds — the candidate inherits the strongest link."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT);
            CREATE TABLE file_stats (file_id INTEGER PRIMARY KEY, commit_count INTEGER);
            CREATE TABLE git_cochange (file_id_a INTEGER, file_id_b INTEGER, cochange_count INTEGER);
            """
        )
        # 3 files: candidate (1), strong-seed (2), weak-seed (3)
        conn.executemany(
            "INSERT INTO files(id, path) VALUES (?, ?)",
            [(1, "cand.py"), (2, "strong.py"), (3, "weak.py")],
        )
        conn.executemany(
            "INSERT INTO symbols(id, file_id, name) VALUES (?, ?, ?)",
            [(10, 1, "C"), (20, 2, "S"), (30, 3, "W")],
        )
        conn.executemany(
            "INSERT INTO file_stats(file_id, commit_count) VALUES (?, ?)",
            [(1, 10), (2, 10), (3, 10)],
        )
        conn.executemany(
            "INSERT INTO git_cochange(file_id_a, file_id_b, cochange_count) VALUES (?, ?, ?)",
            [(1, 2, 9), (1, 3, 1)],  # strong link to S, weak to W
        )
        conn.commit()

        # Score against just S: high
        assert co_change_score_to_seed_set(conn, 10, [20]) > 0.5
        # Score against just W: low
        assert co_change_score_to_seed_set(conn, 10, [30]) < 0.2
        # Score against both: must be the max (the high one)
        max_both = co_change_score_to_seed_set(conn, 10, [20, 30])
        only_strong = co_change_score_to_seed_set(conn, 10, [20])
        assert max_both == only_strong

    def test_candidate_in_seed_set(self):
        """If the candidate's file is itself in the seed set, the
        same-file pair short-circuits to 0; the helper must skip it.
        """
        conn = _make_db_with_cochange(cochanges_ab=5, commits_a=10, commits_b=10)
        # Seed = the candidate's own symbol — should contribute 0 (same file).
        assert co_change_score_to_seed_set(conn, 10, [10]) == 0.0
