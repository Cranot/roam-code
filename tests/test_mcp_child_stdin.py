"""Keep the MCP protocol stream separate from argument-only CLI children."""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
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
