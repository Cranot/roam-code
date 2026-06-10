"""Verify every @_tool wrapper has declared side-effect metadata.

MCP-P2.1 (shipped 2026-05-18): each tool must carry read_only/destructive/
idempotent flags in _TOOL_METADATA so gateways can enforce caller authority.
"""

from __future__ import annotations

from roam.mcp_server import _REGISTERED_TOOLS, _TOOL_METADATA

_REQUIRED_SIDE_EFFECT_FLAGS = {"read_only", "destructive", "idempotent"}


def test_all_registered_tools_have_side_effect_metadata() -> None:
    """Every @_tool wrapper must declare read_only, destructive, idempotent.

    Fails with a list of missing tools, naming exactly which flags are absent.
    """
    assert _TOOL_METADATA, "_TOOL_METADATA empty — mcp_server import failed"
    assert _REGISTERED_TOOLS, "_REGISTERED_TOOLS empty — no @_tool decorators found"

    offenders: dict[str, list[str]] = {}

    for tool_name in _REGISTERED_TOOLS:
        meta = _TOOL_METADATA.get(tool_name)

        if meta is None:
            offenders[tool_name] = ["<entire entry missing>"]
            continue

        missing = []
        for flag in _REQUIRED_SIDE_EFFECT_FLAGS:
            if flag not in meta:
                missing.append(f"{flag}=MISSING")
            elif not isinstance(meta[flag], bool):
                missing.append(f"{flag}={meta[flag]!r} (expected bool, got {type(meta[flag]).__name__})")

        if missing:
            offenders[tool_name] = missing

    assert not offenders, (
        f"{len(offenders)} tool(s) lack side-effect metadata:\n"
        + "\n".join(f"  {name}: {', '.join(issues)}" for name, issues in sorted(offenders.items()))
        + "\n\nFix: add read_only=<bool>, destructive=<bool>, idempotent=<bool> "
        "to the @_tool() decorator for each listed tool."
    )
