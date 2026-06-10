"""Verify every @_tool wrapper declares side-effect metadata.

MCP-P2.1: Each tool must carry read_only/destructive/idempotent flags
in _TOOL_METADATA so gateways can enforce authority-based access control.
"""

from __future__ import annotations

from roam.mcp_server import _REGISTERED_TOOLS, _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def _missing_flags(tool_name: str) -> list[str]:
    """Return list of missing/invalid flags for a tool."""
    meta = _TOOL_METADATA.get(tool_name)
    if meta is None:
        return ["NO _TOOL_METADATA ENTRY"]

    issues = []
    for flag in _REQUIRED_FLAGS:
        if flag not in meta:
            issues.append(f"{flag}=MISSING")
        elif not isinstance(meta[flag], bool):
            issues.append(f"{flag}={type(meta[flag]).__name__} (expected bool)")
    return issues


def test_all_tools_declare_side_effect_metadata() -> None:
    """Every registered @_tool must have read_only/destructive/idempotent declared."""
    assert _REGISTERED_TOOLS, "_REGISTERED_TOOLS is empty — decorator failed to load"

    offenders = {t: _missing_flags(t) for t in _REGISTERED_TOOLS if _missing_flags(t)}
    assert not offenders, (
        f"\n{len(offenders)} tool(s) lack side-effect metadata in _TOOL_METADATA:\n"
        + "\n".join(f"  {name}: {', '.join(flags)}" for name, flags in sorted(offenders.items()))
        + "\n\nFix: Add @_tool(..., read_only=<bool>, destructive=<bool>, idempotent=<bool>)"
    )
