from __future__ import annotations

import sqlite3

import pytest

from roam.commands import cmd_db_check
from roam.commands.cmd_db_check import _check_missing_fts, _check_zero_symbols_per_file, _run_checks


class _RaisingConn:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def execute(self, _sql: str):
        raise self.exc


def test_check_missing_fts_handles_missing_fts_table() -> None:
    finding = _check_missing_fts(_RaisingConn(sqlite3.OperationalError("no such table: symbol_fts")))

    assert finding == {
        "name": "missing_fts_rows",
        "count": 0,
        "severity": "ok",
        "note": "fts5 not available",
    }


def test_check_missing_fts_propagates_unexpected_errors() -> None:
    with pytest.raises(ValueError, match="bad cursor state"):
        _check_missing_fts(_RaisingConn(ValueError("bad cursor state")))


def test_check_zero_symbols_handles_unsupported_schema() -> None:
    finding = _check_zero_symbols_per_file(_RaisingConn(sqlite3.OperationalError("no such column: file_role")))

    assert finding == {
        "name": "files_with_zero_symbols",
        "count": 0,
        "severity": "ok",
        "note": "unsupported",
    }


def test_check_zero_symbols_propagates_unexpected_errors() -> None:
    with pytest.raises(ValueError, match="bad cursor state"):
        _check_zero_symbols_per_file(_RaisingConn(ValueError("bad cursor state")))


def test_run_checks_reports_sqlite_failures(monkeypatch) -> None:
    def _failing_check(_conn) -> dict:
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(cmd_db_check, "CHECKS", (_failing_check,))

    assert _run_checks(object()) == [
        {
            "name": "failing_check",
            "count": 0,
            "severity": "error",
            "note": "check failed: DatabaseError: database disk image is malformed",
        }
    ]


def test_run_checks_propagates_non_sqlite_errors(monkeypatch) -> None:
    def _buggy_check(_conn) -> dict:
        raise ValueError("bad cursor state")

    monkeypatch.setattr(cmd_db_check, "CHECKS", (_buggy_check,))

    with pytest.raises(ValueError, match="bad cursor state"):
        _run_checks(object())
