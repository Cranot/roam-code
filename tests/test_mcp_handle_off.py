"""Tests for the R8.E8 large-response handle-off mechanism.

When an MCP tool returns a JSON envelope larger than
``ROAM_MCP_HANDLE_KB`` (default 50), the wrapper writes the payload
to ``.roam/responses/<sha16>.json`` and replaces the return value
with a small handle envelope. ``roam_fetch_handle`` retrieves the
full payload by handle.

These tests cover:
* threshold gating (small payloads pass through, large don't)
* threshold disable (``ROAM_MCP_HANDLE_KB=0`` always passes through)
* error envelopes are NEVER handle-off'd (agent needs structured error)
* fetch_handle round-trip works
* fetch_handle rejects malformed / unknown handles cleanly
* content-addressed: identical payloads reuse the same handle file
"""

from __future__ import annotations

import json
import shutil

import pytest

pytest.importorskip(
    "fastmcp", reason="MCP tool tests require fastmcp; mcp_server module won't import without it."
)

from roam.mcp_server import _handle_storage_dir, _maybe_handle_off, fetch_handle


@pytest.fixture(autouse=True)
def _isolate_handle_dir(tmp_path, monkeypatch):
    """Run every test from a tmp cwd so handles land under the tmp's
    ``.roam/responses/`` rather than the project's. Prevents test
    pollution and lets us assert on the handle dir cleanly."""
    monkeypatch.chdir(tmp_path)
    yield
    # Defensive cleanup (the chdir should sandbox us already).
    shutil.rmtree(_handle_storage_dir(), ignore_errors=True)


# ---------------------------------------------------------------------------
# threshold gating
# ---------------------------------------------------------------------------


def test_small_payload_passes_through(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "50")
    payload = {"summary": {"verdict": "tiny"}, "data": [1, 2, 3]}
    r = _maybe_handle_off(payload, tool_name="roam_test")
    # Should be returned by-reference (or at least lack is_handle).
    assert "is_handle" not in r
    assert r["summary"]["verdict"] == "tiny"


def test_large_payload_handled_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")  # 1KB threshold
    big_data = ["x" * 100] * 50  # ~5KB serialised
    payload = {"summary": {"verdict": "big"}, "data": big_data}
    r = _maybe_handle_off(payload, tool_name="roam_test")
    assert r["is_handle"] is True
    assert isinstance(r["handle"], str) and len(r["handle"]) == 16
    assert r["byte_size"] > 1024
    assert "preview" in r
    # preview should retain the summary (cheap orientation).
    assert r["preview"]["summary"]["verdict"] == "big"


def test_threshold_zero_disables_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    big = {"summary": {}, "data": ["x" * 1000] * 100}
    r = _maybe_handle_off(big, tool_name="roam_test")
    assert "is_handle" not in r


def test_invalid_threshold_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "garbage")
    # 1KB-ish payload, default 50KB threshold means pass-through.
    r = _maybe_handle_off({"a": "x" * 800}, tool_name="roam_test")
    assert "is_handle" not in r


# ---------------------------------------------------------------------------
# error envelopes are never handle-off'd
# ---------------------------------------------------------------------------


def test_error_envelope_passes_through_even_when_huge(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    err = {
        "isError": True,
        "error_code": "USAGE_ERROR",
        "error": "x" * 2000,  # > threshold
        "hint": "y" * 2000,
    }
    r = _maybe_handle_off(err, tool_name="roam_test")
    # error envelopes must pass through so the agent gets structured
    # error fields (retryable, error_code, hint, doc_link) intact.
    assert r is err or r.get("isError") is True
    assert "is_handle" not in r


def test_skipped_tools_are_passthrough(monkeypatch):
    """``roam_fetch_handle`` itself must not be handle-off'd
    (would loop), and the meta-tool is exempt by design."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    big = {"data": "x" * 5000}
    r = _maybe_handle_off(big, tool_name="roam_fetch_handle")
    assert "is_handle" not in r


def test_already_handle_envelope_not_double_wrapped(monkeypatch):
    """If a handle envelope somehow re-enters the wrapper, don't
    handle-off it again (would create an unbounded chain)."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    handle_env = {"is_handle": True, "handle": "x" * 16, "data": "y" * 5000}
    r = _maybe_handle_off(handle_env, tool_name="roam_other")
    assert r is handle_env or r.get("handle") == "x" * 16


# ---------------------------------------------------------------------------
# fetch_handle round-trip
# ---------------------------------------------------------------------------


def test_fetch_handle_roundtrip(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    payload = {"summary": {"verdict": "big"}, "data": list(range(2000))}
    h = _maybe_handle_off(payload, tool_name="roam_test")
    handle = h["handle"]

    # fetch_handle is wrapped (concurrency + handle-off + tool reg);
    # call the underlying impl through __wrapped__ so we don't trip
    # the asyncio guard inside the test.
    fn = fetch_handle.__wrapped__ if hasattr(fetch_handle, "__wrapped__") else fetch_handle
    fetched = fn(handle=handle)
    assert fetched["summary"]["verdict"] == "big"
    assert len(fetched["data"]) == 2000


def test_fetch_handle_rejects_malformed():
    fn = fetch_handle.__wrapped__ if hasattr(fetch_handle, "__wrapped__") else fetch_handle
    r = fn(handle="not-hex")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_rejects_too_short():
    fn = fetch_handle.__wrapped__ if hasattr(fetch_handle, "__wrapped__") else fetch_handle
    r = fn(handle="abc")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_unknown_returns_no_results():
    fn = fetch_handle.__wrapped__ if hasattr(fetch_handle, "__wrapped__") else fetch_handle
    r = fn(handle="0" * 16)
    assert r.get("isError") is True
    assert r.get("error_code") == "NO_RESULTS"


# ---------------------------------------------------------------------------
# content-addressing
# ---------------------------------------------------------------------------


def test_identical_payloads_share_handle(monkeypatch):
    """Two identical payloads should hash to the same handle and reuse
    the file on disk (not re-write)."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    payload = {"data": ["x" * 100] * 50}
    h1 = _maybe_handle_off(payload, tool_name="roam_test")
    h2 = _maybe_handle_off(payload, tool_name="roam_test")
    assert h1["handle"] == h2["handle"]


def test_different_payloads_different_handles(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    p1 = {"data": ["a" * 100] * 50}
    p2 = {"data": ["b" * 100] * 50}
    h1 = _maybe_handle_off(p1, tool_name="roam_test")
    h2 = _maybe_handle_off(p2, tool_name="roam_test")
    assert h1["handle"] != h2["handle"]
