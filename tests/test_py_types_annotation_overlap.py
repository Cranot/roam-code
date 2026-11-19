"""Full annotation coverage must count the union of signature gaps."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from roam.commands import cmd_py_types


@pytest.mark.parametrize(
    "return_only_gap,param_only_gap,both_gaps,fully_typed",
    [(4, 4, 0, 2), (2, 2, 2, 4), (0, 0, 4, 6), (0, 0, 0, 10)],
    ids=["disjoint", "partial-overlap", "complete-overlap", "fully-typed"],
)
def test_full_annotation_coverage(tmp_path, monkeypatch, return_only_gap, param_only_gap, both_gaps, fully_typed):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE files (id INTEGER, path TEXT, language TEXT, file_role TEXT);"
        "CREATE TABLE symbols (file_id INTEGER, name TEXT, qualified_name TEXT,"
        " signature TEXT, line_start INTEGER, kind TEXT, visibility TEXT);"
        "INSERT INTO files VALUES (1, 'module.py', 'python', 'source');"
    )
    signatures = (
        ["def f(x: int)"] * return_only_gap
        + ["def f(x) -> int"] * param_only_gap
        + ["def f(x)"] * both_gaps
        + ["def f(x: int) -> int"] * fully_typed
    )
    for i, signature in enumerate(signatures, 1):
        conn.execute(
            "INSERT INTO symbols VALUES (1, ?, ?, ?, ?, 'function', 'public')",
            (f"f{i}", f"f{i}", signature.replace("f(", f"f{i}("), i),
        )

    @contextmanager
    def open_index(**kwargs):
        yield conn

    monkeypatch.setattr(cmd_py_types, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_py_types, "open_db", open_index)
    try:
        result = CliRunner().invoke(cmd_py_types.py_types, [], obj={"json": True})
        assert result.exit_code == 0, result.output
        summary = json.loads(result.output)["summary"]
        assert summary["missing_both"] == both_gaps
        assert summary["total_public"] == len(signatures)
        assert summary["no_return_annotation"] == return_only_gap + both_gaps
        assert summary["untyped_params"] == param_only_gap + both_gaps
        assert summary["coverage_pct"] == fully_typed * 100 // len(signatures)
    finally:
        conn.close()
