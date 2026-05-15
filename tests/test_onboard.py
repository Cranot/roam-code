"""Tests for the `roam onboard` command (backward-compat alias for `understand`).

Since v11.1 ``onboard`` is a thin alias for ``understand``.  These tests
verify the alias works correctly and produces the same output as ``understand``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner (Click 8.3+ removed mix_stderr)."""
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
        "class User:\n"
        '    """A user model."""\n'
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display_name(self):\n"
        "        return self.name.title()\n"
        "\n"
        "    def validate_email(self):\n"
        '        return "@" in self.email\n'
        "\n"
        "\n"
        "class Admin(User):\n"
        '    """An admin user."""\n'
        '    def __init__(self, name, email, role="admin"):\n'
        "        super().__init__(name, email)\n"
        "        self.role = role\n"
        "\n"
        "    def promote(self, user):\n"
        "        pass\n"
    )

    (src / "service.py").write_text(
        "from models import User, Admin\n"
        "\n"
        "\n"
        "def create_user(name, email):\n"
        '    """Create a new user."""\n'
        "    user = User(name, email)\n"
        "    if not user.validate_email():\n"
        '        raise ValueError("Invalid email")\n'
        "    return user\n"
        "\n"
        "\n"
        "def get_display(user):\n"
        '    """Get display name."""\n'
        "    return user.display_name()\n"
        "\n"
        "\n"
        "def unused_helper():\n"
        '    """This function is never called."""\n'
        "    return 42\n"
    )

    (src / "utils.py").write_text(
        "def format_name(first, last):\n"
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        "\n"
        "\n"
        "def parse_email(raw):\n"
        '    """Parse an email address."""\n'
        '    if "@" not in raw:\n'
        "        return None\n"
        '    parts = raw.split("@")\n'
        '    return {"user": parts[0], "domain": parts[1]}\n'
    )

    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_models.py").write_text(
        "from src.models import User\n"
        "\n"
        "def test_user_creation():\n"
        '    u = User("alice", "a@b.com")\n'
        '    assert u.name == "alice"\n'
    )

    git_init(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ============================================================================
# Alias works — basic text output
# ============================================================================


class TestOnboardAlias:
    """Verify 'onboard' alias invokes 'understand' correctly."""

    def test_onboard_exits_zero(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0

    def test_onboard_shows_project_name(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "small_proj" in result.output

    def test_onboard_shows_languages(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "py" in result.output.lower()

    def test_onboard_shows_health(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "Health:" in result.output

    def test_onboard_shows_reading_order(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        assert result.exit_code == 0
        assert "reading order" in result.output.lower()

    def test_onboard_matches_understand(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        onboard_result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        understand_result = invoke_cli(cli_runner, ["understand"], cwd=small_indexed_project)
        assert onboard_result.exit_code == 0
        assert understand_result.exit_code == 0
        # `onboard` is a deprecated alias for `understand`; the stderr
        # deprecation note legitimately makes `result.output` (which Click
        # 8.3 merges to include stderr) differ. Compare stdout-only so the
        # canonical-command output is what's tested.
        onboard_stdout = getattr(onboard_result, "stdout", None) or onboard_result.output
        understand_stdout = getattr(understand_result, "stdout", None) or understand_result.output
        assert onboard_stdout == understand_stdout


# ============================================================================
# JSON output structure (matches understand envelope)
# ============================================================================


class TestOnboardJSON:
    """Verify JSON output follows the understand envelope contract."""

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
        assert "files" in summary
        assert "symbols" in summary
        assert "health_score" in summary
        assert "languages" in summary

    def test_json_has_project(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "understand")
        assert "project" in data
        assert data["project"]["files"] > 0
        assert data["project"]["symbols"] > 0

    def test_json_has_tech_stack(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "understand")
        assert "tech_stack" in data
        assert "languages" in data["tech_stack"]

    def test_json_has_architecture(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "understand")
        assert "architecture" in data
        assert "entry_points" in data["architecture"]
        assert "key_abstractions" in data["architecture"]

    def test_json_has_reading_order(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        data = parse_json_output(result, "understand")
        assert "suggested_reading_order" in data
        reading = data["suggested_reading_order"]
        assert isinstance(reading, list)

    def test_json_matches_understand(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        onboard_result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project, json_mode=True)
        understand_result = invoke_cli(cli_runner, ["understand"], cwd=small_indexed_project, json_mode=True)
        onboard_data = parse_json_output(onboard_result, "understand")
        understand_data = parse_json_output(understand_result, "understand")
        # Same structure (timestamp may differ, but summary, project, etc. should match)
        # `onboard` is now a deprecated alias for `understand`; its envelope
        # legitimately includes `summary.deprecation_warning` while the
        # canonical call doesn't. Strip that key before comparing so the
        # rest of the summary contract is what's tested.
        onboard_summary = {k: v for k, v in onboard_data["summary"].items() if k != "deprecation_warning"}
        assert onboard_summary == understand_data["summary"]
        assert onboard_data["project"] == understand_data["project"]


# ============================================================================
# --full flag (inherited from understand)
# ============================================================================


class TestOnboardFullFlag:
    """Verify --full flag works through onboard alias."""

    def test_full_flag_exits_zero(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(cli_runner, ["onboard", "--full"], cwd=small_indexed_project)
        assert result.exit_code == 0

    def test_full_has_more_output_than_default(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        default_result = invoke_cli(cli_runner, ["onboard"], cwd=small_indexed_project)
        full_result = invoke_cli(cli_runner, ["onboard", "--full"], cwd=small_indexed_project)
        assert len(full_result.output) >= len(default_result.output)


# ============================================================================
# Empty / minimal project
# ============================================================================


class TestOnboardMinimalProject:
    """Verify graceful handling of minimal or near-empty projects."""

    def test_empty_project_text(self, cli_runner, empty_indexed_project, monkeypatch):
        monkeypatch.chdir(empty_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=empty_indexed_project)
        assert result.exit_code == 0

    def test_empty_project_json(self, cli_runner, empty_indexed_project, monkeypatch):
        monkeypatch.chdir(empty_indexed_project)
        result = invoke_cli(cli_runner, ["onboard"], cwd=empty_indexed_project, json_mode=True)
        data = parse_json_output(result, "onboard")
        assert_json_envelope(data, "onboard")


# ============================================================================
# Reading order
# ============================================================================


class TestOnboardReadingOrder:
    """Verify the suggested reading order via onboard alias."""

    def test_reading_order_has_entries(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["onboard"],
            cwd=small_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "understand")
        reading = data["suggested_reading_order"]
        assert isinstance(reading, list)
        assert len(reading) > 0

    def test_reading_order_has_priorities(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["onboard"],
            cwd=small_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "understand")
        for item in data["suggested_reading_order"]:
            assert "priority" in item
            assert "path" in item
            assert "reason" in item

    def test_reading_order_priorities_sequential(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["onboard"],
            cwd=small_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "understand")
        priorities = [item["priority"] for item in data["suggested_reading_order"]]
        if priorities:
            assert priorities[0] == 1
            for i in range(1, len(priorities)):
                assert priorities[i] > priorities[i - 1]

    def test_reading_order_no_duplicate_paths(self, cli_runner, small_indexed_project, monkeypatch):
        monkeypatch.chdir(small_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["onboard"],
            cwd=small_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "understand")
        paths = [item["path"] for item in data["suggested_reading_order"]]
        assert len(paths) == len(set(paths)), "Reading order contains duplicate paths"
