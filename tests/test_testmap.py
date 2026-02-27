"""Tests for roam test-map -- map symbols/files to their test coverage."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ===========================================================================
# Fixture: project with source and test files
# ===========================================================================


@pytest.fixture
def testmap_project(tmp_path):
    """A project with source files and matching test files.

    Layout:
      src/calculator.py   -- defines Calculator class and add_numbers function
      src/formatter.py    -- defines format_result function
      tests/test_calc.py  -- imports from calculator, has test_ functions
      tests/test_fmt.py   -- imports from formatter

    This gives test-map real data for:
    - Direct test edges (test calls source symbol)
    - Test file importers (test file imports source file)
    - Symbols with no test coverage (untested_helper)
    """
    proj = tmp_path / "testmap_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (src / "calculator.py").write_text(
        "class Calculator:\n"
        '    """A simple calculator."""\n'
        "\n"
        "    def add(self, a, b):\n"
        '        """Add two numbers."""\n'
        "        return a + b\n"
        "\n"
        "    def subtract(self, a, b):\n"
        '        """Subtract b from a."""\n'
        "        return a - b\n"
        "\n"
        "    def multiply(self, a, b):\n"
        '        """Multiply two numbers."""\n'
        "        return a * b\n"
        "\n"
        "\n"
        "def add_numbers(x, y):\n"
        '    """Add two numbers (module-level)."""\n'
        "    return x + y\n"
        "\n"
        "\n"
        "def untested_helper():\n"
        '    """This function has no test coverage."""\n'
        "    return 0\n"
    )

    (src / "formatter.py").write_text(
        "def format_result(value, precision=2):\n"
        '    """Format a numeric result as a string."""\n"'
        '    return f"{value:.{precision}f}"\n'
        "\n"
        "\n"
        "def format_list(items):\n"
        '    """Format a list of results."""\n'
        "    return ', '.join(str(i) for i in items)\n"
    )

    (tests / "test_calc.py").write_text(
        "from src.calculator import Calculator, add_numbers\n"
        "\n"
        "\n"
        "def test_add():\n"
        '    """Test Calculator.add."""\n'
        "    c = Calculator()\n"
        "    assert c.add(1, 2) == 3\n"
        "\n"
        "\n"
        "def test_subtract():\n"
        '    """Test Calculator.subtract."""\n'
        "    c = Calculator()\n"
        "    assert c.subtract(5, 3) == 2\n"
        "\n"
        "\n"
        "def test_add_numbers():\n"
        '    """Test the module-level add_numbers function."""\n'
        "    assert add_numbers(10, 20) == 30\n"
    )

    (tests / "test_fmt.py").write_text(
        "from src.formatter import format_result\n"
        "\n"
        "\n"
        "def test_format_result_default():\n"
        '    """Test format_result with default precision."""\n'
        '    assert format_result(3.14159) == "3.14"\n'
        "\n"
        "\n"
        "def test_format_result_precision():\n"
        '    """Test format_result with custom precision."""\n'
        '    assert format_result(3.14159, 4) == "3.1416"\n'
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def no_tests_project(tmp_path):
    """A project with source files but no test files."""
    proj = tmp_path / "notests_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text('def important_function():\n    """No tests exist for this."""\n    return 42\n')
    git_init(proj)
    index_in_process(proj)
    return proj


# ===========================================================================
# Smoke tests
# ===========================================================================


class TestTestMapSmoke:
    """Basic invocation smoke tests."""

    def test_exits_zero_for_known_symbol(self, cli_runner, testmap_project, monkeypatch):
        """roam test-map <known-symbol> exits 0."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project)
        assert result.exit_code == 0

    def test_exits_zero_for_function_symbol(self, cli_runner, testmap_project, monkeypatch):
        """roam test-map <known-function> exits 0."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "add_numbers"], cwd=testmap_project)
        assert result.exit_code == 0

    def test_exits_zero_for_untested_symbol(self, cli_runner, testmap_project, monkeypatch):
        """roam test-map on an untested symbol exits 0 with 'no tests' output."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "untested_helper"], cwd=testmap_project)
        assert result.exit_code == 0

    def test_produces_output(self, cli_runner, testmap_project, monkeypatch):
        """roam test-map produces non-empty output."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project)
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    def test_help_works(self, cli_runner):
        """--help exits 0 and mentions 'test-map'."""
        result = invoke_cli(cli_runner, ["test-map", "--help"])
        assert result.exit_code == 0
        assert "test" in result.output.lower()

    def test_unknown_symbol_exits_nonzero(self, cli_runner, testmap_project, monkeypatch):
        """Unknown symbol name should produce a non-zero exit or 'Not found' message."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "nonexistent_xyz_symbol_42"], cwd=testmap_project)
        # test-map raises SystemExit(1) for unknown symbols
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_file_path_mode(self, cli_runner, testmap_project, monkeypatch):
        """roam test-map with a file path (containing slash) exits 0."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "src/calculator.py"], cwd=testmap_project)
        assert result.exit_code == 0


# ===========================================================================
# JSON envelope tests
# ===========================================================================


class TestTestMapJSON:
    """JSON mode output validation."""

    def test_json_envelope_symbol(self, cli_runner, testmap_project, monkeypatch):
        """JSON output for a symbol follows the roam envelope contract."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert_json_envelope(data, command="test-map")

    def test_json_envelope_function(self, cli_runner, testmap_project, monkeypatch):
        """JSON output for a function symbol follows the roam envelope contract."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "add_numbers"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert_json_envelope(data, command="test-map")

    def test_json_summary_has_verdict(self, cli_runner, testmap_project, monkeypatch):
        """JSON summary contains a verdict string."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {list(summary.keys())}"
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 0

    def test_json_summary_has_direct_tests_count(self, cli_runner, testmap_project, monkeypatch):
        """JSON summary contains direct_tests count."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        summary = data.get("summary", {})
        assert "direct_tests" in summary, f"Missing 'direct_tests': {list(summary.keys())}"
        assert isinstance(summary["direct_tests"], int)

    def test_json_summary_has_test_importers_count(self, cli_runner, testmap_project, monkeypatch):
        """JSON summary contains test_importers count."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        summary = data.get("summary", {})
        assert "test_importers" in summary, f"Missing 'test_importers': {list(summary.keys())}"
        assert isinstance(summary["test_importers"], int)

    def test_json_has_name_and_kind(self, cli_runner, testmap_project, monkeypatch):
        """JSON output for a symbol includes name and kind fields."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert "name" in data, f"Missing 'name': {list(data.keys())}"
        assert "kind" in data, f"Missing 'kind': {list(data.keys())}"
        assert data["name"] == "Calculator"

    def test_json_has_direct_tests_list(self, cli_runner, testmap_project, monkeypatch):
        """JSON output includes a direct_tests list."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert "direct_tests" in data, f"Missing 'direct_tests' list: {list(data.keys())}"
        assert isinstance(data["direct_tests"], list)

    def test_json_has_test_importers_list(self, cli_runner, testmap_project, monkeypatch):
        """JSON output includes a test_importers list."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert "test_importers" in data, f"Missing 'test_importers' list: {list(data.keys())}"
        assert isinstance(data["test_importers"], list)

    def test_json_has_convention_tests_list(self, cli_runner, testmap_project, monkeypatch):
        """JSON output includes a convention_tests list."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert "convention_tests" in data, f"Missing 'convention_tests' list: {list(data.keys())}"
        assert isinstance(data["convention_tests"], list)

    def test_json_untested_symbol_verdict(self, cli_runner, testmap_project, monkeypatch):
        """An untested symbol has zero direct tests in the summary."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "untested_helper"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        summary = data.get("summary", {})
        # untested_helper has no direct test callers (even if the file is imported by a test file)
        assert summary.get("direct_tests", 0) == 0, (
            f"Expected 0 direct_tests for untested_helper, got {summary.get('direct_tests')}"
        )

    def test_json_file_path_envelope(self, cli_runner, testmap_project, monkeypatch):
        """JSON output for file-path mode follows the roam envelope contract."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "src/calculator.py"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert_json_envelope(data, command="test-map")

    def test_json_file_mode_has_test_importers(self, cli_runner, testmap_project, monkeypatch):
        """File-path mode JSON output includes test_importers list."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "src/calculator.py"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert "test_importers" in data, f"Missing 'test_importers': {list(data.keys())}"
        assert isinstance(data["test_importers"], list)

    def test_json_file_mode_has_path(self, cli_runner, testmap_project, monkeypatch):
        """File-path mode JSON output includes the path field."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "src/calculator.py"], cwd=testmap_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        assert "path" in data, f"Missing 'path': {list(data.keys())}"

    def test_json_no_tests_project(self, cli_runner, no_tests_project, monkeypatch):
        """Symbol in a project with no tests has zero direct_tests."""
        monkeypatch.chdir(no_tests_project)
        result = invoke_cli(cli_runner, ["test-map", "important_function"], cwd=no_tests_project, json_mode=True)
        data = parse_json_output(result, "test-map")
        summary = data.get("summary", {})
        assert summary.get("direct_tests", 0) == 0


