from __future__ import annotations

import sqlite3

import pytest

from roam.commands.cmd_db_check import _check_missing_fts, _check_zero_symbols_per_file


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
