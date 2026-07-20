"""ruler-1: non-production compile rows must not skew the production KPIs."""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.commands.cmd_compile_stats import compile_stats
from roam.plan.agent_mode import (
    ENV_VAR,
    MODE_BENCH,
    MODE_HOOK,
    NON_PRODUCTION_MODES,
    agent_mode,
    is_non_production,
)
from roam.security.owner_only import ensure_owner_only_path


def test_agent_mode_context_sets_and_restores():
    os.environ.pop(ENV_VAR, None)
    with agent_mode(MODE_BENCH):
        assert os.environ[ENV_VAR] == MODE_BENCH
    assert ENV_VAR not in os.environ  # restored to absent


def test_agent_mode_context_restores_prior_value():
    os.environ[ENV_VAR] = "read_only"
    try:
        with agent_mode(MODE_BENCH):
            assert os.environ[ENV_VAR] == MODE_BENCH
        assert os.environ[ENV_VAR] == "read_only"  # prior preserved
    finally:
        os.environ.pop(ENV_VAR, None)


def test_non_production_classification():
    assert is_non_production({"agent_mode": MODE_BENCH})
    assert is_non_production({"agent_mode": "test"})
    assert is_non_production({"agent_mode": "compile_cache_build"})
    # production channels stay IN the KPIs
    assert not is_non_production({"agent_mode": MODE_HOOK})
    assert not is_non_production({"agent_mode": "read_only"})
    assert not is_non_production({"agent_mode": "unknown"})  # mixed bucket, kept
    assert not is_non_production({})  # missing -> unknown -> kept


def test_hook_is_production():
    assert MODE_HOOK not in NON_PRODUCTION_MODES


def _write_telemetry(root, rows):
    d = root / ".roam"
    d.mkdir(parents=True, exist_ok=True)
    log = d / "compile-runs.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert ensure_owner_only_path(d)
    assert ensure_owner_only_path(log)


def _row(mode, label="l1_probe", ms=100.0):
    return {
        "ts": "2026-07-16T00:00:00Z",
        "task_hash": f"h{hash(mode) % 1000}",
        "procedure": "freeform_explore",
        "art_label": label,
        "agent_mode": mode,
        "compile_ms": ms,
        "envelope_bytes": 1000,
    }


def test_stats_excludes_non_production_by_default(tmp_path):
    # 2 production l1 rows + 8 bench NON-l1 rows: production L1-rate must read
    # 100%, not 20%, because the bench rows are excluded from the KPI.
    rows = [_row("hook", "l1_probe"), _row("read_only", "l1_probe")]
    rows += [_row(MODE_BENCH, "full") for _ in range(8)]
    _write_telemetry(tmp_path, rows)
    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": True})
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["summary"]["row_count"] == 2  # only production rows
    assert env["summary"]["excluded_non_production_rows"] == 8


def test_stats_include_bench_keeps_all(tmp_path):
    rows = [_row("hook", "l1_probe")] + [_row(MODE_BENCH, "full") for _ in range(8)]
    _write_telemetry(tmp_path, rows)
    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path), "--include-bench"], obj={"json": True})
    env = json.loads(result.output)
    assert env["summary"]["row_count"] == 9
    assert "excluded_non_production_rows" not in env["summary"]


def test_all_non_production_discloses_in_human_output(tmp_path):
    # fresh-eyes edge: a repo whose telemetry is 100% non-production must NOT
    # print the misleading "no telemetry yet / no file" message — the file
    # exists, the rows were filtered. Disclose that in the human path too.
    _write_telemetry(tmp_path, [_row(MODE_BENCH, "full") for _ in range(5)])
    runner = CliRunner()
    human = runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": False})
    assert human.exit_code == 0
    assert "no production telemetry" in human.output
    assert "all non-production" in human.output
    assert "no .roam/compile-runs.jsonl" not in human.output  # the OLD wrong message
    # and the JSON path still carries the machine-readable excluded count
    js = json.loads(runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": True}).output)
    assert js["summary"]["excluded_non_production_rows"] == 5


def test_by_mode_shows_full_split_regardless(tmp_path):
    rows = [_row("hook", "l1_probe")] + [_row(MODE_BENCH, "full") for _ in range(8)]
    _write_telemetry(tmp_path, rows)
    runner = CliRunner()
    # even without --include-bench, --by-mode must show BOTH modes
    result = runner.invoke(compile_stats, ["--root", str(tmp_path), "--by-mode"], obj={"json": True})
    env = json.loads(result.output)
    assert set(env["summary"]["by_mode"]) == {"hook", MODE_BENCH}
    assert env["summary"]["by_mode"][MODE_BENCH]["n"] == 8
