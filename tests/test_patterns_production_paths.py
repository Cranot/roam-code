"""Production directory suffixes must not hide architectural patterns."""

import sqlite3

import pytest


@pytest.fixture
def factory_db(tmp_path, monkeypatch):
    # Keep the test independent of repository fixtures and user configuration.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            qualified_name TEXT, kind TEXT, line_start INTEGER
        );
        CREATE TABLE edges (source_id INTEGER, target_id INTEGER);
        INSERT INTO files VALUES (1, 'src/service.py');
        INSERT INTO symbols VALUES
            (1, 1, 'create_widget', 'create_widget', 'function', 1),
            (2, 1, 'Widget', 'Widget', 'class', 5);
        INSERT INTO edges VALUES (1, 2);
        """
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("separator", ["/", "\\"])
@pytest.mark.parametrize(
    "path",
    [
        "src/roam/attest/service.py",
        "src/latest/service.py",
        "src/contest/service.py",
        "src/inspec/service.py",
        "src/production/service.py",
    ],
)
def test_production_factory_survives_directory_suffix(factory_db, path, separator):
    from roam.commands.cmd_patterns import _detect_factory

    factory_db.execute("UPDATE files SET path = ?", (path.replace("/", separator),))
    detected = _detect_factory(factory_db)
    assert {item["name"] for item in detected} == {"create_widget"}


@pytest.mark.parametrize("separator", ["/", "\\"])
@pytest.mark.parametrize(
    "path",
    [
        "tests/service.py",
        "src/test/service.py",
        "src/__tests__/service.py",
        "src/spec/service.py",
        "src/test_service.py",
        "src/service_test.py",
        "src/roam/commands/cmd_patterns.py",
    ],
)
def test_test_code_and_detector_factories_stay_excluded(factory_db, path, separator):
    from roam.commands.cmd_patterns import _detect_factory

    factory_db.execute("UPDATE files SET path = ?", (path.replace("/", separator),))
    assert _detect_factory(factory_db) == []
