"""roam at — code at a file:line with enclosing symbol + callers (2026-06-02)."""

from __future__ import annotations

import click.testing as _ctest

from roam.commands.cmd_at import (
    _callers_of,
    _enclosing_symbol,
    _parse_location,
    _read_range,
    _read_slice,
    at,
)


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


def test_read_range_marks_each_target_line_and_discloses_truncation(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("".join(f"line-{index}\n" for index in range(1, 21)), encoding="utf-8")
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rendered, start, end, total, truncated = _read_range(
            "m.py",
            5,
            12,
            context=0,
            max_lines=4,
        )
    finally:
        os.chdir(cwd)
    assert start == 5
    assert end == 8
    assert total == 20
    assert truncated is True
    assert rendered.count(">>") == 4
    assert "line-5" in rendered and "line-8" in rendered
    assert "line-9" not in rendered


def test_parse_location_accepts_point_range_and_windows_drive():
    assert _parse_location("src/m.py:42") == ("src/m.py", 42, 42)
    assert _parse_location("src/m.py:40-90") == ("src/m.py", 40, 90)
    assert _parse_location(r"C:\repo\src\m.py:7-9") == (
        r"C:\repo\src\m.py",
        7,
        9,
    )


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


def test_reversed_range_errors():
    r = _ctest.CliRunner().invoke(at, ["src/x.py:20-10"], obj={"json": True})
    assert r.exit_code == 2
    assert "bad_location" in r.output


def test_whole_symbol_rejects_explicit_range():
    r = _ctest.CliRunner().invoke(
        at,
        ["src/x.py:10-20", "--whole-symbol"],
        obj={"json": True},
    )
    assert r.exit_code == 2
    assert "whole-symbol" in r.output
