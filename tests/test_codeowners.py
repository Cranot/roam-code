"""Tests for roam codeowners command — CODEOWNERS parsing, pattern matching, CLI output."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseCodeowners:
    """Test CODEOWNERS file parsing."""

    def test_simple_rules(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @backend-team\n*.js @frontend-team\n")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 2
        assert rules[0] == ("*.py", ["@backend-team"])
        assert rules[1] == ("*.js", ["@frontend-team"])

    def test_comments_and_blank_lines(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text(
            "# This is a comment\n"
            "\n"
            "*.py @alice\n"
            "# Another comment\n"
            "\n"
            "*.go @bob\n"
        )
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 2
        assert rules[0] == ("*.py", ["@alice"])
        assert rules[1] == ("*.go", ["@bob"])

    def test_multiple_owners(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @alice @bob @team-backend\n")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 1
        assert rules[0] == ("*.py", ["@alice", "@bob", "@team-backend"])

    def test_inline_comments(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @alice # Python files\n")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 1
        assert rules[0] == ("*.py", ["@alice"])

    def test_directory_pattern(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("/src/ @backend-team\n/docs/ @docs-team\n")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 2
        assert rules[0] == ("/src/", ["@backend-team"])

    def test_doublestar_pattern(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("src/**/*.py @backend-team\n")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 1
        assert rules[0] == ("src/**/*.py", ["@backend-team"])

    def test_pattern_with_no_owner(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @alice\n*.test.py\n")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 2
        assert rules[1] == ("*.test.py", [])

    def test_nonexistent_file(self, tmp_path):
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(tmp_path / "nonexistent"))
        assert rules == []

    def test_empty_file(self, tmp_path):
        co = tmp_path / "CODEOWNERS"
        co.write_text("")
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert rules == []


# ---------------------------------------------------------------------------
# Pattern matching tests
# ---------------------------------------------------------------------------


class TestPatternMatching:
    """Test gitignore-style pattern matching for CODEOWNERS."""

    def test_wildcard_extension(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("*.py", "src/models.py")
        assert _codeowners_match("*.py", "models.py")
        assert not _codeowners_match("*.py", "src/models.js")

    def test_anchored_directory(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("/src/", "src/models.py")
        assert _codeowners_match("/src/", "src/sub/models.py")
        assert not _codeowners_match("/src/", "lib/src/models.py")

    def test_unanchored_directory(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("docs/", "docs/readme.md")
        assert _codeowners_match("docs/", "src/docs/readme.md")

    def test_anchored_file_pattern(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("/Makefile", "Makefile")
        assert not _codeowners_match("/Makefile", "src/Makefile")

    def test_unanchored_basename(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("Makefile", "Makefile")
        assert _codeowners_match("Makefile", "src/Makefile")

    def test_doublestar_middle(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("src/**/*.py", "src/models.py")
        assert _codeowners_match("src/**/*.py", "src/sub/deep/models.py")
        assert not _codeowners_match("src/**/*.py", "lib/models.py")

    def test_doublestar_leading(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("**/*.py", "src/models.py")
        assert _codeowners_match("**/*.py", "models.py")

    def test_path_with_slash(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("src/models.py", "src/models.py")
        assert not _codeowners_match("src/models.py", "lib/models.py")

    def test_question_mark(self):
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("*.p?", "foo.py")
        assert _codeowners_match("*.p?", "foo.pl")
        assert not _codeowners_match("*.p?", "foo.java")


# ---------------------------------------------------------------------------
# Last match wins
# ---------------------------------------------------------------------------


class TestResolveOwners:
    """Test that last matching rule wins."""

    def test_last_match_wins(self):
        from roam.commands.cmd_codeowners import resolve_owners

        rules = [
            ("*", ["@default-team"]),
            ("*.py", ["@python-team"]),
            ("/src/", ["@src-team"]),
        ]
        # src/models.py matches all three; last match wins
        owners = resolve_owners(rules, "src/models.py")
        assert owners == ["@src-team"]

    def test_no_match_returns_empty(self):
        from roam.commands.cmd_codeowners import resolve_owners

        rules = [("*.py", ["@python-team"])]
        owners = resolve_owners(rules, "readme.md")
        assert owners == []

    def test_explicit_unown(self):
        from roam.commands.cmd_codeowners import resolve_owners

        rules = [
            ("*", ["@default-team"]),
            ("*.generated.py", []),  # explicitly unowned
        ]
        owners = resolve_owners(rules, "foo.generated.py")
        assert owners == []

    def test_multiple_rules_override(self):
        from roam.commands.cmd_codeowners import resolve_owners

        rules = [
            ("*.py", ["@alice"]),
            ("*.py", ["@bob"]),
        ]
        owners = resolve_owners(rules, "app.py")
        assert owners == ["@bob"]


# ---------------------------------------------------------------------------
# find_codeowners
# ---------------------------------------------------------------------------


class TestFindCodeowners:
    """Test CODEOWNERS file discovery in standard locations."""

    def test_root_codeowners(self, tmp_path):
        (tmp_path / "CODEOWNERS").write_text("* @owner\n")
        from roam.commands.cmd_codeowners import find_codeowners

        result = find_codeowners(tmp_path)
        assert result is not None
        assert result.name == "CODEOWNERS"

    def test_github_codeowners(self, tmp_path):
        gh = tmp_path / ".github"
        gh.mkdir()
        (gh / "CODEOWNERS").write_text("* @owner\n")
        from roam.commands.cmd_codeowners import find_codeowners

        result = find_codeowners(tmp_path)
        assert result is not None
        assert ".github" in str(result)

    def test_docs_codeowners(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "CODEOWNERS").write_text("* @owner\n")
        from roam.commands.cmd_codeowners import find_codeowners

        result = find_codeowners(tmp_path)
        assert result is not None
        assert "docs" in str(result)

    def test_gitlab_codeowners(self, tmp_path):
        gl = tmp_path / ".gitlab"
        gl.mkdir()
        (gl / "CODEOWNERS").write_text("* @owner\n")
        from roam.commands.cmd_codeowners import find_codeowners

        result = find_codeowners(tmp_path)
        assert result is not None
        assert ".gitlab" in str(result)

    def test_no_codeowners(self, tmp_path):
        from roam.commands.cmd_codeowners import find_codeowners

        result = find_codeowners(tmp_path)
        assert result is None

    def test_priority_order(self, tmp_path):
        """Root CODEOWNERS takes priority over .github/CODEOWNERS."""
        (tmp_path / "CODEOWNERS").write_text("* @root-owner\n")
        gh = tmp_path / ".github"
        gh.mkdir()
        (gh / "CODEOWNERS").write_text("* @gh-owner\n")
        from roam.commands.cmd_codeowners import find_codeowners

        result = find_codeowners(tmp_path)
        assert result is not None
        assert result == tmp_path / "CODEOWNERS"


# ---------------------------------------------------------------------------
# Key areas helper
# ---------------------------------------------------------------------------


class TestKeyAreas:
    """Test key area extraction."""

    def test_key_areas(self):
        from roam.commands.cmd_codeowners import _key_areas

        paths = [
            "src/api/routes.py",
            "src/api/views.py",
            "src/api/models.py",
            "src/models/user.py",
        ]
        areas = _key_areas(paths)
        assert "src/api/" in areas
        assert len(areas) <= 3

    def test_single_file(self):
        from roam.commands.cmd_codeowners import _key_areas

        areas = _key_areas(["config.py"])
        assert areas == ["./"]


# ---------------------------------------------------------------------------
# CLI integration tests (with indexed project)
# ---------------------------------------------------------------------------


def _make_project_with_codeowners(tmp_path, codeowners_content, codeowners_loc=".github"):
    """Helper to create a project with CODEOWNERS and source files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")

    # Source files
    src = repo / "src"
    src.mkdir()
    (src / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
    )
    (src / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name):\n"
        "    return User(name)\n"
    )
    (src / "utils.py").write_text(
        "def helper():\n"
        "    return 42\n"
    )

    docs = repo / "docs"
    docs.mkdir()
    (docs / "readme.md").write_text("# Docs\n")

    # CODEOWNERS
    if codeowners_loc:
        co_dir = repo / codeowners_loc
        co_dir.mkdir(parents=True, exist_ok=True)
        (co_dir / "CODEOWNERS").write_text(codeowners_content)
    else:
        (repo / "CODEOWNERS").write_text(codeowners_content)

    git_init(repo)
    return repo


