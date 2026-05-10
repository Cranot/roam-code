"""MCP tool version stamps — agents detect schema drift via _TOOL_METADATA.

Audit A7: every registered tool carries a ``version`` string (semver).
When a tool's input/output schema changes — meaning agents holding
cached schemas may misbehave — the maintainer bumps the version.
``roam_catalog`` surfaces the current version so agents can detect
and refresh stale schema caches without re-enumerating the whole
137-tool surface.
"""

from __future__ import annotations


def test_every_tool_has_a_version_stamp():
    import roam.mcp_server as mcp

    missing = [
        name
        for name, meta in mcp._TOOL_METADATA.items()
        if not isinstance(meta.get("version"), str) or not meta.get("version")
    ]
    assert not missing, (
        f"{len(missing)} tool(s) lack a version stamp:\n  {missing}\n\n"
        f"_tool decorator now defaults to version='1.0.0'; this should "
        f"be impossible unless a tool registered without going through "
        f"@_tool. Investigate the registration path for the listed tools."
    )


def test_default_tool_version_is_semver():
    import re

    import roam.mcp_server as mcp

    semver = re.compile(r"^\d+\.\d+\.\d+$")
    bad = []
    for name, meta in mcp._TOOL_METADATA.items():
        v = meta.get("version", "")
        if not semver.match(v):
            bad.append((name, v))
    assert not bad, f"non-semver version on tool(s): {bad}"


def test_explicit_version_override_propagates_to_metadata():
    """Decorating a tool with version='2.1.0' must surface that exact
    string in ``_TOOL_METADATA`` so agents can branch on the version.
    """
    from unittest.mock import patch

    import roam.mcp_server as mcp

    # Simulate registering a tool with an explicit version. We use the
    # real decorator but feed it a unique fake name so we don't pollute
    # the registry across tests.
    fake_name = "roam_test_versioned_xyz"

    def fake_fn(root: str = ".") -> dict:  # pragma: no cover - dummy
        return {}

    # Force the tool past the preset filter for this test only.
    with patch.object(mcp, "_ACTIVE_TOOLS", set(mcp._ACTIVE_TOOLS) | {fake_name}):
        decorated = mcp._tool(fake_name, "test", version="2.1.0")(fake_fn)
        assert decorated is not None  # _tool returned the wrapped fn
        meta = mcp._TOOL_METADATA.get(fake_name)
        assert meta is not None, "decorator should have populated metadata"
        assert meta["version"] == "2.1.0"

    # Cleanup so the registry doesn't carry the fake into other tests.
    mcp._TOOL_METADATA.pop(fake_name, None)
    if fake_name in mcp._REGISTERED_TOOLS:
        mcp._REGISTERED_TOOLS.remove(fake_name)


def test_roam_catalog_envelope_carries_versions():
    """``roam_catalog`` output must include the version field per tool
    so agents inspecting the surface can branch on it.
    """
    from roam.mcp_server import roam_catalog

    result = roam_catalog()
    tools = result.get("tools") or result.get("summary", {}).get("tools")
    if tools is None:
        # Some envelope shapes nest tools deeper; pull from top-level.
        tools = result.get("tools", [])
    assert tools, "catalog should return at least one tool"
    # Every tool should have a version field.
    for t in tools[:10]:
        assert "version" in t, f"catalog tool entry missing version: {t.get('name')}"
        assert isinstance(t["version"], str)
