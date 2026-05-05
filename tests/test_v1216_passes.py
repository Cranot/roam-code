"""Tests for v12.16 passes 31-40."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def _isolated_project(tmp_path, monkeypatch):
    """Tiny indexed project so test-pyramid runs against known state.

    The CI failure mode (12.21 round) was: result.output empty when this
    test inherited a cwd left dirty by an earlier test. Isolating to a
    fresh tmp_path makes the test independent of suite ordering.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unit.py").write_text("def test_x():\n    assert 1\n")
    (tmp_path / "tests" / "test_integration.py").write_text("def test_y():\n    assert 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)
    from roam.index.indexer import Indexer

    Indexer().run(quiet=True)
    return tmp_path


def test_pass31_test_pyramid_runs(_isolated_project):
    """`roam test-pyramid` returns a verdict + per-kind counts."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "test-pyramid"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "test-pyramid"
    summary = payload["summary"]
    for k in ("verdict", "total", "unit", "integration", "e2e", "smoke", "unknown"):
        assert k in summary


def test_pass31_test_pyramid_verdict_categories():
    """Each verdict prefix maps to a documented state."""
    from roam.commands.cmd_test_pyramid import _verdict

    # No tests at all
    assert _verdict({"unit": 0, "integration": 0, "e2e": 0, "smoke": 0, "unknown": 0}) == ("no test files indexed")
    # All unknown
    out = _verdict({"unit": 0, "integration": 0, "e2e": 0, "smoke": 0, "unknown": 50})
    assert out.startswith("UNSTRUCTURED")
    # Mostly unknown
    out = _verdict({"unit": 0, "integration": 0, "e2e": 1, "smoke": 0, "unknown": 50})
    assert out.startswith("MOSTLY-UNSTRUCTURED")
    # Inverted (e2e+integration > unit, unit > 0)
    out = _verdict({"unit": 5, "integration": 10, "e2e": 0, "smoke": 0, "unknown": 0})
    assert out.startswith("INVERTED")
    # OK
    out = _verdict({"unit": 50, "integration": 5, "e2e": 1, "smoke": 0, "unknown": 0})
    assert out.startswith("OK")


def test_pass32_dirty_files_field_in_index_status():
    """index_status() now exposes dirty_files (Pass 32)."""
    from roam.commands.resolve import index_status

    status = index_status()
    if status is None:
        return  # no git or no commits indexed; skip
    assert "dirty_files" in status


def test_pass33_roam_catalog_lists_registered_tools():
    """`roam_catalog` enumerates every registered MCP tool."""
    from roam.mcp_server import _TOOL_METADATA, roam_catalog

    out = roam_catalog()
    assert out["summary"]["tool_count"] == len(_TOOL_METADATA)
    if _TOOL_METADATA:
        first = out["tools"][0]
        for k in ("name", "title", "description", "core", "read_only", "destructive"):
            assert k in first


def test_pass34_health_explain_emits_breakdown(tmp_path, monkeypatch):
    """`roam health --explain` works and emits factor breakdown."""
    runner = CliRunner()
    # Health doesn't need a fresh index; running on the live repo is fine.
    monkeypatch.setenv("ROAM_NO_STALENESS_HINT", "1")
    result = runner.invoke(cli, ["health", "--explain"])
    assert result.exit_code in (0, 1, 5), result.output  # gates may flag
    # Either text or JSON, the breakdown header should appear in --explain text mode.
    assert "Score Breakdown" in result.output or "score_breakdown" in result.output


def test_pass35_doctor_runs_new_checks():
    """Doctor surfaces 13 checks now (Pass 35 added 2)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "doctor"])
    # exit 0 (all pass) or 1 (some fail) — not crash
    assert result.exit_code in (0, 1), result.output
    payload = json.loads(result.output)
    names = {c["name"] for c in payload.get("checks", [])}
    assert "Plugin discovery" in names
    assert "Required tables" in names


def test_pass36_env_inventory():
    """`roam config --env` enumerates ROAM_* env vars."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "config", "--env"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "config"
    assert payload["summary"]["count"] >= 5  # we read at least a handful
    names = {e["name"] for e in payload["env_vars"]}
    # Spot-check: a few well-known ROAM_* should always be present
    assert any(n in names for n in ("ROAM_DB_DIR", "ROAM_MCP_PRESET", "ROAM_PARALLEL_INDEX"))


def test_pass37_hotspots_danger_runs():
    """`roam hotspots --danger` produces a danger-zone list."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "hotspots", "--danger"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "hotspots"
    assert "danger_zone" in payload
    assert "thresholds" in payload


def test_pass38_index_stats_runs():
    """`roam index-stats` produces size, page, and table counts."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "index-stats"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "index-stats"
    summary = payload["summary"]
    assert "size_bytes" in summary
    assert "fragmentation_pct" in summary
    assert "table_counts" in payload
    assert payload["table_counts"]["files"] >= 0


def test_pass39_critique_batch(tmp_path):
    """`roam critique --batch <dir>` reviews multiple diffs."""
    # Make a non-diff file and a real diff
    (tmp_path / "empty.diff").write_text("")
    (tmp_path / "junk.patch").write_text("not a diff at all")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "critique", "--batch", str(tmp_path)])
    # Should not crash; non-diff files are reported as errors but loop continues
    assert result.exit_code in (0, 5), result.output
    payload = json.loads(result.output)
    assert payload["command"] == "critique"
    assert "diffs" in payload


def test_pass40_main_handles_keyboard_interrupt(monkeypatch):
    """__main__ wraps cli() to swallow KeyboardInterrupt with exit 130."""
    import io
    import runpy
    import sys

    def fake_cli(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr("roam.cli.cli", fake_cli)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    try:
        runpy.run_module("roam", run_name="__main__")
    except SystemExit as e:
        assert e.code == 130
    else:
        raise AssertionError("expected SystemExit")
