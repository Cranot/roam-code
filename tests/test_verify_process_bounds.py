"""Real pytest producers must stay within output and process-lifetime bounds."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from roam.commands import cmd_verify
from roam.testing import pytest_evidence
from tests.conftest import git_init, index_in_process, invoke_cli


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-s")
    return tmp_path


def _write_test(project, body):
    (project / "test_app.py").write_text(
        "def test_app():\n" + "\n".join("    " + line for line in body.splitlines()) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("stream", ["stdout", "stderr", "combined"])
def test_excessive_output_cannot_pass_even_with_valid_junit(project, monkeypatch, stream):
    monkeypatch.setattr(pytest_evidence, "_PROCESS_OUTPUT_LIMIT", 8192, raising=False)
    body = "import sys\n"
    if stream in {"stdout", "combined"}:
        body += "sys.stdout.write('x' * 6000)\nsys.stdout.flush()\n"
    if stream in {"stderr", "combined"}:
        body += "sys.stderr.write('x' * 6000)\nsys.stderr.flush()\n"
    if stream != "combined":
        body += f"sys.{stream}.write('x' * 6000)\nsys.{stream}.flush()\n"
    _write_test(project, body)
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 10)
    assert result["score"] == 0, result
    assert result["partial_success"] is True
    assert result["test_execution"]["process"]["state"] == "oversized"


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"), reason="verified descendant containment on Windows/Linux"
)
def test_root_exit_cleans_inherited_output_writers(project):
    _write_test(
        project, "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])"
    )
    start = time.monotonic()
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 10)
    elapsed = time.monotonic() - start
    assert elapsed < 2.5, (elapsed, result)
    assert result["score"] == 100, result
    assert result["test_execution"]["process"]["tree_terminated"] is True


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"), reason="verified descendant containment on Windows/Linux"
)
def test_detached_output_child_cannot_outlive_successful_test_run(project):
    marker = project / "late-child-write"
    child = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).touch()"
    _write_test(
        project,
        f"import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)",
    )
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 10)
    time.sleep(2.2)
    assert not marker.exists(), "a test descendant survived verify and wrote after the root exited"
    assert result["score"] == 100, result
    assert result["test_execution"]["process"]["tree_terminated"] is True


def test_timeout_carries_process_cleanup_evidence(project):
    _write_test(project, "import time\ntime.sleep(3)")
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 1)
    assert result["score"] == 0
    assert result["timed_out"] is True
    assert result["partial_success"] is True
    assert result["test_execution"]["process"]["state"] == "timeout"


def test_output_overflow_reaches_public_verify_gate(project, monkeypatch):
    monkeypatch.setattr(pytest_evidence, "_PROCESS_OUTPUT_LIMIT", 8192, raising=False)
    _write_test(project, "print('x' * 16384)")
    (project / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    git_init(project)
    output, code = index_in_process(project)
    assert code == 0, output
    result = invoke_cli(CliRunner(), ["verify", "--checks", "tests", "test_app.py"], cwd=project, json_mode=True)
    envelope = json.loads(result.stdout)
    assert result.exit_code == 5, envelope
    assert envelope["summary"]["verification_complete"] is False
    assert envelope["categories"]["tests"]["test_execution"]["process"]["state"] == "oversized"


def test_passing_and_failing_tests_retain_real_outcomes(project):
    for expected, body in [(100, "assert True"), (0, "assert False")]:
        _write_test(project, body)
        result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 10)
        assert result["score"] == expected, result
        assert result["available"] is True
        assert result["test_execution"]["tests"] == 1


def test_capture_cleans_its_launch_boundary_on_cancellation(monkeypatch):
    from roam.commands import cmd_service_report as process_runner

    class Process:
        stdout = io.BytesIO(b"ready")
        stderr = io.BytesIO()

    proc = Process()
    cleanup = []

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(process_runner, "_wait_for_component_root", interrupt)
    monkeypatch.setattr(
        process_runner, "_terminate_component_process_tree", lambda target: cleanup.append(target) or True
    )
    with pytest.raises(KeyboardInterrupt):
        process_runner._capture_component_output(proc, timeout_seconds=1)
    assert cleanup == [proc]
    assert proc.stdout.closed and proc.stderr.closed


@pytest.mark.parametrize("terminated", [None, False])
def test_valid_report_with_unverified_cleanup_is_not_completed(project, monkeypatch, terminated):
    def run(command, **kwargs):
        from pathlib import Path

        Path(command[command.index("--junitxml") + 1]).write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase/></testsuite>', encoding="utf-8"
        )
        result = subprocess.CompletedProcess(command, 0, "1 passed", "")
        result.roam_process = {"state": "completed", "tree_terminated": terminated}
        return result

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", run)
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 10)
    assert result["score"] == 0
    assert result["available"] is False
    assert result["partial_success"] is True
    assert "cleanup unverified" in result["unavailable_reason"]


def test_containment_launch_failure_does_not_fallback(project, monkeypatch):
    from roam.commands import cmd_service_report as process_runner

    def fail(*args, **kwargs):
        raise RuntimeError("containment unavailable")

    monkeypatch.setattr(process_runner, "_start_component_process", fail)
    result = cmd_verify._run_impacted_pytest(["test_app.py"], project, 10)
    assert result["score"] == 0
    assert result["partial_success"] is True
    assert result["tests_targeted"] == 1
