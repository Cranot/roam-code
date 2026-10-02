"""Explicit aggregate output budgets preserve partial evidence."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.commands import cmd_audit, cmd_dogfood


@pytest.mark.parametrize("command", ["audit", "dogfood"])
@pytest.mark.parametrize("budget", [1000, 100000])
def test_aggregate_honors_requested_budget_without_losing_evidence(monkeypatch, command, budget):
    child = {
        "summary": {"verdict": "partial scan", "partial_success": True, "files_scanned": 9, "files_unreadable": 1},
        "items": [{"name": f"item_{i}", "detail": "x" * 200} for i in range(300)],
    }
    if command == "audit":
        monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
        monkeypatch.setattr(cmd_audit, "_capture", lambda args: child if args[0] == "stale-refs" else {})
        target, args = cmd_audit.audit, []
    else:
        monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
        monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {})
        monkeypatch.setattr(cmd_dogfood, "_run_subcommand", lambda args: child)
        target, args = cmd_dogfood.dogfood_cmd, ["--no-pr-analyze", "--no-audit-trail"]
    # Disable the default cap to distinguish the requested budget from fallback.
    monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", "0")
    result = CliRunner().invoke(target, args, obj={"json": True, "budget": budget})
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    summary = report["summary"]
    assert summary["partial_success"] is True
    evidence = summary["child_evidence"]["stale_refs"] if command == "audit" else summary["child_evidence"]["audit"]
    observed = evidence["summary"] if command == "audit" else evidence
    assert observed["files_scanned"] == 9 and observed["files_unreadable"] == 1
    if budget == 1000:
        assert summary["truncated"] is True
        assert summary["truncation_reason"] == "budget"
        assert summary["budget_tokens"] == budget
        assert "sections" not in report
    else:
        section = "stale_refs" if command == "audit" else "audit"
        assert len(report["sections"][section]["items"]) == 300
        assert not summary.get("truncated", False)


@pytest.mark.parametrize("partial", [False, True])
def test_brief_mode_names_omitted_detail_without_erasing_computation_state(monkeypatch, partial):
    child = {"summary": {"verdict": "observed", "partial_success": partial}, "items": [{"name": "finding"}]}
    monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
    complete = {"summary": {"verdict": "observed", "partial_success": False}}
    monkeypatch.setattr(cmd_audit, "_capture", lambda args: child if args[0] == "health" else complete)
    result = CliRunner().invoke(cmd_audit.audit, ["--brief"], obj={"json": True})
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    summary = report["summary"]
    assert summary["partial_success"] is partial
    assert summary["truncated"] is True
    assert summary["truncation_reason"] == "detail_mode"
    assert summary["detail_available"] is True
    assert summary["detail_command"] == "roam audit"
    assert "items" not in report["sections"]["health"]
