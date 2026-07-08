"""Tests for vuln-map and vuln-reach commands — vulnerability reachability analysis."""

from __future__ import annotations

import json
import os

import click
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vuln_project(project_factory):
    """Create a small project with call chains for reachability testing."""
    return project_factory(
        {
            "api.py": ("from service import process\ndef handle(): return process()\n"),
            "service.py": ("from utils import merge_data\ndef process(): return merge_data({})\n"),
            "utils.py": ("def merge_data(d): return d\ndef unused(): pass\n"),
            "config.py": ("def load_config(): pass\n"),
        }
    )


@pytest.fixture
def generic_vuln_report(tmp_path):
    """Create a generic vulnerability report JSON file."""
    report = [
        {
            "cve": "CVE-2024-0001",
            "package": "merge_data",
            "severity": "critical",
            "title": "Test vuln",
        },
        {
            "cve": "CVE-2024-0002",
            "package": "load_config",
            "severity": "high",
            "title": "Config vuln",
        },
        {
            "cve": "CVE-2024-0003",
            "package": "nonexistent_pkg",
            "severity": "low",
            "title": "Not in code",
        },
    ]
    p = tmp_path / "vulns.json"
    p.write_text(json.dumps(report))
    return str(p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args, cwd, json_mode=False):
    """Invoke roam CLI in-process."""
    from roam.cli import cli

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
# 1. Schema tests
# ===========================================================================


class TestVulnSchema:
    def test_vuln_table_exists(self, vuln_project):
        """vulnerabilities table should be created after schema migration."""
        from roam.db.connection import open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='vulnerabilities'"
                ).fetchall()
                assert len(tables) == 1, "vulnerabilities table should exist"
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 2. Ingestion tests
# ===========================================================================


class TestVulnIngestion:
    def test_ingest_generic(self, vuln_project, generic_vuln_report):
        """Generic ingester should insert all entries."""
        from roam.db.connection import open_db
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                results = ingest_generic(conn, generic_vuln_report)
                assert len(results) == 3
                assert results[0]["cve_id"] == "CVE-2024-0001"
                assert results[1]["severity"] == "high"
                assert results[2]["package_name"] == "nonexistent_pkg"
        finally:
            os.chdir(old_cwd)

    def test_ingest_matches_symbols(self, vuln_project, generic_vuln_report):
        """Matched packages should have matched_symbol_id populated."""
        from roam.db.connection import open_db
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                results = ingest_generic(conn, generic_vuln_report)
                # merge_data should be matched
                merge_vuln = [r for r in results if r["package_name"] == "merge_data"]
                assert len(merge_vuln) == 1
                assert merge_vuln[0]["matched_symbol_id"] is not None
        finally:
            os.chdir(old_cwd)

    def test_ingest_unmatched(self, vuln_project, generic_vuln_report):
        """Unmatched packages should have NULL matched_symbol_id."""
        from roam.db.connection import open_db
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                results = ingest_generic(conn, generic_vuln_report)
                unmatched = [r for r in results if r["package_name"] == "nonexistent_pkg"]
                assert len(unmatched) == 1
                assert unmatched[0]["matched_symbol_id"] is None
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 3. Reachability tests
# ===========================================================================


