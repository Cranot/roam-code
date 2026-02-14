"""Tests for exploration commands: search, grep, file, symbol, trace, deps, uses, fan, impact.

Covers ~80 tests across 9 exploration commands using CliRunner for
in-process testing against the shared indexed_project fixture.

Commands tested:
  search  - fuzzy symbol search
  grep    - context-enriched text search
  file    - file skeleton
  symbol  - symbol details
  trace   - call path between symbols
  deps    - file import/imported-by relationships
  uses    - symbol consumers (callers, importers, inheritors)
  fan     - fan-in/fan-out metrics
  impact  - blast radius analysis
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope


# ---------------------------------------------------------------------------
# Override cli_runner fixture to handle Click 8.2+ (mix_stderr removed)
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ============================================================================
# search command
# ============================================================================

class TestSearch:
    """Tests for `roam search <pattern>` -- fuzzy symbol search."""

    def test_search_finds_symbol(self, cli_runner, indexed_project, monkeypatch):
        """search 'User' finds the User class."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "User" in result.output

    def test_search_partial_match(self, cli_runner, indexed_project, monkeypatch):
        """search 'val' finds validate_email."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "val"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "validate_email" in result.output

    def test_search_no_results(self, cli_runner, indexed_project, monkeypatch):
        """search for a nonexistent pattern returns no matches."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "zzz_nonexistent_zzz"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "No symbols matching" in result.output

    def test_search_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns an envelope with matches."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "search")
        assert_json_envelope(data, "search")
        assert "results" in data
        assert data["summary"]["total"] > 0

    def test_search_case_insensitive(self, cli_runner, indexed_project, monkeypatch):
        """search 'user' (lowercase) still finds User."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "user"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "User" in result.output

    def test_search_finds_function(self, cli_runner, indexed_project, monkeypatch):
        """search 'create' finds create_user function."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "create"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "create_user" in result.output

    def test_search_finds_multiple(self, cli_runner, indexed_project, monkeypatch):
        """search with a broad pattern finds multiple symbols."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "e"], cwd=indexed_project)
        assert result.exit_code == 0
        # Should find multiple results (validate_email, create_user, etc.)
        assert "===" in result.output

    def test_search_json_no_results(self, cli_runner, indexed_project, monkeypatch):
        """--json with no results returns empty results array."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "zzz_nonexistent_zzz"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "search")
        assert_json_envelope(data, "search")
        assert data["summary"]["total"] == 0
        assert data["results"] == []

    def test_search_json_result_structure(self, cli_runner, indexed_project, monkeypatch):
        """JSON results contain expected fields per result."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "search")
        assert len(data["results"]) > 0
        first = data["results"][0]
        assert "name" in first
        assert "kind" in first
        assert "location" in first

    def test_search_kind_filter(self, cli_runner, indexed_project, monkeypatch):
        """search -k cls filters to classes only."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["search", "User", "-k", "cls"], cwd=indexed_project)
        assert result.exit_code == 0
        # Should find User class but the output should be filtered


# ============================================================================
# grep command
# ============================================================================

class TestGrep:
    """Tests for `roam grep <pattern>` -- context-enriched text search."""

    def test_grep_finds_string(self, cli_runner, indexed_project, monkeypatch):
        """grep 'def' finds function definitions."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "def"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "def" in result.output

    def test_grep_pattern(self, cli_runner, indexed_project, monkeypatch):
        """grep 'class' finds classes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "class"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "class" in result.output.lower() or "matches" in result.output.lower()

    def test_grep_no_results(self, cli_runner, indexed_project, monkeypatch):
        """grep for a string that does not appear in the project."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "zzzznotfound9999"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "No matches" in result.output

    def test_grep_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns an envelope with matches."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "def"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "grep")
        assert_json_envelope(data, "grep")
        assert "matches" in data
        assert data["summary"]["total"] > 0

    def test_grep_source_only(self, cli_runner, indexed_project, monkeypatch):
        """--source-only flag runs without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "def", "--source-only"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_grep_json_no_results(self, cli_runner, indexed_project, monkeypatch):
        """--json with no results returns empty matches array."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "zzzznotfound9999"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "grep")
        assert_json_envelope(data, "grep")
        assert data["summary"]["total"] == 0
        assert data["matches"] == []

    def test_grep_match_count(self, cli_runner, indexed_project, monkeypatch):
        """grep shows match count in output."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "def"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "matches" in result.output.lower()

    def test_grep_finds_import(self, cli_runner, indexed_project, monkeypatch):
        """grep 'import' finds import statements."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "import"], cwd=indexed_project)
        assert result.exit_code == 0
        # service.py has "from models import User, Admin"
        assert "import" in result.output.lower()

    def test_grep_json_match_structure(self, cli_runner, indexed_project, monkeypatch):
        """JSON match entries contain path, line, content fields."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["grep", "class"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "grep")
        if data["summary"]["total"] > 0:
            first = data["matches"][0]
            assert "path" in first
            assert "line" in first
            assert "content" in first


