"""#66 — tests for the new ``--schema`` flag on ``roam compile-stats``.

The flag prints the documented ``.roam/compile-runs.jsonl`` row-field
schema (field name + one-line meaning). It is static documentation: it
must work without any telemetry log present, and it must not perturb the
normal summary output when off."""

from __future__ import annotations

import json
from pathlib import Path

import click.testing as _ctest

from roam.commands.cmd_compile_stats import compile_stats

_EXPECTED_FIELDS = {
    "ts",
    "task_hash",
    "task_prefix",
    "procedure",
    "classifier_conf",
    "art_label",
    "prefetched_keys",
    "envelope_bytes",
    "compile_ms",
    "agent_mode",
    "session_id",
    "turn_seq",
    "compiler_fp",
    "injection_advice",
    "probe_timings_ms",
    "cache_hit",
}


def _write_jsonl(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a synthetic .roam/compile-runs.jsonl file."""
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    log = log_dir / "compile-runs.jsonl"
    with log.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return log


def test_schema_lists_documented_fields() -> None:
    """Text mode: every documented field name must appear in the output."""
    runner = _ctest.CliRunner()
    result = runner.invoke(compile_stats, ["--schema"], obj={"json": False})
    assert result.exit_code == 0, result.output
    for field in _EXPECTED_FIELDS:
        assert field in result.output, f"missing field {field!r} in --schema output"


def test_schema_json_variant() -> None:
    """JSON mode: envelope carries every documented field with a non-empty meaning."""
    runner = _ctest.CliRunner()
    result = runner.invoke(compile_stats, ["--schema"], obj={"json": True})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    fields = payload["schema"]["fields"]
    assert _EXPECTED_FIELDS <= set(fields.keys())
    for field in _EXPECTED_FIELDS:
        meaning = fields[field]
        assert isinstance(meaning, str) and meaning.strip(), f"empty meaning for {field!r}"


def test_schema_works_without_telemetry_log(tmp_path: Path) -> None:
    """--schema is documentation: it must not depend on a telemetry log existing.

    Guards the short-circuit placement BEFORE ``_read_telemetry(root)``.
    """
    assert not (tmp_path / ".roam" / "compile-runs.jsonl").exists()
    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--schema"],
        obj={"json": False},
    )
    assert result.exit_code == 0, result.output
    for field in _EXPECTED_FIELDS:
        assert field in result.output


def test_schema_off_unaffects_normal_output(tmp_path: Path) -> None:
    """Without --schema, the normal summary renders and no schema listing leaks."""
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
    ]
    _write_jsonl(tmp_path, rows)
    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path)],
        obj={"json": False},
    )
    assert result.exit_code == 0, result.output
    assert "VERDICT" in result.output
    assert "total compile calls: 1" in result.output
    assert "row schema:" not in result.output
