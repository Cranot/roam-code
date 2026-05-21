"""Per-session memory for the MCP server.

The agent shouldn't have to thread ``recent_symbols`` through every
call. The server tracks symbols touched in this session and feeds
them back into ranking-aware tools (``retrieve``, ``context``,
``preflight``) automatically.

Storage strategy
----------------

We try two approaches in order:

1. ``Context.set_state`` / ``Context.get_state`` when available
   (FastMCP >= 2.x). This is per-session and disposed by the runtime
   when the client disconnects.
2. Fallback: an in-process ``dict`` keyed by ``ctx.session_id`` with
   a soft cap on size. Old sessions are pruned on touch.

A bare ``Context`` value of ``None`` (tools called without a Context
parameter, e.g. unit tests) is silently ignored.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from roam.observability import log_swallowed

# Per-process fallback store: {session_id: SessionState}
_FALLBACK_STORE: dict[str, dict[str, Any]] = {}
_MAX_SESSIONS = 256
_MAX_SYMBOLS_PER_SESSION = 24
_STATE_KEY = "roam_session_state"


def _get_session_id(ctx: Any) -> str | None:
    """Best-effort extraction of a stable session id from a Context."""
    if ctx is None:
        return None
    sid = getattr(ctx, "session_id", None)
    if sid:
        return str(sid)
    # Some FastMCP versions expose request-scoped ids only.
    rid = getattr(ctx, "request_id", None)
    return str(rid) if rid else None


def _get_state(ctx: Any) -> dict[str, Any] | None:
    """Pull the session state dict via Context.get_state, else fallback."""
    if ctx is None:
        return None

    getter = getattr(ctx, "get_state", None)
    if callable(getter):
        try:
            state = getter(_STATE_KEY)
            if isinstance(state, dict):
                return state
        except Exception as exc:  # noqa: BLE001 — Context API varies by FastMCP version; falls through to fallback store
            log_swallowed("session:_get_state.context_api", exc)

    sid = _get_session_id(ctx)
    if not sid:
        return None
    return _FALLBACK_STORE.get(sid)


def _set_state(ctx: Any, state: dict[str, Any]) -> None:
    """Persist state via Context.set_state, else fallback."""
    if ctx is None:
        return

    setter = getattr(ctx, "set_state", None)
    if callable(setter):
        try:
            setter(_STATE_KEY, state)
            return
        except Exception as exc:  # noqa: BLE001 — Context API varies by FastMCP version; falls through to fallback store
            log_swallowed("session:_set_state.context_api", exc)

    sid = _get_session_id(ctx)
    if not sid:
        return
    _FALLBACK_STORE[sid] = state
    # Prune old sessions to bound memory.
    if len(_FALLBACK_STORE) > _MAX_SESSIONS:
        oldest = sorted(
            _FALLBACK_STORE.items(),
            key=lambda kv: kv[1].get("last_touch", 0.0),
        )
        for stale_id, _ in oldest[: len(_FALLBACK_STORE) - _MAX_SESSIONS]:
            _FALLBACK_STORE.pop(stale_id, None)


def _empty_state() -> dict[str, Any]:
    return {
        "symbols": deque(maxlen=_MAX_SYMBOLS_PER_SESSION),
        "task_hint": "",
        "last_touch": time.time(),
        # Map: target_string -> set of tool names called for that target.
        # Used by ``contract_check`` to soft-enforce the agent contract on
        # destructive tools (e.g. roam_mutate must follow roam_simulate).
        "tool_calls": {},
    }


def remember_symbol(ctx: Any, symbol: str) -> None:
    """Record a symbol the agent just looked at.

    Most-recently-used wins: re-touching a symbol moves it to the front.
    """
    if not symbol or ctx is None:
        return
    state = _get_state(ctx)
    if state is None:
        state = _empty_state()
    syms = state.get("symbols")
    if not isinstance(syms, deque):
        syms = deque(syms or [], maxlen=_MAX_SYMBOLS_PER_SESSION)
        state["symbols"] = syms
    # Move-to-front: drop existing, append. A ValueError here is the
    # expected first-touch signal — the symbol isn't in the deque yet,
    # so there is nothing to move. Not a fallback; no lineage needed.
    try:
        syms.remove(symbol)
    except ValueError:
        pass
    syms.append(symbol)
    state["last_touch"] = time.time()
    _set_state(ctx, state)


def remember_task_hint(ctx: Any, hint: str) -> None:
    """Record a free-form task hint (e.g. ``task=`` arg from a tool)."""
    if not hint or ctx is None:
        return
    state = _get_state(ctx) or _empty_state()
    state["task_hint"] = hint
    state["last_touch"] = time.time()
    _set_state(ctx, state)


def recent_symbols(ctx: Any, limit: int = 6) -> list[str]:
    """Return the most-recent N symbols (newest first)."""
    state = _get_state(ctx)
    if state is None:
        return []
    syms = state.get("symbols", [])
    items = list(syms)[-limit:]
    items.reverse()
    return items


def session_hint(ctx: Any) -> str:
    """Return the cached task hint, or empty string."""
    state = _get_state(ctx)
    if state is None:
        return ""
    return str(state.get("task_hint", "") or "")


def reset_session(ctx: Any) -> None:
    """Drop session state. Mostly used for tests."""
    if ctx is None:
        return
    deleter = getattr(ctx, "delete_state", None)
    if callable(deleter):
        try:
            deleter(_STATE_KEY)
        except Exception as exc:  # noqa: BLE001 — Context API varies by FastMCP version; fallback-store pop below still clears state
            log_swallowed("session:reset_session.context_api", exc)
    sid = _get_session_id(ctx)
    if sid:
        _FALLBACK_STORE.pop(sid, None)


def record_tool_call(ctx: Any, tool_name: str, target: str | None = None) -> None:
    """Record that ``tool_name`` was called, optionally for ``target``.

    Used by ``contract_check`` to soft-enforce the agent contract on
    destructive tools — e.g. ``roam_mutate`` should follow a
    ``roam_simulate`` for the same target. Calls without a target
    (whole-repo tools like ``roam_understand``) are recorded under the
    sentinel key ``""``.
    """
    if not tool_name or ctx is None:
        return
    state = _get_state(ctx)
    if state is None:
        state = _empty_state()
    calls = state.setdefault("tool_calls", {})
    if not isinstance(calls, dict):
        calls = {}
        state["tool_calls"] = calls
    key = (target or "").strip()
    bucket = calls.setdefault(key, set())
    if not isinstance(bucket, set):
        bucket = set(bucket or ())
        calls[key] = bucket
    bucket.add(tool_name)
    state["last_touch"] = time.time()
    _set_state(ctx, state)


def tools_called_for(ctx: Any, target: str | None = None) -> set[str]:
    """Return the set of tool names called against ``target`` this session.

    With ``target=None`` returns the union across every recorded target,
    including the no-target sentinel — useful for tools whose
    prerequisites aren't target-specific (``ingest_trace``, ``vuln_map``).
    """
    state = _get_state(ctx)
    if state is None:
        return set()
    calls = state.get("tool_calls", {})
    if not isinstance(calls, dict):
        return set()
    if target is not None:
        bucket = calls.get(target.strip(), set())
        return set(bucket) if not isinstance(bucket, set) else set(bucket)
    out: set[str] = set()
    for bucket in calls.values():
        if isinstance(bucket, set):
            out.update(bucket)
        elif isinstance(bucket, (list, tuple)):
            out.update(bucket)
    return out


def contract_check(
    ctx: Any,
    *,
    current_tool: str,
    target: str | None,
    prerequisites: tuple[str, ...],
) -> dict[str, Any]:
    """Build a soft-enforcement compliance dict for a destructive tool.

    The agent contract says "before ``roam_mutate`` for symbol X, call
    ``roam_simulate`` for X." This helper checks the session log and
    returns a dict suitable for embedding under ``contract_compliance``
    in the destructive tool's response. Soft enforcement only — we
    never refuse the call. A warning lets the agent self-correct on
    the next iteration without breaking workflows that legitimately
    skip a step (e.g. confirmed dead-code sweep, test fixture cleanup).

    Returns shape::

        {
            "current_tool": "roam_mutate",
            "target": "UserSession",
            "prerequisites_satisfied": ["roam_simulate"],
            "prerequisites_skipped":  [],   # any missing prereqs
            "advice": "..."                 # actionable hint when skipped
        }
    """
    target_norm = (target or "").strip() or None

    # Look at this target specifically; fall back to session-wide for
    # tools where target isn't a meaningful key.
    target_calls = tools_called_for(ctx, target_norm) if target_norm else tools_called_for(ctx, None)

    satisfied = [p for p in prerequisites if p in target_calls]
    skipped = [p for p in prerequisites if p not in target_calls]

    advice = ""
    if skipped:
        if target_norm:
            advice = (
                f"{current_tool!r} is destructive — recommended to call "
                f"{', '.join(repr(p) for p in skipped)} for {target_norm!r} "
                f"first to verify the change is safe. Soft warning only; "
                f"no action blocked."
            )
        else:
            advice = (
                f"{current_tool!r} is destructive — recommended to call "
                f"{', '.join(repr(p) for p in skipped)} earlier in this "
                f"session before applying. Soft warning only."
            )

    return {
        "current_tool": current_tool,
        "target": target_norm,
        "prerequisites_satisfied": satisfied,
        "prerequisites_skipped": skipped,
        "advice": advice,
    }


def merge_with_explicit(
    ctx: Any,
    *,
    explicit_recent: str = "",
    explicit_hint: str = "",
    limit: int = 6,
) -> tuple[str, str]:
    """Combine explicit args with session memory.

    Explicit values take precedence (the agent told us something
    specific); session memory fills in the gaps.

    Returns ``(session_hint, recent_symbols_csv)``.
    """
    hint = explicit_hint or session_hint(ctx)

    explicit_list = [s.strip() for s in (explicit_recent or "").split(",") if s.strip()]
    if explicit_list:
        # Don't re-add session memory if the agent gave us a list.
        recent_csv = ",".join(explicit_list[:limit])
        return hint, recent_csv

    recent_csv = ",".join(recent_symbols(ctx, limit=limit))
    return hint, recent_csv
