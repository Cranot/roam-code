"""#66 — tests for the new ``--schema`` flag on ``roam compile-stats``.

The flag prints the documented ``.roam/compile-runs.jsonl`` row-field
schema (field name + one-line meaning). It is static documentation: it
must work without any telemetry log present, and it must not perturb the
normal summary output when off."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import click.testing as _ctest
import pytest

from roam.commands.cmd_compile_stats import _read_telemetry, compile_stats
from roam.security.owner_only import ensure_owner_only_path

_EXPECTED_FIELDS = {
    "schema_version",
    "ts",
    "procedure",
    "classifier_conf",
    "art_label",
    "prefetched_keys",
    "prefetched_fact_count",
    "envelope_bytes",
    "compile_ms",
    "agent_mode",
    "episode_id",
    "task_fingerprint",
    "injection_advice",
    "probe_timings_ms",
    "cache_hit",
    "model_calls_avoided_count",
    "savings",
}

_REMOVED_IDENTIFYING_FIELDS = {
    "task_hash",
    "task_prefix",
    "session_id",
    "turn_seq",
    "compiler_fp",
}


def _write_jsonl(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a synthetic .roam/compile-runs.jsonl file."""
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    log = log_dir / "compile-runs.jsonl"
    with log.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    assert ensure_owner_only_path(log_dir)
    assert ensure_owner_only_path(log)
    return log


def test_schema_lists_documented_fields() -> None:
    """Text mode: every documented field name must appear in the output."""
    runner = _ctest.CliRunner()
    result = runner.invoke(compile_stats, ["--schema"], obj={"json": False})
    assert result.exit_code == 0, result.output
    for field in _EXPECTED_FIELDS:
        assert field in result.output, f"missing field {field!r} in --schema output"
    for field in _REMOVED_IDENTIFYING_FIELDS:
        assert field not in result.output


def test_schema_json_variant() -> None:
    """JSON mode: envelope carries every documented field with a non-empty meaning."""
    runner = _ctest.CliRunner()
    result = runner.invoke(compile_stats, ["--schema"], obj={"json": True})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "roam-envelope-v1"
    assert payload["summary"]["verdict"].startswith("Compile telemetry schema documents")
    assert payload["summary"]["field_count"] == len(_EXPECTED_FIELDS)
    assert payload["summary"]["partial_success"] is False
    fields = payload["row_schema"]["fields"]
    assert set(fields) == _EXPECTED_FIELDS
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


def test_malformed_json_field_types_degrade_without_crashing(tmp_path: Path) -> None:
    """Valid JSON with wrong field types must remain a structured partial result."""

    canary = "PROMPT-TYPE-CANARY-91"
    _write_jsonl(
        tmp_path,
        [
            {
                "ts": {"unexpected": "object"},
                "procedure": [canary],
                "classifier_conf": {"bad": 1},
                "art_label": ["facts"],
                "prefetched_keys": {"not": "a list"},
                "envelope_bytes": [1000],
                "compile_ms": {"bad": 10},
                "agent_mode": ["compile_cache_build"],
                "task_hash": [canary],
                "task_prefix": {"prompt": canary},
                "cache_hit": [False],
                "probe_timings_ms": {
                    "valid_probe": 1.5,
                    "bool_probe": True,
                    "bad_probe": [9],
                },
            }
        ],
    )
    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--by-procedure", "--by-mode", "--slow-probes", "--top-misses"],
        obj={"json": True},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["row_count"] == 1
    assert summary["top_cache_misses_state"] == "unavailable_repeat_identity"
    assert set(summary["probe_section_latency_ms"]) == {"other"}
    assert canary not in result.output


