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
        except Exception:
            pass

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
        except Exception:
            pass

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
    # Move-to-front: drop existing, append.
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
        except Exception:
            pass
    sid = _get_session_id(ctx)
    if sid:
        _FALLBACK_STORE.pop(sid, None)


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
