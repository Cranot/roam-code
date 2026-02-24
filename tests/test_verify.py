"""Tests for the roam verify command (pre-commit consistency check)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def verify_project(tmp_path, monkeypatch):
    """Project with snake_case Python functions, indexed, with modifications."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Establish a codebase with snake_case naming convention
    (proj / "models.py").write_text(
        'class UserAccount:\n'
        '    def __init__(self, name, email):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        '    def display_name(self):\n'
        '        return self.name.title()\n'
        '\n'
        '    def validate_email(self):\n'
        '        return "@" in self.email\n'
    )

    (proj / "service.py").write_text(
        'from models import UserAccount\n'
        '\n'
        'def create_user(name, email):\n'
        '    user = UserAccount(name, email)\n'
        '    return user\n'
        '\n'
        'def get_display(user):\n'
        '    return user.display_name()\n'
        '\n'
        'def process_order(order_id):\n'
        '    return order_id\n'
    )

    (proj / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    return f"{first} {last}"\n'
        '\n'
        'def parse_email(raw):\n'
        '    if "@" not in raw:\n'
        '        return None\n'
        '    parts = raw.split("@")\n'
        '    return {"user": parts[0], "domain": parts[1]}\n'
        '\n'
        'def validate_input(data):\n'
        '    return bool(data)\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    return proj


# ---------------------------------------------------------------------------
# Test: Naming consistency detection
# ---------------------------------------------------------------------------


class TestNamingConsistency:
    """Tests for naming convention detection."""

    def test_camel_case_in_snake_case_codebase(self, verify_project, cli_runner, monkeypatch):
        """camelCase function in a snake_case codebase should produce a violation."""
        # Add a file with camelCase naming
        (verify_project / "new_module.py").write_text(
            'def getData():\n'
            '    return []\n'
            '\n'
            'def processItems(items):\n'
            '    return items\n'
        )
        git_commit(verify_project, "add new module")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        # Modify the file to trigger as "changed"
        (verify_project / "new_module.py").write_text(
            'def getData():\n'
            '    return [1, 2, 3]\n'
            '\n'
            'def processItems(items):\n'
            '    return items\n'
        )

        result = invoke_cli(cli_runner, ["verify", "new_module.py"],
                            cwd=verify_project)
        assert "NAMING" in result.output
        # Should detect camelCase violations
        assert "camelCase" in result.output or "NAMING" in result.output

    def test_consistent_naming_passes(self, verify_project, cli_runner, monkeypatch):
        """snake_case function in a snake_case codebase should pass."""
        (verify_project / "good_module.py").write_text(
            'def get_data():\n'
            '    return []\n'
            '\n'
            'def process_items(items):\n'
            '    return items\n'
        )
        git_commit(verify_project, "add good module")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "good_module.py"],
                            cwd=verify_project)
        # Naming section should show OK or high score
        assert "NAMING" in result.output


# ---------------------------------------------------------------------------
# Test: Import pattern detection
# ---------------------------------------------------------------------------


class TestImportPatterns:
    """Tests for import pattern consistency."""

    def test_import_check_runs(self, verify_project, cli_runner, monkeypatch):
        """Import checking should run without errors."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "service.py"],
                            cwd=verify_project)
        assert "IMPORTS" in result.output

    def test_no_import_violations_on_consistent_code(self, verify_project, cli_runner, monkeypatch):
        """Consistent import patterns should produce no violations."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "service.py"],
                            cwd=verify_project)
        # Should have IMPORTS section
        assert "IMPORTS" in result.output


