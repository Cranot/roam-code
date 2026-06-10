"""Assert every @_tool wrapper carries declared side-effect metadata.

MCP-P2.1 (shipped 2026-05-18): every tool must declare read_only /
destructive / idempotent so gateways can gate calls on declared authority.
The @_tool decorator always populates _TOOL_METADATA at decoration time,
but this test guards against any future bypass (hand-crafted entries,
partial refactors) or missing boolean values.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _missing(entry: dict) -> list[str]:
    return [f for f in _REQUIRED_FLAGS if f not in entry or not isinstance(entry[f], bool)]


def test_tool_metadata_registry_is_nonempty() -> None:
    assert _TOOL_METADATA, "_TOOL_METADATA is empty — did mcp_server.py fail to import?"


@pytest.mark.parametrize("tool_name,meta", list(_TOOL_METADATA.items()))
def test_tool_has_side_effect_flags(tool_name: str, meta: dict) -> None:
    bad = _missing(meta)
    assert not bad, (
        f"Tool '{tool_name}' is missing or has non-bool side-effect flag(s): "
        f"{bad}. Add read_only/destructive/idempotent kwargs to its @_tool decorator."
    )
