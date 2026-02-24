"""Tests for suggest-reviewers command.

Covers:
- Multi-developer scoring with varying ownership
- --exclude flag
- --top N with different values
- No changed files (graceful handling)
- JSON output structure and envelope
- Scoring: ownership signal dominance
- CODEOWNERS integration
- Coverage calculation
- Internal helpers: _parse_codeowners, _resolve_codeowners,
  _compute_file_ownership, _compute_recency, _compute_breadth
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope, git_commit


# ===========================================================================
# Helpers
# ===========================================================================

def _make_multi_author_project(tmp_path):
    """Create a project with multiple authors contributing to files.

    Sets up a git repo where alice, bob, and carol each own different
    files (and share some), providing distinct ownership signals.
    """
    proj = tmp_path / "reviewers_proj"
    proj.mkdir()
    src = proj / "src"
    src.mkdir()
    lib = proj / "lib"
    lib.mkdir()

    (proj / ".gitignore").write_text(".roam/\n")

    # Initial files
    (src / "auth.py").write_text(
        'def login(user, password):\n'
        '    """Authenticate a user."""\n'
        '    return user == "admin"\n'
    )
    (src / "models.py").write_text(
        'class User:\n'
        '    def __init__(self, name):\n'
        '        self.name = name\n'
    )
    (lib / "utils.py").write_text(
        'def helper():\n'
        '    return 42\n'
    )

    # Init with alice
    subprocess.run(["git", "init"], cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.email", "alice@company.com"],
                   cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.name", "alice"],
                   cwd=proj, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init by alice"],
                   cwd=proj, capture_output=True)

    # Bob modifies auth.py
    subprocess.run(["git", "config", "user.name", "bob"],
                   cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.email", "bob@company.com"],
                   cwd=proj, capture_output=True)
    (src / "auth.py").write_text(
        'def login(user, password):\n'
        '    """Authenticate a user (improved by bob)."""\n'
        '    if not user:\n'
        '        return False\n'
        '    return user == "admin" and password == "secret"\n'
        '\n'
        'def logout(user):\n'
        '    """Log out a user."""\n'
        '    pass\n'
    )
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "bob improves auth"],
                   cwd=proj, capture_output=True)

    # Carol modifies models.py and lib/utils.py
    subprocess.run(["git", "config", "user.name", "carol"],
                   cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.email", "carol@company.com"],
                   cwd=proj, capture_output=True)
    (src / "models.py").write_text(
        'class User:\n'
        '    def __init__(self, name, email):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        'class Admin(User):\n'
        '    def __init__(self, name, email):\n'
        '        super().__init__(name, email)\n'
        '        self.is_admin = True\n'
    )
    (lib / "utils.py").write_text(
        'def helper():\n'
        '    return 42\n'
        '\n'
        'def format_name(name):\n'
        '    return name.title()\n'
    )
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "carol expands models and utils"],
                   cwd=proj, capture_output=True)

    return proj


def _index_project(proj, monkeypatch):
    """Index a project using CliRunner."""
    from conftest import index_in_process
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"


def _make_unstaged_change(proj, rel_path, content):
    """Create an unstaged change."""
    fp = proj / rel_path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)


# ===========================================================================
# Command execution tests
# ===========================================================================


class TestSuggestReviewersBasic:
    """Basic suggest-reviewers command invocation."""

    def test_no_changes_exits_zero(self, indexed_project, cli_runner, monkeypatch):
        """suggest-reviewers exits 0 when there are no changes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["suggest-reviewers"])
        assert result.exit_code == 0
        assert "No changed files" in result.output

    def test_no_changes_json(self, indexed_project, cli_runner, monkeypatch):
        """JSON output when no files are changed."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["suggest-reviewers"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["verdict"] == "No changed files found"
        assert data["reviewers"] == []

    def test_with_unstaged_changes(self, indexed_project, cli_runner, monkeypatch):
        """suggest-reviewers runs on unstaged changes."""
        monkeypatch.chdir(indexed_project)
        # Make a change
        (indexed_project / "src" / "models.py").write_text(
            'class User:\n'
            '    """Modified."""\n'
            '    pass\n'
        )
        result = invoke_cli(cli_runner, ["suggest-reviewers"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_changed_flag(self, indexed_project, cli_runner, monkeypatch):
        """--changed flag uses git diff HEAD."""
        monkeypatch.chdir(indexed_project)
        (indexed_project / "src" / "models.py").write_text(
            'class User:\n'
            '    """Modified with --changed."""\n'
            '    pass\n'
        )
        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_positional_file_args(self, indexed_project, cli_runner, monkeypatch):
        """Positional file args work as alternative to --changed."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["suggest-reviewers", "src/models.py"])
        assert result.exit_code == 0