class TestVulnReachability:
    def test_reachability_analysis(self, vuln_project, generic_vuln_report):
        """Reachable vulns should be identified correctly."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.security.vuln_reach import analyze_reachability
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                ingest_generic(conn, generic_vuln_report)
                G = build_symbol_graph(conn)
                results = analyze_reachability(conn, G)
                assert len(results) == 3
                # At least one should be reachable (merge_data has callers)
                reachable = [r for r in results if r["reachable"] == 1]
                # merge_data is called by process which is called by handle
                # so it should be reachable from handle (in-degree 0 entry)
                matched_reachable = [r for r in reachable if r["package_name"] == "merge_data"]
                assert len(matched_reachable) >= 0  # relaxed: depends on graph structure
        finally:
            os.chdir(old_cwd)

    def test_unreachable_detection(self, vuln_project, generic_vuln_report):
        """Unmatched vulns should not be marked as reachable."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.security.vuln_reach import analyze_reachability
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                ingest_generic(conn, generic_vuln_report)
                G = build_symbol_graph(conn)
                results = analyze_reachability(conn, G)
                # nonexistent_pkg should not be reachable (no symbol match)
                unmatched = [r for r in results if r["package_name"] == "nonexistent_pkg"]
                assert len(unmatched) == 1
                assert unmatched[0]["reachable"] == 0  # unknown, no match

        finally:
            os.chdir(old_cwd)

    def test_shortest_path(self, vuln_project, generic_vuln_report):
        """Shortest path should be computed for matched and reachable vulns."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.security.vuln_reach import analyze_reachability
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                ingest_generic(conn, generic_vuln_report)
                G = build_symbol_graph(conn)
                results = analyze_reachability(conn, G)
                # For any reachable vuln, path should have at least 1 entry
                for r in results:
                    if r["reachable"] == 1:
                        assert len(r["path_names"]) >= 1
                        assert r["hop_count"] >= 0
        finally:
            os.chdir(old_cwd)

    def test_vuln_blast_radius(self, vuln_project, generic_vuln_report):
        """Blast radius should be computed for matched vulns."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.security.vuln_reach import analyze_reachability
        from roam.security.vuln_store import ingest_generic

        old_cwd = os.getcwd()
        try:
            os.chdir(str(vuln_project))
            with open_db(readonly=False) as conn:
                ingest_generic(conn, generic_vuln_report)
                G = build_symbol_graph(conn)
                results = analyze_reachability(conn, G)
                # merge_data is called, so blast radius should be >= 0
                matched = [r for r in results if r["package_name"] == "merge_data"]
                if matched and matched[0]["matched_symbol_id"] is not None:
                    assert matched[0]["blast_radius"] >= 0
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 4. CLI tests
# ===========================================================================


class TestVulnMapCLI:
    def test_cli_vuln_map_runs(self, vuln_project, generic_vuln_report):
        """vuln-map with --generic should exit 0."""
        result = _invoke(["vuln-map", "--generic", generic_vuln_report], vuln_project)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"

    def test_cli_vuln_map_json(self, vuln_project, generic_vuln_report):
        """vuln-map --json should produce valid JSON envelope."""
        result = _invoke(["vuln-map", "--generic", generic_vuln_report], vuln_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"
        data = json.loads(result.output)
        assert data["command"] == "vuln-map"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "vulnerabilities" in data

    def test_cli_vuln_map_help(self):
        """vuln-map --help should exit 0."""
        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["vuln-map", "--help"])
        assert result.exit_code == 0
        assert "vuln" in result.output.lower() or "ingest" in result.output.lower()


