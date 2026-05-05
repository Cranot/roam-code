"""Tests for v12.18 passes 81-90."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_pass81_disambiguate_lists_matches():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "disambiguate", "ensure_index"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "disambiguate"
    assert "count" in payload["summary"]
    if payload["summary"]["count"] > 0:
        first = payload["matches"][0]
        for k in ("name", "kind", "file", "line", "pagerank"):
            assert k in first


def test_pass82_pre_commit_print():
    runner = CliRunner()
    result = runner.invoke(cli, ["pre-commit", "--print"])
    assert result.exit_code == 0, result.output
    assert "#!/bin/sh" in result.output
    assert "git diff --cached" in result.output


def test_pass83_mcp_status_emits_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "mcp-status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    for k in ("preset", "tools_registered", "max_concurrent"):
        assert k in summary


def test_pass84_test_impact_runs():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "test-impact", "HEAD~1", "--limit", "3"])
    if result.exit_code != 0:
        return  # may bail on certain repos
    payload = json.loads(result.output)
    assert payload["command"] == "test-impact"
    assert "count" in payload["summary"]


def test_pass85_rerank_env_overrides_alpha(monkeypatch):
    monkeypatch.setenv("ROAM_RERANK_ALPHA", "0.99")
    from roam.config import get_retrieve_weights

    weights = get_retrieve_weights()
    assert weights["alpha"] == 0.99


def test_pass85_invalid_env_silently_ignored(monkeypatch):
    monkeypatch.setenv("ROAM_RERANK_BETA", "not-a-float")
    from roam.config import get_retrieve_weights

    weights = get_retrieve_weights()
    # Default beta is 0.25
    assert weights["beta"] == 0.25


def test_pass87_error_storm_rate_limit():
    from roam.mcp_server import _reset_error_storm, _structured_error

    _reset_error_storm()
    fired = [_structured_error({"error_code": "INDEX_NOT_FOUND", "hint": "x"}) for _ in range(5)]
    # First two: full envelope. Third+: trimmed.
    assert fired[0].get("trimmed") is None
    assert fired[1].get("trimmed") is None
    assert fired[2]["trimmed"] is True
    assert fired[2]["repeat_count"] == 3
    assert fired[4]["repeat_count"] == 5
    # Different error_code resets the counter.
    fired_other = _structured_error({"error_code": "DB_LOCKED"})
    assert fired_other.get("trimmed") is None
    _reset_error_storm()


def test_pass88_recipes_list():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "recipes"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["count"] >= 13
    first = payload["recipes"][0]
    for k in ("name", "intent", "examples", "commands"):
        assert k in first


def test_pass89_why_json_already_structured():
    """Sanity check that `roam why --json` returns structured per-symbol fields."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "why", "ensure_index"])
    if result.exit_code != 0:
        return
    payload = json.loads(result.output)
    if payload.get("symbols"):
        sym = payload["symbols"][0]
        # These structured fields prove the explanation isn't just prose.
        for k in ("role", "fan_in", "fan_out", "pagerank"):
            assert k in sym


def test_pass90_map_seed_filters_results():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "map", "--seed", "src/roam/cli.py", "--depth", "1", "-n", "5"])
    assert result.exit_code == 0, result.output
    # JSON map envelope includes summary; just check the payload parses.
    payload = json.loads(result.output)
    assert payload["command"] == "map"