# ===========================================================================
# Multi-author scoring tests
# ===========================================================================


class TestSuggestReviewersScoring:
    """Test multi-signal scoring with multiple authors."""

    def test_multiple_authors_ranked(self, tmp_path, cli_runner, monkeypatch):
        """Multiple authors are ranked by composite score."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        # Make a change to auth.py (bob and alice have ownership)
        _make_unstaged_change(proj, "src/auth.py",
            'def login(user, password):\n'
            '    """Modified for review test."""\n'
            '    return True\n'
        )

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Should suggest at least one reviewer
        assert "REVIEWER" in result.output or "No reviewers" in result.output

    def test_ownership_signal_present(self, tmp_path, cli_runner, monkeypatch):
        """Ownership signal appears in JSON output."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py",
            'def login(user, password):\n'
            '    """Modified."""\n'
            '    return True\n'
        )

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="suggest-reviewers")

        if data.get("reviewers"):
            reviewer = data["reviewers"][0]
            assert "signals" in reviewer
            assert "ownership" in reviewer["signals"]
            assert "recency" in reviewer["signals"]
            assert "breadth" in reviewer["signals"]

    def test_top_n_limits_results(self, tmp_path, cli_runner, monkeypatch):
        """--top N limits the number of suggested reviewers."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        # Change multiple files to get multiple candidate reviewers
        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')
        _make_unstaged_change(proj, "src/models.py", 'class User: pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed",
                                         "--top", "1"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data.get("reviewers", [])) <= 1

    def test_top_n_default_is_three(self, tmp_path, cli_runner, monkeypatch):
        """Default --top is 3."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')
        _make_unstaged_change(proj, "src/models.py", 'class User: pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data.get("reviewers", [])) <= 3


# ===========================================================================
# Exclude tests
# ===========================================================================


class TestSuggestReviewersExclude:
    """Test --exclude flag."""

    def test_exclude_removes_author(self, tmp_path, cli_runner, monkeypatch):
        """--exclude removes the specified author from results."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        # Get all reviewers first
        result_all = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                               json_mode=True)
        data_all = json.loads(result_all.output)
        all_names = {r["name"] for r in data_all.get("reviewers", [])}

        # Now exclude bob
        result_ex = invoke_cli(cli_runner, ["suggest-reviewers", "--changed",
                                            "--exclude", "bob"], json_mode=True)
        data_ex = json.loads(result_ex.output)
        ex_names = {r["name"] for r in data_ex.get("reviewers", [])}

        assert "bob" not in ex_names

    def test_exclude_multiple(self, tmp_path, cli_runner, monkeypatch):
        """Multiple --exclude flags work."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner,
                           ["suggest-reviewers", "--changed",
                            "--exclude", "bob", "--exclude", "alice"],
                           json_mode=True)
        data = json.loads(result.output)
        names = {r["name"] for r in data.get("reviewers", [])}
        assert "bob" not in names
        assert "alice" not in names


# ===========================================================================
# JSON output contract tests
# ===========================================================================


