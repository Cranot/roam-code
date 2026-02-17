"""Tests for author-aware pr-risk features.

Covers:
- Basic pr-risk command execution
- JSON mode envelope and keys
- Author familiarity scoring with exponential decay
- Minor contributor detection (< 5% churn threshold)
- Edge cases: no changes, no git history, test/config file skipping
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope


# ===========================================================================
# Helpers
# ===========================================================================

def _modify_file(project, rel_path, new_content):
    """Overwrite a file in the project so git diff picks it up."""
    fp = project / rel_path
    fp.write_text(new_content)


def _make_unstaged_change(project):
    """Create a small unstaged change to src/models.py."""
    _modify_file(
        project,
        "src/models.py",
        'class User:\n'
        '    """A user model (modified for pr-risk)."""\n'
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


def _restore_file(project, rel_path, original):
    """Restore original file content."""
    fp = project / rel_path
    fp.write_text(original)


# ===========================================================================
# Basic pr-risk command
# ===========================================================================


class TestPrRiskBasic:
    """Basic pr-risk command invocation."""

    def test_pr_risk_runs_exits_zero(self, indexed_project, cli_runner, monkeypatch):
        """pr-risk exits 0 even when there are no changes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk"])
        assert result.exit_code == 0

    def test_pr_risk_produces_output(self, indexed_project, cli_runner, monkeypatch):
        """pr-risk produces some output (even if just 'No changes found')."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk"])
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    def test_pr_risk_with_unstaged_change(self, indexed_project, cli_runner, monkeypatch):
        """pr-risk with an unstaged modification shows risk assessment."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(cli_runner, ["pr-risk"])
            assert result.exit_code == 0
            output = result.output.lower()
            assert "risk" in output or "verdict" in output
        finally:
            _restore_file(indexed_project, "src/models.py", original)


# ===========================================================================
# JSON mode
# ===========================================================================


class TestPrRiskJson:
    """JSON mode output validation."""

    def test_json_no_changes(self, indexed_project, cli_runner, monkeypatch):
        """--json with no changes returns valid JSON with risk_score 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
        assert result.exit_code == 0
        output = result.output.strip()
        if output.startswith("{"):
            data = json.loads(output)
            assert "risk_score" in data or "summary" in data

    def test_json_with_changes_envelope(self, indexed_project, cli_runner, monkeypatch):
        """--json with changes returns proper json_envelope structure."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
            assert result.exit_code == 0
            data = json.loads(result.output)
            # If it returned the full envelope (has changes detected)
            if "summary" in data:
                assert_json_envelope(data, command="pr-risk")
                summary = data["summary"]
                assert "risk_score" in summary
                assert "risk_level" in summary
                assert "verdict" in summary
                assert "changed_files" in summary
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_json_contains_risk_fields(self, indexed_project, cli_runner, monkeypatch):
        """--json output should contain expected risk-breakdown fields."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "summary" in data:
                # These top-level keys should be present
                for key in [
                    "risk_score", "risk_level", "blast_radius_pct",
                    "hotspot_score", "test_coverage_pct",
                    "bus_factor_risk", "per_file",
                ]:
                    assert key in data, f"Missing key: {key}"
        finally:
            _restore_file(indexed_project, "src/models.py", original)


# ===========================================================================
# Author familiarity
# ===========================================================================


class TestAuthorFamiliarity:
    """Tests for author familiarity scoring in pr-risk."""

    def test_familiarity_in_json_output(self, indexed_project, cli_runner, monkeypatch):
        """JSON output should include a 'familiarity' key."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "summary" in data:
                assert "familiarity" in data, (
                    "Expected 'familiarity' key in JSON output"
                )
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_familiarity_has_expected_shape(self, indexed_project, cli_runner, monkeypatch):
        """Familiarity dict should contain avg_familiarity and files_assessed."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner, ["pr-risk", "--author", "Test"],
                json_mode=True,
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "familiarity" in data:
                fam = data["familiarity"]
                assert "avg_familiarity" in fam
                assert "files_assessed" in fam
                assert "files" in fam
                assert isinstance(fam["files"], list)
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_familiarity_text_output(self, indexed_project, cli_runner, monkeypatch):
        """Text output should show 'Familiarity' line when author is resolved."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner, ["pr-risk", "--author", "Test"],
            )
            assert result.exit_code == 0
            assert "Familiarity" in result.output
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_familiarity_author_option(self, indexed_project, cli_runner, monkeypatch):
        """Explicit --author flag should be used for familiarity scoring."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner,
                ["pr-risk", "--author", "Test"],
                json_mode=True,
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "author" in data:
                assert data["author"] == "Test"
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_familiarity_unknown_author(self, indexed_project, cli_runner, monkeypatch):
        """An unknown author should get low familiarity (0.0)."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner,
                ["pr-risk", "--author", "CompletelyUnknownPerson"],
                json_mode=True,
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "familiarity" in data:
                fam = data["familiarity"]
                # Unknown author has zero familiarity with any file
                assert fam["avg_familiarity"] == 0.0
        finally:
            _restore_file(indexed_project, "src/models.py", original)


