"""Tests for roam.security.vuln_store ingest_* parsers.

Closes the W1xxx coverage gap surfaced by `roam coverage-gaps` dogfooding:
the four scanner-format parsers (`ingest_npm_audit`, `ingest_pip_audit`,
`ingest_trivy`, `ingest_osv`) had zero direct test references in tests/
despite a documented npm-audit operator-precedence bug fix in the source.

Existing `tests/test_vuln.py` exercises the CLI + generic-format path
end-to-end but never imports the named parser functions. These tests
bind the parser contract on a synthetic SQLite connection so format
drift (or a regression of the documented cve_id bug) trips a unit-test
red, not an E2E red on a project with real scanner output.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from roam.security.vuln_store import (
    ensure_vuln_table,
    ingest_npm_audit,
    ingest_osv,
    ingest_pip_audit,
    ingest_trivy,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_vuln_table(c)
    return c


def _write(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


class TestNpmAuditIngest:
    def test_v2_format_parses_vulnerabilities_dict(self, conn, tmp_path):
        report = {
            "vulnerabilities": {
                "lodash": {
                    "severity": "high",
                    "via": [{"url": "https://github.com/advisories/GHSA-xxxx", "title": "Proto"}],
                },
            }
        }
        results = ingest_npm_audit(conn, _write(tmp_path, "npm.json", report))
        assert len(results) == 1
        r = results[0]
        assert r["package_name"] == "lodash"
        assert r["severity"] == "high"
        assert r["cve_id"] == "GHSA-xxxx"
        assert r["source"] == "npm-audit"

    def test_v2_documented_bug_fix_preserves_cve_across_via_entries(self, conn, tmp_path):
        """Regression for the operator-precedence bug noted in vuln_store.py:202-211.

        Prior code: a later via-entry lacking 'url' would WIPE the cve_id
        already derived from an earlier entry. The fixed code must
        preserve the running cve_id once set.
        """
        report = {
            "vulnerabilities": {
                "axios": {
                    "severity": "moderate",
                    "via": [
                        {"url": "https://github.com/advisories/GHSA-keep-me", "title": "First"},
                        # second entry has no 'url' — must NOT wipe cve_id
                        {"title": "Second entry without url"},
                    ],
                }
            }
        }
        results = ingest_npm_audit(conn, _write(tmp_path, "npm.json", report))
        assert len(results) == 1
        assert results[0]["cve_id"] == "GHSA-keep-me", "via-entry without url must not wipe a previously-found cve_id"

    def test_v1_advisories_format(self, conn, tmp_path):
        report = {
            "advisories": {
                "1234": {
                    "module_name": "minimist",
                    "severity": "low",
                    "title": "Proto",
                    "cves": ["CVE-2020-1234"],
                }
            }
        }
        results = ingest_npm_audit(conn, _write(tmp_path, "npm.json", report))
        assert len(results) == 1
        assert results[0]["cve_id"] == "CVE-2020-1234"
        assert results[0]["package_name"] == "minimist"


class TestPipAuditIngest:
    def test_list_format(self, conn, tmp_path):
        report = [
            {
                "name": "requests",
                "vulns": [{"id": "PYSEC-2023-0001", "description": "redirect leak"}],
            }
        ]
        results = ingest_pip_audit(conn, _write(tmp_path, "pip.json", report))
        assert len(results) == 1
        assert results[0]["package_name"] == "requests"
        assert results[0]["cve_id"] == "PYSEC-2023-0001"
        assert results[0]["source"] == "pip-audit"


class TestTrivyIngest:
    def test_results_block_format(self, conn, tmp_path):
        report = {
            "Results": [
                {
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2024-1111",
                            "PkgName": "openssl",
                            "Severity": "HIGH",
                            "Title": "buffer overflow",
                        }
                    ]
                }
            ]
        }
        results = ingest_trivy(conn, _write(tmp_path, "trivy.json", report))
        assert len(results) == 1
        assert results[0]["cve_id"] == "CVE-2024-1111"
        assert results[0]["severity"] == "high", "trivy severity must be lower-cased"
        assert results[0]["source"] == "trivy"


class TestOsvIngest:
    def test_nested_results_format(self, conn, tmp_path):
        report = {
            "results": [
                {
                    "packages": [
                        {
                            "package": {"name": "django"},
                            "vulnerabilities": [
                                {
                                    "id": "GHSA-osv-1",
                                    "summary": "XSS",
                                    "database_specific": {"severity": "MODERATE"},
                                }
                            ],
                        }
                    ]
                }
            ]
        }
        results = ingest_osv(conn, _write(tmp_path, "osv.json", report))
        assert len(results) == 1
        assert results[0]["cve_id"] == "GHSA-osv-1"
        assert results[0]["package_name"] == "django"
        assert results[0]["severity"] == "moderate", "osv severity must be lower-cased"
