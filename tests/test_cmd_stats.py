"""Focused regressions for ``roam stats`` helpers."""

from __future__ import annotations

import sqlite3

import pytest

from roam.commands.cmd_stats import _count_git_commits


def test_count_git_commits_missing_table_returns_zero() -> None:
    conn = sqlite3.connect(":memory:")

    assert _count_git_commits(conn, "SELECT COUNT(*) FROM git_commits") == 0


def test_count_git_commits_missing_timestamp_returns_zero() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE git_commits (id INTEGER PRIMARY KEY)")

    assert (
        _count_git_commits(
            conn,
            "SELECT COUNT(*) FROM git_commits WHERE timestamp >= ?",
            (0,),
        )
        == 0
    )


def test_count_git_commits_unexpected_operational_error_raises() -> None:
    conn = sqlite3.connect(":memory:")

    with pytest.raises(sqlite3.OperationalError, match="no such function"):
        _count_git_commits(conn, "SELECT definitely_not_a_sqlite_function()")
