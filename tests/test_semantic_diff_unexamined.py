"""semantic-diff must say when changed files were not examined.

It used to print ``0 structural changes in 0 files`` for a change that touched
files with no extractable symbols (a mode flip on a script, a config edit).
Real CLI, real git history.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _pin_index_freshness_budget(monkeypatch):
    monkeypatch.setenv("ROAM_INDEX_FRESHNESS_BUDGET_S", "3600")


sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process, invoke_cli

_REASONS = {"mode_only", "no_parser", "no_symbols"}


def _git(proj, *args):
    subprocess.run(["git", *args], cwd=proj, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    proj = tmp_path / "repo"
    (proj / "src").mkdir(parents=True)
    (proj / "scripts").mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "app.py").write_text("def greet(name):\n    return name\n")
    (proj / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n")
    (proj / "settings.cfg").write_text("level = 1\n")
    git_init(proj)
    return proj


def _run(proj, monkeypatch, *flags):
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, out
    return invoke_cli(CliRunner(), [*flags, "semantic-diff", "--base", "HEAD~1"], cwd=proj)


def _mode_flip_commit(proj):
    # The command diffs the WORKING TREE against the ref, so on POSIX
    # (core.fileMode=true) the file itself must be executable, not only the
    # index entry. Windows has no exec bit on disk: the index entry is enough.
    if os.name == "posix":
        os.chmod(proj / "scripts" / "run.sh", 0o755)
    _git(proj, "update-index", "--chmod=+x", "scripts/run.sh")
    _git(proj, "commit", "-m", "make executable")


def test_mode_only_change_is_not_reported_as_zero_files(repo, monkeypatch):
    _mode_flip_commit(repo)
    result = _run(repo, monkeypatch, "--json")
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["files_changed_total"] == 1
    assert summary["files_changed"] == 0
    assert "1 of 1 changed file not analyzable" in summary["verdict"]
    [entry] = json.loads(result.output)["files_not_analyzed"]
    assert entry["file"] == "scripts/run.sh"
    assert entry["reason"] == "mode_only"
    assert summary["partial_success"] is True


def test_mode_only_text_verdict_discloses(repo, monkeypatch):
    _mode_flip_commit(repo)
    result = _run(repo, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "VERDICT: 0 structural changes in 0 files; 1 of 1 changed file not analyzable" in result.output
    assert "scripts/run.sh (mode_only)" in result.output


def test_content_edit_to_unparsed_files_is_disclosed(repo, monkeypatch):
    (repo / "scripts" / "run.sh").write_text("#!/bin/sh\necho changed\n")
    (repo / "settings.cfg").write_text("level = 2\n")
    git_commit(repo, "edit non-code files")
    data = json.loads(_run(repo, monkeypatch, "--json").output)
    assert data["summary"]["files_changed_total"] == 2
    assert {e["file"] for e in data["files_not_analyzed"]} == {"scripts/run.sh", "settings.cfg"}
    assert {e["reason"] for e in data["files_not_analyzed"]} <= _REASONS
    assert "2 of 2 changed files not analyzable" in data["summary"]["verdict"]
    assert data["summary"]["partial_success"] is True


def test_mixed_change_counts_analyzed_and_not(repo, monkeypatch):
    (repo / "src" / "app.py").write_text("def greet(name, loud=False):\n    return name\n")
    (repo / "settings.cfg").write_text("level = 3\n")
    git_commit(repo, "edit both")
    data = json.loads(_run(repo, monkeypatch, "--json").output)
    assert data["summary"]["files_changed"] == 1
    assert data["summary"]["files_changed_total"] == 2
    assert "1 of 2 changed files not analyzable" in data["summary"]["verdict"]
    assert data["summary"]["partial_success"] is True


# --- valid-case controls: output unchanged when every file was examined -------


def test_control_python_edit_verdict_conserved(repo, monkeypatch):
    """Parent-green: the verdict, the text and the legacy fields of a fully analyzed change."""
    (repo / "src" / "app.py").write_text("def greet(name, loud=False):\n    return name\n")
    git_commit(repo, "edit python")
    data = json.loads(_run(repo, monkeypatch, "--json").output)
    assert data["summary"]["verdict"] == "1 structural change in 1 file"
    assert data["summary"]["files_changed"] == 1
    assert "not analyzable" not in _run(repo, monkeypatch).output


def test_full_analysis_has_empty_new_schema_and_is_not_partial(repo, monkeypatch):
    """New schema: a fully analyzed change reports no omissions and partial_success false."""
    (repo / "src" / "app.py").write_text("def greet(name, loud=False):\n    return name\n")
    git_commit(repo, "edit python")
    data = json.loads(_run(repo, monkeypatch, "--json").output)
    assert data["summary"]["files_changed_total"] == 1
    assert data["files_not_analyzed"] == []
    assert data["summary"]["partial_success"] is False


def test_control_no_changed_files_verdict_unchanged(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert index_in_process(repo)[1] == 0
    result = invoke_cli(CliRunner(), ["semantic-diff", "--base", "HEAD"], cwd=repo)
    assert result.exit_code == 0
    assert "No changed files" in result.output


@pytest.mark.parametrize("readable", [False, True])
def test_unreadable_current_side_is_disclosed_without_false_removal(tmp_path, monkeypatch, readable):
    from roam.commands import cmd_semantic_diff as semantic

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "app.py"
    path.write_text("def greet():\n    return 2\n")
    monkeypatch.setattr(semantic, "ensure_index", lambda: None)
    monkeypatch.setattr(semantic, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(semantic, "_git_changed_files", lambda *a: ["app.py"])
    # Named helper contract change: the command requests strict reads so a
    # confirmed absence and a Git failure no longer have the same value.
    monkeypatch.setattr(semantic, "_git_show", lambda *a, **kw: b"def greet():\n    return 1\n")
    real_read = Path.read_bytes

    def read(file):
        if file == path and not readable:
            raise PermissionError("unreadable current file")
        return real_read(file)

    monkeypatch.setattr(Path, "read_bytes", read)
    result = CliRunner().invoke(semantic.semantic_diff, ["--base", "HEAD"], obj={"json": True})
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is not readable
    assert data["summary"]["symbols_removed"] == 0
    assert bool(data["files_not_analyzed"]) is not readable
    if not readable:
        assert data["files_not_analyzed"] == [{"file": "app.py", "reason": "read_failed"}]


@pytest.mark.parametrize("absent", [False, True])
def test_unavailable_base_side_is_not_reported_as_added(repo, monkeypatch, absent):
    from roam.commands import cmd_semantic_diff as semantic

    monkeypatch.chdir(repo)
    monkeypatch.setattr(semantic, "ensure_index", lambda: None)
    monkeypatch.setattr(semantic, "find_project_root", lambda: repo)
    if absent:
        (repo / "new.py").write_text("def fresh():\n    return 1\n")
    path = "new.py" if absent else "src/app.py"
    monkeypatch.setattr(semantic, "_git_changed_files", lambda *a: [path])
    # Named helper contract change: old reads collapsed failures and absence
    # to None. The strict reader now reserves None for confirmed absence and
    # raises on an unavailable blob so the caller can disclose the Git kind.
    from roam.commands.changed_files import GitComparisonError

    def show(*args, strict):
        assert strict
        if not absent:
            raise GitComparisonError("git_error", "unavailable base blob")
        return None

    monkeypatch.setattr(semantic, "_git_show", show)
    result = CliRunner().invoke(semantic.semantic_diff, ["--base", "HEAD"], obj={"json": True})
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is not absent
    assert data["summary"]["symbols_added"] == int(absent)


@pytest.mark.skipif(os.name != "posix", reason="executable mode bit needs POSIX")
def test_unicode_mode_only_path_is_literal(repo, monkeypatch):
    from roam.commands.changed_files import git_mode_only_files

    path = repo / "caf\u00e9.sh"
    path.write_text("echo hi\n")
    git_commit(repo, "unicode script")
    path.chmod(0o755)
    assert path.name in git_mode_only_files(repo, "HEAD")
