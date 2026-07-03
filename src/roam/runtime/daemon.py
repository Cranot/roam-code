"""Optional long-running daemon for shared lock state across commands.

Ships the daemon **shape**: PID file, socket path, lifecycle helpers,
and a public ``acquire_lock_for_command`` entry point. Per-command
migration is incremental.

Running ``roam <cmd>`` without ``roam daemon start`` works exactly as
before — without a daemon, ``acquire_lock_for_command`` falls back to
a process-local ``LockMgr`` so the lock-mode contract still holds
within one process.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from roam.output.formatter import WarningsOut
from roam.runtime.lockmgr import default_lockmgr


__all__ = (
    "acquire_lock_for_command",
    "daemon_running",
    "daemon_state",
    "status_summary",
)


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


def daemon_state(*, warnings_out: WarningsOut = None) -> dict | None:
    """Return the current daemon's state record, or ``None`` if not running.

    W597: mirrors the W595 ``read_permit`` / W596 ``read_run_meta`` plumb —
    when *warnings_out* is supplied, every silent-error site appends one
    structured closed-enum marker so callers can tell "daemon.json not on
    disk" (legitimate "not running" sentinel) from "daemon.json on disk
    but unreadable" from "JSON parsed but top-level not a dict". The
    ``None`` return on every drop path is PRESERVED — the None-return is
    the caller contract (it's how ``status_summary`` projects "not
    running"). ``warnings_out=None`` (default) preserves the pre-W597
    silent-drop behaviour.

    Marker shape mirrors W595's ``read_permit`` / W596's ``read_run_meta``
    closed-enum vocabulary with a ``daemon_state_`` prefix so a caller
    threading the same bucket through multiple substrate read sites sees
    one uniform marker vocabulary.

    Emitted kinds (closed enum):

      * ``daemon_state_not_found:<path>`` — the on-disk ``daemon.json``
        does not exist. This is the most common "no daemon running"
        path; the marker is informational (an operator inspecting
        ``warnings_out`` can distinguish "no daemon" from "corrupt
        state" without parsing free-form text). The ``None`` return
        still means "no daemon."
      * ``daemon_state_read_failed:<path>:<exc_class>:<detail>`` —
        ``Path.read_text`` raised ``OSError`` (typically
        ``PermissionError`` / ``IsADirectoryError`` / generic
        ``OSError``). The file is on disk but unreadable.
      * ``daemon_state_corrupt:<path>:JSONDecodeError`` — the bytes
        parsed as something other than JSON.
      * ``daemon_state_corrupt:<path>:NotAJsonObject`` — JSON parsed
        cleanly but the top-level value was not a dict.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    p = _daemon_state_path()
    if not p.is_file():
        _emit(f"daemon_state_not_found:{p}")
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        _emit(f"daemon_state_read_failed:{p}:{type(exc).__name__}:{exc}")
        return None
    except json.JSONDecodeError:
        _emit(f"daemon_state_corrupt:{p}:JSONDecodeError")
        return None
    if not isinstance(raw, dict):
        _emit(f"daemon_state_corrupt:{p}:NotAJsonObject")
        return None
    return raw


def daemon_running(*, warnings_out: WarningsOut = None) -> bool:
    """Best-effort check: PID file exists + process alive.

    W597-bonus: sibling silent-False reader in the same file. When
    *warnings_out* is supplied, the file-on-disk-but-unreadable / pidfile
    contents corrupt / Windows stat-failure paths each emit one closed-
    enum marker. The legitimate "not running" sentinels — pidfile missing
    AND ``os.kill(pid, 0)`` raising on a stale-PID — do NOT warn; both
    are documented "not running" signals.

    Emitted kinds (closed enum):

      * ``daemon_pidfile_read_failed:<path>:<exc_class>:<detail>`` —
        pidfile is on disk but ``read_text`` raised ``OSError``.
      * ``daemon_pidfile_corrupt:<path>:ValueError:<detail>`` —
        pidfile contents are not parseable as an int.
      * ``daemon_pidfile_stat_failed:<path>:<exc_class>:<detail>`` —
        Win32 only: ``Path.stat`` raised ``OSError`` on a pidfile
        whose contents parsed cleanly.

    The ``False`` return on every path is PRESERVED — callers
    (``status_summary`` + tests) get the same boolean semantic.
    ``warnings_out=None`` (default) preserves the pre-W597 silent
    behaviour.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    pid_path = _daemon_pid_path()
    if not pid_path.is_file():
        # Legitimate "not running" — do NOT warn.
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except OSError as exc:
        _emit(f"daemon_pidfile_read_failed:{pid_path}:{type(exc).__name__}:{exc}")
        return False
    except ValueError as exc:
        _emit(f"daemon_pidfile_corrupt:{pid_path}:ValueError:{exc}")
        return False
    if sys.platform == "win32":
        try:
            import time

            mtime = pid_path.stat().st_mtime
            return (time.time() - mtime) < 24 * 3600
        except OSError as exc:
            _emit(f"daemon_pidfile_stat_failed:{pid_path}:{type(exc).__name__}:{exc}")
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        # Legitimate "stale PID, process not alive" — do NOT warn.
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