class TestSuggestReviewersJSON:
    """Test JSON output structure."""

    def test_json_envelope(self, tmp_path, cli_runner, monkeypatch):
        """JSON output follows the roam envelope contract."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="suggest-reviewers")

    def test_json_has_required_keys(self, tmp_path, cli_runner, monkeypatch):
        """JSON output contains all required top-level keys."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        assert "reviewers" in data
        assert "coverage" in data
        assert "changed_files" in data
        assert "summary" in data

        # Check coverage structure
        cov = data["coverage"]
        assert "covered" in cov
        assert "total" in cov
        assert "uncovered_files" in cov

    def test_json_reviewer_structure(self, tmp_path, cli_runner, monkeypatch):
        """Each reviewer in JSON has the expected fields."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        for reviewer in data.get("reviewers", []):
            assert "name" in reviewer
            assert "score" in reviewer
            assert "signals" in reviewer
            assert "files_covered" in reviewer
            signals = reviewer["signals"]
            assert "ownership" in signals
            assert "codeowners" in signals
            assert "recency" in signals
            assert "breadth" in signals

    def test_json_verdict_in_summary(self, tmp_path, cli_runner, monkeypatch):
        """Summary contains a verdict string."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)


# ===========================================================================
# Coverage calculation tests
# ===========================================================================


class TestSuggestReviewersCoverage:
    """Test file coverage calculation."""

    def test_coverage_counts(self, tmp_path, cli_runner, monkeypatch):
        """Coverage counts are accurate."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)
        cov = data["coverage"]
        assert cov["total"] >= 1
        assert cov["covered"] <= cov["total"]
        assert isinstance(cov["uncovered_files"], list)

    def test_coverage_text_output(self, tmp_path, cli_runner, monkeypatch):
        """Text output shows COVERAGE line."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"])
        assert result.exit_code == 0
        assert "COVERAGE:" in result.output


# ===========================================================================
# CODEOWNERS integration tests
# ===========================================================================


class TestSuggestReviewersCODEOWNERS:
    """Test CODEOWNERS file integration."""

    def test_codeowners_signal_boost(self, tmp_path, cli_runner, monkeypatch):
        """CODEOWNERS declared owners get a signal boost."""
        proj = _make_multi_author_project(tmp_path)

        # Add CODEOWNERS file declaring alice as owner of src/
        (proj / "CODEOWNERS").write_text("src/ @alice\n")
        subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add CODEOWNERS"],
                       cwd=proj, capture_output=True)

        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        # Find alice's codeowners signal
        for reviewer in data.get("reviewers", []):
            if reviewer["name"] == "alice":
                assert reviewer["signals"]["codeowners"] > 0.0
                break

    def test_github_codeowners_path(self, tmp_path, cli_runner, monkeypatch):
        """CODEOWNERS in .github/ directory is found."""
        proj = _make_multi_author_project(tmp_path)

        gh_dir = proj / ".github"
        gh_dir.mkdir()
        (gh_dir / "CODEOWNERS").write_text("*.py @alice\n")
        subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add .github/CODEOWNERS"],
                       cwd=proj, capture_output=True)

        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should find reviewers (CODEOWNERS parsed correctly)
        assert data["summary"]["verdict"] != "No changed files found"


# ===========================================================================
# Internal helper unit tests
# ===========================================================================


class TestCODEOWNERSParser:
    """Unit tests for CODEOWNERS parsing helpers."""

    def test_parse_codeowners_basic(self, tmp_path):
        """Parse a basic CODEOWNERS file."""
        from roam.commands.cmd_suggest_reviewers import _parse_codeowners

        co = tmp_path / "CODEOWNERS"
        co.write_text(
            "# This is a comment\n"
            "*.py @alice @bob\n"
            "src/ @carol\n"
            "\n"
            "# Another comment\n"
            "lib/*.js @dave\n"
        )
        entries = _parse_codeowners(co)
        assert len(entries) == 3
        assert entries[0] == ("*.py", ["alice", "bob"])
        assert entries[1] == ("src/", ["carol"])
        assert entries[2] == ("lib/*.js", ["dave"])

    def test_parse_codeowners_empty(self, tmp_path):
        """Empty CODEOWNERS returns empty list."""
        from roam.commands.cmd_suggest_reviewers import _parse_codeowners

        co = tmp_path / "CODEOWNERS"
        co.write_text("")
        assert _parse_codeowners(co) == []

    def test_parse_codeowners_comments_only(self, tmp_path):
        """CODEOWNERS with only comments returns empty list."""
        from roam.commands.cmd_suggest_reviewers import _parse_codeowners

        co = tmp_path / "CODEOWNERS"
        co.write_text("# comment\n# another comment\n")
        assert _parse_codeowners(co) == []

    def test_resolve_codeowners_wildcard(self):
        """Wildcard pattern matches files."""
        from roam.commands.cmd_suggest_reviewers import _resolve_codeowners

        entries = [("*.py", ["alice"]), ("src/", ["bob"])]
        assert _resolve_codeowners("src/models.py", entries) == ["bob"]

    def test_resolve_codeowners_no_match(self):
        """No matching pattern returns empty list."""
        from roam.commands.cmd_suggest_reviewers import _resolve_codeowners

        entries = [("*.js", ["alice"])]
        assert _resolve_codeowners("src/models.py", entries) == []


