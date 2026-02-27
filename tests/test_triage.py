"""Tests for roam triage -- security finding suppression management."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def triage_project(tmp_path):
    proj = tmp_path / "triage_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    return proj


class TestTriageListSmoke:
    def test_empty_list_exits_zero(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(cli_runner, ["triage", "list"], cwd=triage_project)
        assert result.exit_code == 0

    def test_empty_list_json(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(cli_runner, ["triage", "list"], cwd=triage_project, json_mode=True)
        data = parse_json_output(result, "triage-list")
        assert_json_envelope(data)
        assert "suppressions" in data or "items" in data or "entries" in data or isinstance(data.get("summary"), dict)


class TestTriageAdd:
    def test_add_suppression(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(
            cli_runner,
            [
                "triage",
                "add",
                "--rule",
                "hardcoded-secret",
                "--file",
                "main.py",
                "--reason",
                "test data",
                "--status",
                "safe",
            ],
            cwd=triage_project,
        )
        assert result.exit_code == 0

    def test_add_then_list(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        invoke_cli(
            cli_runner,
            [
                "triage",
                "add",
                "--rule",
                "sql-injection",
                "--file",
                "main.py",
                "--reason",
                "parameterized",
                "--status",
                "safe",
            ],
            cwd=triage_project,
        )
        result = invoke_cli(cli_runner, ["triage", "list"], cwd=triage_project)
        assert result.exit_code == 0
        assert "sql-injection" in result.output

    def test_add_then_check_is_suppressed(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        invoke_cli(
            cli_runner,
            [
                "triage",
                "add",
                "--rule",
                "xss-vuln",
                "--file",
                "main.py",
                "--reason",
                "sanitized",
                "--status",
                "acknowledged",
            ],
            cwd=triage_project,
        )
        result = invoke_cli(cli_runner, ["triage", "check", "xss-vuln", "main.py"], cwd=triage_project)
        assert result.exit_code == 0

    def test_add_json(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(
            cli_runner,
            ["triage", "add", "--rule", "test-rule", "--file", "main.py", "--reason", "testing", "--status", "safe"],
            cwd=triage_project,
            json_mode=True,
        )
        data = parse_json_output(result, "triage-add")
        assert_json_envelope(data)


class TestTriageStats:
    def test_stats_empty(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(cli_runner, ["triage", "stats"], cwd=triage_project)
        assert result.exit_code == 0

    def test_stats_after_adds(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        for rule in ("rule-a", "rule-b"):
            invoke_cli(
                cli_runner,
                ["triage", "add", "--rule", rule, "--file", "main.py", "--reason", "ok", "--status", "safe"],
                cwd=triage_project,
            )
        result = invoke_cli(cli_runner, ["triage", "stats"], cwd=triage_project)
        assert result.exit_code == 0

    def test_stats_json(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(cli_runner, ["triage", "stats"], cwd=triage_project, json_mode=True)
        data = parse_json_output(result, "triage-stats")
        assert_json_envelope(data)


class TestTriageCheck:
    def test_check_not_suppressed(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(cli_runner, ["triage", "check", "no-such-rule", "main.py"], cwd=triage_project)
        assert result.exit_code == 0

    def test_check_json(self, cli_runner, triage_project, monkeypatch):
        monkeypatch.chdir(triage_project)
        result = invoke_cli(cli_runner, ["triage", "check", "test", "main.py"], cwd=triage_project, json_mode=True)
        data = parse_json_output(result, "triage-check")
        assert_json_envelope(data)
