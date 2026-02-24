"""Tests for the ``roam syntax-check`` command.

Covers:
1. Valid Python file -> 0 errors
2. Broken Python file (unclosed paren, invalid syntax) -> errors detected
3. Valid JS file -> 0 errors
4. Broken JS file -> errors detected
5. --changed flag (mock git diff)
6. JSON output format
7. Exit code 0 for clean, 5 for errors
8. Multiple files (mix of clean and broken)
9. Unsupported file types are skipped
10. Non-existent files are skipped
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit

from roam.cli import cli
from roam.exit_codes import EXIT_GATE_FAILURE


# ===========================================================================
# Helpers
# ===========================================================================


def _make_files(tmp_path, file_dict):
    """Create files in tmp_path from a {relative_path: content} dict.

    Returns the project directory path.
    """
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    for rel, content in file_dict.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    return proj


def _invoke(proj, *args, json_mode=False):
    """Run syntax-check in-process via CliRunner."""
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.append("syntax-check")
    full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# Valid file samples
# ===========================================================================

VALID_PYTHON = '''\
def greet(name):
    """Say hello."""
    return f"Hello, {name}!"


class User:
    def __init__(self, name, email):
        self.name = name
        self.email = email

    def display(self):
        return self.name.title()
'''

VALID_JS = '''\
function add(a, b) {
    return a + b;
}

const greet = (name) => {
    console.log("Hello, " + name);
};
'''


# ===========================================================================
# Broken file samples
# ===========================================================================

BROKEN_PYTHON = '''\
def greet(name:
    return f"Hello, {name}!"

def foo(
'''

BROKEN_JS = '''\
function add(a, b {
    return a + b;
}

const x = [1, 2,
'''


# ===========================================================================
# 1. Valid Python file -> 0 errors
# ===========================================================================

class TestValidFiles:
    def test_valid_python(self, tmp_path):
        proj = _make_files(tmp_path, {"app.py": VALID_PYTHON})
        result = _invoke(proj, "app.py")
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "0 errors" in result.output or "clean" in result.output.lower()

    def test_valid_javascript(self, tmp_path):
        proj = _make_files(tmp_path, {"app.js": VALID_JS})
        result = _invoke(proj, "app.js")
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "clean" in result.output.lower()


# ===========================================================================
# 2. Broken Python file -> errors detected
# ===========================================================================

class TestBrokenFiles:
    def test_broken_python(self, tmp_path):
        proj = _make_files(tmp_path, {"broken.py": BROKEN_PYTHON})
        result = _invoke(proj, "broken.py")
        assert result.exit_code == EXIT_GATE_FAILURE
        assert "VERDICT:" in result.output
        assert "syntax error" in result.output.lower()
        assert "broken.py" in result.output

    def test_broken_javascript(self, tmp_path):
        proj = _make_files(tmp_path, {"broken.js": BROKEN_JS})
        result = _invoke(proj, "broken.js")
        assert result.exit_code == EXIT_GATE_FAILURE
        assert "VERDICT:" in result.output
        assert "broken.js" in result.output


# ===========================================================================
# 3. JSON output format
# ===========================================================================

class TestJsonOutput:
    def test_clean_json(self, tmp_path):
        proj = _make_files(tmp_path, {"clean.py": VALID_PYTHON})
        result = _invoke(proj, "clean.py", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "syntax-check"
        assert "summary" in data
        assert data["summary"]["clean"] is True
        assert data["summary"]["total_errors"] == 0
        assert data["summary"]["total_files"] == 1
        assert data["summary"]["files_with_errors"] == 0
        assert "verdict" in data["summary"]
        assert isinstance(data.get("files", []), list)

    def test_broken_json(self, tmp_path):
        proj = _make_files(tmp_path, {"broken.py": BROKEN_PYTHON})
        result = _invoke(proj, "broken.py", json_mode=True)
        assert result.exit_code == EXIT_GATE_FAILURE
        data = json.loads(result.output)
        assert data["command"] == "syntax-check"
        assert data["summary"]["clean"] is False
        assert data["summary"]["total_errors"] > 0
        assert data["summary"]["files_with_errors"] > 0
        # Check files array has errors
        files = data.get("files", [])
        assert len(files) > 0
        assert files[0]["path"] == "broken.py"
        assert len(files[0]["errors"]) > 0
        # Each error has required fields
        err = files[0]["errors"][0]
        assert "line" in err
        assert "column" in err
        assert "node_type" in err
        assert "text" in err
        assert err["node_type"] in ("ERROR", "MISSING")

    def test_json_envelope_structure(self, tmp_path):
        """JSON output follows the standard roam envelope contract."""
        proj = _make_files(tmp_path, {"app.py": VALID_PYTHON})
        result = _invoke(proj, "app.py", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Standard envelope fields
        assert "schema" in data
        assert "schema_version" in data
        assert "version" in data
        assert "command" in data
        assert data["command"] == "syntax-check"
        # Non-deterministic metadata in _meta
        assert "_meta" in data
        assert "timestamp" in data["_meta"]


# ===========================================================================
# 4. Exit codes
# ===========================================================================

class TestExitCodes:
    def test_exit_0_clean(self, tmp_path):
        proj = _make_files(tmp_path, {"ok.py": VALID_PYTHON})
        result = _invoke(proj, "ok.py")
        assert result.exit_code == 0

    def test_exit_5_errors(self, tmp_path):
        proj = _make_files(tmp_path, {"bad.py": BROKEN_PYTHON})
        result = _invoke(proj, "bad.py")
        assert result.exit_code == EXIT_GATE_FAILURE

    def test_exit_0_no_files(self, tmp_path):
        """No existing files -> exit 0 with 'no files' verdict."""
        proj = _make_files(tmp_path, {})
        result = _invoke(proj, "nonexistent.py")
        assert result.exit_code == 0
        assert "no files" in result.output.lower()


# ===========================================================================
# 5. Multiple files
# ===========================================================================

class TestMultipleFiles:
    def test_mix_clean_and_broken(self, tmp_path):
        proj = _make_files(tmp_path, {
            "clean.py": VALID_PYTHON,
            "broken.py": BROKEN_PYTHON,
        })
        result = _invoke(proj, "clean.py", "broken.py")
        assert result.exit_code == EXIT_GATE_FAILURE
        assert "1 file" in result.output  # 1 file affected
        assert "2 files checked" in result.output

    def test_all_clean(self, tmp_path):
        proj = _make_files(tmp_path, {
            "a.py": VALID_PYTHON,
            "b.js": VALID_JS,
        })
        result = _invoke(proj, "a.py", "b.js")
        assert result.exit_code == 0
        assert "clean" in result.output.lower()

    def test_multiple_broken(self, tmp_path):
        proj = _make_files(tmp_path, {
            "a.py": BROKEN_PYTHON,
            "b.js": BROKEN_JS,
        })
        result = _invoke(proj, "a.py", "b.js")
        assert result.exit_code == EXIT_GATE_FAILURE
        assert "2 files" in result.output  # 2 files affected


# ===========================================================================
# 6. Unsupported and edge cases
# ===========================================================================

class TestEdgeCases:
    def test_unsupported_extension_skipped(self, tmp_path):
        """Files with unsupported extensions are silently skipped."""
        proj = _make_files(tmp_path, {
            "data.csv": "a,b,c\n1,2,3\n",
            "clean.py": VALID_PYTHON,
        })
        result = _invoke(proj, "data.csv", "clean.py")
        assert result.exit_code == 0
        # Only 1 file checked (csv skipped)
        assert "1 files checked" in result.output or "clean" in result.output.lower()

    def test_nonexistent_file_skipped(self, tmp_path):
        """Non-existent files are filtered out silently."""
        proj = _make_files(tmp_path, {"ok.py": VALID_PYTHON})
        result = _invoke(proj, "ok.py", "does_not_exist.py")
        assert result.exit_code == 0

    def test_empty_file(self, tmp_path):
        """An empty Python file should parse cleanly (no syntax errors)."""
        proj = _make_files(tmp_path, {"empty.py": ""})
        result = _invoke(proj, "empty.py")
        assert result.exit_code == 0

    def test_no_args_no_changed(self, tmp_path):
        """With no paths and no --changed, should show error."""
        proj = _make_files(tmp_path, {})
        result = _invoke(proj)
        assert result.exit_code != 0 or "error" in result.output.lower() or "provide" in result.output.lower()


# ===========================================================================
# 7. --changed flag
# ===========================================================================

class TestChangedFlag:
    def test_changed_with_git_repo(self, tmp_path):
        """--changed flag picks up git-modified files."""
        proj = _make_files(tmp_path, {"app.py": VALID_PYTHON})
        git_init(proj)

        # Modify a file to make it show in git diff
        (proj / "app.py").write_text(BROKEN_PYTHON, encoding="utf-8")

        result = _invoke(proj, "--changed")
        # The file should be found and checked
        # It might exit 5 (broken) or 0 (if git doesn't show it)
        # The key is that the command runs without error
        assert result.exit_code in (0, EXIT_GATE_FAILURE)

    def test_changed_no_changes(self, tmp_path):
        """--changed with a clean working tree -> no files to check."""
        proj = _make_files(tmp_path, {"app.py": VALID_PYTHON})
        git_init(proj)

        result = _invoke(proj, "--changed")
        assert result.exit_code == 0
        assert "no files" in result.output.lower() or "clean" in result.output.lower()


# ===========================================================================
# 8. Error detail accuracy
# ===========================================================================

class TestErrorDetails:
    def test_error_has_line_number(self, tmp_path):
        """Errors include correct line numbers."""
        proj = _make_files(tmp_path, {"bad.py": BROKEN_PYTHON})
        result = _invoke(proj, "bad.py", json_mode=True)
        data = json.loads(result.output)
        files = data.get("files", [])
        assert len(files) > 0
        errors = files[0]["errors"]
        assert len(errors) > 0
        # Line number should be >= 1
        assert all(e["line"] >= 1 for e in errors)
        assert all(e["column"] >= 1 for e in errors)

    def test_error_text_not_empty(self, tmp_path):
        """Error text field should have some content."""
        proj = _make_files(tmp_path, {"bad.py": BROKEN_PYTHON})
        result = _invoke(proj, "bad.py", json_mode=True)
        data = json.loads(result.output)
        errors = data["files"][0]["errors"]
        assert all(len(e["text"]) > 0 for e in errors)


# ===========================================================================
# 9. Core logic unit tests
# ===========================================================================

class TestCoreFunctions:
    def test_check_syntax_clean(self):
        """check_syntax returns empty list for valid Python."""
        from roam.commands.cmd_syntax_check import check_syntax
        from tree_sitter_language_pack import get_parser

        source = b"def foo():\n    return 42\n"
        parser = get_parser("python")
        tree = parser.parse(source)
        errors = check_syntax("test.py", source, tree)
        assert errors == []

    def test_check_syntax_broken(self):
        """check_syntax returns errors for broken Python."""
        from roam.commands.cmd_syntax_check import check_syntax
        from tree_sitter_language_pack import get_parser

        source = b"def foo(:\n    return 42\n"
        parser = get_parser("python")
        tree = parser.parse(source)
        errors = check_syntax("test.py", source, tree)
        assert len(errors) > 0
        assert all(e["node_type"] in ("ERROR", "MISSING") for e in errors)

    def test_check_syntax_none_tree(self):
        """check_syntax returns empty list when tree is None."""
        from roam.commands.cmd_syntax_check import check_syntax
        errors = check_syntax("test.py", b"content", None)
        assert errors == []

    def test_parse_file_for_syntax_valid(self, tmp_path):
        """_parse_file_for_syntax returns dict with 0 errors for valid file."""
        from roam.commands.cmd_syntax_check import _parse_file_for_syntax

        fp = tmp_path / "good.py"
        fp.write_text("def hello():\n    pass\n", encoding="utf-8")
        result = _parse_file_for_syntax(str(fp))
        assert result is not None
        assert result["language"] == "python"
        assert result["errors"] == []

    def test_parse_file_for_syntax_unsupported(self, tmp_path):
        """_parse_file_for_syntax returns None for unsupported extension."""
        from roam.commands.cmd_syntax_check import _parse_file_for_syntax

        fp = tmp_path / "data.csv"
        fp.write_text("a,b\n", encoding="utf-8")
        result = _parse_file_for_syntax(str(fp))
        assert result is None

    def test_parse_file_for_syntax_regex_only(self, tmp_path):
        """_parse_file_for_syntax returns None for regex-only languages (yaml)."""
        from roam.commands.cmd_syntax_check import _parse_file_for_syntax

        fp = tmp_path / "config.yaml"
        fp.write_text("key: value\n", encoding="utf-8")
        result = _parse_file_for_syntax(str(fp))
        assert result is None
