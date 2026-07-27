"""ruler-1: non-production compile rows must not skew the production KPIs."""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.commands.cmd_compile_stats import compile_stats
from roam.plan.agent_mode import (
    AGENT_MODE_COVERAGE_EPOCH,
    ENV_VAR,
    MODE_BENCH,
    MODE_HOOK,
    NON_PRODUCTION_MODES,
    agent_mode,
    is_non_production,
    unknown_cohort,
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


# ---------------------------------------------------------------------------
# unknown_cohort: refutes the "unknown means the field did not exist" theory
# and verifies a consumer cannot silently blend the two cohorts it mixes.
# ---------------------------------------------------------------------------


def test_unknown_cohort_returns_none_for_stamped_modes():
    assert unknown_cohort({"agent_mode": MODE_HOOK}) is None
    assert unknown_cohort({"agent_mode": MODE_BENCH}) is None


def test_unknown_cohort_pre_coverage():
    row = {"agent_mode": "unknown", "ts": "2026-07-08T03:00:00Z"}  # before the epoch
    assert unknown_cohort(row) == "unknown_pre_coverage"


def test_unknown_cohort_post_coverage():
    row = {"agent_mode": "unknown", "ts": "2026-07-20T00:00:00Z"}  # after the epoch
    assert unknown_cohort(row) == "unknown_post_coverage"


def test_unknown_cohort_boundary_is_inclusive_post():
    row = {"agent_mode": "unknown", "ts": AGENT_MODE_COVERAGE_EPOCH}
    assert unknown_cohort(row) == "unknown_post_coverage"


def test_unknown_cohort_missing_ts_defaults_to_pre_coverage():
    """A row with no parseable ts must not default into the current
    (post-coverage) cohort -- absence of provenance is treated as the wider,
    more conservative bucket, not silently folded into "now"."""
    assert unknown_cohort({"agent_mode": "unknown"}) == "unknown_pre_coverage"
    assert unknown_cohort({"agent_mode": "unknown", "ts": None}) == "unknown_pre_coverage"
    assert unknown_cohort({"agent_mode": None}) == "unknown_pre_coverage"
    assert unknown_cohort({}) == "unknown_pre_coverage"


def test_compile_stats_split_unknown_cohort_does_not_blend(tmp_path):
    """A consumer partitioning by the coverage marker must see two distinct
    sub-cohorts, never one merged 'unknown' number."""
    rows = [
        {**_row("unknown", "l1_probe"), "ts": "2026-07-08T00:00:00Z"},  # pre
        {**_row("unknown", "full"), "ts": "2026-07-08T01:00:00Z"},  # pre
        {**_row("unknown", "l1_probe"), "ts": "2026-07-20T00:00:00Z"},  # post
        _row(MODE_HOOK, "l1_probe"),
    ]
    _write_telemetry(tmp_path, rows)
    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path), "--split-unknown-cohort"], obj={"json": True})
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    split = env["summary"]["unknown_cohort_split"]
    # exactly the two coverage sub-cohorts -- never a bare "unknown" key that
    # would silently blend them back together.
    assert set(split) == {"unknown_pre_coverage", "unknown_post_coverage"}
    assert split["unknown_pre_coverage"]["n"] == 2
    assert split["unknown_post_coverage"]["n"] == 1
    # the hook row (stamped, non-unknown) must not leak into either cohort
    assert sum(v["n"] for v in split.values()) == 3


def test_compile_stats_split_unknown_cohort_off_by_default(tmp_path):
    rows = [{**_row("unknown", "l1_probe"), "ts": "2026-07-08T00:00:00Z"}]
    _write_telemetry(tmp_path, rows)
    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": True})
    env = json.loads(result.output)
    assert "unknown_cohort_split" not in env["summary"]
