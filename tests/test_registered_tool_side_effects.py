"""Every registered @_tool wrapper must carry side-effect metadata.

Source of truth is _REGISTERED_TOOLS (the list the @_tool decorator appends
to as each wrapper registers). For every registered name there must be a
_TOOL_METADATA entry declaring all three MCP-P2.1 flags (read_only /
destructive / idempotent). This catches a tool that registers but is missing
from _TOOL_METADATA entirely — a gap the metadata-only iteration cannot see.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _REGISTERED_TOOLS, _TOOL_METADATA

REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def test_registered_tools_have_side_effect_metadata() -> None:
    assert _REGISTERED_TOOLS, "_REGISTERED_TOOLS is empty — module failed to load tools"

    problems: dict[str, str] = {}
    for name in _REGISTERED_TOOLS:
        meta = _TOOL_METADATA.get(name)
        if meta is None:
            problems[name] = "no _TOOL_METADATA entry"
            continue
        missing = [f for f in REQUIRED_FLAGS if f not in meta]
        if missing:
            problems[name] = f"missing flag(s): {', '.join(missing)}"

    if problems:
        lines = "\n".join(f"  {n}: {why}" for n, why in sorted(problems.items()))
        pytest.fail(f"{len(problems)} registered tool(s) lack side-effect metadata:\n{lines}")
