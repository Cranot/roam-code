"""Tests for the W117 follow-up: vulns detector emits to the central
findings registry.

The vulns detector is the fourth migration onto the A4 findings table
(after W95's clones, W99's dead, and W102's complexity). It continues to
render its own JSON / SARIF / text envelopes (authoritative output
surface) and ALSO, when ``--persist`` is set, emits one row per
vulnerability into ``findings``. These tests cover that additive emit
and the end-to-end visibility through ``roam findings`` for an agent.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_vulns import (
    VULNS_DETECTOR_VERSION,
    _vuln_finding_id,
    _vuln_reachability_tag,
)
from roam.db.connection import open_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vuln_project(project_factory):
    """Small project whose top-level symbols are reachable through a chain.

    Mirrors the fixture used by tests/test_vulns_cmd.py so the vuln-
    matching path lines up with at least one symbol (``merge_data``)
    and at least one isolated function (``load_config``).
    """
    return project_factory(
        {
            "api.py": ("from service import process\ndef handle(): return process()\n"),
            "service.py": ("from utils import merge_data\ndef process(): return merge_data({})\n"),
            "utils.py": ("def merge_data(d): return d\ndef unused(): pass\n"),
            "config.py": ("def load_config(): pass\n"),
        }
    )


@pytest.fixture
def generic_report(tmp_path):
    """Generic JSON vulnerability report — three vulns, one with no
    matching symbol in the project.

    The generic source maps to ``heuristic`` confidence (no curated CVE
    DB validated this), so this fixture exercises that code path.
    """
    report = [
        {
            "cve": "CVE-2024-0001",
            "package": "merge_data",
            "severity": "critical",
            "title": "RCE in merge_data",
        },
        {
            "cve": "CVE-2024-0002",
            "package": "load_config",
            "severity": "high",
            "title": "Config injection",
        },
        {
            "cve": "CVE-2024-0003",
            "package": "nonexistent_pkg",
            "severity": "low",
            "title": "Not in code",
        },
    ]
    p = tmp_path / "generic_vulns.json"
    p.write_text(json.dumps(report))
    return str(p)


@pytest.fixture
def npm_report(tmp_path):
    """npm-audit v2 format report — curated source maps to
    ``static_analysis`` confidence tier."""
    report = {
        "vulnerabilities": {
            "merge_data": {
                "severity": "high",
                "via": [
                    {
                        "title": "Prototype Pollution",
                        "url": "https://github.com/advisories/GHSA-xxxx",
                    }
                ],
            },
            "express": {
                "severity": "medium",
                "via": [{"title": "Open Redirect"}],
            },
        }
    }
    p = tmp_path / "npm_audit.json"
    p.write_text(json.dumps(report))
    return str(p)


def _run_vulns_persist(proj, report_path, fmt="generic"):
    """Import a report with --persist and return the CliRunner result."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(
            cli,
            [
                "vulns",
                "--import-file",
                report_path,
                "--format",
                fmt,
                "--persist",
            ],
        )
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_vulns_emits_to_findings_registry(vuln_project, generic_report):
    """Running vulns --persist on a fixture with vulnerabilities populates findings."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'vulns'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one vulns-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "vulns"
            assert r["source_version"] == VULNS_DETECTOR_VERSION
            assert r["subject_kind"] in ("symbol", "package")
            # Generic source → heuristic; curated sources → static_analysis.
            assert r["confidence"] in ("static_analysis", "heuristic")
            assert r["finding_id_str"].startswith("vulns:cve:")
    finally:
        os.chdir(old_cwd)


def test_vulns_generic_source_maps_to_heuristic(vuln_project, generic_report):
    """Generic JSON (no curated CVE DB) → ``heuristic`` confidence."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT confidence FROM findings WHERE source_detector = 'vulns'"
            ).fetchall()
        assert len(rows) >= 1
        for r in rows:
            assert r["confidence"] == "heuristic"
    finally:
        os.chdir(old_cwd)


