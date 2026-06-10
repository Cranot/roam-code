"""Tests for scripts/quality_dashboard.py — static 3-way telemetry dashboard."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

_SCRIPT = repo_root() / "scripts" / "quality_dashboard.py"


@pytest.fixture(scope="module")
def dashboard_mod():
    spec = importlib.util.spec_from_file_location("quality_dashboard", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["quality_dashboard"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture TSV builders
# ---------------------------------------------------------------------------


def _write_tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    lines = ["\t".join(header)]
    for r in rows:
        lines.append("\t".join(r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def bench_tsv(tmp_path: Path) -> Path:
    p = tmp_path / "ab-bench.tsv"
    today = dt.date.today().isoformat()
    _write_tsv(
        p,
        ["date", "project", "task", "variant", "calls", "seconds", "output_tokens", "roam_calls", "status"],
        [
            [today, "roam-code", "simple", "vanilla", "1", "10", "431", "0", "ok"],
            [today, "roam-code", "simple", "roam", "6", "102", "3558", "4", "ok"],
            [today, "roam-code", "simple", "compile", "3", "55", "1200", "1", "ok"],
            [today, "roam-code", "trace_flow", "vanilla", "19", "200", "5000", "0", "ok"],
            [today, "roam-code", "trace_flow", "compile", "5", "60", "1500", "2", "ok"],
        ],
    )
    return p


@pytest.fixture
def tool_calls_tsv(tmp_path: Path) -> Path:
    p = tmp_path / "all-tool-calls.tsv"
    today = dt.date.today().isoformat()
    _write_tsv(
        p,
        ["date", "time_utc", "session_id", "cwd", "tool_name", "input_chars", "output_chars", "is_error", "agent_mode"],
        [
            [today, "10:00:00", "sess_a", "roam-code", "Bash", "21", "45", "0", "compile"],
            [today, "10:01:00", "sess_b", "roam-code", "Read", "10", "200", "0", "roam"],
        ],
    )
    return p


@pytest.fixture
def mode_usage_tsv(tmp_path: Path) -> Path:
    p = tmp_path / "mode-usage.tsv"
    today = dt.date.today().isoformat()
    _write_tsv(
        p,
        ["date", "session_id", "agent_mode", "total_tokens", "cost_usd"],
        [
            [today, "sess_a", "compile", "1500", "0.0250"],
            [today, "sess_b", "roam", "3000", "0.0500"],
            [today, "sess_c", "vanilla", "8000", "0.1200"],
            [today, "sess_d", "compile", "2200", "0.0350"],
        ],
    )
    return p


@pytest.fixture
def regen_tsv(tmp_path: Path) -> Path:
    p = tmp_path / "regen-signal.tsv"
    today = dt.date.today().isoformat()
    _write_tsv(
        p,
        ["date", "session_id", "agent_mode", "regen_count"],
        [
            [today, "sess_a", "compile", "1"],
            [today, "sess_b", "roam", "3"],
            [today, "sess_c", "vanilla", "5"],
        ],
    )
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_html_all_present(dashboard_mod, bench_tsv, tool_calls_tsv, mode_usage_tsv, regen_tsv):
    out = dashboard_mod.build_html(
        bench_tsv=str(bench_tsv),
        tool_calls_tsv=str(tool_calls_tsv),
        mode_usage_tsv=str(mode_usage_tsv),
        regen_tsv=str(regen_tsv),
    )
    # Each section title must appear.
    assert "roam-code quality dashboard" in out
    assert "Per-mode last-7d summary" in out
    assert "Per-task variant comparison" in out
    assert "Mode usage Top" in out

    # Per-mode table contains rows for compile/roam/vanilla.
    assert ">compile<" in out
    assert ">roam<" in out
    assert ">vanilla<" in out

    # Per-task table contains task names.
    assert ">simple<" in out
    assert ">trace_flow<" in out

    # Footer references source paths.
    assert str(bench_tsv) in out
    assert str(mode_usage_tsv) in out

    # File-status header reports present.
    assert ">present<" in out
    assert ">MISSING<" not in out


def test_missing_files_graceful(dashboard_mod, tmp_path: Path):
    # Point everything at non-existent paths.
    missing = tmp_path / "does-not-exist.tsv"
    out = dashboard_mod.build_html(
        bench_tsv=str(missing),
        tool_calls_tsv=str(missing),
        mode_usage_tsv=str(missing),
        regen_tsv=str(missing),
    )
    # File status row marks MISSING.
    assert ">MISSING<" in out
    # The missing-data placeholder names the path.
    assert str(missing) in out
    assert "no data" in out
    # Sections still render their titles even with no data.
    assert "Per-mode last-7d summary" in out
    assert "Per-task variant comparison" in out


def test_partial_missing(dashboard_mod, bench_tsv, tmp_path: Path):
    missing = tmp_path / "absent.tsv"
    out = dashboard_mod.build_html(
        bench_tsv=str(bench_tsv),
        tool_calls_tsv=str(missing),
        mode_usage_tsv=str(missing),
        regen_tsv=str(missing),
    )
    # bench section populated.
    assert ">simple<" in out
    # mode-usage / regen section shows missing.
    assert "no data" in out
    assert str(missing) in out
    # File status mixed.
    assert ">present<" in out
    assert ">MISSING<" in out


def test_since_filter_excludes_old_rows(dashboard_mod, tmp_path: Path):
    p = tmp_path / "ab-bench.tsv"
    old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    fresh = dt.date.today().isoformat()
    header = ["date", "project", "task", "variant", "calls", "seconds", "output_tokens", "roam_calls", "status"]
    rows = [
        [old, "roam-code", "old_task", "compile", "9", "9", "9", "9", "ok"],
        [fresh, "roam-code", "fresh_task", "compile", "1", "1", "1", "1", "ok"],
    ]
    _write_tsv(p, header, rows)
    missing = tmp_path / "absent.tsv"
    out = dashboard_mod.build_html(
        bench_tsv=str(p),
        tool_calls_tsv=str(missing),
        mode_usage_tsv=str(missing),
        regen_tsv=str(missing),
        since=dt.date.today() - dt.timedelta(days=7),
    )
    assert ">fresh_task<" in out
    assert ">old_task<" not in out


def test_top_n_session_ranking(dashboard_mod, mode_usage_tsv, tmp_path: Path):
    missing = tmp_path / "absent.tsv"
    out = dashboard_mod.build_html(
        bench_tsv=str(missing),
        tool_calls_tsv=str(missing),
        mode_usage_tsv=str(mode_usage_tsv),
        regen_tsv=str(missing),
    )
    # Highest-token session sess_c (8000) should appear in TopN.
    assert ">sess_c<" in out
    assert ">sess_a<" in out


def test_main_writes_file(dashboard_mod, bench_tsv, tool_calls_tsv, mode_usage_tsv, regen_tsv, tmp_path: Path):
    out_path = tmp_path / "dash.html"
    rc = dashboard_mod.main(
        [
            "--out",
            str(out_path),
            "--bench-tsv",
            str(bench_tsv),
            "--tool-calls-tsv",
            str(tool_calls_tsv),
            "--mode-usage-tsv",
            str(mode_usage_tsv),
            "--regen-tsv",
            str(regen_tsv),
        ]
    )
    assert rc == 0
    assert out_path.exists()
    body = out_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in body
    assert "Per-mode last-7d summary" in body
