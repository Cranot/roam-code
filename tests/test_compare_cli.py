"""Tests for R8 — roam compare (structural index diff)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_compare import _compute_delta, _verdict, compare_cmd


def _make_index(path: Path, files: list[tuple[int, str]], symbols: list[tuple[str, str, int]]) -> None:
    """Build a tiny index DB with files + symbols (+ optional math_signals)."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, qualified_name TEXT, kind TEXT, file_id INTEGER);
        CREATE TABLE math_signals (symbol_id INTEGER, cognitive_complexity INTEGER);
    """)
    for fid, fpath in files:
        conn.execute("INSERT INTO files (id, path) VALUES (?, ?)", (fid, fpath))
    for i, (qname, kind, file_id) in enumerate(symbols, start=1):
        conn.execute(
            "INSERT INTO symbols (id, qualified_name, kind, file_id) VALUES (?, ?, ?, ?)",
            (i, qname, kind, file_id),
        )
        conn.execute(
            "INSERT INTO math_signals (symbol_id, cognitive_complexity) VALUES (?, ?)",
            (i, 5),  # default complexity per symbol
        )
    conn.commit()
    conn.close()


def test_compute_delta_detects_added_and_removed(tmp_path) -> None:
    base_db = tmp_path / "base.db"
    targ_db = tmp_path / "targ.db"
    _make_index(base_db, files=[(1, "a.py")], symbols=[("foo", "function", 1)])
    _make_index(targ_db, files=[(1, "a.py")], symbols=[("bar", "function", 1)])

    from roam.commands.cmd_compare import _load_index_state

    base = _load_index_state(base_db)
    targ = _load_index_state(targ_db)
    delta = _compute_delta(base, targ, threshold=1)

    assert any(s["qname"] == "bar" for s in delta["symbols_added"])
    assert any(s["qname"] == "foo" for s in delta["symbols_removed"])


def test_compute_delta_detects_moves(tmp_path) -> None:
    base_db = tmp_path / "base.db"
    targ_db = tmp_path / "targ.db"
    _make_index(base_db, files=[(1, "old.py"), (2, "new.py")], symbols=[("foo", "function", 1)])
    _make_index(targ_db, files=[(1, "old.py"), (2, "new.py")], symbols=[("foo", "function", 2)])

    from roam.commands.cmd_compare import _load_index_state

    base = _load_index_state(base_db)
    targ = _load_index_state(targ_db)
    delta = _compute_delta(base, targ, threshold=1)

    assert len(delta["symbols_moved"]) == 1
    move = delta["symbols_moved"][0]
    assert move["qname"] == "foo"
    assert move["old_path"] == "old.py"
    assert move["new_path"] == "new.py"


def test_verdict_no_change() -> None:
    delta = {"complexity_up": [], "complexity_down": [], "symbols_added": [], "symbols_removed": []}
    assert _verdict(delta) == "NO CHANGE"


def test_verdict_improved() -> None:
    delta = {
        "complexity_up": [],
        "complexity_down": [{}, {}, {}],
        "symbols_added": [{}],
        "symbols_removed": [{}, {}, {}],
    }
    assert _verdict(delta) == "IMPROVED"


def test_verdict_regressed() -> None:
    delta = {
        "complexity_up": [{}, {}, {}, {}],
        "complexity_down": [{}],
        "symbols_added": [{}, {}, {}, {}, {}],
        "symbols_removed": [{}],
    }
    assert _verdict(delta) == "REGRESSED"


def test_cli_text_output(tmp_path) -> None:
    base_db = tmp_path / "base.db"
    targ_db = tmp_path / "targ.db"
    _make_index(base_db, files=[(1, "a.py")], symbols=[("foo", "function", 1)])
    _make_index(targ_db, files=[(1, "a.py")], symbols=[("bar", "function", 1)])

    runner = CliRunner()
    result = runner.invoke(compare_cmd, [str(base_db), str(targ_db)], obj={})
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    assert "Symbols added" in result.output
    assert "Symbols removed" in result.output


def test_cli_json_output(tmp_path) -> None:
    import json as jsonlib

    base_db = tmp_path / "base.db"
    targ_db = tmp_path / "targ.db"
    _make_index(base_db, files=[(1, "a.py")], symbols=[("foo", "function", 1)])
    _make_index(targ_db, files=[(1, "a.py")], symbols=[("bar", "function", 1)])

    runner = CliRunner()
    result = runner.invoke(compare_cmd, [str(base_db), str(targ_db)], obj={"json": True})
    assert result.exit_code == 0
    data = jsonlib.loads(result.output)
    assert data["command"] == "compare"
    assert "symbols_added" in data
    assert data["summary"]["symbols_added"] == 1
    assert data["summary"]["symbols_removed"] == 1
