"""v12.2 daemon scaffold — redacted.

This file ships the **shape** of the daemon: PID file, socket path,
lifecycle helpers, and a public ``acquire_lock_for_command`` entry
point. The full Phase-2 cmd-by-cmd migration lands in v13.0.

Daemon **optional** in v12.2 — running ``roam <cmd>`` without
``roam daemon start`` works exactly as before. Without a daemon, the
``acquire_lock_for_command`` context manager falls back to a
process-local ``LockMgr`` so the lock-mode contract still holds within
one process.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from roam.runtime.lockmgr import default_lockmgr


def _daemon_state_path() -> Path:
    return Path(".roam") / "daemon.json"


def _daemon_pid_path() -> Path:
    return Path(".roam") / "daemon.pid"


def _socket_path() -> Path:
    """Cross-platform socket / named-pipe path."""
    if sys.platform == "win32":
        import hashlib

        repo_id = hashlib.sha256(str(Path.cwd().resolve()).encode("utf-8")).hexdigest()[:12]
        return Path(rf"\\.\pipe\roam-daemon-{repo_id}")
    return Path(".roam") / "daemon.sock"


def daemon_state() -> dict | None:
    """Return the current daemon's state record, or ``None`` if not running."""
    p = _daemon_state_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def daemon_running() -> bool:
    """Best-effort check: PID file exists + process alive."""
    pid_path = _daemon_pid_path()
    if not pid_path.is_file():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if sys.platform == "win32":
        try:
            import time

            mtime = pid_path.stat().st_mtime
            return (time.time() - mtime) < 24 * 3600
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock_for_command(command_name: str, timeout: float = 30.0):
    """Context manager: acquire the daemon lock for *command_name*.

    With a running daemon, talks to its coordinator. Without, falls back
    to a process-local LockMgr (which still gives us deterministic
    re-entrancy semantics within one process).
    """
    from roam.runtime.lock_modes import lookup_mode

    mode = lookup_mode(command_name)
    return default_lockmgr().acquire(mode, timeout=timeout)


def status_summary() -> dict:
    """Quick status payload for ``roam doctor`` and future ``roam daemon status``."""
    return {
        "running": daemon_running(),
        "pid_file": str(_daemon_pid_path()),
        "socket": str(_socket_path()),
        "state": daemon_state(),
        "phase": "v12.2 scaffold (Phase 1 — daemon optional)",
    }
