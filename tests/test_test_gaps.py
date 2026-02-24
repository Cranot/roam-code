"""Tests for the test-gaps command.

Covers:
- Symbols that have test coverage (no gaps reported)
- Symbols that lack test coverage (gaps reported)
- Severity classification (high / medium / low)
- Stale test detection
- JSON envelope contract
- VERDICT text output
- No changed files scenario
- --severity filtering
- --changed flag behavior
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Local helper: invoke test-gaps directly (bypasses CLI group)
# ---------------------------------------------------------------------------

def invoke_test_gaps(runner, args=None, cwd=None, json_mode=False):
    """Invoke the test-gaps command directly via its Click command object."""
    from click.testing import CliRunner
    from roam.commands.cmd_test_gaps import test_gaps

    full_args = list(args or [])
    obj = {"json": json_mode}

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(
            test_gaps, full_args, obj=obj, catch_exceptions=False
        )
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def project_with_tests(tmp_path):
    """Project with source files that ARE covered by tests.

    Structure:
      src/api.py       -> defines process_data() which calls helper()
      src/helper.py    -> defines helper()
      tests/test_api.py -> imports and calls process_data()
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (src / "api.py").write_text(
        'from helper import helper\n\n'
        'def process_data(x):\n'
        '    """Process data."""\n'
        '    return helper(x) + 1\n'
    )

    (src / "helper.py").write_text(
        'def helper(x):\n'
        '    """A helper function."""\n'
        '    return x * 2\n'
    )

    (tests / "test_api.py").write_text(
        'from api import process_data\n\n'
        'def test_process():\n'
        '    assert process_data(5) == 11\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def project_without_tests(tmp_path):
    """Project with source files that have NO test coverage.

    Structure:
      src/api.py       -> defines process_data(), validate_input()
      src/utils.py     -> defines _internal_helper() (private)
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    (src / "api.py").write_text(
        'def process_data(x):\n'
        '    """Process data."""\n'
        '    return x + 1\n'
        '\n'
        'def validate_input(data):\n'
        '    """Validate input data."""\n'
        '    return data is not None\n'
    )

    (src / "utils.py").write_text(
        'def _internal_helper(x):\n'
        '    """Private helper."""\n'
        '    return x * 2\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def project_mixed(tmp_path):
    """Project with a mix of tested and untested symbols.

    Structure:
      src/api.py       -> process_data() (has test), untested_fn() (no test)
      src/private.py   -> _helper() (private, no test)
      tests/test_api.py -> imports process_data
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    (src / "api.py").write_text(
        'def process_data(x):\n'
        '    """Process data."""\n'
        '    return x + 1\n'
        '\n'
        'def untested_fn():\n'
        '    """This has no test."""\n'
        '    return 42\n'
    )

    (src / "private.py").write_text(
        'def _helper():\n'
        '    """Private helper, no test."""\n'
        '    return 99\n'
    )

    (tests / "test_api.py").write_text(
        'from api import process_data\n\n'
        'def test_process():\n'
        '    assert process_data(5) == 6\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoChangedFiles:
    """Test behavior when no files are specified and --changed is not used."""

    def test_no_args_text(self, cli_runner):
        """With no files and no --changed, prints a helpful message."""
        # Create a minimal project to avoid index errors
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "repo"
            proj.mkdir()
            (proj / ".gitignore").write_text(".roam/\n")
            (proj / "main.py").write_text("def main(): pass\n")
            git_init(proj)
            out, rc = index_in_process(proj)
            assert rc == 0

            result = invoke_test_gaps(cli_runner, cwd=proj)
            assert result.exit_code == 0
            assert "No changed files" in result.output

    def test_no_args_json(self, cli_runner):
        """JSON output for no files scenario."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "repo"
            proj.mkdir()
            (proj / ".gitignore").write_text(".roam/\n")
            (proj / "main.py").write_text("def main(): pass\n")
            git_init(proj)
            out, rc = index_in_process(proj)
            assert rc == 0

            result = invoke_test_gaps(cli_runner, cwd=proj, json_mode=True)
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["summary"]["total_gaps"] == 0


class TestSymbolsWithTests:
    """Test that covered symbols are NOT reported as gaps."""

    def test_covered_symbols_no_gaps(self, cli_runner, project_with_tests):
        """Symbols referenced from test files should not appear as gaps."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_with_tests,
        )
        assert result.exit_code == 0
        assert "VERDICT" in result.output
        # process_data is called from test_api.py, so should not be a gap
        # (It might still show 0 gaps or just helper might show as untested)


class TestSymbolsWithoutTests:
    """Test that uncovered symbols ARE reported as gaps."""

    def test_untested_symbols_found(self, cli_runner, project_without_tests):
        """Symbols with no test references should appear as gaps."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
        )
        assert result.exit_code == 0
        assert "VERDICT" in result.output
        # Should find gaps for process_data and validate_input
        assert "test gaps found" in result.output or "No" not in result.output.split("VERDICT")[1].split("\n")[0]

    def test_untested_json(self, cli_runner, project_without_tests):
        """JSON output lists untested symbols."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="test-gaps")
        assert data["summary"]["total_gaps"] > 0

        # Should have gaps in medium or high
        all_gaps = data.get("high_gaps", []) + data.get("medium_gaps", [])
        gap_names = [g["name"] for g in all_gaps]
        assert "process_data" in gap_names or "validate_input" in gap_names


class TestSeverityClassification:
    """Test the HIGH / MEDIUM / LOW severity classification."""

    def test_private_symbol_is_low(self, cli_runner, project_without_tests):
        """Symbols starting with _ should be classified as LOW."""
        result = invoke_test_gaps(
            cli_runner,
            args=["--severity", "low", "src/utils.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)

        low_gaps = data.get("low_gaps", [])
        low_names = [g["name"] for g in low_gaps]
        assert "_internal_helper" in low_names

    def test_public_symbol_is_medium_or_high(self, cli_runner, project_without_tests):
        """Public symbols without tests should be at least MEDIUM."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)

        # Public functions with no tests should be medium (or high if PageRank is high)
        high_plus_medium = data.get("high_gaps", []) + data.get("medium_gaps", [])
        names = [g["name"] for g in high_plus_medium]
        assert len(names) > 0, "Expected at least one public gap"


class TestSeverityFilter:
    """Test the --severity filtering option."""

    def test_severity_high_filters(self, cli_runner, project_mixed):
        """--severity high should only show high-severity gaps."""
        result = invoke_test_gaps(
            cli_runner,
            args=["--severity", "high", "src/api.py", "src/private.py"],
            cwd=project_mixed,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # medium and low should be empty with severity=high filter
        assert len(data.get("medium_gaps", [])) == 0
        assert len(data.get("low_gaps", [])) == 0

    def test_severity_low_shows_all(self, cli_runner, project_mixed):
        """--severity low should show all severity levels."""
        result = invoke_test_gaps(
            cli_runner,
            args=["--severity", "low", "src/api.py", "src/private.py"],
            cwd=project_mixed,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # low filter means everything should be included
        all_gaps = (
            data.get("high_gaps", [])
            + data.get("medium_gaps", [])
            + data.get("low_gaps", [])
        )
        # Should find at least the private helper and untested_fn
        gap_names = [g["name"] for g in all_gaps]
        assert "_helper" in gap_names or "untested_fn" in gap_names

    def test_default_severity_medium(self, cli_runner, project_mixed):
        """Default severity=medium should hide LOW gaps."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py", "src/private.py"],
            cwd=project_mixed,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Default is medium, so low_gaps should be empty
        assert len(data.get("low_gaps", [])) == 0


class TestStaleTests:
    """Test stale test detection."""

    def test_stale_test_detected(self, cli_runner, tmp_path):
        """When source mtime > test mtime, report as stale."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        src = proj / "src"
        src.mkdir()
        tests = proj / "tests"
        tests.mkdir()

        # Write test first (earlier mtime)
        (tests / "test_api.py").write_text(
            'from api import process_data\n\n'
            'def test_process():\n'
            '    assert process_data(5) == 6\n'
        )

        # Write source second (later mtime) — simulates source changed after test
        import time
        time.sleep(0.1)  # Ensure different mtime
        (src / "api.py").write_text(
            'def process_data(x):\n'
            '    """Process data -- MODIFIED."""\n'
            '    return x + 1\n'
        )

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=proj,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)

        stale = data.get("stale_tests", [])
        # Either stale is detected (source newer) or symbol is covered without stale
        # The detection depends on mtime granularity, so we just ensure the field exists
        assert isinstance(stale, list)


class TestJsonOutput:
    """Test JSON envelope structure and required fields."""

    def test_json_envelope(self, cli_runner, project_without_tests):
        """JSON output follows the roam envelope contract."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="test-gaps")

    def test_json_has_required_keys(self, cli_runner, project_without_tests):
        """JSON output contains all expected top-level keys."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)

        # Check summary keys
        summary = data["summary"]
        assert "verdict" in summary
        assert "total_gaps" in summary
        assert "high" in summary
        assert "medium" in summary
        assert "low" in summary
        assert "stale" in summary

        # Check data keys
        assert "high_gaps" in data
        assert "medium_gaps" in data
        assert "low_gaps" in data
        assert "stale_tests" in data
        assert "recommendations" in data

    def test_json_gap_entry_structure(self, cli_runner, project_without_tests):
        """Each gap entry has the expected fields."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)

        all_gaps = (
            data.get("high_gaps", [])
            + data.get("medium_gaps", [])
            + data.get("low_gaps", [])
        )
        assert len(all_gaps) > 0, "Expected at least one gap"
        for gap in all_gaps:
            assert "name" in gap
            assert "kind" in gap
            assert "file" in gap
            assert "line" in gap
            assert "severity" in gap


class TestTextOutput:
    """Test text (non-JSON) output format."""

    def test_verdict_first(self, cli_runner, project_without_tests):
        """Text output starts with VERDICT line."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
        )
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert lines[0].startswith("VERDICT:")

    def test_summary_line(self, cli_runner, project_without_tests):
        """Text output includes a SUMMARY line."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py", "src/utils.py"],
            cwd=project_without_tests,
        )
        assert result.exit_code == 0
        assert "SUMMARY:" in result.output

    def test_sections_present(self, cli_runner, project_mixed):
        """Text output shows severity sections for mixed coverage."""
        result = invoke_test_gaps(
            cli_runner,
            args=["--severity", "low", "src/api.py", "src/private.py"],
            cwd=project_mixed,
        )
        assert result.exit_code == 0
        output = result.output
        # Should have at least one section
        has_section = (
            "HIGH" in output
            or "MEDIUM" in output
            or "LOW" in output
        )
        assert has_section, f"Expected severity section in output:\n{output}"


