"""Fix F (Pattern 6) — response-volume auto-handle integration tests.

Eight CLI commands historically returned 50KB → 1.6MB JSON envelopes
that blew through the MCP wire cap with no auto-handle:
``capsule``, ``partition``, ``conventions``, ``verify-imports``,
``fingerprint``, ``api``, ``changelog``, ``simulate-departure``.

These tests verify two parts of the fix:

1. **Central interception** — the existing ``_wrap_with_handle_off``
   decorator is correctly applied to every problem tool (via the
   ``@_tool`` registration). A response that exceeds the configured
   threshold is replaced with a tiny handle envelope.

2. **MCP wrappers exist** for the four commands that previously had no
   MCP surface at all (``api``, ``conventions``, ``verify_imports``,
   ``changelog``). Without these, the auto-handle pattern can't catch
   their output — there's nothing for it to wrap.

Each test mocks ``_run_roam`` so we don't need a real index/git repo;
the focus is on the routing/wrapping, not on the underlying CLI logic.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

pytest.importorskip(
    "fastmcp",
    reason="MCP tool tests require fastmcp; mcp_server module won't import without it.",
)

# Force ``full`` preset BEFORE importing mcp_server so all tool wrappers
# (including the 4 new ones) get registered + handle-off wrapped at
# decoration time. ``core`` (default) preset would skip them.
os.environ.setdefault("ROAM_MCP_PRESET", "full")

from roam import mcp_server  # noqa: E402
from roam.mcp_server import (  # noqa: E402
    _handle_storage_dir,
    _maybe_handle_off,
    fetch_handle,
)


def _unwrap(fn):
    """FastMCP 2.x exposes ``FunctionTool`` objects; drill in via ``.fn``.

    Note: we deliberately do NOT chase ``__wrapped__`` here. Going
    through ``__wrapped__`` would strip the handle-off / concurrency-guard
    wrappers we explicitly want to exercise. Only the outermost
    FastMCP shell needs to be removed (so the function becomes callable
    sync); everything else must stay intact.
    """
    if hasattr(fn, "fn") and callable(getattr(fn, "fn", None)):
        return fn.fn
    return fn


@pytest.fixture(autouse=True)
def _isolate_handle_dir(tmp_path, monkeypatch):
    """Sandbox each test under its own cwd / handle dir.

    Explicitly resets ``ROAM_MCP_HANDLE_KB`` to 20 (the audit threshold)
    AND clears the per-process write counter so tests run cleanly
    regardless of what earlier tests did to global env / state.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "20")
    # Reset GC counter so amortised cleanup doesn't fire mid-test and
    # delete the file under test.
    try:
        from roam.mcp_server import _HANDLE_GC_WRITE_COUNTER

        _HANDLE_GC_WRITE_COUNTER["n"] = 0
    except Exception:
        pass
    yield
    shutil.rmtree(_handle_storage_dir(), ignore_errors=True)


def _large_payload(verdict: str, n_items: int = 800) -> dict:
    """Build a >20KB envelope shaped like a real roam JSON response."""
    return {
        "command": "stub",
        "summary": {"verdict": verdict, "state": "ok", "partial_success": False},
        "data": [{"i": i, "x": "y" * 40} for i in range(n_items)],
    }


# ---------------------------------------------------------------------------
# Central interception: ``_maybe_handle_off`` writes >threshold payloads to
# .roam/responses/<sha>.json and returns a tiny envelope.
# ---------------------------------------------------------------------------


def test_central_interception_creates_handle_envelope():
    """The central interception path produces a handle envelope of the
    documented shape (verdict, handle, byte_size, preview, fetch_with)."""
    payload = _large_payload("big")
    serialised_size = len(json.dumps(payload).encode("utf-8"))
    assert serialised_size > 20 * 1024, "fixture must exceed 20KB"

    env = _maybe_handle_off(payload, tool_name="roam_capsule_export")
    assert env["is_handle"] is True
    assert isinstance(env["handle"], str) and len(env["handle"]) == 16
    assert env["byte_size"] > 20 * 1024
    # Preview carries the summary so the agent doesn't need to fetch.
    assert env["preview"]["summary"]["verdict"] == "big"
    # The envelope itself should be tiny — well under 5KB.
    env_size = len(json.dumps(env).encode("utf-8"))
    assert env_size < 5 * 1024, f"handle envelope is {env_size} bytes, expected <5K"
    # ``fetch_with`` gives the agent a literal next-step command.
    assert env["fetch_with"].startswith("roam_fetch_handle(handle='")


def test_central_interception_passes_small_payloads_through(monkeypatch):
    """A response below the threshold must NOT be handle-off'd —
    otherwise normal small responses would all become handle envelopes."""
    small = {"summary": {"verdict": "tiny"}, "data": [1, 2, 3]}
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "50")
    env = _maybe_handle_off(small, tool_name="roam_capsule_export")
    assert "is_handle" not in env
    assert env["summary"]["verdict"] == "tiny"


def test_central_interception_roundtrip_via_fetch_handle():
    """After a payload is stored under a handle, the agent must be able
    to retrieve the full payload via ``roam_fetch_handle``. Uses
    ``section="data"`` so we don't blow the test envelope by retrieving
    the whole 20KB+ blob inline."""
    payload = _large_payload("trip")
    env = _maybe_handle_off(payload, tool_name="roam_capsule_export")
    handle = env["handle"]

    fn = _unwrap(fetch_handle)
    fetched = fn(handle=handle, section="data")
    assert fetched["summary"]["mode"] == "section"
    # Original payload had 800 items.
    assert len(fetched["data"]) == 800
    assert fetched["data"][0] == {"i": 0, "x": "y" * 40}


