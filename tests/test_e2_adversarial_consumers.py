"""Adversarial aggregate shapes and large MCP preview boundaries."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.commands import cmd_audit, cmd_dogfood


@pytest.mark.parametrize("invalid", [[], "", 0, False])
def test_empty_malformed_child_summary_is_not_a_clean_result(monkeypatch, invalid):
    monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_audit, "_capture", lambda args: {"summary": invalid} if args[0] == "health" else {})
    result = CliRunner().invoke(cmd_audit.audit, ["--brief"], obj={"json": True})
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is True
    assert summary["child_evidence"]["health"]["state"] == "failed"


def test_dogfood_preserves_zero_health_over_legacy_fallback(monkeypatch):
    monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {})
    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", lambda args: {"summary": {"health_score": 0, "score": 88}})
    result = CliRunner().invoke(cmd_dogfood.dogfood_cmd, ["--no-pr-analyze", "--no-audit-trail"], obj={"json": True})
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["health_score"] == 0
    assert "health 0" in summary["verdict"]


def test_large_summary_handle_is_small_and_retains_partial_state(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from roam.mcp_server import _maybe_handle_off

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    payload = {
        "command": "audit",
        "summary": {
            "verdict": "Incomplete stale-reference scan",
            "partial_success": True,
            "child_evidence": {"stale_refs": {"state": "incomplete", "details": "x" * 100000}},
        },
    }
    result = _maybe_handle_off(payload, tool_name="roam_audit")
    assert result["is_handle"] is True
    assert len(json.dumps(result).encode()) < 8192
    assert result["summary"]["partial_success"] is True
    assert result["preview"]["summary"]["partial_success"] is True
    assert result["preview"]["detail_omitted"] is True
    # The preview may shrink, but stored evidence must remain byte-for-byte meaningful.
    from pathlib import Path

    assert json.loads(Path(result["stored_at"]).read_text(encoding="utf-8")) == payload


@pytest.mark.parametrize("text", ["x", "\U0001f600", "\u0000"])
def test_preview_bound_accounts_for_escaped_strings(text):
    pytest.importorskip("fastmcp")
    from roam.mcp_server import _build_handle_preview

    payload = {key: text * 10000 for key in ("command", "schema", "schema_version", "version")}
    payload["summary"] = {
        "verdict": text * 10000,
        "state": text * 10000,
        "truncation_reason": text * 10000,
        "partial_success": True,
    }
    preview = _build_handle_preview(payload)
    assert len(json.dumps(preview).encode("utf-8")) <= 4096
    assert preview["summary"]["partial_success"] is True
    assert preview["detail_omitted"] is True
    assert payload["summary"]["verdict"] == text * 10000


def test_small_preview_preserves_full_summary():
    pytest.importorskip("fastmcp")
    from roam.mcp_server import _build_handle_preview

    summary = {"verdict": "observed", "partial_success": False, "health_score": 0}
    assert _build_handle_preview({"command": "audit", "summary": summary}) == {"command": "audit", "summary": summary}


@pytest.mark.parametrize("command", ["audit", "dogfood"])
@pytest.mark.parametrize("reason", ["budget", "detail_mode", None, "future_reason"])
@pytest.mark.parametrize("location", ["summary", "root"])
def test_explicit_budget_loss_cannot_be_overridden_by_clean_flag(monkeypatch, command, reason, location):
    child = {
        "summary": {"verdict": "observed", "partial_success": False, "truncated": True, "truncation_reason": reason}
    }
    if reason is None:
        child["summary"].pop("truncation_reason")
    if location == "root":
        child["truncated"] = child["summary"].pop("truncated")
        if "truncation_reason" in child["summary"]:
            child["truncation_reason"] = child["summary"].pop("truncation_reason")
    if command == "audit":
        monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
        complete = {"summary": {"verdict": "observed", "partial_success": False}}
        monkeypatch.setattr(cmd_audit, "_capture", lambda args: child if args[0] == "health" else complete)
        target, args = cmd_audit.audit, []
    else:
        monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
        monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {})
        monkeypatch.setattr(cmd_dogfood, "_run_subcommand", lambda args: child)
        target, args = cmd_dogfood.dogfood_cmd, ["--no-pr-analyze", "--no-audit-trail"]
    result = CliRunner().invoke(target, args, obj={"json": True})
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["partial_success"] is (reason != "detail_mode")
