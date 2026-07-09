"""Regression tests for the MCP oracle batch tool."""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import patch

from roam.commands.cmd_oracle import OracleResult


@contextmanager
def _stub_open_db():
    yield object()


def test_oracle_batch_keeps_running_after_one_item_raises() -> None:
    from roam.mcp_server import oracle_batch

    calls: list[str] = []

    def _oracle(conn, name: str) -> OracleResult:
        calls.append(name)
        if name == "bad":
            raise RuntimeError("boom")
        return OracleResult(True, f"matched {name}", "definitive_yes", "high")

    batch = inspect.unwrap(oracle_batch)

    with (
        patch("roam.commands.resolve.ensure_index"),
        patch("roam.db.connection.open_db", side_effect=lambda readonly=True, project_root=None: _stub_open_db()),
        patch("roam.commands.cmd_oracle.oracle_symbol_exists", side_effect=_oracle),
    ):
        result = batch(
            items=[
                {"oracle": "symbol-exists", "name": "good"},
                {"oracle": "symbol-exists", "name": "bad"},
                {"oracle": "symbol-exists", "name": "after"},
            ],
            root=".",
        )

    assert calls == ["good", "bad", "after"]
    assert result["summary"]["count"] == 3
    assert len(result["results"]) == 3
    error_rows = [row for row in result["results"] if row.get("status") == "error"]
    assert len(error_rows) == 1
    assert "boom" in error_rows[0]["error"]
    assert error_rows[0]["input"]["name"] == "bad"


def test_oracle_batch_all_ok_shape_stays_clean() -> None:
    from roam.mcp_server import oracle_batch

    def _oracle(conn, name: str) -> OracleResult:
        return OracleResult(True, f"matched {name}", "definitive_yes", "high")

    batch = inspect.unwrap(oracle_batch)

    with (
        patch("roam.commands.resolve.ensure_index"),
        patch("roam.db.connection.open_db", side_effect=lambda readonly=True, project_root=None: _stub_open_db()),
        patch("roam.commands.cmd_oracle.oracle_symbol_exists", side_effect=_oracle),
    ):
        result = batch(
            items=[
                {"oracle": "symbol-exists", "name": "good"},
                {"oracle": "symbol-exists", "name": "still-good"},
            ],
            root=".",
        )

    assert result["summary"]["count"] == 2
    assert all("status" not in row for row in result["results"])
    assert all("error" not in row for row in result["results"])
