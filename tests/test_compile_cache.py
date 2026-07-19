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
    (tmp_path / ".roam" / "compile-runs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries),
        encoding="utf-8",
    )
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
    (tmp_path / ".roam" / "compile-runs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries),
        encoding="utf-8",
    )
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
    (tmp_path / ".roam" / "compile-runs.jsonl").write_text("", encoding="utf-8")

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
