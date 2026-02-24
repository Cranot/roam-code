"""Tests for the roam api-changes command."""

from __future__ import annotations

import json
import os
import subprocess
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


def _make_api_project(tmp_path, initial_code):
    """Create a git repo with initial Python code, commit, and return path.

    The initial code is committed.  To test changes, modify files and
    re-index; then compare against HEAD (the initial commit).
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    for filename, content in initial_code.items():
        filepath = src / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Test: removed symbols
# ---------------------------------------------------------------------------


class TestRemovedSymbols:
    """Test detection of removed public symbols."""

    def test_removed_function(self, tmp_path, cli_runner, monkeypatch):
        """Removing a public function should be detected as BREAKING."""
        proj = _make_api_project(tmp_path, {
            "api.py": (
                'def process_data(items):\n'
                '    """Process data items."""\n'
                '    return [x * 2 for x in items]\n'
                '\n'
                'def helper():\n'
                '    return 42\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Modify file (remove process_data) — working tree change
        (proj / "src" / "api.py").write_text(
            'def helper():\n'
            '    return 42\n'
        )
        # Index picks up the current working tree state
        out, rc = index_in_process(proj)
        assert rc == 0, f"index failed: {out}"

        # Compare working tree against HEAD (the initial commit)
        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "breaking"], cwd=proj)
        assert result.exit_code == 0
        assert "REMOVED" in result.output
        assert "process_data" in result.output

    def test_removed_class(self, tmp_path, cli_runner, monkeypatch):
        """Removing a public class should be detected as BREAKING."""
        proj = _make_api_project(tmp_path, {
            "models.py": (
                'class User:\n'
                '    def __init__(self, name):\n'
                '        self.name = name\n'
                '\n'
                'class Admin:\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Remove User class
        (proj / "src" / "models.py").write_text(
            'class Admin:\n'
            '    pass\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "breaking"], cwd=proj)
        assert result.exit_code == 0
        assert "REMOVED" in result.output
        assert "User" in result.output


# ---------------------------------------------------------------------------
# Test: signature changes
# ---------------------------------------------------------------------------


class TestSignatureChanges:
    """Test detection of changed function signatures."""

    def test_added_required_param(self, tmp_path, cli_runner, monkeypatch):
        """Adding a required parameter should be BREAKING."""
        proj = _make_api_project(tmp_path, {
            "config.py": (
                'def parse_config(path):\n'
                '    """Parse config file."""\n'
                '    return {}\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Add required param 'strict'
        (proj / "src" / "config.py").write_text(
            'def parse_config(path, strict):\n'
            '    """Parse config file."""\n'
            '    return {}\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "breaking"], cwd=proj)
        assert result.exit_code == 0
        assert "SIGNATURE" in result.output
        assert "parse_config" in result.output

    def test_removed_param(self, tmp_path, cli_runner, monkeypatch):
        """Removing a parameter should be BREAKING."""
        proj = _make_api_project(tmp_path, {
            "handler.py": (
                'def process(data, validate):\n'
                '    return data\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Remove the 'validate' param
        (proj / "src" / "handler.py").write_text(
            'def process(data):\n'
            '    return data\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "breaking"], cwd=proj)
        assert result.exit_code == 0
        assert "SIGNATURE" in result.output
        assert "process" in result.output


# ---------------------------------------------------------------------------
# Test: renamed symbols
# ---------------------------------------------------------------------------


class TestRenamedSymbols:
    """Test detection of renamed symbols."""

    def test_renamed_function(self, tmp_path, cli_runner, monkeypatch):
        """Renaming a function should be detected as WARNING."""
        proj = _make_api_project(tmp_path, {
            "module.py": (
                'def old_name(x):\n'
                '    return x + 1\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Rename to new_name (similar signature, same position)
        (proj / "src" / "module.py").write_text(
            'def new_name(x):\n'
            '    return x + 1\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "warning"], cwd=proj)
        assert result.exit_code == 0
        # Should detect as either RENAMED or REMOVED+ADDED
        assert "old_name" in result.output
        assert "new_name" in result.output


# ---------------------------------------------------------------------------
# Test: visibility changes
# ---------------------------------------------------------------------------


class TestVisibilityChanges:
    """Test detection of public -> private visibility changes."""

    def test_public_to_private(self, tmp_path, cli_runner, monkeypatch):
        """Making a public function private should be BREAKING."""
        proj = _make_api_project(tmp_path, {
            "utils.py": (
                'def internal_helper(x):\n'
                '    return x * 2\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Make it private by adding underscore
        (proj / "src" / "utils.py").write_text(
            'def _internal_helper(x):\n'
            '    return x * 2\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "breaking"], cwd=proj)
        assert result.exit_code == 0
        assert "internal_helper" in result.output
        # Should show as VISIBILITY, RENAMED, or REMOVED
        output = result.output
        assert "VISIBILITY" in output or "RENAMED" in output or "REMOVED" in output


# ---------------------------------------------------------------------------
# Test: added optional params (non-breaking)
# ---------------------------------------------------------------------------


class TestOptionalParams:
    """Test detection of added optional parameters."""

    def test_added_optional_param(self, tmp_path, cli_runner, monkeypatch):
        """Adding a parameter with default value should be INFO."""
        proj = _make_api_project(tmp_path, {
            "service.py": (
                'def create_user(name):\n'
                '    return {"name": name}\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Add optional param
        (proj / "src" / "service.py").write_text(
            'def create_user(name, active=True):\n'
            '    return {"name": name, "active": active}\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        # With severity=info, should show the optional param addition
        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD", "--severity", "info"], cwd=proj)
        assert result.exit_code == 0
        assert "create_user" in result.output
        # Should be classified as optional param addition, not breaking
        output = result.output
        assert "OPTIONAL" in output or "INFO" in output


# ---------------------------------------------------------------------------
# Test: no changes
# ---------------------------------------------------------------------------


class TestNoChanges:
    """Test behavior when there are no API changes."""

    def test_no_changes(self, tmp_path, cli_runner, monkeypatch):
        """When no files changed, should report no changes."""
        proj = _make_api_project(tmp_path, {
            "stable.py": (
                'def stable_function():\n'
                '    return 42\n'
            ),
        })

        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        # Compare HEAD against HEAD — no working tree changes, nothing modified
        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD"], cwd=proj)
        assert result.exit_code == 0
        assert "No changed files" in result.output or "No API changes" in result.output


# ---------------------------------------------------------------------------
# Test: JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    """Test JSON output format and structure."""

    def test_json_envelope_structure(self, tmp_path, cli_runner, monkeypatch):
        """JSON output should follow the roam envelope contract."""
        proj = _make_api_project(tmp_path, {
            "api.py": (
                'def fetch(url):\n'
                '    return None\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Remove the function
        (proj / "src" / "api.py").write_text(
            'def other():\n'
            '    return None\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD", "--severity", "info"],
            cwd=proj,
            json_mode=True,
        )
        assert result.exit_code == 0

        data = parse_json_output(result, "api-changes")
        assert_json_envelope(data, "api-changes")

        # Check summary fields
        summary = data["summary"]
        assert "verdict" in summary
        assert "breaking_count" in summary
        assert "warning_count" in summary
        assert "info_count" in summary
        assert "base_ref" in summary

        # Check changes list
        assert "changes" in data
        assert isinstance(data["changes"], list)

    def test_json_change_fields(self, tmp_path, cli_runner, monkeypatch):
        """Each change in JSON should have the required fields."""
        proj = _make_api_project(tmp_path, {
            "handler.py": (
                'def handle(request):\n'
                '    return None\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Change signature
        (proj / "src" / "handler.py").write_text(
            'def handle(request, response):\n'
            '    return None\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD", "--severity", "breaking"],
            cwd=proj,
            json_mode=True,
        )
        assert result.exit_code == 0

        data = parse_json_output(result, "api-changes")
        changes = data.get("changes", [])

        # Should have at least one change
        assert len(changes) > 0

        # Check field presence
        for change in changes:
            assert "category" in change
            assert "severity" in change
            assert "symbol_name" in change
            assert "symbol_kind" in change
            assert "file" in change
            assert "description" in change

    def test_json_no_changes(self, tmp_path, cli_runner, monkeypatch):
        """JSON output with no changes should still have valid structure."""
        proj = _make_api_project(tmp_path, {
            "stable.py": (
                'def stable():\n'
                '    return 42\n'
            ),
        })

        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD"],
            cwd=proj,
            json_mode=True,
        )
        assert result.exit_code == 0

        data = parse_json_output(result, "api-changes")
        assert_json_envelope(data, "api-changes")
        assert data["summary"]["breaking_count"] == 0
        assert data["changes"] == []


# ---------------------------------------------------------------------------
# Test: severity filtering
# ---------------------------------------------------------------------------


class TestSeverityFiltering:
    """Test --severity flag filters output correctly."""

    def test_severity_breaking_only(self, tmp_path, cli_runner, monkeypatch):
        """--severity=breaking should hide warnings and info."""
        proj = _make_api_project(tmp_path, {
            "mixed.py": (
                'def removed_fn():\n'
                '    pass\n'
                '\n'
                'def kept_fn():\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Remove one function, add a new one (ADDED = info)
        (proj / "src" / "mixed.py").write_text(
            'def kept_fn():\n'
            '    pass\n'
            '\n'
            'def brand_new():\n'
            '    pass\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD", "--severity", "breaking"],
            cwd=proj,
        )
        assert result.exit_code == 0
        # Should NOT show INFO section
        assert "INFO:" not in result.output

    def test_severity_info_shows_all(self, tmp_path, cli_runner, monkeypatch):
        """--severity=info should show everything including additions."""
        proj = _make_api_project(tmp_path, {
            "api.py": (
                'def existing():\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Add a new function
        (proj / "src" / "api.py").write_text(
            'def existing():\n'
            '    pass\n'
            '\n'
            'def new_feature(x, y):\n'
            '    return x + y\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD", "--severity", "info"],
            cwd=proj,
        )
        assert result.exit_code == 0
        assert "new_feature" in result.output
        assert "ADDED" in result.output


# ---------------------------------------------------------------------------
# Test: --base flag with different refs
# ---------------------------------------------------------------------------


class TestBaseFlag:
    """Test --base flag with different git refs."""

    def test_base_head(self, tmp_path, cli_runner, monkeypatch):
        """--base=HEAD should find no changes when working tree matches."""
        proj = _make_api_project(tmp_path, {
            "app.py": (
                'def main():\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD"], cwd=proj)
        assert result.exit_code == 0
        assert "No changed files" in result.output or "No API changes" in result.output

    def test_base_specific_commit(self, tmp_path, cli_runner, monkeypatch):
        """--base with a specific commit ref should work."""
        proj = _make_api_project(tmp_path, {
            "app.py": (
                'def original():\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Get initial commit hash
        result_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(proj),
            capture_output=True,
            text=True,
        )
        first_commit = result_hash.stdout.strip()

        # Make a second commit with changes
        (proj / "src" / "app.py").write_text(
            'def original():\n'
            '    pass\n'
            '\n'
            'def added_later():\n'
            '    return 1\n'
        )
        git_commit(proj, "add function")
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", first_commit, "--severity", "info"],
            cwd=proj,
        )
        assert result.exit_code == 0
        assert "added_later" in result.output

    def test_base_head_tilde_with_two_commits(self, tmp_path, cli_runner, monkeypatch):
        """HEAD~1 should work with two commits."""
        proj = _make_api_project(tmp_path, {
            "api.py": (
                'def old_api():\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Make a second commit with changes
        (proj / "src" / "api.py").write_text(
            'def new_api():\n'
            '    pass\n'
        )
        git_commit(proj, "change api")
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD~1", "--severity", "info"],
            cwd=proj,
        )
        assert result.exit_code == 0
        assert "old_api" in result.output
        assert "new_api" in result.output


# ---------------------------------------------------------------------------
# Test: added symbols (new file)
# ---------------------------------------------------------------------------


class TestAddedSymbols:
    """Test detection of newly added symbols."""

    def test_new_file_symbols_are_added(self, tmp_path, cli_runner, monkeypatch):
        """Symbols in a brand new file should appear as ADDED."""
        proj = _make_api_project(tmp_path, {
            "existing.py": (
                'def stable():\n'
                '    pass\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Add a new file and commit it (so git diff HEAD~1 shows it)
        (proj / "src" / "new_module.py").write_text(
            'def brand_new_function():\n'
            '    return "hello"\n'
            '\n'
            'class NewClass:\n'
            '    pass\n'
        )
        git_commit(proj, "add new module")
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD~1", "--severity", "info"],
            cwd=proj,
        )
        assert result.exit_code == 0
        assert "brand_new_function" in result.output
        assert "ADDED" in result.output


# ---------------------------------------------------------------------------
# Test: verdict-first output
# ---------------------------------------------------------------------------


class TestVerdictOutput:
    """Test that text output starts with VERDICT line."""

    def test_verdict_present(self, tmp_path, cli_runner, monkeypatch):
        """Output should start with VERDICT line."""
        proj = _make_api_project(tmp_path, {
            "api.py": (
                'def process(data):\n'
                '    return data\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Make a breaking change
        (proj / "src" / "api.py").write_text(
            'def process(data, mode):\n'
            '    return data\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD"], cwd=proj)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_verdict_no_changes(self, tmp_path, cli_runner, monkeypatch):
        """Verdict should be present even with no changes."""
        proj = _make_api_project(tmp_path, {
            "stable.py": (
                'def stable():\n'
                '    return 42\n'
            ),
        })

        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["api-changes", "--base", "HEAD"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


# ---------------------------------------------------------------------------
# Test: type changes
# ---------------------------------------------------------------------------


class TestTypeChanges:
    """Test detection of return type changes."""

    def test_return_type_changed(self, tmp_path, cli_runner, monkeypatch):
        """Changing a return type should be detected."""
        proj = _make_api_project(tmp_path, {
            "counter.py": (
                'def get_count() -> int:\n'
                '    return 42\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Change return type
        (proj / "src" / "counter.py").write_text(
            'def get_count() -> str:\n'
            '    return "42"\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD", "--severity", "warning"],
            cwd=proj,
        )
        assert result.exit_code == 0
        assert "get_count" in result.output
        # Should show TYPE change or SIGNATURE change
        assert "TYPE" in result.output or "SIGNATURE" in result.output


# ---------------------------------------------------------------------------
# Test: multiple changes in one file
# ---------------------------------------------------------------------------


class TestMultipleChanges:
    """Test detection of multiple API changes in a single file."""

    def test_mixed_changes(self, tmp_path, cli_runner, monkeypatch):
        """Multiple change types in one file should all be detected."""
        proj = _make_api_project(tmp_path, {
            "api.py": (
                'def process_data():\n'
                '    pass\n'
                '\n'
                'def transform(x):\n'
                '    return x\n'
                '\n'
                'def validate():\n'
                '    return 1\n'
            ),
        })

        monkeypatch.chdir(proj)

        # Remove process_data, change transform signature, add completely_new_handler
        (proj / "src" / "api.py").write_text(
            'def transform(x, y):\n'
            '    return x + y\n'
            '\n'
            'def validate():\n'
            '    return 1\n'
            '\n'
            'def completely_new_handler():\n'
            '    return "new"\n'
        )
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(
            cli_runner,
            ["api-changes", "--base", "HEAD", "--severity", "info"],
            cwd=proj,
            json_mode=True,
        )
        assert result.exit_code == 0

        data = parse_json_output(result, "api-changes")
        changes = data.get("changes", [])

        # Should have changes for process_data (removed), transform (sig changed),
        # completely_new_handler (added)
        names = [c["symbol_name"] for c in changes]
        assert "process_data" in names, f"Expected process_data in changes, got {names}"
        assert "transform" in names, f"Expected transform in changes, got {names}"
        assert "completely_new_handler" in names, f"Expected completely_new_handler in changes, got {names}"

        # validate should NOT be in changes (unchanged)
        categories = {c["symbol_name"]: c["category"] for c in changes}
        assert categories.get("process_data") == "REMOVED"
        assert categories.get("transform") == "SIGNATURE_CHANGED"
        assert categories.get("completely_new_handler") == "ADDED"


# ---------------------------------------------------------------------------
# Test: unit tests for internal comparison functions
# ---------------------------------------------------------------------------


class TestInternalFunctions:
    """Test internal helper functions directly."""

    def test_extract_params(self):
        from roam.commands.cmd_api_changes import _extract_params
        assert _extract_params("def foo(a, b, c)") == ["a", "b", "c"]
        assert _extract_params("def foo(self, x)") == ["x"]
        assert _extract_params("def foo()") == []
        assert _extract_params(None) == []
        assert _extract_params("") == []

    def test_extract_params_with_defaults(self):
        from roam.commands.cmd_api_changes import _extract_params_with_defaults
        result = _extract_params_with_defaults("def foo(a, b=10, c='x')")
        assert result == [("a", False), ("b", True), ("c", True)]

    def test_extract_return_type(self):
        from roam.commands.cmd_api_changes import _extract_return_type
        assert _extract_return_type("def foo() -> int") == "int"
        assert _extract_return_type("def foo() -> str") == "str"
        assert _extract_return_type("def foo()") == ""
        assert _extract_return_type(None) == ""

    def test_similarity(self):
        from roam.commands.cmd_api_changes import _similarity
        assert _similarity("hello", "hello") == 1.0
        assert _similarity("hello", "helo") > 0.5
        assert _similarity("abc", "xyz") < 0.5

    def test_is_private_name(self):
        from roam.commands.cmd_api_changes import _is_private_name
        assert _is_private_name("_private") is True
        assert _is_private_name("public") is False
        assert _is_private_name("__dunder__") is False

    def test_sig_normalise(self):
        from roam.commands.cmd_api_changes import _sig_normalise
        assert _sig_normalise("def  foo( a,  b )") == "def foo( a, b )"
        assert _sig_normalise(None) == ""
        assert _sig_normalise("") == ""

    def test_severity_order(self):
        from roam.commands.cmd_api_changes import _SEVERITY_ORDER
        assert _SEVERITY_ORDER["breaking"] < _SEVERITY_ORDER["warning"]
        assert _SEVERITY_ORDER["warning"] < _SEVERITY_ORDER["info"]

    def test_change_categories(self):
        from roam.commands.cmd_api_changes import _CHANGE_CATEGORIES
        assert _CHANGE_CATEGORIES["REMOVED"] == "breaking"
        assert _CHANGE_CATEGORIES["SIGNATURE_CHANGED"] == "breaking"
        assert _CHANGE_CATEGORIES["VISIBILITY_REDUCED"] == "breaking"
        assert _CHANGE_CATEGORIES["RENAMED"] == "warning"
        assert _CHANGE_CATEGORIES["TYPE_CHANGED"] == "warning"
        assert _CHANGE_CATEGORIES["ADDED"] == "info"
        assert _CHANGE_CATEGORIES["PARAM_ADDED_OPTIONAL"] == "info"
        assert _CHANGE_CATEGORIES["DEPRECATED"] == "info"

    def test_compare_file_api_removed(self):
        """_compare_file_api should detect removed exported symbols."""
        from roam.commands.cmd_api_changes import _compare_file_api

        old_symbols = [
            {"name": "foo", "qualified_name": "foo", "kind": "function",
             "signature": "def foo()", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]
        new_symbols = []

        changes = _compare_file_api("test.py", old_symbols, new_symbols)
        assert len(changes) == 1
        assert changes[0]["category"] == "REMOVED"
        assert changes[0]["symbol_name"] == "foo"

    def test_compare_file_api_added(self):
        """_compare_file_api should detect added exported symbols."""
        from roam.commands.cmd_api_changes import _compare_file_api

        old_symbols = []
        new_symbols = [
            {"name": "bar", "qualified_name": "bar", "kind": "function",
             "signature": "def bar()", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]

        changes = _compare_file_api("test.py", old_symbols, new_symbols)
        assert len(changes) == 1
        assert changes[0]["category"] == "ADDED"
        assert changes[0]["symbol_name"] == "bar"

    def test_compare_file_api_signature_changed(self):
        """_compare_file_api should detect signature changes."""
        from roam.commands.cmd_api_changes import _compare_file_api

        old_symbols = [
            {"name": "fn", "qualified_name": "fn", "kind": "function",
             "signature": "def fn(a)", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]
        new_symbols = [
            {"name": "fn", "qualified_name": "fn", "kind": "function",
             "signature": "def fn(a, b)", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]

        changes = _compare_file_api("test.py", old_symbols, new_symbols)
        assert len(changes) >= 1
        sig_changes = [c for c in changes if c["category"] == "SIGNATURE_CHANGED"]
        assert len(sig_changes) == 1
        assert sig_changes[0]["symbol_name"] == "fn"

    def test_compare_file_api_optional_param(self):
        """_compare_file_api should detect optional param additions as INFO."""
        from roam.commands.cmd_api_changes import _compare_file_api

        old_symbols = [
            {"name": "fn", "qualified_name": "fn", "kind": "function",
             "signature": "def fn(a)", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]
        new_symbols = [
            {"name": "fn", "qualified_name": "fn", "kind": "function",
             "signature": "def fn(a, b=10)", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]

        changes = _compare_file_api("test.py", old_symbols, new_symbols)
        assert len(changes) >= 1
        opt_changes = [c for c in changes if c["category"] == "PARAM_ADDED_OPTIONAL"]
        assert len(opt_changes) == 1
        assert opt_changes[0]["severity"] == "info"

    def test_compare_file_api_visibility_reduced(self):
        """_compare_file_api should detect visibility reductions."""
        from roam.commands.cmd_api_changes import _compare_file_api

        old_symbols = [
            {"name": "helper", "qualified_name": "helper", "kind": "function",
             "signature": "def helper()", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]
        new_symbols = [
            {"name": "helper", "qualified_name": "helper", "kind": "function",
             "signature": "def helper()", "line_start": 1, "line_end": 2,
             "visibility": "private", "is_exported": False},
        ]

        changes = _compare_file_api("test.py", old_symbols, new_symbols)
        assert len(changes) >= 1
        vis_changes = [c for c in changes if c["category"] == "VISIBILITY_REDUCED"]
        assert len(vis_changes) == 1
        assert vis_changes[0]["severity"] == "breaking"

    def test_compare_file_api_no_changes(self):
        """No changes between identical symbol lists."""
        from roam.commands.cmd_api_changes import _compare_file_api

        symbols = [
            {"name": "fn", "qualified_name": "fn", "kind": "function",
             "signature": "def fn(a)", "line_start": 1, "line_end": 2,
             "visibility": "public", "is_exported": True},
        ]

        changes = _compare_file_api("test.py", symbols, symbols)
        assert len(changes) == 0
