"""B4: ``auth-gaps`` must say when it had nothing to analyze.

A repository with no route or controller files used to print
``0 auth gap(s) found`` -- the same words as a scan that read real route
files and found them all guarded. These tests pin the distinction through the
real CLI: a planted no-input repo, and two valid-case controls whose wording
must not change.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output

NO_INPUT = "no input: 0 route/controller files indexed (detector ran but had no input to analyze)"


def _project(tmp_path, name, files):
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    for rel, body in files.items():
        target = proj / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def python_only(tmp_path):
    return _project(tmp_path, "py_only", {"m.py": "def f():\n    return 1\n"})


@pytest.fixture
def unguarded_route(tmp_path):
    return _project(
        tmp_path,
        "unguarded",
        {"routes/web.php": "<?php\nRoute::post('/api/data', [ApiController::class, 'store']);\n"},
    )


@pytest.fixture
def guarded_route(tmp_path):
    return _project(
        tmp_path,
        "guarded",
        {
            "routes/web.php": (
                "<?php\nRoute::middleware('auth')->group(function () {\n"
                "    Route::get('/dashboard', [DashboardController::class, 'index']);\n"
                "});\n"
            )
        },
    )


def _json(cli_runner, proj, monkeypatch, args=()):
    monkeypatch.chdir(proj)
    result = invoke_cli(cli_runner, ["auth-gaps", *args], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    return parse_json_output(result, "auth-gaps")


def test_no_input_json_is_distinct_from_clean(cli_runner, python_only, monkeypatch):
    data = _json(cli_runner, python_only, monkeypatch)
    summary = data["summary"]
    assert summary["verdict"] == NO_INPUT
    assert summary["no_input"] is True
    assert summary["partial_success"] is True
    assert summary["run_state"] == "NO_INPUT"
    assert summary["total"] == 0
    assert NO_INPUT in data["agent_contract"]["facts"]


def test_no_input_text_says_nothing_was_analysed(cli_runner, python_only, monkeypatch):
    monkeypatch.chdir(python_only)
    result = invoke_cli(cli_runner, ["auth-gaps"], cwd=python_only)
    assert result.exit_code == 0, result.output
    assert f"VERDICT: {NO_INPUT}" in result.output
    assert "No auth gaps detected." not in result.output
    assert "(none found)" not in result.output


def test_control_unguarded_route_still_reports_finding(cli_runner, unguarded_route, monkeypatch):
    summary = _json(cli_runner, unguarded_route, monkeypatch)["summary"]
    assert summary.get("no_input", False) is False
    assert summary["total"] >= 1
    assert summary["verdict"].startswith(f"{summary['total']} auth gap(s) found")


def test_control_guarded_route_keeps_zero_gap_wording(cli_runner, guarded_route, monkeypatch):
    summary = _json(cli_runner, guarded_route, monkeypatch)["summary"]
    assert summary.get("no_input", False) is False
    assert summary["verdict"] == "0 auth gap(s) found"
    assert summary["run_state"] == "NO_AUTH_GAPS"
    assert summary["partial_success"] is False
