"""Unit tests for the shared batch-search implementation."""

from __future__ import annotations

import sqlite3
from importlib.util import module_from_spec, spec_from_file_location

import pytest

from tests._helpers.repo_root import repo_root

MODULE_PATH = repo_root() / "src" / "roam" / "commands" / "batch_search_core.py"
SPEC = spec_from_file_location("local_batch_search_core", MODULE_PATH)
assert SPEC is not None
batch_search_core = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(batch_search_core)

MAX_BATCH_QUERIES = batch_search_core.MAX_BATCH_QUERIES
batch_search_one = batch_search_core.batch_search_one


@pytest.fixture
def batch_search_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL
        );

        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            qualified_name TEXT,
            kind TEXT NOT NULL,
            file_id INTEGER NOT NULL,
            line_start INTEGER NOT NULL
        );

        CREATE TABLE graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL
        );
        """
    )
    conn.executemany(
        "INSERT INTO files (id, path) VALUES (?, ?)",
        [
            (1, "src/probes.py"),
            (2, "tests/composables/Probe/use_probe_test.py"),
            (3, "src/ties.py"),
            (4, "src/standalone.py"),
            (5, "src/widgets.py"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO symbols
            (id, name, qualified_name, kind, file_id, line_start)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "Probe", "pkg.Probe", "function", 1, 10),
            (2, "helper", "pkg.ProbeHelper", "function", 1, 20),
            (3, "setup", "pkg.fixtures.setup", "function", 2, 5),
            (4, "BetaTie", "pkg.BetaTie", "class", 3, 2),
            (5, "AlphaTie", "pkg.AlphaTie", "class", 3, 1),
            (6, "standalone", None, "function", 4, 7),
            # No graph_metrics row for symbol 7 — exercises the LEFT JOIN +
            # COALESCE path (a symbol indexed before PageRank is computed).
            (7, "Widget", "pkg.Widget", "class", 5, 30),
        ],
    )
    conn.executemany(
        "INSERT INTO graph_metrics (symbol_id, pagerank) VALUES (?, ?)",
        [
            (1, 0.1234567),
            (2, 0.02),
            (3, 0.5),
            (4, 0.01),
            (5, 0.01),
            (6, None),
        ],
    )
    try:
        yield conn
    finally:
        conn.close()


def test_max_batch_queries_contract():
    assert MAX_BATCH_QUERIES == 10


def test_default_search_matches_name_and_qualified_name_but_not_file_path(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "probe", 10)

    assert err is None
    assert rows == [
        {
            "name": "Probe",
            "qualified_name": "pkg.Probe",
            "kind": "function",
            "file_path": "src/probes.py",
            "line_start": 10,
            "pagerank": 0.123457,
        },
        {
            "name": "helper",
            "qualified_name": "pkg.ProbeHelper",
            "kind": "function",
            "file_path": "src/probes.py",
            "line_start": 20,
            "pagerank": 0.02,
        },
    ]


def test_include_paths_adds_file_path_matches(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "probe", 10, include_paths=True)

    assert err is None
    assert [row["name"] for row in rows] == ["setup", "Probe", "helper"]
    assert rows[0]["file_path"] == "tests/composables/Probe/use_probe_test.py"


def test_ordering_uses_pagerank_descending_then_symbol_name_and_limit(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "tie", 1)

    assert err is None
    assert [row["name"] for row in rows] == ["AlphaTie"]


def test_row_mapping_normalizes_null_qualified_name_and_pagerank(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "standalone", 5)

    assert err is None
    assert rows == [
        {
            "name": "standalone",
            "qualified_name": "",
            "kind": "function",
            "file_path": "src/standalone.py",
            "line_start": 7,
            "pagerank": 0.0,
        }
    ]


def test_no_matches_return_empty_rows_without_error(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "missing", 5)

    assert rows == []
    assert err is None


def test_default_sql_error_returns_empty_rows_and_error(batch_search_conn):
    rows, err = batch_search_one(
        batch_search_conn,
        "probe",
        5,
        like_sql=("SELECT * FROM missing_table WHERE a LIKE ? OR b LIKE ? LIMIT ?"),
    )

    assert rows == []
    assert err is not None
    assert "missing_table" in err


def test_include_paths_sql_error_returns_empty_rows_and_error(batch_search_conn):
    rows, err = batch_search_one(
        batch_search_conn,
        "probe",
        5,
        include_paths=True,
        like_with_paths_sql=("SELECT * FROM missing_table WHERE a LIKE ? OR b LIKE ? OR c LIKE ? LIMIT ?"),
    )

    assert rows == []
    assert err is not None
    assert "missing_table" in err


def test_empty_query_matches_every_symbol_in_ranked_order(batch_search_conn):
    # Core does not filter empty queries (CLI/MCP callers do); "" -> "%%" matches
    # all. ORDER BY has no COLLATE NOCASE, so the name tiebreak is BINARY
    # ("Widget" < "standalone").
    rows, err = batch_search_one(batch_search_conn, "", 10)

    assert err is None
    assert [row["name"] for row in rows] == [
        "setup",
        "Probe",
        "helper",
        "AlphaTie",
        "BetaTie",
        "Widget",
        "standalone",
    ]


def test_limit_zero_returns_no_rows_even_when_matches_exist(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "probe", 0)

    assert rows == []
    assert err is None


def test_limit_above_match_count_returns_all_matches(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "probe", 50)

    assert err is None
    assert [row["name"] for row in rows] == ["Probe", "helper"]


def test_case_insensitive_match_accepts_uppercase_and_mixedcase_query(batch_search_conn):
    upper_rows, upper_err = batch_search_one(batch_search_conn, "PROBE", 10)
    mixed_rows, mixed_err = batch_search_one(batch_search_conn, "tIe", 10)

    assert upper_err is None
    assert [row["name"] for row in upper_rows] == ["Probe", "helper"]
    assert mixed_err is None
    assert [row["name"] for row in mixed_rows] == ["AlphaTie", "BetaTie"]


def test_equal_pagerank_tiebreak_is_full_name_ascending_sequence(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "tie", 2)

    assert err is None
    assert [row["name"] for row in rows] == ["AlphaTie", "BetaTie"]


def test_symbol_without_graph_metrics_row_defaults_to_zero_pagerank(batch_search_conn):
    rows, err = batch_search_one(batch_search_conn, "widget", 5)

    assert err is None
    assert rows == [
        {
            "name": "Widget",
            "qualified_name": "pkg.Widget",
            "kind": "class",
            "file_path": "src/widgets.py",
            "line_start": 30,
            "pagerank": 0.0,
        }
    ]


def test_row_mapping_error_is_not_caught_only_execute_errors_are(batch_search_conn):
    # The try/except wraps conn.execute().fetchall() only. A custom SQL that
    # executes cleanly but returns rows missing required columns propagates the
    # mapping error to the caller (CLI/MCP each wrap _batch_search_one in their
    # own try/except — see the W103 note in mcp_server.py).
    bad_sql = "SELECT s.name FROM symbols s WHERE (s.name LIKE ? OR s.name LIKE ?) LIMIT ?"
    with pytest.raises(IndexError):
        batch_search_one(batch_search_conn, "probe", 5, like_sql=bad_sql)
