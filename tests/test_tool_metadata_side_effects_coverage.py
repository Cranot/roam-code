"""Every @_tool wrapper must declare side-effect metadata.

Guards against any registration path that bypasses the @_tool decorator or a
future change that makes the three side-effect flags conditional.  The three
flags (read_only / destructive / idempotent) are MCP-receipt inputs and gateway
policy gates — missing entries are a security regression.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _missing_flags(meta: dict) -> list[str]:
    return [flag for flag in _REQUIRED_FLAGS if flag not in meta or not isinstance(meta[flag], bool)]


@pytest.mark.parametrize("tool_name", sorted(_TOOL_METADATA))
def test_tool_has_side_effect_metadata(tool_name: str) -> None:
    meta = _TOOL_METADATA[tool_name]
    missing = _missing_flags(meta)
    assert not missing, (
        f"Tool {tool_name!r} is missing side-effect flag(s): {missing}. "
        "Add read_only=, destructive=, idempotent= kwargs to its @_tool decorator."
    )
