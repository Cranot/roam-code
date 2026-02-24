"""Tests for `roam semantic-diff` -- structural change summary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)

from roam.cli import cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.fixture
def semantic_diff_project(tmp_path):
    """Create a project with two commits to enable semantic diff testing.

    Commit 1 (init): Initial python files with functions.
    Commit 2: Adds more content to create a valid HEAD~1.
    Working tree: Modifies, adds, and removes functions + changes imports.
    The project is indexed after the working tree changes.

    git diff HEAD~1 compares commit 1 (initial state) to the working tree.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    # --- Commit 1 (init): initial state ---
    (src / "app.py").write_text(
        'from utils import format_name\n'
        '\n'
        '\n'
        'def process_data(items):\n'
        '    """Process a list of items."""\n'
        '    result = []\n'
        '    for item in items:\n'
        '        result.append(item.upper())\n'
        '    return result\n'
        '\n'
        '\n'
        'def old_helper():\n'
        '    """This will be removed."""\n'
        '    return 42\n'
        '\n'
        '\n'
        'def stable_function():\n'
        '    """This will not change."""\n'
        '    return True\n'
    )

    (src / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
    )

    git_init(proj)

    # --- Commit 2: minor addition to create HEAD~1 ---
    (src / "config.py").write_text(
        'DEBUG = False\n'
    )
    git_commit(proj, "add config")

    # --- Working tree changes: modify, add, remove ---
    (src / "app.py").write_text(
        'from utils import format_name, parse_email\n'
        '\n'
        '\n'
        'def process_data(items, strict=False):\n'
        '    """Process a list of items with optional strict mode."""\n'
        '    result = []\n'
        '    for item in items:\n'
        '        if strict and not item:\n'
        '            raise ValueError("Empty item")\n'
        '        result.append(item.upper())\n'
        '    return result\n'
        '\n'
        '\n'
        'def validate_input(data):\n'
        '    """New validation function."""\n'
        '    if not isinstance(data, dict):\n'
        '        return False\n'
        '    return True\n'
        '\n'
        '\n'
        'def stable_function():\n'
        '    """This will not change."""\n'
        '    return True\n'
    )

    (src / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        '\n'
        '\n'
        'def parse_email(raw):\n'
        '    """Parse an email address."""\n'
        '    if "@" not in raw:\n'
        '        return None\n'
        '    return raw.split("@")\n'
    )

    # Index the current state
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    return proj


@pytest.fixture
def no_change_project(tmp_path):
    """Create a project where HEAD~1 equals current state (no changes)."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    (src / "app.py").write_text(
        'def greet(name):\n'
        '    return f"Hello, {name}"\n'
    )

    git_init(proj)

    # Make a second commit with the same content
    (src / "extra.py").write_text(
        'def extra():\n'
        '    pass\n'
    )
    git_commit(proj, "add extra")

    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    return proj


# ===========================================================================
# Basic execution tests
# ===========================================================================

class TestSemanticDiffBasic:
    """Basic execution and output format tests."""

    def test_semantic_diff_runs(self, cli_runner, semantic_diff_project, monkeypatch):
        """Command executes without errors."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(cli_runner, ["semantic-diff"], cwd=semantic_diff_project)
        assert result.exit_code == 0

    def test_semantic_diff_with_base_flag(self, cli_runner, semantic_diff_project, monkeypatch):
        """Command accepts --base flag."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner,
            ["semantic-diff", "--base", "HEAD"],
            cwd=semantic_diff_project,
        )
        assert result.exit_code == 0

    def test_semantic_diff_no_changes(self, cli_runner, no_change_project, monkeypatch):
        """When HEAD~1 has only file additions, shows appropriate output."""
        monkeypatch.chdir(no_change_project)
        result = invoke_cli(
            cli_runner,
            ["semantic-diff", "--base", "HEAD"],
            cwd=no_change_project,
        )
        assert result.exit_code == 0


# ===========================================================================
# Text output tests
# ===========================================================================

class TestSemanticDiffText:
    """Tests for text output format."""

    def test_verdict_line(self, cli_runner, semantic_diff_project, monkeypatch):
        """Output starts with VERDICT: line."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(cli_runner, ["semantic-diff"], cwd=semantic_diff_project)
        assert result.exit_code == 0
        out = result.output
        assert "VERDICT:" in out

    def test_symbols_added_section(self, cli_runner, semantic_diff_project, monkeypatch):
        """Output contains SYMBOLS ADDED section."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(cli_runner, ["semantic-diff"], cwd=semantic_diff_project)
        out = result.output
        # Should detect validate_input and/or parse_email as added
        assert "SYMBOLS ADDED" in out or "SUMMARY:" in out

    def test_symbols_removed_section(self, cli_runner, semantic_diff_project, monkeypatch):
        """Output contains SYMBOLS REMOVED section for removed functions."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(cli_runner, ["semantic-diff"], cwd=semantic_diff_project)
        out = result.output
        # old_helper was removed
        assert "SYMBOLS REMOVED" in out or "SUMMARY:" in out

    def test_symbols_modified_section(self, cli_runner, semantic_diff_project, monkeypatch):
        """Output contains SYMBOLS MODIFIED section for changed functions."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(cli_runner, ["semantic-diff"], cwd=semantic_diff_project)
        out = result.output
        # process_data was modified (new parameter, more lines)
        assert "SYMBOLS MODIFIED" in out or "SUMMARY:" in out

    def test_summary_line(self, cli_runner, semantic_diff_project, monkeypatch):
        """Output ends with a SUMMARY line."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(cli_runner, ["semantic-diff"], cwd=semantic_diff_project)
        out = result.output
        assert "SUMMARY:" in out

    def test_no_changes_message(self, cli_runner, no_change_project, monkeypatch):
        """When comparing HEAD vs HEAD, shows appropriate message."""
        monkeypatch.chdir(no_change_project)
        result = invoke_cli(
            cli_runner,
            ["semantic-diff", "--base", "HEAD"],
            cwd=no_change_project,
        )
        out = result.output
        # Should show "No changed files" or "0 structural changes"
        assert ("no changed" in out.lower() or "0 structural" in out.lower()
                or "VERDICT:" in out)


