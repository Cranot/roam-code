"""Per-tool invocation telemetry on the MCP server.

The audit (04-agent-mcp-dx R3) flagged that without visibility into
which tools agents actually call, every other tuning recommendation
is unfalsifiable. The telemetry counters in
``roam.mcp_extras.concurrency`` track invocations grouped by outcome
(success / rate_limited / error) so we can answer:

* Which of the 137 tools are dead weight?
* Did adding ``roam_ask`` reduce Grep+Read fallback?
* Are agents getting rate-limited in real workflows?

Local-only — never phones home. Counters live in the MCP server
process and reset on restart.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


@pytest.fixture(autouse=True)
def _reset_counters():
    """Clear the per-test counter state to keep assertions deterministic."""
    from roam.mcp_extras.concurrency import _tool_invocations

    _tool_invocations.clear()
    yield
    _tool_invocations.clear()


def test_record_tool_outcome_increments_counter():
    from roam.mcp_extras.concurrency import (
        record_tool_outcome,
        tool_invocation_summary,
    )

    record_tool_outcome("roam_understand", "success")
    record_tool_outcome("roam_understand", "success")
    record_tool_outcome("roam_understand", "error")
    record_tool_outcome("roam_ask", "success")

    summary = tool_invocation_summary()
    assert summary["roam_understand"]["success"] == 2
    assert summary["roam_understand"]["error"] == 1
    assert summary["roam_ask"]["success"] == 1
    # Tools never invoked don't appear.
    assert "roam_mutate" not in summary


def test_tool_invocation_summary_is_alphabetised():
    from roam.mcp_extras.concurrency import (
        record_tool_outcome,
        tool_invocation_summary,
    )

    record_tool_outcome("zzz_last", "success")
    record_tool_outcome("aaa_first", "success")
    record_tool_outcome("mmm_middle", "success")

    keys = list(tool_invocation_summary().keys())
    assert keys == ["aaa_first", "mmm_middle", "zzz_last"], keys


def test_wrap_with_guard_records_success_on_normal_return():
    from roam.mcp_extras.concurrency import (
        tool_invocation_summary,
        wrap_with_guard,
    )

    def echo(x):
        return {"ok": True, "x": x}

    wrapped = wrap_with_guard("test_echo", echo)
    result = wrapped(42)
    assert result == {"ok": True, "x": 42}

    summary = tool_invocation_summary()
    assert summary["test_echo"]["success"] == 1
    assert summary["test_echo"].get("error", 0) == 0


@pytest.mark.parametrize("is_async", [False, True])
def test_wrap_with_guard_preserves_synthesised_runtime_annotations(is_async):
    """FastMCP must see one type hint for every synthesised parameter.

    Python 3.14's ``functools.wraps`` no longer copies a runtime-assigned
    ``__annotations__`` mapping.  This shape mirrors the alias-normalisation
    wrapper that sits immediately inside the concurrency guard.
    """
    from roam.mcp_extras.concurrency import wrap_with_guard

    if is_async:

        async def tool(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

    else:

        def tool(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

    tool.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "symbol",
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default="",
                annotation=str,
            ),
            inspect.Parameter(
                "name",
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=str,
            ),
        ],
        return_annotation=dict,
    )
    tool.__annotations__ = {"symbol": str, "name": str, "return": dict}

    wrapped = wrap_with_guard("test_synthesised_annotations", tool)

    assert inspect.signature(wrapped) == tool.__signature__
    assert wrapped.__annotations__ == tool.__annotations__
    pydantic = pytest.importorskip("pydantic", reason="FastMCP schema generation requires pydantic")
    schema = pydantic.TypeAdapter(wrapped).json_schema()
    assert set(schema["properties"]) == {"symbol", "name"}


def test_wrap_with_guard_records_error_on_exception():
    from roam.mcp_extras.concurrency import (
        tool_invocation_summary,
        wrap_with_guard,
    )

    def boom():
        raise RuntimeError("intended")

    wrapped = wrap_with_guard("test_boom", boom)
    with pytest.raises(RuntimeError):
        wrapped()

    summary = tool_invocation_summary()
    assert summary["test_boom"]["error"] == 1
    assert summary["test_boom"].get("success", 0) == 0


def test_wrap_with_guard_records_async_expected_error():
    from roam.mcp_extras.concurrency import (
        tool_invocation_summary,
        wrap_with_guard,
    )

    async def boom():
        raise RuntimeError("intended")

    wrapped = wrap_with_guard("test_async_boom", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(wrapped())

    summary = tool_invocation_summary()
    assert summary["test_async_boom"]["error"] == 1
    assert summary["test_async_boom"].get("success", 0) == 0


def test_wrap_with_guard_does_not_record_unexpected_attribute_error():
    from roam.mcp_extras.concurrency import (
        tool_invocation_summary,
        wrap_with_guard,
    )

    def boom():
        raise AttributeError("programmer bug")

    wrapped = wrap_with_guard("test_attr_bug", boom)
    with pytest.raises(AttributeError):
        wrapped()

    assert "test_attr_bug" not in tool_invocation_summary()


def test_metrics_snapshot_includes_invocations():
    from roam.mcp_extras.concurrency import metrics, record_tool_outcome

    record_tool_outcome("roam_health", "success")
    snapshot = metrics()
    assert "invocations" in snapshot
    # Keys are JSON-safe ``"tool::outcome"`` strings (the raw counter
    # uses tuples but ``metrics()`` flattens for serialisation).
    assert snapshot["invocations"]["roam_health::success"] == 1
    # Also includes the existing concurrency telemetry fields.
    assert "max_concurrent" in snapshot
    assert "in_flight" in snapshot


def test_session_metrics_mcp_tool_returns_envelope():
    """``roam_session_metrics`` MCP tool wraps the counter snapshot in
    a standard envelope with a verdict line.
    """
    from roam.mcp_extras.concurrency import record_tool_outcome
    from roam.mcp_server import roam_session_metrics

    record_tool_outcome("roam_understand", "success")
    record_tool_outcome("roam_ask", "success")
    record_tool_outcome("roam_mutate", "error")

    result = roam_session_metrics()
    summary = result["summary"]
    assert summary["distinct_tools"] == 3
    assert summary["total_calls"] == 3
    assert summary["error_count"] == 1
    assert "invocations" in result
    assert result["invocations"]["roam_ask"]["success"] == 1
