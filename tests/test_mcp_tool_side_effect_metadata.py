"""Every @_tool wrapper must declare side-effect metadata.

Asserts that _TOOL_METADATA contains a read_only, destructive, and
idempotent boolean for every registered MCP tool.  Fails with the
names of any tools that are missing or have a non-bool value.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _TOOL_METADATA

REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _audit() -> dict[str, list[str]]:
    """Return {tool_name: [missing-or-wrong flags]}."""
    bad: dict[str, list[str]] = {}
    for name, meta in _TOOL_METADATA.items():
        problems = [flag for flag in REQUIRED_FLAGS if not isinstance(meta.get(flag), bool)]
        if problems:
            bad[name] = problems
    return bad


def test_all_tools_have_side_effect_metadata() -> None:
    assert _TOOL_METADATA, "_TOOL_METADATA is empty — did mcp_server import fail?"
    bad = _audit()
    if bad:
        lines = [f"  {name}: missing/non-bool flags {flags}" for name, flags in sorted(bad.items())]
        pytest.fail(f"{len(bad)} tool(s) lack side-effect metadata:\n" + "\n".join(lines))