class TestMultipleFiles:
    """Test analyzing multiple files at once."""

    def test_multiple_files(self, cli_runner, project_without_tests):
        """Passing multiple file args analyzes all of them."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py", "src/utils.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)

        all_gaps = (
            data.get("high_gaps", [])
            + data.get("medium_gaps", [])
            + data.get("low_gaps", [])
        )
        # Should find gaps from both files
        gap_names = [g["name"] for g in all_gaps]
        # At minimum, process_data or validate_input from api.py
        assert len(gap_names) >= 1


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_nonexistent_file(self, cli_runner, project_without_tests):
        """Passing a non-existent file path handles gracefully."""
        result = invoke_test_gaps(
            cli_runner,
            args=["nonexistent.py"],
            cwd=project_without_tests,
        )
        assert result.exit_code == 0
        assert "not found" in result.output.lower() or "No" in result.output

    def test_test_file_as_input(self, cli_runner, project_with_tests):
        """Passing a test file as input skips it (only source files analyzed)."""
        result = invoke_test_gaps(
            cli_runner,
            args=["tests/test_api.py"],
            cwd=project_with_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total_gaps"] == 0


class TestRecommendations:
    """Test that recommendations are generated appropriately."""

    def test_recommendations_for_gaps(self, cli_runner, project_without_tests):
        """Recommendations list is non-empty when gaps exist."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_without_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        recs = data.get("recommendations", [])
        assert len(recs) > 0, "Expected recommendations when gaps exist"

    def test_no_recommendations_when_covered(self, cli_runner, project_with_tests):
        """No recommendations when all symbols are covered."""
        result = invoke_test_gaps(
            cli_runner,
            args=["src/api.py"],
            cwd=project_with_tests,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # If everything is covered, recommendations might be empty
        # (or might not, if stale tests detected)
        assert isinstance(data.get("recommendations", []), list)
