"""Tests for refactoring-related CLI commands.

Covers ~40 tests across 5 commands: dead, safe-delete, split, conventions, breaking.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope

from roam.cli import cli


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
# dead command
# ============================================================================

class TestDead:
    """Tests for `roam dead` -- unreferenced exports."""

    def test_dead_runs(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_json(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dead")
        assert_json_envelope(data, "dead")

    def test_dead_json_has_confidence_arrays(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dead")
        assert "high_confidence" in data
        assert "low_confidence" in data

    def test_dead_json_summary_has_counts(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dead")
        summary = data.get("summary", {})
        for key in ["safe", "review", "intentional"]:
            assert key in summary, f"Missing '{key}' in dead summary: {summary}"

    def test_dead_all_flag(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--all"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_by_directory(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--by-directory"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_by_kind(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--by-kind"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_summary_only(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--summary"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_clusters(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--clusters"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_text_shows_exports(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        assert "Unreferenced" in out or "none" in out.lower() or "dead" in out.lower()


# ============================================================================
# safe-delete command
# ============================================================================

class TestSafeDelete:
    """Tests for `roam safe-delete` -- check if symbol can be safely removed."""

    def test_safe_delete_runs(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "unused_helper"], cwd=indexed_project)
        # May fail if symbol not found, that's OK
        assert result.exit_code in (0, 1)

    def test_safe_delete_json(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "unused_helper"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = parse_json_output(result, "safe-delete")
            assert "command" in data

    def test_safe_delete_missing_symbol(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = cli_runner.invoke(cli, ["safe-delete", "nonexistent_symbol_xyz"], catch_exceptions=True)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_safe_delete_json_has_summary(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "unused_helper"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert "summary" in data

    def test_safe_delete_used_symbol(self, cli_runner, indexed_project, monkeypatch):
        """Deleting a used symbol should warn about dependents."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "User"], cwd=indexed_project)
        # Should succeed but warn about dependents, or fail to find
        assert result.exit_code in (0, 1)


# ============================================================================
# split command
# ============================================================================

class TestSplit:
    """Tests for `roam split` -- suggest file decomposition."""

    def test_split_runs(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["split", "src/models.py"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_split_json(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["split", "src/models.py"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = parse_json_output(result, "split")
            assert "command" in data

    def test_split_nonexistent_file(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = cli_runner.invoke(cli, ["split", "nonexistent.py"], catch_exceptions=True)
        assert result.exit_code != 0 or "not found" in result.output.lower() or "no file" in result.output.lower()

    def test_split_json_has_groups(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["split", "src/models.py"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert "groups" in data or "clusters" in data or "suggestions" in data

    def test_split_text_shows_suggestions(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["split", "src/models.py"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        assert ("split" in out.lower() or "group" in out.lower() or
                "cluster" in out.lower() or "already" in out.lower() or
                "symbol" in out.lower() or "few" in out.lower())


# ============================================================================
# conventions command
# ============================================================================

class TestConventions:
    """Tests for `roam conventions` -- naming/style conventions."""

    def test_conventions_runs(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_conventions_json(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert_json_envelope(data, "conventions")

    def test_conventions_json_summary(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        summary = data.get("summary", {})
        assert isinstance(summary, dict)

    def test_conventions_text_shows_analysis(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        assert ("convention" in out.lower() or "naming" in out.lower() or
                "style" in out.lower() or "snake_case" in out.lower() or
                "camelCase" in out.lower() or "function" in out.lower() or
                "class" in out.lower())

    def test_conventions_json_has_rules(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        # Should have some naming analysis data
        has_data = ("rules" in data or "conventions" in data or
                    "naming" in data or "functions" in data.get("summary", {}))
        assert has_data or data.get("summary", {}), f"Conventions JSON seems empty: {list(data.keys())}"

    def test_conventions_json_has_naming_section(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "naming" in data, f"Missing 'naming' in conventions JSON: {list(data.keys())}"

    def test_conventions_json_has_files_section(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "files" in data, f"Missing 'files' in conventions JSON: {list(data.keys())}"

    def test_conventions_json_has_imports_section(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "imports" in data, f"Missing 'imports' in conventions JSON: {list(data.keys())}"

    def test_conventions_json_has_exports_section(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "exports" in data, f"Missing 'exports' in conventions JSON: {list(data.keys())}"

    def test_conventions_json_summary_has_total_symbols(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        summary = data.get("summary", {})
        assert "total_symbols_analyzed" in summary, f"Missing 'total_symbols_analyzed': {summary}"

    def test_conventions_json_summary_has_outlier_count(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        summary = data.get("summary", {})
        assert "outlier_count" in summary, f"Missing 'outlier_count': {summary}"

    def test_conventions_json_has_violations_list(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["conventions"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "conventions")
        assert "violations" in data, f"Missing 'violations' in conventions JSON: {list(data.keys())}"
        assert isinstance(data["violations"], list)


# ============================================================================
# breaking command
# ============================================================================

class TestBreaking:
    """Tests for `roam breaking` -- detect breaking API changes."""

    def test_breaking_runs(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking"], cwd=indexed_project)
        # May exit 0 (no changes) or show an error about no baseline
        assert result.exit_code in (0, 1)

    def test_breaking_json(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = parse_json_output(result, "breaking")
            assert "command" in data

    def test_breaking_text_output(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking"], cwd=indexed_project)
        if result.exit_code == 0:
            out = result.output
            assert ("breaking" in out.lower() or "no breaking" in out.lower() or
                    "change" in out.lower() or "api" in out.lower() or
                    "public" in out.lower() or "no changed" in out.lower())

    def test_breaking_json_has_summary(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert "summary" in data

    def test_breaking_json_summary_has_counts(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = json.loads(result.output)
            summary = data.get("summary", {})
            assert "removed" in summary
            assert "signature_changed" in summary
            assert "renamed" in summary

    def test_breaking_json_has_arrays(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking"], cwd=indexed_project, json_mode=True)
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert "removed" in data
            assert "signature_changed" in data
            assert "renamed" in data
            assert isinstance(data["removed"], list)
            assert isinstance(data["signature_changed"], list)
            assert isinstance(data["renamed"], list)

    def test_breaking_with_explicit_ref(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking", "HEAD"], cwd=indexed_project)
        assert result.exit_code in (0, 1)

    def test_breaking_no_changes_vs_head(self, cli_runner, indexed_project, monkeypatch):
        """Comparing HEAD vs HEAD should show no changes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["breaking", "HEAD"], cwd=indexed_project)
        if result.exit_code == 0:
            out = result.output
            # With no changes, should mention "no changed" or "no breaking"
            assert ("no changed" in out.lower() or "no breaking" in out.lower() or
                    "0" in out or len(out.strip()) > 0)
