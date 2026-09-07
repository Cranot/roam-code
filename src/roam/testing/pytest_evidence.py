"""Bounded pytest execution reports, not stdout-based success inference."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

_REPORT_LIMIT = 16 * 1024 * 1024
_PROCESS_OUTPUT_LIMIT = 8 * 1024 * 1024
_OUTCOMES = ("tests", "failures", "errors", "skipped")


def read_pytest_report(path: Path) -> dict:
    """Check pytest's counters against testcase records before reporting passes.

    The report is evidence from the selected test process, not authentication
    of that process or proof of test quality/coverage. Empty collect/setup-only
    sessions, unavailable reports and inconsistent counters cannot imply a pass.
    """
    try:
        if not path.is_file():
            return {"state": "report_unavailable"}
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {"state": "report_unavailable"}
            data = stream.read(_REPORT_LIMIT + 1)
        if len(data) > _REPORT_LIMIT:
            return {"state": "report_too_large"}
        text = data.decode("utf-8")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            return {"state": "report_invalid"}
        root = ET.fromstring(text)
        suites = [root] if root.tag == "testsuite" else list(root)
        if root.tag not in {"testsuite", "testsuites"} or not suites:
            return {"state": "report_invalid"}
        totals = dict.fromkeys((*_OUTCOMES, "passed"), 0)
        for suite in suites:
            if suite.tag != "testsuite":
                return {"state": "report_invalid"}
            counts = {}
            for key in _OUTCOMES:
                value = suite.get(key, "")
                if not re.fullmatch(r"[0-9]{1,9}", value):
                    return {"state": "report_invalid"}
                counts[key] = int(value)
            cases = suite.findall("testcase")
            actual = {"failures": 0, "errors": 0, "skipped": 0, "passed": 0}
            for case in cases:
                outcomes = [child.tag for child in case if child.tag in {"failure", "error", "skipped"}]
                if len(outcomes) > 1:
                    return {"state": "report_invalid"}
                key = {"failure": "failures", "error": "errors", "skipped": "skipped"}.get(
                    outcomes[0] if outcomes else "", "passed"
                )
                actual[key] += 1
            if counts["tests"] != len(cases) or any(counts[key] != actual[key] for key in _OUTCOMES[1:]):
                return {"state": "report_invalid"}
            for key, value in counts.items():
                totals[key] += value
            totals["passed"] += actual["passed"]
        return {
            "state": "reported",
            **totals,
            "metric_definition": "pytest_junit_testcase_outcomes; skipped includes expected failures",
        }
    except (OSError, ValueError, ET.ParseError):
        return {"state": "report_unavailable"}


def _run_pytest_process(command: list[str], *, cwd: str, timeout: int) -> subprocess.CompletedProcess:
    """Reuse the bounded launcher, including Windows jobs/Linux subreaping.

    Keep the import lazy: merely reading a JUnit report needs no command or
    process machinery. One implementation owns containment across consumers;
    this adapter supplies pytest's output budget and structured receipt.
    """
    from roam.commands.cmd_service_report import _capture_component_output, _start_component_process

    try:
        proc = _start_component_process(command, cwd=cwd, env=dict(os.environ))
    except RuntimeError as exc:
        raise OSError("pytest process containment could not initialize") from exc
    stdout, stderr, state, terminated, error = _capture_component_output(
        proc,
        timeout_seconds=timeout,
        output_limit=_PROCESS_OUTPUT_LIMIT,
    )
    result = subprocess.CompletedProcess(
        command, proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    )
    result.roam_process = {
        "state": state,
        "tree_terminated": terminated,
        "output_limit_bytes": _PROCESS_OUTPUT_LIMIT,
        "timeout_seconds": timeout,
        **({"error": error} if error else {}),
    }
    return result


def run_pytest_with_report(cmd: list[str], cwd: Path, timeout: int) -> tuple[subprocess.CompletedProcess, dict]:
    """Use a fresh private report per invocation; clean it up on every outcome."""
    with tempfile.TemporaryDirectory(prefix="roam-verify-") as directory:
        report = Path(directory) / "pytest.xml"
        command = [*cmd, "--junitxml", str(report), "-o", "junit_logging=no", "-o", "junit_log_passing_tests=false"]
        result = _run_pytest_process(command, cwd=str(cwd), timeout=timeout)
        process = result.roam_process
        if process["state"] != "completed" or process["tree_terminated"] is not True:
            # Even a valid report may have been written before capture failed
            # or a descendant escaped. Do not convert it to completed evidence.
            return result, {"state": "process_incomplete", "process": process}
        return result, {**read_pytest_report(report), "process": process}
