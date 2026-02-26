"""Tests for roam flag-dead -- feature flag staleness detection."""

from __future__ import annotations

import pytest

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
    (proj / "utils.py").write_text(
        "def format_name(first, last):\n"
        "    return f'{first} {last}'\n"
    )

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
