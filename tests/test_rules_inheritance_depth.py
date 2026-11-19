"""Inheritance depth belongs to descendants, with edges from child to base."""

import sqlite3

import pytest

from roam.rules.builtin import _check_no_deep_inheritance


@pytest.mark.parametrize("with_unrelated_cycle", [False, True])
@pytest.mark.parametrize("threshold, expected", [(4, {"E"}), (5, set())])
def test_inheritance_depth_counts_bases(with_unrelated_cycle, threshold, expected):
    # Base <- A <- B <- C <- D <- E: only E exceeds four base levels.
    # An unrelated cycle must not change the result for this valid hierarchy.
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript("""
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY, name TEXT, file_id INTEGER, line_start INTEGER
            );
            CREATE TABLE edges (source_id INTEGER, target_id INTEGER, kind TEXT);
            INSERT INTO files VALUES (1, 'hierarchy.py');
        """)
        names = ["Base", "A", "B", "C", "D", "E", "CycleA", "CycleB"]
        conn.executemany(
            "INSERT INTO symbols VALUES (?, ?, 1, ?)",
            [(i, name, i) for i, name in enumerate(names, start=1)],
        )
        conn.executemany(
            "INSERT INTO edges VALUES (?, ?, 'inherits')",
            [(child, child - 1) for child in range(2, 7)],
        )
        if with_unrelated_cycle:
            conn.executemany("INSERT INTO edges VALUES (?, ?, 'inherits')", [(7, 8), (8, 7)])

        violations = _check_no_deep_inheritance(conn, None, threshold)
        assert {v["symbol"] for v in violations} == expected
    finally:
        conn.close()