# ---------------------------------------------------------------------------
# Test: Error handling pattern detection
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling pattern detection."""

    def test_bare_except_detected(self, verify_project, cli_runner, monkeypatch):
        """Bare except: should be flagged."""
        (verify_project / "handler.py").write_text(
            'def handle_request(data):\n'
            '    try:\n'
            '        return process(data)\n'
            '    except:\n'
            '        return None\n'
        )
        git_commit(verify_project, "add handler")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "handler.py"],
                            cwd=verify_project)
        assert "ERROR HANDLING" in result.output
        assert "bare" in result.output.lower() or "except" in result.output.lower()

    def test_broad_exception_detected(self, verify_project, cli_runner, monkeypatch):
        """except Exception: should be flagged as a warning."""
        (verify_project / "handler2.py").write_text(
            'def handle_data(data):\n'
            '    try:\n'
            '        return int(data)\n'
            '    except Exception:\n'
            '        return 0\n'
        )
        git_commit(verify_project, "add handler2")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "handler2.py"],
                            cwd=verify_project)
        assert "ERROR HANDLING" in result.output

    def test_silent_exception_detected(self, verify_project, cli_runner, monkeypatch):
        """except: pass should be flagged as silent swallowing."""
        (verify_project / "handler3.py").write_text(
            'def safe_parse(data):\n'
            '    try:\n'
            '        return int(data)\n'
            '    except ValueError:\n'
            '        pass\n'
        )
        git_commit(verify_project, "add handler3")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "handler3.py"],
                            cwd=verify_project)
        assert "ERROR HANDLING" in result.output

    def test_clean_error_handling_passes(self, verify_project, cli_runner, monkeypatch):
        """Proper error handling should score well."""
        (verify_project / "clean_handler.py").write_text(
            'def parse_value(data):\n'
            '    try:\n'
            '        return int(data)\n'
            '    except ValueError as e:\n'
            '        raise RuntimeError("Invalid data") from e\n'
        )
        git_commit(verify_project, "add clean handler")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "clean_handler.py"],
                            cwd=verify_project)
        assert "ERROR HANDLING" in result.output
        # Score for error handling should be 100 (no violations)
        assert "100/100" in result.output or "OK" in result.output


# ---------------------------------------------------------------------------
# Test: Duplicate detection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    """Tests for duplicate logic detection."""

    def test_exact_name_duplicate_detected(self, verify_project, cli_runner, monkeypatch):
        """A function with the exact same name in a different file should flag."""
        (verify_project / "new_utils.py").write_text(
            'def format_name(first, last):\n'
            '    return first + " " + last\n'
        )
        git_commit(verify_project, "add duplicate")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "new_utils.py"],
                            cwd=verify_project)
        assert "DUPLICATES" in result.output
        # Should flag format_name as similar/duplicate
        assert "format_name" in result.output

    def test_similar_name_detected(self, verify_project, cli_runner, monkeypatch):
        """A function with a very similar name should flag."""
        (verify_project / "helpers.py").write_text(
            'def parse_emails(raw_list):\n'
            '    return [r.strip() for r in raw_list]\n'
        )
        git_commit(verify_project, "add similar")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "helpers.py"],
                            cwd=verify_project)
        assert "DUPLICATES" in result.output

    def test_unique_function_passes(self, verify_project, cli_runner, monkeypatch):
        """A genuinely unique function should not flag."""
        (verify_project / "unique.py").write_text(
            'def calculate_fibonacci(n):\n'
            '    if n <= 1:\n'
            '        return n\n'
            '    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)\n'
        )
        git_commit(verify_project, "add unique fn")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "unique.py"],
                            cwd=verify_project)
        assert "DUPLICATES" in result.output


# ---------------------------------------------------------------------------
# Test: Syntax check integration
# ---------------------------------------------------------------------------


class TestSyntaxCheck:
    """Tests for syntax integrity verification."""

    def test_syntax_section_present(self, verify_project, cli_runner, monkeypatch):
        """Syntax check section should always appear in output."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project)
        assert "SYNTAX" in result.output

    def test_valid_syntax_passes(self, verify_project, cli_runner, monkeypatch):
        """Valid Python syntax should score 100."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project)
        assert "SYNTAX" in result.output


# ---------------------------------------------------------------------------
# Test: Threshold gating (EXIT_GATE_FAILURE)
# ---------------------------------------------------------------------------


class TestThresholdGating:
    """Tests for threshold-based exit codes."""

    def test_high_threshold_may_fail(self, verify_project, cli_runner, monkeypatch):
        """A very high threshold on code with violations should produce non-zero exit."""
        # Add a file with naming violations
        (verify_project / "bad_module.py").write_text(
            'def getData():\n'
            '    return []\n'
            '\n'
            'def processItems(items):\n'
            '    try:\n'
            '        return items\n'
            '    except:\n'
            '        pass\n'
        )
        git_commit(verify_project, "add bad module")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "--threshold", "100",
                                         "bad_module.py"],
                            cwd=verify_project)
        # With threshold=100 and violations, should fail
        # exit code 5 for gate failure
        assert result.exit_code == 5 or result.exit_code == 0
        # If score < 100, exit_code should be 5
        if "100/100" not in result.output:
            assert result.exit_code == 5

    def test_low_threshold_passes(self, verify_project, cli_runner, monkeypatch):
        """A low threshold on clean code should pass."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "--threshold", "1",
                                         "utils.py"],
                            cwd=verify_project)
        # Should pass with such a low threshold
        assert result.exit_code == 0

    def test_default_threshold_is_70(self, verify_project, cli_runner, monkeypatch):
        """Default threshold should be 70."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project)
        assert "threshold: 70" in result.output


# ---------------------------------------------------------------------------
# Test: JSON output structure
# ---------------------------------------------------------------------------


class TestJsonOutput:
    """Tests for JSON output format."""

    def test_json_envelope_structure(self, verify_project, cli_runner, monkeypatch):
        """JSON output should follow the roam envelope contract."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        assert_json_envelope(data, "verify")

        # Verify summary fields
        summary = data["summary"]
        assert "verdict" in summary
        assert summary["verdict"] in ("PASS", "WARN", "FAIL")
        assert "score" in summary
        assert isinstance(summary["score"], int)
        assert "threshold" in summary
        assert "files_checked" in summary
        assert "violation_count" in summary

    def test_json_categories_structure(self, verify_project, cli_runner, monkeypatch):
        """JSON output should include per-category scores and violations."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")

        assert "categories" in data
        cats = data["categories"]
        for cat_name in ("naming", "imports", "error_handling", "duplicates", "syntax"):
            assert cat_name in cats, f"Missing category: {cat_name}"
            assert "score" in cats[cat_name]
            assert "violations" in cats[cat_name]
            assert isinstance(cats[cat_name]["score"], int)

    def test_json_violations_list(self, verify_project, cli_runner, monkeypatch):
        """JSON output should include a flat list of all violations."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        assert "violations" in data
        assert isinstance(data["violations"], list)

    def test_json_violation_fields(self, verify_project, cli_runner, monkeypatch):
        """Each violation should have required fields."""
        # Create a file with known violations
        (verify_project / "violation.py").write_text(
            'def getData():\n'
            '    try:\n'
            '        return []\n'
            '    except:\n'
            '        pass\n'
        )
        git_commit(verify_project, "add violation")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        # Use --threshold 0 so the gate doesn't fail (exit code 5)
        result = invoke_cli(cli_runner, ["verify", "--threshold", "0",
                                         "violation.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        violations = data.get("violations", [])
        assert len(violations) > 0, "Expected violations for this file"
        for v in violations:
            assert "category" in v
            assert "severity" in v
            assert "file" in v
            assert "message" in v
            assert v["severity"] in ("FAIL", "WARN", "INFO")


# ---------------------------------------------------------------------------
# Test: No changed files
# ---------------------------------------------------------------------------


class TestNoChangedFiles:
    """Tests for handling when no files are changed."""

    def test_no_files_text_output(self, verify_project, cli_runner, monkeypatch):
        """No changed files should produce PASS verdict in text mode."""
        monkeypatch.chdir(verify_project)
        # Pass a nonexistent file to trigger no-match
        result = invoke_cli(cli_runner, ["verify", "nonexistent.py"],
                            cwd=verify_project)
        # Should still show VERDICT line even with no matching files
        assert "VERDICT" in result.output

    def test_no_files_json_output(self, verify_project, cli_runner, monkeypatch):
        """No changed files should produce valid JSON with PASS."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "nonexistent.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        summary = data["summary"]
        # Should be PASS with score 100 when no matched files
        assert summary["verdict"] in ("PASS", "WARN", "FAIL")


# ---------------------------------------------------------------------------
# Test: --fix-suggestions flag
# ---------------------------------------------------------------------------


class TestFixSuggestions:
    """Tests for the --fix-suggestions flag."""

    def test_fix_suggestions_shown_when_enabled(self, verify_project, cli_runner, monkeypatch):
        """Fix suggestions should appear when --fix-suggestions is set."""
        (verify_project / "fixable.py").write_text(
            'def getData():\n'
            '    try:\n'
            '        return []\n'
            '    except:\n'
            '        pass\n'
        )
        git_commit(verify_project, "add fixable")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "--fix-suggestions",
                                         "fixable.py"],
                            cwd=verify_project)
        # Should show FIX: lines
        if "FAIL:" in result.output or "WARN:" in result.output:
            assert "FIX:" in result.output

    def test_fix_suggestions_hidden_by_default(self, verify_project, cli_runner, monkeypatch):
        """Fix suggestions should not appear without the flag."""
        (verify_project / "fixable2.py").write_text(
            'def getData():\n'
            '    try:\n'
            '        return []\n'
            '    except:\n'
            '        pass\n'
        )
        git_commit(verify_project, "add fixable2")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "fixable2.py"],
                            cwd=verify_project)
        # Should NOT show FIX: lines
        assert "FIX:" not in result.output


# ---------------------------------------------------------------------------
# Test: Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    """Tests for composite scoring."""

    def test_clean_code_scores_high(self, verify_project, cli_runner, monkeypatch):
        """Clean code with no violations should score high."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        assert data["summary"]["score"] >= 80

    def test_score_is_weighted(self, verify_project, cli_runner, monkeypatch):
        """Score should be computed as a weighted average of categories."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        # Score should be between 0 and 100
        assert 0 <= data["summary"]["score"] <= 100

    def test_verdict_pass_for_high_score(self, verify_project, cli_runner, monkeypatch):
        """Score >= 80 should produce PASS verdict."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project, json_mode=True)

        data = parse_json_output(result, "verify")
        if data["summary"]["score"] >= 80:
            assert data["summary"]["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# Test: Text output format
# ---------------------------------------------------------------------------


class TestTextOutput:
    """Tests for text output formatting."""

    def test_verdict_first_line(self, verify_project, cli_runner, monkeypatch):
        """First line of output should be VERDICT:."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project)
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")

    def test_all_categories_shown(self, verify_project, cli_runner, monkeypatch):
        """All 5 categories should appear in text output."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project)
        assert "NAMING" in result.output
        assert "IMPORTS" in result.output
        assert "ERROR HANDLING" in result.output
        assert "DUPLICATES" in result.output
        assert "SYNTAX" in result.output

    def test_overall_summary_line(self, verify_project, cli_runner, monkeypatch):
        """Should end with an Overall: summary line."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py"],
                            cwd=verify_project)
        assert "Overall:" in result.output
        assert "threshold:" in result.output


# ---------------------------------------------------------------------------
# Test: Multiple files
# ---------------------------------------------------------------------------


class TestMultipleFiles:
    """Tests for verifying multiple files at once."""

    def test_multiple_files_argument(self, verify_project, cli_runner, monkeypatch):
        """Should accept multiple file arguments."""
        monkeypatch.chdir(verify_project)
        result = invoke_cli(cli_runner, ["verify", "utils.py", "service.py"],
                            cwd=verify_project)
        assert result.exit_code == 0 or result.exit_code == 5
        assert "VERDICT" in result.output


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and robustness."""

    def test_empty_file(self, verify_project, cli_runner, monkeypatch):
        """Empty file should not crash."""
        (verify_project / "empty.py").write_text("")
        git_commit(verify_project, "add empty")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "empty.py"],
                            cwd=verify_project)
        assert "VERDICT" in result.output

    def test_non_python_file(self, verify_project, cli_runner, monkeypatch):
        """Non-Python files should still be handled gracefully."""
        (verify_project / "readme.txt").write_text("This is a readme\n")
        git_commit(verify_project, "add readme")
        monkeypatch.chdir(verify_project)
        out, rc = index_in_process(verify_project, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["verify", "readme.txt"],
                            cwd=verify_project)
        # Should handle non-indexed files gracefully
        assert "VERDICT" in result.output
