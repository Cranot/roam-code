"""Assert every @_tool wrapper in mcp_server.py has the three side-effect flags.

_TOOL_METADATA is populated unconditionally by the _tool decorator (before any
fastmcp presence check), so every registered tool must have read_only /
destructive / idempotent as booleans.  A missing or non-bool value means the
decorator was bypassed or the entry was mutated after decoration.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def test_tool_metadata_registry_is_non_empty():
    assert _TOOL_METADATA, "_TOOL_METADATA is empty — no @_tool wrappers registered"


@pytest.mark.parametrize("tool_name", list(_TOOL_METADATA))
def test_tool_has_side_effect_flags(tool_name: str):
    entry = _TOOL_METADATA[tool_name]
    missing = [f for f in _REQUIRED_FLAGS if f not in entry]
    assert not missing, f"Tool {tool_name!r} is missing side-effect metadata fields: {missing}"
    non_bool = [f for f in _REQUIRED_FLAGS if not isinstance(entry[f], bool)]
    assert not non_bool, f"Tool {tool_name!r} has non-bool side-effect flags: " + ", ".join(
        f"{f}={entry[f]!r}" for f in non_bool
    )
