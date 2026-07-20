"""Tests for ``roam compile-cache`` cache maintenance."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli


def _write_private_compile_telemetry(root: Path, content: str) -> Path:
    from roam.security.owner_only import ensure_owner_only_path

    state = root / ".roam"
    state.mkdir(exist_ok=True)
    log = state / "compile-runs.jsonl"
    log.write_text(content, encoding="utf-8")
    assert ensure_owner_only_path(state)
    assert ensure_owner_only_path(log)
    return log


def _git_init(path: Path) -> str:
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True)
    return head.stdout.strip()


def test_compile_cache_clear_stale_drops_only_wrong_head_rows(cli_runner, tmp_path):
    head = _git_init(tmp_path)
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db_path = roam_dir / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE env_cache (key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, envelope_json TEXT, ts REAL)"
    )
    conn.executemany(
        "INSERT INTO env_cache (key, repo_head, art_label, envelope_json, ts) VALUES (?, ?, ?, ?, ?)",
        [
            ("current", head, "facts", "{}", 1.0),
            ("stale", "not-the-current-head", "facts", "{}", 2.0),
        ],
    )
    conn.commit()
    conn.close()

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "clear", "--root", str(tmp_path), "--stale"],
        json_mode=True,
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-clear"
    assert data["summary"]["rows_dropped"] == 1
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT key, repo_head FROM env_cache ORDER BY key").fetchall()
    conn.close()
    assert rows == [("current", head)]


def test_compile_cache_clear_stale_no_cache_emits_json_envelope(cli_runner, tmp_path):
    result = invoke_cli(
        cli_runner,
        ["compile-cache", "clear", "--root", str(tmp_path), "--stale"],
        json_mode=True,
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-clear"
    assert data["summary"]["verdict"] == "no cache to clear"
    assert data["summary"]["rows_dropped"] == 0
    assert data["agent_contract"]["facts"] == ["0 dropped records", "cache clear passed"]


def test_compile_cache_clear_requires_selector_in_json_mode(cli_runner):
    result = invoke_cli(cli_runner, ["compile-cache", "clear"], json_mode=True)

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-clear"
    assert data["summary"]["verdict"] == "pass --all or --stale to specify which rows to drop"
    assert data["summary"]["partial_success"] is True
    assert data["agent_contract"]["facts"] == ["0 dropped records", "cache selector failed"]


def test_compile_cache_clear_stale_no_head_emits_json_envelope(cli_runner, tmp_path):
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db_path = roam_dir / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE env_cache (key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, envelope_json TEXT, ts REAL)"
    )
    conn.commit()
    conn.close()

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "clear", "--root", str(tmp_path), "--stale"],
        json_mode=True,
    )

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-clear"
    assert data["summary"]["verdict"] == "cannot determine HEAD; refusing to clear"
    assert data["summary"]["partial_success"] is True


def test_compile_cache_stats_no_cache_has_concrete_agent_contract(cli_runner, tmp_path):
    result = invoke_cli(
        cli_runner,
        ["compile-cache", "stats", "--root", str(tmp_path)],
        json_mode=True,
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-stats"
    assert data["agent_contract"]["facts"] == ["0 cache records", "cache stats checked"]
    assert data["agent_contract"]["next_commands"] == ["roam compile-cache build"]


def test_compile_cache_build_resolves_root_for_compile_key(cli_runner, tmp_path, monkeypatch):
    """`--root .` must warm the same absolute-cwd key normal `roam compile` uses."""
    (tmp_path / ".roam").mkdir()
    corpus = tmp_path / "tasks.txt"
    corpus.write_text("what does src/roam/cli.py do\n", encoding="utf-8")
    monkeypatch.setenv("ROAM_AGENT_MODE", "outer_mode")
    seen: list[tuple[str, Path, str | None]] = []

    class _Plan:
        pass

    def fake_compile_plan(task, cwd=None):
        seen.append(("plan", Path(cwd), os.environ.get("ROAM_AGENT_MODE")))
        return _Plan()

    def fake_compile_for_artifact(plan, cwd=None):
        seen.append(("artifact", Path(cwd), os.environ.get("ROAM_AGENT_MODE")))
        return {}, "facts"

    from roam.plan import compiler as compiler_mod

    monkeypatch.setattr(compiler_mod, "compile_plan", fake_compile_plan)
    monkeypatch.setattr(compiler_mod, "compile_for_artifact", fake_compile_for_artifact)

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "build", "--root", ".", "--corpus", str(corpus)],
        cwd=tmp_path,
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    assert seen == [
        ("plan", tmp_path.resolve(), "compile_cache_build"),
        ("artifact", tmp_path.resolve(), "compile_cache_build"),
    ]
    assert os.environ.get("ROAM_AGENT_MODE") == "outer_mode"


def test_compile_cache_build_top_misses_warms_active_reconstructable_tasks(cli_runner, tmp_path, monkeypatch):
    (tmp_path / ".roam").mkdir()
    long_prefix = "x" * 80
    entries = [
        {"task_hash": "long", "task_prefix": long_prefix, "cache_hit": False},
        {"task_hash": "long", "task_prefix": long_prefix, "cache_hit": False},
        {"task_hash": "first", "task_prefix": "what files are coupled to `compile_plan`", "cache_hit": False},
        {"task_hash": "first", "task_prefix": "what files are coupled to `compile_plan`", "cache_hit": False},
        {"task_hash": "stale", "task_prefix": "now cached", "cache_hit": False},
        {"task_hash": "stale", "task_prefix": "now cached", "cache_hit": True},
        {"task_hash": "warmer", "task_prefix": "builder only", "cache_hit": False, "agent_mode": "compile_cache_build"},
        {"task_hash": "second", "task_prefix": "what's the blast radius of `compile_plan`", "cache_hit": False},
    ]
    _write_private_compile_telemetry(tmp_path, "\n".join(json.dumps(e) for e in entries))
    seen: list[tuple[str, Path, str | None]] = []

    class _Plan:
        pass

    def fake_compile_plan(task, cwd=None):
        seen.append((task, Path(cwd), os.environ.get("ROAM_AGENT_MODE")))
        return _Plan()

    def fake_compile_for_artifact(plan, cwd=None):
        return {}, "facts"

    from roam.commands import cmd_compile_cache as cache_mod
    from roam.plan import compiler as compiler_mod

    monkeypatch.setattr(cache_mod, "_task_has_cached_envelope", lambda task, root: False)
    monkeypatch.setattr(compiler_mod, "compile_plan", fake_compile_plan)
    monkeypatch.setattr(compiler_mod, "compile_for_artifact", fake_compile_for_artifact)

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "build", "--root", str(tmp_path), "--top-misses", "--miss-limit", "2"],
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-build"
    assert data["summary"]["built"] == 2
    assert data["summary"]["top_miss_tasks_added"] == 2
    assert data["summary"]["truncated_prefixes_skipped"] == 1
    assert data["summary"]["corpus"] == "(--top-misses)"
    assert [item[0] for item in seen] == [
        "what files are coupled to `compile_plan`",
        "what's the blast radius of `compile_plan`",
    ]
    assert all(item[1] == tmp_path.resolve() for item in seen)
    assert all(item[2] == "compile_cache_build" for item in seen)


def test_compile_cache_build_top_misses_skips_already_cached_tasks(cli_runner, tmp_path, monkeypatch):
    (tmp_path / ".roam").mkdir()
    entries = [
        {"task_hash": "cached", "task_prefix": "what files are coupled to `compile_plan`", "cache_hit": False},
        {"task_hash": "cached", "task_prefix": "what files are coupled to `compile_plan`", "cache_hit": False},
        {"task_hash": "next", "task_prefix": "what's the blast radius of `compile_plan`", "cache_hit": False},
    ]
    _write_private_compile_telemetry(tmp_path, "\n".join(json.dumps(e) for e in entries))
    seen: list[str] = []

    class _Plan:
        pass

    def fake_compile_plan(task, cwd=None):
        seen.append(task)
        return _Plan()

    def fake_compile_for_artifact(plan, cwd=None):
        return {}, "facts"

    from roam.commands import cmd_compile_cache as cache_mod
    from roam.plan import compiler as compiler_mod

    monkeypatch.setattr(
        cache_mod,
        "_task_has_cached_envelope",
        lambda task, root: task == "what files are coupled to `compile_plan`",
    )
    monkeypatch.setattr(compiler_mod, "compile_plan", fake_compile_plan)
    monkeypatch.setattr(compiler_mod, "compile_for_artifact", fake_compile_for_artifact)

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "build", "--root", str(tmp_path), "--top-misses", "--miss-limit", "1"],
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["built"] == 1
    assert data["summary"]["already_cached_skipped"] == 1
    assert seen == ["what's the blast radius of `compile_plan`"]


def test_compile_cache_build_top_misses_empty_emits_json_envelope(cli_runner, tmp_path):
    (tmp_path / ".roam").mkdir()
    _write_private_compile_telemetry(tmp_path, "")

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "build", "--root", str(tmp_path), "--top-misses"],
        json_mode=True,
    )

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-build"
    assert data["summary"]["verdict"] == "empty corpus and no warmable telemetry tasks"
    assert data["summary"]["partial_success"] is True
    assert data["agent_contract"]["facts"] == [
        "0 warmed records",
        "0 skipped records",
        "0 telemetry records",
    ]


def test_compile_cache_build_discloses_degraded_top_miss_telemetry(
    cli_runner,
    tmp_path,
    monkeypatch,
):
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    _write_private_compile_telemetry(
        tmp_path,
        '{"task_hash":"one","task_hash":"two","task_prefix":"ambiguous"}\n',
    )
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("inspect the compile cache\n", encoding="utf-8")

    class _Plan:
        pass

    from roam.plan import compiler as compiler_mod

    monkeypatch.setattr(compiler_mod, "compile_plan", lambda task, cwd=None: _Plan())
    monkeypatch.setattr(compiler_mod, "compile_for_artifact", lambda plan, cwd=None: ({}, "facts"))

    result = invoke_cli(
        cli_runner,
        [
            "compile-cache",
            "build",
            "--root",
            str(tmp_path),
            "--corpus",
            str(corpus),
            "--top-misses",
        ],
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["built"] == 1
    assert summary["telemetry_read_state"] == "partial_invalid_rows"
    assert summary["invalid_telemetry_rows"] == 1
    assert summary["partial_success"] is True
    assert "telemetry_partial_invalid_rows" in summary["verdict"]


def test_compile_cache_corpus_reader_enforces_byte_budget(tmp_path, monkeypatch):
    from roam.commands import cmd_compile_cache as cache_mod

    corpus = tmp_path / "tasks.txt"
    corpus.write_text("first task\nsecond task\nthird task\n", encoding="utf-8")
    monkeypatch.setattr(cache_mod, "_MAX_CORPUS_BYTES", len(b"first task\n"))

    tasks, meta = cache_mod._read_corpus_tasks(str(corpus))

    assert tasks == ["first task"]
    assert meta["corpus_bytes_read"] == len(b"first task\n")
    assert meta["corpus_truncated"] is True


def test_compile_cache_all_files_enumeration_caps_paths_and_discloses_truncation(tmp_path, monkeypatch):
    from roam.commands import cmd_compile_cache as cache_mod

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py", "b.py"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(cache_mod, "_MAX_ALL_FILE_PATHS", 1)

    tasks, meta = cache_mod._all_file_tasks(str(tmp_path))

    assert len(tasks) == 3
    assert all("a.py" in task for task in tasks)
    assert meta["all_files_paths_read"] == 1
    assert meta["all_files_read_state"] == "truncated"
    assert meta["all_files_truncated"] is True


def test_compile_cache_evict_no_cache_emits_json_envelope(cli_runner, tmp_path):
    result = invoke_cli(
        cli_runner,
        ["compile-cache", "evict", "--root", str(tmp_path)],
        json_mode=True,
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-evict"
    assert data["summary"]["verdict"] == "no cache to evict"
    assert data["summary"]["rows_evicted"] == 0
    assert data["agent_contract"]["facts"] == ["0 evicted records", "cache evict passed"]


def test_compile_cache_vanilla_stats_no_cache_has_concrete_agent_contract(cli_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    result = invoke_cli(cli_runner, ["compile-cache", "vanilla-stats"], json_mode=True)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-vanilla-stats"
    assert data["agent_contract"]["facts"] == ["0 vanilla records", "vanilla cache checked"]
    assert data["agent_contract"]["next_commands"] == ["roam bench-compile"]


def test_compile_cache_evict_no_changed_files_emits_json_envelope(cli_runner, tmp_path):
    _git_init(tmp_path)
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db_path = roam_dir / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE env_cache ("
        "key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, "
        "envelope_json TEXT, ts REAL, dep_mtimes_json TEXT)"
    )
    conn.commit()
    conn.close()

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "evict", "--root", str(tmp_path), "--diff", "HEAD"],
        json_mode=True,
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-evict"
    assert data["summary"]["files_changed"] == 0
    assert data["agent_contract"]["facts"] == ["0 evicted records", "0 changed files"]


def test_compile_cache_evict_git_failure_emits_json_envelope(cli_runner, tmp_path):
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    db_path = roam_dir / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE env_cache ("
        "key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, "
        "envelope_json TEXT, ts REAL, dep_mtimes_json TEXT)"
    )
    conn.commit()
    conn.close()

    result = invoke_cli(
        cli_runner,
        ["compile-cache", "evict", "--root", str(tmp_path)],
        json_mode=True,
    )

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["command"] == "compile-cache-evict"
    assert data["summary"]["partial_success"] is True
    assert data["agent_contract"]["facts"] == ["0 evicted records", "git diff failed"]
