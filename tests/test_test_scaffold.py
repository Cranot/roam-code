"""Tests for roam test-scaffold -- test file skeleton generator."""

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
def scaffold_project(tmp_path):
    proj = tmp_path / "scaffold_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "calculator.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def subtract(a, b):\n"
        "    return a - b\n"
        "\n"
        "def multiply(a, b):\n"
        "    return a * b\n"
        "\n"
        "class MathEngine:\n"
        "    def divide(self, a, b):\n"
        "        return a / b\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


class TestTestScaffoldSmoke:
    def test_file_path_exits_zero(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py"], cwd=scaffold_project)
        assert result.exit_code == 0

    def test_symbol_name_exits_zero(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "add"], cwd=scaffold_project)
        assert result.exit_code == 0

    def test_nonexistent_symbol(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "nonexistent_xyz"], cwd=scaffold_project)
        # Should handle gracefully (exit 0 with message or exit 1)
        assert result.exit_code in (0, 1)


class TestTestScaffoldJSON:
    def test_json_envelope(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py"], cwd=scaffold_project, json_mode=True)
        data = parse_json_output(result, "test-scaffold")
        assert_json_envelope(data, "test-scaffold")

    def test_json_has_scaffold_content(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py"], cwd=scaffold_project, json_mode=True)
        data = parse_json_output(result, "test-scaffold")
        # Should have scaffold text or lines or symbols
        has_content = (
            "scaffold" in data
            or "content" in data
            or "lines" in data
            or "test_path" in data
            or "symbols" in data
        )
        assert has_content, f"Expected scaffold content in JSON, got keys: {list(data.keys())}"


class TestTestScaffoldText:
    def test_verdict_line(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py"], cwd=scaffold_project)
        assert "VERDICT:" in result.output

    def test_scaffold_contains_test_functions(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py"], cwd=scaffold_project)
        output = result.output.lower()
        # Should mention at least one of the functions
        assert "test_" in output or "def test" in output

    def test_scaffold_with_framework(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py", "--framework", "unittest"], cwd=scaffold_project)
        assert result.exit_code == 0


class TestTestScaffoldWrite:
    def test_write_creates_file(self, cli_runner, scaffold_project, monkeypatch):
        monkeypatch.chdir(scaffold_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py", "--write"], cwd=scaffold_project)
        assert result.exit_code == 0
        # Check that a test file was created
        test_files = list(scaffold_project.glob("**/test_calculator*"))
        assert len(test_files) >= 1, "Expected a test file to be created"
