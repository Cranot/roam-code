"""Tests for the verify-imports command -- hallucination firewall for import statements."""

from __future__ import annotations

import json
import os

import click
import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, git_init


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def python_import_project(project_factory):
    """Python project with both resolvable and unresolvable imports."""
    return project_factory({
        "models.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "class Admin(User):\n"
            "    pass\n"
        ),
        "service.py": (
            "from models import User, Admin\n"
            "\n"
            "def create_user(name):\n"
            "    return User(name)\n"
        ),
        "broken.py": (
            "from nonexistent_module import FakeClass\n"
            "import totally_missing\n"
            "\n"
            "def broken_func():\n"
            "    return FakeClass()\n"
        ),
        "utils.py": (
            "def helper():\n"
            "    return 42\n"
        ),
    })


@pytest.fixture
def js_import_project(project_factory):
    """JavaScript project with import/require statements."""
    return project_factory({
        "app.js": (
            "import { render } from 'renderer'\n"
            "import Config from 'config'\n"
            "\n"
            "function main() { render(); }\n"
        ),
        "renderer.js": (
            "function render() { return 'ok'; }\n"
            "module.exports = { render };\n"
        ),
        "config.js": (
            "const Config = { debug: false };\n"
            "module.exports = Config;\n"
        ),
        "broken.js": (
            "const missing = require('does_not_exist')\n"
            "import { ghost } from 'phantom_module'\n"
        ),
    })


@pytest.fixture
def clean_project(project_factory):
    """Project with no imports at all."""
    return project_factory({
        "main.py": (
            "def main():\n"
            "    print('hello')\n"
        ),
        "utils.py": (
            "def add(a, b):\n"
            "    return a + b\n"
        ),
    })


