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


# With roam 14.2.3 roam_doctor answered in 6-18 s over stdio on one Windows 11
# host with numpy 2.4.4 and scipy installed; with the stdio isolation call
# removed it gave no answer in 120 s.
_DOCTOR_CALL_BOUND_S = 60.0


def test_stdio_server_doctor_answers_with_scipy_installed(tmp_path, monkeypatch):
    # Intentional integration I/O. On one Windows 11 host, roam 14.2.2's
    # `roam mcp` gave no answer to a roam_doctor call over stdio once numpy and
    # scipy were installed; 14.2.3 answers. Measured with this fixture, an
    # indexed git project. In the one run in an empty folder, the call answered
    # with the isolation removed.
    import importlib.util
    from datetime import timedelta
    from pathlib import Path

    import roam

    if importlib.util.find_spec("scipy") is None:
        pytest.skip("scipy is not installed")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for key in tuple(os.environ):
        if key.startswith(("ROAM_", "GIT_")):
            monkeypatch.delenv(key, raising=False)
    # The children run in the project folder: point them at the roam under test,
    # not whichever roam their interpreter would find from there.
    roam_src = str(Path(roam.__file__).resolve().parents[1])
    # Every child, roam's own git calls included, reads no host git config.
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(filter(None, [roam_src, os.environ.get("PYTHONPATH")])),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    git_env = dict(
        env,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def run(argv, run_env, timeout):
        return subprocess.run(
            argv,
            cwd=project,
            env=run_env,
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )

    for args in (
        ["init", "-q"],
        ["add", "app.py"],
        ["-c", "commit.gpgsign=false", "-c", f"core.hooksPath={tmp_path / 'no-hooks'}", "commit", "-qm", "init"],
    ):
        run(["git", *args], git_env, 60)
    origin = run([sys.executable, "-c", "import roam; print(roam.__file__)"], env, 60).stdout.strip()
    assert Path(origin).resolve() == Path(roam.__file__).resolve(), origin
    try:
        run([sys.executable, "-c", "import numpy, scipy.linalg"], env, 120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"numpy/scipy do not load in a child interpreter: {exc}")
    run([sys.executable, "-m", "roam", "index"], env, 180)

    async def observe():
        server_env = dict(env, ROAM_MCP_PRESET="full", ROAM_MCP_WATCH="0")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "roam", "mcp", "--no-auto-index"], cwd=project, env=server_env
        )
        with (tmp_path / "server.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120)) as session:
                    await session.initialize()
                    result = await asyncio.wait_for(session.call_tool("roam_doctor", {}), _DOCTOR_CALL_BOUND_S)
                    assert not result.isError, result
                    payload = json.loads(result.content[0].text)
                    assert payload["command"] == "doctor", payload
                    checks = {check["name"]: check for check in payload["checks"]}
                    # The check that imports scipy ran and found it; the index is the fixture's.
                    assert "scipy=installed" in checks["Optional extras"]["detail"], checks["Optional extras"]
                    assert checks["Index exists"]["passed"], checks["Index exists"]
                    assert str(project.name) in checks["Index exists"]["detail"], checks["Index exists"]

    asyncio.run(asyncio.wait_for(observe(), timeout=240))


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


def test_index_freshness_git_children_detach_stdin(tmp_path):
    from roam.db import freshness

    calls = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 1, b"", b"")

    with patch.object(freshness.subprocess, "run", side_effect=fake_run):
        freshness._run_git(tmp_path, ["git", "status", "--porcelain"], freshness._CheckDeadline())
        freshness._run_git(tmp_path, ["git", "hash-object", "--stdin"], freshness._CheckDeadline(), data=b"x")
        freshness._git_history_relation(tmp_path, "a" * 40, "b" * 40, freshness._CheckDeadline())
    argument_only = [calls[0], *calls[2:]]
    assert argument_only and all(kw.get("stdin") == subprocess.DEVNULL for kw in argument_only)
    assert all(kw.get("input") is None for kw in argument_only)
    assert calls[1].get("input") == b"x" and "stdin" not in calls[1]
