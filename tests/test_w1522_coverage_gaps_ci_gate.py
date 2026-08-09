"""W1522 -- `coverage-gaps` computed "blocking" violations and had no exit path at all.

The defect. ``roam coverage-gaps --preset python`` printed
``VERDICT: 1 blocking and 2 advisory gate violations across 3 findings`` and
exited 0. ``gate_presets.py`` documented ``severity: "error"`` as
``blocks CI`` -- the only "blocks CI" string in src/ or docs/ -- and the
command had no ``--ci`` / ``--gate`` / ``--fail-on-*`` flag and no exit path:
every exit site in the file was ``SystemExit(1)`` on a coverage-import
exception and ``SystemExit(2)`` on a missing ``--gate``/``--gate-pattern``.
The word "blocking" named nothing.

Two further states printed their own refusal and exited 0 as well:
``no_gates`` and ``no_entries`` both emit "coverage cannot be computed" with
``partial_success: True``, and returned success.

The shape of the fix. An OPT-IN ``--ci`` flag -- the repo's dominant gate-flag
name (boundary / taint / test-hermeticity / compatibility / py-types /
delete-check / guard-pr); ``--gate`` was unavailable because this command
already uses it for gate SYMBOL names. The default stays exit 0 in every mode.

Why the default MUST stay 0, and what would break otherwise. The ``security``
report preset's second section is
``coverage-gaps --gate-pattern 'auth|permission|guard'`` with no gate flag.
Gating without the opt-in would make ``roam report security`` fail on
essentially every repo, since 40-50% gate coverage is the normal state, not a
defect. The negative tests below pin the default at 0 on every branch.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output


@pytest.fixture
def gap_project(tmp_path):
    """Project that trips the python preset's error-severity critical-modules rule.

    `src/service.py` matches `critical-modules` (min_test_count=3) and has no
    tests at all, so the preset yields 1 error + 2 warnings.
    """
    proj = tmp_path / "w1522_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "service.py").write_text(
        'def charge(user, amount):\n    """Core business logic."""\n    return {\'user\': user, \'amount\': amount}\n'
    )
    (src / "app.py").write_text(
        "from src.service import charge\n"
        "\n"
        "\n"
        "def handle_request(user, amount):\n"
        '    """Public entry point with no auth gate on the path."""\n'
        "    return charge(user, amount)\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def warnings_only_project(tmp_path):
    """Project whose only preset violations are WARNING severity."""
    proj = tmp_path / "w1522_warn"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "mod.py").write_text('def foo(a):\n    """Doc."""\n    return a\n')
    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# MUST FIRE -- --ci makes "blocking" mean something
# ---------------------------------------------------------------------------


class TestCiGateFires:
    def test_error_severity_preset_violation_refuses(self, cli_runner, gap_project, monkeypatch):
        monkeypatch.chdir(gap_project)
        result = invoke_cli(cli_runner, ["coverage-gaps", "--preset", "python", "--ci"], cwd=gap_project)
        assert result.exit_code == 5, result.output
        assert "blocking" in result.output, result.output

    def test_uncovered_entry_points_refuse_in_traversal_mode(self, cli_runner, gap_project, monkeypatch):
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            ["coverage-gaps", "--gate-pattern", "auth|permission|guard", "--ci"],
            cwd=gap_project,
        )
        assert result.exit_code == 5, result.output

    def test_no_gates_state_refuses(self, cli_runner, gap_project, monkeypatch):
        """The verdict already said "coverage cannot be computed" and exited 0."""
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            ["coverage-gaps", "--gate-pattern", "zzz_matches_nothing_at_all", "--ci"],
            cwd=gap_project,
        )
        assert result.exit_code == 5, result.output
        assert "coverage cannot be computed" in result.output, result.output

    def test_no_gates_state_carries_scan_incomplete(self, cli_runner, gap_project, monkeypatch):
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            ["--json", "coverage-gaps", "--gate-pattern", "zzz_matches_nothing_at_all"],
            cwd=gap_project,
        )
        summary = json.loads(result.stdout)["summary"]
        assert summary["state"] == "no_gates", summary
        assert summary["scan_incomplete"] is True, summary
        assert summary["partial_success"] is True, summary

    def test_no_entries_state_refuses(self, cli_runner, gap_project, monkeypatch):
        """A real gate exists but the scope holds no entry points."""
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            [
                "coverage-gaps",
                "--gate-pattern",
                "charge",
                "--entry-pattern",
                "zzz_no_entry_matches_this",
                "--ci",
            ],
            cwd=gap_project,
        )
        assert result.exit_code == 5, result.output
        assert "coverage cannot be computed" in result.output, result.output

    def test_json_channel_agrees_with_text(self, cli_runner, gap_project, monkeypatch):
        """One decision, read by both channels."""
        monkeypatch.chdir(gap_project)
        text = invoke_cli(cli_runner, ["coverage-gaps", "--preset", "python", "--ci"], cwd=gap_project)
        as_json = invoke_cli(cli_runner, ["--json", "coverage-gaps", "--preset", "python", "--ci"], cwd=gap_project)
        assert text.exit_code == as_json.exit_code == 5, (text.exit_code, as_json.exit_code)


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- the default is unchanged, everywhere
# ---------------------------------------------------------------------------


class TestDefaultStaysZero:
    def test_preset_without_ci_still_exits_zero(self, cli_runner, gap_project, monkeypatch):
        monkeypatch.chdir(gap_project)
        result = invoke_cli(cli_runner, ["coverage-gaps", "--preset", "python"], cwd=gap_project)
        assert result.exit_code == 0, result.output
        assert "blocking" in result.output, "the finding is still reported, just not gated"

    def test_traversal_without_ci_still_exits_zero(self, cli_runner, gap_project, monkeypatch):
        """This is the exact invocation the `security` report preset runs.

        cmd_report.PRESETS["security"] section 2 is
        `coverage-gaps --gate-pattern 'auth|permission|guard'` with no gate
        flag. If this ever returns non-zero, `roam report security` starts
        reporting a failed section on every repo with normal gate coverage.
        """
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            ["coverage-gaps", "--gate-pattern", "auth|permission|guard"],
            cwd=gap_project,
        )
        assert result.exit_code == 0, result.output

    def test_no_gates_without_ci_still_exits_zero(self, cli_runner, gap_project, monkeypatch):
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            ["coverage-gaps", "--gate-pattern", "zzz_matches_nothing_at_all"],
            cwd=gap_project,
        )
        assert result.exit_code == 0, result.output

    def test_warning_only_violations_do_not_gate_under_ci(self, cli_runner, warnings_only_project, monkeypatch):
        """The outage this guards against.

        The python preset's `source-modules` rule flags every module without a
        matching test file. On a real repo that is hundreds of files, and it
        is advisory by design. Gating on warnings would make `--ci`
        unconditionally red.
        """
        monkeypatch.chdir(warnings_only_project)
        result = invoke_cli(cli_runner, ["coverage-gaps", "--preset", "python", "--ci"], cwd=warnings_only_project)
        assert result.exit_code == 0, result.output
        assert "0 blocking" in result.output, result.output

    def test_security_report_preset_section_carries_no_gate_flag(self):
        """Structural guard on the coupling, not just on the exit code.

        If someone adds `--ci` to the security preset's coverage-gaps section
        without deciding to, `report security` changes behaviour for every
        existing consumer. That is a contract decision; this test makes it a
        deliberate one.
        """
        from roam.commands.cmd_report import PRESETS

        section = next(s for s in PRESETS["security"]["sections"] if s["title"] == "Coverage Gaps")
        assert "--ci" not in section["command"], section["command"]


class TestEnvelopeAndExitCode:
    def test_gate_failure_exit_code_stays_inside_mcp_success_codes(self):
        """coverage-gaps is MCP-exposed in the core preset (roam_coverage_gaps).

        MCP `_SUCCESS_EXIT_CODES` is {0, 5}; exits 1 or 2 would flip the tool
        result to isError:true for a state that is a gate refusal, not a tool
        error.
        """
        from roam.exit_codes import EXIT_GATE_FAILURE
        from roam.mcp_server import _SUCCESS_EXIT_CODES

        assert EXIT_GATE_FAILURE == 5
        assert EXIT_GATE_FAILURE in _SUCCESS_EXIT_CODES

    def test_computable_traversal_reports_scan_incomplete_false(self, cli_runner, gap_project, monkeypatch):
        monkeypatch.chdir(gap_project)
        result = invoke_cli(
            cli_runner,
            ["--json", "coverage-gaps", "--gate-pattern", "charge"],
            cwd=gap_project,
        )
        summary = parse_json_output(result, "coverage-gaps")["summary"]
        assert summary["scan_incomplete"] is False, summary

    def test_severity_doc_names_the_flag(self):
        """`# Severity: "error" (blocks CI)` was the only "blocks CI" string in
        src/ or docs/, and it named a mechanism that did not exist."""
        import inspect

        from roam.commands import gate_presets

        source = inspect.getsource(gate_presets)
        assert "blocks CI under `coverage-gaps --ci`" in source
