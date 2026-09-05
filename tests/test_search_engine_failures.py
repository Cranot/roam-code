"""Real subprocess failures must never become empty, safe search results."""

from __future__ import annotations

import json
import subprocess

import pytest

from roam.commands.grep_helpers import SearchEngineError, _run_and_parse
from tests.conftest import invoke_cli, parse_json_output


def test_partial_stdout_survives_nonzero_search_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 2, "./example.py:2:found_here\n", "unreadable path"
        ),
    )
    with pytest.raises(SearchEngineError, match="exited 2") as caught:
        _run_and_parse(["rg"], tmp_path, 1)
    assert caught.value.matches == [{"path": "example.py", "line": 2, "content": "found_here"}]


@pytest.mark.parametrize("failure", [FileNotFoundError("rg"), subprocess.TimeoutExpired("rg", 1)])
def test_missing_or_timed_out_engine_is_not_no_matches(monkeypatch, tmp_path, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(SearchEngineError, match="did not complete"):
        _run_and_parse(["rg"], tmp_path, 1)


def test_successful_empty_search_stays_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""))
    assert _run_and_parse(["rg"], tmp_path, 1) == []


def _fail_search(*args, **kwargs):
    raise SearchEngineError("rg search exited 2: unreadable path")


def test_grep_partial_results_are_disclosed(cli_runner, indexed_project, monkeypatch):
    from roam.commands import cmd_grep

    def fail(*args, **kwargs):
        raise SearchEngineError(
            "rg search exited 2: unreadable path",
            [{"path": "src/service.py", "line": 1, "content": "partialneedle"}],
        )

    monkeypatch.setattr(cmd_grep, "_run_engine", fail)
    result = invoke_cli(cli_runner, ["grep", "partialneedle"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "grep")
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["total"] == 1
    assert data["summary"]["verdict"].startswith("Partial search:")
    assert data["matches"][0]["content"] == "partialneedle"


def test_grep_failed_empty_search_is_not_confirmed_absence(cli_runner, indexed_project, monkeypatch):
    from roam.commands import cmd_grep

    monkeypatch.setattr(cmd_grep, "_run_engine", _fail_search)
    result = invoke_cli(cli_runner, ["grep", "unknown"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "grep")
    assert data["summary"]["partial_success"] is True
    assert "incomplete" in data["summary"]["verdict"].lower()


@pytest.mark.parametrize("json_mode", [True, False])
def test_refs_text_does_not_authorize_removal_after_search_failure(cli_runner, indexed_project, monkeypatch, json_mode):
    from roam.commands import cmd_refs_text

    monkeypatch.setattr(cmd_refs_text, "run_search", _fail_search)
    result = invoke_cli(cli_runner, ["refs-text", "retired_name"], cwd=indexed_project, json_mode=json_mode)
    if json_mode:
        data = parse_json_output(result, "refs-text")
        assert data["summary"]["partial_success"] is True
        assert data["results"][0]["verdict"] == "REVIEW"
    else:
        assert "REVIEW" in result.output
        assert "SAFE-TO-REMOVE" not in result.output


@pytest.mark.parametrize("mode", ["text", "json", "sarif"])
def test_delete_check_refuses_failed_search_in_every_channel(cli_runner, indexed_project, monkeypatch, mode):
    from roam.commands import cmd_delete_check

    diff = "diff --git a/retired.py b/retired.py\n--- a/retired.py\n+++ b/retired.py\n@@ -1,2 +0,0 @@\n-def retired_name():\n-    pass\n"
    monkeypatch.setattr(cmd_delete_check, "_git_diff", lambda *a, **k: (diff, None))
    monkeypatch.setattr(cmd_delete_check, "run_search", _fail_search)
    argv = ["delete-check", "--ci"]
    if mode == "sarif":
        argv.insert(0, "--sarif")
    result = invoke_cli(cli_runner, argv, cwd=indexed_project, json_mode=mode == "json")
    assert result.exit_code == 5, result.output
    if mode == "json":
        data = json.loads(result.stdout)
        assert data["summary"]["overall"] == "REVIEW"
        assert data["summary"]["scan_incomplete"] is True
        assert data["deletions"][0]["verdict"] == "REVIEW"
    elif mode == "text":
        assert "Search incomplete" in result.output
        assert " SAFE" not in result.output
    else:
        sarif = json.loads(result.stdout)
        assert any(row["ruleId"] == "delete-check/review" for row in sarif["runs"][0]["results"])
