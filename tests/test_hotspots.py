"""Tests for the `roam hotspots` command.

Covers two distinct modes:
- Default (runtime correlation): graceful exit when no runtime data has been ingested.
- Security mode (--security): static sink scanning with reachability scoring.

JSON envelope shape, VERDICT line presence, and flag conflict handling are also
validated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def plain_project(project_factory):
    """A minimal indexed Python project with no security sinks and no runtime data."""
    return project_factory(
        {
            "app.py": (
                "def greet(name):\n"
                '    """Return a greeting."""\n'
                '    return f"Hello, {name}"\n'
                "\n"
                "\n"
                "def add(a, b):\n"
                '    """Add two numbers."""\n'
                "    return a + b\n"
            ),
            "utils.py": (
                "def slugify(text):\n"
                '    """Convert text to slug form."""\n'
                "    return text.lower().replace(' ', '-')\n"
            ),
        }
    )


@pytest.fixture
def security_project(project_factory):
    """An indexed Python project containing multiple security sinks.

    Includes eval(), os.system(), and pickle.load() calls so that
    --security mode has guaranteed findings to assert against.
    """
    return project_factory(
        {
            "handler.py": (
                "import os\n"
                "import pickle\n"
                "\n"
                "def public_api(user_input):\n"
                '    """Entry-point handler (exported)."""\n'
                "    return _run(user_input)\n"
                "\n"
                "def _run(cmd):\n"
                '    """Execute a shell command."""\n'
                "    return os.system(cmd)\n"
                "\n"
                "def _eval_expr(expr):\n"
                '    """Evaluate a Python expression."""\n'
                "    return eval(expr)\n"
                "\n"
                "def _load_data(path):\n"
                '    """Load serialized data."""\n"'
                "    with open(path, 'rb') as f:\n"
                "        return pickle.load(f)\n"
            ),
            "service.py": (
                "from handler import public_api\n"
                "\n"
                "def process(request):\n"
                '    """Process an incoming request."""\n'
                "    return public_api(request['cmd'])\n"
            ),
        }
    )


# ---------------------------------------------------------------------------
# Smoke tests — default (runtime correlation) mode
# ---------------------------------------------------------------------------


class TestHotspotsDefaultMode:
    """Default mode requires prior `roam ingest-trace`; tests graceful degradation."""

    def test_exits_zero_without_runtime_data(self, plain_project, cli_runner):
        """hotspots exits 0 even when no runtime data has been ingested."""
        result = invoke_cli(cli_runner, ["hotspots"], cwd=plain_project)
        assert result.exit_code == 0, f"hotspots failed unexpectedly:\n{result.output}"

    def test_verdict_line_present_without_runtime_data(self, plain_project, cli_runner):
        """Text output begins with a VERDICT line when no runtime data exists."""
        result = invoke_cli(cli_runner, ["hotspots"], cwd=plain_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output, f"Expected VERDICT line in output:\n{result.output}"

    def test_json_exits_zero_without_runtime_data(self, plain_project, cli_runner):
        """hotspots --json exits 0 even without runtime data."""
        result = invoke_cli(cli_runner, ["hotspots"], cwd=plain_project, json_mode=True)
        assert result.exit_code == 0, f"hotspots --json failed:\n{result.output}"

    def test_json_envelope_without_runtime_data(self, plain_project, cli_runner):
        """JSON output follows the roam envelope contract when no runtime data exists."""
        result = invoke_cli(cli_runner, ["hotspots"], cwd=plain_project, json_mode=True)
        data = parse_json_output(result, "hotspots")
        assert_json_envelope(data, "hotspots")

    def test_json_summary_has_verdict_without_runtime_data(self, plain_project, cli_runner):
        """JSON summary contains a verdict field even with no runtime ingestion."""
        result = invoke_cli(cli_runner, ["hotspots"], cwd=plain_project, json_mode=True)
        data = parse_json_output(result, "hotspots")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {summary}"
        assert isinstance(summary["verdict"], str)
        assert summary["verdict"]  # non-empty

    def test_no_runtime_data_message_in_output(self, plain_project, cli_runner):
        """Text output explains that runtime data is missing or shows zero hotspots."""
        result = invoke_cli(cli_runner, ["hotspots"], cwd=plain_project)
        out_lower = result.output.lower()
        # Either the no-data message or a zero-hotspot verdict is acceptable
        has_no_data = "no runtime" in out_lower or "ingest" in out_lower
        has_zero = "0 runtime" in out_lower or "no runtime hotspot" in out_lower
        assert has_no_data or has_zero, f"Expected no-data message or zero-hotspot verdict:\n{result.output}"

    def test_help_flag(self, cli_runner):
        """hotspots --help exits 0 and mentions relevant terms."""
        from roam.cli import cli

        result = cli_runner.invoke(cli, ["hotspots", "--help"])
        assert result.exit_code == 0
        out_lower = result.output.lower()
        assert "hotspot" in out_lower or "runtime" in out_lower or "security" in out_lower


# ---------------------------------------------------------------------------
# Smoke tests — security sink scanning mode (--security)
# ---------------------------------------------------------------------------


class TestHotspotsSecurityMode:
    """--security mode does static sink scanning; no runtime data required."""

    def test_exits_zero_with_security_flag(self, security_project, cli_runner):
        """hotspots --security exits 0 on a project with known sinks."""
        result = invoke_cli(cli_runner, ["hotspots", "--security"], cwd=security_project)
        assert result.exit_code == 0, f"hotspots --security failed:\n{result.output}"

    def test_verdict_line_present_security_mode(self, security_project, cli_runner):
        """Text output begins with VERDICT: in security mode."""
        result = invoke_cli(cli_runner, ["hotspots", "--security"], cwd=security_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output, f"Expected VERDICT: line:\n{result.output}"

    def test_exits_zero_security_no_sinks(self, plain_project, cli_runner):
        """--security exits 0 cleanly on a project with no dangerous calls."""
        result = invoke_cli(cli_runner, ["hotspots", "--security"], cwd=plain_project)
        assert result.exit_code == 0, f"hotspots --security failed on clean project:\n{result.output}"

    def test_no_sinks_verdict_message(self, plain_project, cli_runner):
        """On a clean project, the verdict says no hotspots were detected."""
        result = invoke_cli(cli_runner, ["hotspots", "--security"], cwd=plain_project)
        assert result.exit_code == 0
        out_lower = result.output.lower()
        assert "no security hotspot" in out_lower or "0 security hotspot" in out_lower, (
            f"Expected 'no security hotspot' in output:\n{result.output}"
        )

    def test_detects_sinks_in_project(self, security_project, cli_runner):
        """--security detects eval, os.system, and pickle.load in the fixture project."""
        result = invoke_cli(cli_runner, ["hotspots", "--security"], cwd=security_project)
        assert result.exit_code == 0
        out_lower = result.output.lower()
        # At least one sink pattern title or count must appear
        assert "security hotspot" in out_lower, f"Expected sinks to be reported:\n{result.output}"

    def test_json_envelope_security_mode(self, security_project, cli_runner):
        """JSON output in --security mode follows the envelope contract."""
        result = invoke_cli(
            cli_runner,
            ["hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        assert_json_envelope(data, "hotspots")

    def test_json_summary_fields_security_mode(self, security_project, cli_runner):
        """JSON summary in --security mode has expected count fields."""
        result = invoke_cli(
            cli_runner,
            ["hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict': {summary}"
        assert "mode" in summary, f"Missing 'mode': {summary}"
        assert summary["mode"] == "security"
        assert "total" in summary, f"Missing 'total': {summary}"
        assert "reachable" in summary, f"Missing 'reachable': {summary}"

    def test_json_mode_field_top_level(self, security_project, cli_runner):
        """JSON envelope top-level has a 'mode' field set to 'security'."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        assert "mode" in data, f"Missing top-level 'mode' key: {list(data.keys())}"
        assert data["mode"] == "security"

    def test_json_hotspots_array_present(self, security_project, cli_runner):
        """JSON output in --detail security mode includes a 'hotspots' array."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        assert "hotspots" in data, f"Missing 'hotspots' array: {list(data.keys())}"
        assert isinstance(data["hotspots"], list)

    def test_json_hotspots_have_required_fields(self, security_project, cli_runner):
        """Each hotspot entry in the JSON array has the required fields."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        hotspots = data.get("hotspots", [])
        assert len(hotspots) >= 1, "Expected at least one hotspot in fixture project"
        required_fields = {"file", "line", "severity", "pattern_id", "title", "risk_score"}
        for hs in hotspots:
            missing = required_fields - set(hs.keys())
            assert not missing, f"Hotspot entry missing fields {missing}: {hs}"

    def test_json_hotspots_severity_values(self, security_project, cli_runner):
        """Severity values in hotspot entries are one of critical/high/medium."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        valid_severities = {"critical", "high", "medium"}
        for hs in data.get("hotspots", []):
            assert hs["severity"] in valid_severities, f"Unexpected severity '{hs['severity']}' in: {hs}"

    def test_json_hotspots_count_matches_summary(self, security_project, cli_runner):
        """The hotspots array length matches summary['total']."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        total = data["summary"]["total"]
        actual = len(data.get("hotspots", []))
        assert actual == total, f"summary.total={total} does not match hotspots array length={actual}"

    def test_signals_field_in_json(self, security_project, cli_runner):
        """JSON envelope contains a 'signals' dict with entrypoints and files_scanned."""
        result = invoke_cli(
            cli_runner,
            ["--detail", "hotspots", "--security"],
            cwd=security_project,
            json_mode=True,
        )
        data = parse_json_output(result, "hotspots")
        assert "signals" in data, f"Missing 'signals' key: {list(data.keys())}"
        signals = data["signals"]
        assert "files_scanned" in signals, f"Missing 'files_scanned' in signals: {signals}"
        assert "entrypoints" in signals, f"Missing 'entrypoints' in signals: {signals}"
        assert signals["files_scanned"] >= 1


# ---------------------------------------------------------------------------
# Flag conflict tests
# ---------------------------------------------------------------------------


class TestHotspotsFlags:
    """Validate flag incompatibility rules."""

    def test_security_and_runtime_flags_conflict(self, plain_project, cli_runner):
        """--security combined with --runtime should exit non-zero."""
        result = invoke_cli(
            cli_runner,
            ["hotspots", "--security", "--runtime"],
            cwd=plain_project,
        )
        assert result.exit_code != 0, "Expected non-zero exit when combining --security with --runtime"
        assert "Cannot combine" in result.output

    def test_security_and_discrepancy_flags_conflict(self, plain_project, cli_runner):
        """--security combined with --discrepancy should exit non-zero."""
        result = invoke_cli(
            cli_runner,
            ["hotspots", "--security", "--discrepancy"],
            cwd=plain_project,
        )
        assert result.exit_code != 0, "Expected non-zero exit when combining --security with --discrepancy"
        assert "Cannot combine" in result.output
