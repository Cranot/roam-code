"""Tests for progress indicators during roam index / roam init.

Verifies that:
- Progress output appears on stderr during indexing
- Progress is suppressed in JSON mode
- Progress is suppressed with --quiet flag
- The summary line includes file count, symbol count, edge count, and timing
- The Indexer.summary dict is populated after run
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def small_project(tmp_path):
    """A minimal Python project with a few files for indexing."""
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "app.py").write_text(
        "def main():\n"
        "    return greet('world')\n"
        "\n"
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n"
    )
    (repo / "utils.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def unused():\n"
        "    pass\n"
    )
    git_init(repo)
    return repo


# ---------------------------------------------------------------------------
# Tests: Indexer progress output
# ---------------------------------------------------------------------------

class TestIndexerProgress:
    """Test the Indexer class progress output directly."""

    def test_progress_output_to_stderr(self, small_project, capsys):
        """Progress messages should go to stderr."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer
            indexer = Indexer(project_root=small_project)
            indexer.run(force=True)
        finally:
            os.chdir(old_cwd)

        captured = capsys.readouterr()
        # Progress goes to stderr, not stdout
        assert "Discovering files" in captured.err
        assert "Index complete:" in captured.err
        # stdout should be empty (no progress leaked)
        assert captured.out == ""

    def test_progress_suppressed_when_quiet(self, small_project, capsys):
        """When quiet=True, no progress output should appear."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer
            indexer = Indexer(project_root=small_project)
            indexer.run(force=True, quiet=True)
        finally:
            os.chdir(old_cwd)

        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_summary_line_format(self, small_project, capsys):
        """The final summary line should include files, symbols, edges, and timing."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer
            indexer = Indexer(project_root=small_project)
            indexer.run(force=True)
        finally:
            os.chdir(old_cwd)

        captured = capsys.readouterr()
        # Match the summary line pattern:
        # "Index complete: N files, N symbols, N edges (X.Xs)"
        pattern = r"Index complete: [\d,]+ files, [\d,]+ symbols, [\d,]+ edges \(\d+\.\d+s\)"
        assert re.search(pattern, captured.err), (
            f"Summary line not found in stderr. Got:\n{captured.err}"
        )

    def test_summary_dict_populated(self, small_project):
        """After run(), indexer.summary should be populated with counts."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer
            indexer = Indexer(project_root=small_project)
            indexer.run(force=True, quiet=True)
        finally:
            os.chdir(old_cwd)

        assert indexer.summary is not None
        assert indexer.summary["files"] > 0
        assert indexer.summary["symbols"] > 0
        assert isinstance(indexer.summary["elapsed"], float)
        assert indexer.summary["elapsed"] >= 0
        assert indexer.summary["up_to_date"] is False

    def test_summary_up_to_date(self, small_project):
        """When index is up to date, summary reflects that."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer

            # First run builds the index
            indexer1 = Indexer(project_root=small_project)
            indexer1.run(force=True, quiet=True)

            # Second run should detect no changes
            indexer2 = Indexer(project_root=small_project)
            indexer2.run(quiet=True)
        finally:
            os.chdir(old_cwd)

        assert indexer2.summary is not None
        assert indexer2.summary["up_to_date"] is True

    def test_discovery_phase_message(self, small_project, capsys):
        """Discovery phase should announce file count."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer
            indexer = Indexer(project_root=small_project)
            indexer.run(force=True)
        finally:
            os.chdir(old_cwd)

        captured = capsys.readouterr()
        assert "Discovering files" in captured.err
        assert "files found" in captured.err

    def test_phase_announcements(self, small_project, capsys):
        """Key pipeline phases should be announced in stderr."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            from roam.index.indexer import Indexer
            indexer = Indexer(project_root=small_project)
            indexer.run(force=True)
        finally:
            os.chdir(old_cwd)

        captured = capsys.readouterr()
        # Check that major phases are announced
        assert "Resolving references" in captured.err
        assert "Computing health scores" in captured.err
        assert "Building search index" in captured.err


# ---------------------------------------------------------------------------
# Tests: CLI command progress output
# ---------------------------------------------------------------------------

class TestCLIProgress:
    """Test progress output via CLI commands (roam index)."""

    def test_index_quiet_flag(self, small_project):
        """roam index --quiet should suppress all text output."""
        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            result = runner.invoke(cli, ["index", "--force", "--quiet"],
                                   catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        # Both stdout and stderr should be empty/minimal in quiet mode
        assert result.output.strip() == ""

    def test_index_json_mode_no_progress_on_stdout(self, small_project):
        """roam --json index should not mix progress text into JSON stdout."""
        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            result = runner.invoke(cli, ["--json", "index", "--force"],
                                   catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        # stdout should be valid JSON (no progress text mixed in)
        output = result.output.strip()
        if output:
            data = json.loads(output)
            assert "command" in data
            assert data["command"] == "index"

    def test_index_normal_shows_progress(self, small_project):
        """roam index (normal mode) should show completion message."""
        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(small_project))
            result = runner.invoke(cli, ["index", "--force"],
                                   catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        # Normal output should show completion
        assert "Index complete" in result.output or "Files:" in result.output


# ---------------------------------------------------------------------------
# Tests: format_count helper
# ---------------------------------------------------------------------------

class TestFormatCount:
    """Test the _format_count helper."""

    def test_small_number(self):
        from roam.index.indexer import _format_count
        assert _format_count(42) == "42"

    def test_thousands(self):
        from roam.index.indexer import _format_count
        assert _format_count(1247) == "1,247"

    def test_millions(self):
        from roam.index.indexer import _format_count
        assert _format_count(1234567) == "1,234,567"

    def test_zero(self):
        from roam.index.indexer import _format_count
        assert _format_count(0) == "0"
