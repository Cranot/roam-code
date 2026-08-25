"""Tests for the verify-imports command -- hallucination firewall for import statements."""

from __future__ import annotations

import json
import os

import click
import pytest
from click.testing import CliRunner


def test_windows_stdlib_table_is_platform_independent():
    from roam.commands.cmd_verify_imports import _is_stdlib_module

    assert _is_stdlib_module("msvcrt")
    assert _is_stdlib_module("winreg")
    assert _is_stdlib_module("_winapi")


def test_nonsense_name_is_not_promoted_to_stdlib():
    from roam.commands.cmd_verify_imports import _is_stdlib_module

    assert not _is_stdlib_module("definitely_missing_roam_precision_pkg")


def test_optional_extra_and_uninstalled_self_import_are_optional_unresolved(project_factory, monkeypatch):
    proj = project_factory(
        {
            "app.py": "import optional_accelerator\nimport demo_pkg\n",
            "pyproject.toml": (
                "[project]\n"
                'name = "demo-pkg"\n'
                'version = "1.0"\n'
                "[project.optional-dependencies]\n"
                'fast = ["optional-accelerator>=1"]\n'
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.commands.cmd_verify_imports import verify_imports_for_connection
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        result = verify_imports_for_connection(conn, str(proj))
    statuses = {row["name"]: row["status"] for row in result["imports"]}
    assert statuses["optional_accelerator"] == "optional-unresolved"
    assert statuses["demo_pkg"] == "optional-unresolved"
    assert result["unresolved"] == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def python_import_project(project_factory):
    """Python project with both resolvable and unresolvable imports."""
    return project_factory(
        {
            "models.py": (
                "class User:\n    def __init__(self, name):\n        self.name = name\n\nclass Admin(User):\n    pass\n"
            ),
            "service.py": ("from models import User, Admin\n\ndef create_user(name):\n    return User(name)\n"),
            "broken.py": (
                "from nonexistent_module import FakeClass\n"
                "import totally_missing\n"
                "\n"
                "def broken_func():\n"
                "    return FakeClass()\n"
            ),
            "utils.py": ("def helper():\n    return 42\n"),
        }
    )


@pytest.fixture
def js_import_project(project_factory):
    """JavaScript project with import/require statements."""
    return project_factory(
        {
            "app.js": (
                "import { render } from 'renderer'\nimport Config from 'config'\n\nfunction main() { render(); }\n"
            ),
            "renderer.js": ("function render() { return 'ok'; }\nmodule.exports = { render };\n"),
            "config.js": ("const Config = { debug: false };\nmodule.exports = Config;\n"),
            "broken.js": ("const missing = require('does_not_exist')\nimport { ghost } from 'phantom_module'\n"),
        }
    )


@pytest.fixture
def clean_project(project_factory):
    """Project with no imports at all."""
    return project_factory(
        {
            "main.py": ("def main():\n    print('hello')\n"),
            "utils.py": ("def add(a, b):\n    return a + b\n"),
        }
    )


@pytest.fixture
def all_resolved_project(project_factory):
    """Project where all imports resolve correctly."""
    return project_factory(
        {
            "models.py": ("class User:\n    pass\n"),
            "service.py": ("from models import User\n\ndef get_user():\n    return User()\n"),
        }
    )


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
            python_import_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should only have imports from broken.py
        for imp in data.get("imports", []):
            assert imp["file"] == "broken.py"

    def test_filter_clean_file(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "utils.py"],
            python_import_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # utils.py has no imports
        assert data["summary"]["total_imports"] == 0

    def test_filter_resolved_file(self, python_import_project):
        result = _invoke(
            ["verify-imports", "--file", "service.py"],
            python_import_project,
            json_mode=True,
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
        # Module-only contract (2026-06-12): see test_from_import_with_alias.
        assert names == ["models"]

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
        # Module-only contract (2026-06-12): member names are not validated —
        # stdlib members and internal re-exports made member checks FP-heavy
        # (28 FPs on 4 of this repo's own files). The module is the signal.
        assert names == ["models"]

    def test_star_import_excluded(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line

        names = _extract_import_names_from_line("from models import *", "python")
        assert "models" in names
        assert "*" not in names

    def test_non_import_line(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line

        names = _extract_import_names_from_line("x = import_data()", "python")
        assert len(names) == 0


class TestTripleQuoteTracking:
    """Lines inside triple-quoted strings (docstrings, fixture blobs) are
    string content, not imports — the FP class found 2026-06-12 when the
    scanner flagged import-shaped text inside its own docstring."""

    def test_docstring_lines_are_skipped(self):
        from roam.commands.cmd_verify_imports import _track_triple_quote_state

        state, inside = _track_triple_quote_state('FIXTURE = """', None)
        assert state == '"""' and not inside
        state, inside = _track_triple_quote_state("import phantom_module_zq", state)
        assert state == '"""' and inside
        state, inside = _track_triple_quote_state('"""', state)
        assert state is None and inside

    def test_single_line_string_does_not_open_state(self):
        from roam.commands.cmd_verify_imports import _track_triple_quote_state

        state, inside = _track_triple_quote_state('x = """import os"""', None)
        assert state is None and not inside

    def test_delimiter_in_comment_is_ignored(self):
        from roam.commands.cmd_verify_imports import _track_triple_quote_state

        state, inside = _track_triple_quote_state('# prefer """ for docstrings', None)
        assert state is None and not inside

    def test_mixed_delimiters_do_not_close_each_other(self):
        from roam.commands.cmd_verify_imports import _track_triple_quote_state

        state, _ = _track_triple_quote_state("BLOB = '''", None)
        state, inside = _track_triple_quote_state('still inside """ here', state)
        assert state == "'''" and inside


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
        # Module-path-only contract (2026-06-12): braced members ride the
        # module; the package specifier is the validation target.
        assert names == ["react-dom"]

    def test_default_import(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line

        names = _extract_import_names_from_line("import React from 'react'", "javascript")
        # Module-path-only contract (2026-06-12): the default-import NAME is
        # a member binding; the package specifier is the validation target.
        assert names == ["react"]

    def test_require_path(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line

        names = _extract_import_names_from_line("const helper = require('./utils/helper')", "javascript")
        assert names == ["./utils/helper"]


# ===========================================================================
# 7. Resolution logic tests
# ===========================================================================


class TestResolutionLogic:
    """Test the name resolution against the DB."""

    def test_symbol_name_resolves(self, python_import_project):
        from roam.commands.cmd_verify_imports import _check_name_exists
        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                # "User" is a symbol in models.py
                assert _check_name_exists(conn, "User")
        finally:
            os.chdir(old_cwd)

    def test_module_name_resolves_via_file(self, python_import_project):
        from roam.commands.cmd_verify_imports import _check_name_exists
        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                # "models" should resolve to models.py
                assert _check_name_exists(conn, "models")
        finally:
            os.chdir(old_cwd)

    def test_nonexistent_name_unresolved(self, python_import_project):
        from roam.commands.cmd_verify_imports import _check_name_exists
        from roam.db.connection import open_db

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
        from roam.commands.cmd_verify_imports import _fts_suggestions
        from roam.db.connection import open_db

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
        from roam.commands.cmd_verify_imports import _fts_suggestions
        from roam.db.connection import open_db

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
        from roam.commands.cmd_verify_imports import _fts_suggestions
        from roam.db.connection import open_db

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
        from roam.commands.cmd_verify_imports import _get_edge_imports
        from roam.db.connection import open_db

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
        from roam.commands.cmd_verify_imports import _get_edge_imports
        from roam.db.connection import open_db

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
# 10. verify_imports_for_connection() function tests
# ===========================================================================


class TestVerifyImportsFunction:
    """Test the main verify_imports_for_connection() helper directly."""

    def test_returns_expected_keys(self, python_import_project):
        from roam.commands.cmd_verify_imports import verify_imports_for_connection
        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                result = verify_imports_for_connection(conn, str(python_import_project))
                assert "imports" in result
                assert "total" in result
                assert "resolved" in result
                assert "unresolved" in result
                assert "files_checked" in result
        finally:
            os.chdir(old_cwd)

    def test_total_is_sum_of_resolved_unresolved(self, python_import_project):
        from roam.commands.cmd_verify_imports import verify_imports_for_connection
        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                result = verify_imports_for_connection(conn, str(python_import_project))
                assert result["total"] == result["resolved"] + result["unresolved"]
        finally:
            os.chdir(old_cwd)

    def test_file_filter_limits_scope(self, python_import_project):
        from roam.commands.cmd_verify_imports import verify_imports_for_connection
        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(python_import_project))
            with open_db(readonly=True) as conn:
                all_result = verify_imports_for_connection(conn, str(python_import_project))
                filtered_result = verify_imports_for_connection(
                    conn, str(python_import_project), file_filter="broken.py"
                )
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

    def test_js_resolver_builtins_dependencies_and_relative_paths(self, project_factory):
        project = project_factory(
            {
                "packages/app/package.json": json.dumps(
                    {
                        "dependencies": {"lodash": "^4"},
                        "devDependencies": {"vitest": "^1"},
                        "peerDependencies": {"react": "^18"},
                    }
                ),
                "packages/app/src/main.ts": (
                    'import nodeFs from "node:fs";\n'
                    'import bareFs from "fs";\n'
                    'import lodash from "lodash";\n'
                    'import merge from "lodash/merge";\n'
                    'import vitest from "vitest";\n'
                    'import jsxRuntime from "react/jsx-runtime";\n'
                    'import helper from "./helper";\n'
                    'import shared from "../shared";\n'
                    'import missingPackage from "genuinely-missing";\n'
                    'import missingRelative from "./missing";\n'
                ),
                "packages/app/src/helper.ts": "export default 1;\n",
                "packages/app/shared/index.ts": "export default 2;\n",
                # Prove relative and bare imports do not resolve by basename elsewhere.
                "other/missing.ts": "export default 3;\n",
                "other/genuinely-missing.ts": "export default 4;\n",
            }
        )

        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        imports = {row["name"]: row["status"] for row in json.loads(result.output)["imports"]}

        for name in (
            "node:fs",
            "fs",
            "lodash",
            "lodash/merge",
            "vitest",
            "react/jsx-runtime",
            "./helper",
            "../shared",
        ):
            assert imports[name] == "resolved", name
        assert imports["genuinely-missing"] == "unresolved"
        assert imports["./missing"] == "unresolved"


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
            python_import_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total_imports"] == 0

    def test_binary_file_ignored(self, project_factory):
        proj = project_factory(
            {
                "main.py": "import os\ndef main(): pass\n",
            }
        )
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
            python_import_project,
            json_mode=True,
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


class TestJsFirewallSemantics:
    """2026-06-12 — the JS side of the in-loop firewall. Dogfooded on a
    production Vue3 app: member names + npm packages + Vite alias dirs
    produced 30 FPs on one SFC before these contracts."""

    def test_js_import_module_path_only(self):
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line

        names = _extract_import_names_from_line('import { ref, computed } from "vue";', "vue")
        assert names == ["vue"]  # members ride the module
        names = _extract_import_names_from_line('import Modal from "@/components/Modal.vue";', "typescript")
        assert names == ["@/components/Modal.vue"]  # FULL path kept

    def test_js_declared_package_matching(self):
        from roam.commands.cmd_verify_imports import _js_module_is_declared

        deps = frozenset({"vue", "@tanstack/vue-query", "lodash"})
        assert _js_module_is_declared("vue", deps)
        assert _js_module_is_declared("@tanstack/vue-query", deps)
        assert _js_module_is_declared("@tanstack/vue-query/devtools", deps)
        assert _js_module_is_declared("lodash/debounce", deps)
        assert not _js_module_is_declared("totally-fake-package", deps)
        assert not _js_module_is_declared("./relative/path", deps)

    def test_package_json_declared_packages(self, tmp_path):
        from roam.commands.cmd_verify_imports import _declared_js_dependency_packages

        (tmp_path / "package.json").write_text('{"dependencies": {"vue": "^3"}, "devDependencies": {"vitest": "^1"}}')
        deps = _declared_js_dependency_packages(str(tmp_path))
        assert deps == frozenset({"vue", "vitest"})
        assert _declared_js_dependency_packages(str(tmp_path / "nope")) == frozenset()

    def test_node_builtins_resolved(self):
        from roam.commands.cmd_verify_imports import _is_node_builtin

        for mod in ("crypto", "node:crypto", "fs", "fs/promises", "path", "os"):
            assert _is_node_builtin(mod), mod
        assert _is_node_builtin("node:future-built-in")
        assert not _is_node_builtin("totally-fake-package")
        assert not _is_node_builtin("./crypto")  # relative path, not a builtin

    def test_workspaces_merge(self, tmp_path):
        """Monorepo: root workspaces glob pulls in workspace deps AND the
        workspace package's own name (intra-monorepo `@myorg/utils` imports)."""
        from roam.commands.cmd_verify_imports import _declared_js_dependency_packages

        root = tmp_path / "mono"
        (root / "packages" / "x").mkdir(parents=True)
        (root / "package.json").write_text('{"dependencies": {"vue": "^3"}, "workspaces": ["packages/*"]}')
        (root / "packages" / "x" / "package.json").write_text(
            '{"name": "@myorg/utils", "dependencies": {"left-pad": "^1"}}'
        )
        deps = _declared_js_dependency_packages(str(root))
        assert deps == frozenset({"vue", "left-pad", "@myorg/utils"})

    def test_workspaces_yarn_object_form(self, tmp_path):
        from roam.commands.cmd_verify_imports import _declared_js_dependency_packages

        root = tmp_path / "mono2"
        (root / "pkgs" / "a").mkdir(parents=True)
        (root / "package.json").write_text('{"workspaces": {"packages": ["pkgs/*"]}}')
        (root / "pkgs" / "a" / "package.json").write_text('{"name": "@org/a", "devDependencies": {"vitest": "^1"}}')
        deps = _declared_js_dependency_packages(str(root))
        assert deps == frozenset({"@org/a", "vitest"})

    def test_tsconfig_alias_parsing_jsonc(self, tmp_path):
        """tsconfig allows // comments + trailing commas — both must survive."""
        from roam.commands.cmd_verify_imports import _js_path_aliases

        root = tmp_path / "tsproj"
        root.mkdir()
        (root / "tsconfig.json").write_text(
            "{\n"
            "  // Vite default alias\n"
            "  /* block comment */\n"
            '  "compilerOptions": {\n'
            '    "paths": {\n'
            '      "@/*": ["./src/*"],\n'  # trailing comma below
            "    },\n"
            "  },\n"
            "}\n"
        )
        aliases = _js_path_aliases(str(root))
        assert aliases == {"@/*": ["./src/*"]}

    def test_jsconfig_fallback_when_no_tsconfig(self, tmp_path):
        from roam.commands.cmd_verify_imports import _js_path_aliases

        root = tmp_path / "jsproj"
        root.mkdir()
        (root / "jsconfig.json").write_text('{"compilerOptions": {"paths": {"~/*": ["./app/*"]}}}')
        assert _js_path_aliases(str(root)) == {"~/*": ["./app/*"]}
        # No config at all -> empty dict, no crash.
        empty = tmp_path / "none"
        empty.mkdir()
        assert _js_path_aliases(str(empty)) == {}

    def test_alias_rewrite_matching(self):
        """Unit-level alias-prefixed specifier rewriting (wildcard + exact)."""
        from roam.commands.cmd_verify_imports import _rewrite_js_alias

        aliases = {"@/*": ["./src/*"], "utils": ["./src/utils/index.ts"]}
        assert _rewrite_js_alias("@/components/Modal.vue", aliases) == ["./src/components/Modal.vue"]
        assert _rewrite_js_alias("utils", aliases) == ["./src/utils/index.ts"]
        assert _rewrite_js_alias("vue", aliases) == []  # bare package: no alias
        assert _rewrite_js_alias("utils/deep", aliases) == []  # exact key, no prefix match

    def test_lru_cache_keyed_on_root(self, tmp_path):
        """Two roots give two answers — the per-process cache must not bleed."""
        from roam.commands.cmd_verify_imports import (
            _declared_js_dependency_packages,
            _js_path_aliases,
        )

        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "package.json").write_text('{"dependencies": {"vue": "^3"}}')
        (b / "package.json").write_text('{"dependencies": {"react": "^18"}}')
        (a / "tsconfig.json").write_text('{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}')
        assert _declared_js_dependency_packages(str(a)) == frozenset({"vue"})
        assert _declared_js_dependency_packages(str(b)) == frozenset({"react"})
        # Repeat hits the cache and stays correct per-root.
        assert _declared_js_dependency_packages(str(a)) == frozenset({"vue"})
        assert _js_path_aliases(str(a)) == {"@/*": ["./src/*"]}
        assert _js_path_aliases(str(b)) == {}


# ===========================================================================
# 14. Documentation-file exclusion (.md / .mdx)
# ===========================================================================


class TestDocFileExclusion:
    """``import`` lines inside ``.md`` / ``.mdx`` fenced code blocks are
    documentation examples, not real imports. Dogfooded 2026-07-15: the
    firewall flagged ``import x`` inside a markdown code fence as an
    unresolved hallucination. Docs are skipped by default; ``--include-docs``
    restores the old scan-everything behaviour."""

    @pytest.fixture
    def markdown_import_project(self, project_factory):
        return project_factory(
            {
                "real.py": "def thing():\n    return 1\n",
                "guide.md": (
                    "# Usage\n\n```python\nimport phantom_md_pkg_xyz\nfrom another_fake_md_mod import Thing\n```\n"
                ),
            }
        )

    def test_markdown_import_not_reported_by_default(self, markdown_import_project):
        result = _invoke(["verify-imports"], markdown_import_project, json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # No import row may originate from a .md file, and neither fenced
        # example name may appear.
        assert not any(imp["file"].endswith(".md") for imp in data["imports"]), data["imports"]
        names = {imp["name"] for imp in data["imports"]}
        assert "phantom_md_pkg_xyz" not in names
        assert "another_fake_md_mod" not in names

    def test_markdown_import_reported_with_include_docs(self, markdown_import_project):
        result = _invoke(
            ["verify-imports", "--include-docs"],
            markdown_import_project,
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        md_rows = {imp["name"]: imp["status"] for imp in data["imports"] if imp["file"].endswith(".md")}
        # The opt-in restores the pre-fix behaviour: the code-fence imports
        # are scanned and flagged as unresolved.
        assert md_rows.get("phantom_md_pkg_xyz") == "unresolved", data["imports"]
        assert md_rows.get("another_fake_md_mod") == "unresolved", data["imports"]


# ===========================================================================
# 15. Multi-line import statement joining (task #148)
# ===========================================================================


def _rows_by_name(result):
    data = json.loads(result.output)
    return {imp["name"]: imp for imp in data["imports"]}


class TestMultiLineImportJoining:
    """Task #148 — multi-line import statements were invisible per-line.

    Every import regex is single-line: ``import {\\n  a,\\n} from 'x';``
    matched ZERO regexes (the opener has no specifier; ``} from 'x';`` fails
    the ``^\\s*import`` anchor) and ``from pkg import (\\n  alpha,\\n)``
    dropped the ENTIRE module path (the ``(`` fails ``_PY_FROM_IMPORT``'s
    member group). Both shapes passed the firewall unverified — a false
    negative on exactly the hallucination shape the command exists to catch.
    The fix pre-joins physical lines into one logical statement, attributed
    to the FIRST physical line number."""

    # -- must fire ---------------------------------------------------------

    def test_multiline_esm_import_reported_at_opening_line(self, project_factory):
        project = project_factory(
            {
                "app.js": (
                    "// header comment\n"
                    "import {\n"
                    "  alpha,\n"
                    "  beta\n"
                    "} from 'totally-fake-esm-pkg';\n"
                    "export function main() { return alpha + beta; }\n"
                ),
            }
        )
        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        row = rows.get("totally-fake-esm-pkg")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        # Attributed to the statement's OPENING line, not the closer.
        assert row["line"] == 2, row

    def test_python_parenthesized_from_import_module_reported(self, project_factory):
        project = project_factory(
            {
                "deep.py": ('"""Module docstring."""\nfrom totally.fake.pkg import (\n    alpha,\n    beta,\n)\n'),
            }
        )
        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        row = rows.get("totally.fake.pkg")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        assert row["line"] == 2, row

    def test_python_backslash_continuation_module_reported(self, project_factory):
        """The broken backslash shape is the backslash IMMEDIATELY after
        ``import`` — ``[\\w*]`` fails on ``\\`` and the whole statement
        vanished. (``import alpha, \\`` was already caught pre-fix: the
        module group closes before the backslash — measured on 8009b0c0.)
        The second statement pins that already-working shape against
        regression, at its own line."""
        project = project_factory(
            {
                "cont.py": (
                    "from other_fake_mod_zz import \\\n"
                    "    alpha, beta\n"
                    "from second_fake_mod_qq import gamma, \\\n"
                    "    delta\n"
                ),
            }
        )
        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        row = rows.get("other_fake_mod_zz")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        assert row["line"] == 1, row
        row = rows.get("second_fake_mod_qq")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        assert row["line"] == 3, row

    # -- import type: consistent with the single-line treatment ------------

    def test_single_line_import_type_is_not_extracted(self):
        """Pins today's single-line behaviour: ``import type`` binds no
        runtime value and no regex matches it — it is never verified."""
        from roam.commands.cmd_verify_imports import _extract_import_names_from_line

        names = _extract_import_names_from_line("import type { Foo } from 'phantom-type-pkg';", "typescript")
        assert names == []

    def test_multiline_import_type_matches_single_line_treatment(self, project_factory):
        """Multi-line ``import type`` joins to the same logical statement the
        single-line form is, and gets the same treatment: not reported. The
        sibling VALUE import in the same file proves the scan itself ran."""
        project = project_factory(
            {
                "typed.ts": (
                    "import type {\n"
                    "  OnlyAType,\n"
                    "} from 'phantom-type-only-pkg';\n"
                    "import {\n"
                    "  realValue,\n"
                    "} from 'phantom-value-pkg';\n"
                    "export const x = realValue;\n"
                ),
            }
        )
        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        assert "phantom-type-only-pkg" not in rows, rows
        row = rows.get("phantom-value-pkg")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        assert row["line"] == 4, row

    # -- must NOT fire: the negative controls ------------------------------

    def test_python_docstring_import_shapes_cannot_arm_the_joiner(self, project_factory):
        """Import-shaped text inside a docstring is string content: the
        triple-quote state machine keeps it away from the accumulator, so an
        unterminated ``import {`` there must not swallow later real code."""
        project = project_factory(
            {
                "doc.py": (
                    'BLOB = """\nimport {\nfrom fake_inside_docstring import (\n"""\nimport real_after_docstring_pkg\n'
                ),
            }
        )
        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        assert "fake_inside_docstring" not in rows, rows
        row = rows.get("real_after_docstring_pkg")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        assert row["line"] == 5, row

    def test_js_template_literal_import_does_not_swallow_following_code(self, project_factory):
        """An unterminated ``import {`` inside a template literal arms the
        accumulator (the raw-line scanner has no JS string model), but the
        next REAL import opener aborts the join and replays per-line — the
        real statement is still reported at its own line."""
        project = project_factory(
            {
                "tpl.js": ("const T = `\nimport {\n  swallowed\n`;\nimport { real } from 'actually-fake-pkg';\n"),
            }
        )
        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        row = rows.get("actually-fake-pkg")
        assert row is not None, result.output
        assert row["status"] == "unresolved", row
        assert row["line"] == 5, row

    def test_cap_overflow_degrades_to_per_line_without_error(self, project_factory):
        """A pathological unterminated brace buffers at most
        ``_JOINER_MAX_LINES`` physical lines; on overflow the buffer replays
        per-line (a ``require`` caught mid-buffer keeps its own line number,
        exactly the pre-joiner behaviour) and scanning continues."""
        lines = ["import {"]
        lines += [f"const v{i} = {i};" for i in range(40)]
        lines.append("const mid = require('buffered-mid-fake-req');")
        lines += [f"const w{i} = {i};" for i in range(40)]
        lines.append("const late = require('after-cap-fake-req');")
        mid_line = lines.index("const mid = require('buffered-mid-fake-req');") + 1
        late_line = lines.index("const late = require('after-cap-fake-req');") + 1
        project = project_factory({"huge.js": "\n".join(lines) + "\n"})

        result = _invoke(["verify-imports"], project, json_mode=True)
        assert result.exit_code == 0, result.output
        rows = _rows_by_name(result)
        mid = rows.get("buffered-mid-fake-req")
        assert mid is not None, result.output
        assert mid["status"] == "unresolved" and mid["line"] == mid_line, mid
        late = rows.get("after-cap-fake-req")
        assert late is not None, result.output
        assert late["status"] == "unresolved" and late["line"] == late_line, late

    # -- joiner unit level -------------------------------------------------

    def test_joiner_single_line_statements_pass_through_verbatim(self):
        from roam.commands.cmd_verify_imports import _ImportStatementJoiner

        j = _ImportStatementJoiner("js")
        line = "import { a } from 'x';\n"
        assert j.feed(3, line) == [(3, line)]
        j = _ImportStatementJoiner("py")
        line = "import os\n"
        assert j.feed(7, line) == [(7, line)]

    def test_joiner_attributes_merged_statement_to_opening_line(self):
        from roam.commands.cmd_verify_imports import _ImportStatementJoiner

        j = _ImportStatementJoiner("js")
        assert j.feed(10, "import {\n") == []
        assert j.feed(11, "  a,\n") == []
        assert j.feed(12, "} from 'pkg';\n") == [(10, "import { a, } from 'pkg';")]

    def test_joiner_python_paren_join_yields_the_module(self):
        from roam.commands.cmd_verify_imports import (
            _extract_import_names_from_line,
            _ImportStatementJoiner,
        )

        j = _ImportStatementJoiner("py")
        assert j.feed(1, "from fakepkg import (\n") == []
        assert j.feed(2, "    alpha,\n") == []
        units = j.feed(3, ")\n")
        assert len(units) == 1
        num, text = units[0]
        assert num == 1
        assert _extract_import_names_from_line(text, "python") == ["fakepkg"]

    def test_joiner_cap_overflow_replays_buffered_lines_verbatim(self):
        from roam.commands.cmd_verify_imports import _JOINER_MAX_LINES, _ImportStatementJoiner

        j = _ImportStatementJoiner("js")
        assert j.feed(1, "import {\n") == []
        out = []
        for num in range(2, _JOINER_MAX_LINES + 1):
            out.extend(j.feed(num, f"  junk{num},\n"))
        assert len(out) == _JOINER_MAX_LINES
        assert out[0] == (1, "import {\n")
        assert out[-1] == (_JOINER_MAX_LINES, f"  junk{_JOINER_MAX_LINES},\n")
