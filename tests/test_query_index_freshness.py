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
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "Move HEAD"], check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    run = subprocess.run

    def fail_git_status(command, *args, **kwargs):
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 128, b"", b"fatal: bad revision")
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_git_status)
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    result = cli_runner.invoke(cli, ["search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert "Could not check index freshness" in result.stderr

    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_state"] == "unknown"
    assert envelope["_meta"]["index_changed_files"] is None
    assert "git status" in envelope["_meta"]["index_state_reason"]
    assert envelope["_meta"]["git_history_head_commit"] == head
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


@pytest.mark.parametrize("legacy_manifest", [False, True], ids=["recorded-paths", "legacy"])
def test_dirty_build_counts_reverting_an_indexed_edit(project_factory, cli_runner, monkeypatch, legacy_manifest):
    project = project_factory({"app.py": "def committed_helper():\n    pass\n"}, index=False)
    monkeypatch.chdir(project)
    app = project / "app.py"
    app.write_text("def indexed_helper():\n    pass\n")
    old = time.time() - 5
    os.utime(app, (old, old))
    built = cli_runner.invoke(cli, ["index"])
    assert built.exit_code == 0, built.output

    if legacy_manifest:
        with closing(sqlite3.connect(get_db_path())) as conn, conn:
            notes = json.loads(conn.execute("SELECT notes FROM index_manifest").fetchone()[0])
            del notes["git_dirty_paths"]
            conn.execute("UPDATE index_manifest SET notes = ?", (json.dumps(notes),))

    subprocess.run(["git", "checkout", "--", "app.py"], cwd=project, check=True)
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=project)
    if not legacy_manifest:
        run = subprocess.run

        def require_candidates(command, *args, **kwargs):
            assert command[:2] != ["git", "ls-files"]
            return run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", require_candidates)
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


@pytest.mark.parametrize("committed", [False, True], ids=["worktree", "committed"])
def test_deleted_source_and_excluded_untracked_directory(project_factory, monkeypatch, committed):
    from roam.db.freshness import probe_index_freshness

    project = project_factory(
        {
            ".gitignore": ".roam/\nignored.py\n",
            ".roamignore": "excluded/\n",
            "app.py": "def helper(): pass\n",
            "deleted.py": "def deleted(): pass\n",
        }
    )
    monkeypatch.chdir(project)
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=project)
    (project / "deleted.py").unlink()
    if committed:
        subprocess.run(["git", "commit", "-qam", "Delete indexed file"], cwd=project, check=True)
    for name in ("excluded/untracked.py", "new/admitted.py", "new/ignored.py"):
        path = project / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("def untracked(): pass\n")

    freshness = probe_index_freshness(get_db_path(), project)
    assert freshness.state == "stale"
    assert freshness.changed_paths == {"deleted.py", "new/admitted.py"}


def test_clean_build_checks_git_status_without_listing_all_files(query_project, monkeypatch):
    from roam.db.freshness import probe_index_freshness

    run = subprocess.run
    commands = []

    def record(command, *args, **kwargs):
        if command[0] == "git":
            commands.append(command[1])
        return run(command, *args, **kwargs)

    (query_project / "app.py").write_text("def edited(): pass\n")
    monkeypatch.setattr(subprocess, "run", record)
    freshness = probe_index_freshness(get_db_path(), query_project)

    assert freshness.changed_paths == {"app.py"}
    assert commands == ["status"]


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
        if command[:2] == ["git", "status"]:
            assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"
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


