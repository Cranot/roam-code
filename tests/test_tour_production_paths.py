"""Tour test statistics must agree with the index's file classifications."""

import sqlite3

import pytest


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from roam.db.schema import SCHEMA_SQL

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA_SQL)
    try:
        yield db
    finally:
        db.close()


def test_tour_test_ratio_uses_file_classification(conn):
    from roam.commands.cmd_tour import _patterns

    conn.executemany(
        "INSERT INTO files (path, file_role) VALUES (?, ?)",
        [
            ("src/latest.py", "source"),
            ("contest/runner.py", "source"),
            ("src/inspector.py", "source"),
            ("src/app.py", "source"),
            ("tests/test_api.py", "test"),
            ("checks/behavior.py", "test"),
        ],
    )

    stats = _patterns(conn)

    assert stats["files"] == 6
    assert stats["test_files"] == 2
    assert stats["test_ratio"] == pytest.approx(33.3)


def test_tour_conventional_test_paths(conn):
    from roam.commands.cmd_tour import _patterns

    conn.executemany(
        "INSERT INTO files (path, file_role) VALUES (?, ?)",
        [("tests/test_api.py", "test"), ("spec/api_spec.rb", "test"), ("src/app.py", None)],
    )

    stats = _patterns(conn)

    assert stats["test_files"] == 2
    assert stats["test_ratio"] == pytest.approx(66.7)


def test_tour_empty_index_has_zero_test_ratio(conn):
    from roam.commands.cmd_tour import _patterns

    stats = _patterns(conn)

    assert stats["test_files"] == 0
    assert stats["test_ratio"] == 0
