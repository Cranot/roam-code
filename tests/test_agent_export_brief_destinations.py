"""The brief export must omit document details in every output destination."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from roam.commands import cmd_agent_export as export
from roam.db.schema import SCHEMA_SQL


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    root = tmp_path / "brief-example"
    root.mkdir()
    monkeypatch.chdir(root)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO files(id, path, language) VALUES (1, ?, 'python')",
        ("src/detailed_implementation_only.py",),
    )
    conn.execute("INSERT INTO symbols(file_id, name, kind) VALUES (1, 'example', 'function')")
    conn.commit()

    @contextmanager
    def database(**kwargs):
        yield conn

    # Bypass index discovery only; gathering, rendering and writing remain real.
    monkeypatch.setattr(export, "ensure_index", lambda: None)
    monkeypatch.setattr(export, "find_project_root", lambda: root)
    monkeypatch.setattr(export, "open_db", database)
    yield root
    conn.close()


@pytest.mark.parametrize("destination", ["stdout", "write", "output", "bundle"])
def test_brief_text_omits_full_document_details(project, destination):
    runner = CliRunner()
    options = {
        "stdout": [],
        "write": ["--write"],
        "output": ["--output", str(project / "custom.md")],
        "bundle": ["--profile", "codex", "--bundle", "--write"],
    }[destination]
    paths = {
        "stdout": [],
        "write": [project / "CLAUDE.md"],
        "output": [project / "custom.md"],
        "bundle": [project / "AGENTS.md", project / "CODEX.md"],
    }[destination]

    full = runner.invoke(export.agent_export_cmd, options, obj={})
    assert full.exit_code == 0, full.output
    full_docs = [p.read_text() for p in paths] if paths else [full.output]
    for doc in full_docs:
        assert "src/detailed_implementation_only.py" in doc
    # Exercise a fresh write, independent of preservation of existing user text.
    for path in paths:
        path.unlink()

    brief = runner.invoke(export.agent_export_cmd, [*options, "--brief"], obj={})
    assert brief.exit_code == 0, brief.output
    brief_docs = [p.read_text() for p in paths] if paths else [brief.output]
    for compact, complete in zip(brief_docs, full_docs):
        assert compact.strip()
        assert "src/detailed_implementation_only.py" not in compact
        assert len(compact) < len(complete)


def test_json_brief_still_omits_detailed_payload(project):
    runner = CliRunner()
    full = runner.invoke(export.agent_export_cmd, [], obj={"json": True})
    brief = runner.invoke(export.agent_export_cmd, ["--brief"], obj={"json": True})
    assert full.exit_code == brief.exit_code == 0
    full_data, brief_data = json.loads(full.output), json.loads(brief.output)
    assert full_data["key_files"]
    assert "key_files" not in brief_data
    assert brief_data["project_name"] == project.name
    assert brief_data["summary"]["files"] == 1


@pytest.mark.parametrize(
    "options,names",
    [
        (["--write"], ["CLAUDE.md"]),
        (["--write", "--profile", "codex", "--bundle"], ["AGENTS.md", "CODEX.md"]),
    ],
)
def test_repeated_brief_write_preserves_manual_sections(project, options, names):
    runner = CliRunner()
    assert runner.invoke(export.agent_export_cmd, options, obj={}).exit_code == 0
    for name in names:
        path = project / name
        path.write_text("Manual prefix\n" + path.read_text() + "\nManual suffix\n")
    for _ in range(2):
        result = runner.invoke(export.agent_export_cmd, [*options, "--brief"], obj={})
        assert result.exit_code == 0, result.output
        for name in names:
            text = (project / name).read_text()
            assert text.startswith("Manual prefix\n")
            assert text.rstrip("\n").endswith("\nManual suffix")
            assert "src/detailed_implementation_only.py" not in text
