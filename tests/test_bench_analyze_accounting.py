"""Saved benchmark artifacts retain failures and explicit metric denominators."""

from __future__ import annotations

import importlib.util
import json

import pytest

from tests._helpers.repo_root import repo_root

_SPEC = importlib.util.spec_from_file_location("bench_analyze_accounting", repo_root() / "scripts" / "bench_analyze.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def cell(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "data",
    [
        [],
        None,
        "not a result",
        {"subtype": "success", "is_error": True},
        {"subtype": "success", "result": []},
        {"subtype": "success", "usage": None},
    ],
)
def test_non_success_cell_is_retained_not_crashed_or_promoted(tmp_path, data):
    cell(tmp_path, "t0_compile_0.json", data)
    cells = bench._aggregate(str(tmp_path))["compile"]
    assert len(cells) == 1
    assert cells[0]["status"] != "success"


def test_invalid_json_remains_in_denominator(tmp_path):
    (tmp_path / "t0_compile_0.json").write_text("{truncated", encoding="utf-8")
    assert len(bench._aggregate(str(tmp_path))["compile"]) == 1


def test_permission_failure_remains_in_denominator(tmp_path, monkeypatch):
    cell(tmp_path, "t0_compile_0.json", {"subtype": "success"})
    monkeypatch.setattr(bench, "_load_cell", lambda path: (_ for _ in ()).throw(PermissionError("private detail")))
    rows = bench._aggregate(str(tmp_path))["compile"]
    assert len(rows) == 1 and rows[0]["status"] == "unreadable"
    assert "private detail" not in json.dumps(rows)


def test_only_explicit_timeout_is_a_timeout(tmp_path):
    path = cell(tmp_path, "t0_compile_0.json", {"type": "error", "reason": "authentication"})
    assert bench._load_cell(str(path))["status"] == "error"


def test_missing_metrics_are_unknown_not_free_work(tmp_path):
    path = cell(tmp_path, "t0_compile_0.json", {"subtype": "success"})
    row = bench._load_cell(str(path))
    assert row["status"] == "success"
    assert row["cost_usd"] is None
    assert row["duration_ms"] is None


@pytest.mark.parametrize("value", [True, -1, "12", float("nan"), float("inf")])
def test_invalid_metrics_are_unknown(tmp_path, value):
    path = cell(tmp_path, "t0_compile_0.json", {"subtype": "success", "total_cost_usd": value})
    assert bench._load_cell(str(path))["cost_usd"] is None


def test_report_does_not_claim_error_excluding_means_are_per_dispatched(tmp_path, capsys):
    cell(tmp_path, "t0_compile_0.json", {"subtype": "success", "duration_ms": 2000, "total_cost_usd": 1})
    cell(tmp_path, "t1_compile_0.json", {"type": "error", "reason": "timeout"})
    cell(tmp_path, "t2_compile_0.json", {"type": "error", "returncode": 1})
    bench.report(str(tmp_path), 90000)
    output = capsys.readouterr().out
    assert "per-DISPATCHED" not in output
    assert "unknown" in output.lower()
    assert "3" in output


def test_static_and_custom_conditions_are_reported(tmp_path, capsys):
    for condition in ("static", "custom_checklist"):
        cell(tmp_path, f"t0_{condition}_0.json", {"subtype": "success"})
    bench.report(str(tmp_path), 90000)
    output = capsys.readouterr().out
    assert "static" in output and "custom_checklist" in output


def test_summary_uses_observed_denominators_without_imputation(tmp_path):
    cell(tmp_path, "t0_compile_0.json", {"subtype": "success", "duration_ms": 2000, "total_cost_usd": 1})
    cell(tmp_path, "t1_compile_0.json", {"type": "error", "reason": "timeout"})
    cell(tmp_path, "t2_compile_0.json", {"type": "error", "duration_ms": 500, "total_cost_usd": 0})
    data = bench.summarize(str(tmp_path), 90000)
    row = data["conditions"]["compile"]
    assert row["counts"] == {"success": 1, "timeout": 1, "error": 1, "invalid": 0, "unreadable": 0}
    assert row["successful_result_rate"] == pytest.approx(1 / 3)
    assert row["all_observed_metrics"]["duration_ms"] == {"observed": 2, "unknown": 1, "total": 2500, "mean": 1250}
    assert row["all_observed_metrics"]["cost_usd"] == {"observed": 2, "unknown": 1, "total": 1, "mean": 0.5}
    assert row["timeout_wall_estimate_ms"] == 90000
    assert data["assigned_cases"] is data["dispatch_attempts"] is data["verified_task_successes"] is None


@pytest.mark.parametrize("args,expected", [(["--json"], 2), (["--timeout-cap", "0"], 2)])
def test_cli_empty_corpus_and_invalid_timeout_do_not_claim_success(tmp_path, args, expected):
    import subprocess
    import sys

    result = subprocess.run([sys.executable, _SPEC.origin, str(tmp_path), *args], capture_output=True, text=True)
    assert result.returncode == expected
    if "--json" in args:
        assert json.loads(result.stdout)[0]["conditions"] == {}


