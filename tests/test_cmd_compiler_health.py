"""Tests for ``roam compiler-health`` — the daily compiler-quality dashboard.

Builds a synthetic project tree (no real index) and asserts:
  * all 4 envelope sections are present
  * the verdict line is well-formed
  * the score is in 0..100
  * the empty case sets ``state: "not_initialized"`` (not crash)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_compiler_health import (
    _build_alerts,
    _compute_score,
    _load_recent_telemetry,
    _section_env_drift,
    _section_per_mode_kpis,
    _section_routing,
    _section_self_magic,
    compiler_health,
)

# ----------------------------------------------------------------------
# Fixture helpers
# ----------------------------------------------------------------------


def _seed_telemetry(root: Path, rows: list[dict]) -> None:
    """Write rows as JSONL under ``.roam/compile-runs.jsonl``."""
    log_dir = root / ".roam"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "compile-runs.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _seed_baselines(root: Path, count: int) -> None:
    """Create ``count`` placeholder JSON baselines."""
    bdir = root / "internal" / "benchmarks" / "envelope-baselines"
    bdir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (bdir / f"task_{i}.json").write_text(json.dumps({"task": f"t{i}"}))


def _seed_compiler(root: Path, with_magic: bool = True) -> None:
    """Write a fake ``src/roam/plan/compiler.py`` with a known magic number."""
    cdir = root / "src" / "roam" / "plan"
    cdir.mkdir(parents=True, exist_ok=True)
    if with_magic:
        # Value 42 appears >=3 times to clear the threshold.
        body = (
            "from __future__ import annotations\n"
            "def f():\n"
            "    a = 42\n"
            "    b = 42\n"
            "    c = 42\n"
            "    d = 99\n"
            "    e = 99\n"
            "    f_ = 99\n"
            "    return a + b + c + d + e + f_\n"
        )
    else:
        body = "from __future__ import annotations\nx = 1\n"
    (cdir / "compiler.py").write_text(body)


def _full_fixture(tmp_path: Path) -> Path:
    """All 4 sources seeded."""
    rows = []
    for i in range(20):
        rows.append(
            {
                "ts": f"2026-06-02T00:00:{i:02d}Z",
                "procedure": "stack_trace_fix" if i % 2 == 0 else "trace_flow",
                "art_label": "l1_probe" if i % 3 != 0 else "fallback",
                "classifier_conf": 0.8,
                "envelope_bytes": 1500 + i * 10,
                "compile_ms": 200 + i * 5,
                "prefetched_keys": ["x", "y"],
                "agent_mode": "roam" if i % 2 == 0 else "vanilla",
                "cache_hit": (i % 5 == 0),
            }
        )
    _seed_telemetry(tmp_path, rows)
    _seed_baselines(tmp_path, 7)
    _seed_compiler(tmp_path, with_magic=True)
    return tmp_path


# ----------------------------------------------------------------------
# Section-helper unit tests
# ----------------------------------------------------------------------


def test_section_env_drift_no_baseline(tmp_path):
    sect = _section_env_drift(tmp_path)
    assert sect == {"state": "no_baseline"}


def test_section_env_drift_with_baseline(tmp_path):
    _seed_baselines(tmp_path, 3)
    sect = _section_env_drift(tmp_path)
    assert sect["state"] == "ok"
    assert sect["baseline_count"] == 3
    assert sect["drifted_count"] == 0


def test_section_routing_empty():
    assert _section_routing([])["state"] == "not_initialized"


def test_section_routing_populated():
    rows = [
        {"procedure": "stack_trace_fix"},
        {"procedure": "stack_trace_fix"},
        {"procedure": "trace_flow"},
    ]
    sect = _section_routing(rows)
    assert sect["state"] == "ok"
    assert sect["dominant_procedure"] == "stack_trace_fix"
    assert sect["distribution"]["stack_trace_fix"] == 2


def test_section_per_mode_kpis_empty():
    assert _section_per_mode_kpis([])["state"] == "not_initialized"


def test_section_per_mode_kpis_populated():
    rows = [
        {
            "procedure": "p",
            "art_label": "l1_probe",
            "compile_ms": 100,
            "agent_mode": "roam",
            "envelope_bytes": 500,
            "classifier_conf": 0.9,
            "task_hash": "a",
            "cache_hit": True,
        },
        {
            "procedure": "p",
            "art_label": "l1_probe",
            "compile_ms": 200,
            "agent_mode": "roam",
            "envelope_bytes": 500,
            "classifier_conf": 0.9,
            "task_hash": "a",
            "cache_hit": False,
        },
        {
            "procedure": "p",
            "art_label": "fallback",
            "compile_ms": 300,
            "agent_mode": "vanilla",
            "envelope_bytes": 500,
            "classifier_conf": 0.9,
            "task_hash": "b",
            "cache_hit": False,
        },
    ]
    sect = _section_per_mode_kpis(rows)
    assert sect["state"] == "ok"
    assert "roam" in sect["per_mode"]
    assert sect["per_mode"]["roam"]["n"] == 2
    assert sect["per_mode"]["roam"]["l1_pct"] == 100
    assert sect["per_mode"]["roam"]["cache_hit_pct"] == 50
    assert sect["per_mode"]["roam"]["repeat_task_pct"] == 100


def test_section_self_magic_missing_file(tmp_path):
    assert _section_self_magic(tmp_path)["state"] == "not_initialized"


def test_section_self_magic_finds_repeats(tmp_path):
    _seed_compiler(tmp_path, with_magic=True)
    sect = _section_self_magic(tmp_path, threshold=3)
    assert sect["state"] == "ok"
    # 42 appears 3 times, 99 appears 3 times — both clear threshold.
    assert sect["findings_count"] >= 2
    values = {f["value"] for f in sect["top"]}
    assert 42 in values
    assert 99 in values


def test_section_self_magic_only_degrades_expected_scan_errors(tmp_path, monkeypatch):
    _seed_compiler(tmp_path, with_magic=True)

    import roam.commands.cmd_magic_numbers as magic_numbers

    def raise_syntax_error(*args, **kwargs):
        raise SyntaxError("synthetic parse failure")

    monkeypatch.setattr(magic_numbers, "_scan_file", raise_syntax_error)
    assert _section_self_magic(tmp_path)["state"] == "not_initialized"

    def raise_runtime_error(*args, **kwargs):
        raise RuntimeError("synthetic scanner bug")

    monkeypatch.setattr(magic_numbers, "_scan_file", raise_runtime_error)
    with pytest.raises(RuntimeError, match="synthetic scanner bug"):
        _section_self_magic(tmp_path)


# ----------------------------------------------------------------------
# Score + alerts unit tests
# ----------------------------------------------------------------------


def test_compute_score_all_dimensions():
    env = {"state": "ok", "drifted_count": 0, "baseline_count": 5}
    pm = {"state": "ok", "l1_probe_pct": 80, "median_compile_ms": 400}
    sm = {"state": "ok", "findings_count": 2}
    score, contribs = _compute_score(env, pm, sm)
    assert 0 <= score <= 100
    assert "l1_fire_rate" in contribs
    assert "latency_budget" in contribs
    assert "drift_clean" in contribs
    assert "magic_debt" in contribs


def test_compute_score_handles_empty_sections():
    score, contribs = _compute_score(
        {"state": "no_baseline"},
        {"state": "not_initialized"},
        {"state": "not_initialized"},
    )
    # No active weights -> 0, no crash.
    assert score == 0
    assert contribs == {}


def test_compute_score_prefers_eligible_l1_rate():
    score, contribs = _compute_score(
        {"state": "ok", "drifted_count": 0, "baseline_count": 5},
        {
            "state": "ok",
            "l1_probe_pct": 20,
            "l1_eligible_count": 10,
            "l1_eligible_probe_pct": 90,
            "median_compile_ms": 400,
        },
        {"state": "ok", "findings_count": 0},
    )
    assert score > 80
    assert contribs["l1_fire_rate"] == 36


def test_build_alerts_flags_low_l1():
    alerts = _build_alerts(
        {"state": "ok", "drifted_count": 0},
        {"state": "ok", "row_count": 10, "dominant_procedure": "p"},
        {"state": "ok", "l1_probe_pct": 30, "median_compile_ms": 100, "per_mode": {}},
        {"state": "ok", "findings_count": 0},
    )
    assert any("l1 fire rate" in a["message"] for a in alerts)


def test_build_alerts_ignores_broad_freeform_l1_mix():
    """Broad Codex prompts should not be treated as missed L1 probes."""
    rows = [
        {
            "procedure": "freeform_explore",
            "art_label": "facts",
            "compile_ms": 2,
            "agent_mode": "compile_codex",
            "classifier_conf": 0.35,
            "task_hash": f"free-{i}",
        }
        for i in range(12)
    ]
    per_mode = _section_per_mode_kpis(rows)
    alerts = _build_alerts(
        {"state": "ok", "drifted_count": 0},
        {"state": "ok", "row_count": 12, "dominant_procedure": "freeform_explore"},
        per_mode,
        {"state": "ok", "findings_count": 0},
    )
    assert not any("l1" in a["message"] for a in alerts)


def test_build_alerts_flags_eligible_l1_miss_by_mode():
    rows = [
        {
            "procedure": "structural_coupling",
            "art_label": "facts",
            "compile_ms": 10,
            "agent_mode": "compile_codex",
            "classifier_conf": 0.95,
            "task_hash": f"struct-{i}",
        }
        for i in range(6)
    ]
    per_mode = _section_per_mode_kpis(rows)
    alerts = _build_alerts(
        {"state": "ok", "drifted_count": 0},
        {"state": "ok", "row_count": 6, "dominant_procedure": "structural_coupling"},
        per_mode,
        {"state": "ok", "findings_count": 0},
    )
    messages = [a["message"] for a in alerts]
    assert any("l1 eligible probe rate" in msg for msg in messages)
    assert any("compile_codex l1 eligible probe rate" in msg for msg in messages)


def test_build_alerts_flags_repeated_cache_misses_by_mode():
    rows = [
        {
            "procedure": "freeform_explore",
            "art_label": "facts",
            "compile_ms": 5,
            "agent_mode": "compile_codex",
            "classifier_conf": 0.35,
            "task_hash": f"repeat-{i % 3}",
            "cache_hit": False,
        }
        for i in range(12)
    ]
    per_mode = _section_per_mode_kpis(rows)
    alerts = _build_alerts(
        {"state": "ok", "drifted_count": 0},
        {"state": "ok", "row_count": 12, "dominant_procedure": "freeform_explore"},
        per_mode,
        {"state": "ok", "findings_count": 0},
    )
    assert any("repeated-task cache hit rate" in a["message"] for a in alerts)


# ----------------------------------------------------------------------
# Telemetry-loader unit tests
# ----------------------------------------------------------------------


def test_load_recent_telemetry_missing_log(tmp_path):
    assert _load_recent_telemetry(tmp_path) == []


def test_load_recent_telemetry_tolerates_bad_lines(tmp_path):
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    (log_dir / "compile-runs.jsonl").write_text(
        json.dumps({"procedure": "p"}) + "\nNOT-JSON\n" + json.dumps({"procedure": "q"}) + "\n"
    )
    rows = _load_recent_telemetry(tmp_path)
    assert len(rows) == 2


def test_load_recent_telemetry_reads_bounded_tail(tmp_path):
    _seed_telemetry(tmp_path, [{"procedure": f"p{i}"} for i in range(10)])
    rows = _load_recent_telemetry(tmp_path, tail=3)
    assert [r["procedure"] for r in rows] == ["p7", "p8", "p9"]


# ----------------------------------------------------------------------
# End-to-end CLI tests
# ----------------------------------------------------------------------


def test_cli_full_fixture_json(tmp_path):
    """Smoke: all 4 sections present, verdict well-formed, score in range."""
    _full_fixture(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        compiler_health,
        ["--root", str(tmp_path)],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)

    # All 4 sections appear as top-level keys.
    assert "env_drift" in envelope
    assert "routing_distribution" in envelope
    assert "per_mode_kpis" in envelope
    assert "self_magic_numbers" in envelope
    assert "alerts" in envelope

    # Each populated section has state == "ok".
    assert envelope["env_drift"]["state"] == "ok"
    assert envelope["routing_distribution"]["state"] == "ok"
    assert envelope["per_mode_kpis"]["state"] == "ok"
    assert envelope["self_magic_numbers"]["state"] == "ok"

    # Verdict is well-formed.
    verdict = envelope["summary"]["verdict"]
    assert verdict.startswith("Compiler health:")
    assert "/100" in verdict
    assert "p50" in verdict
    assert "dominant" in verdict

    # Score in [0, 100].
    score = envelope["summary"]["score"]
    assert isinstance(score, int)
    assert 0 <= score <= 100

    # All sources populated -> partial_success False.
    assert envelope["summary"]["partial_success"] is False

    # agent_contract.facts present and non-empty.
    facts = envelope["agent_contract"]["facts"]
    assert isinstance(facts, list)
    assert len(facts) >= 4


def test_compiler_health_empty_project_emits_degraded_sections(tmp_path):
    """No telemetry, no baselines, no compiler.py -> sections degrade cleanly."""
    runner = CliRunner()
    result = runner.invoke(
        compiler_health,
        ["--root", str(tmp_path)],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)

    assert envelope["env_drift"]["state"] == "no_baseline"
    assert envelope["routing_distribution"]["state"] == "not_initialized"
    assert envelope["per_mode_kpis"]["state"] == "not_initialized"
    assert envelope["self_magic_numbers"]["state"] == "not_initialized"

    # partial_success must be True when any section is degraded.
    assert envelope["summary"]["partial_success"] is True

    # Score still produced (0 when no active weights).
    assert envelope["summary"]["score"] == 0

    # No crash + alerts surfaces the empty-telemetry state.
    msgs = " ".join(a["message"] for a in envelope["alerts"])
    assert "no compile telemetry" in msgs


def test_compiler_health_text_output_starts_with_verdict(tmp_path):
    """Text rendering puts ``VERDICT:`` as the first line."""
    _full_fixture(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        compiler_health,
        ["--root", str(tmp_path)],
        obj={"json": False},
    )
    assert result.exit_code == 0, result.output
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("VERDICT: Compiler health:")
