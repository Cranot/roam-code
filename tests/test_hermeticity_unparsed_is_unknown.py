"""An unparseable test file is UNKNOWN, never "hermetic".

Pre-fix, ``_scan_test_file`` floored to ``[]`` on ``SyntaxError`` / ``OSError``
/ an unresolvable path, and the caller computed
``hermetic = total_test_files - len(non_hermetic_files)``. A file that was
never parsed was therefore arithmetically indistinguishable from one that
parsed clean.

Measured: appending a syntax error to a file holding a live ``requests.get``
call flipped ``roam test-hermeticity --ci`` from exit 5 to exit 0, while the
envelope asserted ``hermeticity_rate: 100.0``, ``partial_success: false``,
verdict ``"all 1 test files are hermetic"``, and the agent-contract fact
``"1 Python test files scanned"`` -- zero were. tree-sitter still indexes the
file (so it counts toward ``total``), which is what kept the floor invisible.

The producer now returns an explicit skip reason and every sink -- the ``--ci``
exit code, the verdict, the rate, and the agent contract -- fails closed on it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

# A live network call, then a hard syntax error.
UNPARSEABLE_LEAKY = (
    'import requests\n\n\ndef test_network():\n    requests.get("https://example.com")\n\n\ndef broken(:\n    pass\n'
)
PARSEABLE_HERMETIC = "def test_pure():\n    assert 1 + 1 == 2\n"
PARSEABLE_LEAKY = 'import requests\n\n\ndef test_network():\n    requests.get("https://example.com")\n'


def _make_single_test_project(tmp_path: Path, body: str, name: str) -> Path:
    """Project whose ``tests/`` holds exactly one test file containing ``body``."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "foo.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests = proj / "tests"
    tests.mkdir()
    (tests / "test_only.py").write_text(body, encoding="utf-8")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True, env=env)
    return proj


def _indexed(tmp_path: Path, body: str, name: str):
    proj = _make_single_test_project(tmp_path, body, name)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed:\n{result.output}"
        yield proj
    finally:
        os.chdir(old_cwd)


@pytest.fixture
def unparseable_project(tmp_path: Path):
    """Indexed project whose only test file cannot be parsed by ``ast``."""
    yield from _indexed(tmp_path, UNPARSEABLE_LEAKY, "proj_unparseable")


@pytest.fixture
def clean_hermetic_project(tmp_path: Path):
    """NEGATIVE CONTROL: one genuinely hermetic, parseable test file."""
    yield from _indexed(tmp_path, PARSEABLE_HERMETIC, "proj_clean")


@pytest.fixture
def leaky_parseable_project(tmp_path: Path):
    """NEGATIVE CONTROL: parseable non-hermetic code must still be reported."""
    yield from _indexed(tmp_path, PARSEABLE_LEAKY, "proj_leaky")


def _envelope(args: list[str]) -> dict:
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", *args], catch_exceptions=False)
    return json.loads(result.stdout if hasattr(result, "stdout") else result.output)


# ---------------------------------------------------------------------------
# Producer: the scanner must say WHY it produced nothing.
# ---------------------------------------------------------------------------


