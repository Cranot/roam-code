"""Tests for the chunked / projected ``roam_fetch_handle`` retrieval API.

Closes the "fetch_handle escapes itself" irony bug: when an original
tool response exceeded the 50KB threshold and was handle-off'd, calling
``roam_fetch_handle(handle=X)`` returned the FULL 1MB+ payload, blowing
the wire cap a second time. The fix gives ``fetch_handle`` three
retrieval modes plus a chunked default:

1. Default (no offset/limit/section/jq) returns first 20000 bytes with
   ``has_more`` and ``next_offset`` for pagination.
2. ``offset + limit`` — byte slice with pagination metadata.
3. ``section="key"`` — return one top-level key's value.
4. ``jq=".field[:5]"`` — jq-style projection (full ``jq`` lib if
   importable, otherwise a built-in subset).

Tests exercise all four modes plus error envelopes for bad args.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "fastmcp",
    reason="MCP tool tests require fastmcp; mcp_server module won't import without it.",
)

from roam.mcp_server import (
    _apply_jq_projection,
    _handle_storage_dir,
    fetch_handle,
)


def _unwrap(fn):
    """Strip the outermost FastMCP shell so tests can call the tool's
    underlying function synchronously.

    FastMCP 2.x wraps every registered tool in a ``FunctionTool`` object
    that is not callable — its underlying function is exposed on ``.fn``.
    We deliberately do NOT chase ``__wrapped__`` past that point: going
    further would strip the concurrency / handle-off wrappers we want
    to exercise.
    """
    if hasattr(fn, "fn") and callable(getattr(fn, "fn", None)):
        return fn.fn
    return fn


@pytest.fixture(autouse=True)
def _isolate_handle_dir(tmp_path, monkeypatch):
    """Each test gets its own cwd → its own ``.roam/responses/``.

    W478-followup-3: ``_handle_storage_dir()`` resolves to
    ``Path.cwd() / .roam / responses``; ``monkeypatch.chdir(tmp_path)``
    redirects every write under ``tmp_path``, which pytest tears down
    automatically. No defensive ``shutil.rmtree(..., ignore_errors=True)``
    swallow is needed (and the previous one masked any genuine leak
    outside ``tmp_path``).
    """
    monkeypatch.chdir(tmp_path)
    yield


def _store_payload(payload: dict) -> str:
    """Persist ``payload`` to the handle dir manually and return the
    16-hex handle.

    We bypass ``_maybe_handle_off`` here so test payloads can be of any
    size — the handle-off threshold is in KB-units and even an empty
    dict would have to pad to 1KB to trigger handle-off. The
    fetch_handle behaviour under test is independent of the writing
    path: it just needs a real ``.json`` file with a sha16 name.
    """
    import hashlib as _hashlib
    import json as _json

    blob = _json.dumps(payload, default=str)
    encoded = blob.encode("utf-8")
    sha = _hashlib.sha256(encoded).hexdigest()[:16]
    handle_dir = _handle_storage_dir()
    handle_dir.mkdir(parents=True, exist_ok=True)
    (handle_dir / f"{sha}.json").write_text(blob, encoding="utf-8")
    return sha


# ---------------------------------------------------------------------------
# Byte slice mode
# ---------------------------------------------------------------------------


def test_fetch_handle_byte_slice():
    """offset + limit returns a strict byte slice plus pagination info."""
    big = {"data": ["row-%04d" % i for i in range(500)]}  # ~5KB serialised
    h = _store_payload(big)

    fn = _unwrap(fetch_handle)
    r = fn(handle=h, offset=0, limit=100)

    assert r["command"] == "roam_fetch_handle"
    assert r["summary"]["mode"] == "byte_slice"
    assert r["summary"]["offset"] == 0
    assert r["summary"]["limit"] == 100
    assert r["summary"]["end"] == 100
    assert r["summary"]["total_size"] > 100
    assert r["summary"]["has_more"] is True
    assert r["summary"]["next_offset"] == 100
    assert isinstance(r["data"], str)
    assert len(r["data"].encode("utf-8")) == 100
    # The slice should start with the JSON object opening.
    assert r["data"].startswith("{")


def test_fetch_handle_byte_slice_pagination():
    """Chaining offset=next_offset advances correctly through the payload."""
    big = {"items": list(range(2000))}
    h = _store_payload(big)
    fn = _unwrap(fetch_handle)

    r1 = fn(handle=h, offset=0, limit=200)
    assert r1["summary"]["has_more"] is True
    next_off = r1["summary"]["next_offset"]

    r2 = fn(handle=h, offset=next_off, limit=200)
    assert r2["summary"]["offset"] == next_off
    # second slice should start immediately after the first ends
    assert r2["summary"]["offset"] == r1["summary"]["end"]


def test_fetch_handle_default_caps_at_20k():
    """No params → returns first 20000 bytes with has_more if larger."""
    # Build a payload > 20KB.
    big = {"rows": [{"i": i, "name": "x" * 50} for i in range(800)]}
    h = _store_payload(big)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h)
    assert r["summary"]["mode"] == "byte_slice"
    assert r["summary"]["offset"] == 0
    assert r["summary"]["limit"] == 20000
    assert r["summary"]["total_size"] > 20000
    assert r["summary"]["has_more"] is True
    assert r["summary"]["next_offset"] == 20000
    assert len(r["data"].encode("utf-8")) <= 20000


def test_fetch_handle_small_payload_returns_full():
    """If the stored payload fits inside the default limit, ``has_more``
    is False and ``parsed`` is populated for convenience. We need a
    payload bigger than the 1KB handle-off threshold but smaller than
    the 20KB default fetch limit."""
    # ~2KB payload: store via handle-off (threshold=1KB), retrieve full.
    medium = {"verdict": "med", "rows": [{"i": i} for i in range(100)]}
    h = _store_payload(medium)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h)
    assert r["summary"]["has_more"] is False
    assert r["summary"]["next_offset"] is None
    assert r.get("parsed", {}).get("verdict") == "med"


def test_fetch_handle_negative_offset_errors():
    h = _store_payload({"x": [1] * 500})
    fn = _unwrap(fetch_handle)
    r = fn(handle=h, offset=-1)
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_negative_limit_errors():
    h = _store_payload({"x": [1] * 500})
    fn = _unwrap(fetch_handle)
    r = fn(handle=h, limit=-5)
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


# ---------------------------------------------------------------------------
# Section pick mode
# ---------------------------------------------------------------------------


def test_fetch_handle_section_pick():
    """section= extracts one top-level key and reports siblings."""
    payload = {
        "summary": {"verdict": "ok"},
        "context": {"callers": ["a", "b", "c"], "callees": []},
        "other_big": ["x" * 100] * 50,
    }
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, section="context")
    assert r["summary"]["mode"] == "section"
    assert r["summary"]["section"] == "context"
    assert r["section"] == "context"
    assert "summary" in r["total_keys"]
    assert "context" in r["total_keys"]
    assert "other_big" in r["total_keys"]
    assert r["data"]["callers"] == ["a", "b", "c"]


def test_fetch_handle_section_missing_returns_error():
    payload = {"a": 1, "b": 2}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, section="nope")
    assert r.get("isError") is True
    assert r.get("error_code") == "NO_RESULTS"
    # The error envelope should list available keys for orientation.
    assert "a" in r.get("total_keys", []) or "a" in (r.get("hint") or "")


def test_fetch_handle_section_and_jq_conflict():
    payload = {"a": [1, 2, 3]}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)
    r = fn(handle=h, section="a", jq=".a")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


# ---------------------------------------------------------------------------
# JQ projection mode
# ---------------------------------------------------------------------------


def test_fetch_handle_jq_projection():
    """Simple ``.field.subfield`` works."""
    payload = {"context": {"callers": [{"name": "alpha"}, {"name": "beta"}]}}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, jq=".context.callers")
    assert r["summary"]["mode"] == "jq"
    assert r["data"] == [{"name": "alpha"}, {"name": "beta"}]


def test_fetch_handle_jq_array_index():
    """``.list[2]`` returns the third element."""
    payload = {"list": [10, 20, 30, 40, 50]}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, jq=".list[2]")
    assert r["data"] == 30


def test_fetch_handle_jq_array_slice():
    """``.list[:3]`` returns the first three elements (built-in subset)."""
    payload = {"list": list(range(20))}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, jq=".list[:3]")
    assert r["data"] == [0, 1, 2]


def test_fetch_handle_jq_identity():
    """``.`` returns the whole payload."""
    payload = {"verdict": "ok", "x": 1}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, jq=".")
    assert r["data"]["verdict"] == "ok"


def test_fetch_handle_jq_unsupported_falls_back():
    """Filters / pipes / functions not in the built-in subset return a
    clean error envelope (when full ``jq`` library isn't installed) or
    succeed (when it is). Either way, no crash."""
    payload = {"list": [1, 2, 3]}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    # Pipe operator — not in the built-in subset.
    r = fn(handle=h, jq=".list | map(. + 1)")
    try:
        import jq  # type: ignore[import-not-found]  # noqa: F401

        # If real jq is installed, the call should succeed.
        assert r.get("isError") is not True
    except ImportError:
        # Without jq, this MUST return a structured USAGE_ERROR — never crash.
        assert r.get("isError") is True
        assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_jq_bad_field_returns_error():
    """Selecting a non-existent field returns a clean error envelope."""
    payload = {"a": 1}
    h = _store_payload(payload)
    fn = _unwrap(fetch_handle)

    r = fn(handle=h, jq=".missing")
    # built-in subset reports unknown key as USAGE_ERROR; real jq returns
    # null. Accept either as "no crash + readable response".
    if r.get("isError"):
        assert r.get("error_code") == "USAGE_ERROR"
    else:
        assert r.get("data") in (None, "null")


# ---------------------------------------------------------------------------
# _apply_jq_projection unit tests — independent of fetch_handle plumbing
# ---------------------------------------------------------------------------


def test_apply_jq_projection_identity():
    r, err = _apply_jq_projection({"a": 1}, ".")
    assert err is None
    assert r == {"a": 1}


def test_apply_jq_projection_nested():
    r, err = _apply_jq_projection({"a": {"b": {"c": 42}}}, ".a.b.c")
    assert err is None
    assert r == 42


def test_apply_jq_projection_index_and_slice():
    payload = {"items": [10, 20, 30, 40, 50]}
    r, err = _apply_jq_projection(payload, ".items[1]")
    assert err is None
    assert r == 20

    r2, err2 = _apply_jq_projection(payload, ".items[1:4]")
    assert err2 is None
    assert r2 == [20, 30, 40]


def test_apply_jq_projection_negative_index():
    payload = {"items": [10, 20, 30]}
    r, err = _apply_jq_projection(payload, ".items[-1]")
    assert err is None
    assert r == 30


def test_apply_jq_projection_bad_expr_returns_error():
    # Doesn't start with '.' — invalid for the built-in subset.
    r, err = _apply_jq_projection({"a": 1}, "a")
    if err is None:
        # full jq accepts "a" only as a string literal — irrelevant for us
        return
    assert err is not None
    assert "must start with '.'" in err or "unsupported" in err


# ---------------------------------------------------------------------------
# Handle resolution / error paths
# ---------------------------------------------------------------------------


def test_fetch_handle_rejects_malformed_handle():
    fn = _unwrap(fetch_handle)
    r = fn(handle="not-hex")
    assert r.get("isError") is True
    assert r.get("error_code") == "USAGE_ERROR"


def test_fetch_handle_unknown_handle_no_results():
    fn = _unwrap(fetch_handle)
    r = fn(handle="0" * 16)
    assert r.get("isError") is True
    assert r.get("error_code") == "NO_RESULTS"
