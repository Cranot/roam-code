"""W1519 -- ``report <preset> --strict`` says "OK" for a section that cannot fail.

The defect. ``roam report security --strict`` exited 0 on a repo with three
unprotected entry points, three critical-risk symbols and a credential-shaped
literal, and printed ``3/3 sections OK``. ``--strict``'s help said "Exit
non-zero if any section fails", and ``_run_section`` defined "fails" as
``result.returncode != 0`` -- so the only two things that could make
``--strict`` refuse were a sub-command that carried its OWN gate flag, and a
crashed subprocess. Of the five built-in presets only ``guardian`` contains a
gated sub-command (``health --gate``); ``security``, ``first-contact``,
``pre-pr`` and ``refactor`` pass no gate flag to any section, so ``--strict``
was structurally incapable of failing on anything those presets found.

What this change does, and does NOT do. It does not redefine ``--strict``:
turning "any section reported findings" into a failure would make
``report first-contact --strict`` and ``report refactor --strict`` fail
unconditionally, because map / health / weather / layers / coupling / dead /
fan all report something on every real repo. It makes the envelope and the
text say which sections can refuse and which merely report, so a reader stops
mistaking "the section ran" for "the section found nothing".

The load-bearing negative test is ``test_gate_pattern_is_not_a_gate_flag``.
``coverage-gaps --gate-pattern 'auth|permission|guard'`` -- the ``security``
preset's second section -- names which SYMBOLS count as a gate; it does not
make the command refuse. Classifying gate flags by a ``--gate`` prefix match
would report that section as gate-capable and hand the reader exactly the
false assurance this whole change exists to remove.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_report import (
    _GATE_FLAGS,
    PRESETS,
    _section_is_gated,
)
from tests.conftest import (
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def report_project(tmp_path):
    """Minimal indexed Python project the security preset can run against."""
    proj = tmp_path / "w1519_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "def require_auth(user):\n"
        '    """Gate."""\n'
        "    return bool(user)\n"
        "\n"
        "\n"
        "def login(user, pw):\n"
        '    """Entry point with no gate on the path."""\n'
        "    return {'user': user}\n"
    )
    (src / "api.py").write_text(
        "from src.auth import login\n"
        "\n"
        "\n"
        "def handle_request(user, pw):\n"
        '    """Public entry point."""\n'
        "    return login(user, pw)\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# MUST FIRE -- the sections that cannot refuse are named as such
# ---------------------------------------------------------------------------


class TestAdvisoryClassification:
    def test_security_preset_has_no_gate_capable_section(self):
        """Every section of the shipped `security` preset is advisory.

        This is the finding stated as an assertion: `report security --strict`
        cannot fail on a security finding, because not one of its three
        sections carries a flag that makes the sub-command refuse.
        """
        sections = PRESETS["security"]["sections"]
        gated = [s["title"] for s in sections if _section_is_gated(s)]
        assert gated == [], (
            "a security preset section now carries a gate flag -- if that is "
            f"deliberate, update this test and the CHANGELOG; got {gated!r}"
        )

    def test_guardian_health_gate_is_recognised_as_gate_capable(self):
        """The one shipped gated section must classify as gated.

        Without this the classifier could satisfy the test above by simply
        answering "advisory" to everything.
        """
        sections = PRESETS["guardian"]["sections"]
        gated = [s["title"] for s in sections if _section_is_gated(s)]
        assert "Health Gate" in gated, gated

    def test_custom_preset_sections_are_classified_from_their_command(self):
        """Classification reads the command line, not an annotation.

        Presets loaded from ``--config`` get the same treatment as built-ins;
        nothing has to be kept in sync by hand.
        """
        assert _section_is_gated({"title": "S", "command": ["secrets", "--fail-on-found"]}) is True
        assert _section_is_gated({"title": "S", "command": ["secrets"]}) is False


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- the classifier must not over-claim
# ---------------------------------------------------------------------------


class TestGatePatternIsNotAGate:
    def test_gate_pattern_is_not_a_gate_flag(self):
        """`--gate-pattern` selects SYMBOLS; it does not make a command refuse.

        A ``--gate`` prefix match would classify the security preset's
        coverage-gaps section as gate-capable. The reader would then be told
        the section can fail the build, which is precisely the false claim
        this module exists to prevent -- a worse defect than the silence it
        replaced, because it is stated with confidence.
        """
        assert "--gate-pattern" not in _GATE_FLAGS
        coverage_section = {
            "title": "Coverage Gaps",
            "command": ["coverage-gaps", "--gate-pattern", "auth|permission|guard"],
        }
        assert _section_is_gated(coverage_section) is False

    def test_gate_and_gate_pattern_are_distinguished(self):
        """The exact-token rule keeps `--gate` gating while `--gate-pattern` does not."""
        assert _section_is_gated({"title": "H", "command": ["health", "--gate"]}) is True
        assert _section_is_gated({"title": "C", "command": ["coverage-gaps", "--gate-pattern", "x"]}) is False


# ---------------------------------------------------------------------------
# Envelope + exit code
# ---------------------------------------------------------------------------


class TestReportEnvelope:
    def test_security_json_names_its_advisory_sections(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "security"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")
        summary = data["summary"]

        assert summary["sections_advisory"] == 3, summary
        assert sorted(summary["advisory_sections"]) == sorted(["Risk", "Coverage Gaps", "Secret Scan"]), summary
        assert "advisory" in summary["verdict"], summary["verdict"]

    def test_every_section_carries_an_execution_state(self, cli_runner, report_project, monkeypatch):
        """`success` conflated "ran" with "clean"; a timeout was indistinguishable
        from a command that ran and refused. `execution_state` uses the
        vocabulary cmd_verify already publishes."""
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "security"], cwd=report_project, json_mode=True)
        data = parse_json_output(result, "report")

        states = {s["title"]: s["execution_state"] for s in data["sections"]}
        assert states, data
        assert set(states.values()) <= {"complete", "failed", "timed_out"}, states
        for section in data["sections"]:
            assert "gated" in section, section

    def test_text_output_says_ok_means_ran(self, cli_runner, report_project, monkeypatch):
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "security"], cwd=report_project)
        assert "[OK] means the section RAN" in result.output, result.output

    # --- must not fire -------------------------------------------------

    def test_strict_still_exits_zero_when_every_section_ran(self, cli_runner, report_project, monkeypatch):
        """Disclosure is not a gate.

        The outage this guards against: redefining --strict as "any section
        reported findings" would make `report first-contact --strict` and
        `report refactor --strict` fail on every real repo, because their
        sections report something unconditionally.
        """
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "security", "--strict"], cwd=report_project)
        assert result.exit_code == 0, result.output

    def test_sections_failed_stays_an_int(self, cli_runner, report_project, monkeypatch):
        """.github/workflows/architecture-guardian.yml asserts
        ``isinstance(summary["sections_failed"], int)`` and keeps guardian
        findings advisory. Adding keys must not change that key's type."""
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "security"], cwd=report_project, json_mode=True)
        summary = parse_json_output(result, "report")["summary"]
        assert isinstance(summary["sections_failed"], int), summary
        assert isinstance(summary["sections_ok"], int), summary

    def test_partial_success_is_false_when_nothing_failed(self, cli_runner, report_project, monkeypatch):
        """`partial_success` reports whether the REPORT is partial -- a section
        that could not run. A section that ran and found things is complete."""
        monkeypatch.chdir(report_project)
        result = invoke_cli(cli_runner, ["report", "security"], cwd=report_project, json_mode=True)
        summary = parse_json_output(result, "report")["summary"]
        assert summary["partial_success"] is False, summary
        assert summary["incomplete_reasons"] == [], summary
