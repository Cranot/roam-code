"""Tests for `roam guard-diff` verdict-delta command."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli
from tests.helpers import make_pr_bundle


def _bundle_blocking(tmp_path, name: str):
    """Build a bundle whose v1 verdict will be blocked (auth file, no tests)."""
    p = tmp_path / name
    p.write_text(
        json.dumps(
            make_pr_bundle(
                risks=[{"severity": "high", "paths": ["src/auth/x.py"], "description": "auth"}],
                tests_run=[],
            )
        )
    )
    return p


def _bundle_passing(tmp_path, name: str):
    """Build a bundle that produces a pass verdict (no affected, no risk)."""
    bundle = make_pr_bundle(intent="docs only")
    bundle["affected_symbols"] = []
    p = tmp_path / name
    p.write_text(json.dumps(bundle))
    return p


def test_guard_diff_two_explicit_bundles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _bundle_blocking(tmp_path, "a.json")
    b = _bundle_passing(tmp_path, "b.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", str(a), str(b)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    # blocked → pass is an improvement.
    assert payload["summary"]["direction"] == "improved"
    assert payload["diff"]["verdict"]["from"] in {"blocked", "pass_with_warnings", "needs_review", "pass"}
    assert payload["diff"]["verdict"]["to"] == "pass"


def test_guard_diff_unchanged_when_same_bundle(tmp_path):
    a = _bundle_passing(tmp_path, "a.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", str(a), str(a)])
    payload = json.loads(result.output)
    assert payload["summary"]["direction"] == "unchanged"


def test_guard_diff_text_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _bundle_blocking(tmp_path, "a.json")
    b = _bundle_passing(tmp_path, "b.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    # Improvement direction surfaces in text mode too.
    assert "improved" in result.output


def test_guard_diff_missing_args_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["error_code"] == "missing_required_field"


def test_guard_diff_bad_bundle_errors_gracefully(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", "/nonexistent/a.json", "/nonexistent/b.json"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["error_code"] == "bundle_load_failed"


def test_guard_diff_from_log_needs_two_entries(tmp_path, monkeypatch):
    """--from-log with no log → error."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", "--from-log"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["error_code"] == "missing_required_field"


def test_guard_diff_from_log_with_two_entries(tmp_path, monkeypatch):
    """--from-log with 2 entries → returns a diff."""
    from roam.guard_log import append_log_entry

    monkeypatch.chdir(tmp_path)
    for verdict in ("blocked", "pass"):
        append_log_entry(
            tmp_path,
            {
                "ts": f"2026-05-30T00:00:0{0 if verdict == 'blocked' else 1}Z",
                "branch": "main",
                "verdict": verdict,
                "changed_files": 1,
                "required": 1,
                "executed": 1 if verdict == "pass" else 0,
                "missing": 0 if verdict == "pass" else 1,
                "reasons": [],
            },
        )
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", "--from-log"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    # Older = blocked, newer = pass → improvement.
    assert payload["summary"]["from"] == "blocked"
    assert payload["summary"]["to"] == "pass"
    assert payload["summary"]["direction"] == "improved"


def test_guard_diff_by_file_annotates_status(tmp_path, monkeypatch):
    """--by-file emits per-file status (added/removed/shared) + reasons."""
    monkeypatch.chdir(tmp_path)
    a = _bundle_blocking(tmp_path, "a.json")
    b = _bundle_blocking(tmp_path, "b.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", "--by-file", str(a), str(b)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload["per_file"], list)
    # Each entry has the expected schema.
    for entry in payload["per_file"]:
        assert entry["status"] in {"added", "removed", "shared"}
        assert "file" in entry
        assert "reasons" in entry


def test_guard_diff_by_file_text_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _bundle_blocking(tmp_path, "a.json")
    b = _bundle_blocking(tmp_path, "b.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-diff", "--by-file", str(a), str(b)])
    assert result.exit_code == 0
    assert "By file" in result.output


def test_guard_diff_without_by_file_omits_per_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _bundle_blocking(tmp_path, "a.json")
    b = _bundle_blocking(tmp_path, "b.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", str(a), str(b)])
    payload = json.loads(result.output)
    assert payload["per_file"] is None


def test_guard_diff_detects_regression(tmp_path, monkeypatch):
    """When verdict moves to a more-severe value, direction = regressed."""
    monkeypatch.chdir(tmp_path)
    a = _bundle_passing(tmp_path, "good.json")
    b = _bundle_blocking(tmp_path, "bad.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-diff", str(a), str(b)])
    payload = json.loads(result.output)
    assert payload["summary"]["direction"] == "regressed"
    # Regression should surface in agent_contract.risks.
    risks = payload["agent_contract"]["risks"]
    assert any(r.get("code") == "regression" for r in risks)
