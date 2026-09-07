"""A failed changeset measurement is not an empty impacted-test selection."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def project(project_factory, monkeypatch):
    root = project_factory(
        {
            "core.py": "def calculate():\n    return 1\n",
            "tests/test_core.py": "from core import calculate\ndef test_calculate():\n    assert calculate() == 1\n",
        }
    )
    monkeypatch.chdir(root)
    return root


@pytest.mark.parametrize("mode", ["--json", "--sarif", "text"])
def test_invalid_range_is_unavailable_in_every_channel(project, mode):
    args = [] if mode == "text" else [mode]
    result = CliRunner().invoke(cli, [*args, "test-impact", "roam-missing-ref-control"])
    assert result.exit_code == 6, result.output
    if mode == "--json":
        data = json.loads(result.stdout)
        assert data["summary"]["state"] == "diff_unavailable"
        assert data["summary"]["git_error"] == "git_error"
        assert data["summary"]["partial_success"] is True
        assert data["tests"] == []
    elif mode == "--sarif":
        invocation = json.loads(result.stdout)["runs"][0]["invocations"][0]
        assert invocation["executionSuccessful"] is False
        assert invocation["toolExecutionNotifications"]
    else:
        assert "unavailable" in result.stdout
        assert "no non-test source files changed" not in result.stdout


def test_valid_empty_range_remains_successful(project):
    result = CliRunner().invoke(cli, ["--json", "test-impact", "HEAD..HEAD"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["summary"]["partial_success"] is False
    assert data["tests"] == []


def test_real_changes_retain_reachable_test(project):
    (project / "core.py").write_text("def calculate():\n    return 2\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["--json", "test-impact"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["changed_files"] == ["core.py"]
    assert {item["file"] for item in data["tests"]} == {"tests/test_core.py"}


@pytest.mark.parametrize(
    "error, expected",
    [
        (FileNotFoundError("git missing"), "git_not_available"),
        (subprocess.TimeoutExpired("git", 10), "git_timeout"),
        (PermissionError("git denied"), "git_error"),
    ],
)
def test_diff_launch_failure_retains_reason(project, monkeypatch, error, expected):
    from roam.commands import cmd_test_impact as mod

    original_run = subprocess.run

    def fail_diff(args, *pos, **kwargs):
        if list(args[:3]) == ["git", "diff", "--name-only"]:
            raise error
        return original_run(args, *pos, **kwargs)

    monkeypatch.setattr(mod.subprocess, "run", fail_diff)
    result = CliRunner().invoke(cli, ["--json", "test-impact"])
    assert result.exit_code == 6, result.output
    assert json.loads(result.stdout)["summary"]["git_error"] == expected


def test_path_name_is_not_accepted_as_a_revision(project):
    result = CliRunner().invoke(cli, ["--json", "test-impact", "core.py"])
    assert result.exit_code == 6, result.output


def test_changed_paths_are_not_git_quoted(project):
    path = project / "source with space é.py"
    path.write_text("def separate():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", path.name], check=True, capture_output=True)
    # Compare HEAD to the index plus working tree; an unindexed file remains
    # an explicitly named changed path even when it has no indexed symbols.
    from roam.commands.cmd_test_impact import _changed_files

    assert _changed_files("HEAD") == [path.name]


def test_real_mcp_wrapper_preserves_unavailable_diff(project):
    from roam.mcp_server import roam_test_impact

    result = roam_test_impact(commit_range="roam-missing-ref-control", root=str(project))
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["state"] == "diff_unavailable"
    assert result["summary"]["git_error"] == "git_error"


def test_large_changeset_respects_sqlite_parameter_budget(project, monkeypatch):
    from roam.commands import cmd_test_impact as mod

    original_open = mod.open_db

    @contextmanager
    def bounded_open(**kwargs):
        with original_open(**kwargs) as conn:

            class BudgetedConnection:
                def execute(self, sql, params=()):
                    assert len(params) <= 999
                    return conn.execute(sql, params)

                def __getattr__(self, name):
                    return getattr(conn, name)

            yield BudgetedConnection()

    monkeypatch.setattr(mod, "open_db", bounded_open)
    monkeypatch.setattr(mod, "_changed_files", lambda _: ["core.py", *[f"absent_{i}.py" for i in range(1200)]])
    result = CliRunner().invoke(cli, ["--json", "test-impact"])
    assert result.exit_code == 0, result.output
    assert {row["file"] for row in json.loads(result.stdout)["tests"]} == {"tests/test_core.py"}
