"""W40 polish — tests for:
C1 _PATH_RE URL bleed fix (negative lookahead `(?!//)`)
C2 _STACK_FRAME_GENERIC_RE adds .rs (Rust panics)
B1 structural_coupling backtick fallback
E1 roam compile-stats command end-to-end
"""

from __future__ import annotations

import json
import os

import pytest

# xdist: these tests compile against the MAIN repo (shared
# .roam/compile-envelope-cache.sqlite + live probe subprocesses), so they
# serialize on one worker. Surfaced on the first parallel CI run
# (2026-06-11): the blast probe returned empty under 4-worker contention.
pytestmark = pytest.mark.xdist_group("mainrepo_compile")

from roam.plan.compiler import (
    _extract_file_paths,
    _extract_stack_frames,
    _probe_coupling_backtick_for_task,
    compile_for_artifact,
    compile_plan,
)

# ---- C1 PATH_RE URL bleed ----


def test_w40_c1_url_does_not_extract():
    """URL-form paths must NOT enter named_paths (regression: bleed via the `:` boundary)."""
    assert _extract_file_paths("https://github.com/x/y/blob/main/src/foo.py") == []
    assert _extract_file_paths("see http://example.com/src/bar.py") == []


def test_w40_c1_real_path_still_extracted_after_url():
    """A real path in the same task should still extract."""
    out = _extract_file_paths(
        "github link: https://github.com/x/y/blob/main/src/foo.py — but the file is at src/local/real.py"
    )
    assert "src/local/real.py" in out
    assert "//github.com/x/y/blob/main/src/foo.py" not in out


# ---- C2 Rust panic stack frame ----


def test_w40_c2_rust_panic_frame_extracted():
    frames = _extract_stack_frames("thread 'main' panicked at 'index out of bounds', src/main.rs:42")
    assert ("src/main.rs", 42) in frames


def test_w40_c2_kotlin_swift_frames():
    frames = _extract_stack_frames("at MainActivity.kt:73")
    assert ("MainActivity.kt", 73) in frames
    frames = _extract_stack_frames("Crash in AppDelegate.swift:18 — fatal")
    # Swift extension supported; error word ("Crash"/"fatal") would be needed
    # for stack-trace classifier, but raw extraction should work
    assert ("AppDelegate.swift", 18) in frames


# ---- B1 coupling backtick fallback ----


def test_w40_b1_coupling_backtick_returns_none_without_backticks():
    out = _probe_coupling_backtick_for_task(
        "what files are coupled to the cli",
        cwd=None,
    )
    assert out is None


def test_w40_b1_coupling_backtick_resolves_real_symbol(monkeypatch):
    """When a backticked symbol resolves to a real file, embed coupling."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(os.path.join(repo, ".roam", "index.db")):
        pytest.skip("requires .roam/index.db in cwd")
    plan = compile_plan("what files are coupled to `compile_plan`")
    env, label = compile_for_artifact(plan, cwd=repo)
    pre = env["plan"].get("prefetched_facts", {})
    # Probe must have fired one of the coupling keys
    assert any(
        k in pre
        for k in (
            "structural_imports",
            "structural_imported_by_top",
            "temporal_coupling_pairs",
        )
    ), f"expected coupling keys, got: {sorted(pre.keys())}"
    assert label == "l1_probe"


# ---- E1 compile-stats CLI ----


def test_w40_e1_compile_stats_handles_empty_log(tmp_path):
    """No telemetry log → returns verdict without crashing."""
    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": False})
    assert result.exit_code == 0
    assert "no telemetry yet" in result.output


def test_w40_e1_compile_stats_parses_telemetry(tmp_path):
    """Synthetic .roam/compile-runs.jsonl is summarized correctly."""
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    log = log_dir / "compile-runs.jsonl"
    entries = [
        {
            "ts": "2026-05-31T00:00:00Z",
            "task_hash": "a",
            "task_prefix": "p1",
            "procedure": "structural_coupling",
            "classifier_conf": 0.85,
            "art_label": "l1_probe",
            "prefetched_keys": ["structural_imports"],
            "envelope_bytes": 4000,
            "compile_ms": 200.0,
        },
        {
            "ts": "2026-05-31T00:00:01Z",
            "task_hash": "b",
            "task_prefix": "p2",
            "procedure": "freeform_explore",
            "classifier_conf": 0.35,
            "art_label": "facts",
            "prefetched_keys": [],
            "envelope_bytes": 800,
            "compile_ms": 50.0,
        },
        {
            "ts": "2026-05-31T00:00:02Z",
            "task_hash": "c",
            "task_prefix": "p3",
            "procedure": "stack_trace_fix",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["stack_frames"],
            "envelope_bytes": 2000,
            "compile_ms": 100.0,
        },
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))

    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": False})
    assert result.exit_code == 0
    # L1 route: 2/3 = 66%
    assert "L1-route rate 66%" in result.output
    assert "stack_frames" in result.output  # appears in top probe keys
    assert "structural_coupling" in result.output


def test_w40_e1_compile_stats_json_mode(tmp_path):
    """JSON mode emits a valid envelope with our summary fields."""
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    (log_dir / "compile-runs.jsonl").write_text(
        json.dumps(
            {
                "ts": "x",
                "task_hash": "a",
                "task_prefix": "p",
                "procedure": "structural_dead",
                "classifier_conf": 0.85,
                "art_label": "full",
                "prefetched_keys": [],
                "envelope_bytes": 500,
                "compile_ms": 20.0,
            }
        )
        + "\n"
    )
    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path)],
        obj={"json": True},
    )
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["summary"]["row_count"] == 1
    assert env["summary"]["l1_probe_pct"] == 0


def test_w40_e1_compile_stats_tolerates_corrupted_lines(tmp_path):
    """Malformed JSON lines should be skipped, not crash."""
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    (log_dir / "compile-runs.jsonl").write_text(
        '{"ts": "x", "procedure": "x", "classifier_conf": 0.5, "art_label": "full", '
        '"prefetched_keys": [], "envelope_bytes": 100, "compile_ms": 10, "task_hash": "a", "task_prefix": "p"}\n'
        "{this is not valid json\n"
        '{"ts": "y", "procedure": "y", "classifier_conf": 0.6, "art_label": "l1_probe", '
        '"prefetched_keys": ["x"], "envelope_bytes": 200, "compile_ms": 20, "task_hash": "b", "task_prefix": "q"}\n'
    )
    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(compile_stats, ["--root", str(tmp_path)], obj={"json": False})
    assert result.exit_code == 0
    assert "L1-route rate 50%" in result.output  # 1/2 valid rows
