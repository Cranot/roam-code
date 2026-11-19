"""Bumpy-road rankings must apply the same role filters as complexity."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from roam.commands import cmd_complexity


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT);
        CREATE TABLE symbol_metrics (symbol_id INTEGER, cognitive_complexity REAL, nesting_depth INTEGER);
    """)

    def add_file(path, specs):
        file_id = conn.execute("INSERT INTO files(path) VALUES (?)", (path,)).lastrowid
        for name, kind, score in specs:
            symbol_id = conn.execute(
                "INSERT INTO symbols(file_id, name, kind) VALUES (?, ?, ?)",
                (file_id, name, kind),
            ).lastrowid
            conn.execute("INSERT INTO symbol_metrics VALUES (?, ?, ?)", (symbol_id, score, 2))

    regular = [("alpha", "function", 6), ("beta", "function", 7), ("gamma", "function", 8)]
    add_file("src/core.py", regular)
    for path in (".github/ci.py", "examples/demo.py", "vendor/lib.py", "generated/client.py"):
        add_file(path, [(name, kind, score + 40) for name, kind, score in regular])
    add_file("src/lifecycle.py", regular[:2] + [("render", "method", 40)])
    add_file("src/imports.py", regular[:2] + [("bridge", "import", 40)])
    add_file("src/interop.py", regular[:2] + [("importBridge", "function", 40)])
    add_file("src/module_imports.py", regular[:2] + [("bridge", "module_import", 40)])
    # Still qualifies after either symbol filter, but its aggregates must change.
    add_file("src/mixed.py", regular + [("render", "method", 40), ("bridge", "import", 30)])

    @contextmanager
    def open_db(**kwargs):
        yield conn

    monkeypatch.setattr(cmd_complexity, "open_db", open_db)
    monkeypatch.setattr(cmd_complexity, "ensure_index", lambda: None)

    def run(*args):
        result = CliRunner().invoke(cmd_complexity.complexity, ["--bumpy-road", *args], obj={"json": True})
        assert result.exit_code == 0, (result.output, result.exception)
        return json.loads(result.output)

    yield run
    conn.close()


TOOLING = {".github/ci.py", "examples/demo.py", "vendor/lib.py", "generated/client.py"}
SOURCE = {
    "src/core.py",
    "src/lifecycle.py",
    "src/imports.py",
    "src/interop.py",
    "src/module_imports.py",
    "src/mixed.py",
}


def test_bumpy_road_excludes_tooling_before_display_limit(invoke):
    result = invoke("--top", "1")
    assert [row["file"] for row in result["files"]] == ["src/mixed.py"]
    assert {row["file"] for row in invoke()["files"]} == SOURCE


def test_bumpy_road_include_tooling_opt_in(invoke):
    assert {row["file"] for row in invoke("--include-tooling")["files"]} == SOURCE | TOOLING


@pytest.mark.parametrize(
    "flags, removed, count, total",
    [
        (["--no-framework"], {"src/lifecycle.py"}, 4, 51),
        (["--no-imports"], {"src/imports.py", "src/interop.py", "src/module_imports.py"}, 4, 61),
        (["--no-framework", "--no-imports"], SOURCE - {"src/core.py", "src/mixed.py"}, 3, 21),
    ],
)
def test_bumpy_road_filters_symbols_before_qualification_and_aggregation(invoke, flags, removed, count, total):
    # Opt in to tooling to isolate symbol filtering from the default path filter.
    rows = {row["file"]: row for row in invoke("--include-tooling", *flags)["files"]}
    assert set(rows) == (SOURCE - removed) | TOOLING
    assert rows["src/mixed.py"]["complex_functions"] == count
    assert rows["src/mixed.py"]["total_complexity"] == total
    assert rows["src/mixed.py"]["avg_complexity"] == round(total / count, 1)
    assert rows["src/mixed.py"]["bumpy_score"] == total
