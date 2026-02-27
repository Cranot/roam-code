"""Tests for roam report -- compound report presets."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# Built-in preset names from cmd_report.py
_BUILTIN_PRESETS = ["first-contact", "security", "pre-pr", "refactor", "guardian"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def report_project(tmp_path):
    """Minimal indexed Python project suitable for running report presets."""
    proj = tmp_path / "report_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    (src / "auth.py").write_text(
        "def authenticate(user, password):\n"
        '    """Authenticate user."""\n'
        "    if not user or not password:\n"
        "        return None\n"
        "    return user\n"
        "\n"
        "\n"
        "def hash_password(pw):\n"
        '    """Return hashed password."""\n'
        "    return pw[::-1]\n"
    )

    (src / "db.py").write_text(
        "def connect(dsn):\n"
        '    """Return DB connection."""\n'
        "    return dsn\n"
        "\n"
        "\n"
        "def query(conn, sql):\n"
        '    """Execute SQL."""\n'
        "    return []\n"
    )

    (src / "api.py").write_text(
        "from src.auth import authenticate\n"
        "from src.db import connect, query\n"
        "\n"
        "\n"
        "def handle_login(user, pw, dsn):\n"
        '    """Handle login request."""\n'
        "    token = authenticate(user, pw)\n"
        "    conn = connect(dsn)\n"
        '    return {"token": token, "db": conn}\n'
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestReportSmoke:
    def test_list_presets_exits_zero(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project)
        assert result.exit_code == 0

    def test_first_contact_preset_exits_zero(self, cli_runner, report_project, monkeypatch):
        """Running the 'first-contact' preset should succeed (may have failed sections
        but the command itself should exit 0 when --strict is not set)."""
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project)
        assert result.exit_code == 0

    def test_unknown_preset_exits_nonzero(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "nonexistent-preset-xyz"], cwd=report_project)
        assert result.exit_code != 0

    def test_no_preset_no_list_exits_nonzero(self, cli_runner, report_project, monkeypatch):
        """Calling 'report' with no preset and no --list should fail."""
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report"], cwd=report_project)
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestReportJSON:
    def test_list_json_envelope(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert_json_envelope(data, "report")

    def test_list_json_summary_has_verdict(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)

    def test_list_json_has_presets_dict(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert "presets" in data
        assert isinstance(data["presets"], dict)

    def test_list_json_includes_builtin_preset_names(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        presets = data.get("presets", {})
        for name in _BUILTIN_PRESETS:
            assert name in presets, f"Built-in preset '{name}' missing from JSON presets dict"

    def test_list_json_summary_has_count(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert "presets" in data["summary"]
        assert data["summary"]["presets"] == len(_BUILTIN_PRESETS)

    def test_run_json_envelope(self, cli_runner, report_project, monkeypatch):
        """Running a preset in JSON mode should produce a valid envelope."""
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert_json_envelope(data, "report")

    def test_run_json_summary_has_verdict(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert "verdict" in data["summary"]

    def test_run_json_summary_has_preset_field(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert "preset" in data["summary"]
        assert data["summary"]["preset"] == "first-contact"

    def test_run_json_has_sections_list(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        assert "sections" in data
        assert isinstance(data["sections"], list)


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestReportText:
    def test_list_shows_verdict_line(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project)
        assert "VERDICT:" in result.output

    def test_list_shows_all_builtin_preset_names(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project)
        for name in _BUILTIN_PRESETS:
            assert name in result.output, f"Built-in preset '{name}' not found in --list output"

    def test_list_shows_preset_descriptions(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "--list"], cwd=report_project)
        # At least the first-contact description or 'sections:' label should appear
        assert "sections" in result.output.lower() or "overview" in result.output.lower()

    def test_run_shows_verdict_line(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project)
        assert "VERDICT:" in result.output

    def test_run_shows_section_headers(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project)
        # first-contact has sections: Map, Health, Weather, Layers, Coupling
        assert any(section in result.output for section in ["Map", "Health", "Weather", "Layers", "Coupling"])

    def test_run_shows_preset_name_in_output(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "first-contact"], cwd=report_project)
        assert "first-contact" in result.output
