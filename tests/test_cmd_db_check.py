from __future__ import annotations

import sqlite3

import pytest

from roam.commands import cmd_db_check
from roam.commands.cmd_db_check import (
    SEVERITY_UNSUPPORTED,
    _check_corrupt_metrics,
    _check_missing_fts,
    _check_zero_symbols_per_file,
    _run_checks,
)


class _RaisingConn:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def execute(self, _sql: str):
        raise self.exc


def test_check_missing_fts_handles_missing_fts_table() -> None:
    """An absent FTS5 table is UNSUPPORTED, not a measured zero.

    W1332: this used to return ``count=0, severity="ok"`` — identical to a
    database where every symbol has an FTS row. ``count`` is now None so no
    consumer can read a measurement out of a check that never ran.
    """
    finding = _check_missing_fts(_RaisingConn(sqlite3.OperationalError("no such table: symbol_fts")))

    assert finding["name"] == "missing_fts_rows"
    assert finding["count"] is None
    assert finding["severity"] == SEVERITY_UNSUPPORTED
    assert "fts5 not available" in finding["note"]


def test_check_missing_fts_propagates_operational_errors_that_are_not_capability_gaps() -> None:
    """A corrupt DB is not "FTS is optional here" — it must reach _run_checks."""
    with pytest.raises(sqlite3.OperationalError, match="database disk image is malformed"):
        _check_missing_fts(_RaisingConn(sqlite3.OperationalError("database disk image is malformed")))


def test_check_missing_fts_propagates_unexpected_errors() -> None:
    with pytest.raises(ValueError, match="bad cursor state"):
        _check_missing_fts(_RaisingConn(ValueError("bad cursor state")))


def test_check_corrupt_metrics_propagates_operational_errors() -> None:
    """``symbol_metrics`` ships in the schema; an unreadable one is a fault.

    W1332: the swallowed OperationalError reported ``count=0, severity="ok"``
    for a table that could not be read at all.
    """
    with pytest.raises(sqlite3.OperationalError, match="no such table: symbol_metrics"):
        _check_corrupt_metrics(_RaisingConn(sqlite3.OperationalError("no such table: symbol_metrics")))


def test_check_zero_symbols_propagates_operational_errors() -> None:
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        _check_zero_symbols_per_file(_RaisingConn(sqlite3.OperationalError("no such column: file_role")))


def test_check_zero_symbols_propagates_unexpected_errors() -> None:
    with pytest.raises(ValueError, match="bad cursor state"):
        _check_zero_symbols_per_file(_RaisingConn(ValueError("bad cursor state")))


def test_run_checks_reports_sqlite_failures(monkeypatch) -> None:
    def _check_failing(_conn) -> dict:
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(cmd_db_check, "CHECKS", (_check_failing,))

    assert _run_checks(object()) == [
        {
            "name": "failing",
            "count": None,
            "severity": "error",
            "note": "check failed: DatabaseError: database disk image is malformed",
        }
    ]


def test_run_checks_names_the_failed_check_without_mangling(monkeypatch) -> None:
    """``str.lstrip`` strips a CHARACTER SET: ``_check_corrupt_metrics`` came
    out as ``orrupt_metrics``, so the disclosure named a check that does not
    exist."""

    def _check_corrupt_metrics(_conn) -> dict:
        raise sqlite3.DatabaseError("boom")

    monkeypatch.setattr(cmd_db_check, "CHECKS", (_check_corrupt_metrics,))

    assert _run_checks(object())[0]["name"] == "corrupt_metrics"


def test_run_checks_propagates_non_sqlite_errors(monkeypatch) -> None:
    def _buggy_check(_conn) -> dict:
        raise ValueError("bad cursor state")

    monkeypatch.setattr(cmd_db_check, "CHECKS", (_buggy_check,))

    with pytest.raises(ValueError, match="bad cursor state"):
        _run_checks(object())