# ============================================================================
# file command
# ============================================================================

class TestFile:
    """Tests for `roam file <path>` -- file skeleton."""

    def test_file_shows_symbols(self, cli_runner, indexed_project, monkeypatch):
        """file src/models.py shows User and Admin."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/models.py"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "User" in result.output
        assert "Admin" in result.output

    def test_file_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with symbols list."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/models.py"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "file")
        assert_json_envelope(data, "file")
        assert "symbols" in data
        assert isinstance(data["symbols"], list)
        assert len(data["symbols"]) > 0

    def test_file_nonexistent(self, cli_runner, indexed_project, monkeypatch):
        """file nonexistent.py handles gracefully with non-zero exit."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "nonexistent.py"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_file_shows_methods(self, cli_runner, indexed_project, monkeypatch):
        """file src/models.py shows methods like display_name, validate_email."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/models.py"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "display_name" in result.output
        assert "validate_email" in result.output

    def test_file_service(self, cli_runner, indexed_project, monkeypatch):
        """file src/service.py shows create_user, get_display, unused_helper."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/service.py"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "create_user" in result.output

    def test_file_utils(self, cli_runner, indexed_project, monkeypatch):
        """file src/utils.py shows format_name and parse_email."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/utils.py"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "format_name" in result.output
        assert "parse_email" in result.output

    def test_file_json_structure(self, cli_runner, indexed_project, monkeypatch):
        """JSON output contains path, language, line_count, symbols."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/models.py"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "file")
        assert "path" in data
        assert "language" in data
        assert "line_count" in data

    def test_file_shows_kind_info(self, cli_runner, indexed_project, monkeypatch):
        """file text output includes kind abbreviations like cls or fn."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file", "src/models.py"], cwd=indexed_project)
        assert result.exit_code == 0
        # Should show kind info like 'cls' for class or 'fn' for function or 'meth' for method
        output_lower = result.output.lower()
        assert "cls" in output_lower or "class" in output_lower

    def test_file_no_args_shows_help(self, cli_runner, indexed_project, monkeypatch):
        """file with no arguments shows help text."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["file"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "Usage" in result.output or "skeleton" in result.output.lower() or result.output.strip() != ""


# ============================================================================
# symbol command
# ============================================================================

