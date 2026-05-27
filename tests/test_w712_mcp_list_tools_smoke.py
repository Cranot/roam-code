"""W712: Success-path smoke coverage for ``roam mcp --list-tools``.

The existing ``test_list_tools_flag`` in ``test_mcp_server.py`` only
exercises the failure path (``mcp is None`` -> exit 1). W695 added the
``--card`` happy path; W712 closes the symmetric gap for
``--list-tools`` and ``--list-tools-json`` so a regression that turns
the success branch into a no-op or a crash gets caught.

These tests do NOT boot fastmcp; they assert on what the Click
command writes to stdout when the module-level ``mcp`` object is
present (mocked truthy) and the registered-tools registry is
populated at import time.

Anchored on LAW 4 concrete-noun terminals: ``tools``, ``presets``,
``lines``, ``entries``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

# Canonical core-preset tools used as anchors. Picked from the
# ``core`` preset definition in ``src/roam/mcp_server.py`` and
# stable across recent sprints (W695 + W712 timeframe). If any of
# these drops out of core the test fails loudly, which is the
# correct signal.
_CANONICAL_CORE_TOOLS = (
    "roam_ask",
    "roam_search_symbol",
    "roam_coupling",
    "roam_batch_search",
    "roam_dead_code",
)


# ---------------------------------------------------------------------------
# --list-tools (plain-text success path)
# ---------------------------------------------------------------------------


def test_list_tools_default_invocation_exits_zero():
    """``roam mcp --list-tools`` must exit 0 and emit a non-empty
    tool inventory when fastmcp is available.
    """
    from roam.mcp_server import _REGISTERED_TOOLS, mcp_cmd

    # The handler short-circuits on ``mcp is None``; everything else
    # is module-level state we don't need to monkey-patch.
    runner = CliRunner()
    with patch("roam.mcp_server.mcp", object()):
        result = runner.invoke(mcp_cmd, ["--list-tools"])

    assert result.exit_code == 0, result.output
    assert result.output.strip(), "--list-tools produced no output"
    # The header line names how many tools are registered.
    assert "tools registered" in result.output
    # And the registry actually populated something.
    assert len(_REGISTERED_TOOLS) > 0


def test_list_tools_lists_canonical_core_tools():
    """The success path must print the canonical core-preset tool
    names. If any of these go missing the surface contract broke.
    """
    from roam.mcp_server import mcp_cmd

    runner = CliRunner()
    with patch("roam.mcp_server.mcp", object()):
        result = runner.invoke(mcp_cmd, ["--list-tools"])

    assert result.exit_code == 0, result.output
    for tool_name in _CANONICAL_CORE_TOOLS:
        assert tool_name in result.output, (
            f"canonical core tool {tool_name!r} missing from --list-tools output. Sample:\n{result.output[:600]}"
        )


def test_list_tools_advertises_presets():
    """The trailing line must advertise the closed set of available
    presets so an agent can discover the ``ROAM_MCP_PRESET`` knob
    without reading source.
    """
    from roam.mcp_server import _PRESETS, mcp_cmd

    runner = CliRunner()
    with patch("roam.mcp_server.mcp", object()):
        result = runner.invoke(mcp_cmd, ["--list-tools"])

    assert result.exit_code == 0, result.output
    assert "available presets" in result.output
    # Every preset key from _PRESETS must appear on that line.
    for preset_name in _PRESETS:
        assert preset_name in result.output, f"preset {preset_name!r} not advertised in --list-tools output"


def test_list_tools_entries_are_sorted_unique():
    """The handler sorts ``_REGISTERED_TOOLS`` before printing. Both
    properties (sorted + unique) are part of the contract because
    agents diff this output across runs to detect tool-surface drift.
    """
    from roam.mcp_server import mcp_cmd

    runner = CliRunner()
    with patch("roam.mcp_server.mcp", object()):
        result = runner.invoke(mcp_cmd, ["--list-tools"])

    assert result.exit_code == 0, result.output

    # Each tool name lives on its own line, indented with two spaces.
    tool_lines = [
        line.strip()
        for line in result.output.splitlines()
        if line.startswith("  ") and line.strip().startswith("roam_")
    ]
    assert len(tool_lines) > 0, f"no tool entries parsed from output:\n{result.output[:600]}"
    assert tool_lines == sorted(tool_lines), "tool entries not sorted"
    assert len(tool_lines) == len(set(tool_lines)), "duplicate tool entries"


# ---------------------------------------------------------------------------
# --list-tools-json (structured success path)
# ---------------------------------------------------------------------------


def test_list_tools_json_emits_valid_envelope():
    """``--list-tools-json`` is the structured-output sibling of
    ``--list-tools``. The success path must emit valid JSON with the
    documented fields (``server``, ``preset``, ``tool_count``,
    ``tools``). This mirrors the existing
    ``test_list_tools_json_outputs_json`` but pins the envelope shape
    independently (W712).
    """
    from roam.mcp_server import mcp_cmd

    class _Ann:
        def model_dump(self, exclude_none=True):
            return {"readOnlyHint": True}

    class _Exec:
        def model_dump(self, exclude_none=True):
            return {"taskSupport": "optional"}

    class _Tool:
        def __init__(self, name):
            self.name = name
            self.title = name
            self.description = f"{name} description"
            self.annotations = _Ann()
            self.execution = _Exec()
            self.meta = {"taskSupport": "optional"}
            self.parameters = {"type": "object", "properties": {}}

    async def _fake_list_tools():
        # Out of order on purpose -- the handler must sort.
        return [_Tool("roam_zeta"), _Tool("roam_alpha"), _Tool("roam_mu")]

    runner = CliRunner()
    with patch("roam.mcp_server.mcp") as mock_mcp:
        mock_mcp.list_tools = _fake_list_tools
        result = runner.invoke(mcp_cmd, ["--list-tools-json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["server"] == "roam-code"
    assert data["tool_count"] == 3
    assert "preset" in data
    names = [t["name"] for t in data["tools"]]
    # Handler sorts by tool.name; verify the contract.
    assert names == sorted(names), f"tools not sorted: {names}"
    # Every tool entry carries the documented fields.
    for entry in data["tools"]:
        assert "name" in entry
        assert "description" in entry
        assert "inputSchema" in entry
