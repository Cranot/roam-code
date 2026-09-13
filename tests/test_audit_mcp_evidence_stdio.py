"""Observe audit partial evidence over MCP stdio and handle retrieval."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests import test_w1448_health_list_cap_disclosure as cap_fixture
from tests.conftest import git_init, index_in_process


@pytest.mark.parametrize("storage_available", [True, False])
def test_audit_partial_state_survives_stdio_handle_and_fetch(tmp_path, monkeypatch, storage_available):
    # Intentional integration I/O: a real child process, Git/index fixture, and
    # tmp_path storage exercise serialization and delivery failures that mocked
    # transports cannot establish. All writable data belongs to this fixture.
    (tmp_path / ".gitignore").write_text(".roam/\n*.log\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    if not storage_available:
        (tmp_path / ".roam").mkdir()
        (tmp_path / ".roam" / "responses").write_text("preserve this file", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Host Roam configuration must not change indexing, presets, or evidence.
    for key in tuple(os.environ):
        if not key.startswith("ROAM_"):
            continue
        monkeypatch.delenv(key, raising=False)
    git_init(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    god_population, _ = cap_fixture._seed_graph_metrics(tmp_path)

    async def observe():
        env = dict(os.environ, ROAM_MCP_PRESET="full", ROAM_MCP_WATCH="0", ROAM_MCP_HANDLE_KB="1")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "roam", "mcp", "--no-auto-index"], cwd=tmp_path, env=env
        )
        with (tmp_path / "server.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session:
                    await session.initialize()
                    result = await session.call_tool("roam_audit", {"brief": True, "root": str(tmp_path)})
                    assert not result.isError, result
                    payload = json.loads(next(item.text for item in result.content if item.type == "text"))
                    if not storage_available:
                        assert payload["isError"] is True
                        assert payload["summary"]["state"] == "response_storage_unavailable"
                        assert payload["summary"]["partial_success"] is True
                        assert payload["retryable"] is False
                        assert "handle" not in payload and "fetch_with" not in payload
                        assert (tmp_path / ".roam" / "responses").read_text(encoding="utf-8") == "preserve this file"
                        return
                    assert payload["is_handle"] is True
                    assert payload["summary"]["partial_success"] is True
                    fetched = await session.call_tool(
                        "roam_fetch_handle", {"handle": payload["handle"], "section": "summary"}
                    )
                    assert not fetched.isError, fetched
                    report = json.loads(next(item.text for item in fetched.content if item.type == "text"))
                    assert report["data"]["partial_success"] is True
                    assert report["data"]["health_score_scope"] == "legacy_top_candidates"
                    assert report["data"]["health_issue_collection_scope"] == "all_qualifying_indexed_symbols"
                    child = report["data"]["child_evidence"]["test_pyramid"]["summary"]
                    assert child["state"] == "no_test_files"
                    # Same real protocol session: the explicit MCP full-scope
                    # request must reach CLI classification and survive handles.
                    health_result = await session.call_tool("roam_health", {"all_issues": True, "root": str(tmp_path)})
                    assert not health_result.isError, health_result
                    health_payload = json.loads(
                        next(item.text for item in health_result.content if item.type == "text")
                    )
                    assert health_payload["is_handle"] is True
                    health_fetched = await session.call_tool(
                        "roam_fetch_handle", {"handle": health_payload["handle"], "section": "summary"}
                    )
                    health_report = json.loads(
                        next(item.text for item in health_fetched.content if item.type == "text")
                    )
                    health_summary = health_report["data"]
                    assert health_summary["issue_collection_scope"] == "all_qualifying_indexed_symbols"
                    assert health_summary["health_score_scope"] == "legacy_top_candidates"
                    assert health_summary["god_components"] == god_population
                    assert health_summary["partial_success"] is False

    # Wall-clock deadlines bound a stuck protocol; they are not performance
    # assertions. Keep a whole-session bound as well as the per-read timeout.
    asyncio.run(asyncio.wait_for(observe(), timeout=120))