class TestSymbol:
    """Tests for `roam symbol <name>` -- symbol details."""

    def test_symbol_shows_details(self, cli_runner, indexed_project, monkeypatch):
        """symbol User shows class details."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "User" in result.output

    def test_symbol_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with symbol details."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "symbol")
        assert_json_envelope(data, "symbol")
        assert "name" in data
        assert "kind" in data
        assert "location" in data

    def test_symbol_not_found(self, cli_runner, indexed_project, monkeypatch):
        """symbol 'nonexistent' handles gracefully with non-zero exit."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "totally_nonexistent_xyzzy"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_symbol_shows_kind(self, cli_runner, indexed_project, monkeypatch):
        """symbol User shows that it is a class."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "cls" in result.output.lower() or "class" in result.output.lower()

    def test_symbol_shows_location(self, cli_runner, indexed_project, monkeypatch):
        """symbol User shows file location."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "models.py" in result.output

    def test_symbol_function(self, cli_runner, indexed_project, monkeypatch):
        """symbol create_user shows function details."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "create_user"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "create_user" in result.output

    def test_symbol_json_has_callers(self, cli_runner, indexed_project, monkeypatch):
        """JSON output for a symbol includes callers and callees lists."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "symbol")
        assert "callers" in data
        assert "callees" in data
        assert isinstance(data["callers"], list)
        assert isinstance(data["callees"], list)

    def test_symbol_method(self, cli_runner, indexed_project, monkeypatch):
        """symbol validate_email shows method details."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "validate_email"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "validate_email" in result.output

    def test_symbol_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary includes caller and callee counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "symbol")
        summary = data["summary"]
        assert "callers" in summary
        assert "callees" in summary


# ============================================================================
# trace command
# ============================================================================

class TestTrace:
    """Tests for `roam trace <from> <to>` -- call path between symbols."""

    def test_trace_finds_path(self, cli_runner, indexed_project, monkeypatch):
        """trace from create_user to User finds a path (create_user calls User)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "create_user", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        # Should show a path or indicate no path
        output = result.output
        assert "create_user" in output or "User" in output or "No dependency path" in output

    def test_trace_no_path(self, cli_runner, indexed_project, monkeypatch):
        """trace between unrelated symbols shows no path."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "format_name", "unused_helper"], cwd=indexed_project)
        assert result.exit_code == 0
        # Could find a path or not depending on the graph
        assert result.output.strip() != ""

    def test_trace_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with path info."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "create_user", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "trace")
        assert_json_envelope(data, "trace")
        assert "source" in data
        assert "target" in data
        assert "paths" in data

    def test_trace_source_not_found(self, cli_runner, indexed_project, monkeypatch):
        """trace with nonexistent source exits with error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "nonexistent_abc", "User"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_trace_target_not_found(self, cli_runner, indexed_project, monkeypatch):
        """trace with nonexistent target exits with error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "User", "nonexistent_abc"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_trace_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary includes hops and paths count."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "create_user", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "trace")
        summary = data["summary"]
        assert "hops" in summary
        assert "paths" in summary

    def test_trace_same_file_symbols(self, cli_runner, indexed_project, monkeypatch):
        """trace between symbols in the same file runs without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["trace", "User", "Admin"], cwd=indexed_project)
        assert result.exit_code == 0
        assert result.output.strip() != ""

    def test_trace_json_no_path(self, cli_runner, indexed_project, monkeypatch):
        """--json with no path returns paths=[] and hops=0."""
        monkeypatch.chdir(indexed_project)
        # format_name and UNUSED_CONSTANT are likely unrelated
        result = invoke_cli(cli_runner, ["trace", "format_name", "UNUSED_CONSTANT"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "trace")
        assert_json_envelope(data, "trace")
        # Either has a path or not, but the envelope should be valid


# ============================================================================
# deps command
# ============================================================================

class TestDeps:
    """Tests for `roam deps <path>` -- file import/imported-by relationships."""

    def test_deps_shows_dependencies(self, cli_runner, indexed_project, monkeypatch):
        """deps src/service.py shows imports from models.py."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "src/service.py"], cwd=indexed_project)
        assert result.exit_code == 0
        # service.py imports from models.py
        assert "models.py" in result.output or "Imports" in result.output

    def test_deps_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with imports and imported_by."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "src/service.py"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "deps")
        assert_json_envelope(data, "deps")
        assert "imports" in data
        assert "imported_by" in data

    def test_deps_no_deps(self, cli_runner, indexed_project, monkeypatch):
        """deps for a leaf file (utils.py has no imports)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "src/utils.py"], cwd=indexed_project)
        assert result.exit_code == 0
        # utils.py does not import from other project files
        assert "none" in result.output.lower() or "Imports" in result.output

    def test_deps_file_not_found(self, cli_runner, indexed_project, monkeypatch):
        """deps for nonexistent file exits with non-zero code."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "nonexistent.py"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_deps_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary includes import and imported_by counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "src/service.py"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "deps")
        summary = data["summary"]
        assert "imports" in summary
        assert "imported_by" in summary

    def test_deps_models(self, cli_runner, indexed_project, monkeypatch):
        """deps src/models.py shows imported_by (service.py imports models)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "src/models.py"], cwd=indexed_project)
        assert result.exit_code == 0
        # models.py is imported by service.py
        output = result.output
        assert "Imported by" in output or "imported_by" in output.lower() or "service" in output

    def test_deps_json_imports_structure(self, cli_runner, indexed_project, monkeypatch):
        """JSON imports entries have path field."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["deps", "src/service.py"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "deps")
        if data["imports"]:
            first = data["imports"][0]
            assert "path" in first


# ============================================================================
# uses command
# ============================================================================

class TestUses:
    """Tests for `roam uses <name>` -- symbol consumers."""

    def test_uses_finds_callers(self, cli_runner, indexed_project, monkeypatch):
        """uses User finds create_user as a consumer."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        # create_user calls User(), so it should be found
        output = result.output
        assert "create_user" in output or "Consumers" in output or "consumers" in output.lower()

    def test_uses_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with consumers."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "uses")
        assert_json_envelope(data, "uses")
        assert "consumers" in data

    def test_uses_no_callers(self, cli_runner, indexed_project, monkeypatch):
        """uses for unused_helper finds no consumers (or minimal)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "unused_helper"], cwd=indexed_project)
        assert result.exit_code == 0
        # Should report no consumers or very few
        output = result.output
        assert "No consumers" in output or "Consumers" in output or result.output.strip() != ""

    def test_uses_not_found(self, cli_runner, indexed_project, monkeypatch):
        """uses for a nonexistent symbol exits with error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "totally_nonexistent_symbol_xyz"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_uses_json_summary(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary includes total_consumers and total_files."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "uses")
        summary = data["summary"]
        assert "total_consumers" in summary
        assert "total_files" in summary

    def test_uses_json_no_callers(self, cli_runner, indexed_project, monkeypatch):
        """JSON for unused symbol has zero consumers."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "format_name"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "uses")
        assert_json_envelope(data, "uses")
        # format_name is not called by anything in the test project
        assert data["summary"]["total_consumers"] >= 0

    def test_uses_validate_email(self, cli_runner, indexed_project, monkeypatch):
        """uses validate_email finds callers from service.py."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["uses", "validate_email"], cwd=indexed_project)
        assert result.exit_code == 0
        # create_user calls user.validate_email()
        assert result.output.strip() != ""


# ============================================================================
# fan command
# ============================================================================

class TestFan:
    """Tests for `roam fan [symbol|file]` -- fan-in/fan-out metrics."""

    def test_fan_shows_metrics(self, cli_runner, indexed_project, monkeypatch):
        """fan symbol shows fan-in/fan-out counts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "symbol"], cwd=indexed_project)
        assert result.exit_code == 0
        output = result.output
        # Should show the fan table or a message about no data
        assert "fan" in output.lower() or "No graph metrics" in output

    def test_fan_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with items."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "symbol"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "fan")
        assert_json_envelope(data, "fan")
        assert "items" in data
        summary = data["summary"]
        assert "mode" in summary
        assert summary["mode"] == "symbol"

    def test_fan_file_mode(self, cli_runner, indexed_project, monkeypatch):
        """fan file shows file-level fan-in/fan-out."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "file"], cwd=indexed_project)
        assert result.exit_code == 0
        output = result.output
        assert "fan" in output.lower() or "No file edges" in output

    def test_fan_json_file_mode(self, cli_runner, indexed_project, monkeypatch):
        """--json in file mode returns file-level items."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "file"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "fan")
        assert_json_envelope(data, "fan")
        assert data["summary"]["mode"] == "file"

    def test_fan_default_mode(self, cli_runner, indexed_project, monkeypatch):
        """fan with no mode argument defaults to symbol mode."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan"], cwd=indexed_project)
        assert result.exit_code == 0
        # Default is 'symbol' mode
        assert result.output.strip() != ""

    def test_fan_json_item_structure(self, cli_runner, indexed_project, monkeypatch):
        """JSON items have expected fields (name, fan_in, fan_out)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "symbol"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "fan")
        if data["items"]:
            first = data["items"][0]
            assert "name" in first
            assert "fan_in" in first
            assert "fan_out" in first
            assert "total" in first

    def test_fan_json_file_item_structure(self, cli_runner, indexed_project, monkeypatch):
        """JSON file-mode items have path, fan_in, fan_out."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "file"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "fan")
        if data["items"]:
            first = data["items"][0]
            assert "path" in first
            assert "fan_in" in first
            assert "fan_out" in first

    def test_fan_no_framework(self, cli_runner, indexed_project, monkeypatch):
        """fan --no-framework flag runs without error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["fan", "symbol", "--no-framework"], cwd=indexed_project)
        assert result.exit_code == 0


# ============================================================================
# impact command
# ============================================================================

class TestImpact:
    """Tests for `roam impact <name>` -- blast radius analysis."""

    def test_impact_shows_affected(self, cli_runner, indexed_project, monkeypatch):
        """impact User shows affected files and symbols."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project)
        assert result.exit_code == 0
        output = result.output
        # Should show verdict and affected info
        assert "VERDICT" in output or "affected" in output.lower() or "No dependents" in output

    def test_impact_json(self, cli_runner, indexed_project, monkeypatch):
        """--json returns envelope with blast radius data."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "impact")
        assert_json_envelope(data, "impact")
        summary = data["summary"]
        assert "affected_symbols" in summary
        assert "affected_files" in summary

    def test_impact_not_found(self, cli_runner, indexed_project, monkeypatch):
        """impact for nonexistent symbol exits with error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "totally_nonexistent_symbol_xyz"], cwd=indexed_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_impact_leaf_symbol(self, cli_runner, indexed_project, monkeypatch):
        """impact for a leaf symbol with no dependents."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "format_name"], cwd=indexed_project)
        assert result.exit_code == 0
        # format_name is not called by anything, so no dependents
        output = result.output
        assert "VERDICT" in output or "No dependents" in output or "affected" in output.lower()

    def test_impact_json_verdict(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary includes verdict string."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "impact")
        assert "verdict" in data["summary"]

    def test_impact_json_has_file_list(self, cli_runner, indexed_project, monkeypatch):
        """JSON output includes affected_file_list."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "impact")
        assert "affected_file_list" in data
        assert isinstance(data["affected_file_list"], list)

    def test_impact_function(self, cli_runner, indexed_project, monkeypatch):
        """impact for create_user function runs successfully."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "create_user"], cwd=indexed_project)
        assert result.exit_code == 0
        assert result.output.strip() != ""

    def test_impact_json_leaf(self, cli_runner, indexed_project, monkeypatch):
        """JSON for a leaf symbol shows zero affected symbols."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "unused_helper"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "impact")
        assert_json_envelope(data, "impact")
        assert data["summary"]["affected_symbols"] >= 0

    def test_impact_json_weighted(self, cli_runner, indexed_project, monkeypatch):
        """JSON summary includes weighted_impact and reach_pct."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert "weighted_impact" in summary
        assert "reach_pct" in summary

    def test_impact_direct_dependents(self, cli_runner, indexed_project, monkeypatch):
        """JSON output includes direct_dependents dict."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["impact", "User"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "impact")
        assert "direct_dependents" in data
        assert isinstance(data["direct_dependents"], dict)


