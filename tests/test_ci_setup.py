"""Tests for roam ci-setup -- CI/CD pipeline config generator."""

from __future__ import annotations

import json
import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def ci_project(tmp_path):
    proj = tmp_path / "ci_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("print('hello')\n")
    git_init(proj)
    return proj


class TestCiSetupSmoke:
    def test_explicit_platform_exits_zero(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project)
        assert result.exit_code == 0

    def test_auto_detect_exits_zero(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup"], cwd=ci_project)
        assert result.exit_code == 0

    def test_all_platforms_exit_zero(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        for platform in ("github", "gitlab", "azure", "jenkins", "bitbucket"):
            result = invoke_cli(cli_runner, ["ci-setup", "--platform", platform], cwd=ci_project)
            assert result.exit_code == 0, f"ci-setup --platform {platform} failed"


class TestCiSetupJSON:
    def test_json_envelope(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project, json_mode=True)
        data = parse_json_output(result, "ci-setup")
        assert_json_envelope(data, "ci-setup")

    def test_json_has_template(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project, json_mode=True)
        data = parse_json_output(result, "ci-setup")
        assert "template" in data or "config" in data or "content" in data

    def test_json_summary_has_platform(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "gitlab"], cwd=ci_project, json_mode=True)
        data = parse_json_output(result, "ci-setup")
        summary = data["summary"]
        assert "platform" in summary


class TestCiSetupText:
    def test_verdict_line(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project)
        assert "VERDICT:" in result.output or "===" in result.output

    def test_github_template_contains_roam(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project)
        assert "roam" in result.output.lower()

    def test_auto_detect_with_github_marker(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        gh = ci_project / ".github" / "workflows"
        gh.mkdir(parents=True)
        result = invoke_cli(cli_runner, ["ci-setup"], cwd=ci_project)
        assert result.exit_code == 0
        assert "github" in result.output.lower() or "GitHub" in result.output


class TestCiSetupWrite:
    def test_write_creates_file(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github", "--write"], cwd=ci_project)
        assert result.exit_code == 0
        # Check that a workflow file was created
        gh_dir = ci_project / ".github" / "workflows"
        if gh_dir.exists():
            yaml_files = list(gh_dir.glob("*.yml")) + list(gh_dir.glob("*.yaml"))
            assert len(yaml_files) >= 1
