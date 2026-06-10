"""Tests for `roam bench-compile` — W24's A/B harness command.

The actual `claude -p` subprocess is monkey-patched so the tests run
without external network / API. Coverage:
  * missing-input error path
  * happy path with a single task + 1 condition + 1 run
  * aggregator handles all-failed cells without crashing
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def _fake_claude_p_output(prompt: str) -> dict:
    """Deterministic stand-in for `claude -p --output-format json`."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1234,
        "num_turns": 2,
        "total_cost_usd": 0.42,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 500,
        },
        "result": "stub answer for: " + prompt[:40],
    }


def test_bench_compile_requires_task_or_tasks_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "bench-compile"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "missing_input"


def test_bench_compile_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # Patch the subprocess call used by the harness to invoke claude -p.
    from roam.commands import cmd_bench

    def fake_run_claude_p(prompt, out_path, timeout_sec, model=None):
        out_path.write_text(json.dumps(_fake_claude_p_output(prompt)))
        return {"ok": True, "elapsed": 0.1}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", fake_run_claude_p)

    # Patch the compile-envelope generator so we don't shell out either.
    monkeypatch.setattr(cmd_bench, "_compile_envelope", lambda task, cwd: "FAKE COMPILE PLAN")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "Find files coupled to src/roam/cli.py",
            "--conditions",
            "vanilla,compile",
            "--runs",
            "2",
            "--workers",
            "2",
            "--out-dir",
            str(tmp_path / "cells"),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["cells"] == 4  # 1 task × 2 cond × 2 runs
    assert payload["summary"]["parsed_cells"] == 4
    # Per-condition stats populated.
    assert payload["per_condition"]["vanilla"]["n"] == 2
    assert payload["per_condition"]["compile"]["n"] == 2
    # Means from the fake output should be 2 turns, 1234ms, etc.
    assert payload["per_condition"]["vanilla"]["turns"]["mean"] == 2.0
    assert payload["per_condition"]["compile"]["cost_usd"]["mean"] == 0.42


def test_bench_compile_handles_all_failed_cells(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roam.commands import cmd_bench

    def fake_run_claude_p(prompt, out_path, timeout_sec, model=None):
        out_path.write_text(json.dumps({"type": "error", "reason": "stub"}))
        return {"error": "stub", "elapsed": 0.0}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", fake_run_claude_p)
    monkeypatch.setattr(cmd_bench, "_compile_envelope", lambda task, cwd: "")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "stub task",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--out-dir",
            str(tmp_path / "cells"),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["cells"] == 1
    assert payload["summary"]["parsed_cells"] == 0
    assert payload["summary"]["partial_success"] is True


def test_bench_compile_text_mode(tmp_path, monkeypatch):
    """Smoke-check the text-mode table renders without crashing."""
    monkeypatch.chdir(tmp_path)
    from roam.commands import cmd_bench

    def fake_run_claude_p(prompt, out_path, timeout_sec, model=None):
        out_path.write_text(json.dumps(_fake_claude_p_output(prompt)))
        return {"ok": True, "elapsed": 0.1}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", fake_run_claude_p)
    monkeypatch.setattr(cmd_bench, "_compile_envelope", lambda task, cwd: "FAKE")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench-compile",
            "stub task",
            "--conditions",
            "vanilla,static",
            "--runs",
            "1",
            "--out-dir",
            str(tmp_path / "cells"),
        ],
    )
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    assert "vanilla" in result.output
    assert "static" in result.output
