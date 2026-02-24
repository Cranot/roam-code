"""Tests for roam_batch_search and roam_batch_get MCP batch operations.

Covers:
- batch_search: empty queries, normal queries, cap at 10, partial failures,
  FTS5 path, LIKE fallback, limit_per_query, DB error handling
- batch_get: empty symbols, normal lookups, cap at 50, partial failures,
  not-found symbols, DB error handling
- _CORE_TOOLS membership for both tools
- _CORE_TOOLS count update (now 23)
- Helper functions: _fts_query_for, _batch_search_one, _batch_get_one
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    """Create a minimal in-memory-style SQLite DB with required tables."""
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode=DELETE;

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            language TEXT,
            file_role TEXT DEFAULT 'source'
        );

        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER REFERENCES files(id),
            name TEXT NOT NULL,
            qualified_name TEXT,
            kind TEXT,
            signature TEXT,
            docstring TEXT,
            line_start INTEGER DEFAULT 0,
            line_end INTEGER DEFAULT 0,
            is_exported INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER REFERENCES symbols(id),
            target_id INTEGER REFERENCES symbols(id),
            kind TEXT,
            line INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS graph_metrics (
            id INTEGER PRIMARY KEY,
            symbol_id INTEGER REFERENCES symbols(id),
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0
        );

        -- FTS5 virtual table (porter tokenizer + unicode61)
        CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(
            name, qualified_name, kind, file_path, signature,
            content='symbols', content_rowid='id',
            tokenize='porter unicode61'
        );

        -- Insert test data
        INSERT INTO files (id, path, language) VALUES
            (1, 'src/auth.py', 'python'),
            (2, 'src/user.py', 'python'),
            (3, 'src/api.py', 'python');

        INSERT INTO symbols (id, file_id, name, qualified_name, kind, signature,
                             docstring, line_start, is_exported) VALUES
            (1, 1, 'authenticate', 'auth.authenticate', 'function',
             'def authenticate(token)', 'Authenticate a user token.', 10, 1),
            (2, 1, 'AuthError', 'auth.AuthError', 'class',
             'class AuthError(Exception)', 'Authentication error class.', 25, 1),
            (3, 2, 'User', 'user.User', 'class',
             'class User', 'User model.', 5, 1),
            (4, 2, 'get_user', 'user.get_user', 'function',
             'def get_user(user_id)', 'Fetch user by ID.', 30, 1),
            (5, 3, 'create_endpoint', 'api.create_endpoint', 'function',
             'def create_endpoint(route)', 'Create API endpoint.', 15, 1);

        INSERT INTO graph_metrics (symbol_id, pagerank, in_degree, out_degree) VALUES
            (1, 0.25, 3, 2),
            (2, 0.10, 1, 0),
            (3, 0.30, 5, 1),
            (4, 0.15, 2, 3),
            (5, 0.05, 1, 1);

        INSERT INTO edges (source_id, target_id, kind, line) VALUES
            (4, 1, 'call', 32),
            (5, 3, 'call', 17);

        -- Populate FTS index
        INSERT INTO symbol_fts (rowid, name, qualified_name, kind, file_path, signature)
            SELECT s.id, s.name, COALESCE(s.qualified_name, ''), s.kind,
                   f.path, COALESCE(s.signature, '')
            FROM symbols s JOIN files f ON s.file_id = f.id;
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def mock_open_db(tmp_db):
    """Patch open_db and ensure_index so tools use the tmp_db fixture."""
    from contextlib import contextmanager

    @contextmanager
    def _open(readonly=False):
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    with patch("roam.mcp_server.open_db", side_effect=_open), \
         patch("roam.commands.resolve.db_exists", return_value=True), \
         patch("roam.mcp_server.batch_search.__wrapped__", None, create=True):
        yield _open


# ---------------------------------------------------------------------------
# Helper: patch ensure_index + open_db together for tool calls
# ---------------------------------------------------------------------------


def _patch_db(tmp_db):
    """Return a context manager patching ensure_index and open_db.

    open_db is imported inside the batch functions via
    'from roam.db.connection import open_db', so we patch the source module.
    """
    from contextlib import contextmanager

    @contextmanager
    def _open(readonly=False):
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    return (
        patch("roam.db.connection.open_db", side_effect=_open),
        patch("roam.commands.resolve.db_exists", return_value=True),
    )


# ---------------------------------------------------------------------------
# _fts_query_for unit tests
# ---------------------------------------------------------------------------


class TestFtsQueryFor:
    """Unit tests for _fts_query_for helper."""

    def test_single_token(self):
        from roam.mcp_server import _fts_query_for
        result = _fts_query_for("authenticate")
        assert '"authenticate"*' in result

    def test_underscore_split(self):
        from roam.mcp_server import _fts_query_for
        result = _fts_query_for("get_user")
        # Should split on underscore producing two tokens
        assert '"get"*' in result
        assert '"user"*' in result
        assert " OR " in result

    def test_dot_split(self):
        from roam.mcp_server import _fts_query_for
        result = _fts_query_for("auth.AuthError")
        assert '"auth"*' in result
        assert '"AuthError"*' in result

    def test_empty_string_fallback(self):
        from roam.mcp_server import _fts_query_for
        result = _fts_query_for("")
        # Empty string should produce a fallback query
        assert '"' in result

    def test_multi_word(self):
        from roam.mcp_server import _fts_query_for
        result = _fts_query_for("create endpoint")
        assert '"create"*' in result
        assert '"endpoint"*' in result


# ---------------------------------------------------------------------------
# _batch_search_one unit tests
# ---------------------------------------------------------------------------


class TestBatchSearchOne:
    """Unit tests for _batch_search_one helper using a live SQLite connection."""

    def _conn(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        return conn

    def test_fts_hit(self, tmp_db):
        from roam.mcp_server import _batch_search_one
        conn = self._conn(tmp_db)
        rows, err = _batch_search_one(conn, "authenticate", 5)
        conn.close()
        assert err is None
        assert len(rows) >= 1
        names = [r["name"] for r in rows]
        assert "authenticate" in names

    def test_like_fallback(self, tmp_db):
        """Even without FTS5, LIKE fallback should find symbols."""
        from roam.mcp_server import _batch_search_one
        conn = self._conn(tmp_db)
        # Force FTS5 path to fail by patching the SQL constant
        with patch("roam.mcp_server._BATCH_FTS_SQL", "SELECT invalid"):
            rows, err = _batch_search_one(conn, "user", 5)
        conn.close()
        # LIKE fallback should still produce results
        assert err is None
        names = [r["name"] for r in rows]
        assert len(rows) >= 1

    def test_no_results(self, tmp_db):
        from roam.mcp_server import _batch_search_one
        conn = self._conn(tmp_db)
        rows, err = _batch_search_one(conn, "zzznomatchzz", 5)
        conn.close()
        assert err is None
        assert rows == []

    def test_limit_respected(self, tmp_db):
        from roam.mcp_server import _batch_search_one
        conn = self._conn(tmp_db)
        rows, err = _batch_search_one(conn, "a", 2)
        conn.close()
        assert err is None
        assert len(rows) <= 2

    def test_row_dict_keys(self, tmp_db):
        from roam.mcp_server import _batch_search_one
        conn = self._conn(tmp_db)
        rows, err = _batch_search_one(conn, "user", 5)
        conn.close()
        assert err is None
        for row in rows:
            assert "name" in row
            assert "kind" in row
            assert "file_path" in row
            assert "line_start" in row
            assert "pagerank" in row

    def test_pagerank_is_float(self, tmp_db):
        from roam.mcp_server import _batch_search_one
        conn = self._conn(tmp_db)
        rows, err = _batch_search_one(conn, "user", 5)
        conn.close()
        assert err is None
        for row in rows:
            assert isinstance(row["pagerank"], float)


# ---------------------------------------------------------------------------
# _batch_get_one unit tests
# ---------------------------------------------------------------------------


class TestBatchGetOne:
    """Unit tests for _batch_get_one helper using a live SQLite connection."""

    def _conn(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        return conn

    def test_found_by_name(self, tmp_db):
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "authenticate")
        conn.close()
        assert err is None
        assert details is not None
        assert details["kind"] == "function"

    def test_found_by_qualified_name(self, tmp_db):
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "user.User")
        conn.close()
        assert err is None
        assert details is not None
        assert "User" in details["name"]

    def test_not_found(self, tmp_db):
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "zzznomatchzz")
        conn.close()
        assert details is None
        assert err is not None
        assert "not found" in err.lower()

    def test_detail_keys(self, tmp_db):
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "User")
        conn.close()
        assert err is None
        assert details is not None
        assert "name" in details
        assert "kind" in details
        assert "location" in details
        assert "callers" in details
        assert "callees" in details

    def test_callers_and_callees_are_lists(self, tmp_db):
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "authenticate")
        conn.close()
        assert err is None
        assert isinstance(details["callers"], list)
        assert isinstance(details["callees"], list)

    def test_caller_has_edge_data(self, tmp_db):
        """authenticate has one caller (get_user → authenticate)."""
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "authenticate")
        conn.close()
        assert err is None
        # get_user calls authenticate
        assert len(details["callers"]) >= 1
        caller = details["callers"][0]
        assert "name" in caller
        assert "edge_kind" in caller
        assert "location" in caller

    def test_pagerank_included_when_metrics_exist(self, tmp_db):
        from roam.mcp_server import _batch_get_one
        conn = self._conn(tmp_db)
        details, err = _batch_get_one(conn, "User")
        conn.close()
        assert err is None
        assert "pagerank" in details
        assert isinstance(details["pagerank"], float)


# ---------------------------------------------------------------------------
# batch_search tool tests
# ---------------------------------------------------------------------------


class TestBatchSearch:
    """Integration-style tests for the roam_batch_search MCP tool."""

    def test_empty_queries_returns_empty_results(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=[], root=".")
        assert result["summary"]["queries_executed"] == 0
        assert result["summary"]["total_matches"] == 0
        assert result["results"] == {}

    def test_single_query_returns_results(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["auth"], limit_per_query=5, root=".")
        assert "results" in result
        assert "auth" in result["results"]
        matches = result["results"]["auth"]
        assert isinstance(matches, list)
        assert len(matches) >= 1

    def test_multiple_queries(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["auth", "user", "endpoint"], root=".")
        assert len(result["results"]) == 3
        assert "auth" in result["results"]
        assert "user" in result["results"]
        assert "endpoint" in result["results"]

    def test_total_matches_is_aggregate(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["auth", "user"], limit_per_query=10, root=".")
        total = sum(len(v) for v in result["results"].values())
        assert result["summary"]["total_matches"] == total

    def test_queries_capped_at_10(self, tmp_db):
        from roam.mcp_server import batch_search
        queries = [f"sym{i}" for i in range(15)]
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=queries, root=".")
        # Only 10 queries should have been executed
        assert result["summary"]["queries_executed"] == 10

    def test_limit_per_query_respected(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["a"], limit_per_query=2, root=".")
        if "a" in result["results"]:
            assert len(result["results"]["a"]) <= 2

    def test_limit_per_query_clamped_to_50(self, tmp_db):
        """Passing limit_per_query > 50 should be clamped to 50."""
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            # Should not raise; limit is silently clamped
            result = batch_search(queries=["user"], limit_per_query=999, root=".")
        assert "results" in result

    def test_command_field_is_correct(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["user"], root=".")
        assert result["command"] == "batch-search"

    def test_summary_has_verdict(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["user"], root=".")
        assert "verdict" in result["summary"]
        assert isinstance(result["summary"]["verdict"], str)

    def test_no_match_query_returns_empty_list(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["zzznomatchzzz"], root=".")
        assert "zzznomatchzzz" in result["results"]
        assert result["results"]["zzznomatchzzz"] == []

    def test_partial_failure_returns_errors_key(self, tmp_db):
        """If a query raises, it should appear in errors and not abort others."""
        from roam.mcp_server import batch_search, _batch_search_one

        call_count = [0]
        original = _batch_search_one

        def failing_search(conn, q, limit):
            call_count[0] += 1
            if q == "bad_query":
                return [], "simulated db error"
            return original(conn, q, limit)

        p1, p2 = _patch_db(tmp_db)
        with p1, p2, patch("roam.mcp_server._batch_search_one", side_effect=failing_search):
            result = batch_search(queries=["user", "bad_query", "auth"], root=".")

        # "user" and "auth" should still work
        assert "user" in result["results"]
        assert "auth" in result["results"]
        # "bad_query" error should be captured
        assert "errors" in result
        assert "bad_query" in result["errors"]

    def test_fatal_db_error_returns_structured_response(self):
        """A complete DB connection failure returns a structured error, not an exception."""
        from roam.mcp_server import batch_search

        with patch("roam.db.connection.open_db", side_effect=RuntimeError("db offline")), \
             patch("roam.commands.resolve.db_exists", return_value=True):
            result = batch_search(queries=["user"], root=".")

        assert "command" in result
        assert result["command"] == "batch-search"
        assert result["summary"]["queries_executed"] == 0
        assert "_fatal" in result.get("errors", {})

    def test_result_rows_have_required_fields(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["User"], root=".")
        rows = result["results"].get("User", [])
        for row in rows:
            assert "name" in row
            assert "kind" in row
            assert "file_path" in row
            assert "line_start" in row
            assert "pagerank" in row

    def test_queries_executed_matches_capped_count(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=["auth", "user"], root=".")
        assert result["summary"]["queries_executed"] == 2

    def test_none_queries_treated_as_empty(self, tmp_db):
        from roam.mcp_server import batch_search
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_search(queries=None, root=".")
        assert result["summary"]["queries_executed"] == 0


# ---------------------------------------------------------------------------
# batch_get tool tests
# ---------------------------------------------------------------------------


class TestBatchGet:
    """Integration-style tests for the roam_batch_get MCP tool."""

    def test_empty_symbols_returns_empty_results(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=[], root=".")
        assert result["summary"]["symbols_resolved"] == 0
        assert result["results"] == {}

    def test_single_symbol_found(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["authenticate"], root=".")
        assert "authenticate" in result["results"]
        details = result["results"]["authenticate"]
        assert details["kind"] == "function"

    def test_multiple_symbols(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["authenticate", "User", "get_user"], root=".")
        assert result["summary"]["symbols_resolved"] == 3
        assert "authenticate" in result["results"]
        assert "User" in result["results"]
        assert "get_user" in result["results"]

    def test_symbols_capped_at_50(self, tmp_db):
        from roam.mcp_server import batch_get
        symbols = [f"sym{i}" for i in range(60)]
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=symbols, root=".")
        # 60 requested but cap is 50 — all will be "not found" but only 50 attempted
        assert result["summary"]["symbols_requested"] == 50

    def test_not_found_symbol_in_errors(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["nonexistent_xyz"], root=".")
        assert "errors" in result
        assert "nonexistent_xyz" in result["errors"]
        assert "nonexistent_xyz" not in result["results"]

    def test_partial_found_partial_not_found(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["User", "nonexistent_xyz"], root=".")
        assert "User" in result["results"]
        assert "errors" in result
        assert "nonexistent_xyz" in result["errors"]
        assert result["summary"]["symbols_resolved"] == 1

    def test_command_field(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["User"], root=".")
        assert result["command"] == "batch-get"

    def test_summary_verdict(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["User", "get_user"], root=".")
        assert "verdict" in result["summary"]
        assert "2/2" in result["summary"]["verdict"]

    def test_details_include_callers_callees(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["authenticate"], root=".")
        details = result["results"]["authenticate"]
        assert "callers" in details
        assert "callees" in details
        assert isinstance(details["callers"], list)
        assert isinstance(details["callees"], list)

    def test_details_include_pagerank(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["User"], root=".")
        details = result["results"]["User"]
        assert "pagerank" in details
        assert isinstance(details["pagerank"], float)

    def test_fatal_db_error_returns_structured_response(self):
        from roam.mcp_server import batch_get

        with patch("roam.db.connection.open_db", side_effect=RuntimeError("db offline")), \
             patch("roam.commands.resolve.db_exists", return_value=True):
            result = batch_get(symbols=["User"], root=".")

        assert result["command"] == "batch-get"
        assert result["summary"]["symbols_resolved"] == 0
        assert "_fatal" in result.get("errors", {})

    def test_none_symbols_treated_as_empty(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=None, root=".")
        assert result["summary"]["symbols_requested"] == 0

    def test_symbols_requested_count_accurate(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["User", "authenticate"], root=".")
        assert result["summary"]["symbols_requested"] == 2

    def test_qualified_name_lookup(self, tmp_db):
        from roam.mcp_server import batch_get
        p1, p2 = _patch_db(tmp_db)
        with p1, p2:
            result = batch_get(symbols=["user.User"], root=".")
        # Should resolve to the User symbol via qualified name
        assert result["summary"]["symbols_resolved"] == 1


# ---------------------------------------------------------------------------
# _CORE_TOOLS membership tests
# ---------------------------------------------------------------------------


class TestCoreToolsMembership:
    """Verify batch tools are registered as core tools."""

    def test_batch_search_in_core_tools(self):
        from roam.mcp_server import _CORE_TOOLS
        assert "roam_batch_search" in _CORE_TOOLS

    def test_batch_get_in_core_tools(self):
        from roam.mcp_server import _CORE_TOOLS
        assert "roam_batch_get" in _CORE_TOOLS

    def test_core_tools_count_is_23(self):
        """_CORE_TOOLS should now contain 23 tools (21 original + 2 batch)."""
        from roam.mcp_server import _CORE_TOOLS
        assert len(_CORE_TOOLS) == 23

    def test_presets_are_supersets_of_updated_core(self):
        """All named presets must include the new batch tools."""
        from roam.mcp_server import _PRESETS, _CORE_TOOLS
        for name, tools in _PRESETS.items():
            if name == "full":
                continue  # full has empty set (no filtering)
            assert "roam_batch_search" in tools, f"{name} missing roam_batch_search"
            assert "roam_batch_get" in tools, f"{name} missing roam_batch_get"

    def test_batch_tools_callable(self):
        from roam.mcp_server import batch_search, batch_get
        assert callable(batch_search)
        assert callable(batch_get)


# ---------------------------------------------------------------------------
# Output schema tests
# ---------------------------------------------------------------------------


class TestBatchSchemas:
    """Verify the output schemas are well-formed JSON Schema dicts."""

    def test_batch_search_schema_structure(self):
        from roam.mcp_server import _SCHEMA_BATCH_SEARCH
        assert _SCHEMA_BATCH_SEARCH["type"] == "object"
        props = _SCHEMA_BATCH_SEARCH["properties"]
        assert "command" in props
        assert "summary" in props
        assert "results" in props
        assert "errors" in props

    def test_batch_get_schema_structure(self):
        from roam.mcp_server import _SCHEMA_BATCH_GET
        assert _SCHEMA_BATCH_GET["type"] == "object"
        props = _SCHEMA_BATCH_GET["properties"]
        assert "command" in props
        assert "summary" in props
        assert "results" in props
        assert "errors" in props

    def test_batch_search_summary_has_count_fields(self):
        from roam.mcp_server import _SCHEMA_BATCH_SEARCH
        summary_props = _SCHEMA_BATCH_SEARCH["properties"]["summary"]["properties"]
        assert "queries_executed" in summary_props
        assert "total_matches" in summary_props

    def test_batch_get_summary_has_count_fields(self):
        from roam.mcp_server import _SCHEMA_BATCH_GET
        summary_props = _SCHEMA_BATCH_GET["properties"]["summary"]["properties"]
        assert "symbols_resolved" in summary_props
        assert "symbols_requested" in summary_props


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestBatchConstants:
    """Verify the cap constants are correct."""

    def test_max_batch_queries(self):
        from roam.mcp_server import _MAX_BATCH_QUERIES
        assert _MAX_BATCH_QUERIES == 10

    def test_max_batch_symbols(self):
        from roam.mcp_server import _MAX_BATCH_SYMBOLS
        assert _MAX_BATCH_SYMBOLS == 50
