from __future__ import annotations

import sqlite3

import pytest


def test_gather_tour_data_degrades_on_sqlite_graph_error(monkeypatch):
    from roam.commands import cmd_tour, cmd_understand
    from roam.graph import builder

    entry_points = [{"file": "src/app.py", "symbols": ["main"]}]

    def boom(_conn):
        raise sqlite3.OperationalError("missing graph table")

    monkeypatch.setattr(builder, "build_symbol_graph", boom)
    monkeypatch.setattr(cmd_tour, "_entry_points", lambda _conn: entry_points)
    monkeypatch.setattr(cmd_tour, "_reading_order", lambda _conn, _graph: pytest.fail("graph fallback not used"))
    monkeypatch.setattr(
        cmd_tour, "_top_symbols", lambda _conn, _graph, limit=10: pytest.fail("graph fallback not used")
    )

    assert cmd_understand._gather_tour_data(object(), None) == {
        "reading_order": [],
        "entry_points": entry_points,
        "top_symbols": [],
    }


def test_gather_tour_data_does_not_swallow_non_sqlite_graph_error(monkeypatch):
    from roam.commands import cmd_understand
    from roam.graph import builder

    def boom(_conn):
        raise RuntimeError("unexpected graph failure")

    monkeypatch.setattr(builder, "build_symbol_graph", boom)

    with pytest.raises(RuntimeError, match="unexpected graph failure"):
        cmd_understand._gather_tour_data(object(), None)
