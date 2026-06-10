"""roam at — code at a file:line with enclosing symbol + callers (2026-06-02)."""

from __future__ import annotations

import click.testing as _ctest

from roam.commands.cmd_at import _callers_of, _enclosing_symbol, _read_slice, at


def test_read_slice_marks_target(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rendered, start, total = _read_slice("m.py", 3, 1)
    finally:
        os.chdir(cwd)
    assert ">>" in rendered
    assert "    3  c" in rendered.replace(">>", "  ")
    assert start == 2 and total == 5


def test_enclosing_symbol_and_callers():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols(id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            qualified_name TEXT, kind TEXT, signature TEXT,
            line_start INTEGER, line_end INTEGER);
        CREATE TABLE edges(source_id INTEGER, target_id INTEGER, line INTEGER);
        INSERT INTO files VALUES (1,'src/m.py'),(2,'src/caller.py');
        INSERT INTO symbols VALUES
            (10,1,'outer','m.outer','function','def outer()',1,50),
            (11,1,'inner','m.inner','function','def inner()',10,20),
            (12,2,'c1','caller.c1','function','def c1()',1,5);
        INSERT INTO edges VALUES (12,11,3);
        """
    )
    # line 15 is inside inner (10-20), the smallest enclosing span
    enc = _enclosing_symbol(conn, "src/m.py", 15)
    assert enc["name"] == "inner"
    callers = _callers_of(conn, enc["id"])
    assert callers == ["src/caller.py:3"]


def test_bad_location_errors():
    r = _ctest.CliRunner().invoke(at, ["src/x.py"], obj={"json": True})
    assert r.exit_code == 2
    assert "bad_location" in r.output


def test_bad_line_number():
    r = _ctest.CliRunner().invoke(at, ["src/x.py:abc"], obj={"json": True})
    assert r.exit_code == 2