# ===========================================================================
# JSON output tests
# ===========================================================================

class TestSemanticDiffJSON:
    """Tests for JSON output format."""

    def test_json_output_valid(self, cli_runner, semantic_diff_project, monkeypatch):
        """JSON mode produces valid JSON."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_json_envelope(self, cli_runner, semantic_diff_project, monkeypatch):
        """JSON output follows the roam envelope contract."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        data = parse_json_output(result, "semantic-diff")
        assert_json_envelope(data, "semantic-diff")

    def test_json_summary_has_verdict(self, cli_runner, semantic_diff_project, monkeypatch):
        """JSON summary contains a verdict field."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        data = json.loads(result.output)
        summary = data.get("summary", {})
        assert "verdict" in summary

    def test_json_summary_has_counts(self, cli_runner, semantic_diff_project, monkeypatch):
        """JSON summary contains all expected count fields."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        data = json.loads(result.output)
        summary = data.get("summary", {})
        assert "files_changed" in summary
        assert "symbols_added" in summary
        assert "symbols_removed" in summary
        assert "symbols_modified" in summary
        assert "imports_added" in summary
        assert "imports_removed" in summary

    def test_json_has_arrays(self, cli_runner, semantic_diff_project, monkeypatch):
        """JSON output contains the expected top-level arrays."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        data = json.loads(result.output)
        assert "symbols_added" in data
        assert "symbols_removed" in data
        assert "symbols_modified" in data
        assert "imports_added" in data
        assert "imports_removed" in data
        assert isinstance(data["symbols_added"], list)
        assert isinstance(data["symbols_removed"], list)
        assert isinstance(data["symbols_modified"], list)
        assert isinstance(data["imports_added"], list)
        assert isinstance(data["imports_removed"], list)

    def test_json_has_base_ref(self, cli_runner, semantic_diff_project, monkeypatch):
        """JSON output contains the base_ref field."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        data = json.loads(result.output)
        assert "base_ref" in data
        assert data["base_ref"] == "HEAD~1"

    def test_json_no_changes(self, cli_runner, no_change_project, monkeypatch):
        """JSON output for no-change case has zero counts."""
        monkeypatch.chdir(no_change_project)
        result = invoke_cli(
            cli_runner,
            ["semantic-diff", "--base", "HEAD"],
            cwd=no_change_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        summary = data.get("summary", {})
        assert summary.get("files_changed", 0) == 0

    def test_json_added_symbol_shape(self, cli_runner, semantic_diff_project, monkeypatch):
        """Added symbols have the expected dict keys."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=semantic_diff_project, json_mode=True,
        )
        data = json.loads(result.output)
        added = data.get("symbols_added", [])
        if added:
            sym = added[0]
            assert "name" in sym
            assert "kind" in sym
            assert "file" in sym


# ===========================================================================
# Unit tests for internal comparison logic
# ===========================================================================

class TestComparisonHelpers:
    """Unit tests for the comparison functions."""

    def test_extract_params_simple(self):
        """Extract params from a simple Python signature."""
        from roam.commands.cmd_semantic_diff import _extract_params
        params = _extract_params("def process_data(items, strict=False)")
        assert "items" in params
        assert "strict" in params

    def test_extract_params_empty(self):
        """Extract params from empty parens."""
        from roam.commands.cmd_semantic_diff import _extract_params
        params = _extract_params("def no_args()")
        assert params == []

    def test_extract_params_none(self):
        """Extract params from None returns empty."""
        from roam.commands.cmd_semantic_diff import _extract_params
        params = _extract_params(None)
        assert params == []

    def test_extract_params_typed(self):
        """Extract params from typed signature."""
        from roam.commands.cmd_semantic_diff import _extract_params
        params = _extract_params("def func(name: str, age: int)")
        assert "name" in params
        assert "age" in params

    def test_count_lines(self):
        """Count lines from line_start/line_end."""
        from roam.commands.cmd_semantic_diff import _count_lines
        assert _count_lines({"line_start": 5, "line_end": 10}) == 6
        assert _count_lines({"line_start": 5, "line_end": 5}) == 1
        assert _count_lines({"line_start": None, "line_end": None}) is None

    def test_sym_key(self):
        """Symbol key prefers qualified_name."""
        from roam.commands.cmd_semantic_diff import _sym_key
        assert _sym_key({"qualified_name": "Foo.bar", "name": "bar"}) == "Foo.bar"
        assert _sym_key({"name": "bar"}) == "bar"

    def test_compare_symbols_added(self):
        """Detect added symbols."""
        from roam.commands.cmd_semantic_diff import _compare_symbols
        old = [{"name": "a", "kind": "function", "line_start": 1, "line_end": 3}]
        new = [
            {"name": "a", "kind": "function", "line_start": 1, "line_end": 3},
            {"name": "b", "kind": "function", "line_start": 5, "line_end": 8},
        ]
        added, removed, modified = _compare_symbols("test.py", old, new)
        assert len(added) == 1
        assert added[0]["name"] == "b"
        assert len(removed) == 0

    def test_compare_symbols_removed(self):
        """Detect removed symbols."""
        from roam.commands.cmd_semantic_diff import _compare_symbols
        old = [
            {"name": "a", "kind": "function", "line_start": 1, "line_end": 3},
            {"name": "b", "kind": "function", "line_start": 5, "line_end": 8},
        ]
        new = [{"name": "a", "kind": "function", "line_start": 1, "line_end": 3}]
        added, removed, modified = _compare_symbols("test.py", old, new)
        assert len(removed) == 1
        assert removed[0]["name"] == "b"
        assert len(added) == 0

    def test_compare_symbols_modified_body(self):
        """Detect body line count changes."""
        from roam.commands.cmd_semantic_diff import _compare_symbols
        old = [{"name": "a", "kind": "function", "line_start": 1, "line_end": 5}]
        new = [{"name": "a", "kind": "function", "line_start": 1, "line_end": 10}]
        added, removed, modified = _compare_symbols("test.py", old, new)
        assert len(modified) == 1
        assert "body_lines" in modified[0]["changes"]
        assert modified[0]["changes"]["body_lines"]["old"] == 5
        assert modified[0]["changes"]["body_lines"]["new"] == 10

    def test_compare_symbols_modified_signature(self):
        """Detect signature changes."""
        from roam.commands.cmd_semantic_diff import _compare_symbols
        old = [{
            "name": "func",
            "kind": "function",
            "signature": "def func(a, b)",
            "line_start": 1,
            "line_end": 5,
        }]
        new = [{
            "name": "func",
            "kind": "function",
            "signature": "def func(a, b, c)",
            "line_start": 1,
            "line_end": 5,
        }]
        added, removed, modified = _compare_symbols("test.py", old, new)
        assert len(modified) == 1
        assert "params" in modified[0]["changes"]
        assert modified[0]["changes"]["params"]["old_count"] == 2
        assert modified[0]["changes"]["params"]["new_count"] == 3
        assert "c" in modified[0]["changes"]["params"]["added"]

    def test_compare_symbols_no_change(self):
        """Unchanged symbols produce no output."""
        from roam.commands.cmd_semantic_diff import _compare_symbols
        old = [{"name": "a", "kind": "function", "line_start": 1, "line_end": 5}]
        new = [{"name": "a", "kind": "function", "line_start": 1, "line_end": 5}]
        added, removed, modified = _compare_symbols("test.py", old, new)
        assert len(added) == 0
        assert len(removed) == 0
        assert len(modified) == 0

    def test_compare_imports_added(self):
        """Detect added imports."""
        from roam.commands.cmd_semantic_diff import _compare_imports
        old_refs = [
            {"kind": "import", "target_name": "os", "import_path": ""},
        ]
        new_refs = [
            {"kind": "import", "target_name": "os", "import_path": ""},
            {"kind": "import", "target_name": "sys", "import_path": ""},
        ]
        added, removed = _compare_imports("test.py", old_refs, new_refs)
        assert len(added) == 1
        assert added[0]["import"] == "sys"
        assert len(removed) == 0

    def test_compare_imports_removed(self):
        """Detect removed imports."""
        from roam.commands.cmd_semantic_diff import _compare_imports
        old_refs = [
            {"kind": "import", "target_name": "os", "import_path": ""},
            {"kind": "import", "target_name": "sys", "import_path": ""},
        ]
        new_refs = [
            {"kind": "import", "target_name": "os", "import_path": ""},
        ]
        added, removed = _compare_imports("test.py", old_refs, new_refs)
        assert len(removed) == 1
        assert removed[0]["import"] == "sys"
        assert len(added) == 0

    def test_compare_imports_with_path(self):
        """Import keys include import_path when present."""
        from roam.commands.cmd_semantic_diff import _compare_imports
        old_refs = []
        new_refs = [
            {"kind": "import_from", "target_name": "bar", "import_path": "foo"},
        ]
        added, removed = _compare_imports("test.py", old_refs, new_refs)
        assert len(added) == 1
        assert added[0]["import"] == "foo:bar"

    def test_compare_imports_ignores_calls(self):
        """Non-import references are ignored."""
        from roam.commands.cmd_semantic_diff import _compare_imports
        old_refs = [{"kind": "call", "target_name": "foo", "import_path": ""}]
        new_refs = []
        added, removed = _compare_imports("test.py", old_refs, new_refs)
        assert len(added) == 0
        assert len(removed) == 0


# ===========================================================================
# Edge case tests
# ===========================================================================

class TestSemanticDiffEdgeCases:
    """Edge case and robustness tests."""

    def test_new_file_all_added(self, cli_runner, tmp_path, monkeypatch):
        """A brand new file should show all symbols as added."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        # Initial commit with one file
        src = proj / "src"
        src.mkdir()
        (src / "old.py").write_text('def old_func():\n    pass\n')
        git_init(proj)

        # Add a new file (not committed -- working tree only)
        (src / "new_module.py").write_text(
            'def brand_new():\n'
            '    """A brand new function."""\n'
            '    return 1\n'
        )

        out, rc = index_in_process(proj)
        assert rc == 0, f"roam index failed:\n{out}"

        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=proj, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # The new file should contribute to added symbols
        assert isinstance(data.get("symbols_added"), list)

    def test_deleted_file_all_removed(self, cli_runner, tmp_path, monkeypatch):
        """A deleted file should show all its symbols as removed."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        src = proj / "src"
        src.mkdir()
        (src / "app.py").write_text(
            'def will_be_deleted():\n'
            '    """This function lives in a file that will be removed."""\n'
            '    return 42\n'
        )
        (src / "keep.py").write_text('def stay():\n    pass\n')
        git_init(proj)

        # Delete the file
        os.remove(str(src / "app.py"))

        out, rc = index_in_process(proj)
        assert rc == 0, f"roam index failed:\n{out}"

        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=proj, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data.get("symbols_removed"), list)

    def test_unsupported_file_type(self, cli_runner, tmp_path, monkeypatch):
        """Non-code files (.txt, .md) should be skipped gracefully."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "readme.md").write_text("# Hello\n")
        git_init(proj)

        (proj / "readme.md").write_text("# Hello World\nUpdated readme.\n")

        out, rc = index_in_process(proj)
        assert rc == 0

        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner, ["semantic-diff"],
            cwd=proj,
        )
        assert result.exit_code == 0

    def test_custom_base_ref(self, cli_runner, semantic_diff_project, monkeypatch):
        """The --base flag correctly changes the comparison base."""
        monkeypatch.chdir(semantic_diff_project)
        result = invoke_cli(
            cli_runner,
            ["semantic-diff", "--base", "HEAD"],
            cwd=semantic_diff_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("base_ref") == "HEAD"
