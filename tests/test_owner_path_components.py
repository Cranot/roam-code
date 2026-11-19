"""Ownership path selection must respect complete path components."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from roam.commands import cmd_owner


@pytest.fixture
def invoke_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE git_commits
            (id INTEGER PRIMARY KEY, author TEXT, timestamp INTEGER);
        CREATE TABLE git_file_changes
            (commit_id INTEGER, file_id INTEGER,
             lines_added INTEGER, lines_removed INTEGER);
    """)

    @contextmanager
    def open_db(**kwargs):
        yield conn

    monkeypatch.setattr(cmd_owner, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_owner, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cmd_owner, "open_db", open_db)
    authors = {}
    monkeypatch.setattr(
        cmd_owner,
        "get_blame_for_file",
        lambda root, path: [{"author": authors[path], "timestamp": 1700000000}],
    )

    def invoke(path, files):
        for fid, (file_path, author) in enumerate(files, 1):
            authors[file_path] = author
            conn.execute("INSERT INTO files VALUES (?, ?)", (fid, file_path))
            conn.execute("INSERT INTO git_commits VALUES (?, ?, ?)", (fid, author, 1700000000))
            conn.execute("INSERT INTO git_file_changes VALUES (?, ?, 1, 0)", (fid, fid))
        result = CliRunner().invoke(cmd_owner.owner, [path], obj={"json": True})
        assert result.exit_code == 0, (result.output, result.exception)
        return json.loads(result.output)

    yield invoke
    conn.close()


@pytest.mark.parametrize("path", ["src/Order", "src/Order/"])
def test_directory_ownership_excludes_prefix_siblings(invoke_owner, path):
    payload = invoke_owner(
        path,
        [
            ("src/Order/model.php", "Inside"),
            ("src/Order/nested/service.php", "Inside"),
            ("src/OrderItem.php", "SiblingFile"),
            ("src/Orders/model.php", "SiblingDirectory"),
        ],
    )
    assert payload["file_count"] == 2
    assert {author["name"] for author in payload["authors"]} == {"Inside"}


@pytest.mark.parametrize("path", ["src/Order.php", "Order.php"])
def test_exact_file_and_basename_keep_separate_ownership(invoke_owner, path):
    payload = invoke_owner(
        path,
        [
            ("src/Order.php", "Exact"),
            ("src/Order.php.bak", "Backup"),
        ],
    )
    assert payload["type"] == "file"
    assert payload["path"] == "src/Order.php"
    assert {author["name"] for author in payload["authors"]} == {"Exact"}


def test_suffix_lookup_does_not_match_partial_basename(invoke_owner):
    payload = invoke_owner("Order.php", [("src/PreOrder.php", "Unrelated")])
    assert payload.get("file_count", 1) == 0
    assert payload.get("authors", []) == []


@pytest.mark.parametrize("path", ["src/order", "SRC/Order/", "order/model.php", "MODEL.PHP"])
def test_lookup_retains_case_insensitive_components(invoke_owner, path):
    payload = invoke_owner(path, [("src/Order/model.php", "Inside"), ("src/Orders/model.php.bak", "Outside")])
    assert {a["name"] for a in payload["authors"]} == {"Inside"}


@pytest.mark.parametrize("path", ["src/order.php", "order.php", "src/order"])
def test_exact_case_wins(invoke_owner, path):
    suffix = "/model.php" if path == "src/order" else ""
    payload = invoke_owner(
        path,
        [
            ("src/Order.php" if not suffix else "src/Order" + suffix, "Upper"),
            ("src/order.php" if not suffix else "src/order" + suffix, "Exact"),
        ],
    )
    assert {a["name"] for a in payload["authors"]} == {"Exact"}
