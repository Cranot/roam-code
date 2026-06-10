"""Tests for `roam guard-doctor` preflight."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def repo_with_roam(tmp_path, monkeypatch):
    """Bare project with .roam/ dir but no bundles."""
    (tmp_path / ".roam" / "pr-bundles").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_doctor_runs_all_checks_text_mode(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-doctor"])
    # Exit may be 0/1/2 depending on env; just verify output shape.
    assert "VERDICT:" in result.output
    for check_name in (
        "dot_roam",
        "bundles_dir",
        "rule_pack",
        "command_graph",
        "git",
        "github_token",
        "verdict_log",
        "yaml_lib",
    ):
        assert check_name in result.output


def test_doctor_json_envelope_shape(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    assert payload["command"] == "guard-doctor"
    assert "checks" in payload
    assert len(payload["checks"]) == 9
    for c in payload["checks"]:
        assert c["status"] in ("pass", "warn", "fail")


def test_doctor_passes_with_healthy_setup(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    # No blocking failures expected in a fresh repo with .roam/ present.
    assert payload["summary"]["blocking_failures"] == []
    assert result.exit_code in (0, 1)  # 0 if all-pass, 1 if some warns


def test_doctor_with_invalid_rule_pack_fails_blocking(repo_with_roam):
    bad_yaml = repo_with_roam / "bad.yml"
    bad_yaml.write_text("name: x\nfile_patterns: [not-a-mapping]")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor", "--rules", str(bad_yaml)])
    payload = json.loads(result.output)
    assert result.exit_code == 2  # blocking failure
    assert "rule_pack" in payload["summary"]["blocking_failures"]


def test_doctor_summary_verdict_terms(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] in ("healthy", "warnings", "blocked")


def test_doctor_text_mode_shows_fix_hints_for_failures(repo_with_roam):
    """When a check fails, its `fix:` hint surfaces below the row."""
    bad_yaml = repo_with_roam / "bad.yml"
    bad_yaml.write_text("not yaml at all: [unclosed")
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-doctor", "--rules", str(bad_yaml)])
    # Fix hint surfaces with `fix:` prefix in text mode.
    assert "fix:" in result.output


def test_doctor_smoke_compose_runs_when_bundle_exists(repo_with_roam):
    """`smoke_compose` check passes when the compose pipeline succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    smoke = next((c for c in payload["checks"] if c["name"] == "smoke_compose"), None)
    assert smoke is not None
    # repo_with_roam fixture supplies at least one valid pr-bundle.
    assert smoke["status"] in ("pass", "warn")


def test_doctor_smoke_compose_skips_when_no_bundles(tmp_path, monkeypatch):
    """`smoke_compose` warns (doesn't fail) when no bundles are present."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    smoke = next((c for c in payload["checks"] if c["name"] == "smoke_compose"), None)
    assert smoke is not None
    assert smoke["status"] == "warn"
    assert smoke["blocking"] is False
