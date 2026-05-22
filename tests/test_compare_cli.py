"""Tests for roam compare (structural index diff)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_compare import _compute_delta, _verdict, compare_cmd


def _make_index(
    path: Path,
    files: list[tuple[int, str]],
    symbols: list[tuple[str, str, int]],
    *,
    complexity: int = 5,
    with_metrics: bool = True,
) -> None:
    """Build a tiny index DB with files + symbols (+ optional symbol_metrics).

    ``symbols`` rows are ``(qualified_name, kind, file_id)``; each symbol
    gets a distinct ``line_start`` (10, 20, 30, ...) so same-qname symbols
    stay distinguishable. Set ``with_metrics=False`` to simulate an index
    that genuinely predates the symbol_metrics schema.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, qualified_name TEXT, kind TEXT,
            file_id INTEGER, line_start INTEGER
        );
    """)
    if with_metrics:
        conn.execute("CREATE TABLE symbol_metrics (symbol_id INTEGER PRIMARY KEY, cognitive_complexity REAL)")
    for fid, fpath in files:
        conn.execute("INSERT INTO files (id, path) VALUES (?, ?)", (fid, fpath))
    for i, (qname, kind, file_id) in enumerate(symbols, start=1):
        conn.execute(
            "INSERT INTO symbols (id, qualified_name, kind, file_id, line_start) VALUES (?, ?, ?, ?, ?)",
            (i, qname, kind, file_id, i * 10),
        )
        if with_metrics:
            conn.execute(
                "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) VALUES (?, ?)",
                (i, complexity),  # default complexity per symbol
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


# --- A.2: symbol_count must not undercount same-qname symbols -----------


def test_symbol_count_does_not_drop_same_qname_collisions(tmp_path) -> None:
    """5 symbols sharing one qualified_name must all be counted (A.2).

    Keying _load_index_state's dict on qualified_name silently
    overwrote same-qname rows (overloaded methods) -- 33395 rows
    surfaced as 27771. Keying on the row id keeps every symbol.
    """
    from roam.commands.cmd_compare import _load_index_state

    db = tmp_path / "collide.db"
    _make_index(
        db,
        files=[(1, "a.py")],
        symbols=[("Cls.run", "method", 1)] * 5,  # 5 rows, identical qname
    )
    state = _load_index_state(db)
    assert state["symbol_count"] == 5, "same-qname symbols must not collapse"
    assert len(state["symbols"]) == 5


def test_same_qname_symbols_keyed_by_row_id(tmp_path) -> None:
    """The symbols dict is keyed on the unique row id, not qname."""
    from roam.commands.cmd_compare import _load_index_state

    db = tmp_path / "collide.db"
    _make_index(db, files=[(1, "a.py")], symbols=[("dup", "function", 1)] * 3)
    state = _load_index_state(db)
    assert set(state["symbols"].keys()) == {1, 2, 3}


# --- A.1: complexity delta uses symbol_metrics, not math_signals --------


def test_complexity_delta_uses_symbol_metrics(tmp_path) -> None:
    """A real complexity delta is produced from the symbol_metrics table.

    Pre-fix the query joined math_signals (no cognitive_complexity
    column there); the OperationalError was swallowed and compare
    falsely claimed the index predated the schema.
    """
    from roam.commands.cmd_compare import _load_index_state

    base_db = tmp_path / "base.db"
    targ_db = tmp_path / "targ.db"
    _make_index(base_db, files=[(1, "a.py")], symbols=[("foo", "function", 1)], complexity=3)
    _make_index(targ_db, files=[(1, "a.py")], symbols=[("foo", "function", 1)], complexity=20)

    base = _load_index_state(base_db)
    targ = _load_index_state(targ_db)
    assert base["complexity_data_available"] is True
    assert targ["complexity_data_available"] is True
    assert base["complexities"]["a.py"] == 3
    assert targ["complexities"]["a.py"] == 20

    delta = _compute_delta(base, targ, threshold=5)
    assert delta["complexity_data_available"] is True
    assert len(delta["complexity_up"]) == 1
    assert delta["complexity_up"][0]["delta"] == 17


def test_compare_self_yields_no_change(tmp_path) -> None:
    """Comparing an index against itself yields NO CHANGE / zero delta."""
    import json as jsonlib

    db = tmp_path / "idx.db"
    _make_index(
        db,
        files=[(1, "a.py"), (2, "b.py")],
        symbols=[("foo", "function", 1), ("bar", "method", 2), ("foo", "function", 2)],
        complexity=7,
    )
    runner = CliRunner()
    result = runner.invoke(compare_cmd, [str(db), str(db)], obj={"json": True})
    assert result.exit_code == 0
    data = jsonlib.loads(result.output)
    assert data["summary"]["verdict"] == "NO CHANGE"
    assert data["summary"]["symbols_added"] == 0
    assert data["summary"]["symbols_removed"] == 0
    assert data["summary"]["symbols_moved"] == 0
    assert data["summary"]["complexity_regressions"] == 0
    assert data["summary"]["complexity_improvements"] == 0
    # verdict must NOT claim the index predates the schema
    assert "predates" not in data["summary"]["verdict"]


def test_genuinely_old_index_still_discloses_missing_complexity(tmp_path) -> None:
    """An index with no symbol_metrics table keeps the honest disclosure.

    The loud-fallback rule: the 'predates the schema' path stays valid
    ONLY for an index that ACTUALLY lacks the table -- not for a code
    bug that the old bare except used to mask.
    """
    from roam.commands.cmd_compare import _load_index_state

    db = tmp_path / "old.db"
    _make_index(
        db,
        files=[(1, "a.py")],
        symbols=[("foo", "function", 1)],
        with_metrics=False,
    )
    state = _load_index_state(db)
    assert state["complexity_data_available"] is False
    assert state["complexities"] == {}
