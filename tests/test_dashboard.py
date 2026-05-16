"""Tests for `roam dashboard` -- unified single-screen codebase status.

Covers text output, JSON output, section presence, data ranges,
minimal projects, and graceful handling of missing data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


# W414: All dashboard tests in this file are read-only against the indexed
# project (text output, JSON output, section presence). Re-indexing on
# every test costs ~2s x 17. Override at module scope so the project is
# built and indexed once per worker. Mirrors W346's pattern in
# test_json_contracts.
@pytest.fixture(scope="module")
def indexed_project(tmp_path_factory):
    """Module-scoped indexed Python project for read-only dashboard tests."""
    import textwrap

    proj = tmp_path_factory.mktemp("dashboard_proj")
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()

    (src / "models.py").write_text(
        textwrap.dedent(
            '''\
            class User:
                """A user model."""
                def __init__(self, name, email):
                    self.name = name
                    self.email = email

                def display_name(self):
                    return self.name.title()

                def validate_email(self):
                    return "@" in self.email


            class Admin(User):
                """An admin user."""
                def __init__(self, name, email, role="admin"):
                    super().__init__(name, email)
                    self.role = role

                def promote(self, user):
                    pass
            '''
        ),
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        textwrap.dedent(
            '''\
            from models import User, Admin


            def create_user(name, email):
                """Create a new user."""
                user = User(name, email)
                if not user.validate_email():
                    raise ValueError("Invalid email")
                return user


            def get_display(user):
                """Get display name."""
                return user.display_name()


            def unused_helper():
                """This function is never called (dead code)."""
                return 42
            '''
        ),
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        textwrap.dedent(
            '''\
            def format_name(first, last):
                """Format a full name."""
                return f"{first} {last}"


            def parse_email(raw):
                """Parse an email address."""
                if "@" not in raw:
                    return None
                parts = raw.split("@")
                return {"user": parts[0], "domain": parts[1]}


            UNUSED_CONSTANT = "never_referenced"
            '''
        ),
        encoding="utf-8",
    )

    import os
    import subprocess

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )

    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, f"roam index failed:\n{result.output}"
    return proj


# ============================================================================
# TestDashboard
# ============================================================================


class TestDashboard:
    """Tests for `roam dashboard`."""

    # ---- Text output ----

    def test_dashboard_shows_verdict(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should start with a VERDICT line."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0, f"dashboard failed: {result.output}"
        assert "VERDICT:" in result.output

    def test_dashboard_has_overview_section(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should contain an Overview section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Overview ===" in result.output

    def test_dashboard_has_health_section(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should contain a Health section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Health ===" in result.output

    def test_dashboard_has_risk_areas_section(self, cli_runner, indexed_project, monkeypatch):
        """roam dashboard should contain a Risk Areas section."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Risk Areas ===" in result.output

    def test_dashboard_shows_files_and_symbols(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard overview should mention file and symbol counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "Files:" in result.output
        assert "Symbols:" in result.output

    def test_dashboard_shows_health_score(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard health section should show a numeric score."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "/100" in result.output

    def test_dashboard_shows_details_hint(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard should end with a hint to run detailed commands."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "roam health" in result.output
        assert "roam vibe-check" in result.output

    def test_dashboard_text_is_compact(self, cli_runner, indexed_project, monkeypatch):
        """Dashboard text output should be concise (<50 lines)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) < 50, f"Dashboard output too long ({len(lines)} lines), expected < 50"

    # ---- JSON output ----

    def test_dashboard_json(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dashboard should return a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        assert_json_envelope(data, "dashboard")

    def test_dashboard_json_summary_has_verdict(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary should include a verdict string."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        summary = data["summary"]
        assert "verdict" in summary
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 10

    def test_dashboard_json_summary_has_health_score(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary should include health_score in valid range."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        summary = data["summary"]
        assert "health_score" in summary
        assert 0 <= summary["health_score"] <= 100

    def test_dashboard_json_has_all_sections(self, cli_runner, indexed_project, monkeypatch):
        """JSON output should include overview, health, hotspots, risks."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        for key in ["overview", "health", "hotspots", "risks"]:
            assert key in data, f"Missing '{key}' in JSON: {list(data.keys())}"

    def test_dashboard_json_overview_has_files(self, cli_runner, indexed_project, monkeypatch):
        """JSON overview section should have file/symbol/edge counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        overview = data["overview"]
        assert overview["files"] > 0
        assert overview["symbols"] > 0

    def test_dashboard_json_health_has_score(self, cli_runner, indexed_project, monkeypatch):
        """JSON health section should have a score and label."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        health = data["health"]
        assert "score" in health
        assert "label" in health
        assert 0 <= health["score"] <= 100

    def test_dashboard_json_risks_has_dead_symbols(self, cli_runner, indexed_project, monkeypatch):
        """JSON risks section should include dead_symbols count."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        risks = data["risks"]
        assert "dead_symbols" in risks
        assert isinstance(risks["dead_symbols"], int)


# ============================================================================
# TestDashboardMinimal
# ============================================================================


class TestDashboardMinimal:
    """Test dashboard with a minimal project."""

    def test_dashboard_minimal_project(self, project_factory):
        """Dashboard should work on a project with a single file."""
        proj = project_factory(
            {
                "main.py": "def main():\n    return 1\n",
            }
        )
        runner = CliRunner()
        result = invoke_cli(runner, ["dashboard"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_dashboard_minimal_json(self, project_factory):
        """JSON mode should work on a minimal project."""
        proj = project_factory(
            {
                "main.py": "def main():\n    return 1\n",
            }
        )
        runner = CliRunner()
        result = invoke_cli(runner, ["dashboard"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "dashboard")
        assert_json_envelope(data, "dashboard")
        assert data["overview"]["files"] >= 1
