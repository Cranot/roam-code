"""Tests for extended symbol_match requirement checks."""

from __future__ import annotations

import sqlite3

from roam.rules.engine import evaluate_rule


def _make_db(tmp_path):
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            file_role TEXT DEFAULT 'source',
            line_count INTEGER,
            loc INTEGER
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            line_start INTEGER,
            line_end INTEGER,
            is_exported INTEGER DEFAULT 1,
            parent_id INTEGER
        );
        CREATE TABLE graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            in_degree INTEGER DEFAULT 0
        );
        CREATE TABLE symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT DEFAULT 'calls'
        );
        """
    )
    conn.commit()
    return conn


def test_symbol_match_require_style_thresholds(tmp_path):
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO files (id, path, file_role, line_count, loc) VALUES (1, 'src/app.py', 'source', 920, 920)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, is_exported) "
        "VALUES (1, 1, 'BadName', 'function', 10, 60, 1)"
    )
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, in_degree) VALUES (1, 4)"
    )
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, param_count, line_count) VALUES (1, 5, 51)"
    )
    conn.commit()

    rule = {
        "name": "style thresholds",
        "severity": "warning",
        "type": "symbol_match",
        "match": {
            "kind": ["function"],
            "file_glob": "src/**",
            "require": {
                "name_regex": "^[a-z_][a-z0-9_]*$",
                "max_params": 3,
                "max_symbol_lines": 40,
            },
        },
    }

    result = evaluate_rule(rule, conn)
    assert result["passed"] is False
    assert len(result["violations"]) == 1

    reason = result["violations"][0]["reason"]
    assert "does not match" in reason
    assert "parameter count" in reason
    assert "symbol line count" in reason


def test_symbol_match_require_max_file_lines(tmp_path):
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO files (id, path, file_role, line_count, loc) VALUES (1, 'src/huge.py', 'source', 1200, 1200)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, is_exported) "
        "VALUES (1, 1, 'helper', 'function', 1, 10, 1)"
    )
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, in_degree) VALUES (1, 1)"
    )
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, param_count, line_count) VALUES (1, 1, 10)"
    )
    conn.commit()

    rule = {
        "name": "file size threshold",
        "severity": "warning",
        "type": "symbol_match",
        "match": {
            "kind": ["function"],
            "file_glob": "src/**",
            "require": {
                "max_file_lines": 500,
            },
        },
    }

    result = evaluate_rule(rule, conn)
    assert result["passed"] is False
    assert len(result["violations"]) == 1
    assert "file line count" in result["violations"][0]["reason"]


def test_symbol_match_invalid_name_regex_fails_rule(tmp_path):
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO files (id, path, file_role, line_count, loc) VALUES (1, 'src/a.py', 'source', 10, 10)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, is_exported) "
        "VALUES (1, 1, 'ok_name', 'function', 1, 2, 1)"
    )
    conn.commit()

    rule = {
        "name": "bad regex",
        "severity": "error",
        "type": "symbol_match",
        "match": {
            "kind": ["function"],
            "require": {
                "name_regex": "[unterminated",
            },
        },
    }

    result = evaluate_rule(rule, conn)
    assert result["passed"] is False
    assert "invalid require.name_regex" in result["violations"][0]["reason"]
