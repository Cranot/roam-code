"""Assert every @_tool wrapper in mcp_server.py has declared side-effect metadata.

Pins the MCP-P2.1 invariant: every entry in _REGISTERED_TOOLS must have a
corresponding _TOOL_METADATA record with all three side-effect axes declared:
  - read_only   (bool) — tool does not mutate persistent state
  - destructive (bool) — tool may delete/overwrite data
  - idempotent  (bool) — safe to retry without compounding side effects

A missing entry means a tool was registered via a path that bypassed @_tool,
or that a future refactor wiped the metadata dict without updating the registry.
"""

from __future__ import annotations

import pytest

from roam.mcp_server import _REGISTERED_TOOLS, _TOOL_METADATA

_REQUIRED_FLAGS = ("read_only", "destructive", "idempotent")


def test_every_registered_tool_has_metadata_entry() -> None:
    missing = [t for t in _REGISTERED_TOOLS if t not in _TOOL_METADATA]
    assert not missing, f"{len(missing)} registered tool(s) have no _TOOL_METADATA entry:\n" + "\n".join(
        f"  - {t}" for t in sorted(missing)
    )


@pytest.mark.parametrize("flag", _REQUIRED_FLAGS)
def test_every_tool_metadata_entry_has_flag(flag: str) -> None:
    missing = [name for name, meta in _TOOL_METADATA.items() if flag not in meta]
    assert not missing, f"{len(missing)} tool(s) are missing the '{flag}' flag in _TOOL_METADATA:\n" + "\n".join(
        f"  - {t}" for t in sorted(missing)
    )
