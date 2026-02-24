"""Tests for runtime trace ingestion and hotspot analysis."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process, git_init, invoke_cli, parse_json_output, assert_json_envelope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runtime_project(project_factory):
    return project_factory({
        "api.py": "from service import process\ndef handle(): return process()\n",
        "service.py": "from utils import helper\ndef process(): return helper()\n",
        "utils.py": "def helper(): return 42\n",
    })


@pytest.fixture
def security_hotspots_project(project_factory):
    return project_factory({
        "app.py": (
            "import os\n"
            "\n"
            "def public_handler(user_input):\n"
            "    return run_eval(user_input)\n"
            "\n"
            "def run_eval(user_input):\n"
            "    return eval(user_input)\n"
            "\n"
            "def _unsafe_shell(cmd):\n"
            "    return os.system(cmd)\n"
        ),
        "ui.js": (
            "export function renderUnsafe(input) {\n"
            "  const out = document.getElementById('out');\n"
            "  out.innerHTML = input;\n"
            "}\n"
        ),
    })


@pytest.fixture
def generic_trace(tmp_path):
    trace = [
        {"function": "handle", "file": "api.py", "call_count": 1000, "p50_ms": 10, "p99_ms": 100, "error_rate": 0.01},
        {"function": "process", "file": "service.py", "call_count": 950, "p50_ms": 8, "p99_ms": 80, "error_rate": 0.0},
        {"function": "helper", "file": "utils.py", "call_count": 900, "p50_ms": 2, "p99_ms": 5, "error_rate": 0.0},
        {"function": "unknown_fn", "file": "nowhere.py", "call_count": 500, "p50_ms": 50, "p99_ms": 500, "error_rate": 0.1},
    ]
    p = tmp_path / "trace.json"
    p.write_text(json.dumps(trace))
    return str(p)


@pytest.fixture
def otel_trace_with_db_attrs(tmp_path):
    trace = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "process",
                                "startTimeUnixNano": 1_000_000_000,
                                "endTimeUnixNano": 1_150_000_000,
                                "attributes": [
                                    {"key": "db.system", "value": {"stringValue": "postgresql"}},
                                    {"key": "db.operation", "value": {"stringValue": "SELECT"}},
                                    {"key": "db.statement", "value": {"stringValue": "SELECT * FROM users"}},
                                    {"key": "code.filepath", "value": {"stringValue": "service.py"}},
                                ],
                            },
                            {
                                "name": "process",
                                "startTimeUnixNano": 1_200_000_000,
                                "endTimeUnixNano": 1_380_000_000,
                                "attributes": [
                                    {"key": "db.system", "value": {"stringValue": "postgresql"}},
                                    {"key": "db.operation", "value": {"stringValue": "SELECT"}},
                                    {"key": "db.statement", "value": {"stringValue": "SELECT id FROM users"}},
                                    {"key": "code.filepath", "value": {"stringValue": "service.py"}},
                                ],
                            },
                        ]
                    }
                ]
            }
        ]
    }
    p = tmp_path / "otel_trace.json"
    p.write_text(json.dumps(trace))
    return str(p)


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Unit tests: runtime_stats table
# ---------------------------------------------------------------------------

class TestRuntimeStatsTable:
    def test_runtime_stats_table_exists(self, runtime_project):
        """Table created after migration via ensure_schema."""
        from roam.db.connection import open_db
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                # The table should exist after ensure_schema runs
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_stats'"
                ).fetchone()
                assert row is not None, "runtime_stats table should exist"
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: trace ingestion
# ---------------------------------------------------------------------------

class TestIngestGenericTrace:
    def test_ingest_generic_trace(self, runtime_project, generic_trace):
        """Stats inserted correctly from generic trace."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_generic_trace, ensure_runtime_table
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                results = ingest_generic_trace(conn, generic_trace)
                conn.commit()

            assert len(results) == 4
            # Check that call counts are correctly recorded
            names = {r["symbol_name"]: r["call_count"] for r in results}
            assert names["handle"] == 1000
            assert names["process"] == 950
            assert names["helper"] == 900
            assert names["unknown_fn"] == 500
        finally:
            os.chdir(old_cwd)

    def test_ingest_matches_symbols(self, runtime_project, generic_trace):
        """Matched symbol_id populated for known functions."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_generic_trace, ensure_runtime_table
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                results = ingest_generic_trace(conn, generic_trace)
                conn.commit()

            matched = [r for r in results if r["matched"]]
            # handle, process, helper should be matched
            assert len(matched) >= 3, f"Expected at least 3 matched, got {len(matched)}: {matched}"
        finally:
            os.chdir(old_cwd)

    def test_ingest_unmatched_spans(self, runtime_project, generic_trace):
        """Unmatched spans still recorded."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_generic_trace, ensure_runtime_table
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                results = ingest_generic_trace(conn, generic_trace)
                conn.commit()

            unmatched = [r for r in results if not r["matched"]]
            assert len(unmatched) >= 1
            assert any(r["symbol_name"] == "unknown_fn" for r in unmatched)
        finally:
            os.chdir(old_cwd)

    def test_ingest_updates_existing(self, runtime_project, generic_trace, tmp_path):
        """Re-ingestion updates stats rather than duplicating."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_generic_trace, ensure_runtime_table
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            # First ingest
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                ingest_generic_trace(conn, generic_trace)
                conn.commit()

            # Second ingest with updated values
            trace2 = [
                {"function": "handle", "file": "api.py", "call_count": 2000, "p50_ms": 15, "p99_ms": 150, "error_rate": 0.05},
            ]
            p2 = tmp_path / "trace2.json"
            p2.write_text(json.dumps(trace2))

            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                ingest_generic_trace(conn, str(p2))
                conn.commit()

            # Should have updated, not duplicated
            with open_db(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT call_count FROM runtime_stats WHERE symbol_name = 'handle' AND trace_source = 'generic'"
                ).fetchall()
                assert len(rows) == 1, f"Expected 1 row for handle, got {len(rows)}"
                assert rows[0][0] == 2000
        finally:
            os.chdir(old_cwd)


class TestIngestOtelTrace:
    def test_ingest_otel_captures_db_semantics(
        self, runtime_project, otel_trace_with_db_attrs
    ):
        """OTel ingestion should persist DB semantic attributes."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_otel_trace, ensure_runtime_table

        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                results = ingest_otel_trace(conn, otel_trace_with_db_attrs)
                conn.commit()

                assert len(results) >= 1
                row = conn.execute(
                    "SELECT otel_db_system, otel_db_operation, otel_db_statement_type "
                    "FROM runtime_stats "
                    "WHERE symbol_name = 'process' AND trace_source = 'otel' "
                    "LIMIT 1"
                ).fetchone()
                assert row is not None
                assert row["otel_db_system"] == "postgresql"
                assert row["otel_db_operation"] == "SELECT"
                assert row["otel_db_statement_type"] == "SELECT"
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: symbol matching
# ---------------------------------------------------------------------------

