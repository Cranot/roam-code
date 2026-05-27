"""Every @_tool wrapper must declare side-effect metadata.

MCP-P2.1 (shipped 2026-05-18): each tool carries ``read_only`` /
``destructive`` / ``idempotent`` flags in ``_TOOL_METADATA`` so gateways
can reject calls whose declared effects exceed caller authority.
``_TOOL_METADATA`` is populated unconditionally by the ``@_tool``
decorator (audit A7 / R8) before the fastmcp-presence guard, so this
test runs even without fastmcp installed.

This file is the canonical regression for the rule. Earlier waves shipped
five near-identical copies of the same check (``test_mcp_side_effect_metadata``,
``test_tool_metadata_completeness``, ``test_tool_metadata_side_effect_flags``,
``test_tool_metadata_side_effects``, ``test_tool_side_effect_metadata``) —
they were consolidated here on 2026-05-26.
"""

from __future__ import annotations

import pytest
from roam.mcp_server import _REGISTERED_TOOLS, _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _problems(name: str) -> list[str]:
    meta = _TOOL_METADATA.get(name)
    if meta is None:
        return ["<no _TOOL_METADATA entry>"]
    out: list[str] = []
    for flag in _REQUIRED_FLAGS:
        if flag not in meta:
            out.append(f"{flag}=MISSING")
        elif not isinstance(meta[flag], bool):
            out.append(f"{flag}={meta[flag]!r} (expected bool, got {type(meta[flag]).__name__})")
    return out


def test_tool_metadata_registry_is_nonempty() -> None:
    """Sanity-guard: at least one @_tool wrapper must have loaded."""
    assert _TOOL_METADATA, "_TOOL_METADATA is empty — mcp_server import failed silently"


def test_all_registered_tools_have_metadata() -> None:
    """Bulk check — names every offender in one failure message."""
    bad = {t: _problems(t) for t in _REGISTERED_TOOLS if _problems(t)}
    assert not bad, (
        f"{len(bad)} tool(s) missing read_only/destructive/idempotent in _TOOL_METADATA:\n"
        + "\n".join(f"  {t}: {', '.join(issues)}" for t, issues in sorted(bad.items()))
        + "\n\nFix: ensure the @_tool decorator for each listed tool passes explicit "
        "read_only=<bool>, destructive=<bool>, and idempotent=<bool> kwargs "
        "(or relies on their boolean defaults — do NOT set them to None)."
    )


@pytest.mark.parametrize("tool_name", sorted(_REGISTERED_TOOLS))
def test_tool_has_side_effect_metadata(tool_name: str) -> None:
    """Per-tool check — pinpoints the exact failing tool by name."""
    issues = _problems(tool_name)
    assert not issues, (
        f"Tool {tool_name!r} is missing side-effect metadata: {', '.join(issues)}"
    )