@pytest.mark.parametrize(
    "raw", ["{broken", "[]", '"text"', '{"subtype":"error_max_turns"}', '{"subtype":"success","is_error":true}']
)
def test_live_benchmark_parser_rejects_non_success_results(tmp_path, raw):
    from roam.commands.cmd_bench import _parse_cell

    path = tmp_path / "cell.json"
    path.write_text(raw, encoding="utf-8")
    assert _parse_cell(path) is None


def test_live_benchmark_preserves_missing_cell_in_assigned_count(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_bench

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cmd_bench, "_run_claude_p", lambda *args: {"error": "missing executable"})
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "Find a function",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--out-dir",
            str(tmp_path / "cells"),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["cells"] == summary["dispatched_cells"] == 1
    assert summary["parsed_cells"] == summary["reused_cells"] == 0
    assert summary["partial_success"] is True


@pytest.mark.parametrize("json_mode", [False, True])
def test_live_benchmark_unknown_metrics_render_without_crashing(tmp_path, monkeypatch, json_mode):
    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_bench

    monkeypatch.chdir(tmp_path)

    def run(prompt, out_path, *args):
        out_path.write_text(json.dumps({"type": "result", "subtype": "success", "result": "answer"}), encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", run)
    monkeypatch.setattr(cmd_bench, "_vanilla_cache_store", lambda *args: None)
    args = (["--json"] if json_mode else []) + [
        "bench-compile",
        "Find a function",
        "--conditions",
        "vanilla",
        "--runs",
        "1",
        "--out-dir",
        str(tmp_path / "cells"),
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    if json_mode:
        assert json.loads(result.output)["per_condition"]["vanilla"]["cost_usd"] is None
    else:
        assert "unknown" in result.output


def test_failed_live_dispatch_cannot_reuse_an_old_output(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_bench

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cells"
    output.mkdir()
    cell(output, "t0_vanilla_1.json", {"subtype": "success", "result": "old answer"})
    monkeypatch.setattr(cmd_bench, "_run_claude_p", lambda *args: {"error": "dispatch failed"})
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "Find a function",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--out-dir",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["parsed_cells"] == 0


@pytest.mark.parametrize("cached_success", [False, True])
def test_cached_result_accounting_is_not_a_new_dispatch(tmp_path, monkeypatch, cached_success):
    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_bench

    monkeypatch.chdir(tmp_path)
    cached = cell(tmp_path, "cached.json", {"subtype": "success" if cached_success else "error_max_turns"})
    monkeypatch.setattr(cmd_bench, "_vanilla_cache_lookup", lambda *args: str(cached))
    calls = []

    def run(prompt, out_path, *args):
        calls.append(prompt)
        out_path.write_text('{"subtype":"success"}', encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", run)
    monkeypatch.setattr(cmd_bench, "_vanilla_cache_store", lambda *args: None)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "Find a function",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--reuse-vanilla",
            "--out-dir",
            str(tmp_path / "cells"),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["cells"] == summary["parsed_cells"] == 1
    assert summary["dispatched_cells"] == len(calls) == int(not cached_success)
    assert summary["reused_cells"] == int(cached_success)


def test_cell_record_persistence_failure_is_disclosed(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_bench

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cells"
    output.mkdir()
    (output / "cells.tsv").mkdir()  # Portable, real write failure; no permission assumptions.

    def run(prompt, out_path, *args):
        out_path.write_text('{"subtype":"success"}', encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(cmd_bench, "_run_claude_p", run)
    monkeypatch.setattr(cmd_bench, "_vanilla_cache_store", lambda *args: None)
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "bench-compile",
            "Find a function",
            "--conditions",
            "vanilla",
            "--runs",
            "1",
            "--out-dir",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["parsed_cells"] == 1
    assert summary["cell_records_persisted"] is False
    assert summary["partial_success"] is True
    assert "persistence failed" in summary["verdict"]


def test_live_result_bytes_round_trip_as_utf8(tmp_path, monkeypatch):
    import subprocess

    from roam.commands import cmd_bench

    raw = json.dumps({"subtype": "success", "result": "caf\u00e9 \u03b4"}, ensure_ascii=False)

    def run(args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        return subprocess.CompletedProcess(args, 0, raw, "")

    monkeypatch.setattr(cmd_bench.subprocess, "run", run)
    path = tmp_path / "cell.json"
    assert cmd_bench._run_claude_p("test", path, 1)["ok"] is True
    assert path.read_bytes() == raw.encode("utf-8")
    assert cmd_bench._parse_cell(path)["result"] == "caf\u00e9 \u03b4"


def test_partial_usage_does_not_imply_zero_input_tokens(tmp_path):
    from roam.commands.cmd_bench import _parse_cell

    path = cell(tmp_path, "result.json", {"subtype": "success", "usage": {"output_tokens": 12}})
    parsed = _parse_cell(path)
    assert parsed["input_tokens"] is None
    assert parsed["output_tokens"] == 12
