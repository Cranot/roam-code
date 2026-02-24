"""Tests for the `roam --check` install verification flag.

This covers the eager --check option added to the main CLI group.
It validates Python version, tree-sitter, tree-sitter-language-pack,
git availability, and SQLite — then exits 0 (pass) or 1 (fail).
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from click.testing import CliRunner

from roam.cli import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner():
    """Return a CliRunner, using mix_stderr=False when available (Click < 8.2)."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def invoke_check(*extra_args):
    """Run `roam --check [extra_args]` via CliRunner and return the result."""
    runner = _make_runner()
    return runner.invoke(cli, ["--check", *extra_args], catch_exceptions=False)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestInstallCheckPass:
    """All checks pass in a normal dev environment."""

    def test_exit_code_zero(self):
        result = invoke_check()
        assert result.exit_code == 0, (
            f"Expected exit 0 but got {result.exit_code}.\n"
            f"Output: {result.output}"
        )

    def test_output_contains_ready(self):
        result = invoke_check()
        assert "roam-code ready" in result.output

    def test_output_is_single_line(self):
        """The success message is exactly one non-empty line."""
        result = invoke_check()
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert len(lines) == 1, f"Expected 1 line, got: {result.output!r}"

    def test_no_setup_incomplete_on_success(self):
        result = invoke_check()
        assert "setup incomplete" not in result.output


# ---------------------------------------------------------------------------
# Failure: missing tree-sitter
# ---------------------------------------------------------------------------


class TestInstallCheckMissingTreeSitter:

    def test_exit_code_one_when_tree_sitter_missing(self):
        with patch.dict(sys.modules, {"tree_sitter": None}):
            result = invoke_check()
        assert result.exit_code == 1

    def test_output_shows_setup_incomplete(self):
        with patch.dict(sys.modules, {"tree_sitter": None}):
            result = invoke_check()
        assert "setup incomplete" in result.output

    def test_output_mentions_tree_sitter(self):
        with patch.dict(sys.modules, {"tree_sitter": None}):
            result = invoke_check()
        assert "tree-sitter" in result.output

    def test_no_ready_when_tree_sitter_missing(self):
        with patch.dict(sys.modules, {"tree_sitter": None}):
            result = invoke_check()
        assert "roam-code ready" not in result.output


# ---------------------------------------------------------------------------
# Failure: missing tree-sitter-language-pack
# ---------------------------------------------------------------------------


class TestInstallCheckMissingLanguagePack:

    def test_exit_code_one_when_language_pack_missing(self):
        with patch.dict(sys.modules, {"tree_sitter_language_pack": None}):
            result = invoke_check()
        assert result.exit_code == 1

    def test_output_mentions_language_pack(self):
        with patch.dict(sys.modules, {"tree_sitter_language_pack": None}):
            result = invoke_check()
        assert "tree-sitter-language-pack" in result.output

    def test_output_shows_setup_incomplete(self):
        with patch.dict(sys.modules, {"tree_sitter_language_pack": None}):
            result = invoke_check()
        assert "setup incomplete" in result.output


# ---------------------------------------------------------------------------
# Failure: git not on PATH
# ---------------------------------------------------------------------------


class TestInstallCheckMissingGit:

    def test_exit_code_one_when_git_missing(self):
        with patch("shutil.which", return_value=None):
            result = invoke_check()
        assert result.exit_code == 1

    def test_output_mentions_git(self):
        with patch("shutil.which", return_value=None):
            result = invoke_check()
        assert "git" in result.output

    def test_output_shows_setup_incomplete(self):
        with patch("shutil.which", return_value=None):
            result = invoke_check()
        assert "setup incomplete" in result.output

    def test_no_ready_when_git_missing(self):
        with patch("shutil.which", return_value=None):
            result = invoke_check()
        assert "roam-code ready" not in result.output


# ---------------------------------------------------------------------------
# Failure: multiple issues accumulate into a single message
# ---------------------------------------------------------------------------


