"""Fixture BFS preserves its exact output while bounding SQLite query count."""

from __future__ import annotations

import random
import sqlite3
from contextlib import closing

import pytest

from roam.commands.cmd_pytest_fixtures import _fetch_chain
from roam.index.pytest_fixtures import _fixture_autouse, _fixture_scope


def make_graph(size, edges):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, name TEXT,
            qualified_name TEXT, line_start INTEGER, decorators TEXT, file_id INTEGER);
        CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, kind TEXT);
        CREATE INDEX edges_source ON edges(source_id);
        CREATE INDEX edges_target ON edges(target_id);
        INSERT INTO files VALUES (1, 'tests/conftest.py');
    """)
    # Repeated names test tie ordering; qualified names still distinguish rows.
    conn.executemany(
        "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, 1)",
        [
            (
                i,
                f"fixture_{i % 23:02d}",
                f"scope_{i}.fixture",
                i,
                '@pytest.fixture(scope="session", autouse=True)' if i % 3 == 0 else "@pytest.fixture",
            )
            for i in range(size)
        ],
    )
    conn.executemany("INSERT INTO edges(source_id, target_id, kind) VALUES (?, ?, ?)", edges)
    return conn


def reference_chain(conn, root, max_depth=6, reverse=False):
    """Frozen per-node query oracle, independent of production batching."""
    parent, child = ("target_id", "source_id") if reverse else ("source_id", "target_id")
    visited, frontier, out = {root}, [(root, 0)], []
    while frontier:
        next_frontier = []
        for sid, depth in frontier:
            if depth >= max_depth:
                continue
            rows = conn.execute(
                f"""
                SELECT s.id, s.name, s.qualified_name, s.line_start, s.decorators, f.path AS file_path
                FROM edges e JOIN symbols s ON e.{child} = s.id
                JOIN files f ON s.file_id = f.id
                WHERE e.{parent} = ? AND e.kind = 'pytest_fixture_dep' ORDER BY s.name
            """,
                (sid,),
            ).fetchall()
            for row in rows:
                if row["id"] in visited:
                    continue
                visited.add(row["id"])
                out.append(
                    {
                        "depth": depth + 1,
                        "id": row["id"],
                        "name": row["name"],
                        "qualified_name": row["qualified_name"],
                        "file_path": row["file_path"],
                        "line_start": row["line_start"],
                        "scope": _fixture_scope(row["decorators"]),
                        "autouse": _fixture_autouse(row["decorators"]),
                    }
                )
                next_frontier.append((row["id"], depth + 1))
        frontier = next_frontier
    return out


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("depth", [0, 1, 2, 6])
def test_exact_bfs_equivalence(reverse, depth):
    rng = random.Random(8912)
    edges = [(rng.randrange(90), rng.randrange(90), "pytest_fixture_dep") for _ in range(500)]
    edges += [(0, 1, "pytest_fixture_dep")] * 3  # duplicate, cycle, shared children
    edges += [(0, 89, "call"), (1, 0, "pytest_fixture_dep")]
    with closing(make_graph(90, edges)) as conn:
        assert _fetch_chain(conn, 0, depth, reverse) == reference_chain(conn, 0, depth, reverse)


@pytest.mark.parametrize("reverse", [False, True])
def test_wide_frontier_uses_bounded_batches(reverse):
    edges = [(0, i, "pytest_fixture_dep") for i in range(1, 1202)]
    if reverse:
        edges = [(b, a, kind) for a, b, kind in edges]
    with closing(make_graph(1202, edges)) as conn:
        expected = reference_chain(conn, 0, 2, reverse)

        class BoundedConnection:
            def __init__(self):
                self.calls = 0

            def execute(self, sql, params=()):
                assert len(params) <= 999
                self.calls += 1
                return conn.execute(sql, params)

        bounded = BoundedConnection()
        actual = _fetch_chain(bounded, 0, 2, reverse)
        assert actual == expected
        assert len(actual) == 1201
        assert bounded.calls <= 5  # root + ceil(1201 / 400), not 1202 queries


def test_missing_root_and_empty_graph():
    with closing(make_graph(1, [])) as conn:
        assert _fetch_chain(conn, 0) == []
        assert _fetch_chain(conn, 123) == []
