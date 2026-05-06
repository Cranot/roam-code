"""Tests for ``roam dogfood`` — one-shot v2 stack runner."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process, invoke_cli  # noqa: E402


@pytest.fixture
def tiny_indexed(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def add(a, b):\n    return a + b\n")
    git_init(proj)
    git_commit(proj, "initial")
    monkeypatch.chdir(proj)
    index_in_process(proj)
    return proj


def _last_json(text: str) -> dict:
    """Pull the last JSON object out of mixed stdout."""
    idx = text.rfind("\n{\n")
    if idx == -1:
        idx = text.find("{")
    return _json.loads(text[idx:])


def test_dogfood_help_lists_all_options(cli_runner):
    result = invoke_cli(cli_runner, ["dogfood", "--help"])
    assert "--audit" in result.output
    assert "--pr-analyze" in result.output
    assert "--audit-trail" in result.output
    assert "--rules" in result.output


def test_dogfood_text_output_includes_verdict_line(tiny_indexed, cli_runner):
    result = invoke_cli(cli_runner, ["dogfood"])
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    # Should include a "Drill in" hint section
    assert "Drill in:" in result.output


def test_dogfood_json_envelope_has_summary_and_sections(tiny_indexed, cli_runner):
    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    env = _last_json(result.output)
    assert "summary" in env
    assert "sections" in env
    assert isinstance(env["summary"]["sections_run"], list)
    assert "audit" in env["summary"]["sections_run"]
    assert "pr_analyze" in env["summary"]["sections_run"]


def test_dogfood_no_audit_skips_section(tiny_indexed, cli_runner):
    result = invoke_cli(cli_runner, ["dogfood", "--no-audit"], json_mode=True)
    env = _last_json(result.output)
    assert "audit" not in env["summary"]["sections_run"]
    assert "pr_analyze" in env["summary"]["sections_run"]


def test_dogfood_no_pr_analyze_skips_section(tiny_indexed, cli_runner):
    result = invoke_cli(cli_runner, ["dogfood", "--no-pr-analyze"], json_mode=True)
    env = _last_json(result.output)
    assert "pr_analyze" not in env["summary"]["sections_run"]
    assert "audit" in env["summary"]["sections_run"]


def test_dogfood_no_audit_trail_skips_conformance(tiny_indexed, cli_runner):
    result = invoke_cli(cli_runner, ["dogfood", "--no-audit-trail"], json_mode=True)
    env = _last_json(result.output)
    # When audit-trail is off, conformance section shouldn't run either.
    assert "conformance" not in env["summary"]["sections_run"]


def test_dogfood_summary_includes_health_and_pr_verdict(tiny_indexed, cli_runner):
    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    env = _last_json(result.output)
    summary = env["summary"]
    # On a tiny indexed project, all keys should be present (None or value).
    assert "health_score" in summary
    assert "pr_verdict" in summary
    assert "conformance_score" in summary
    assert "git_sha" in summary
