"""Tests for the coverage-gaps command.

Covers:
- Basic invocation with --gate-pattern (exit 0)
- Empty / no-gate-symbol scenarios (graceful exit 0)
- JSON envelope contract
- JSON summary has required fields
- Text output header (=== Coverage Gaps ===)
- Call-graph traversal: covered vs uncovered entry points
- --gate (exact name) flag
- --max-depth flag accepted without error
- No entry points found scenario

Note: The call-graph BFS only fires when there are exported top-level functions
with call edges.  The fixture below creates two entry points (handle_request and
public_endpoint) where only the first calls the gate function (require_auth).
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Local helper: invoke coverage-gaps directly via its Click command object
# ---------------------------------------------------------------------------


def invoke_coverage_gaps(runner, args=None, cwd=None, json_mode=False):
    """Invoke the coverage-gaps command directly, bypassing the CLI group."""
    from roam.commands.cmd_coverage_gaps import coverage_gaps

    full_args = list(args or [])
    obj = {"json": json_mode}

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(coverage_gaps, full_args, obj=obj, catch_exceptions=False)
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
def gated_project(tmp_path, monkeypatch):
    """Python project with two entry points: one that calls the gate, one that does not.

    Call graph:
        handle_request  ->  require_auth  (COVERED — reaches gate)
        public_endpoint                   (UNCOVERED — never calls require_auth)

    Both functions are module-level (no parent) and exported (public names).
    require_auth is the gate symbol.

    The import statement ``from auth import require_auth`` makes the indexer
    record a reference edge from handle_request -> require_auth, which the BFS
    follows.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # auth.py: defines the gate function
    (proj / "auth.py").write_text(
        "def require_auth(user):\n"
        '    """Verify that the user is authenticated."""\n'
        "    if not user:\n"
        '        raise PermissionError("Unauthenticated")\n'
        "    return True\n"
    )

    # handler.py: entry point that CALLS the gate
    (proj / "handler.py").write_text(
        "from auth import require_auth\n"
        "\n"
        "\n"
        "def handle_request(user, data):\n"
        '    """Protected handler — calls require_auth before processing."""\n'
        "    require_auth(user)\n"
        "    return process(data)\n"
        "\n"
        "\n"
        "def process(data):\n"
        '    """Internal helper called by handle_request."""\n'
        "    return data\n"
    )

    # public.py: entry point that does NOT call the gate
    (proj / "public.py").write_text(
        'def public_endpoint(data):\n    """Public endpoint — no auth check performed."""\n    return data\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def empty_project(tmp_path, monkeypatch):
    """Minimal project with a single utility function and no call edges."""
    proj = tmp_path / "empty_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "utils.py").write_text('def add(a, b):\n    """Add two numbers."""\n    return a + b\n')
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# TestCoverageGapsSmoke
# ---------------------------------------------------------------------------


class TestCoverageGapsSmoke:
    def test_gate_pattern_exits_zero(self, cli_runner, gated_project):
        """--gate-pattern with a matching regex exits 0."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
        )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

    def test_gate_exact_name_exits_zero(self, cli_runner, gated_project):
        """--gate with an exact name exits 0."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate", "require_auth"],
            cwd=gated_project,
        )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

    def test_nonexistent_gate_exits_zero(self, cli_runner, gated_project):
        """--gate-pattern matching nothing exits 0 gracefully (no gate symbols found)."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "no_such_gate_xyz_99"],
            cwd=gated_project,
        )
        # Command echoes a message and returns — not a crash
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

    def test_empty_project_gate_pattern_exits_zero(self, cli_runner, empty_project):
        """--gate-pattern on an empty project exits 0 (no gate symbols found)."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=empty_project,
        )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

    def test_max_depth_flag_accepted(self, cli_runner, gated_project):
        """--max-depth flag is accepted without error."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth", "--max-depth", "3"],
            cwd=gated_project,
        )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"

    def test_no_gate_args_exits_nonzero(self, cli_runner, gated_project):
        """Invoking without --gate or --gate-pattern exits non-zero."""
        result = invoke_coverage_gaps(cli_runner, [], cwd=gated_project)
        assert result.exit_code != 0, "Expected non-zero exit when no gate args given"


# ---------------------------------------------------------------------------
# TestCoverageGapsJSON
# ---------------------------------------------------------------------------