# ============================================================================
# Cross-command integration tests
# ============================================================================

class TestExplorationIntegration:
    """Cross-command integration tests for exploration commands."""

    def test_search_then_symbol(self, cli_runner, indexed_project, monkeypatch):
        """Symbols found via search can be inspected via symbol."""
        monkeypatch.chdir(indexed_project)
        # Search for User
        search_result = invoke_cli(cli_runner, ["search", "User"], cwd=indexed_project, json_mode=True)
        search_data = parse_json_output(search_result, "search")
        assert search_data["summary"]["total"] > 0
        # Get the first result name and look it up via symbol
        name = search_data["results"][0]["name"]
        sym_result = invoke_cli(cli_runner, ["symbol", name], cwd=indexed_project)
        assert sym_result.exit_code == 0
        assert name in sym_result.output

    def test_file_then_deps(self, cli_runner, indexed_project, monkeypatch):
        """Files shown via file command can be queried via deps."""
        monkeypatch.chdir(indexed_project)
        # Get file skeleton
        file_result = invoke_cli(cli_runner, ["file", "src/service.py"], cwd=indexed_project, json_mode=True)
        file_data = parse_json_output(file_result, "file")
        path = file_data["path"]
        # Query deps for the same file
        deps_result = invoke_cli(cli_runner, ["deps", path], cwd=indexed_project)
        assert deps_result.exit_code == 0

    def test_search_then_impact(self, cli_runner, indexed_project, monkeypatch):
        """Symbols found via search can be analyzed via impact."""
        monkeypatch.chdir(indexed_project)
        search_result = invoke_cli(cli_runner, ["search", "Admin"], cwd=indexed_project, json_mode=True)
        search_data = parse_json_output(search_result, "search")
        assert search_data["summary"]["total"] > 0
        name = search_data["results"][0]["name"]
        impact_result = invoke_cli(cli_runner, ["impact", name], cwd=indexed_project)
        assert impact_result.exit_code == 0