class TestMatchTraceToSymbol:
    def test_match_trace_to_symbol_exact(self, runtime_project):
        """Exact name+file match works."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import match_trace_to_symbol
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=True) as conn:
                sid = match_trace_to_symbol(conn, "handle", "api.py")
                assert sid is not None
        finally:
            os.chdir(old_cwd)

    def test_match_trace_to_symbol_name_only(self, runtime_project):
        """Name-only match works when unique."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import match_trace_to_symbol
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=True) as conn:
                sid = match_trace_to_symbol(conn, "helper")
                assert sid is not None
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: hotspots
# ---------------------------------------------------------------------------

class TestHotspots:
    def test_hotspots_returns_list(self, runtime_project, generic_trace):
        """Hotspots analysis returns results after ingestion."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_generic_trace, ensure_runtime_table
        from roam.runtime.hotspots import compute_hotspots
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                ingest_generic_trace(conn, generic_trace)
                conn.commit()

            with open_db(readonly=True) as conn:
                hotspots = compute_hotspots(conn)
                assert isinstance(hotspots, list)
                assert len(hotspots) > 0
        finally:
            os.chdir(old_cwd)

    def test_hotspot_classification(self, runtime_project, generic_trace):
        """UPGRADE/CONFIRMED/DOWNGRADE categories are assigned."""
        from roam.db.connection import open_db
        from roam.runtime.trace_ingest import ingest_generic_trace, ensure_runtime_table
        from roam.runtime.hotspots import compute_hotspots
        old_cwd = os.getcwd()
        try:
            os.chdir(str(runtime_project))
            with open_db(readonly=False) as conn:
                ensure_runtime_table(conn)
                ingest_generic_trace(conn, generic_trace)
                conn.commit()

            with open_db(readonly=True) as conn:
                hotspots = compute_hotspots(conn)
                classifications = {h["classification"] for h in hotspots}
                # At least one classification should be assigned
                assert classifications.issubset({"UPGRADE", "CONFIRMED", "DOWNGRADE"})
                assert len(classifications) >= 1
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# CLI tests: ingest-trace
# ---------------------------------------------------------------------------

class TestCliIngestTrace:
    def test_cli_ingest_trace_runs(self, runtime_project, generic_trace, cli_runner):
        """Exit code 0."""
        result = invoke_cli(cli_runner, ["ingest-trace", generic_trace], cwd=runtime_project)
        assert result.exit_code == 0, f"ingest-trace failed:\n{result.output}"
        assert "VERDICT" in result.output

    def test_cli_ingest_trace_json(self, runtime_project, generic_trace, cli_runner):
        """Valid JSON envelope."""
        result = invoke_cli(cli_runner, ["ingest-trace", generic_trace],
                           cwd=runtime_project, json_mode=True)
        data = parse_json_output(result, "ingest-trace")
        assert_json_envelope(data, "ingest-trace")
        assert "total" in data["summary"]
        assert "matched" in data["summary"]
        assert "spans" in data

    def test_cli_ingest_trace_help(self, cli_runner):
        """--help works."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["ingest-trace", "--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output.lower() or "trace" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI tests: hotspots
