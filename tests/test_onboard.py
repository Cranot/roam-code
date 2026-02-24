"""Tests for the `roam onboard` command.

Covers:
- All sections present in text output
- JSON output structure and envelope contract
- --detail flag levels (brief/normal/full)
- Empty / minimal project handling
- Entry points section
- Reading order generation
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.fixture
def empty_indexed_project(tmp_path):
    """A git repo with a single trivial file, indexed."""
    repo = tmp_path / "empty_proj"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "hello.py").write_text("# empty\n")
    git_init(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def small_indexed_project(tmp_path):
    """A small Python project with imports and calls, indexed."""
    repo = tmp_path / "small_proj"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")

    src = repo / "src"
    src.mkdir()

    (src / "models.py").write_text(
        'class User:\n'
        '    """A user model."""\n'
        '    def __init__(self, name, email):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        '    def display_name(self):\n'
        '        return self.name.title()\n'
        '\n'
        '    def validate_email(self):\n'
        '        return "@" in self.email\n'
        '\n'
        '\n'
        'class Admin(User):\n'
        '    """An admin user."""\n'
        '    def __init__(self, name, email, role="admin"):\n'
        '        super().__init__(name, email)\n'
        '        self.role = role\n'
        '\n'
        '    def promote(self, user):\n'
        '        pass\n'
    )

    (src / "service.py").write_text(
        'from models import User, Admin\n'
        '\n'
        '\n'
        'def create_user(name, email):\n'
        '    """Create a new user."""\n'
        '    user = User(name, email)\n'
        '    if not user.validate_email():\n'
        '        raise ValueError("Invalid email")\n'
        '    return user\n'
        '\n'
        '\n'
        'def get_display(user):\n'
        '    """Get display name."""\n'
        '    return user.display_name()\n'
        '\n'
        '\n'
        'def unused_helper():\n'
        '    """This function is never called."""\n'
        '    return 42\n'
    )

    (src / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        '\n'
        '\n'
        'def parse_email(raw):\n'
        '    """Parse an email address."""\n'
        '    if "@" not in raw:\n'
        '        return None\n'
        '    parts = raw.split("@")\n'
        '    return {"user": parts[0], "domain": parts[1]}\n'
    )

    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_models.py").write_text(
        'from src.models import User\n'
        '\n'
        'def test_user_creation():\n'
        '    u = User("alice", "a@b.com")\n'
        '    assert u.name == "alice"\n'
    )

    git_init(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ============================================================================
# Text output: all sections present
# ============================================================================

class TestOnboardTextSections:
    """Verify all expected sections appear in text output."""

    def test_project_overview_present(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "PROJECT OVERVIEW" in result.output

    def test_architecture_present(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "ARCHITECTURE" in result.output

    def test_entry_points_present(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "ENTRY POINTS" in result.output

    def test_reading_order_present(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "SUGGESTED READING ORDER" in result.output

    def test_conventions_present(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "CONVENTIONS" in result.output

    def test_onboarding_guide_header(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "ONBOARDING GUIDE" in result.output

    def test_files_count_shown(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "Files:" in result.output

    def test_symbols_count_shown(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "Symbols:" in result.output

    def test_languages_shown(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "Languages:" in result.output
        # Language name in DB is "python" or "py" depending on version
        assert "py" in result.output.lower()


# ============================================================================
# JSON output structure
# ============================================================================

class TestOnboardJSON:
    """Verify JSON output follows the envelope contract."""

    def test_json_envelope(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        assert_json_envelope(data, "onboard")

    def test_json_summary_fields(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        summary = data["summary"]
        assert "verdict" in summary
        assert "files" in summary
        assert "symbols" in summary
        assert "languages" in summary
        assert "layers" in summary
        assert "modules" in summary
        assert "entry_points" in summary
        assert "risk_areas" in summary
        assert "detail" in summary

    def test_json_has_all_sections(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        assert "overview" in data
        assert "architecture" in data
        assert "entry_points" in data
        assert "critical_paths" in data
        assert "risk_areas" in data
        assert "reading_order" in data
        assert "conventions" in data

    def test_json_overview_structure(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        overview = data["overview"]
        assert "total_files" in overview
        assert "total_symbols" in overview
        assert "languages" in overview
        assert "primary_language" in overview
        assert "has_tests" in overview
        assert overview["total_files"] > 0
        assert overview["total_symbols"] > 0

    def test_json_architecture_structure(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        arch = data["architecture"]
        assert "layer_count" in arch
        assert "layers" in arch
        assert "cluster_count" in arch
        assert "clusters" in arch

    def test_json_entry_points_structure(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        eps = data["entry_points"]
        assert isinstance(eps, list)
        if eps:
            ep = eps[0]
            assert "name" in ep
            assert "kind" in ep
            assert "file" in ep
            assert "pagerank" in ep

    def test_json_conventions_structure(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        conv = data["conventions"]
        assert "naming" in conv
        assert "test_pattern" in conv
        assert "test_file_count" in conv

    def test_json_detail_field_reflects_flag(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        for detail_val in ("brief", "normal", "full"):
            result = invoke_cli(
                cli_runner,
                ["onboard", "--detail", detail_val],
                cwd=small_indexed_project,
                json_mode=True,
            )
            data = parse_json_output(result, "onboard")
            assert data["summary"]["detail"] == detail_val


# ============================================================================
# --detail flag levels
# ============================================================================

class TestOnboardDetailLevels:
    """Verify that --detail flag changes output volume."""

    def test_brief_runs_ok(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard", "--detail", "brief"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "ONBOARDING GUIDE" in result.output

    def test_normal_runs_ok(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard", "--detail", "normal"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "ONBOARDING GUIDE" in result.output

    def test_full_runs_ok(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard", "--detail", "full"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "ONBOARDING GUIDE" in result.output

    def test_full_has_more_output_than_brief(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        brief_result = invoke_cli(cli_runner, ["onboard", "--detail", "brief"], cwd=small_indexed_project)
        full_result = invoke_cli(cli_runner, ["onboard", "--detail", "full"], cwd=small_indexed_project)
        # Full should have at least as much output as brief
        assert len(full_result.output) >= len(brief_result.output)

    def test_invalid_detail_rejected(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard", "--detail", "invalid"], cwd=small_indexed_project)
        assert result.exit_code != 0

    def test_json_brief_has_fewer_entry_points(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        brief_result = invoke_cli(
            cli_runner, ["onboard", "--detail", "brief"],
            cwd=small_indexed_project, json_mode=True,
        )
        full_result = invoke_cli(
            cli_runner, ["onboard", "--detail", "full"],
            cwd=small_indexed_project, json_mode=True,
        )
        brief_data = parse_json_output(brief_result, "onboard")
        full_data = parse_json_output(full_result, "onboard")
        # Full should have >= entry points compared to brief
        assert len(full_data["entry_points"]) >= len(brief_data["entry_points"])


# ============================================================================
# Empty / minimal project
# ============================================================================

class TestOnboardMinimalProject:
    """Verify graceful handling of minimal or near-empty projects."""

    def test_empty_project_text(self, cli_runner, empty_indexed_project, monkeypatch):
        monkeypatch.chdir(empty_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=empty_indexed_project)
        assert result.exit_code == 0
        assert "ONBOARDING GUIDE" in result.output
        assert "PROJECT OVERVIEW" in result.output

    def test_empty_project_json(self, cli_runner, empty_indexed_project, monkeypatch):
        monkeypatch.chdir(empty_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=empty_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        assert_json_envelope(data, "onboard")
        assert "verdict" in data["summary"]

    def test_empty_project_no_crash_on_all_details(self, cli_runner, empty_indexed_project, monkeypatch):
        monkeypatch.chdir(empty_indexed_project)
        for detail in ("brief", "normal", "full"):
            result = invoke_cli(
                cli_runner, ["onboard", "--detail", detail],
                cwd=empty_indexed_project,
            )
            assert result.exit_code == 0


# ============================================================================
# Entry points section
# ============================================================================

class TestOnboardEntryPoints:
    """Verify entry points are extracted and shown correctly."""

    def test_entry_points_contain_symbols(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        eps = data["entry_points"]
        # Our project has functions and classes, some should show as entry points
        if eps:
            names = [ep["name"] for ep in eps]
            # At least one of the project's symbols should appear
            assert len(names) > 0

    def test_entry_points_have_pagerank(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        for ep in data["entry_points"]:
            assert "pagerank" in ep
            assert isinstance(ep["pagerank"], (int, float))

    def test_entry_points_have_why(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        for ep in data["entry_points"]:
            assert "why" in ep
            assert len(ep["why"]) > 0


# ============================================================================
# Reading order
# ============================================================================

class TestOnboardReadingOrder:
    """Verify the suggested reading order is generated correctly."""

    def test_reading_order_has_entries(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        reading = data["reading_order"]
        assert isinstance(reading, list)
        # With a project that has symbols, we should have at least one reading entry
        if data["entry_points"]:
            assert len(reading) > 0

    def test_reading_order_has_priorities(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        for item in data["reading_order"]:
            assert "priority" in item
            assert "path" in item
            assert "reason" in item

    def test_reading_order_priorities_sequential(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        priorities = [item["priority"] for item in data["reading_order"]]
        if priorities:
            # Priorities should start at 1 and be sequential
            assert priorities[0] == 1
            for i in range(1, len(priorities)):
                assert priorities[i] > priorities[i - 1]

    def test_reading_order_no_duplicate_paths(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        paths = [item["path"] for item in data["reading_order"]]
        assert len(paths) == len(set(paths)), "Reading order contains duplicate paths"


# ============================================================================
# Verdict
# ============================================================================

class TestOnboardVerdict:
    """Verify the verdict is generated."""

    def test_verdict_in_json(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner, ["onboard"],
            cwd=small_indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "onboard")
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)
        assert len(data["summary"]["verdict"]) > 0