def test_init_indexes_the_configuration_it_writes(project_factory, cli_runner, monkeypatch):
    from roam.index.manifest import _config_hash

    project = project_factory(
        {".gitignore": "# project exclusions\n", "app.py": "def indexed_helper(): pass\n"}, index=False
    )
    monkeypatch.chdir(project)
    result = cli_runner.invoke(cli, ["--json", "init"])
    assert result.exit_code == 0, result.output

    with closing(sqlite3.connect(get_db_path())) as conn:
        assert conn.execute("SELECT config_hash FROM index_manifest").fetchone()[0] == _config_hash(project)

    run = subprocess.run

    def no_full_comparison(command, *args, **kwargs):
        assert command[:2] != ["git", "ls-files"]
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", no_full_comparison)
    result = cli_runner.invoke(cli, ["--json", "search", "indexed_helper"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_state"] == "fresh"


def test_init_discloses_existing_stale_index(query_project, cli_runner):
    (query_project / "app.py").write_text("def changed_since_init(): pass\n")

    result = cli_runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    assert "Index is stale" in result.stderr
    assert "Roam is ready" not in result.stdout


def test_init_discloses_existing_stale_index_in_json(query_project, cli_runner):
    (query_project / "app.py").write_text("def changed_since_init(): pass\n")

    result = cli_runner.invoke(cli, ["--json", "init"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["_meta"]["index_state"] == "stale"
    assert envelope["_meta"]["index_changed_files"] == 1
    assert envelope["summary"]["partial_success"] is True
    assert "Index is stale" in envelope["warnings"][-1]["message"]


@pytest.mark.parametrize("move_head", [False, True], ids=["revert-and-edit", "checkout"])
def test_index_discloses_changes_between_manifest_snapshots(project_factory, monkeypatch, move_head):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    project = project_factory({"app.py": "def original(): pass\n", "other.py": "def other(): pass\n"}, index=False)
    monkeypatch.chdir(project)
    app = project / "app.py"
    app.write_text("def indexed_during_race(): pass\n")
    if move_head:
        subprocess.run(["git", "commit", "-qam", "New source"], check=True)

    record_manifest = Indexer._record_manifest

    def change_tree(indexer, *args, **kwargs):
        if move_head:
            subprocess.run(["git", "checkout", "-q", "--detach", "HEAD^"], check=True)
        else:
            subprocess.run(["git", "restore", "app.py"], check=True)
            (project / "other.py").write_text("def changed_after_read(): pass\n")
        return record_manifest(indexer, *args, **kwargs)

    monkeypatch.setattr(Indexer, "_record_manifest", change_tree)
    assert Indexer(project).run(quiet=True)
    db = get_db_path(project)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT 1 FROM symbols WHERE name='indexed_during_race'").fetchone() == (1,)
        dirty_hash, notes = conn.execute("SELECT git_dirty_hash, notes FROM index_manifest").fetchone()
    freshness = probe_index_freshness(db, project)
    expected = {"app.py"} if move_head else {"app.py", "other.py"}
    assert freshness.changed_paths == expected
    assert dirty_hash
    assert json.loads(notes)["git_dirty_paths"] == ([] if move_head else sorted(expected))


def test_gitignore_edit_compares_scope_with_one_pattern_compilation(project_factory, monkeypatch):
    from roam.db.freshness import probe_index_freshness
    from roam.index import gitignore
    from roam.index.discovery import load_exclude_patterns

    project = project_factory(
        {
            ".gitignore": ".roam/\nnew.py\n",
            ".roamignore": "generated/\n",
            **{f"module_{i}.py": f"def helper_{i}(): pass\n" for i in range(50)},
        },
        index=False,
    )
    monkeypatch.chdir(project)
    (project / "new.py").write_text("def admitted(): pass\n")
    (project / "scratch.py").write_text("def indexed_untracked(): pass\n")
    from roam.index.indexer import Indexer

    assert Indexer(project).run(quiet=True)
    (project / ".gitignore").write_text(".roam/\nscratch.py\n")
    compile_pattern = gitignore._compile_pattern
    calls = []

    def record(pattern):
        calls.append(pattern)
        return compile_pattern(pattern)

    monkeypatch.setattr(gitignore, "_compile_pattern", record)
    freshness = probe_index_freshness(get_db_path(), project)
    assert freshness.state == "stale"
    assert freshness.changed_paths == {"new.py", "scratch.py"}
    assert len(calls) <= len(load_exclude_patterns(project))

    (project / ".gitignore").write_text(".roam/\nnew.py\n")
    (project / ".roamignore").write_text("generated/\nmodule_1.py\n")
    calls.clear()
    freshness = probe_index_freshness(get_db_path(), project)
    assert freshness.changed_paths == {"module_1.py"}
    assert len(calls) == len(load_exclude_patterns(project))


def test_gitignore_inside_untracked_directory_removes_indexed_file(project_factory):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    repo = project_factory({"app.py": "def helper(): pass\n"}, index=False)
    directory = repo / "untracked"
    directory.mkdir()
    (directory / "scratch.py").write_text("def scratch(): pass\n")
    assert Indexer(repo).run(quiet=True)
    db = get_db_path(repo)
    assert probe_index_freshness(db, repo).state == "fresh"

    (directory / ".gitignore").write_text("scratch.py\n")
    assert probe_index_freshness(db, repo).changed_paths == {"untracked/scratch.py"}


@pytest.mark.parametrize(
    ("args", "reads_history"),
    [
        (["weather"], True),
        (["health"], True),
        (["diagnose", "indexed_helper"], True),
        (["search", "indexed_helper"], False),
    ],
    ids=["weather", "health", "diagnose", "search"],
)
def test_head_only_change_discloses_git_history_lag(query_project, cli_runner, monkeypatch, args, reads_history):
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    indexed = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    initial = cli_runner.invoke(cli, args)
    assert initial.exit_code == 0, initial.output
    assert "NOTE: Indexed Git history" not in initial.output

    subprocess.run(["git", "commit", "--allow-empty", "-qm", "History-only change"], check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert indexed != head

    result = cli_runner.invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    meta = envelope["_meta"]
    assert meta["index_state"] == "fresh"
    assert meta["index_changed_files"] == 0
    assert "stale_index" not in meta
    assert meta["git_history_behind"] is True
    assert meta["git_history_indexed_commit"] == indexed
    assert meta["git_history_head_commit"] == head
    if not reads_history:
        assert envelope["summary"]["partial_success"] is False

    result = cli_runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert ("NOTE: Indexed Git history" in result.output) is reads_history
    if reads_history:
        assert "Indexed Git history is behind HEAD" in result.output
        assert indexed[:12] in result.output and head[:12] in result.output
    assert "Index is stale" not in result.stderr

    refreshed = cli_runner.invoke(cli, ["index"])
    assert refreshed.exit_code == 0, refreshed.output
    result = cli_runner.invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    assert "git_history_behind" not in json.loads(result.stdout)["_meta"]


@pytest.mark.parametrize("diverged", [False, True], ids=["ahead", "diverged"])
def test_git_history_note_after_backward_or_divergent_move(project_factory, cli_runner, monkeypatch, diverged):
    project = project_factory({"app.py": "def indexed_helper(): pass\n"}, index=False)
    monkeypatch.chdir(project)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "Indexed history"], check=True)
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0

    subprocess.run(["git", "checkout", "-q", "--detach", "HEAD^"], check=True)
    if diverged:
        subprocess.run(["git", "commit", "--allow-empty", "-qm", "Divergent history"], check=True)

    result = cli_runner.invoke(cli, ["weather"])
    assert result.exit_code == 0, result.output
    expected = "has diverged from HEAD" if diverged else "is ahead of HEAD"
    assert f"Indexed Git history {expected}" in result.stdout


def test_linked_worktree_uses_its_own_index(project_factory, tmp_path):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    main = project_factory({"app.py": "def main(): pass\n"}, index=False)
    worktree = tmp_path / "linked worktree"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach", str(worktree)], check=True)
    assert (worktree / ".git").is_file()
    assert Indexer(worktree).run(quiet=True)

    (main / "app.py").write_text("def outside_edit(): pass\n")
    db = get_db_path(worktree)
    assert probe_index_freshness(db, worktree).state == "fresh"
    (worktree / "app.py").write_text("def worktree_edit(): pass\n")
    assert probe_index_freshness(db, worktree).changed_paths == {"app.py"}


def test_explicit_project_root_inside_repository(project_factory):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    repo = project_factory(
        {"nested/app.py": "def helper(): pass\n", "outside.py": "def outer(): pass\n"},
        index=False,
    )
    project = repo / "nested"
    app = project / "app.py"
    app.write_text("def dirty_at_build(): pass\n")
    assert Indexer(project).run(quiet=True)
    db = get_db_path(project)
    assert probe_index_freshness(db, project).state == "fresh"

    (repo / "outside.py").write_text("def outside_edit(): pass\n")
    assert probe_index_freshness(db, project).state == "fresh"

    subprocess.run(["git", "-C", str(repo), "restore", "nested/app.py"], check=True)
    assert probe_index_freshness(db, project).changed_paths == {"app.py"}
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "Outside edit"], check=True)
    app.write_text("def dirty_at_build(): pass\n")
    assert probe_index_freshness(db, project).state == "fresh"

    (project / "new.py").write_text("def new_helper(): pass\n")
    (repo / ".gitignore").write_text(".roam/\nnested/new.py\n")
    assert probe_index_freshness(db, project).state == "fresh"
    (repo / ".gitignore").write_text(".roam/\n")
    assert probe_index_freshness(db, project).changed_paths == {"new.py"}

    subprocess.run(["git", "-C", str(repo), "add", "nested"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "Inside edit"], check=True)
    assert probe_index_freshness(db, project).changed_paths == {"new.py"}


def test_pruned_indexed_commit_after_rebase_is_not_fresh(project_factory, cli_runner, monkeypatch):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    repo = project_factory({"app.py": "def before(): pass\n"}, index=False)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    base = git("rev-parse", "HEAD")
    (repo / "app.py").write_text("def after(): pass\n")
    git("commit", "-qam", "Indexed source")
    indexed = git("rev-parse", "HEAD")
    assert Indexer(repo).run(quiet=True)
    db = get_db_path(repo)

    git("checkout", "-qb", "new-base", base)
    git("commit", "--allow-empty", "-qm", "Updated base")
    git("checkout", "-q", "-")
    git("rebase", "new-base")
    assert git("rev-parse", "HEAD^{tree}") == git("rev-parse", f"{indexed}^{{tree}}")
    git("reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
    git("gc", "--prune=now")
    assert subprocess.run(["git", "cat-file", "-e", indexed], cwd=repo, capture_output=True).returncode != 0

    freshness = probe_index_freshness(db, repo)
    assert freshness.state in {"stale", "unknown"}
    assert freshness.reason and "git diff" in freshness.reason
    assert freshness.indexed_commit == indexed
    assert freshness.head_commit == git("rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    result = cli_runner.invoke(cli, ["weather"])
    assert result.exit_code == 0, result.output
    assert "Indexed Git history is unreachable" in result.stdout


def test_shallow_clone_compares_snapshots_after_fetch(project_factory, tmp_path):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    origin = project_factory({"app.py": "def initial(): pass\n"}, index=False)
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth=1", origin.as_uri(), str(clone)], check=True)
    assert subprocess.check_output(["git", "rev-parse", "--is-shallow-repository"], cwd=clone).strip() == b"true"
    assert Indexer(clone).run(quiet=True)
    db = get_db_path(clone)
    assert probe_index_freshness(db, clone).state == "fresh"

    (origin / "app.py").write_text("def updated(): pass\n")
    subprocess.run(["git", "commit", "-qam", "Change source"], cwd=origin, check=True)
    subprocess.run(["git", "fetch", "--depth=1", "origin"], cwd=clone, check=True)
    subprocess.run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=clone, check=True)
    freshness = probe_index_freshness(db, clone)
    assert freshness.state == "stale"
    assert freshness.changed_paths == {"app.py"}


def test_git_paths_preserve_spaces_unicode_and_quoted_names(project_factory):
    from roam.db.freshness import probe_index_freshness

    path = "a directory/café file.py"
    repo = project_factory({path: "def initial(): pass\n"})
    db = get_db_path(repo)
    assert probe_index_freshness(db, repo).state == "fresh"
    (repo / path).write_text("def edited(): pass\n")
    assert probe_index_freshness(db, repo).changed_paths == {path}
    subprocess.run(["git", "commit", "-qam", "Edit quoted path"], cwd=repo, check=True)
    assert probe_index_freshness(db, repo).changed_paths == {path}


def test_git_status_preserves_unmerged_path(project_factory, monkeypatch):
    from roam.db.freshness import probe_index_freshness
    from roam.index.indexer import Indexer

    path = "module with spaces.py"
    repo = project_factory({path: "def original(): pass\n"}, index=False)
    monkeypatch.chdir(repo)
    subprocess.run(["git", "checkout", "-qb", "side"], check=True)
    (repo / path).write_text("def side(): pass\n")
    subprocess.run(["git", "commit", "-qam", "Side edit"], check=True)
    subprocess.run(["git", "checkout", "-q", "-"], check=True)
    (repo / path).write_text("def main(): pass\n")
    subprocess.run(["git", "commit", "-qam", "Main edit"], check=True)
    assert Indexer(repo).run(quiet=True)
    merge = subprocess.run(["git", "merge", "side"], capture_output=True, check=False)
    assert merge.returncode == 1
    assert probe_index_freshness(get_db_path(repo), repo).changed_paths == {path}