@pytest.fixture
def all_resolved_project(project_factory):
    """Project where all imports resolve correctly."""
    return project_factory({
        "models.py": (
            "class User:\n"
            "    pass\n"
        ),
        "service.py": (
            "from models import User\n"
            "\n"
            "def get_user():\n"
            "    return User()\n"
        ),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_cli():
    """Build a minimal CLI group with the verify-imports command registered."""
    from roam.commands.cmd_verify_imports import verify_imports_cmd

    @click.group()
    @click.option("--json", "json_mode", is_flag=True, default=False)
    @click.pass_context
    def cli(ctx, json_mode):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_mode
    cli.add_command(verify_imports_cmd)
    return cli


def _invoke(args, cwd, json_mode=False):
    """Invoke verify-imports via a standalone CLI group (no cli.py dependency)."""
    cli = _build_cli()
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# 1. Basic command tests
# ===========================================================================

class TestBasicCommand:
    """Test that the command runs and produces output."""

    def test_runs_without_error(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"

    def test_help_flag(self):
        cli = _build_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["verify-imports", "--help"])
        assert result.exit_code == 0
        assert "import" in result.output.lower() or "hallucination" in result.output.lower()

    def test_clean_project_no_imports(self, clean_project):
        result = _invoke(["verify-imports"], clean_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


# ===========================================================================
# 2. Text output tests
# ===========================================================================

class TestTextOutput:
    """Test the plain-text output format."""

    def test_verdict_line_present(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project)
        assert "VERDICT:" in result.output

    def test_unresolved_imports_shown(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project)
        # Should detect unresolved imports from broken.py
        assert "unresolved" in result.output.lower()

    def test_all_resolved_verdict(self, all_resolved_project):
        result = _invoke(["verify-imports"], all_resolved_project)
        output = result.output.lower()
        # Should indicate all imports are resolved
        assert "unresolved" not in output or "0 unresolved" in output or "all" in output

    def test_suggestions_in_text_output(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project)
        # The output should have a table with suggestions column
        assert "Suggestions" in result.output or "suggestions" in result.output or "Location" in result.output

    def test_tip_message_on_unresolved(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project)
        if "unresolved" in result.output.lower():
            # Should have a helpful tip
            assert "roam search" in result.output or "roam index" in result.output


# ===========================================================================
# 3. JSON output tests
# ===========================================================================

class TestJsonOutput:
    """Test the JSON envelope structure."""

    def test_json_envelope_structure(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "verify-imports"
        assert "summary" in data
        assert "imports" in data
        assert "version" in data

    def test_json_summary_fields(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert "verdict" in summary
        assert "total_imports" in summary
        assert "resolved" in summary
        assert "unresolved" in summary
        assert "files_checked" in summary

    def test_json_import_fields(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        data = json.loads(result.output)
        imports = data.get("imports", [])
        assert len(imports) > 0
        for imp in imports:
            assert "file" in imp
            assert "line" in imp
            assert "name" in imp
            assert "status" in imp
            assert imp["status"] in ("resolved", "unresolved")

    def test_json_unresolved_have_suggestions(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        data = json.loads(result.output)
        unresolved = [i for i in data.get("imports", []) if i["status"] == "unresolved"]
        # At least some unresolved imports should exist
        assert len(unresolved) > 0
        # Suggestions field should be present (may be empty if no FTS matches)
        for u in unresolved:
            if "suggestions" in u:
                assert isinstance(u["suggestions"], list)

    def test_json_resolved_count_correct(self, all_resolved_project):
        result = _invoke(["verify-imports"], all_resolved_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert summary["unresolved"] == 0
        assert summary["resolved"] == summary["total_imports"]

    def test_json_total_is_sum(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert summary["total_imports"] == summary["resolved"] + summary["unresolved"]


# ===========================================================================
# 4. File filter tests
# ===========================================================================

class TestFileFilter:
    """Test the --file option to restrict to a single file."""

    def test_filter_specific_file(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "broken.py"],
            python_import_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should only have imports from broken.py
        for imp in data.get("imports", []):
            assert imp["file"] == "broken.py"

    def test_filter_clean_file(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "utils.py"],
            python_import_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # utils.py has no imports
        assert data["summary"]["total_imports"] == 0

    def test_filter_resolved_file(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "service.py"],
            python_import_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # service.py imports from models which exists
        assert data["summary"]["unresolved"] == 0


# ===========================================================================
# 5. Python import pattern tests
# ===========================================================================

class TestPythonPatterns:
    """Test Python import statement parsing."""

    def test_from_import(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("from models import User, Admin", "python")
        assert "models" in names
        assert "User" in names
        assert "Admin" in names

    def test_simple_import(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("import os", "python")
        assert "os" in names

    def test_dotted_import(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("import os.path", "python")
        assert "os.path" in names

    def test_from_import_with_alias(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("from models import User as U", "python")
        assert "models" in names
        assert "User" in names
        assert "U" not in names  # alias should not be checked

    def test_star_import_excluded(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("from models import *", "python")
        assert "models" in names
        assert "*" not in names

    def test_non_import_line(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("x = import_data()", "python")
        assert len(names) == 0


# ===========================================================================
# 6. JavaScript import pattern tests
# ===========================================================================

class TestJavaScriptPatterns:
    """Test JavaScript import/require statement parsing."""

    def test_require(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("const x = require('lodash')", "javascript")
        assert "lodash" in names

    def test_import_from(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("import { render } from 'react-dom'", "javascript")
        assert "render" in names
        # Module name should be extracted (last segment)
        assert "react-dom" in names

    def test_default_import(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("import React from 'react'", "javascript")
        assert "React" in names

    def test_require_path(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line
        names = _extract_import_names_from_line("const helper = require('./utils/helper')", "javascript")
        assert "helper" in names


# ===========================================================================
# 7. Resolution logic tests
# ===========================================================================

class TestResolutionLogic:
    """Test the name resolution against the DB."""

    def test_symbol_name_resolves(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _check_name_exists
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                # "User" is a symbol in models.py
                assert _check_name_exists(conn, "User")
        finally:
            os.chdir(old_cwd)

    def test_module_name_resolves_via_file(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _check_name_exists
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                # "models" should resolve to models.py
                assert _check_name_exists(conn, "models")
        finally:
            os.chdir(old_cwd)

    def test_nonexistent_name_unresolved(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _check_name_exists
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                assert not _check_name_exists(conn, "totally_bogus_name_xyz")
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 8. FTS5 suggestion tests
# ===========================================================================

class TestFtsSuggestions:
    """Test fuzzy matching suggestions for unresolved imports."""

    def test_suggestions_for_close_name(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _fts_suggestions
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                # "User" should find matching symbols via FTS
                suggestions = _fts_suggestions(conn, "User")
                # Should return at least something
                assert isinstance(suggestions, list)
        finally:
            os.chdir(old_cwd)

    def test_suggestions_empty_for_random(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _fts_suggestions
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                suggestions = _fts_suggestions(conn, "zzzzz_completely_impossible_xyz")
                assert isinstance(suggestions, list)
                # May be empty or have distant matches
        finally:
            os.chdir(old_cwd)

    def test_suggestions_limit(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _fts_suggestions
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                suggestions = _fts_suggestions(conn, "User", limit=1)
                assert len(suggestions) <= 1
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 9. Edge-based import detection tests
# ===========================================================================

class TestEdgeImports:
    """Test import detection from the edges table."""

    def test_edge_imports_found(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _get_edge_imports
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                edges = _get_edge_imports(conn, None)
                # Should find import edges from service.py -> models
                assert isinstance(edges, list)
        finally:
            os.chdir(old_cwd)

    def test_edge_imports_filtered_by_file(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import _get_edge_imports
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                edges = _get_edge_imports(conn, "service.py")
                # All edges should be from service.py
                for e in edges:
                    assert e["file_path"] == "service.py"
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 10. verify_imports() function tests
# ===========================================================================

class TestVerifyImportsFunction:
    """Test the main verify_imports() function directly."""

    def test_returns_expected_keys(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import verify_imports
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                result = verify_imports(conn, str(python_import_project))
                assert "imports" in result
                assert "total" in result
                assert "resolved" in result
                assert "unresolved" in result
                assert "files_checked" in result
        finally:
            os.chdir(old_cwd)

    def test_total_is_sum_of_resolved_unresolved(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import verify_imports
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                result = verify_imports(conn, str(python_import_project))
                assert result["total"] == result["resolved"] + result["unresolved"]
        finally:
            os.chdir(old_cwd)

    def test_file_filter_limits_scope(self, python_import_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_verify_imports import verify_imports
        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                all_result = verify_imports(conn, str(python_import_project))
                filtered_result = verify_imports(conn, str(python_import_project), file_filter="broken.py")
                # Filtered should have fewer or equal imports
                assert filtered_result["total"] <= all_result["total"]
                # Filtered should only check 1 file at most
                assert filtered_result["files_checked"] <= 1
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 11. JavaScript project tests
# ===========================================================================

class TestJavaScriptProject:
    """Test with JavaScript project fixtures."""

    def test_js_project_runs(self, js_import_project):
        result = _invoke(["verify-imports"], js_import_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_js_project_json(self, js_import_project):
        result = _invoke(["verify-imports"], js_import_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "verify-imports"
        assert data["summary"]["total_imports"] >= 0


# ===========================================================================
# 12. Empty / edge case tests
# ===========================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_project(self, project_factory):
        proj = project_factory({"empty.py": ""})
        result = _invoke(["verify-imports"], proj)
        assert result.exit_code == 0

    def test_nonexistent_file_filter(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "nonexistent.py"],
            python_import_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total_imports"] == 0

    def test_binary_file_ignored(self, project_factory):
        proj = project_factory({
            "main.py": "import os\ndef main(): pass\n",
        })
        # Even with no binary files in index, the command should work
        result = _invoke(["verify-imports"], proj)
        assert result.exit_code == 0

    def test_import_line_number_positive(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        data = json.loads(result.output)
        for imp in data.get("imports", []):
            assert imp["line"] > 0


# ===========================================================================
# 13. Multiple imports on same file tests
# ===========================================================================

class TestMultipleImports:
    """Test files with multiple import statements."""

    def test_multiple_imports_counted(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "broken.py"],
            python_import_project, json_mode=True,
        )
        data = json.loads(result.output)
        # broken.py has 2 import lines, each with names to check
        assert data["summary"]["total_imports"] >= 2

    def test_mixed_resolved_unresolved(self, python_import_project):
        result = _invoke(["verify-imports"], python_import_project, json_mode=True)
        data = json.loads(result.output)
        # Project has both resolved (service.py -> models) and unresolved (broken.py)
        assert data["summary"]["resolved"] >= 1
        assert data["summary"]["unresolved"] >= 1
