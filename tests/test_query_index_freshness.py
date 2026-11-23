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

    def fail_git_list(command, *args, **kwargs):
        if command[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(command, 128, b"", b"fatal: bad revision")
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_git_list)
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert "Could not check index freshness" in result.stderr

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_state"] == "unknown"
    assert envelope["_meta"]["index_changed_files"] is None
    assert "git ls-files" in envelope["_meta"]["index_state_reason"]
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
    assert envelope["_meta"]["index_changed_files"] == 3
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
    monkeypatch.setattr(mcp_server, "_FRESHNESS_CACHE_TTL_S", 0)
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
    assert envelope["_meta"]["index_changed_files"] == 2
    assert envelope["_meta"]["stale_index"] is True
    assert envelope["_meta"]["index_state"] == "stale"
    assert envelope["_meta"]["index_age_s"] < 3600
    assert envelope["summary"]["partial_success"] is True

    text = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert text.exit_code == 0, text.output
    assert "Index is stale" in text.stderr
    assert "2 changed files" in text.stderr


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


@pytest.mark.parametrize("args", [["health"], ["diagnose", "indexed_helper"]], ids=["health", "diagnose"])
def test_command_uses_one_freshness_rule(query_project, cli_runner, args):
    (query_project / "app.py").write_text("# change\ndef indexed_helper():\n    return 1\n")
    result = cli_runner.invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["index_status"] == envelope["_meta"].get("index_status")
    assert envelope["index_status"]["dirty_files"] == 1
    result = cli_runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert result.output.lower().count("roam index\x60 to refresh") == 1, result.output


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("batch_search", [["indexed_helper"]]),
        ("batch_get", [["indexed_helper"]]),
        ("oracle_batch", [[{"oracle": "symbol-exists", "name": "indexed_helper"}]]),
    ],
)
def test_direct_mcp_reads_disclose_freshness(query_project, tool, args):
    from roam import mcp_server

    (query_project / "app.py").write_text("# changed\ndef indexed_helper():\n    return 1\n")
    result = getattr(mcp_server, tool)(*args)
    assert result["results"]
    assert result["_meta"]["index_state"] == "stale"
    assert result["_meta"]["index_changed_files"] == 1
    assert result["_meta"]["stale_index"] is True
    assert result["summary"]["partial_success"] is True


def test_legacy_status_and_text_hint_opt_out(query_project, cli_runner, monkeypatch):
    (query_project / "app.py").write_text("def updated_helper():\n    return 1\n")
    monkeypatch.setenv("ROAM_NO_STALENESS_HINT", "1")
    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    meta = json.loads(result.stdout)["_meta"]
    legacy = meta["index_status"]
    assert legacy["fresh"] is False
    assert legacy["dirty_files"] == meta["index_changed_files"] == 1
    assert legacy["head_commit"] == legacy["indexed_commit"]
    assert "roam index" in legacy["hint"]


@pytest.mark.parametrize(
    "args",
    [
        ["stats"],
        ["diff"],
        ["constitution", "init"],
        ["compatibility", "--write-baseline", "surface.json"],
        ["exit-codes"],
    ],
    ids=["stats", "diff", "constitution", "compatibility", "exit-codes"],
)
def test_stale_insensitive_commands_remain_available(query_project, cli_runner, monkeypatch, args):
    (query_project / "app.py").write_text("def updated_helper():\n    return 1\n")
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    result = cli_runner.invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_state"] == "stale"
    assert envelope["_meta"]["stale_index"] is True
    assert envelope["summary"]["partial_success"] is False


@pytest.mark.parametrize("dirty_build", [False, True], ids=["clean", "dirty"])
def test_indexable_paths_are_counted_in_clean_and_dirty_builds(project_factory, cli_runner, monkeypatch, dirty_build):
    project = project_factory(
        {
            "app.py": "def indexed_helper():\n    return 1\n",
            "rename_me.py": "def to_rename():\n    pass\n",
            "NOTES.txt": "original\n",
            ".roamignore": "excluded.py\n",
            ".gitignore": ".roam/\nignored.py\n",
        },
        index=False,
    )
    monkeypatch.chdir(project)
    if dirty_build:
        (project / "app.py").write_text("def indexed_helper():\n    return 2\n")
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0

    def count():
        result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)["_meta"]["index_changed_files"]

    assert count() == 0
    (project / "NOTES.txt").write_text("updated\n")
    (project / "excluded.py").write_text("def excluded(): pass\n")
    (project / "ignored.py").write_text("def ignored(): pass\n")
    assert count() == 0
    (project / "new.py").write_text("def added(): pass\n")
    assert count() == 1
    (project / "rename_me.py").rename(project / "renamed.py")
    assert count() == 3
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    assert count() == 0


