"""MCP capacity backpressure —.

The MCP server runs every roam tool as either a thread-pool subprocess
invocation or an async tool. Without a guard, a parallel batch of 10+
tool calls overwhelms the executor and FastMCP drops connections
mid-batch.

This module wraps every tool with a bounded semaphore so:

* Concurrency is capped at ``ROAM_MCP_MAX_CONCURRENT`` (default 8).
* Over-capacity calls return a structured ``RATE_LIMITED`` / ``BUSY``
  envelope with a retry hint instead of dropping the connection.
* Below-threshold calls pay zero overhead — non-blocking acquire is
  ~100 nanoseconds.

Per-tool overrides via ``ROAM_MCP_LIMITS`` (JSON) let heavy tools (like
``roam_retrieve`` and ``roam_taint_classify``) get tighter caps without
slowing down cheap oracle calls.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import threading
from collections import Counter
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MAX_CONCURRENT = 8

# Tools that benchmark slow enough to warrant tighter caps. Each entry
# is independent; per-tool semaphores stack on top of the global one.
_DEFAULT_PER_TOOL_LIMITS = {
    "roam_retrieve": 2,
    "roam_taint_classify": 2,
    "roam_eval_retrieve": 1,
    "roam_attest": 1,
    "roam_index": 1,
    "roam_reindex": 1,
}


def _read_int_env(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name)
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _per_tool_limits_from_env() -> dict[str, int]:
    raw = os.environ.get("ROAM_MCP_LIMITS")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in data.items():
        try:
            out[str(k)] = max(1, int(v))
        except (TypeError, ValueError):
            continue
    return out


_max_concurrent = _read_int_env("ROAM_MCP_MAX_CONCURRENT", _DEFAULT_MAX_CONCURRENT)
_global_sem = threading.BoundedSemaphore(_max_concurrent)
_per_tool_overrides = {**_DEFAULT_PER_TOOL_LIMITS, **_per_tool_limits_from_env()}
_per_tool_sems: dict[str, threading.BoundedSemaphore] = {
    name: threading.BoundedSemaphore(limit) for name, limit in _per_tool_overrides.items()
}

# Telemetry counters — useful for debugging and visible via /admin endpoints.
_in_flight = 0
_busy_responses = 0
_metrics_lock = threading.Lock()

# Per-tool invocation counters — local-only telemetry. Tracks how many
# times each tool was called and how many returned each outcome
# (success / rate_limited / error). Helps answer "is roam_ask actually
# being used now that it's wired?" and "are 90 of the 227 tools dead
# weight?" without phoning home.
_tool_invocations: Counter[tuple[str, str]] = Counter()

# Tool-body exceptions are converted into structured envelopes by the
# inner MCP exception wrapper before this guard sees them. These are the
# expected wrapper-boundary failures worth counting before re-raising.
_EXPECTED_GUARD_ERRORS = (RuntimeError, ValueError, OSError)


def record_tool_outcome(tool_name: str, outcome: str) -> None:
    """Increment the invocation counter for ``(tool_name, outcome)``.

    ``outcome`` is one of ``"success"`` / ``"rate_limited"`` / ``"error"``.
    Local-only — never phones home. Inspect via ``metrics()`` or the
    new ``roam_session_metrics`` MCP tool.
    """
    with _metrics_lock:
        _tool_invocations[(tool_name, outcome)] += 1


def tool_invocation_summary() -> dict[str, dict[str, int]]:
    """Return per-tool invocation counts grouped by outcome.

    Shape::

        {
          "roam_ask":      {"success": 12, "rate_limited": 0, "error": 1},
          "roam_understand": {"success": 3, ...},
          ...
        }

    Tools never invoked don't appear. Sorted alphabetically for
    deterministic output across snapshots.
    """
    with _metrics_lock:
        snapshot = dict(_tool_invocations)
    out: dict[str, dict[str, int]] = {}
    for (name, outcome), count in snapshot.items():
        out.setdefault(name, {})[outcome] = count
    return dict(sorted(out.items()))


def metrics() -> dict:
    """Return a snapshot of current backpressure state.

    The ``invocations`` field is JSON-safe: tuple keys ``(tool, outcome)``
    are flattened into ``"tool::outcome"`` strings. Use
    :func:`tool_invocation_summary` for the grouped-by-tool view.
    """
    with _metrics_lock:
        flat_invocations = {f"{tool}::{outcome}": count for (tool, outcome), count in _tool_invocations.items()}
        return {
            "max_concurrent": _max_concurrent,
            "in_flight": _in_flight,
            "busy_responses_total": _busy_responses,
            "per_tool_limits": dict(_per_tool_overrides),
            "invocations": flat_invocations,
        }


# ---------------------------------------------------------------------------
# Acquire / release primitives
# ---------------------------------------------------------------------------


def _try_acquire(name: str) -> tuple[bool, threading.BoundedSemaphore | None]:
    """Try non-blocking acquire on global + per-tool semaphores.

    Returns ``(True, sem)`` on success — the caller MUST release ``sem``
    plus ``_global_sem`` later. Returns ``(False, None)`` when over
    capacity, with no semaphores held.
    """
    if not _global_sem.acquire(blocking=False):
        return False, None
    per_tool = _per_tool_sems.get(name)
    if per_tool is not None:
        if not per_tool.acquire(blocking=False):
            _global_sem.release()
            return False, None
    return True, per_tool


def _release(per_tool: threading.BoundedSemaphore | None) -> None:
    # A ValueError from BoundedSemaphore.release() is the expected
    # idempotency signal — a double-release past the initial value. The
    # _release contract is deliberately idempotent so a caller that
    # releases twice (or after a busy_envelope short-circuit) is safe.
    # Not a fallback; no lineage needed.
    if per_tool is not None:
        try:
            per_tool.release()
        except ValueError:
            pass
    try:
        _global_sem.release()
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Guard wrapper used by the _tool decorator
# ---------------------------------------------------------------------------


def busy_envelope(name: str) -> dict:
    """Structured response when the server is over capacity.

    Returned in place of executing the tool — callers (agents) should
    branch on ``error_code == "RATE_LIMITED"`` and back off.

    Pattern-1 conformance (CLAUDE.md "canonical failure envelope"): the
    ``error`` / ``error_code`` / ``hint`` / ``retryable`` fields live at
    the TOP LEVEL alongside ``isError: true`` and the closed-enum
    ``status: "rate_limited"`` — not nested under ``summary``. ``summary``
    keeps a single-line ``verdict`` so a consumer reading only the
    verdict still gets the answer (LAW 6).
    """
    global _busy_responses
    with _metrics_lock:
        _busy_responses += 1
    per_tool_limit = _per_tool_overrides.get(name)
    limit_text = (
        f"{per_tool_limit} per-tool, {_max_concurrent} global"
        if per_tool_limit is not None
        else f"{_max_concurrent} global"
    )
    return {
        "command": name,
        "isError": True,
        "status": "rate_limited",
        "summary": {
            "verdict": f"BUSY: {name} declined — server at capacity ({limit_text})",
        },
        "error": "server at capacity",
        "error_code": "RATE_LIMITED",
        "hint": (
            f"retry in 100-500ms with exponential backoff. Current limit: {limit_text}. "
            f"Tune via ROAM_MCP_MAX_CONCURRENT or ROAM_MCP_LIMITS env vars."
        ),
        "retryable": True,
        "_meta": {
            "max_concurrent": _max_concurrent,
            "per_tool_limit": per_tool_limit,
        },
    }


@contextmanager
def _track_in_flight():
    global _in_flight
    with _metrics_lock:
        _in_flight += 1
    try:
        yield
    finally:
        with _metrics_lock:
            _in_flight -= 1


def wrap_with_guard(name: str, fn):
    """Wrap a tool callable with the backpressure guard.

    Detects sync vs async automatically. The fast path (capacity
    available) is a single non-blocking semaphore acquire — measured
    overhead is sub-microsecond. Over-capacity calls return the BUSY
    envelope without invoking ``fn`` at all.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            acquired, per_tool = _try_acquire(name)
            if not acquired:
                record_tool_outcome(name, "rate_limited")
                return busy_envelope(name)
            with _track_in_flight():
                try:
                    result = await fn(*args, **kwargs)
                    record_tool_outcome(name, "success")
                    return result
                except _EXPECTED_GUARD_ERRORS:
                    record_tool_outcome(name, "error")
                    raise
                finally:
                    _release(per_tool)

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        acquired, per_tool = _try_acquire(name)
        if not acquired:
            record_tool_outcome(name, "rate_limited")
            return busy_envelope(name)
        with _track_in_flight():
            try:
                result = fn(*args, **kwargs)
                record_tool_outcome(name, "success")
                return result
            except _EXPECTED_GUARD_ERRORS:
                record_tool_outcome(name, "error")
                raise
            finally:
                _release(per_tool)

    return sync_wrapper