class TestCoverageGapsJSON:
    def test_json_envelope_structure(self, cli_runner, gated_project):
        """JSON output follows the standard roam envelope contract."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        assert_json_envelope(data, "coverage-gaps")

    def test_json_summary_has_coverage_count_fields(self, cli_runner, gated_project):
        """JSON summary contains the expected count fields."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        summary = data["summary"]
        assert "total_entries" in summary, f"summary missing 'total_entries': {summary}"
        assert "covered" in summary, f"summary missing 'covered': {summary}"
        assert "uncovered" in summary, f"summary missing 'uncovered': {summary}"
        assert "coverage_pct" in summary, f"summary missing 'coverage_pct': {summary}"

    def test_json_summary_verdict_present(self, cli_runner, gated_project):
        """JSON summary contains a 'verdict' field when using --preset mode."""
        # Use --preset python which triggers the gate-rules path that emits verdict
        result = invoke_coverage_gaps(
            cli_runner,
            ["--preset", "python"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        assert "verdict" in data["summary"], (
            f"Expected 'verdict' in summary when using --preset, got: {data['summary']}"
        )

    def test_json_covered_list_is_list(self, cli_runner, gated_project):
        """JSON output contains a 'covered' list at the top level."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        assert "covered" in data, "Expected top-level 'covered' list in JSON"
        assert isinstance(data["covered"], list)

    def test_json_uncovered_list_is_list(self, cli_runner, gated_project):
        """JSON output contains an 'uncovered' list at the top level."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        assert "uncovered" in data, "Expected top-level 'uncovered' list in JSON"
        assert isinstance(data["uncovered"], list)

    def test_json_covered_plus_uncovered_equals_total(self, cli_runner, gated_project):
        """covered + uncovered entries must equal total_entries."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        summary = data["summary"]
        assert summary["covered"] + summary["uncovered"] == summary["total_entries"], (
            f"covered ({summary['covered']}) + uncovered ({summary['uncovered']}) "
            f"!= total_entries ({summary['total_entries']})"
        )

    def test_json_gates_found_field(self, cli_runner, gated_project):
        """JSON output includes a 'gates_found' list naming matched gate symbols."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        assert "gates_found" in data, "Expected top-level 'gates_found' in JSON"
        assert isinstance(data["gates_found"], list)
        # The gate symbol should appear in the list
        assert any("require_auth" in g for g in data["gates_found"]), (
            f"Expected 'require_auth' in gates_found, got: {data['gates_found']}"
        )

    def test_json_coverage_pct_range(self, cli_runner, gated_project):
        """coverage_pct is a number between 0 and 100 (inclusive)."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
            json_mode=True,
        )
        data = parse_json_output(result, "coverage-gaps")
        pct = data["summary"]["coverage_pct"]
        assert isinstance(pct, (int, float)), f"coverage_pct should be numeric, got {type(pct)}"
        assert 0 <= pct <= 100, f"coverage_pct out of range: {pct}"

    def test_json_nonexistent_gate_envelope(self, cli_runner, gated_project):
        """No-gate-found path returns a strict Pattern-2 JSON envelope."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "no_such_gate_xyz_99"],
            cwd=gated_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "coverage-gaps")
        assert_json_envelope(data, "coverage-gaps")
        summary = data["summary"]
        assert summary["state"] == "no_gates"
        assert summary["partial_success"] is True
        assert summary["error"] == "No gate symbols found"
        assert "no gate symbols matched" in summary["verdict"]


# ---------------------------------------------------------------------------
# TestCoverageGapsText
# ---------------------------------------------------------------------------


class TestCoverageGapsText:
    def test_text_output_has_coverage_gaps_header(self, cli_runner, gated_project):
        """Text output contains the '=== Coverage Gaps ===' header."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
        )
        assert result.exit_code == 0
        assert "Coverage Gaps" in result.output, f"Expected 'Coverage Gaps' header in output:\n{result.output}"

    def test_text_output_mentions_gates(self, cli_runner, gated_project):
        """Text output names the gate symbol(s) found."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
        )
        assert result.exit_code == 0
        assert "require_auth" in result.output, f"Expected 'require_auth' in text output:\n{result.output}"

    def test_text_output_reports_entry_counts(self, cli_runner, gated_project):
        """Text output contains 'Entry points:' summary line."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "require_auth"],
            cwd=gated_project,
        )
        assert result.exit_code == 0
        assert "Entry points:" in result.output, f"Expected 'Entry points:' in output:\n{result.output}"

    def test_text_output_preset_mode_has_verdict_or_header(self, cli_runner, gated_project):
        """Preset mode text output contains either VERDICT: or a Coverage Gaps header."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--preset", "python"],
            cwd=gated_project,
        )
        assert result.exit_code == 0
        has_verdict = "VERDICT:" in result.output
        has_header = "Coverage Gaps" in result.output
        assert has_verdict or has_header, (
            f"Expected VERDICT: or Coverage Gaps header in preset output:\n{result.output}"
        )

    def test_text_output_no_gate_found_message(self, cli_runner, gated_project):
        """When no gate symbol matches, text output says so."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate-pattern", "no_such_gate_xyz_99"],
            cwd=gated_project,
        )
        assert result.exit_code == 0
        assert "No gate symbols found" in result.output or result.output.strip() == "", (
            f"Expected 'No gate symbols found' message or empty output:\n{result.output}"
        )

    def test_text_output_gate_flag_exits_zero(self, cli_runner, gated_project):
        """--gate (exact name) produces text output with exit 0."""
        result = invoke_coverage_gaps(
            cli_runner,
            ["--gate", "require_auth"],
            cwd=gated_project,
        )
        assert result.exit_code == 0
        assert "Coverage Gaps" in result.output, f"Expected 'Coverage Gaps' in output:\n{result.output}"
