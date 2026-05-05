"""redactedcoverage for the ``ROAM_QUERY_TIMEOUT_S`` interrupt path.

Pass 58 added an opt-in SQLite progress handler that aborts queries
running past N seconds. The path was untested. These tests pin both
the no-op default and the interrupt firing.
"""

from __future__ import annotations

import sqlite3
import time

import pytest


def _heavy_query(conn) -> None:
    """A query that's slow enough to trip a small timeout.

    We cross-join sqlite_master with itself a few times to generate
    work without needing any test data. The progress handler fires
    every 1000 vops, so even a small budget interrupts.
    """
    conn.execute(
        """
        WITH RECURSIVE r(n) AS (
            SELECT 0 UNION ALL SELECT n + 1 FROM r WHERE n < 50000
        )
        SELECT COUNT(*) FROM r a, r b LIMIT 1
        """
    ).fetchone()


def test_no_env_var_no_progress_handler(monkeypatch, tmp_path):
    """Default: no env var → no progress handler installed → query completes."""
    monkeypatch.delenv("ROAM_QUERY_TIMEOUT_S", raising=False)
    db_path = tmp_path / "x.db"
    from roam.db.connection import get_connection

    # First create the file so the open path doesn't try URI-readonly
    sqlite3.connect(str(db_path)).close()
    conn = get_connection(db_path, readonly=False)
    try:
        # No timeout → trivial query completes without raising.
        row = conn.execute("SELECT 1+1").fetchone()
        assert row[0] == 2
    finally:
        conn.close()


def test_invalid_value_silently_ignored(monkeypatch, tmp_path):
    """Bad ROAM_QUERY_TIMEOUT_S value should not crash the connection."""
    monkeypatch.setenv("ROAM_QUERY_TIMEOUT_S", "not-a-float")
    db_path = tmp_path / "x.db"
    sqlite3.connect(str(db_path)).close()
    from roam.db.connection import get_connection

    conn = get_connection(db_path, readonly=False)
    try:
        row = conn.execute("SELECT 1").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


def test_zero_or_negative_disabled(monkeypatch, tmp_path):
    """Zero / negative timeout → no handler installed."""
    db_path = tmp_path / "x.db"
    sqlite3.connect(str(db_path)).close()
    from roam.db.connection import get_connection

    for val in ("0", "-1", "0.0"):
        monkeypatch.setenv("ROAM_QUERY_TIMEOUT_S", val)
        conn = get_connection(db_path, readonly=False)
        try:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        finally:
            conn.close()


def test_tiny_timeout_aborts_long_query(monkeypatch, tmp_path):
    """redacteda 0.001s budget should interrupt a heavy CTE query."""
    db_path = tmp_path / "x.db"
    sqlite3.connect(str(db_path)).close()
    monkeypatch.setenv("ROAM_QUERY_TIMEOUT_S", "0.001")
    from roam.db.connection import get_connection

    conn = get_connection(db_path, readonly=False)
    try:
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            _heavy_query(conn)
        # Should abort within a sane budget (< 5s) — proves the handler fired
        assert time.monotonic() - start < 5.0
    finally:
        conn.close()