# ---------------------------------------------------------------------------
# Per-tool integration: each of the 8 problem tools, when fed a large
# response, ends up with a handle envelope on the wire.
# ---------------------------------------------------------------------------


# Tools registered as ``@_tool`` — the wrapper auto-applies handle-off.
# Format: (mcp_tool_name, module_attr_name, kwargs)
# Note: ``partition`` and ``simulate_departure`` are defined as
# ``roam_partition`` / ``roam_simulate_departure`` at the Python level
# (since they'd collide with stdlib or third-party names otherwise).
_BIG_TOOLS = [
    ("roam_capsule_export", "capsule_export", {}),
    ("roam_partition", "roam_partition", {"n_agents": 3}),
    ("roam_fingerprint", "fingerprint", {}),
    ("roam_simulate_departure", "roam_simulate_departure", {"developer": "alice"}),
    ("roam_api", "api", {}),
    ("roam_conventions", "conventions", {}),
    ("roam_verify_imports", "verify_imports", {}),
    ("roam_changelog", "changelog", {}),
]


@pytest.mark.parametrize("tool_name,fn_name,kwargs", _BIG_TOOLS, ids=[t[0] for t in _BIG_TOOLS])
def test_big_tool_returns_handle_envelope_when_response_exceeds_threshold(tool_name, fn_name, kwargs, monkeypatch):
    """For each of the 8 commands, monkeypatch ``_run_roam`` to return a
    >20KB envelope, invoke the MCP tool, and assert the response is a
    tiny handle envelope rather than the fat payload."""
    # Defensive: re-assert threshold inside the parametrized test in
    # case an earlier test left a stale env var. Belt-and-braces — the
    # autouse fixture should also have set this.
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "20")
    big = _large_payload(f"{tool_name} big payload")
    serialised_size = len(json.dumps(big).encode("utf-8"))
    assert serialised_size > 20 * 1024

    # Stub the CLI runner so we don't need an actual indexed project.
    def _fake_run(args, root="."):
        return big

    monkeypatch.setattr(mcp_server, "_run_roam", _fake_run)

    # Resolve the tool function from the module. With FastMCP 2.x the
    # @_tool decorator returns a ``FunctionTool`` object — _unwrap()
    # drills in to the underlying callable. The handle-off wrapper is
    # applied BEFORE the FunctionTool wrap (R8.E8 / Fix F change), so
    # calling the unwrapped function still passes through handle-off.
    tool_fn = getattr(mcp_server, fn_name)
    callable_fn = _unwrap(tool_fn)
    result = callable_fn(**kwargs)

    # The handle-off wrapper must have triggered.
    assert result.get("is_handle") is True, (
        f"{tool_name} did NOT auto-handle a {serialised_size}-byte response; "
        f"agent would get the full envelope on the wire."
    )
    # Verify the documented handle-envelope shape.
    assert isinstance(result["handle"], str) and len(result["handle"]) == 16
    assert result["byte_size"] >= 20 * 1024
    # Envelope itself must be tiny.
    env_bytes = len(json.dumps(result).encode("utf-8"))
    assert env_bytes < 5 * 1024, (
        f"{tool_name} handle envelope is {env_bytes} bytes — defeats the purpose of handle-off (must be <5KB)."
    )


# ---------------------------------------------------------------------------
# Confirm the four newly-added MCP tools are actually registered.
# ---------------------------------------------------------------------------


def test_new_mcp_wrappers_have_handle_off_wrappers():
    """The four CLI commands that previously had no MCP surface
    (``api``, ``conventions``, ``verify-imports``, ``changelog``) must
    now go through ``_wrap_with_handle_off`` so the central handle-off
    intercepts their large responses. The wrapper is applied BEFORE the
    preset filter so it works even in restricted presets (``core``)."""
    # Patch _run_roam to return a >threshold payload and verify that
    # calling each new tool returns a handle envelope. The wrapper would
    # only be present if @_tool ran successfully.
    big = _large_payload("wrapper check")

    def _fake_run(args, root="."):
        return big

    # Standalone monkeypatching since this test isn't parametrized.
    orig = mcp_server._run_roam
    try:
        mcp_server._run_roam = _fake_run
        for fn_name in ("api", "conventions", "verify_imports", "changelog"):
            tool = getattr(mcp_server, fn_name, None)
            assert tool is not None, f"mcp_server.{fn_name} missing"
            fn = _unwrap(tool)
            result = fn() if fn_name != "verify_imports" else fn(file="")
            assert result.get("is_handle") is True, (
                f"{fn_name} did not auto-handle a large response — handle-off wrapper not applied"
            )
    finally:
        mcp_server._run_roam = orig


def test_new_mcp_wrappers_are_callable():
    """Smoke: each new MCP tool function is importable and callable
    (after unwrapping the FastMCP FunctionTool shell)."""
    for fn_name in ("api", "conventions", "verify_imports", "changelog"):
        tool = getattr(mcp_server, fn_name, None)
        assert tool is not None, f"mcp_server.{fn_name} not exported"
        fn = _unwrap(tool)
        assert callable(fn), f"{fn_name} unwrap did not yield a callable"
