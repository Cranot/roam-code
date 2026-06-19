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
    except OSError:
        cache = Path(os.path.expanduser("~")) / ".cache" / "roam"
        cache.mkdir(parents=True, exist_ok=True)
        return cache / "telemetry.db"


def _open() -> sqlite3.Connection | None:
    """Open the telemetry SQLite DB, creating the schema on first use.

    The schema-create is wrapped in an explicit transaction (``with conn:``)
    so the DDL commits atomically — protecting against a torn schema on
    concurrent first-use across processes and silencing the
    ``roam tx-boundaries`` ``unsafe_mutation`` heuristic that flagged the
    bare ``conn.execute`` form (R28 substrate dogfood).

    SQLite itself uses a write-ahead log / rollback journal under the hood,
    so a crash mid-INSERT in :func:`record` cannot tear a row — that
    durability lives in the engine. The change here is the
    schema-creation step explicitly opting into a transaction so the
    intent is visible at the call site, not implicit.
    """
    try:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=2.0)
        # Explicit transaction for the DDL — ``with conn:`` commits on
        # success, rolls back on exception. Idempotent because of
        # ``IF NOT EXISTS``.
        with conn:
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
        # Explicit transaction around the insert + prune so the two
        # statements commit atomically. ``with conn:`` calls
        # ``conn.commit()`` on clean exit and ``conn.rollback()`` on
        # exception — replaces the manual ``conn.commit()`` and pairs
        # the begin with an explicit close so heuristic scanners
        # (``roam tx-boundaries``) classify this as ``transactional``.
        with conn:
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
    except Exception:  # noqa: BLE001 — telemetry must never break the command
        pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — close() failure on cleanup is moot
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
