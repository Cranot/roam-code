"""Keep the MCP protocol stream separate from argument-only CLI children."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from roam import mcp_server
from roam.mcp_extras import progress


def test_cli_fallback_detaches_stdin():
    payload = {"command": "search", "summary": {"verdict": "0 matching symbols"}, "results": []}
    with patch.object(
        subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")
    ) as run:
        assert mcp_server._run_roam_subprocess(["search", "absent"], "/explicit-root") == payload
    assert run.call_args.kwargs.get("stdin") == subprocess.DEVNULL
    assert "input" not in run.call_args.kwargs


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_progress_children_receive_eof(asynchronous, monkeypatch, tmp_path):
    # Run a real child that consumes all stdin. Guard the launch so an unfixed
    # runner fails immediately instead of blocking on pytest/the host's input.
    monkeypatch.setattr(
        progress,
        "_roam_subprocess_cmd",
        lambda args: [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
    )
    if asynchronous:
        launch = asyncio.create_subprocess_exec

        async def guarded(*args, **kwargs):
            assert kwargs.get("stdin") == subprocess.DEVNULL
            return await launch(*args, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded)
        code, stdout, stderr = asyncio.run(progress.run_with_phase_progress([], cwd=str(tmp_path)))
    else:
        launch = subprocess.Popen

        def guarded(*args, **kwargs):
            assert kwargs.get("stdin") == subprocess.DEVNULL
            return launch(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", guarded)
        code, stdout, stderr = progress.run_with_phase_progress_sync([], cwd=str(tmp_path))
    assert code == 0
    assert stdout.strip() == "''"
    assert stderr == ""


# Far below the 10 s git timeout in changed_files and the 5 s ones in resolve:
# a git child blocked on the protocol pipe runs into those timeouts, so an
# answer inside this bound was not rescued by one. It bounds the tool call,
# never the server start.
_TOOL_CALL_BOUND_S = 5.0


def test_stdio_server_children_do_not_inherit_the_protocol_pipe(tmp_path, monkeypatch):
    # Intentional integration I/O: a real stdio server whose in-process tools
    # start real git children. On Windows a child that inherits the protocol
    # pipe while the transport reads it blocks at startup, so `roam_diff`
    # answered "diff unavailable: git_timeout" with 0 changed files.
    from datetime import timedelta

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from tests.conftest import git_init, index_in_process

    (tmp_path / ".gitignore").write_text(".roam/\n*.log\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    for key in tuple(os.environ):
        if key.startswith("ROAM_"):
            monkeypatch.delenv(key, raising=False)
    git_init(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    (tmp_path / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    async def call(session, tool, arguments):
        started = time.perf_counter()
        result = await asyncio.wait_for(session.call_tool(tool, arguments), _TOOL_CALL_BOUND_S)
        elapsed = time.perf_counter() - started
        assert not result.isError, result
        payload = json.loads(next(item.text for item in result.content if item.type == "text"))
        return payload, elapsed

    async def observe():
        env = dict(os.environ, ROAM_MCP_PRESET="full", ROAM_MCP_WATCH="0")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "roam", "mcp", "--no-auto-index"], cwd=tmp_path, env=env
        )
        with (tmp_path / "server.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as session:
                    await session.initialize()
                    # `git rev-parse` and `git status` feed the envelope's index_status.
                    found, _ = await call(session, "roam_search_symbol", {"query": "f"})
                    assert found["_meta"]["index_status"]["dirty_files"] == 1, found["_meta"]
                    # `git diff` feeds the changed-file set.
                    diff, elapsed = await call(session, "roam_diff", {})
                    assert diff["summary"]["changed_files"] == 1, (elapsed, diff["summary"])
                    assert "git_error" not in diff["summary"], diff["summary"]

    # Whole-session deadline: server start alone can take a minute on a slow
    # Windows host, so it is bounded here and not by _TOOL_CALL_BOUND_S.
    asyncio.run(asyncio.wait_for(observe(), timeout=240))


_SERVE_WITH_CHILD_PROBE = r"""
import subprocess, sys
from roam import mcp_server
from roam.cli import cli

def serve(*args, **kwargs):
    # A child started with no stdin= argument, as most of roam's children are.
    child = subprocess.run(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
        capture_output=True, text=True, timeout=20,
    )
    print("child:", child.stdout.strip(), flush=True)
    print("transport:", repr(sys.stdin.readline()), flush=True)

mcp_server.mcp.run = serve
cli.main(["mcp", "--no-auto-index"], standalone_mode=False)
"""


def test_stdio_server_start_hands_children_the_null_device(tmp_path):
    # The class guard, independent of any one call site: every child the
    # stdio server starts without its own stdin reads end-of-file at once,
    # while the transport still receives the protocol bytes.
    if mcp_server.mcp is None:
        pytest.skip("fastmcp is not installed")
    env = {key: value for key, value in os.environ.items() if not key.startswith("ROAM_")}
    server = subprocess.Popen(
        [sys.executable, "-c", _SERVE_WITH_CHILD_PROBE],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        server.stdin.write("protocol line\n")
        server.stdin.flush()
        # Read before closing stdin: an inherited pipe would stay open here.
        out = server.stdout.read()
        server.stdin.close()
        err = server.stderr.read()
        server.wait(timeout=60)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
    assert server.returncode == 0, err
    assert "child: ''" in out, (out, err)
    assert "transport: 'protocol line\\n'" in out, (out, err)


def test_stdin_isolation_leaves_a_replaced_stdin_alone(monkeypatch):
    import io

    from roam.mcp_extras.stdio_isolation import isolate_stdio_transport_stdin

    replaced = io.StringIO("")
    monkeypatch.setattr(sys, "stdin", replaced)
    assert isolate_stdio_transport_stdin() is False
    assert sys.stdin is replaced


def test_explicit_critique_diff_keeps_its_input_channel():
    diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    payload = {"command": "critique", "summary": {"verdict": "Review 1 files"}}
    with patch.object(
        subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")
    ) as run:
        result = inspect.unwrap(mcp_server.critique_patch)(diff, root="/explicit-root")
    assert result == payload
    assert run.call_args.kwargs["input"] == diff
    assert "stdin" not in run.call_args.kwargs
