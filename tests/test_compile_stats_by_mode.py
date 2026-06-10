"""W5 (2026-06-02) — tests for the new ``--by-mode`` flag on
``roam compile-stats``.

The flag groups telemetry rows by ``agent_mode`` (compile/roam/vanilla/
unknown). Rows that pre-date the field count as ``unknown`` so the
summary is stable even without the telemetry-emit edit in place."""

from __future__ import annotations

import json
from pathlib import Path

import click.testing as _ctest

from roam.commands.cmd_compile_stats import compile_stats


def _write_jsonl(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a synthetic .roam/compile-runs.jsonl file."""
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    log = log_dir / "compile-runs.jsonl"
    with log.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return log


def test_by_mode_buckets_known_modes(tmp_path: Path) -> None:
    rows = [
        {
            "ts": "2026-06-02T00:00:00Z",
            "task_hash": "a",
            "task_prefix": "x",
            "procedure": "freeform_explore",
            "classifier_conf": 0.5,
            "art_label": "facts",
            "prefetched_keys": [],
            "envelope_bytes": 1000,
            "compile_ms": 100,
            "agent_mode": "compile",
            "cache_hit": False,
        },
        {
            "ts": "2026-06-02T00:00:01Z",
            "task_hash": "b",
            "task_prefix": "y",
            "procedure": "structural_coupling",
            "classifier_conf": 0.9,
            "art_label": "l1_probe",
            "prefetched_keys": ["k"],
            "envelope_bytes": 2000,
            "compile_ms": 200,
            "agent_mode": "compile",
            "cache_hit": True,
        },
        {
            "ts": "2026-06-02T00:00:02Z",
            "task_hash": "c",
            "task_prefix": "z",
            "procedure": "freeform_explore",
            "classifier_conf": 0.4,
            "art_label": "facts",
            "prefetched_keys": [],
            "envelope_bytes": 800,
            "compile_ms": 50,
            "agent_mode": "roam",
            "cache_hit": False,
        },
    ]
    _write_jsonl(tmp_path, rows)
    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--by-mode"],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_mode = payload["summary"]["by_mode"]
    assert set(by_mode.keys()) == {"compile", "roam"}
    assert by_mode["compile"]["n"] == 2
    assert by_mode["compile"]["l1_pct"] == 50  # 1 of 2 was l1_probe
    assert by_mode["compile"]["cache_hit_pct"] == 50  # 1 of 2 cache_hit
    assert by_mode["roam"]["n"] == 1


def test_by_mode_treats_missing_field_as_unknown(tmp_path: Path) -> None:
    """Pre-W5 telemetry rows lack ``agent_mode`` — they must bucket as
    ``unknown`` rather than silently dropping."""
    rows = [
        {
            "ts": "2026-06-02T00:00:00Z",
            "task_hash": "a",
            "task_prefix": "x",
            "procedure": "freeform_explore",
            "classifier_conf": 0.5,
            "art_label": "facts",
            "prefetched_keys": [],
            "envelope_bytes": 1000,
            "compile_ms": 100,
        },  # no agent_mode
    ]
    _write_jsonl(tmp_path, rows)
    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--by-mode"],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_mode = payload["summary"]["by_mode"]
    assert "unknown" in by_mode
    assert by_mode["unknown"]["n"] == 1


def test_by_mode_default_off_unaffects_existing_output(tmp_path: Path) -> None:
    """Without ``--by-mode``, the summary must not gain a by_mode key —
    back-compat for the cron consumers."""
    rows = [
        {
            "ts": "2026-06-02T00:00:00Z",
            "task_hash": "a",
            "task_prefix": "x",
            "procedure": "freeform_explore",
            "classifier_conf": 0.5,
            "art_label": "facts",
            "prefetched_keys": [],
            "envelope_bytes": 1000,
            "compile_ms": 100,
            "agent_mode": "compile",
        },
    ]
    _write_jsonl(tmp_path, rows)
    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path)],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "by_mode" not in payload["summary"]