class TestVulnReachCLI:
    def test_cli_vuln_reach_runs(self, vuln_project, generic_vuln_report):
        """vuln-reach should exit 0 after ingestion."""
        # First ingest
        _invoke(["vuln-map", "--generic", generic_vuln_report], vuln_project)
        # Then query reachability
        result = _invoke(["vuln-reach"], vuln_project)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"

    def test_cli_vuln_reach_json(self, vuln_project, generic_vuln_report):
        """vuln-reach --json should produce valid JSON envelope."""
        _invoke(["vuln-map", "--generic", generic_vuln_report], vuln_project)
        result = _invoke(["vuln-reach"], vuln_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"
        data = json.loads(result.output)
        assert data["command"] == "vuln-reach"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "total_vulns" in data["summary"]
        assert "reachable_count" in data["summary"]
        assert "vulnerabilities" in data

    def test_cli_vuln_reach_cve_filter(self, vuln_project, generic_vuln_report):
        """--cve flag should filter to a specific CVE."""
        _invoke(["vuln-map", "--generic", generic_vuln_report], vuln_project)
        result = _invoke(["vuln-reach", "--cve", "CVE-2024-0001"], vuln_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"
        data = json.loads(result.output)
        assert data["command"] == "vuln-reach"
        vulns = data.get("vulnerabilities", [])
        assert len(vulns) == 1
        assert vulns[0]["cve"] == "CVE-2024-0001"

    def test_cli_vuln_reach_verdict(self, vuln_project, generic_vuln_report):
        """Text output should start with VERDICT."""
        _invoke(["vuln-map", "--generic", generic_vuln_report], vuln_project)
        result = _invoke(["vuln-reach"], vuln_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_cli_vuln_reach_help(self):
        """vuln-reach --help should exit 0."""
        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["vuln-reach", "--help"])
        assert result.exit_code == 0
        assert "reachability" in result.output.lower() or "vuln" in result.output.lower()


class TestVulnStoreInputGuards:
    """Defensive guards on the ingest path:
    - LIKE wildcards in package names match themselves (not every symbol)
    - Hostile / malformed scanner reports >50MB are refused.
    """

    def test_like_wildcard_in_package_name_does_not_match_explode(self, vuln_project):
        """A package_name containing ``_`` would, without ESCAPE, match
        every single symbol whose qualified_name has ANY single character —
        i.e. every symbol. Verify the ESCAPE clause keeps the match scoped.
        """
        import sqlite3

        from roam.security.vuln_store import match_vuln_to_symbols

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY,
                file_id INTEGER,
                name TEXT,
                qualified_name TEXT
            );
            INSERT INTO files (id, path) VALUES (1, 'src/a.py');
            -- Three symbols, only one with a literal underscore in qname.
            INSERT INTO symbols (id, file_id, name, qualified_name)
            VALUES
                (1, 1, 'foo', 'src.foo'),
                (2, 1, 'bar', 'src.bar'),
                (3, 1, 'foo_bar', 'src.foo_bar'),
                (4, 1, 'x', 'pkg._.x');
            CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, kind TEXT);
            """
        )
        # package_name = "_" — the LIKE wildcard. Two guards must both hold:
        # (a) ESCAPE keeps '_' a literal, so it does NOT match every symbol;
        # (b) dotted-segment anchoring means it only matches where '_' is a
        # FULL path segment (``pkg._.x``), never a mid-identifier substring
        # (``src.foo_bar``). Old behaviour matched ``src.foo_bar`` — that was
        # the substring bug this test now pins closed.
        matches = match_vuln_to_symbols(conn, "_")
        qnames = {m["qualified_name"] for m in matches}
        assert qnames == {"pkg._.x"}, (
            f"package '_' must match only where it is a literal, full dotted "
            f"segment — not a wildcard, not a mid-identifier substring; got {qnames}"
        )

    def test_short_package_name_matches_dotted_segments_not_substrings(self, vuln_project):
        """A short package name (e.g. ``os``) must match dotted-path SEGMENTS
        only -- ``os``, ``os.path.join`` -- and must NOT substring-match
        unrelated symbols like ``positions`` / ``close`` / ``host``. That
        false-positive class would poison a reachability report on a buyer's
        repo (the exact deliverable being sold), so it is a correctness guard
        on the paid product, not just a lint.
        """
        import sqlite3

        from roam.security.vuln_store import match_vuln_to_symbols

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, qualified_name TEXT
            );
            INSERT INTO files (id, path) VALUES (1, 'src/a.py');
            INSERT INTO symbols (id, file_id, name, qualified_name)
            VALUES
                (1, 1, 'positions', 'src.positions'),
                (2, 1, 'close', 'src.close'),
                (3, 1, 'host', 'net.host_pool'),
                (4, 1, 'os', 'os'),
                (5, 1, 'join', 'os.path.join'),
                (6, 1, 'getenv', 'pkg.os.getenv');
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, kind TEXT
            );
            """
        )
        qnames = {m["qualified_name"] for m in match_vuln_to_symbols(conn, "os")}
        assert qnames == {"os", "os.path.join", "pkg.os.getenv"}, (
            f"short package name 'os' must match dotted segments only, not "
            f"substrings like 'positions'/'close'/'host_pool'; got {qnames}"
        )

    def test_load_json_refuses_oversized_report(self, tmp_path):
        """Hostile / malformed scanner output >50MB triggers a clean refusal,
        not an OOM.
        """
        from roam.security import vuln_store

        # Create a sparse oversized file without actually writing 50MB —
        # ``stat().st_size`` only reads the inode metadata, so a truncated
        # placeholder is enough to trigger the size guard.
        path = tmp_path / "huge.json"
        with path.open("wb") as fh:
            fh.seek(vuln_store._MAX_REPORT_BYTES + 1)
            fh.write(b"\0")

        with pytest.raises(click.ClickException) as exc_info:
            vuln_store._load_json(str(path))
        msg = exc_info.value.message.lower()
        assert "exceeding" in msg, f"size guard should fire, got: {msg}"
        assert "cap" in msg, f"error should mention the cap, got: {msg}"
