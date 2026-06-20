"""Tests for roam flag-dead -- feature flag staleness detection."""

from __future__ import annotations

import sqlite3

import pytest

from roam.commands import cmd_flag_dead
from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def flag_project(tmp_path):
    """Project with feature flag usage patterns."""
    proj = tmp_path / "flag_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # File with LaunchDarkly-style flag calls
    (proj / "features.py").write_text(
        "class FlagClient:\n"
        "    def variation(self, flag_name, default):\n"
        "        return default\n"
        "\n"
        "client = FlagClient()\n"
        "\n"
        "def show_new_dashboard():\n"
        "    if client.variation('new-dashboard', False):\n"
        "        return 'new'\n"
        "    return 'old'\n"
        "\n"
        "def use_beta_api():\n"
        "    if client.variation('beta-api', True):\n"
        "        return 'beta'\n"
        "    return 'stable'\n"
    )

    # File with env var flags
    (proj / "config.py").write_text(
        "import os\n"
        "\n"
        "FEATURE_DARK_MODE = os.getenv('FEATURE_DARK_MODE', 'false')\n"
        "FEATURE_V2_CHECKOUT = os.getenv('FEATURE_V2_CHECKOUT', 'true')\n"
    )

    # File with no flags
    (proj / "utils.py").write_text("def format_name(first, last):\n    return f'{first} {last}'\n")

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def no_flag_project(tmp_path):
    """Project with no feature flags."""
    proj = tmp_path / "noflag_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestFlagDeadSmoke:
    def test_exits_zero(self, cli_runner, flag_project, monkeypatch):
        monkeypatch.chdir(flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=flag_project)
        assert result.exit_code == 0

    def test_no_flags_exits_zero(self, cli_runner, no_flag_project, monkeypatch):
        monkeypatch.chdir(no_flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=no_flag_project)
        assert result.exit_code == 0

    def test_include_tests_flag(self, cli_runner, flag_project, monkeypatch):
        monkeypatch.chdir(flag_project)
        result = invoke_cli(cli_runner, ["flag-dead", "--include-tests"], cwd=flag_project)
        assert result.exit_code == 0

    def test_index_read_database_error_falls_back_to_filesystem(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        (project / "app.py").write_text(
            "def enabled(client):\n    return client.variation('stale-flag', False)\n",
            encoding="utf-8",
        )

        def fake_open_db(*_args, **_kwargs):
            raise sqlite3.DatabaseError("synthetic index read failure")

        monkeypatch.setattr(cmd_flag_dead, "open_db", fake_open_db)

        findings = cmd_flag_dead.scan_project_for_flags(project, use_index=True)

        assert [finding["flag_name"] for finding in findings] == ["stale-flag"]

    def test_index_read_unexpected_error_is_not_swallowed(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()

        def fake_open_db(*_args, **_kwargs):
            raise RuntimeError("synthetic programmer error")

        monkeypatch.setattr(cmd_flag_dead, "open_db", fake_open_db)

        with pytest.raises(RuntimeError, match="synthetic programmer error"):
            cmd_flag_dead.scan_project_for_flags(project, use_index=True)


class TestFlagDeadJSON:
    def test_json_envelope(self, cli_runner, flag_project, monkeypatch):
        monkeypatch.chdir(flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=flag_project, json_mode=True)
        data = parse_json_output(result, "flag-dead")
        assert_json_envelope(data, "flag-dead")

    def test_json_summary_has_verdict(self, cli_runner, flag_project, monkeypatch):
        monkeypatch.chdir(flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=flag_project, json_mode=True)
        data = parse_json_output(result, "flag-dead")
        assert "verdict" in data["summary"]

    def test_no_flags_json(self, cli_runner, no_flag_project, monkeypatch):
        monkeypatch.chdir(no_flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=no_flag_project, json_mode=True)
        data = parse_json_output(result, "flag-dead")
        assert_json_envelope(data, "flag-dead")


class TestFlagDeadText:
    def test_verdict_line(self, cli_runner, flag_project, monkeypatch):
        monkeypatch.chdir(flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=flag_project)
        assert "VERDICT:" in result.output

    def test_no_flags_verdict(self, cli_runner, no_flag_project, monkeypatch):
        monkeypatch.chdir(no_flag_project)
        result = invoke_cli(cli_runner, ["flag-dead"], cwd=no_flag_project)
        assert "VERDICT:" in result.output


class TestFlagDeadConfig:
    def test_config_file(self, cli_runner, flag_project, monkeypatch):
        monkeypatch.chdir(flag_project)
        config = flag_project / "stale-flags.txt"
        config.write_text("new-dashboard\n")
        result = invoke_cli(cli_runner, ["flag-dead", "--config", str(config)], cwd=flag_project)
        assert result.exit_code == 0
