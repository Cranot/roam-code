"""Impacted tests must use one bounded, identified project environment."""

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


def _python(directory):
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _fake_environment(directory):
    interpreter = _python(directory / ".venv")
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    interpreter.chmod(0o755)
    return interpreter


@pytest.fixture
def launches(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        Path(argv[argv.index("--junitxml") + 1]).write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="test_app"/></testsuite></testsuites>',
            encoding="utf-8",
        )
        result = subprocess.CompletedProcess(argv, 0, "1 passed", "")
        result.roam_process = {"state": "completed", "tree_terminated": True}
        return result

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", run)
    return calls


@pytest.mark.parametrize("nested", [False, True])
def test_selects_project_interpreter_and_working_directory(tmp_path, launches, nested):
    directory = tmp_path / "service" if nested else tmp_path
    interpreter = _fake_environment(directory)
    target = "service/tests/test_app.py" if nested else "tests/test_app.py"
    result = cmd_verify._run_impacted_pytest([target], tmp_path, 10)
    assert result["score"] == 100
    assert launches[0][0][0] == str(interpreter)
    assert launches[0][1]["cwd"] == str(directory)


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_environments_refuse_before_any_execution(tmp_path, launches, reverse):
    _fake_environment(tmp_path / "one")
    _fake_environment(tmp_path / "two")
    targets = ["one/tests/test_one.py", "two/tests/test_two.py"]
    if reverse:
        targets.reverse()
    result = cmd_verify._run_impacted_pytest(targets, tmp_path, 10)
    assert launches == []
    assert result["available"] is False
    assert result["partial_success"] is True
    assert "multiple project test environments" in result["unavailable_reason"]


def test_absent_environment_preserves_current_interpreter(tmp_path, launches):
    result = cmd_verify._run_impacted_pytest(["tests/test_app.py"], tmp_path, 10)
    assert result["score"] == 100
    assert launches[0][0][0] == sys.executable


def test_parent_environment_is_not_selected(tmp_path, launches):
    _fake_environment(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    result = cmd_verify._run_impacted_pytest(["tests/test_app.py"], project, 10)
    assert result["score"] == 100
    assert launches[0][0][0] == sys.executable


@pytest.mark.parametrize("kind", ["outside", "nested_git", "broken_environment", "empty"])
def test_unsafe_or_unavailable_selection_does_not_launch(tmp_path, launches, kind):
    target = "tests/test_app.py"
    if kind == "outside":
        target = "../outside/test_app.py"
    elif kind == "nested_git":
        (tmp_path / "child/.git").mkdir(parents=True)
        target = "child/tests/test_app.py"
    elif kind == "broken_environment":
        (tmp_path / ".venv").mkdir()
    result = cmd_verify._run_impacted_pytest([] if kind == "empty" else [target], tmp_path, 10)
    assert launches == []
    assert result["available"] is False
    assert result["score"] == 0
    assert result["partial_success"] is True


def test_launch_failure_is_structured(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("unusable interpreter")

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", fail)
    result = cmd_verify._run_impacted_pytest(["tests/test_app.py"], tmp_path, 10)
    assert result["available"] is False
    assert result["partial_success"] is True


@pytest.mark.parametrize("code,stderr", [(1, "No module named pytest"), (2, "collection failed"), (5, "no tests ran")])
def test_missing_test_execution_is_incomplete(tmp_path, monkeypatch, code, stderr):
    def run(argv, **kwargs):
        result = subprocess.CompletedProcess(argv, code, "", stderr)
        result.roam_process = {"state": "completed", "tree_terminated": True}
        return result

    monkeypatch.setattr(pytest_evidence, "_run_pytest_process", run)
    result = cmd_verify._run_impacted_pytest(["tests/test_app.py"], tmp_path, 10)
    assert result["partial_success"] is True
    assert result["available"] is False


def test_real_verify_cli_runs_nested_environment(tmp_path, monkeypatch):
    project = tmp_path / "project"
    service = project / "service"
    service.mkdir(parents=True)
    environment = service / ".venv"
    # Build from the resolved base interpreter: Python 3.10's EnvBuilder can
    # otherwise retain an outer uv venv as home and lose the standard library.
    subprocess.run(
        [str(Path(sys._base_executable).resolve()), "-m", "venv", "--without-pip", str(environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Reuse this test runner's pytest dependencies without network installation.
    # The child is still the actual project interpreter; the test checks prefix.
    monkeypatch.setenv("PYTHONPATH", str(Path(pytest.__file__).parent.parent))
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    # Keep the fixture independent of any enclosing checkout's pytest plugins.
    (service / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (project / ".gitignore").write_text(".roam/\n.venv/\n__pycache__/\n", encoding="utf-8")
    (service / "test_app.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "def test_project_environment():\n"
        f"    assert Path(sys.prefix) == Path({str(environment)!r})\n"
        f"    assert Path.cwd() == Path({str(service)!r})\n",
        encoding="utf-8",
    )
    git_init(project)
    output, code = index_in_process(project)
    assert code == 0, output
    result = invoke_cli(
        CliRunner(), ["verify", "--checks", "tests", "service/test_app.py"], cwd=project, json_mode=True
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["summary"]["verification_complete"] is True


def test_environment_disclosure_survives_public_category_projection():
    environment = {
        "interpreter": "/project/.venv/bin/python",
        "working_directory": "/project",
        "source": "project_venv",
    }
    public = cmd_verify._category_summary({"tests": {"score": 100, "violations": [], "test_environment": environment}})
    assert json.loads(json.dumps(public))["tests"]["test_environment"] == environment
