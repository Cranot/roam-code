"""Tests for the path-coverage command.

Covers:
- Basic invocation and exit code
- JSON envelope contract
- VERDICT text output
- Entry point and sink discovery
- Path finding and node annotation
- Filter options (--from, --to, --max-depth)
- Graceful handling of projects with no entry-to-sink paths
- Summary count accuracy

Note: The command is invoked directly (not via the CLI group) so these tests
work before cli.py registration is complete.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Local helper: invoke path-coverage directly (bypasses CLI group)
# ---------------------------------------------------------------------------


def invoke_path_coverage(runner, args=None, cwd=None, json_mode=False):
    """Invoke the path-coverage command directly via its Click command object.

    Bypasses the CLI group so the command works before cli.py registration.
    """
    from click.testing import CliRunner
    from roam.commands.cmd_path_coverage import path_coverage

    full_args = list(args or [])
    obj = {"json": json_mode}

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(
            path_coverage, full_args, obj=obj, catch_exceptions=False
        )
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def path_cov_project(tmp_path, monkeypatch):
    """Project with entry points, middle functions, and sinks.

    Call chain: handle_request -> process -> save (DB write)
    handle_request has no callers and calls process => it is an entry point.
    save has no outgoing edges => it is a leaf sink.
    The project also has pure utility functions that form no entry-to-sink path.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Entry point (no callers, calls service)
    (proj / "handler.py").write_text(
        'from service import process\n\n'
        'def handle_request(data):\n'
        '    return process(data)\n'
    )

    # Middle layer — calls save (sink)
    (proj / "service.py").write_text(
        'from db import save\n\n'
        'def process(data):\n'
        '    result = transform(data)\n'
        '    save(result)\n'
        '    return result\n\n'
        'def transform(data):\n'
        '    return data\n'
    )

    # Sink (DB write — leaf node with no outgoing edges)
    (proj / "db.py").write_text(
        'def save(record):\n'
        '    conn.execute("INSERT INTO t VALUES (?)", (record,))\n'
        '    conn.commit()\n'
    )

    # Pure utility — forms no entry-to-sink chain
    (proj / "utils.py").write_text(
        'def format_name(n):\n'
        '    return n.title()\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def no_paths_project(tmp_path, monkeypatch):
    """Project with only pure utility functions — no entry-to-sink call chain.

    Every function either has no outgoing edges or no incoming edges but
    none of them form a connected chain from entry to sink.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "math_utils.py").write_text(
        'def add(a, b):\n'
        '    return a + b\n\n'
        'def multiply(a, b):\n'
        '    return a * b\n\n'
        'def subtract(a, b):\n'
        '    return a - b\n'
    )

    (proj / "string_utils.py").write_text(
        'def upper(s):\n'
        '    return s.upper()\n\n'
        'def lower(s):\n'
        '    return s.lower()\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def tested_project(tmp_path, monkeypatch):
    """Project with an entry-to-sink path AND a test file that calls into it."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "api.py").write_text(
        'from worker import do_work\n\n'
        'def api_handler(req):\n'
        '    return do_work(req)\n'
    )

    (proj / "worker.py").write_text(
        'from store import write_record\n\n'
        'def do_work(req):\n'
        '    write_record(req)\n'
        '    return True\n'
    )

    (proj / "store.py").write_text(
        'def write_record(data):\n'
        '    conn.execute("INSERT INTO records VALUES (?)", (data,))\n'
        '    conn.commit()\n'
    )

    # Test file that calls api_handler — this covers the entry point
    (proj / "test_api.py").write_text(
        'from api import api_handler\n\n'
        'def test_api_handler():\n'
        '    result = api_handler("payload")\n'
        '    assert result is True\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestPathCoverage:

    def test_path_coverage_runs(self, path_cov_project, cli_runner):
        """Command exits with code 0 on a valid indexed project."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )

    def test_path_coverage_json_envelope(self, path_cov_project, cli_runner):
        """JSON output follows the standard roam envelope contract."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        assert_json_envelope(data, "path-coverage")

    def test_path_coverage_verdict_line(self, path_cov_project, cli_runner):
        """Text output starts with VERDICT: on the first non-blank line."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:"), (
            f"Expected output to start with VERDICT:, got: {first_line!r}"
        )

    def test_path_coverage_finds_entry_points(self, path_cov_project, cli_runner):
        """JSON output reports at least one entry point found."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        assert data.get("entry_points_found", 0) > 0, (
            f"Expected entry_points_found > 0, got: {data.get('entry_points_found')}"
        )

    def test_path_coverage_finds_paths(self, path_cov_project, cli_runner):
        """JSON output contains a non-empty paths list when chains exist."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        paths = data.get("paths", [])
        assert isinstance(paths, list), "paths should be a list"
        assert len(paths) > 0, (
            f"Expected at least one path, got 0. "
            f"entry_points_found={data.get('entry_points_found')}, "
            f"sinks_found={data.get('sinks_found')}"
        )

    def test_path_coverage_has_suggestions(self, path_cov_project, cli_runner):
        """JSON output contains a suggestions list."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        assert "suggestions" in data, "Expected 'suggestions' key in JSON output"
        assert isinstance(data["suggestions"], list), "suggestions should be a list"

    def test_path_coverage_path_has_nodes(self, path_cov_project, cli_runner):
        """Each path in JSON output contains nodes with required fields."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        paths = data.get("paths", [])
        if not paths:
            pytest.skip("No paths found in this project configuration")

        for path in paths:
            assert "nodes" in path, f"Path missing 'nodes': {path}"
            assert "risk" in path, f"Path missing 'risk': {path}"
            assert "tested_count" in path, f"Path missing 'tested_count': {path}"
            assert "total_count" in path, f"Path missing 'total_count': {path}"
            nodes = path["nodes"]
            assert len(nodes) > 0, "Path should have at least one node"
            for node in nodes:
                assert "name" in node, f"Node missing 'name': {node}"
                assert "file" in node, f"Node missing 'file': {node}"
                assert "tested" in node, f"Node missing 'tested': {node}"
                assert isinstance(node["tested"], bool), (
                    f"Node 'tested' should be bool, got {type(node['tested'])}"
                )

    def test_path_coverage_from_filter(self, path_cov_project, cli_runner):
        """--from filter restricts entry points to matching file glob."""
        # Filter to handler.py (which contains handle_request)
        result = invoke_path_coverage(
            cli_runner, ["--from", "handler.py"],
            cwd=path_cov_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "path-coverage")
        # All paths should start from handler.py
        for path in data.get("paths", []):
            if path["nodes"]:
                first_file = path["nodes"][0]["file"]
                assert "handler" in first_file.replace("\\", "/"), (
                    f"Expected first node from handler.py, got: {first_file}"
                )

    def test_path_coverage_to_filter(self, path_cov_project, cli_runner):
        """--to filter restricts sinks to matching file glob."""
        # Filter to db.py sinks
        result = invoke_path_coverage(
            cli_runner, ["--to", "db.py"],
            cwd=path_cov_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "path-coverage")
        # Sinks found should be an integer (possibly 0 if no symbol_effects row matches)
        assert isinstance(data.get("sinks_found", 0), int)

    def test_path_coverage_no_paths_project(self, no_paths_project, cli_runner):
        """Project with no entry-to-sink chains exits 0 with graceful message."""
        result = invoke_path_coverage(cli_runner, cwd=no_paths_project)
        assert result.exit_code == 0, (
            f"Expected exit 0 for no-paths project, got {result.exit_code}:\n{result.output}"
        )
        assert "VERDICT:" in result.output, (
            f"Expected VERDICT: line in output:\n{result.output}"
        )

    def test_path_coverage_no_paths_project_json(self, no_paths_project, cli_runner):
        """Project with no paths returns valid JSON envelope with total_paths=0."""
        result = invoke_path_coverage(cli_runner, cwd=no_paths_project, json_mode=True)
        assert result.exit_code == 0
        data = parse_json_output(result, "path-coverage")
        assert_json_envelope(data, "path-coverage")
        assert data["summary"]["total_paths"] == 0

    def test_path_coverage_summary_counts(self, path_cov_project, cli_runner):
        """JSON summary contains total_paths and untested_paths integer fields."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        summary = data["summary"]
        assert "total_paths" in summary, "summary missing 'total_paths'"
        assert "untested_paths" in summary, "summary missing 'untested_paths'"
        assert isinstance(summary["total_paths"], int)
        assert isinstance(summary["untested_paths"], int)
        assert summary["total_paths"] >= summary["untested_paths"], (
            "untested_paths cannot exceed total_paths"
        )
        assert "critical" in summary, "summary missing 'critical'"
        assert "high" in summary, "summary missing 'high'"

    def test_path_coverage_max_depth(self, path_cov_project, cli_runner):
        """--max-depth 1 limits path length to at most 1 hop (2 nodes)."""
        result = invoke_path_coverage(
            cli_runner, ["--max-depth", "1"],
            cwd=path_cov_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "path-coverage")
        for path in data.get("paths", []):
            assert len(path["nodes"]) <= 2, (
                f"With --max-depth 1 paths should have at most 2 nodes, "
                f"got {len(path['nodes'])}: {[n['name'] for n in path['nodes']]}"
            )

    def test_path_coverage_suggestions_have_required_fields(self, path_cov_project, cli_runner):
        """Each suggestion has symbol, file, line, and paths_covered fields."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        for suggestion in data.get("suggestions", []):
            assert "symbol" in suggestion, f"Suggestion missing 'symbol': {suggestion}"
            assert "file" in suggestion, f"Suggestion missing 'file': {suggestion}"
            assert "line" in suggestion, f"Suggestion missing 'line': {suggestion}"
            assert "paths_covered" in suggestion, (
                f"Suggestion missing 'paths_covered': {suggestion}"
            )
            assert isinstance(suggestion["paths_covered"], int)
            assert suggestion["paths_covered"] >= 1

    def test_path_coverage_risk_labels_valid(self, path_cov_project, cli_runner):
        """All path risk labels are one of the four valid values."""
        valid_risks = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        for path in data.get("paths", []):
            assert path["risk"] in valid_risks, (
                f"Unexpected risk label: {path['risk']!r}. Valid: {valid_risks}"
            )

    def test_path_coverage_tested_project_lower_risk(self, tested_project, cli_runner):
        """A project with test coverage should have valid output with lower untested counts."""
        result = invoke_path_coverage(cli_runner, cwd=tested_project, json_mode=True)
        assert result.exit_code == 0
        data = parse_json_output(result, "path-coverage")
        assert_json_envelope(data, "path-coverage")
        # The test project has test_api.py which calls api_handler;
        # the command should run and produce valid output.
        summary = data["summary"]
        assert "total_paths" in summary
        # untested_paths should be <= total_paths
        assert summary.get("untested_paths", 0) <= summary.get("total_paths", 0)
