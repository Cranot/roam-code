"""Tests for the vulns command -- vulnerability scanning, import, and reachability."""

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
def vuln_project(project_factory):
    """Create a project with call chains for reachability testing."""
    return project_factory({
        "api.py": (
            "from service import process\n"
            "def handle(): return process()\n"
        ),
        "service.py": (
            "from utils import merge_data\n"
            "def process(): return merge_data({})\n"
        ),
        "utils.py": (
            "def merge_data(d): return d\n"
            "def unused(): pass\n"
        ),
        "config.py": (
            "def load_config(): pass\n"
        ),
    })


@pytest.fixture
def generic_report(tmp_path):
    """Create a generic vulnerability report JSON file."""
    report = [
        {"cve": "CVE-2024-0001", "package": "merge_data", "severity": "critical", "title": "RCE in merge_data"},
        {"cve": "CVE-2024-0002", "package": "load_config", "severity": "high", "title": "Config injection"},
        {"cve": "CVE-2024-0003", "package": "nonexistent_pkg", "severity": "low", "title": "Not in code"},
    ]
    p = tmp_path / "generic_vulns.json"
    p.write_text(json.dumps(report))
    return str(p)


@pytest.fixture
def npm_report(tmp_path):
    """Create an npm audit v2 format report."""
    report = {
        "vulnerabilities": {
            "lodash": {
                "severity": "high",
                "via": [{"title": "Prototype Pollution", "url": "https://github.com/advisories/GHSA-xxxx"}],
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


@pytest.fixture
def pip_report(tmp_path):
    """Create a pip-audit format report."""
    report = [
        {
            "name": "requests",
            "version": "2.25.0",
            "vulns": [
                {"id": "CVE-2023-9999", "fix_versions": ["2.28.0"], "description": "SSRF vulnerability"},
            ],
        },
    ]
    p = tmp_path / "pip_audit.json"
    p.write_text(json.dumps(report))
    return str(p)


@pytest.fixture
def trivy_report(tmp_path):
    """Create a Trivy format report."""
    report = {
        "Results": [
            {
                "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-2024-1111", "PkgName": "openssl", "Severity": "CRITICAL", "Title": "Buffer overflow"},
                    {"VulnerabilityID": "CVE-2024-2222", "PkgName": "curl", "Severity": "HIGH", "Title": "Use after free"},
                ]
            }
        ]
    }
    p = tmp_path / "trivy_report.json"
    p.write_text(json.dumps(report))
    return str(p)


@pytest.fixture
def osv_report(tmp_path):
    """Create an OSV scanner format report."""
    report = {
        "results": [
            {
                "packages": [
                    {
                        "package": {"name": "flask", "ecosystem": "PyPI"},
                        "vulnerabilities": [
                            {
                                "id": "GHSA-xxxx-yyyy-zzzz",
                                "summary": "XSS in Flask debug mode",
                                "database_specific": {"severity": "MEDIUM"},
                            },
                        ],
                    },
                ],
            },
        ],
    }
    p = tmp_path / "osv_report.json"
    p.write_text(json.dumps(report))
    return str(p)


@pytest.fixture
def empty_project(project_factory):
    """A minimal project with no vulnerabilities."""
    return project_factory({
        "main.py": "def main(): pass\n",
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_cli():
    """Build a minimal CLI group with the vulns command registered."""
    from roam.commands.cmd_vulns import vulns

    @click.group()
    @click.option("--json", "json_mode", is_flag=True, default=False)
    @click.option("--sarif", "sarif_mode", is_flag=True, default=False)
    @click.pass_context
    def cli(ctx, json_mode, sarif_mode):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_mode
        ctx.obj["sarif"] = sarif_mode
    cli.add_command(vulns)
    return cli


def _invoke(args, cwd, json_mode=False, sarif_mode=False):
    """Invoke vulns via a standalone CLI group (no cli.py dependency)."""
    cli = _build_cli()
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    if sarif_mode:
        full_args.append("--sarif")
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

    def test_runs_without_error(self, empty_project):
        result = _invoke(["vulns"], empty_project)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"

    def test_help_flag(self):
        cli = _build_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["vulns", "--help"])
        assert result.exit_code == 0
        assert "vuln" in result.output.lower()

    def test_empty_inventory(self, empty_project):
        result = _invoke(["vulns"], empty_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "No vulnerabilities" in result.output


# ===========================================================================
# 2. Import tests -- generic format
# ===========================================================================

class TestGenericImport:
    """Test importing generic vulnerability reports."""

    def test_import_generic(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"
        assert "VERDICT:" in result.output

    def test_import_generic_json(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "vulns"
        assert "summary" in data
        assert "vulnerabilities" in data
        assert data["summary"]["total"] == 3
        assert data["summary"]["imported"] == 3

    def test_import_generic_severity_breakdown(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        by_sev = data["summary"]["by_severity"]
        assert by_sev.get("critical", 0) == 1
        assert by_sev.get("high", 0) == 1
        assert by_sev.get("low", 0) == 1

    def test_import_shows_vulns_in_text(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project)
        assert "CVE-2024-0001" in result.output
        assert "merge_data" in result.output


# ===========================================================================
# 3. Import tests -- multiple formats
# ===========================================================================

class TestMultiFormatImport:
    """Test format auto-detection and explicit format selection."""

    def test_auto_detect_npm(self, vuln_project, npm_report):
        result = _invoke(
            ["vulns", "--import-file", npm_report, "--format", "auto"],
            vuln_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] >= 2

    def test_explicit_npm_format(self, vuln_project, npm_report):
        result = _invoke(
            ["vulns", "--import-file", npm_report, "--format", "npm-audit"],
            vuln_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] >= 2

    def test_auto_detect_pip(self, vuln_project, pip_report):
        result = _invoke(
            ["vulns", "--import-file", pip_report],
            vuln_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] >= 1

    def test_auto_detect_trivy(self, vuln_project, trivy_report):
        result = _invoke(
            ["vulns", "--import-file", trivy_report],
            vuln_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] >= 2

    def test_auto_detect_osv(self, vuln_project, osv_report):
        result = _invoke(
            ["vulns", "--import-file", osv_report],
            vuln_project, json_mode=True,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] >= 1


# ===========================================================================
# 4. Format detection unit tests
# ===========================================================================

class TestFormatDetection:
    """Test the _detect_format function directly."""

    def test_detect_npm_v2(self):
        from roam.commands.cmd_vulns import _detect_format
        data = {"vulnerabilities": {"lodash": {"severity": "high"}}}
        assert _detect_format(data) == "npm-audit"

    def test_detect_npm_v1(self):
        from roam.commands.cmd_vulns import _detect_format
        data = {"advisories": {"1": {"module_name": "lodash"}}}
        assert _detect_format(data) == "npm-audit"

    def test_detect_trivy(self):
        from roam.commands.cmd_vulns import _detect_format
        data = {"Results": [{"Vulnerabilities": []}]}
        assert _detect_format(data) == "trivy"

    def test_detect_osv(self):
        from roam.commands.cmd_vulns import _detect_format
        data = {"results": [{"packages": []}]}
        assert _detect_format(data) == "osv"

    def test_detect_pip_audit_list(self):
        from roam.commands.cmd_vulns import _detect_format
        data = [{"name": "requests", "vulns": []}]
        assert _detect_format(data) == "pip-audit"

    def test_detect_pip_audit_wrapped(self):
        from roam.commands.cmd_vulns import _detect_format
        data = {"dependencies": [{"name": "requests", "vulns": []}]}
        assert _detect_format(data) == "pip-audit"

    def test_detect_generic_list(self):
        from roam.commands.cmd_vulns import _detect_format
        data = [{"cve": "CVE-2024-0001", "package": "foo"}]
        assert _detect_format(data) == "generic"

    def test_detect_unknown_raises(self):
        from roam.commands.cmd_vulns import _detect_format
        with pytest.raises(ValueError, match="Cannot auto-detect"):
            _detect_format({"random_key": "value"})


# ===========================================================================
# 5. Inventory tests (no import, just query)
# ===========================================================================

class TestInventory:
    """Test querying existing vulnerability inventory."""

    def test_inventory_after_import(self, vuln_project, generic_report):
        # Import first
        _invoke(["vulns", "--import-file", generic_report], vuln_project)
        # Then query inventory
        result = _invoke(["vulns"], vuln_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] == 3

    def test_inventory_empty(self, empty_project):
        result = _invoke(["vulns"], empty_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] == 0


# ===========================================================================
# 6. Reachable-only filter tests
# ===========================================================================

class TestReachableOnly:
    """Test the --reachable-only flag."""

    def test_reachable_only_flag(self, vuln_project, generic_report):
        # Import first
        _invoke(["vulns", "--import-file", generic_report], vuln_project)
        # Query with reachable-only
        result = _invoke(["vulns", "--reachable-only"], vuln_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # All returned vulns should be reachable (or none if graph doesn't connect)
        for v in data.get("vulnerabilities", []):
            assert v["reachable"] == 1

    def test_reachable_only_empty_inventory(self, empty_project):
        result = _invoke(["vulns", "--reachable-only"], empty_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total"] == 0


# ===========================================================================
# 7. JSON output format tests
# ===========================================================================

class TestJsonOutput:
    """Test the JSON envelope structure."""

    def test_json_envelope_fields(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        assert data["command"] == "vulns"
        assert "version" in data
        assert "summary" in data
        assert "vulnerabilities" in data

    def test_json_summary_fields(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        summary = data["summary"]
        assert "verdict" in summary
        assert "total" in summary
        assert "by_severity" in summary
        assert "reachable_count" in summary
        assert isinstance(summary["by_severity"], dict)

    def test_json_vulnerability_fields(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        vulns = data.get("vulnerabilities", [])
        assert len(vulns) > 0
        for v in vulns:
            assert "cve_id" in v
            assert "package" in v
            assert "severity" in v
            assert "reachable" in v

    def test_json_verdict_content(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        verdict = data["summary"]["verdict"]
        assert "3 vulnerabilities" in verdict


# ===========================================================================
# 8. Text output format tests
# ===========================================================================

class TestTextOutput:
    """Test the plain-text output format."""

    def test_verdict_line(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project)
        assert "VERDICT:" in result.output

    def test_table_has_cve_column(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project)
        assert "CVE" in result.output

    def test_severity_shown_uppercase(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project)
        assert "CRITICAL" in result.output or "HIGH" in result.output

    def test_empty_inventory_text(self, empty_project):
        result = _invoke(["vulns"], empty_project)
        assert "No vulnerabilities" in result.output

    def test_import_count_shown(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project)
        assert "Imported" in result.output or "3" in result.output


# ===========================================================================
# 9. SARIF output tests
# ===========================================================================

class TestSarifOutput:
    """Test the SARIF 2.1.0 output format."""

    def test_sarif_structure(self, vuln_project, generic_report):
        _invoke(["vulns", "--import-file", generic_report], vuln_project)
        result = _invoke(["vulns"], vuln_project, sarif_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["version"] == "2.1.0"
        assert "$schema" in data
        assert "runs" in data
        assert len(data["runs"]) == 1

    def test_sarif_has_rules(self, vuln_project, generic_report):
        _invoke(["vulns", "--import-file", generic_report], vuln_project)
        result = _invoke(["vulns"], vuln_project, sarif_mode=True)
        data = json.loads(result.output)
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) > 0
        for rule in rules:
            assert rule["id"].startswith("vuln/")

    def test_sarif_has_results(self, vuln_project, generic_report):
        _invoke(["vulns", "--import-file", generic_report], vuln_project)
        result = _invoke(["vulns"], vuln_project, sarif_mode=True)
        data = json.loads(result.output)
        results = data["runs"][0]["results"]
        assert len(results) >= 3
        for r in results:
            assert "ruleId" in r
            assert "level" in r
            assert "message" in r

    def test_sarif_empty_inventory(self, empty_project):
        result = _invoke(["vulns"], empty_project, sarif_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["runs"][0]["results"]) == 0


# ===========================================================================
# 10. Severity helper tests
# ===========================================================================

class TestSeverityHelpers:
    """Test severity ranking and breakdown functions."""

    def test_severity_rank_order(self):
        from roam.commands.cmd_vulns import _severity_rank
        assert _severity_rank("critical") > _severity_rank("high")
        assert _severity_rank("high") > _severity_rank("medium")
        assert _severity_rank("medium") > _severity_rank("low")
        assert _severity_rank("low") > _severity_rank("unknown")

    def test_severity_rank_case_insensitive(self):
        from roam.commands.cmd_vulns import _severity_rank
        assert _severity_rank("CRITICAL") == _severity_rank("critical")
        assert _severity_rank("High") == _severity_rank("high")

    def test_severity_breakdown(self):
        from roam.commands.cmd_vulns import _severity_breakdown
        vulns = [
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "low"},
        ]
        breakdown = _severity_breakdown(vulns)
        assert breakdown["critical"] == 1
        assert breakdown["high"] == 2
        assert breakdown["low"] == 1
        assert "medium" not in breakdown  # zero-count removed

    def test_severity_breakdown_empty(self):
        from roam.commands.cmd_vulns import _severity_breakdown
        breakdown = _severity_breakdown([])
        assert len(breakdown) == 0


# ===========================================================================
# 11. Multiple import accumulation tests
# ===========================================================================

class TestMultipleImports:
    """Test that importing multiple reports accumulates vulnerabilities."""

    def test_accumulate_two_imports(self, vuln_project, generic_report, npm_report):
        # Import generic first
        _invoke(["vulns", "--import-file", generic_report], vuln_project)
        # Import npm second
        _invoke(["vulns", "--import-file", npm_report], vuln_project)
        # Check total
        result = _invoke(["vulns"], vuln_project, json_mode=True)
        data = json.loads(result.output)
        # Should have vulns from both reports
        assert data["summary"]["total"] >= 5  # 3 generic + 2 npm


# ===========================================================================
# 12. Matched file tracking tests
# ===========================================================================

class TestMatchedFiles:
    """Test that matched_file is populated for package-matching vulns."""

    def test_matched_vulns_have_file(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        # merge_data matches a symbol in utils.py
        merge_vulns = [v for v in data.get("vulnerabilities", []) if v["package"] == "merge_data"]
        if merge_vulns:
            assert merge_vulns[0].get("matched_file") is not None

    def test_unmatched_vulns_no_file(self, vuln_project, generic_report):
        result = _invoke(["vulns", "--import-file", generic_report], vuln_project, json_mode=True)
        data = json.loads(result.output)
        unmatched = [v for v in data.get("vulnerabilities", []) if v["package"] == "nonexistent_pkg"]
        if unmatched:
            assert unmatched[0].get("matched_file") is None