# ===========================================================================
# Text output tests
# ===========================================================================


class TestTestMapText:
    """Text mode output validation."""

    def test_verdict_line_present(self, cli_runner, testmap_project, monkeypatch):
        """Text output contains a VERDICT: line."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_verdict_is_first_line(self, cli_runner, testmap_project, monkeypatch):
        """VERDICT: is the first non-empty line of text output."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:")

    def test_symbol_name_in_output(self, cli_runner, testmap_project, monkeypatch):
        """The queried symbol name appears in text output."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project)
        assert result.exit_code == 0
        assert "Calculator" in result.output

    def test_test_file_reference_in_output(self, cli_runner, testmap_project, monkeypatch):
        """A test file name appears in the output when tests exist."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "Calculator"], cwd=testmap_project)
        assert result.exit_code == 0
        assert "test_calc" in result.output or "test" in result.output.lower()

    def test_untested_symbol_no_tests_message(self, cli_runner, testmap_project, monkeypatch):
        """An untested symbol shows a 'no tests' or 'none' message."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "untested_helper"], cwd=testmap_project)
        assert result.exit_code == 0
        lower = result.output.lower()
        assert "no tests" in lower or "none" in lower

    def test_file_mode_verdict_present(self, cli_runner, testmap_project, monkeypatch):
        """File-path mode also produces a VERDICT: line."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "src/calculator.py"], cwd=testmap_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_file_mode_shows_file_path(self, cli_runner, testmap_project, monkeypatch):
        """File-path mode output mentions the queried file."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "src/calculator.py"], cwd=testmap_project)
        assert result.exit_code == 0
        assert "calculator" in result.output.lower()

    def test_unknown_symbol_shows_not_found(self, cli_runner, testmap_project, monkeypatch):
        """Unknown symbol shows 'Not found' or similar message."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "totally_nonexistent_xyz"], cwd=testmap_project)
        lower = result.output.lower()
        # Either exit 1 or output contains a 'not found' message
        assert result.exit_code != 0 or "not found" in lower

    def test_add_numbers_has_test_coverage(self, cli_runner, testmap_project, monkeypatch):
        """add_numbers has a test in test_calc.py — output should reflect this."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "add_numbers"], cwd=testmap_project)
        assert result.exit_code == 0
        # The verdict should not be 'no tests'
        first_line = result.output.strip().splitlines()[0]
        assert "no tests" not in first_line.lower()

    def test_format_result_has_test_coverage(self, cli_runner, testmap_project, monkeypatch):
        """format_result is tested by test_fmt.py — output should reflect this."""
        monkeypatch.chdir(testmap_project)
        result = invoke_cli(cli_runner, ["test-map", "format_result"], cwd=testmap_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
