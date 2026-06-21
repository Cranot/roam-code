"""Regression: the roam_retrieve MCP wrapper must pass `--` before the task.

`retrieve`'s ``task`` argument is a ``nargs=-1`` positional, so a leading-dash
task ("-v trace the login flow") was parsed by Click as an unknown option,
yielding non-JSON help/error output and dropping the retrieval. The wrapper now
appends ``--`` after the options so the task is always treated as positional.

Sibling of tests/test_compile_trace_leading_dash.py, which covers the identical
fix in the compiler's trace probe.
"""

from __future__ import annotations

from unittest.mock import patch

from roam.mcp_server import retrieve_context


def test_retrieve_wrapper_passes_dashdash_before_task():
    captured: dict = {}

    def _fake_run_roam(args, root):
        captured["args"] = list(args)
        return {"summary": {"verdict": "ok"}, "candidates": []}

    task = "-v then trace the login flow"
    with patch("roam.mcp_server._run_roam", side_effect=_fake_run_roam):
        retrieve_context(task, root=".")

    args = captured["args"]
    assert args[0] == "retrieve"
    # The task is the final arg, immediately preceded by `--` so Click stops
    # option parsing before reaching the leading-dash task text.
    assert args[-1] == task
    assert args[-2] == "--"


def test_retrieve_wrapper_dashdash_follows_options():
    """`--` must come AFTER real options so they are still parsed."""
    captured: dict = {}

    def _fake_run_roam(args, root):
        captured["args"] = list(args)
        return {"summary": {"verdict": "ok"}, "candidates": []}

    task = "-x weird task"
    with patch("roam.mcp_server._run_roam", side_effect=_fake_run_roam):
        retrieve_context(task, budget=500, k=5, root=".")

    args = captured["args"]
    # Options appear before `--`; task is the lone positional after it.
    assert "--budget" in args
    assert args.index("--budget") < args.index("--")
    assert "--k" in args
    assert args.index("--k") < args.index("--")
    assert args[-2:] == ["--", task]