def test_non_git_query_and_doctor(tmp_path, cli_runner, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("def indexed_helper():\n    return 1\n")
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    meta = envelope["_meta"]
    assert meta["index_state"] == "unknown"
    assert "Git" in meta["index_state_reason"]
    assert envelope["summary"]["partial_success"] is False

    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""

    result = cli_runner.invoke(cli, ["--json", "doctor"])
    assert result.exit_code == 0, result.output
    doctor = json.loads(result.stdout)
    check = next(check for check in doctor["checks"] if check["name"] == "Index freshness")
    assert check["skipped"] is True
    assert check["state"] == "unknown"
    assert check["reason"] == meta["index_state_reason"]


def test_freshness_check_has_subsecond_budget(query_project, cli_runner, monkeypatch):
    run = subprocess.run
    timeouts = []

    def time_out_freshness_git(command, *args, **kwargs):
        if command[:2] == ["git", "ls-files"] or (command[:2] == ["git", "diff"] and "-z" in command):
            timeout = kwargs["timeout"]
            timeouts.append(timeout)
            raise subprocess.TimeoutExpired(command, timeout)
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", time_out_freshness_git)
    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert len(timeouts) == 1
    assert 0 < timeouts[0] <= 0.3
    meta = json.loads(result.stdout)["_meta"]
    assert meta["index_state"] == "unknown"
    assert meta["index_state_reason"] == "freshness check exceeded its time budget"

    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert len(result.stderr.splitlines()) == 1
    assert "Could not check index freshness" in result.stderr


def test_mcp_cache_hit_reuses_recent_freshness(query_project, monkeypatch):
    from roam import mcp_server
    from roam.db import freshness

    monkeypatch.setattr(mcp_server, "_ROAM_RESULT_CACHE", {})
    observations = []
    read = freshness.read_query_index_freshness

    def record(*args, **kwargs):
        result = read(*args, **kwargs)
        observations.append(result)
        return result

    monkeypatch.setattr(freshness, "read_query_index_freshness", record)
    initial = mcp_server.search_symbol("indexed_helper")
    assert len(observations) == 1
    assert initial["_meta"]["index_state"] == "fresh"
    for _ in range(2):
        cached = mcp_server.search_symbol("indexed_helper")
        assert cached["_meta"]["cache_hit"] is True
        assert cached["_meta"]["index_state"] == "fresh"
    assert len(observations) == 1


def test_mcp_path_completion_discloses_freshness(query_project):
    from roam.mcp_server import complete_tool

    (query_project / "new.py").write_text("def new_helper(): pass\n")
    result = complete_tool("app", kind="path", root=str(query_project))
    assert result["paths"] == ["app.py"]
    assert result["_meta"]["index_state"] == "stale"
    assert result["_meta"]["index_changed_files"] == 1


def test_protocol_completion_discloses_freshness(query_project):
    import asyncio
    from types import SimpleNamespace

    from mcp.server.lowlevel import Server
    from mcp.types import CompleteRequest, CompleteRequestParams, CompletionArgument, PromptReference

    from roam.mcp_extras.completions import install_completion_handler

    server = Server("test-completions")
    assert install_completion_handler(SimpleNamespace(_mcp_server=server))
    (query_project / "new.py").write_text("def new_helper(): pass\n")
    request = CompleteRequest(
        method="completion/complete",
        params=CompleteRequestParams(
            ref=PromptReference(type="ref/prompt", name="lookup"),
            argument=CompletionArgument(name="path", value="app"),
        ),
    )
    result = asyncio.run(server.request_handlers[CompleteRequest](request)).root
    assert result.completion.values == ["app.py"]
    assert result.meta["index_state"] == "stale"
    assert result.meta["index_changed_files"] == 1


def test_next_uses_content_freshness(query_project, cli_runner):
    db_mtime = get_db_path().stat().st_mtime
    app = query_project / "app.py"
    os.utime(app, (db_mtime + 10, db_mtime + 10))
    result = cli_runner.invoke(cli, ["--json", "next"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["state"]["index_stale"] is False
    assert envelope["_meta"]["index_state"] == "fresh"


def test_compile_freshness_uses_indexed_content_and_manifest_age(query_project):
    from roam.plan.compiler import _index_freshness_signals

    _age_index(25 * 3600)
    staleness, newer_files = _index_freshness_signals(["app.py"], str(query_project))
    assert staleness is None
    assert newer_files is None

    app = query_project / "app.py"
    app.write_text("def indexed_helper():\n    return 555\n")
    os.utime(app, (100, 100))
    staleness, newer_files = _index_freshness_signals(["app.py"], str(query_project))
    assert staleness["is_stale"] is True
    assert newer_files["files_newer_than_index"] == ["app.py"]
