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

import pytest

pytest.importorskip("fastmcp", reason="MCP tool tests require fastmcp; mcp_server module won't import without it.")

from roam.mcp_server import _HANDLE_GC_WRITE_COUNTER as _GC_COUNTER
from roam.mcp_server import (
    _gc_handle_dir,
    _handle_storage_dir,
    _maybe_handle_off,
    fetch_handle,
)


def _unwrap_tool(fn):
    """Strip the outermost FastMCP ``FunctionTool`` shell. We deliberately
    do NOT chase ``__wrapped__`` further — going past the FunctionTool
    boundary would strip the concurrency / handle-off wrappers that
    these tests need to exercise."""
    if hasattr(fn, "fn") and callable(getattr(fn, "fn", None)):
        return fn.fn
    return fn


@pytest.fixture(autouse=True)
def _isolate_handle_dir(tmp_path, monkeypatch):
    """Run every test from a tmp cwd so handles land under the tmp's
    ``.roam/responses/`` rather than the project's. Prevents test
    pollution and lets us assert on the handle dir cleanly.

    W478-followup-3: dropped the defensive
    ``shutil.rmtree(..., ignore_errors=True)`` swallow. The chdir
    already sandboxes every write under ``tmp_path`` (pytest tears it
    down for us); the rmtree was redundant *and* hid any genuine leak
    outside the sandbox by silently ignoring all errors.
    """
    monkeypatch.chdir(tmp_path)
    yield


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
    """Fix F: ``fetch_handle`` v2 is chunked-by-default — the legacy
    "return the whole payload" behaviour now requires either a
    ``section="key"`` pick or a ``jq=".data"`` projection. The
    round-trip is verified by retrieving a known section and confirming
    the payload contents survived the write/read cycle."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    payload = {"summary": {"verdict": "big"}, "data": list(range(2000))}
    h = _maybe_handle_off(payload, tool_name="roam_test")
    handle = h["handle"]

    fn = _unwrap_tool(fetch_handle)
    # Default mode returns first 20000 bytes plus pagination metadata.
    fetched = fn(handle=handle)
    assert fetched["summary"]["mode"] == "byte_slice"
    assert fetched["summary"]["total_size"] > 0
    # Use section pick to retrieve the original ``data`` field.
    data_only = fn(handle=handle, section="data")
    assert data_only["summary"]["mode"] == "section"
    assert len(data_only["data"]) == 2000
    # Confirm summary section round-trips too.
    summary_only = fn(handle=handle, section="summary")
    assert summary_only["data"]["verdict"] == "big"


def test_fetch_handle_rejects_malformed():
    fn = _unwrap_tool(fetch_handle)
    r = fn(handle="not-hex")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_rejects_too_short():
    fn = _unwrap_tool(fetch_handle)
    r = fn(handle="abc")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_unknown_returns_no_results():
    fn = _unwrap_tool(fetch_handle)
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


# ---------------------------------------------------------------------------
# GC — TTL + max-bytes eviction (R9 hardening)
#
# .roam/responses/ used to grow unbounded. Long-running MCP sessions that
# repeatedly hit roam_understand / roam_for_security_review accumulated
# files indefinitely (disk DoS + forensic trail of source excerpts). The
# fix: amortised LRU/TTL cleanup inside _maybe_handle_off, configurable
# via ROAM_MCP_HANDLE_TTL_HOURS and ROAM_MCP_HANDLE_MAX_BYTES.
# ---------------------------------------------------------------------------


import os as _os  # noqa: E402  -- below the imports above by design


def _seed_handle_file(handle_dir, name: str, size_bytes: int = 100, age_seconds: float = 0.0):
    """Helper: create a fake handle file under ``handle_dir`` with the
    given content size and (optionally) backdated mtime."""
    handle_dir.mkdir(parents=True, exist_ok=True)
    p = handle_dir / f"{name}.json"
    p.write_bytes(b"x" * size_bytes)
    if age_seconds > 0:
        import time as _t

        now = _t.time()
        _os.utime(p, (now - age_seconds, now - age_seconds))
    return p


def test_gc_ttl_evicts_old_files(monkeypatch, tmp_path):
    """TTL pass should delete files whose mtime is older than the TTL."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_TTL_HOURS", "1")  # 1 hour TTL
    monkeypatch.setenv("ROAM_MCP_HANDLE_MAX_BYTES", "0")  # disable size pass
    handle_dir = _handle_storage_dir()

    fresh = _seed_handle_file(handle_dir, "a" * 16, size_bytes=200, age_seconds=60)  # 1 min old
    old = _seed_handle_file(handle_dir, "b" * 16, size_bytes=200, age_seconds=2 * 3600)  # 2h old
    older = _seed_handle_file(handle_dir, "c" * 16, size_bytes=200, age_seconds=48 * 3600)  # 2d old

    _gc_handle_dir(handle_dir)

    assert fresh.exists(), "fresh file (under TTL) must survive"
    assert not old.exists(), "1-hour-old file must be evicted by 1h TTL"
    assert not older.exists(), "2-day-old file must be evicted by 1h TTL"


