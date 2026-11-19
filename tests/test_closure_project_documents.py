"""Rename closures must scan indexed docs relative to the project root."""

import sqlite3

import pytest

from roam.commands.cmd_closure import _collect_closure
from roam.db.schema import SCHEMA_SQL


@pytest.mark.parametrize("working_directory", [".", "src/nested"])
def test_rename_includes_project_doc_references(tmp_path, monkeypatch, working_directory):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / "config"))
    root = tmp_path / "project"
    nested = root / "src" / "nested"
    nested.mkdir(parents=True)
    # A valid root marker, without invoking git or consulting user configuration.
    git_dir = root / ".git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs").mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "src" / "api.py").write_text("def old_api(): pass\n", encoding="utf-8")
    (root / "README.md").write_text("Call old_api to begin.\n", encoding="utf-8")
    (root / "settings.yaml").write_text("entrypoint: old_api\n", encoding="utf-8")
    (root / "unrelated.md").write_text("No API references here.\n", encoding="utf-8")
    # Same relative paths under the nested cwd must not override project files.
    (nested / "README.md").write_text("No API references here.\n", encoding="utf-8")
    (nested / "unrelated.md").write_text("old_api\n", encoding="utf-8")

    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.executemany(
            "INSERT INTO files (id, path, language) VALUES (?, ?, ?)",
            [
                (1, "src/api.py", "python"),
                (2, "README.md", "markdown"),
                (3, "settings.yaml", "yaml"),
                (4, "unrelated.md", "markdown"),
            ],
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start) VALUES (1, 1, 'old_api', 'function', 1)"
        )
        sym = conn.execute(
            "SELECT s.*, f.path AS file_path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = 1"
        ).fetchone()
        monkeypatch.chdir(root / working_directory)
        changes = _collect_closure(conn, sym, rename="new_api")

    doc_paths = {c["file"] for c in changes if c["change_type"] == "update_doc"}
    assert doc_paths == {"README.md", "settings.yaml"}
    assert any(c["change_type"] == "update_definition" and c["file"] == "src/api.py" for c in changes)
