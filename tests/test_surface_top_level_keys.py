"""Regression pin for `roam surface --json` top-level count-headline keys.

Pre-fix bug: `d["command_count"]` returned None on the surface envelope
because the headline integers lived ONLY under `summary.*`. CLAUDE.md
headline copy + landing-page docs imply top-level access; a fresh-install
audit reading `d["command_count"]` got None and read it as "broken
contract".

Fix: mirror the count headlines at envelope top level too (additive —
`summary.*` preserved for backward-compat). This test pins the mirror so
a future editor can't silently drop it.

Pattern-3a vocabulary discipline (CLAUDE.md): same concept, same name in
the two places it's consumed.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def _invoke_surface_json() -> dict:
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "surface"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_surface_top_level_command_count_mirrors_summary():
    data = _invoke_surface_json()
    assert "command_count" in data, "top-level command_count missing"
    assert data["command_count"] == data["summary"]["command_count"]


def test_surface_top_level_canonical_count_mirrors_summary():
    data = _invoke_surface_json()
    assert "canonical_count" in data, "top-level canonical_count missing"
    assert data["canonical_count"] == data["summary"]["canonical_count"]


def test_surface_top_level_category_count_mirrors_summary():
    data = _invoke_surface_json()
    assert "category_count" in data, "top-level category_count missing"
    assert data["category_count"] == data["summary"]["category_count"]


def test_surface_top_level_mcp_tool_count_mirrors_summary():
    data = _invoke_surface_json()
    assert "mcp_tool_count" in data, "top-level mcp_tool_count missing"
    assert data["mcp_tool_count"] == data["summary"]["mcp_tool_count"]


def test_surface_top_level_mcp_tool_count_by_preset_mirrors_summary():
    data = _invoke_surface_json()
    assert "mcp_tool_count_by_preset" in data, "top-level mcp_tool_count_by_preset missing"
    assert data["mcp_tool_count_by_preset"] == data["summary"]["mcp_tool_count_by_preset"]


def test_surface_top_level_keys_are_integers():
    """Pin the headline integers as actual ints — agents that branch on
    `d["command_count"] > 200` get a TypeError on None and a clean bool
    on int. Pin both the existence AND the type.
    """
    data = _invoke_surface_json()
    for key in ("command_count", "canonical_count", "category_count", "mcp_tool_count"):
        assert isinstance(data[key], int), f"{key} must be int, got {type(data[key]).__name__}"
        assert data[key] > 0, f"{key} must be positive"


def test_surface_summary_keys_preserved_for_backward_compat():
    """`summary.*` mirror must survive — pre-fix consumers read from there.

    The fix is additive, not a migration. Removing `summary.command_count`
    would break every consumer that reads the canonical envelope shape.
    """
    data = _invoke_surface_json()
    summary = data["summary"]
    for key in ("command_count", "canonical_count", "category_count", "mcp_tool_count"):
        assert key in summary, f"summary.{key} missing — backward-compat regression"
