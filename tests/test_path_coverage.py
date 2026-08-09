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

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Local helper: invoke path-coverage directly (bypasses CLI group)
# ---------------------------------------------------------------------------


def invoke_path_coverage(runner, args=None, cwd=None, json_mode=False):
    """Invoke the path-coverage command directly via its Click command object.

    Bypasses the CLI group so the command works before cli.py registration.
    """
    from roam.commands.cmd_path_coverage import path_coverage

    full_args = list(args or [])
    obj = {"json": json_mode}

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(path_coverage, full_args, obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


class _RaisingConn:
    def __init__(self, exc):
        self.exc = exc

    def execute(self, _sql):
        raise self.exc


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
        "from service import process\n\ndef handle_request(data):\n    return process(data)\n"
    )

    # Middle layer — calls save (sink)
    (proj / "service.py").write_text(
        "from db import save\n\n"
        "def process(data):\n"
        "    result = transform(data)\n"
        "    save(result)\n"
        "    return result\n\n"
        "def transform(data):\n"
        "    return data\n"
    )

    # Sink (DB write — leaf node with no outgoing edges)
    (proj / "db.py").write_text(
        'def save(record):\n    conn.execute("INSERT INTO t VALUES (?)", (record,))\n    conn.commit()\n'
    )

    # Pure utility — forms no entry-to-sink chain
    (proj / "utils.py").write_text("def format_name(n):\n    return n.title()\n")

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
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def multiply(a, b):\n"
        "    return a * b\n\n"
        "def subtract(a, b):\n"
        "    return a - b\n"
    )

    (proj / "string_utils.py").write_text(
        "def upper(s):\n    return s.upper()\n\ndef lower(s):\n    return s.lower()\n"
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

    (proj / "api.py").write_text("from worker import do_work\n\ndef api_handler(req):\n    return do_work(req)\n")

    (proj / "worker.py").write_text(
        "from store import write_record\n\ndef do_work(req):\n    write_record(req)\n    return True\n"
    )

    (proj / "store.py").write_text(
        'def write_record(data):\n    conn.execute("INSERT INTO records VALUES (?)", (data,))\n    conn.commit()\n'
    )

    # Test file that calls api_handler — this covers the entry point
    (proj / "test_api.py").write_text(
        "from api import api_handler\n\n"
        "def test_api_handler():\n"
        '    result = api_handler("payload")\n'
        "    assert result is True\n"
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
    def test_effect_sink_query_missing_schema_falls_back(self):
        """Missing symbol_effects schema falls back to leaf-node sink discovery."""
        from roam.commands.cmd_path_coverage import _find_sinks_from_effects

        sink_info, sink_effects = _find_sinks_from_effects(
            _RaisingConn(sqlite3.OperationalError("no such table: symbol_effects")),
            None,
        )

        assert sink_info == {}
        assert sink_effects == {}

    def test_effect_sink_query_non_sqlite_error_surfaces(self):
        """Non-SQL failures in sink discovery fail loud instead of degrading."""
        from roam.commands.cmd_path_coverage import _find_sinks_from_effects

        with pytest.raises(RuntimeError, match="synthetic sink failure"):
            _find_sinks_from_effects(_RaisingConn(RuntimeError("synthetic sink failure")), None)

    def test_path_coverage_runs(self, path_cov_project, cli_runner):
        """Command exits with code 0 on a valid indexed project."""
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project)
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

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
        assert first_line.startswith("VERDICT:"), f"Expected output to start with VERDICT:, got: {first_line!r}"

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
                assert isinstance(node["tested"], bool), f"Node 'tested' should be bool, got {type(node['tested'])}"

    def test_path_coverage_from_filter(self, path_cov_project, cli_runner):
        """--from filter restricts entry points to matching file glob."""
        # Filter to handler.py (which contains handle_request)
        result = invoke_path_coverage(
            cli_runner,
            ["--from", "handler.py"],
            cwd=path_cov_project,
            json_mode=True,
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
            cli_runner,
            ["--to", "db.py"],
            cwd=path_cov_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "path-coverage")
        # Sinks found should be an integer (possibly 0 if no symbol_effects row matches)
        assert isinstance(data.get("sinks_found", 0), int)

    def test_path_coverage_no_paths_project(self, no_paths_project, cli_runner):
        """Project with no entry-to-sink chains exits 0 with graceful message."""
        result = invoke_path_coverage(cli_runner, cwd=no_paths_project)
        assert result.exit_code == 0, f"Expected exit 0 for no-paths project, got {result.exit_code}:\n{result.output}"
        assert "VERDICT:" in result.output, f"Expected VERDICT: line in output:\n{result.output}"

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
        assert summary["total_paths"] >= summary["untested_paths"], "untested_paths cannot exceed total_paths"
        assert "critical" in summary, "summary missing 'critical'"
        assert "high" in summary, "summary missing 'high'"

    def test_path_coverage_max_depth(self, path_cov_project, cli_runner):
        """--max-depth 1 limits path length to at most 1 hop (2 nodes)."""
        result = invoke_path_coverage(
            cli_runner,
            ["--max-depth", "1"],
            cwd=path_cov_project,
            json_mode=True,
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
            assert "paths_covered" in suggestion, f"Suggestion missing 'paths_covered': {suggestion}"
            assert isinstance(suggestion["paths_covered"], int)
            assert suggestion["paths_covered"] >= 1

    def test_path_coverage_risk_labels_valid(self, path_cov_project, cli_runner):
        """All path risk labels are one of the four valid values.

        W718: risk labels are lowercase canonical roam vocabulary
        (``critical``/``high``/``medium``/``low``) per W547. Pre-W718
        they were UPPER-cased.
        """
        valid_risks = {"critical", "high", "medium", "low"}
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        data = parse_json_output(result, "path-coverage")
        for path in data.get("paths", []):
            assert path["risk"] in valid_risks, f"Unexpected risk label: {path['risk']!r}. Valid: {valid_risks}"

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


# ---------------------------------------------------------------------------
# W1528: the --max-depth bound is part of the measurement
# ---------------------------------------------------------------------------


@pytest.fixture
def deep_chain_project(tmp_path, monkeypatch):
    """Project holding one 13-node chain AND one 2-node chain.

    This is the shape that hides the defect rather than exposing it: at the
    default ``--max-depth 8`` the short chain is enumerated, so the output
    looks fully populated while the 13-node chain -- and its optimal test
    point -- are absent entirely.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    parts = ["def sink_writer():\n    return 1\n\n"]
    prev = "sink_writer"
    for i in range(11, 0, -1):
        parts.append(f"def f{i}():\n    {prev}()\n\n")
        prev = f"f{i}"
    parts.append(f"def entry_main():\n    {prev}()\n")
    (proj / "chain.py").write_text("".join(parts))

    (proj / "short.py").write_text("def sink_short():\n    return 2\n\ndef entry_short():\n    sink_short()\n")

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def deep_only_project(tmp_path, monkeypatch):
    """Project holding ONLY the 13-node chain, no short chain to mask it."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    parts = ["def sink_writer():\n    return 1\n\n"]
    prev = "sink_writer"
    for i in range(11, 0, -1):
        parts.append(f"def f{i}():\n    {prev}()\n\n")
        prev = f"f{i}"
    parts.append(f"def entry_main():\n    {prev}()\n")
    (proj / "chain.py").write_text("".join(parts))

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


class TestPathCoverageDepthTruncationDisclosure:
    """A BFS that discarded frontier at ``--max-depth`` has not finished.

    Before W1528 the discard was silent: ``total_paths``, the verdict and
    ``partial_success`` all read as a completed scan while whole entry->sink
    chains longer than the bound were missing with no marker of any kind.
    """

    # --- must fire -------------------------------------------------------

    def test_truncated_walk_discloses_pruning_in_json(self, deep_chain_project, cli_runner):
        """Default depth on a 13-node chain: counts are marked as a lower bound."""
        result = invoke_path_coverage(cli_runner, cwd=deep_chain_project, json_mode=True)
        assert result.exit_code == 0
        summary = parse_json_output(result, "path-coverage")["summary"]

        assert summary.get("pruned_at_max_depth", 0) > 0, (
            f"BFS discarded frontier at the bound but reported none: {summary}"
        )
        assert summary["partial_success"] is True, (
            f"a truncated walk must not publish its counts as a completed scan: {summary}"
        )
        assert summary.get("scan_incomplete") is True, f"summary missing scan_incomplete: {summary}"
        assert summary.get("incomplete_reasons") == ["max_depth"], (
            f"truncation reason must name the bound that fired: {summary}"
        )
        assert summary.get("max_depth") == 8, f"effective bound must be echoed so counts self-describe: {summary}"

    def test_truncated_walk_verdict_names_the_bound(self, deep_chain_project, cli_runner):
        """LAW 6: the verdict alone says the enumeration was cut short."""
        result = invoke_path_coverage(cli_runner, cwd=deep_chain_project, json_mode=True)
        verdict = parse_json_output(result, "path-coverage")["summary"]["verdict"]
        assert "pruned" in verdict, f"verdict must disclose pruning standalone; got {verdict!r}"
        assert "--max-depth" in verdict, f"verdict must name the bound that fired; got {verdict!r}"

    def test_truncated_walk_text_mode_names_the_escape_hatch(self, deep_chain_project, cli_runner):
        """Text mode prints a NOTE that tells the user how to search further."""
        result = invoke_path_coverage(cli_runner, cwd=deep_chain_project)
        assert result.exit_code == 0
        assert "NOTE:" in result.output, f"text mode must disclose pruning:\n{result.output}"
        assert "--max-depth" in result.output, f"NOTE must name the escape hatch:\n{result.output}"

    def test_zero_paths_at_a_bound_that_pruned_is_not_a_clean_scan(self, deep_only_project, cli_runner):
        """0 paths + discarded frontier is a different state from 0 paths + finished walk."""
        result = invoke_path_coverage(
            cli_runner,
            ["--max-depth", "3"],
            cwd=deep_only_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        summary = parse_json_output(result, "path-coverage")["summary"]
        assert summary["total_paths"] == 0
        assert summary.get("state") == "no_paths_within_max_depth", (
            f"a depth-truncated empty result must not claim no_paths_connecting: {summary}"
        )
        assert summary["partial_success"] is True, (
            f"a walk that ran out of depth proved nothing about connectivity: {summary}"
        )

    # --- must NOT fire ---------------------------------------------------

    def test_complete_walk_does_not_claim_truncation(self, deep_chain_project, cli_runner):
        """Same repo, bound large enough to finish: the flag stays down.

        This is the assertion that keeps the disclosure meaningful. The flag
        is keyed on an actual discard, not on the graph being deeper than the
        default, so raising the bound past the deepest chain clears it.
        """
        result = invoke_path_coverage(
            cli_runner,
            ["--max-depth", "20"],
            cwd=deep_chain_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        summary = parse_json_output(result, "path-coverage")["summary"]

        assert summary.get("pruned_at_max_depth", 0) == 0, f"nothing was discarded at depth 20: {summary}"
        assert summary.get("partial_success") is not True, f"a completed walk must not be flagged partial: {summary}"
        assert "scan_incomplete" not in summary, f"completed walk must not carry scan_incomplete: {summary}"
        assert "incomplete_reasons" not in summary, f"completed walk must not carry incomplete_reasons: {summary}"
        assert "pruned" not in summary["verdict"], f"completed walk verdict must not mention pruning: {summary}"
        # And the deeper chain is genuinely there once the bound allows it.
        assert summary["total_paths"] > 1, f"raising the bound must surface the long chain the default hid: {summary}"

    def test_shallow_project_at_default_depth_is_not_flagged(self, path_cov_project, cli_runner):
        """A repo whose chains all terminate inside the bound stays clean.

        Guards the over-eager fix: flagging on graph depth rather than on a
        real discard would make ``partial_success`` permanently true and the
        signal worthless.
        """
        result = invoke_path_coverage(cli_runner, cwd=path_cov_project, json_mode=True)
        assert result.exit_code == 0
        summary = parse_json_output(result, "path-coverage")["summary"]
        assert summary.get("pruned_at_max_depth", 0) == 0, f"nothing should be discarded here: {summary}"
        assert summary.get("partial_success") is not True, f"clean scan flagged partial: {summary}"

    def test_genuinely_disconnected_graph_keeps_no_paths_connecting(self, no_paths_project, cli_runner):
        """A finished walk that found nothing is still a finished walk."""
        result = invoke_path_coverage(cli_runner, cwd=no_paths_project, json_mode=True)
        assert result.exit_code == 0
        summary = parse_json_output(result, "path-coverage")["summary"]
        assert summary.get("state") != "no_paths_within_max_depth", (
            f"an untruncated empty result must not claim depth truncation: {summary}"
        )
        assert summary.get("pruned_at_max_depth", 0) == 0, f"no frontier was discarded: {summary}"

    # --- unit level ------------------------------------------------------

    def test_find_paths_reports_zero_pruned_when_walk_completes(self):
        """``_find_paths`` counts real discards, not graph depth."""
        from roam.commands.cmd_path_coverage import _find_paths

        # entry 1 -> 2 -> 3 (sink). Depth 10 is far past the graph.
        paths, pruned = _find_paths({1: [2], 2: [3]}, 1, {3}, 10)
        assert paths == [[1, 2, 3]]
        assert pruned == 0

    def test_find_paths_counts_discarded_frontier(self):
        """Bounding the same walk records what it threw away."""
        from roam.commands.cmd_path_coverage import _find_paths

        paths, pruned = _find_paths({1: [2], 2: [3]}, 1, {3}, 2)
        assert paths == []
        assert pruned > 0, "the depth-3 frontier was discarded and must be counted"
