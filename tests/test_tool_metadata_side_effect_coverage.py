"""Every @_tool wrapper must declare the three side-effect flags.

``_TOOL_METADATA`` is populated unconditionally by the ``@_tool`` decorator
(before the FastMCP-presence check), so this test works in any environment.
The three required flags are: ``read_only``, ``destructive``, ``idempotent``.
"""

from __future__ import annotations

import importlib
import os

import pytest

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def test_every_tool_has_side_effect_metadata():
    """``_TOOL_METADATA`` for every registered tool must contain all three
    side-effect flags as booleans.  A missing flag means the MCP receipt
    ``declared_side_effects`` field is incomplete and gateway policy cannot
    correctly gate the call.
    """
    import roam.mcp_server as mcp

    # Reload under full preset so all tools are registered, not just core.
    old_preset = os.environ.get("ROAM_MCP_PRESET")
    os.environ["ROAM_MCP_PRESET"] = "full"
    try:
        mcp = importlib.reload(mcp)
    finally:
        if old_preset is None:
            os.environ.pop("ROAM_MCP_PRESET", None)
        else:
            os.environ["ROAM_MCP_PRESET"] = old_preset

    if not mcp._TOOL_METADATA:
        pytest.skip("_TOOL_METADATA is empty — FastMCP not installed or no tools registered")

    missing: dict[str, list[str]] = {}
    for tool_name, meta in mcp._TOOL_METADATA.items():
        absent = [f for f in _REQUIRED_FLAGS if f not in meta or not isinstance(meta[f], bool)]
        if absent:
            missing[tool_name] = absent

    assert not missing, (
        f"{len(missing)} tool(s) are missing side-effect metadata flags:\n"
        + "\n".join(f"  {name}: missing {flags}" for name, flags in sorted(missing.items()))
        + "\n\nAdd read_only=..., destructive=..., idempotent=... to the @_tool(...) call."
    )