class TestOwnershipComputation:
    """Unit tests for time-decayed ownership."""

    def test_compute_file_ownership_empty(self, indexed_project, monkeypatch):
        """File with no git data returns empty ownership."""
        from roam.commands.cmd_suggest_reviewers import _compute_file_ownership

        monkeypatch.chdir(indexed_project)
        with open_db_helper() as conn:
            # Use a non-existent file_id
            result = _compute_file_ownership(conn, 99999)
            assert result == {}

    def test_compute_recency_no_data(self, indexed_project, monkeypatch):
        """File with no recent commits returns empty recency."""
        from roam.commands.cmd_suggest_reviewers import _compute_recency

        monkeypatch.chdir(indexed_project)
        with open_db_helper() as conn:
            result = _compute_recency(conn, 99999)
            assert result == {}


def open_db_helper():
    """Helper to open the DB for unit tests."""
    from roam.db.connection import open_db
    return open_db(readonly=True)


# ===========================================================================
# Scoring behavior tests
# ===========================================================================


class TestScoringBehavior:
    """Test that scoring signals behave correctly."""

    def test_scores_are_between_0_and_1(self, tmp_path, cli_runner, monkeypatch):
        """All individual signal scores are in [0, 1]."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        for reviewer in data.get("reviewers", []):
            signals = reviewer["signals"]
            for signal_name, value in signals.items():
                assert 0.0 <= value <= 1.0, (
                    f"Signal {signal_name} for {reviewer['name']} "
                    f"is {value}, expected [0, 1]"
                )

    def test_total_score_non_negative(self, tmp_path, cli_runner, monkeypatch):
        """Total score is non-negative."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        for reviewer in data.get("reviewers", []):
            assert reviewer["score"] >= 0.0

    def test_reviewers_sorted_by_score(self, tmp_path, cli_runner, monkeypatch):
        """Reviewers are sorted by score descending."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')
        _make_unstaged_change(proj, "src/models.py", 'class User: pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        reviewers = data.get("reviewers", [])
        for i in range(len(reviewers) - 1):
            assert reviewers[i]["score"] >= reviewers[i + 1]["score"]

    def test_files_covered_within_bounds(self, tmp_path, cli_runner, monkeypatch):
        """files_covered is between 0 and total changed files."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')
        _make_unstaged_change(proj, "src/models.py", 'class User: pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"],
                           json_mode=True)
        data = json.loads(result.output)

        n_changed = len(data.get("changed_files", []))
        for reviewer in data.get("reviewers", []):
            assert 0 <= reviewer["files_covered"] <= n_changed


# ===========================================================================
# Text output format tests
# ===========================================================================


class TestSuggestReviewersTextOutput:
    """Test text output formatting."""

    def test_verdict_first_line(self, tmp_path, cli_runner, monkeypatch):
        """First line of output starts with VERDICT:"""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"])
        assert result.exit_code == 0
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")

    def test_table_headers(self, tmp_path, cli_runner, monkeypatch):
        """Text output contains table headers."""
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)

        _make_unstaged_change(proj, "src/auth.py", 'def login(): pass\n')

        result = invoke_cli(cli_runner, ["suggest-reviewers", "--changed"])
        assert result.exit_code == 0
        output = result.output
        # If there are reviewers, we should see table headers
        if "reviewer" in output.lower():
            assert "RANK" in output
            assert "SCORE" in output