class TestInstallCheckMultipleFailures:

    def test_multiple_issues_in_single_line(self):
        """Both missing tree-sitter and git show up in the same output line."""
        with patch.dict(sys.modules, {"tree_sitter": None}):
            with patch("shutil.which", return_value=None):
                result = invoke_check()
        assert result.exit_code == 1
        assert "tree-sitter" in result.output
        assert "git" in result.output
        # Only one line of output (single compound message)
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert len(lines) == 1

    def test_issues_separated_by_semicolon(self):
        """Multiple issues are joined with '; '."""
        with patch.dict(sys.modules, {"tree_sitter": None}):
            with patch("shutil.which", return_value=None):
                result = invoke_check()
        assert ";" in result.output


# ---------------------------------------------------------------------------
# Eager behaviour: --check consumes the invocation and exits immediately
# ---------------------------------------------------------------------------


class TestInstallCheckEagerBehaviour:

    def test_check_exits_before_subcommand_processing(self):
        """--check placed before a subcommand still exits before running the command."""
        runner = _make_runner()
        result = runner.invoke(cli, ["--check", "health"], catch_exceptions=False)
        # Should exit cleanly (0) without attempting to run health (which needs an index)
        assert result.exit_code == 0
        assert "roam-code ready" in result.output

    def test_check_works_with_other_flags(self):
        """--check alongside --json doesn't crash."""
        runner = _make_runner()
        result = runner.invoke(cli, ["--check", "--json"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "roam-code ready" in result.output

    def test_check_works_with_version_flag(self):
        """--check can coexist with --version (whichever is processed first wins)."""
        runner = _make_runner()
        # --version is also eager; Click processes eager options left-to-right,
        # so --check fires first when listed first.
        result = runner.invoke(cli, ["--check", "--version"], catch_exceptions=False)
        # Either --check or --version may win, but there should be no crash.
        assert result.exit_code == 0

    def test_check_not_present_does_nothing(self):
        """Omitting --check does not print the check message."""
        runner = _make_runner()
        result = runner.invoke(cli, ["--help"], catch_exceptions=False)
        assert "roam-code ready" not in result.output
        assert "setup incomplete" not in result.output


# ---------------------------------------------------------------------------
# Option registration on the CLI group
# ---------------------------------------------------------------------------


class TestInstallCheckHelpText:

    def test_check_registered_as_cli_param(self):
        """--check is registered as a parameter on the main CLI group.

        The custom LazyGroup.format_help does not render the options section,
        so we verify registration directly via cli.params instead of help text.
        """
        param_names = [p.name for p in cli.params]
        assert "check" in param_names, (
            f"--check not found in cli.params: {param_names}"
        )

    def test_check_is_eager_flag(self):
        """--check option must be marked eager so it fires before subcommands."""
        for param in cli.params:
            if param.name == "check":
                assert param.is_eager, "--check must be marked is_eager=True"
                assert param.is_flag, "--check must be a flag"
                break
        else:
            raise AssertionError("--check param not found on cli group")

    def test_check_help_text_present(self):
        """The --check option carries a non-empty help string."""
        for param in cli.params:
            if param.name == "check":
                assert param.help, "--check should have a help string"
                assert len(param.help) > 5
                break
        else:
            raise AssertionError("--check param not found on cli group")


# ---------------------------------------------------------------------------
# Output format: plain text only, no JSON wrapper
# ---------------------------------------------------------------------------


class TestInstallCheckOutputFormat:

    def test_output_is_plain_text_not_json(self):
        """--check always outputs plain text, even if --json is also passed."""
        runner = _make_runner()
        result = runner.invoke(cli, ["--check", "--json"], catch_exceptions=False)
        # Should NOT be a JSON envelope
        assert not result.output.strip().startswith("{")
        assert "roam-code ready" in result.output

    def test_failure_output_is_plain_text(self):
        """Failure message is also plain text."""
        with patch("shutil.which", return_value=None):
            runner = _make_runner()
            result = runner.invoke(cli, ["--check"], catch_exceptions=False)
        assert not result.output.strip().startswith("{")
        assert "setup incomplete" in result.output
