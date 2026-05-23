"""Regression tests for ``_SCHEMA_DIFF`` matching the envelope ``cmd_diff``
actually emits. The pre-fix schema declared ``files`` (top-level array,
never emitted by any code path) and ``affected_symbols`` (declared as
``array`` but emitted as an integer count), which tripped Claude Code's
strict-schema guard (``safeParse -> return null``, microsoft/vscode-copilot-chat#41361
/ anthropics/claude-code#45839) and silently swallowed ``roam_diff``
responses on the MCP boundary.

Each test below fails on the pre-fix ``_SCHEMA_DIFF`` and passes after.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastmcp", reason="MCP schema tests require fastmcp.")

from roam.mcp_server import _SCHEMA_DIFF


def _top_level_props() -> dict:
    return _SCHEMA_DIFF["properties"]


def _summary_props() -> dict:
    return _SCHEMA_DIFF["properties"]["summary"]["properties"]


def test_schema_affected_symbols_is_integer_in_summary_and_top_level():
    """cmd_diff emits ``affected_symbols`` as a count, not an array."""
    assert _summary_props()["affected_symbols"]["type"] == "integer"
    assert _top_level_props()["affected_symbols"]["type"] == "integer"


def test_schema_does_not_declare_phantom_files_field():
    """The pre-fix schema declared a top-level ``files`` array; no emit
    path in cmd_diff.py ever produces that key."""
    assert "files" not in _top_level_props()


def test_schema_declares_actual_array_payloads():
    """The real array payloads emitted by cmd_diff are ``per_file`` and
    ``blast_radius`` (see cmd_diff.py:946-960 envelope_data)."""
    assert _top_level_props()["per_file"]["type"] == "array"
    assert _top_level_props()["blast_radius"]["type"] == "array"


def test_schema_mirrors_canonical_risk_pair():
    """W641-followup-E pins ``risk_level_canonical`` (str) + ``risk_rank``
    (int) in both summary and top-level for cross-command consumers."""
    assert _summary_props()["risk_level_canonical"]["type"] == "string"
    assert _summary_props()["risk_rank"]["type"] == "integer"
    assert _top_level_props()["risk_level_canonical"]["type"] == "string"
    assert _top_level_props()["risk_rank"]["type"] == "integer"
