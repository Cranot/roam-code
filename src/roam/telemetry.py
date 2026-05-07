"""opt-in local telemetry.

A tiny SQLite ring buffer that records `(timestamp, command, duration_ms,
exit_code)` rows when ``ROAM_TELEMETRY_LOCAL=1``. Surfaced via
``roam telemetry``. Strictly local — no network, no third-party. Useful
for spotting slow commands and recurring failures during long agent
sessions.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

_RING_LIMIT = 500  # ring buffer size; rows past this are pruned at write time


def _enabled() -> bool:
    return os.environ.get("ROAM_TELEMETRY_LOCAL", "").strip() in {"1", "true", "yes"}


def _db_path() -> Path:
    """Telemetry DB lives next to the project's ``.roam`` so it follows
    the same per-project lifecycle. Falls back to a user-cache location
    when no project root is detected."""
    try:
        from roam.db.connection import find_project_root

        root = find_project_root()
        return root / ".roam" / "telemetry.db"
    except Exception:
        cache = Path(os.path.expanduser("~")) / ".cache" / "roam"
        cache.mkdir(parents=True, exist_ok=True)
        return cache / "telemetry.db"


def _open() -> sqlite3.Connection | None:
    try:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=2.0)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                command TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                exit_code INTEGER NOT NULL
            )"""
        )
        return conn
    except Exception:
        return None


def record(command: str, duration_ms: int, exit_code: int) -> None:
    """Append one row, pruning oldest rows past ``_RING_LIMIT``.

    Silently no-ops on any failure — telemetry must never break a CLI run.
    """
    if not _enabled():
        return
    conn = _open()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO calls (ts, command, duration_ms, exit_code) VALUES (?, ?, ?, ?)",
            (time.time(), command, int(duration_ms), int(exit_code)),
        )
        # Ring-buffer pruning: drop everything older than the most recent
        # _RING_LIMIT rows. Cheap and bounded.
        conn.execute(
            "DELETE FROM calls WHERE id NOT IN (SELECT id FROM calls ORDER BY id DESC LIMIT ?)",
            (_RING_LIMIT,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_top_slow(limit: int = 10) -> list[dict]:
    """Return the slowest recorded calls (descending duration)."""
    conn = _open()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT ts, command, duration_ms, exit_code FROM calls ORDER BY duration_ms DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [{"ts": r[0], "command": r[1], "duration_ms": r[2], "exit_code": r[3]} for r in rows]
    finally:
        conn.close()


def fetch_recent(limit: int = 20) -> list[dict]:
    """Return the most recent recorded calls (descending time)."""
    conn = _open()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT ts, command, duration_ms, exit_code FROM calls ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [{"ts": r[0], "command": r[1], "duration_ms": r[2], "exit_code": r[3]} for r in rows]
    finally:
        conn.close()