# ---------------------------------------------------------------------------

class TestCliHotspots:
    def test_cli_hotspots_runs(self, runtime_project, generic_trace, cli_runner):
        """Exit code 0 after ingestion."""
        # Ingest first
        invoke_cli(cli_runner, ["ingest-trace", generic_trace], cwd=runtime_project)
        # Then run hotspots
        result = invoke_cli(cli_runner, ["hotspots"], cwd=runtime_project)
        assert result.exit_code == 0, f"hotspots failed:\n{result.output}"

    def test_cli_hotspots_json(self, runtime_project, generic_trace, cli_runner):
        """Valid JSON envelope."""
        invoke_cli(cli_runner, ["ingest-trace", generic_trace], cwd=runtime_project)
        result = invoke_cli(cli_runner, ["--detail", "hotspots"], cwd=runtime_project, json_mode=True)
        data = parse_json_output(result, "hotspots")
        assert_json_envelope(data, "hotspots")
        assert "upgrades" in data["summary"]
        assert "hotspots" in data

    def test_cli_hotspots_verdict(self, runtime_project, generic_trace, cli_runner):
        """Text starts with VERDICT."""
        invoke_cli(cli_runner, ["ingest-trace", generic_trace], cwd=runtime_project)
        result = invoke_cli(cli_runner, ["hotspots"], cwd=runtime_project)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_cli_hotspots_help(self, cli_runner):
        """--help works."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["hotspots", "--help"])
        assert result.exit_code == 0
        assert "hotspot" in result.output.lower() or "runtime" in result.output.lower()


class TestCliSecurityHotspots:
    def test_cli_hotspots_security_runs(self, security_hotspots_project, cli_runner):
        """Security mode should run without runtime ingestion."""
        result = invoke_cli(
            cli_runner,
            ["hotspots", "--security"],
            cwd=security_hotspots_project,
        )
        assert result.exit_code == 0, f"hotspots --security failed:\n{result.output}"
        assert result.output.strip().startswith("VERDICT:")
        assert "security hotspot" in result.output.lower()

    def test_cli_hotspots_security_json(self, security_hotspots_project, cli_runner):
        """Security mode JSON shape includes reachability metadata."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_hotspots_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        assert_json_envelope(data, "hotspots")
        assert data["mode"] == "security"
        assert data["summary"]["total"] >= 1
        assert "hotspots" in data
        assert isinstance(data["hotspots"], list)
        assert len(data["hotspots"]) >= 1
        required = {
            "file",
            "line",
            "pattern_id",
            "severity",
            "reachable_from_entrypoint",
            "risk_score",
        }
        assert required.issubset(set(data["hotspots"][0].keys()))
        assert any(h.get("reachable_from_entrypoint") for h in data["hotspots"])

    def test_cli_hotspots_security_conflict_flags(
        self, security_hotspots_project, cli_runner
    ):
        """Runtime-only flags should conflict with security mode."""
        result = invoke_cli(
            cli_runner,
            ["hotspots", "--security", "--runtime"],
            cwd=security_hotspots_project,
        )
        assert result.exit_code != 0
        assert "Cannot combine --security with --runtime or --discrepancy" in result.output
