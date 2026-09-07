"""Qualify Roam's product identity through the real MCP stdio handshake."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                result = await session.initialize()
                tools = await session.list_tools()
                return json.loads(result.model_dump_json()), [tool.name for tool in tools.tools]

    result, tools = asyncio.run(observe())
    assert result["serverInfo"]["name"] == "roam-code"
    assert result["serverInfo"]["version"] == __version__
    assert "roam_search_symbol" in tools
    assert "roam_expand_toolset" in tools
