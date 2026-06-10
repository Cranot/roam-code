"""Assert every @_tool wrapper has declared side-effect metadata.

Every entry in _REGISTERED_TOOLS must have a _TOOL_METADATA record with all
three side-effect flags (read_only / destructive / idempotent). The @_tool
decorator populates this at import time; a missing entry or missing key means
the MCP boundary security layer (_wrap_with_receipt) cannot surface accurate
declared_side_effects to gateway integrators.
"""

from __future__ import annotations

import importlib
import os

import pytest

REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _reload_full():
    """Return mcp_server loaded under preset=full so all wrappers register."""
    env_before = os.environ.get("ROAM_MCP_PRESET")
    os.environ["ROAM_MCP_PRESET"] = "full"
    try:
        import roam.mcp_server as mcp

        importlib.reload(mcp)
        return mcp
    finally:
        if env_before is None:
            os.environ.pop("ROAM_MCP_PRESET", None)
        else:
            os.environ["ROAM_MCP_PRESET"] = env_before


def test_every_registered_tool_has_side_effect_metadata():
    mcp = _reload_full()
    if mcp.FastMCP is None:
        pytest.skip("fastmcp not installed")

    missing_entry = [name for name in mcp._REGISTERED_TOOLS if name not in mcp._TOOL_METADATA]
    assert not missing_entry, (
        f"{len(missing_entry)} tool(s) in _REGISTERED_TOOLS lack a _TOOL_METADATA entry: {sorted(missing_entry)}"
    )

    missing_flags: dict[str, list[str]] = {}
    for name in mcp._REGISTERED_TOOLS:
        meta = mcp._TOOL_METADATA[name]
        absent = [f for f in REQUIRED_FLAGS if f not in meta]
        if absent:
            missing_flags[name] = absent

    assert not missing_flags, f"{len(missing_flags)} tool(s) are missing side-effect flag(s):\n" + "\n".join(
        f"  {name}: missing {flags}" for name, flags in sorted(missing_flags.items())
    )
