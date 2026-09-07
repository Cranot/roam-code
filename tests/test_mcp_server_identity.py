"""Qualify Roam's identity and tool calls through the real MCP stdio transport."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.conftest import git_init, index_in_process


def test_initialize_advertises_roam_package_not_framework_version(tmp_path):
    from roam import __version__

    async def observe():
        env = dict(os.environ, ROAM_MCP_PRESET="core", ROAM_MCP_WATCH="0")
        for key in ("ROAM_PROJECT_ROOT", "ROAM_DB_DIR"):
            env.pop(key, None)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "roam", "mcp", "--no-auto-index"],
            cwd=tmp_path,
            env=env,
        )
        # An SDK default captured at import time can point to pytest/Click's
        # in-memory stderr, which has no POSIX subprocess fileno. Keep a real,
        # task-local descriptor and retain server diagnostics on every platform.
        with (tmp_path / "mcp-stderr.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                    result = await session.initialize()
                    tools = await session.list_tools()
                    return json.loads(result.model_dump_json()), [tool.name for tool in tools.tools]

    result, tools = asyncio.run(observe())
    assert result["serverInfo"]["name"] == "roam-code"
    assert result["serverInfo"]["version"] == __version__
    assert "roam_search_symbol" in tools
    assert "roam_expand_toolset" in tools


def test_stdio_tool_calls_do_not_inherit_the_protocol_input(tmp_path, monkeypatch):
    """An explicit-root CLI child must finish while the MCP input stays open."""
    root = tmp_path / "repo space $literal 'quote' &"
    root.mkdir()
    (root / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (root / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    for key in ("ROAM_PROJECT_ROOT", "ROAM_DB_DIR", "ROAM_RUN_ID"):
        monkeypatch.delenv(key, raising=False)
    git_init(root)
    output, code = index_in_process(root)
    assert code == 0, output
    (root / "roam.py").write_text('print(\'{"command":"PROJECT_MODULE_EXECUTED"}\')\n', encoding="utf-8")

    async def observe():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "roam", "mcp", "--no-auto-index"],
            cwd=tmp_path,
            env=dict(os.environ, ROAM_MCP_PRESET="core", ROAM_MCP_WATCH="0"),
        )
        with (tmp_path / "mcp-tools-stderr.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                    await session.initialize()
                    observations = []
                    for query in ("answer", "absent_symbol_7fe302"):
                        result = await session.call_tool("roam_search_symbol", {"query": query, "root": str(root)})
                        assert not result.isError, result
                        payload = json.loads(next(item.text for item in result.content if item.type == "text"))
                        assert payload["command"] == "search"
                        assert not payload["summary"].get("partial_success", False), payload
                        observations.append(payload["results"])
                    followup = await session.call_tool("roam_expand_toolset", {})
                    assert not followup.isError, followup
                    assert followup.content
                    return observations

    found, missing = asyncio.run(observe())
    assert [item["name"] for item in found] == ["answer"]
    assert missing == []
