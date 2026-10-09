"""An ungrounded task is disclosed instead of presented as a useful plan."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("json_mode", [False, True])
def test_generic_bug_fix_reports_typed_reason(tmp_path, monkeypatch, json_mode):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")
    args = (["--json"] if json_mode else []) + ["compile", "Fix the bug that causes incorrect results"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    if json_mode:
        summary = json.loads(result.output)["summary"]
        assert summary["named_paths_count"] == 0
        assert summary["plan_quality"] == 0.0
        assert summary["state"] == "unresolved_task"
        assert summary["reason"] == "no_plan_evidence"
        assert summary["partial_success"] is True
        assert "file or symbol" in summary["hint"]
    else:
        assert "unresolved_task" in result.output
        assert "no_plan_evidence" in result.output
        assert "file or symbol" in result.output


def test_named_bug_fix_retains_success(indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = CliRunner().invoke(cli, ["--json", "compile", "Fix the bug in src/models.py"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["named_paths_count"] > 0
    assert summary["partial_success"] is False
    assert summary.get("state") != "unresolved_task"
