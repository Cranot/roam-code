"""Assert every @_tool wrapper in mcp_server.py has all three side-effect
metadata flags (read_only / destructive / idempotent) as booleans.

The @_tool decorator stores them unconditionally in _TOOL_METADATA, so this
test acts as a regression guard against: hand-inserted entries that skip the
decorator, or future decorator refactors that drop a flag silently.
"""

from __future__ import annotations

import importlib
import os

import pytest

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def test_every_tool_has_side_effect_flags():
    import roam.mcp_server as mcp

    if mcp.FastMCP is None:
        pytest.skip("FastMCP not installed — _TOOL_METADATA is empty by design")

    old_preset = os.environ.get("ROAM_MCP_PRESET")
    os.environ["ROAM_MCP_PRESET"] = "full"
    try:
        mcp = importlib.reload(mcp)
    finally:
        if old_preset is None:
            os.environ.pop("ROAM_MCP_PRESET", None)
        else:
            os.environ["ROAM_MCP_PRESET"] = old_preset

    missing: dict[str, list[str]] = {}
    for tool_name, meta in mcp._TOOL_METADATA.items():
        absent = [f for f in _REQUIRED_FLAGS if f not in meta or not isinstance(meta[f], bool)]
        if absent:
            missing[tool_name] = absent

    assert not missing, (
        f"{len(missing)} tool(s) in _TOOL_METADATA are missing side-effect flag(s):\n"
        + "\n".join(f"  {t}: missing {flags}" for t, flags in sorted(missing.items()))
        + "\n\nEvery @_tool wrapper must declare read_only, destructive, and idempotent "
        "as bool kwargs so MCP clients and mode-gate enforcement can reason about "
        "tool safety without inspecting source code."
    )
