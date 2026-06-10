"""Every @_tool wrapper must declare side-effect metadata.

Asserts that each entry in _TOOL_METADATA carries boolean read_only,
destructive, and idempotent flags — the three axes required by MCP-P2.1
(per-tool side-effect declarations).  A missing or non-bool flag means
the tool was registered without the full metadata shape.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


@pytest.mark.parametrize("tool_name", sorted(_TOOL_METADATA))
def test_tool_has_side_effect_flags(tool_name: str) -> None:
    meta = _TOOL_METADATA[tool_name]
    missing = [f for f in _REQUIRED_FLAGS if f not in meta]
    wrong_type = [f for f in _REQUIRED_FLAGS if f in meta and not isinstance(meta[f], bool)]
    assert not missing, f"Tool {tool_name!r} is missing side-effect flags: {missing}"
    assert not wrong_type, f"Tool {tool_name!r} has non-bool side-effect flags: " + ", ".join(
        f"{f}={meta[f]!r}" for f in wrong_type
    )