def test_non_finite_json_numbers_are_dropped_before_aggregation(tmp_path: Path) -> None:
    """Exponent overflow remains valid input but cannot poison any aggregate."""

    prompt_canaries = ("PROMPT-NONFINITE-CANARY-93", "PROMPT-NONFINITE-CANARY-94")
    overflow = "__JSON_EXPONENT_OVERFLOW__"
    huge_integer = 10**400
    common = {
        "ts": "2026-07-19T10:12:34Z",
        "task_hash": "legacy-repeat-hash",
        "procedure": "freeform_explore",
        "art_label": "facts",
        "prefetched_keys": [],
        "agent_mode": "compile_codex",
    }
    rows = [
        {
            **common,
            "task_prefix": prompt_canaries[0],
            "classifier_conf": overflow,
            "envelope_bytes": overflow,
            "compile_ms": overflow,
            "model_calls_avoided_count": huge_integer,
            "cache_hit": False,
            "probe_timings_ms": {
                "overflow_probe": overflow,
                "huge_integer_probe": huge_integer,
            },
        },
        {
            **common,
            "task_prefix": prompt_canaries[1],
            "classifier_conf": 0.75,
            "envelope_bytes": 2048,
            "compile_ms": 12.5,
            "cache_hit": True,
            "probe_timings_ms": {"safe_probe": 4.0},
        },
    ]
    log = _write_jsonl(tmp_path, rows)
    encoded_overflow = json.dumps(overflow)
    log.write_text(log.read_text(encoding="utf-8").replace(encoded_overflow, "1e309"), encoding="utf-8")

    sanitized_rows = _read_telemetry(str(tmp_path), retain_legacy_task_text=False)
    assert len(sanitized_rows) == 2
    assert "task_prefix" not in sanitized_rows[0]
    assert all(canary not in repr(sanitized_rows) for canary in prompt_canaries)

    runner = _ctest.CliRunner()
    result = runner.invoke(
        compile_stats,
        [
            "--root",
            str(tmp_path),
            "--by-procedure",
            "--by-mode",
            "--slow-probes",
            "--top-misses",
        ],
        obj={"json": True},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["row_count"] == 2
    assert summary["classifier_confidence"]["mean"] == 0.75
    assert summary["envelope_size_bytes"]["mean"] == 2048
    assert summary["compile_latency_ms"]["mean"] == 12.5
    assert set(summary["probe_section_latency_ms"]) == {"other"}
    assert summary["top_cache_misses_state"] == "legacy_redacted_rows"
    assert summary["top_cache_misses"] == [
        {
            "active_miss": False,
            "hit_count": 1,
            "identity_type": "legacy",
            "last_cache_hit": True,
            "miss_count": 1,
            "miss_rate_pct": 50,
            "task_ref": "legacy_task_001",
            "total_count": 2,
        }
    ]
    assert "Infinity" not in result.output
    assert "NaN" not in result.output
    assert "legacy-repeat-hash" not in result.output
    assert all(canary not in result.output for canary in prompt_canaries)


def test_reader_rejects_hardlinked_telemetry_without_touching_victim(tmp_path: Path) -> None:
    state = tmp_path / ".roam"
    state.mkdir()
    assert ensure_owner_only_path(state)
    victim = tmp_path / "victim.jsonl"
    original = b'{"ts":"2026-07-19T10:00:00Z","procedure":"freeform_explore"}\n'
    victim.write_bytes(original)
    try:
        os.link(victim, state / "compile-runs.jsonl")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    rows = _read_telemetry(str(tmp_path), retain_legacy_task_text=False)

    assert rows == []
    assert getattr(rows, "read_state") == "unsafe_log_path"
    assert victim.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit preservation assertion")
def test_reader_never_repairs_state_or_log_permissions(tmp_path: Path) -> None:
    state = tmp_path / ".roam"
    state.mkdir(mode=0o700)
    log = state / "compile-runs.jsonl"
    log.write_text('{"ts":"2026-07-19T10:00:00Z"}\n', encoding="utf-8")
    log.chmod(0o644)

    rows = _read_telemetry(str(tmp_path), retain_legacy_task_text=False)

    assert rows == []
    assert getattr(rows, "read_state") == "unsafe_log_path"
    assert stat.S_IMODE(log.stat().st_mode) == 0o644

    state.chmod(0o755)
    rows = _read_telemetry(str(tmp_path), retain_legacy_task_text=False)
    assert rows == []
    assert getattr(rows, "read_state") == "unsafe_state_directory"
    assert stat.S_IMODE(state.stat().st_mode) == 0o755


def test_reader_rejects_deep_bounded_json_without_recursion_error(tmp_path: Path) -> None:
    state = tmp_path / ".roam"
    state.mkdir()
    deep = '{"ts":"2026-07-19T10:00:00Z","nested":' + "[" * 200 + "0" + "]" * 200 + "}\n"
    log = state / "compile-runs.jsonl"
    log.write_text(deep, encoding="utf-8")
    assert ensure_owner_only_path(state)
    assert ensure_owner_only_path(log)

    rows = _read_telemetry(str(tmp_path), retain_legacy_task_text=False)

    assert rows == []
    assert getattr(rows, "read_state") == "partial_invalid_rows"
    assert getattr(rows, "invalid_rows") == 1


def test_compile_stats_rejects_duplicate_json_keys_and_discloses_partial_read(tmp_path: Path) -> None:
    state = tmp_path / ".roam"
    state.mkdir()
    (state / "compile-runs.jsonl").write_text(
        '{"ts":"2026-07-19T10:00:00Z","procedure":"freeform_explore",'
        '"procedure":"structural_coupling"}\n'
        '{"ts":"2026-07-19T10:00:00Z","procedure":"freeform_explore"}\n',
        encoding="utf-8",
    )
    assert ensure_owner_only_path(state)
    assert ensure_owner_only_path(state / "compile-runs.jsonl")

    result = _ctest.CliRunner().invoke(
        compile_stats,
        ["--root", str(tmp_path)],
        obj={"json": True},
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["row_count"] == 1
    assert summary["telemetry_read_state"] == "partial_invalid_rows"
    assert summary["invalid_telemetry_rows"] == 1
    assert summary["partial_success"] is True
    assert summary["verdict"].endswith("; telemetry read degraded: partial_invalid_rows")
