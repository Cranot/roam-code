"""Tests for roam describe -- auto-generate project description for AI coding agents."""

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
def describe_project(tmp_path):
    """Python project with several files and functions for describe testing."""
    proj = tmp_path / "describe_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
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
    )

    (src / "service.py").write_text(
        "from models import User\n"
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

    git_init(proj)
    index_in_process(proj)
    return proj


class TestDescribeSmoke:
    def test_exits_zero(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project)
        assert result.exit_code == 0

    def test_agent_prompt_exits_zero(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe", "--agent-prompt"], cwd=describe_project)
        assert result.exit_code == 0


class TestDescribeJSON:
    def test_json_envelope(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project, json_mode=True)
        data = parse_json_output(result, "describe")
        assert_json_envelope(data, "describe")

    def test_json_summary_has_verdict(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project, json_mode=True)
        data = parse_json_output(result, "describe")
        assert "verdict" in data["summary"]

    def test_json_has_markdown_field(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project, json_mode=True)
        data = parse_json_output(result, "describe")
        assert "markdown" in data
        assert isinstance(data["markdown"], str)
        assert len(data["markdown"]) > 0

    def test_agent_prompt_json_envelope(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe", "--agent-prompt"], cwd=describe_project, json_mode=True)
        data = parse_json_output(result, "describe")
        assert_json_envelope(data, "describe")

    def test_agent_prompt_json_has_files_count(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe", "--agent-prompt"], cwd=describe_project, json_mode=True)
        data = parse_json_output(result, "describe")
        assert "files" in data
        assert isinstance(data["files"], int)
        assert data["files"] > 0


class TestDescribeText:
    def test_verdict_line(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project)
        assert "VERDICT:" in result.output

    def test_output_includes_project_overview(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project)
        # Should contain project structure section heading
        output_lower = result.output.lower()
        assert "project" in output_lower or "overview" in output_lower or "files" in output_lower

    def test_output_includes_directory_structure(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project)
        # The src/ directory should appear in the directory breakdown
        assert "src" in result.output

    def test_output_includes_language_info(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe"], cwd=describe_project)
        # Should mention Python as detected language
        assert "python" in result.output.lower()

    def test_agent_prompt_verdict_line(self, cli_runner, describe_project, monkeypatch):
        monkeypatch.chdir(describe_project)
        result = invoke_cli(cli_runner, ["describe", "--agent-prompt"], cwd=describe_project)
        assert "VERDICT:" in result.output

    def test_write_flag_creates_file(self, cli_runner, describe_project, monkeypatch, tmp_path):
        monkeypatch.chdir(describe_project)
        out_file = describe_project / "AGENT_DESC.md"
        result = invoke_cli(
            cli_runner,
            ["describe", "-o", str(out_file)],
            cwd=describe_project,
        )
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert len(content) > 0
