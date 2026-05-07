"""observability hook for swallowed exceptions.

The codebase has ~84 ``except Exception: pass`` blocks that hide real
failures (missing schema columns, optional dependencies absent, sqlite
busy timeouts, etc.). A blanket conversion to logging would either
spam users on the happy path or break tests. This module adds an
**opt-in** logger that surfaces those failures when
``ROAM_VERBOSE=1`` (or ``ROAM_OBSERVABILITY=1``) is set, and stays
silent otherwise.

Usage at a swallow site::

    try:
        risky()
    except Exception as exc:  # noqa: BLE001 — defensive
        log_swallowed("cmd_metrics:per_symbol", exc)
"""

from __future__ import annotations

import os
import sys
import threading

_lock = threading.Lock()
# Per-scope counter — a single noisy site can fire 1000× per call.
# We rate-limit each scope to 5 reports per process.
_PER_SCOPE_LIMIT = 5
_seen: dict[str, int] = {}


def _enabled() -> bool:
    return os.environ.get("ROAM_VERBOSE", "").strip() in {"1", "true", "yes"} or os.environ.get(
        "ROAM_OBSERVABILITY", ""
    ).strip() in {"1", "true", "yes"}


def log_swallowed(scope: str, exc: BaseException) -> None:
    """Surface a swallowed exception to stderr when verbose mode is on.

    ``scope`` is a short stable identifier so the user can ``grep`` the
    output for a known site. Rate-limited per scope to avoid floods.
    """
    if not _enabled():
        return
    with _lock:
        count = _seen.get(scope, 0) + 1
        _seen[scope] = count
        if count > _PER_SCOPE_LIMIT:
            return
        suffix = f" (silenced after {_PER_SCOPE_LIMIT})" if count == _PER_SCOPE_LIMIT else ""
        sys.stderr.write(f"roam[swallow] {scope}: {type(exc).__name__}: {exc}{suffix}\n")


def reset() -> None:
    """Test helper — drop the per-scope rate-limit counters."""
    with _lock:
        _seen.clear()
