"""CLI and MCP disclose the state of the index they actually queried."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing

import pytest

from roam.cli import cli
from roam.db.connection import get_db_path


@pytest.fixture
def query_project(project_factory, monkeypatch):
    project = project_factory(
        {"app.py": "def indexed_helper():\n    return 1\n", "other.py": "def other_helper():\n    return 2\n"}
    )
    monkeypatch.chdir(project)
    return project


def _age_index(seconds=66 * 86400):
    old = int(time.time()) - seconds
    db_path = get_db_path()
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("UPDATE index_manifest SET indexed_at = ?", (old,))
    os.utime(db_path, (old, old))


def test_25_hour_index_with_unchanged_tree(query_project, cli_runner, monkeypatch):
    _age_index(25 * 3600)
    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""

    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_state"] == "fresh"
    assert envelope["_meta"]["index_changed_files"] == 0
    assert abs(envelope["_meta"]["index_age_s"] - 25 * 3600) < 10
    assert "stale_index" not in envelope["_meta"]
    assert envelope["summary"]["partial_success"] is False


def test_snapshot_write_preserves_index_age(query_project, cli_runner):
    _age_index(25 * 3600)
    db_path = get_db_path()
    before = db_path.stat().st_mtime
    saved = cli_runner.invoke(cli, ["trends", "--save", "--tag", "after-index"])
    assert saved.exit_code == 0, saved.output
    assert "Snapshot saved" in saved.stdout
    assert db_path.stat().st_mtime > before

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert abs(json.loads(result.stdout)["_meta"]["index_age_s"] - 25 * 3600) < 10


def test_git_failure_leaves_index_state_unknown(query_project, cli_runner, monkeypatch):
    run = subprocess.run

    def fail_git_diff(command, *args, **kwargs):
        if command[:2] == ["git", "diff"] and "--name-only" in command:
            return subprocess.CompletedProcess(command, 128, b"", b"fatal: bad revision")
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_git_diff)
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert "Could not check index freshness" in result.stderr

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_state"] == "unknown"
    assert envelope["_meta"]["index_changed_files"] is None
    assert "git diff" in envelope["_meta"]["index_state_reason"]
    assert "stale_index" not in envelope["_meta"]
    assert envelope["summary"]["partial_success"] is True

    doctor = cli_runner.invoke(cli, ["--json", "doctor"])
    check = next(check for check in json.loads(doctor.stdout)["checks"] if check["name"] == "Index freshness")
    assert check["state"] == "unknown"
    assert check["reason"] == envelope["_meta"]["index_state_reason"]


@pytest.mark.parametrize("flags", [[], ["--compact"]], ids=["full", "compact"])
def test_old_index_json(query_project, cli_runner, flags):
    (query_project / "app.py").write_text("def renamed_helper():\n    return 1\n")
    (query_project / "other.py").unlink()
    (query_project / "scratch.py").write_text("def untracked():\n    pass\n")
    _age_index()

    result = cli_runner.invoke(cli, ["--json", *flags, "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    assert "indexed_helper" in result.stdout
    assert envelope["_meta"]["stale_index"] is True
    assert envelope["_meta"]["index_changed_files"] == 2
    assert abs(envelope["_meta"]["index_age_s"] - 66 * 86400) < 10
    assert envelope["summary"]["partial_success"] is True


def test_old_index_text(query_project, cli_runner):
    (query_project / "app.py").write_text("def renamed_helper():\n    return 1\n")
    _age_index()

    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert "indexed_helper" in result.stdout
    warnings = result.stderr.splitlines()
    assert len(warnings) == 1, result.stderr
    assert all(term in warnings[0] for term in ("66 days", "1 changed file", "roam index"))
    assert "roam index" not in result.stdout


def test_fresh_index_has_no_stale_flag_or_warning(query_project, cli_runner):
    text_result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert text_result.exit_code == 0, text_result.output
    assert text_result.stderr == ""

    json_result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert json_result.exit_code == 0, json_result.output
    assert json_result.stderr == ""
    envelope = json.loads(json_result.stdout)
    assert envelope["_meta"]["index_changed_files"] == 0
    assert "stale_index" not in envelope["_meta"]
    assert envelope["summary"]["partial_success"] is False


def test_old_index_mcp(query_project, monkeypatch):
    from roam import mcp_server

    monkeypatch.setattr(mcp_server, "_ROAM_RESULT_CACHE", {})
    _age_index()
    fresh_worktree = mcp_server.search_symbol("indexed_helper")
    (query_project / "app.py").write_text("def renamed_helper():\n    return 1\n")
    changed_worktree = mcp_server.search_symbol("indexed_helper")

    assert fresh_worktree["_meta"]["index_state"] == "fresh"
    assert "stale_index" not in fresh_worktree["_meta"]
    assert fresh_worktree["_meta"]["index_changed_files"] == 0
    assert changed_worktree["_meta"]["stale_index"] is True
    assert changed_worktree["_meta"]["index_changed_files"] == 1
    assert changed_worktree["_meta"]["cache_hit"] is True
    assert changed_worktree["summary"]["partial_success"] is True
    assert changed_worktree["summary"]["verdict"] == fresh_worktree["summary"]["verdict"]
    assert not changed_worktree["summary"]["verdict"].startswith("INDEX STALE")


def test_tracked_changes_on_a_recent_index(query_project, cli_runner):
    (query_project / "new.py").write_text("def newly_tracked():\n    pass\n")
    subprocess.run(["git", "add", "new.py"], cwd=query_project, check=True)
    (query_project / "scratch.py").write_text("def untracked():\n    pass\n")

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_changed_files"] == 1
    assert envelope["_meta"]["stale_index"] is True
    assert envelope["_meta"]["index_state"] == "stale"
    assert envelope["_meta"]["index_age_s"] < 3600
    assert envelope["summary"]["partial_success"] is True

    text = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert text.exit_code == 0, text.output
    assert "Index is stale" in text.stderr
    assert "1 changed file" in text.stderr


def test_dirty_build_counts_reverting_an_indexed_edit(project_factory, cli_runner, monkeypatch):
    project = project_factory({"app.py": "def committed_helper():\n    pass\n"}, index=False)
    monkeypatch.chdir(project)
    app = project / "app.py"
    app.write_text("def indexed_helper():\n    pass\n")
    old = time.time() - 5
    os.utime(app, (old, old))
    built = cli_runner.invoke(cli, ["index"])
    assert built.exit_code == 0, built.output

    subprocess.run(["git", "checkout", "--", "app.py"], cwd=project, check=True)
    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["results"][0]["name"] == "indexed_helper"
    assert envelope["_meta"]["index_changed_files"] == 1
    assert envelope["_meta"]["stale_index"] is True


def test_stale_refusal_is_opt_in(query_project, cli_runner, monkeypatch):
    (query_project / "app.py").write_text("def renamed_helper():\n    return 1\n")
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 1, result.output
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    assert envelope["error_code"] == "INDEX_STALE"
    assert envelope["_meta"]["stale_index"] is True
    assert envelope["next_command"] == "roam index"

    refresh = cli_runner.invoke(cli, ["index"])
    assert refresh.exit_code == 0, refresh.output
    refreshed = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert refreshed.exit_code == 0, refreshed.output
    assert "stale_index" not in json.loads(refreshed.stdout)["_meta"]
