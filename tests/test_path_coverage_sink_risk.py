"""DB-writing sinks retain critical risk regardless of effect row order."""

import itertools
import sqlite3

import pytest

from roam.commands.cmd_path_coverage import _classify_risk, _find_sinks_from_effects


@pytest.mark.parametrize(
    "effects, expected_risk",
    [(order, "critical") for order in itertools.permutations(("writes_db", "network", "filesystem"))]
    + [(("writes_db",), "critical"), (("network", "filesystem"), "high")],
)
def test_sink_risk_preserves_database_writes(effects, expected_risk):
    # No indexes or project files are needed: exercise the real DISTINCT query
    # against an isolated database, with every insertion order of mixed effects.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY, name TEXT, kind TEXT,
                file_id INTEGER, line_start INTEGER
            );
            CREATE TABLE symbol_effects (
                symbol_id INTEGER, effect_type TEXT, source TEXT
            );
            INSERT INTO files VALUES (1, 'service.py');
            INSERT INTO symbols VALUES (2, 'save_and_notify', 'function', 1, 10);
        """)
        conn.executemany(
            "INSERT INTO symbol_effects VALUES (2, ?, 'direct')",
            [(effect,) for effect in effects],
        )
        sinks, sink_effects = _find_sinks_from_effects(conn, None)
        assert 2 in sinks
        assert _classify_risk([1, 2], set(), sink_effects) == expected_risk
    finally:
        conn.close()
