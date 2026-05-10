"""Soft contract enforcement on destructive MCP tools.

Audit recommendation R5: agents calling ``roam_mutate`` for symbol X
should have called ``roam_simulate`` for X earlier in the session.
The check is *soft* — we never refuse the call, we just inject a
``contract_compliance`` block into the response so the agent can
self-correct on the next iteration.

These tests exercise the session-tracking primitives directly. The
end-to-end MCP flow is covered separately (``test_mcp_server.py``);
here we lock the session-state contract that the MCP wrappers rely on.
"""

from __future__ import annotations


class _FakeContext:
    """Minimal Context stand-in for the FastMCP session API.

    Real FastMCP contexts expose ``set_state``/``get_state``/
    ``session_id``. The session module falls back to a process-local
    dict keyed by ``session_id`` when state isn't available, so we
    just provide the id and let the fallback path handle storage.
    """

    def __init__(self, sid: str = "test-session"):
        self.session_id = sid
        self.request_id = sid


def test_record_tool_call_persists_target():
    from roam.mcp_extras.session import (
        record_tool_call,
        reset_session,
        tools_called_for,
    )

    ctx = _FakeContext("rec1")
    reset_session(ctx)

    record_tool_call(ctx, "roam_simulate", target="UserSession")
    record_tool_call(ctx, "roam_understand", target=None)
    record_tool_call(ctx, "roam_simulate", target="OtherSymbol")

    assert "roam_simulate" in tools_called_for(ctx, "UserSession")
    assert "roam_simulate" in tools_called_for(ctx, "OtherSymbol")
    # Calling for "UserSession" should NOT include unrelated targets.
    assert "roam_understand" not in tools_called_for(ctx, "UserSession")
    # Session-wide aggregation pulls everything across targets.
    all_tools = tools_called_for(ctx, target=None)
    assert {"roam_simulate", "roam_understand"} <= all_tools


def test_contract_check_satisfied_when_prereq_present():
    from roam.mcp_extras.session import (
        contract_check,
        record_tool_call,
        reset_session,
    )

    ctx = _FakeContext("contract1")
    reset_session(ctx)
    # Agent did the right thing — simulate before mutate.
    record_tool_call(ctx, "roam_simulate", target="PaymentService")

    result = contract_check(
        ctx,
        current_tool="roam_mutate",
        target="PaymentService",
        prerequisites=("roam_simulate",),
    )
    assert result["prerequisites_satisfied"] == ["roam_simulate"]
    assert result["prerequisites_skipped"] == []
    assert result["advice"] == ""  # nothing to advise


def test_contract_check_warns_when_prereq_skipped():
    from roam.mcp_extras.session import contract_check, reset_session

    ctx = _FakeContext("contract2")
    reset_session(ctx)
    # No prior simulate for this target.

    result = contract_check(
        ctx,
        current_tool="roam_mutate",
        target="UserSession",
        prerequisites=("roam_simulate",),
    )
    assert "roam_simulate" in result["prerequisites_skipped"]
    assert result["prerequisites_satisfied"] == []
    advice = result["advice"]
    assert "roam_simulate" in advice
    assert "UserSession" in advice
    # Soft warning — message must explicitly say nothing's blocked, so
    # the agent doesn't think it failed and retry-loop forever.
    lower = advice.lower()
    assert "no action blocked" in lower or "soft warning only" in lower


def test_contract_check_target_scoped():
    """Calling simulate for symbol A should NOT satisfy mutate for symbol B."""
    from roam.mcp_extras.session import (
        contract_check,
        record_tool_call,
        reset_session,
    )

    ctx = _FakeContext("contract3")
    reset_session(ctx)
    record_tool_call(ctx, "roam_simulate", target="SymbolA")

    # Mutate target = SymbolB → simulate not satisfied.
    result = contract_check(
        ctx,
        current_tool="roam_mutate",
        target="SymbolB",
        prerequisites=("roam_simulate",),
    )
    assert "roam_simulate" in result["prerequisites_skipped"]


def test_contract_check_handles_no_target():
    """Tools without a meaningful target (ingest_trace, vuln_map) check
    the session-wide tool history rather than per-target.
    """
    from roam.mcp_extras.session import (
        contract_check,
        record_tool_call,
        reset_session,
    )

    ctx = _FakeContext("contract4")
    reset_session(ctx)
    record_tool_call(ctx, "roam_understand", target=None)

    result = contract_check(
        ctx,
        current_tool="roam_vuln_map",
        target=None,
        prerequisites=("roam_understand",),
    )
    assert result["prerequisites_satisfied"] == ["roam_understand"]
    assert result["prerequisites_skipped"] == []


def test_contract_check_no_context_returns_skipped():
    """When ctx is None (CLI tests, ad-hoc invocations), we treat all
    prereqs as skipped — the helper still returns a well-formed dict.
    """
    from roam.mcp_extras.session import contract_check

    result = contract_check(
        ctx=None,
        current_tool="roam_mutate",
        target="X",
        prerequisites=("roam_simulate",),
    )
    assert result["prerequisites_skipped"] == ["roam_simulate"]
    assert result["prerequisites_satisfied"] == []