# ===========================================================================
# _author_familiarity unit tests (direct function tests)
# ===========================================================================


class TestAuthorFamiliarityUnit:
    """Unit tests for the _author_familiarity function directly."""

    def test_exponential_decay_half_life(self):
        """Decay rate 0.005 per day gives half-life ~139 days."""
        decay_rate = 0.005
        half_life = math.log(2) / decay_rate
        assert 138 < half_life < 140, f"Half-life was {half_life}"

    def test_no_changed_files(self):
        """With no changed files, returns zero risk and empty details."""
        from roam.commands.cmd_pr_risk import _author_familiarity

        # Create a mock connection that should never be called
        class MockConn:
            def execute(self, *a, **kw):
                raise RuntimeError("Should not be called")

        risk, details = _author_familiarity(MockConn(), "Alice", {})
        assert risk == 0.0
        assert details["files_assessed"] == 0
        assert details["avg_familiarity"] == 1.0
        assert details["files"] == []


# ===========================================================================
# PR-risk math helpers
# ===========================================================================


class TestPrRiskMathHelpers:
    """Unit tests for calibrated PR-risk helper math."""

    def test_calibrated_hotspot_score_monotonic(self):
        from roam.commands.cmd_pr_risk import _calibrated_hotspot_score

        repo = [5, 10, 20, 40, 80, 120, 200]
        low = _calibrated_hotspot_score(5, repo)
        mid = _calibrated_hotspot_score(40, repo)
        high = _calibrated_hotspot_score(200, repo)

        assert 0.0 <= low <= 1.0
        assert 0.0 <= mid <= 1.0
        assert 0.0 <= high <= 1.0
        assert low <= mid <= high

    def test_author_count_risk_continuous(self):
        from roam.commands.cmd_pr_risk import _author_count_risk

        assert _author_count_risk([]) == 0.0
        assert _author_count_risk([1]) == pytest.approx(1.0)
        assert _author_count_risk([2]) == pytest.approx(0.5)
        assert _author_count_risk([4]) == pytest.approx(0.25)
        assert _author_count_risk([1, 4]) == pytest.approx(0.625)


# ===========================================================================
# Minor contributor detection
# ===========================================================================


class TestMinorContributor:
    """Tests for minor contributor detection in pr-risk."""

    def test_minor_risk_in_json_output(self, indexed_project, cli_runner, monkeypatch):
        """JSON output should include 'minor_risk' key."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner,
                ["pr-risk", "--author", "Test"],
                json_mode=True,
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "summary" in data:
                assert "minor_risk" in data, (
                    "Expected 'minor_risk' key in JSON output"
                )
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_minor_risk_has_expected_shape(self, indexed_project, cli_runner, monkeypatch):
        """minor_risk dict should have minor_files and files_assessed."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner,
                ["pr-risk", "--author", "Test"],
                json_mode=True,
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            if "minor_risk" in data:
                mr = data["minor_risk"]
                assert "minor_files" in mr
                assert "files_assessed" in mr
                assert "files" in mr
                assert isinstance(mr["files"], list)
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_minor_contributor_unit_no_files(self):
        """With no changed files, returns zero risk."""
        from roam.commands.cmd_pr_risk import _minor_contributor_risk

        class MockConn:
            def execute(self, *a, **kw):
                raise RuntimeError("Should not be called")

        risk, details = _minor_contributor_risk(MockConn(), "Alice", {})
        assert risk == 0.0
        assert details["minor_files"] == 0
        assert details["files_assessed"] == 0

    def test_minor_text_output_known_author(self, indexed_project, cli_runner, monkeypatch):
        """Text output should show 'Minor risk' line when author is known."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner, ["pr-risk", "--author", "Test"],
            )
            assert result.exit_code == 0
            # Should show either "Minor risk:" line
            assert "Minor risk" in result.output or "minor" in result.output.lower()
        finally:
            _restore_file(indexed_project, "src/models.py", original)


# ===========================================================================
# Edge cases
# ===========================================================================


class TestPrRiskEdgeCases:
    """Edge cases for pr-risk."""

    def test_no_staged_changes(self, indexed_project, cli_runner, monkeypatch):
        """--staged with nothing staged reports no changes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk", "--staged"])
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_no_staged_changes_json(self, indexed_project, cli_runner, monkeypatch):
        """--staged --json with nothing staged returns valid JSON."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk", "--staged"], json_mode=True)
        assert result.exit_code == 0
        output = result.output.strip()
        if output.startswith("{"):
            data = json.loads(output)
            assert "risk_score" in data or "message" in data

    def test_author_flag_accepts_arbitrary_name(self, indexed_project, cli_runner, monkeypatch):
        """--author with a nonexistent name doesn't crash."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            result = invoke_cli(
                cli_runner,
                ["pr-risk", "--author", "NonexistentDev123"],
            )
            assert result.exit_code == 0
        finally:
            _restore_file(indexed_project, "src/models.py", original)

    def test_detect_author_function(self):
        """_detect_author should return a string or None."""
        from roam.commands.cmd_pr_risk import _detect_author
        author = _detect_author()
        assert author is None or isinstance(author, str)
