"""Tests for the ``roam_validate_plan`` MCP tool (R8.E3).

The tool runs in-process via ``_run_roam(...)`` Click invocations
against the live index in this repo, so the tests check verdict
shape, blocker codes, and edge cases against the dogfooded data.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip(
    "fastmcp", reason="MCP tool tests require fastmcp; mcp_server module won't import without it."
)

from roam.mcp_server import _vp_check_target_file, validate_plan


# ---------------------------------------------------------------------------
# Helper-level
# ---------------------------------------------------------------------------


def test_vp_check_target_file_existing_file_blocks_add(tmp_path):
    f = tmp_path / "exists.txt"
    f.write_text("hi", encoding="utf-8")
    ok, reason = _vp_check_target_file("exists.txt", must_exist=False, root=str(tmp_path))
    assert ok is False
    assert "already exists" in reason


def test_vp_check_target_file_missing_parent_blocks(tmp_path):
    ok, reason = _vp_check_target_file(
        "no/such/dir/file.txt", must_exist=False, root=str(tmp_path)
    )
    assert ok is False
    assert "parent directory missing" in reason or "does not exist" in reason


def test_vp_check_target_file_path_traversal_blocked(tmp_path):
    ok, reason = _vp_check_target_file(
        "../../../etc/passwd", must_exist=False, root=str(tmp_path)
    )
    assert ok is False
    assert "escapes project root" in reason


# ---------------------------------------------------------------------------
# Top-level validate_plan
# ---------------------------------------------------------------------------


def test_empty_operations_returns_structured_error():
    r = validate_plan(operations=[])
    assert r.get("isError") is True
    # Note: under error-storm rate-limit (>=3 same-code errors in a
    # row) the verbose ``error`` text is dropped — assert on
    # ``error_code`` which always survives.
    assert r.get("error_code") == "USAGE_ERROR"


def test_invalid_plan_json_returns_structured_error():
    r = validate_plan(plan_json="not json {{")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_unknown_kind_blocks():
    r = validate_plan(operations=[{"kind": "frobnicate", "symbol": "x"}])
    assert r["summary"]["verdict"] == "blocked"
    op = r["operations"][0]
    codes = {b["code"] for b in op["blockers"]}
    assert "UNKNOWN_KIND" in codes


def test_malformed_op_blocks():
    """Non-dict in operations must produce a MALFORMED_OP blocker, not crash."""
    r = validate_plan(operations=["just a string"])
    assert r["summary"]["verdict"] == "blocked"
    codes = {b["code"] for b in r["operations"][0]["blockers"]}
    assert "MALFORMED_OP" in codes


def test_missing_symbol_blocks():
    r = validate_plan(operations=[{"kind": "rename", "new_name": "y"}])
    codes = {b["code"] for op in r["operations"] for b in op["blockers"]}
    assert "MISSING_SYMBOL" in codes


def test_missing_new_name_for_rename_blocks():
    # Use a symbol we know exists in this repo so the symbol-existence
    # check passes and we isolate the new_name check.
    r = validate_plan(operations=[{"kind": "rename", "symbol": "_format_count"}])
    codes = {b["code"] for op in r["operations"] for b in op["blockers"]}
    assert "MISSING_NEW_NAME" in codes


def test_unknown_symbol_blocks():
    r = validate_plan(
        operations=[{"kind": "modify", "symbol": "this_symbol_definitely_does_not_exist_123abc"}]
    )
    codes = {b["code"] for op in r["operations"] for b in op["blockers"]}
    assert "SYMBOL_NOT_FOUND" in codes


def test_remove_with_callers_blocks():
    """``analyze_n1`` is called from cmd_n1 itself — has callers, must
    be blocked from removal."""
    r = validate_plan(operations=[{"kind": "remove", "symbol": "analyze_n1"}])
    op = r["operations"][0]
    codes = {b["code"] for b in op["blockers"]}
    assert "REMOVE_HAS_CALLERS" in codes
    # And the blast-radius fact should be > 0
    assert isinstance(op["facts"].get("blast_radius"), int)
    assert op["facts"]["blast_radius"] > 0


def test_add_existing_file_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "exists.py").write_text("x = 1\n", encoding="utf-8")
    r = validate_plan(operations=[{"kind": "add", "file": "exists.py"}])
    op = r["operations"][0]
    codes = {b["code"] for b in op["blockers"]}
    assert "INVALID_ADD_FILE" in codes


def test_verdict_aggregates_correctly():
    """Verdict order: blocked > needs-review > ok."""
    r = validate_plan(
        operations=[
            {"kind": "modify", "symbol": "_format_count"},  # ok
            {"kind": "remove", "symbol": "analyze_n1"},  # blocker
        ]
    )
    assert r["summary"]["verdict"] == "blocked"
    assert r["summary"]["blockers_count"] >= 1


def test_envelope_carries_schema_field():
    r = validate_plan(operations=[{"kind": "modify", "symbol": "_format_count"}])
    assert r.get("schema") == "roam-code.com/spec/validate-plan/v1"
    assert r.get("schema_version") == "1.0.0"


def test_plan_json_alternative_input():
    plan = json.dumps([{"kind": "modify", "symbol": "_format_count"}])
    r = validate_plan(plan_json=plan)
    assert r["summary"]["operations"] == 1
    assert r["summary"]["verdict"] in {"ok", "needs-review", "blocked"}


def test_plan_json_with_operations_wrapper():
    plan = json.dumps({"operations": [{"kind": "modify", "symbol": "_format_count"}]})
    r = validate_plan(plan_json=plan)
    assert r["summary"]["operations"] == 1