class TestScannerReportsSkipReason:
    def test_syntax_error_reports_skip_reason(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file_ex

        f = tmp_path / "test_broken.py"
        f.write_text(UNPARSEABLE_LEAKY, encoding="utf-8")
        findings, skip_reason = _scan_test_file_ex(str(f))
        assert skip_reason == "syntax_error", (
            f"an unparseable file must report a skip reason, not a clean scan; got {skip_reason!r}"
        )
        assert findings == []

    def test_unreadable_file_reports_skip_reason(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file_ex

        findings, skip_reason = _scan_test_file_ex(str(tmp_path / "test_absent.py"))
        assert skip_reason == "unreadable", (
            f"a file that cannot be opened must report a skip reason; got {skip_reason!r}"
        )
        assert findings == []

    def test_path_outside_root_reports_skip_reason(self, tmp_path):
        from roam.commands.cmd_test_hermeticity import _scan_test_file_ex

        project = tmp_path / "proj"
        (project / "tests").mkdir(parents=True)
        findings, skip_reason = _scan_test_file_ex("../test_outside.py", project_root=project)
        assert skip_reason == "unresolvable_path", (
            f"a path escaping the project root must report a skip reason; got {skip_reason!r}"
        )
        assert findings == []

    def test_parseable_file_reports_no_skip_reason(self, tmp_path):
        """NEGATIVE CONTROL -- a readable, parseable file must NOT be skipped."""
        from roam.commands.cmd_test_hermeticity import _scan_test_file_ex

        f = tmp_path / "test_ok.py"
        f.write_text("import requests\nrequests.get('https://e.com')\n", encoding="utf-8")
        findings, skip_reason = _scan_test_file_ex(str(f))
        assert skip_reason is None, f"a parseable file must not be skipped; got {skip_reason!r}"
        assert any(x["kind"] == "network" for x in findings)


# ---------------------------------------------------------------------------
# Sinks: exit code, verdict, rate, agent contract.
# ---------------------------------------------------------------------------


class TestUnparsedFileFailsClosed:
    def test_ci_gate_does_not_pass_on_a_file_it_never_read(self, unparseable_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["test-hermeticity", "--ci"], catch_exceptions=False)
        assert result.exit_code == 5, (
            "--ci must fail closed when a test file could not be parsed "
            f"(its hermeticity is UNKNOWN, not clean); got {result.exit_code}\n{result.output}"
        )

    def test_verdict_does_not_claim_unparsed_files_are_hermetic(self, unparseable_project):
        verdict = _envelope(["test-hermeticity"])["summary"]["verdict"]
        assert "are hermetic" not in verdict, (
            f"verdict must not claim hermeticity for a file that was never parsed; got {verdict!r}"
        )

    def test_envelope_discloses_partial_success_and_names_what_it_skipped(self, unparseable_project):
        env = _envelope(["test-hermeticity"])
        summary = env["summary"]
        assert summary.get("partial_success") is True, (
            f"an unscanned test file makes this a degraded run; summary={summary}"
        )
        assert summary.get("scanned") == 0
        assert summary.get("skipped") == 1
        assert summary["total"] == 1, "the indexed file still counts toward the corpus total"
        skipped_files = env.get("skipped_files")
        assert skipped_files and skipped_files[0]["reason"] == "syntax_error", (
            f"the envelope must name what it could not scan; got {skipped_files!r}"
        )

    def test_rate_is_unknown_not_100_when_nothing_parsed(self, unparseable_project):
        summary = _envelope(["test-hermeticity"])["summary"]
        assert summary["hermeticity_rate"] is None, (
            f"0 files parsed cannot yield a rate of {summary['hermeticity_rate']!r}"
        )
        assert summary["hermetic"] == 0, (
            f"an unparsed file must not be counted hermetic; hermetic={summary['hermetic']}"
        )

    def test_agent_facts_do_not_overstate_the_scanned_count(self, unparseable_project):
        env = _envelope(["test-hermeticity"])
        facts = env["agent_contract"]["facts"]
        assert "1 Python test files scanned" not in facts, (
            f"agent_contract claimed a scan that never happened; facts={facts}"
        )
        assert "0 Python test files scanned" in facts, f"facts={facts}"
        assert env["agent_contract"]["risks"], "a degraded scan must surface a risk, not an empty risk list"

    def test_text_mode_discloses_the_unscanned_file(self, unparseable_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["test-hermeticity"], catch_exceptions=False)
        assert "NOT SCANNED" in result.output, f"text mode must disclose unscanned files too; got:\n{result.output}"


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS -- a fix that merely blocks/degrades everything fails here.
# ---------------------------------------------------------------------------


class TestHealthyPathUnchanged:
    def test_clean_project_still_passes_the_ci_gate(self, clean_hermetic_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["test-hermeticity", "--ci"], catch_exceptions=False)
        assert result.exit_code == 0, (
            f"a fully hermetic, parseable project must still pass --ci; got {result.exit_code}\n{result.output}"
        )

    def test_clean_project_envelope_is_undegraded(self, clean_hermetic_project):
        env = _envelope(["test-hermeticity"])
        summary = env["summary"]
        assert summary["verdict"] == "all 1 test files are hermetic"
        assert summary["hermeticity_rate"] == 100.0
        assert summary["hermetic"] == 1
        assert summary.get("partial_success") is not True
        assert "skipped_files" not in env, (
            "the healthy envelope must stay byte-identical to the pre-fix shape; "
            f"got extra key: {env.get('skipped_files')!r}"
        )
        assert env["agent_contract"]["facts"][0] == "1 Python test files scanned"

    def test_parseable_leaky_project_still_reports_and_gates(self, leaky_parseable_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["test-hermeticity", "--ci"], catch_exceptions=False)
        assert result.exit_code == 5, f"got {result.exit_code}\n{result.output}"
        env = _envelope(["test-hermeticity"])
        assert env["findings"], "parseable non-hermetic code must still be reported"
        assert env["summary"]["hermeticity_rate"] == 0.0
        assert env["summary"].get("skipped", 0) == 0
        assert "skipped_files" not in env
