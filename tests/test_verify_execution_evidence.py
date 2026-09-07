"""A successful pytest process must carry evidence that tests actually ran."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands import cmd_verify
from roam.testing import pytest_evidence
from tests.conftest import git_init, index_in_process, invoke_cli


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    return tmp_path


@pytest.mark.parametrize("mode", ["collect_only", "setup_plan", "all_skipped"])
def test_successful_process_without_executed_tests_is_incomplete(project, monkeypatch, mode):
    if mode == "all_skipped":
        (project / "test_app.py").write_text(
            "import pytest\n@pytest.mark.skip(reason='unavailable')\ndef test_app():\n    assert False\n",
            encoding="utf-8",
        )
    else:
        monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only" if mode == "collect_only" else "--setup-plan")
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 0, result
    assert result["partial_success"] is True
    assert result["available"] is False


def test_actual_passing_execution_remains_complete(project):
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 100, result
    assert result["execution_state"] == "complete"


def test_successful_process_without_report_is_incomplete(project, monkeypatch):
    def run(argv, **kwargs):
        result = subprocess.CompletedProcess(argv, 0, "1 passed", "")
        result.roam_process = {"state": "completed", "tree_terminated": True}
        return result

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", run)
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 0, result
    assert result["partial_success"] is True


def test_collect_only_cannot_pass_the_public_verify_gate(project, monkeypatch):
    (project / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    git_init(project)
    output, code = index_in_process(project)
    assert code == 0, output
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    result = invoke_cli(CliRunner(), ["verify", "--checks", "tests", "test_app.py"], cwd=project, json_mode=True)
    assert result.exit_code == 5, result.output
    envelope = json.loads(result.stdout)
    assert envelope["summary"]["verification_complete"] is False
    assert envelope["categories"]["tests"]["partial_success"] is True


@pytest.mark.parametrize(
    "report",
    [
        "",
        "<testsuites>",
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>',
        '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase><failure/></testcase></testsuite>',
        '<testsuite tests="-1" failures="0" errors="0" skipped="0"/>',
        '<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase><failure/></testcase></testsuite>',
        '<!DOCTYPE testsuite [<!ENTITY pass "1">]><testsuite tests="&pass;" failures="0" errors="0" skipped="0"><testcase/></testsuite>',
    ],
    ids=["empty", "truncated", "missing-case", "hidden-failure", "negative-count", "failed-but-exit-zero", "doctype"],
)
def test_unusable_reports_cannot_turn_zero_exit_into_a_pass(project, monkeypatch, report):
    report_paths = []

    def run(argv, **kwargs):
        report_path = Path(argv[argv.index("--junitxml") + 1])
        report_path.write_text(report, encoding="utf-8")
        report_paths.append(report_path)
        result = subprocess.CompletedProcess(argv, 0, "1 passed", "")
        result.roam_process = {"state": "completed", "tree_terminated": True}
        return result

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", run)
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 0
    assert result["partial_success"] is True
    assert not report_paths[0].parent.exists()


def test_passing_and_skipped_counts_reach_the_public_category(project):
    with (project / "test_app.py").open("a", encoding="utf-8") as stream:
        stream.write("\nimport pytest\n@pytest.mark.skip(reason='optional')\ndef test_optional():\n    assert False\n")
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 100
    public = cmd_verify._category_summary({"tests": result})["tests"]["test_execution"]
    assert public["passed"] == public["skipped"] == 1
    assert public["tests"] == 2


def test_project_config_collection_only_is_not_overridden_or_passed(project):
    (project / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n", encoding="utf-8")
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 0
    assert result["available"] is False


@pytest.mark.parametrize("message", ["FAILED not_a_real_test.py::example", "No module named pytest"])
def test_passing_test_stdout_cannot_invent_a_failure(project, monkeypatch, message):
    (project / "test_app.py").write_text(
        f"def test_app():\n    print({message!r})\n    assert True\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-s")
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 20)
    assert result["score"] == 100, result
    assert result["violations"] == []


def test_report_size_is_bounded(project, monkeypatch):
    report = project / "oversized.xml"
    report.write_bytes(b"x" * 129)
    monkeypatch.setattr(pytest_evidence, "_REPORT_LIMIT", 128)
    assert pytest_evidence.read_pytest_report(report) == {"state": "report_too_large"}


@pytest.mark.parametrize("exception", [OSError, subprocess.TimeoutExpired])
def test_temporary_report_is_cleaned_after_launch_failure(project, monkeypatch, exception):
    paths = []

    def fail(argv, **kwargs):
        paths.append(Path(argv[argv.index("--junitxml") + 1]))
        raise exception(argv, 1) if exception is subprocess.TimeoutExpired else exception("launch failed")

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", fail)
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 1)
    assert result["score"] == 0
    assert result["partial_success"] is True
    assert not paths[0].parent.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO report control")
def test_non_regular_report_cannot_block_after_type_check(project):
    report = project / "pytest.xml"
    os.mkfifo(report)
    script = (
        "from pathlib import Path\nfrom roam.testing.pytest_evidence import read_pytest_report\n"
        "Path.is_file = lambda self: True\n"
        f"assert read_pytest_report(Path({str(report)!r}))['state'] == 'report_unavailable'\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=10)