def test_vulns_npm_audit_source_maps_to_static_analysis(vuln_project, npm_report):
    """Curated scanner output (npm-audit) → ``static_analysis`` confidence."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, npm_report, fmt="npm-audit")

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT confidence FROM findings WHERE source_detector = 'vulns'"
            ).fetchall()
        assert len(rows) >= 1
        for r in rows:
            assert r["confidence"] == "static_analysis"
    finally:
        os.chdir(old_cwd)


def test_vuln_finding_id_str_is_deterministic_unit():
    """``_vuln_finding_id`` returns the same id on repeated input."""
    a = _vuln_finding_id("CVE-2024-0001", "merge_data")
    b = _vuln_finding_id("CVE-2024-0001", "merge_data")
    assert a == b
    assert a.startswith("vulns:cve:")
    # Different inputs → different ids.
    assert _vuln_finding_id("CVE-2024-0002", "merge_data") != a
    assert _vuln_finding_id("CVE-2024-0001", "other_pkg") != a
    # Missing CVE falls back to package-only id (still deterministic).
    no_cve = _vuln_finding_id(None, "merge_data")
    assert no_cve.startswith("vulns:cve:")
    assert no_cve == _vuln_finding_id(None, "merge_data")


def test_vulns_finding_id_str_is_deterministic_e2e(vuln_project, generic_report):
    """Re-running vulns --persist produces the same finding_id_str (upsert)."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'vulns'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'vulns'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str on first run"
        assert first_count >= 1

        # Re-run: --import-file re-ingests the same JSON, then --persist
        # re-emits. ids must be stable; the row count must not drift.
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vulns",
                "--import-file",
                generic_report,
                "--format",
                "generic",
                "--persist",
            ],
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'vulns'"
                ).fetchall()
            }
        # The vulnerabilities table grows on re-import (each row is a new
        # INSERT, not an upsert) — but the FINDINGS rows MUST upsert on
        # finding_id_str so the same (cve, package) pair never duplicates.
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_vulns_finding_evidence_carries_reachability(vuln_project, generic_report):
    """evidence_json carries cve_id, package_name, source, reachability."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vulns'"
            ).fetchall()
        assert len(rows) >= 1
        for r in rows:
            evidence = json.loads(r["evidence_json"])
            assert "cve_id" in evidence
            assert "package_name" in evidence
            assert "source" in evidence
            assert "severity" in evidence
            assert "reachability" in evidence
            assert evidence["reachability"] in (
                "reachable",
                "unreachable",
                "unknown",
            )
    finally:
        os.chdir(old_cwd)


def test_vulns_subject_kind_picks_symbol_when_matched(vuln_project, generic_report):
    """Matched vulns get subject_kind=symbol + subject_id; otherwise package."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)

        with open_db(readonly=True) as conn:
            rows = [
                {
                    "subject_kind": r["subject_kind"],
                    "subject_id": r["subject_id"],
                }
                for r in conn.execute(
                    "SELECT subject_kind, subject_id "
                    "FROM findings WHERE source_detector = 'vulns'"
                ).fetchall()
            ]
            matched = [r for r in rows if r["subject_kind"] == "symbol"]
            unmatched = [r for r in rows if r["subject_kind"] == "package"]
            # The fixture has merge_data + load_config in the source,
            # plus a nonexistent_pkg that should NOT match — so we
            # expect at least one symbol-subject and at least one
            # package-subject row.
            assert len(matched) >= 1, (
                "expected at least one symbol-resolved vuln finding"
            )
            assert len(unmatched) >= 1, (
                "expected at least one package-only vuln finding"
            )
            # symbol-subject rows must reference a real symbols.id.
            for r in matched:
                assert r["subject_id"] is not None
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?",
                    (r["subject_id"],),
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
            # package-subject rows must have NULL subject_id.
            for r in unmatched:
                assert r["subject_id"] is None
    finally:
        os.chdir(old_cwd)


def test_vuln_reachability_tag_codes():
    """``_vuln_reachability_tag`` returns the documented enumeration."""
    assert _vuln_reachability_tag(1) == "reachable"
    assert _vuln_reachability_tag(-1) == "unreachable"
    assert _vuln_reachability_tag(0) == "unknown"
    assert _vuln_reachability_tag(None) == "unknown"


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_vulns_findings_visible_via_cmd_findings_list(vuln_project, generic_report):
    """`roam findings list --detector vulns` returns rows after migration."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "vulns"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "vulns" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "vulns" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_vulns_findings_visible_via_cmd_findings_count(vuln_project, generic_report):
    """`roam findings count` includes a non-zero entry for vulns."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        _run_vulns_persist(vuln_project, generic_report)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(vuln_project, "vulns")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_vulns_no_findings_table_no_crash(vuln_project, generic_report):
    """``roam vulns --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index but
    before vulns --persist runs. The normal vulns text / JSON output
    (and the vulnerabilities-table write) must keep working — registry
    emit is purely additive.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vulns",
                "--import-file",
                generic_report,
                "--format",
                "generic",
                "--persist",
            ],
        )
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output

        # The vulnerabilities-table write path is authoritative and must
        # still have populated rows.
        with open_db(readonly=False) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM vulnerabilities"
            ).fetchone()[0]
        assert count >= 1, "vulnerabilities table empty despite successful import"
    finally:
        os.chdir(old_cwd)


def test_vulns_without_persist_does_not_emit_findings(vuln_project, generic_report):
    """Without --persist, no findings rows are written.

    The registry mirror is gated behind the explicit ``--persist`` flag —
    running ``roam vulns --import-file ...`` plain must remain
    findings-side-effect-free.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_project))
        runner = CliRunner()
        # No --persist.
        result = runner.invoke(
            cli,
            [
                "vulns",
                "--import-file",
                generic_report,
                "--format",
                "generic",
            ],
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'vulns'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist vulns still wrote to findings"
    finally:
        os.chdir(old_cwd)