class TestCodeownersCommand:
    """Test the roam codeowners CLI command."""

    def test_no_codeowners_file(self, tmp_path, monkeypatch):
        """When no CODEOWNERS file exists, show a helpful message."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main():\n    pass\n")
        git_init(repo)
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No CODEOWNERS file found" in result.output

    def test_no_codeowners_json(self, tmp_path, monkeypatch):
        """JSON output when no CODEOWNERS file exists."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main():\n    pass\n")
        git_init(repo)
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "codeowners"
        assert data["summary"]["codeowners_found"] is False

    def test_full_report(self, tmp_path, monkeypatch):
        """Full report with CODEOWNERS file."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "/src/ @backend-team\n/docs/ @docs-team\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "coverage" in result.output.lower()

    def test_full_report_json(self, tmp_path, monkeypatch):
        """Full report JSON output."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "/src/ @backend-team\n/docs/ @docs-team\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "codeowners"
        assert "total_files" in data["summary"]
        assert "owned_files" in data["summary"]
        assert "coverage_pct" in data["summary"]
        assert "owners" in data
        assert "unowned" in data

    def test_unowned_flag(self, tmp_path, monkeypatch):
        """--unowned flag shows only unowned files."""
        # Only src/ is owned, so docs/ files and root files should be unowned
        repo = _make_project_with_codeowners(
            tmp_path,
            "/src/ @backend-team\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["codeowners", "--unowned"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "unowned" in result.output.lower()

    def test_unowned_json(self, tmp_path, monkeypatch):
        """--unowned JSON output."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "/src/ @backend-team\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "codeowners", "--unowned"], catch_exceptions=False
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "codeowners"
        assert "unowned_count" in data["summary"]
        assert "unowned" in data

    def test_owner_filter(self, tmp_path, monkeypatch):
        """--owner flag filters to a specific owner."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "/src/ @backend-team\n/docs/ @docs-team\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["codeowners", "--owner", "@backend-team"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "@backend-team" in result.output

    def test_owner_filter_json(self, tmp_path, monkeypatch):
        """--owner JSON output."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "/src/ @backend-team\n/docs/ @docs-team\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "codeowners", "--owner", "@backend-team"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "codeowners"
        assert data["summary"]["owner"] == "@backend-team"
        assert "files" in data

    def test_coverage_percentage(self, tmp_path, monkeypatch):
        """Coverage percentage is calculated correctly."""
        # Own everything with *
        repo = _make_project_with_codeowners(
            tmp_path,
            "* @everyone\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # 100% coverage since * matches everything
        assert data["summary"]["coverage_pct"] == 100.0

    def test_zero_coverage(self, tmp_path, monkeypatch):
        """No patterns match, 0% coverage."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "nonexistent_pattern_that_matches_nothing.xyz @nobody\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["coverage_pct"] == 0.0
        assert data["summary"]["owned_files"] == 0

    def test_root_codeowners_location(self, tmp_path, monkeypatch):
        """CODEOWNERS in repo root (not .github/)."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "* @owner\n",
            codeowners_loc="",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["coverage_pct"] == 100.0
        assert data["summary"]["codeowners_path"] == "CODEOWNERS"

    def test_owner_not_found(self, tmp_path, monkeypatch):
        """--owner with a non-existent owner."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "* @owner\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["codeowners", "--owner", "@nonexistent"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "No files found" in result.output or "0 files" in result.output

    def test_verdict_in_text_output(self, tmp_path, monkeypatch):
        """Text output starts with VERDICT: line."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "* @owner\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert lines[0].startswith("VERDICT:")

    def test_verdict_in_json_output(self, tmp_path, monkeypatch):
        """JSON summary includes verdict field."""
        repo = _make_project_with_codeowners(
            tmp_path,
            "* @owner\n",
        )
        monkeypatch.chdir(repo)
        index_in_process(repo)

        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "codeowners"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "verdict" in data["summary"]
        assert "coverage" in data["summary"]["verdict"].lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for CODEOWNERS parsing and matching."""

    def test_backslash_path_normalization(self):
        """Windows-style backslash paths are normalized."""
        from roam.commands.cmd_codeowners import _codeowners_match

        assert _codeowners_match("/src/", "src\\models.py")

    def test_empty_rules_list(self):
        """resolve_owners with no rules returns empty list."""
        from roam.commands.cmd_codeowners import resolve_owners

        assert resolve_owners([], "any/file.py") == []

    def test_complex_codeowners(self, tmp_path):
        """Parse a complex CODEOWNERS file with mixed patterns."""
        co = tmp_path / "CODEOWNERS"
        co.write_text(
            "# Default owner for everything\n"
            "*                 @global-owner\n"
            "\n"
            "# Frontend\n"
            "*.js              @frontend-team\n"
            "*.ts              @frontend-team @typescript-reviewers\n"
            "*.css             @frontend-team\n"
            "\n"
            "# Backend\n"
            "/src/api/         @backend-team\n"
            "/src/models/      @backend-team @data-team\n"
            "\n"
            "# Docs\n"
            "/docs/            @docs-team\n"
            "*.md              @docs-team\n"
            "\n"
            "# Generated code (explicitly unowned)\n"
            "/generated/\n"
        )
        from roam.commands.cmd_codeowners import parse_codeowners

        rules = parse_codeowners(str(co))
        assert len(rules) == 9  # 9 non-comment, non-blank lines

    def test_resolve_complex_rules(self):
        """Complex rule resolution with multiple overrides."""
        from roam.commands.cmd_codeowners import resolve_owners

        rules = [
            ("*", ["@global-owner"]),
            ("*.js", ["@frontend-team"]),
            ("/src/api/", ["@backend-team"]),
        ]
        # JS file in src/api/ => last match (/src/api/) wins
        assert resolve_owners(rules, "src/api/routes.js") == ["@backend-team"]
        # JS file elsewhere => *.js wins
        assert resolve_owners(rules, "frontend/app.js") == ["@frontend-team"]
        # .py file => * wins
        assert resolve_owners(rules, "lib/utils.py") == ["@global-owner"]