def test_gc_max_bytes_keeps_newest(monkeypatch, tmp_path):
    """Size pass should evict oldest-first until under the cap."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_TTL_HOURS", "0")  # disable TTL pass
    monkeypatch.setenv("ROAM_MCP_HANDLE_MAX_BYTES", "500")  # tiny cap
    handle_dir = _handle_storage_dir()

    # Three 300-byte files; total 900 > cap of 500.
    # Backdate by descending age so we know which order they evict in.
    oldest = _seed_handle_file(handle_dir, "a" * 16, size_bytes=300, age_seconds=10000)
    middle = _seed_handle_file(handle_dir, "b" * 16, size_bytes=300, age_seconds=5000)
    newest = _seed_handle_file(handle_dir, "c" * 16, size_bytes=300, age_seconds=100)

    _gc_handle_dir(handle_dir)

    # Evict oldest first; total 900 → drop 300 → 600 (still over) → drop 300 → 300 (under cap).
    assert not oldest.exists(), "oldest must be evicted first"
    assert not middle.exists(), "middle must be evicted next to fit under cap"
    assert newest.exists(), "newest must survive once under cap"


def test_gc_noop_below_thresholds(monkeypatch):
    """When TTL+size are both within bounds, GC must not delete anything."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_TTL_HOURS", "168")  # 7 days
    monkeypatch.setenv("ROAM_MCP_HANDLE_MAX_BYTES", str(10 * 1024 * 1024))  # 10MB
    handle_dir = _handle_storage_dir()

    files = [_seed_handle_file(handle_dir, c * 16, size_bytes=200, age_seconds=60) for c in ("a", "b", "c", "d", "e")]
    _gc_handle_dir(handle_dir)
    for f in files:
        assert f.exists(), "no file should be evicted when below all thresholds"


def test_gc_handles_race_deleted_file(monkeypatch, tmp_path, mocker=None):
    """If a file vanishes between listdir and stat (another process
    cleaning up concurrently), GC must not crash."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_TTL_HOURS", "1")
    monkeypatch.setenv("ROAM_MCP_HANDLE_MAX_BYTES", "0")
    handle_dir = _handle_storage_dir()

    # Two real old files + one path that won't exist by stat-time.
    real = _seed_handle_file(handle_dir, "a" * 16, size_bytes=200, age_seconds=2 * 3600)
    _seed_handle_file(handle_dir, "b" * 16, size_bytes=200, age_seconds=2 * 3600)

    # Patch Path.stat to raise FileNotFoundError for "b"*16.json — emulates
    # a race where another writer/cleaner unlinked between listdir + stat.
    from pathlib import Path as _Path

    real_stat = _Path.stat
    target_basename = ("b" * 16) + ".json"

    def _flaky_stat(self, *args, **kw):
        if str(self).endswith(target_basename):
            raise FileNotFoundError(str(self))
        return real_stat(self, *args, **kw)

    monkeypatch.setattr(_Path, "stat", _flaky_stat)

    # Must not raise.
    _gc_handle_dir(handle_dir)

    # The non-flaky old file should still have been evicted (its stat works).
    assert not real.exists()


def test_gc_tolerates_missing_dir(tmp_path):
    """If the handle dir doesn't exist, GC is a silent no-op."""
    missing = tmp_path / "nonexistent" / "responses"
    # Must not raise even though the dir is missing.
    _gc_handle_dir(missing)
    assert not missing.exists()


def test_gc_tolerates_non_directory(tmp_path):
    """If the handle dir path points at a regular file, GC is a no-op."""
    not_a_dir = tmp_path / "responses"
    not_a_dir.write_text("oops, this is a file")
    # Must not raise; must not delete the file (it's outside our scope).
    _gc_handle_dir(not_a_dir)
    assert not_a_dir.is_file()


def test_gc_invalid_env_falls_back_to_defaults(monkeypatch):
    """Bad env values must not crash GC; it should silently use defaults."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_TTL_HOURS", "garbage")
    monkeypatch.setenv("ROAM_MCP_HANDLE_MAX_BYTES", "also-bad")
    handle_dir = _handle_storage_dir()
    _seed_handle_file(handle_dir, "a" * 16, size_bytes=200, age_seconds=60)
    # Must not raise.
    _gc_handle_dir(handle_dir)


def test_gc_runs_amortised_via_handle_off(monkeypatch):
    """Smoke test: writing many large payloads should trigger GC at least
    once, but not on every single call (amortisation)."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    monkeypatch.setenv("ROAM_MCP_HANDLE_TTL_HOURS", "1")
    monkeypatch.setenv("ROAM_MCP_HANDLE_MAX_BYTES", "0")

    # Reset counter so test is order-independent.
    _GC_COUNTER["n"] = 0

    # Plant an "old" file that GC should evict on the next run.
    handle_dir = _handle_storage_dir()
    stale = _seed_handle_file(handle_dir, "f" * 16, size_bytes=200, age_seconds=2 * 3600)

    # 30 distinct large payloads — distinct so each writes a new file.
    for i in range(30):
        _maybe_handle_off({"data": ["x" * 100] * 50, "i": i}, tool_name="roam_test")

    # After ≥ 25 writes the amortised GC should have fired and removed the stale file.
    assert not stale.exists(), "amortised GC should have evicted stale file"


def test_handle_dir_chmod_700_on_first_creation(tmp_path, monkeypatch):
    """First-creation of the handle dir should set mode 0o700 (owner-only)
    on POSIX systems. On Windows chmod is a near no-op so the test only
    asserts non-failure there."""
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    handle_dir = _handle_storage_dir()
    # Pre-condition: dir must not yet exist (the autouse fixture chdirs us
    # into a fresh tmp_path so this should hold).
    assert not handle_dir.exists()

    _maybe_handle_off({"data": ["x" * 100] * 50}, tool_name="roam_test")

    assert handle_dir.is_dir()
    if _os.name == "posix":
        mode = handle_dir.stat().st_mode & 0o777
        assert mode == 0o700, f"expected 0o700, got {oct(mode)}"
