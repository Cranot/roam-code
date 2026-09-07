"""Git quoting and record separators must not alter discovered source paths."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from roam.index.discovery import _git_ls_files
from tests.conftest import git_init, index_in_process


@pytest.mark.parametrize(
    "filename",
    [
        "plain.py",
        "space name.py",
        "δοκιμή.py",
        " leading.py",
        pytest.param(
            "line\nbreak.py", marks=pytest.mark.skipif(os.name == "nt", reason="Windows forbids newline names")
        ),
        pytest.param('quote"name.py', marks=pytest.mark.skipif(os.name == "nt", reason="Windows forbids quote names")),
        pytest.param(
            "carriage\rreturn.py", marks=pytest.mark.skipif(os.name == "nt", reason="Windows forbids CR names")
        ),
    ],
)
def test_git_discovery_and_index_keep_literal_filenames(tmp_path, filename):
    import sqlite3

    root = tmp_path / "repo"
    root.mkdir()
    (root / filename).write_text("def answer():\n    return 42\n", encoding="utf-8")
    git_init(root)
    subprocess.run(["git", "config", "core.quotePath", "true"], cwd=root, check=True)
    (root / "untracked.py").write_text("def second():\n    return 7\n", encoding="utf-8")
    assert set(_git_ls_files(root)) == {filename, "untracked.py"}
    output, code = index_in_process(root)
    assert code == 0, output
    conn = sqlite3.connect(root / ".roam/index.db")
    try:
        rows = conn.execute("SELECT f.path, s.name FROM symbols s JOIN files f ON f.id=s.file_id").fetchall()
    finally:
        conn.close()
    assert (filename, "answer") in rows
    assert ("untracked.py", "second") in rows


def test_git_record_pipe_preserves_carriage_returns(tmp_path, monkeypatch):
    """Test subprocess decoding even where the filesystem forbids these names."""
    run = subprocess.run
    records = b"carriage\rreturn.py\0line\nbreak.py\0 leading.py\0"

    def emit_git_records(command, **kwargs):
        assert command[:2] == ["git", "ls-files"]
        return run([sys.executable, "-c", f"import sys; sys.stdout.buffer.write({records!r})"], **kwargs)

    monkeypatch.setattr(subprocess, "run", emit_git_records)
    assert _git_ls_files(tmp_path) == ["carriage\rreturn.py", "line\nbreak.py", " leading.py"]
