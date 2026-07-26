"""opt-in local telemetry.

A tiny SQLite ring buffer that records `(timestamp, command, duration_ms,
exit_code)` rows when ``ROAM_TELEMETRY_LOCAL=1``. Surfaced via
``roam telemetry``. Strictly local — no network, no third-party. Useful
for spotting slow commands and recurring failures during long agent
sessions.

``exit_code`` provenance (measurement-integrity follow-up to the
2026-07-26 ``fix(telemetry): stop recording every CLI invocation as
exit_code 0`` fix, commit 1c52395f): before that fix, the close hook wrote
``exit_code=0`` unconditionally — every row in an existing ring buffer may
carry that hardcoded constant rather than a real outcome, and nothing in
the row itself said so. ``schema_version`` closes that gap: rows written
under the fixed code always carry ``schema_version >= EXIT_CODE_SCHEMA_
VERSION``; rows written earlier (or before this column existed at all)
read back with ``schema_version`` missing/``None``. Use
:func:`exit_code_is_reliable` before trusting a row's ``exit_code`` for
any success/failure aggregate — never assume an absent value means 0.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

_RING_LIMIT = 500  # ring buffer size; rows past this are pruned at write time

# Bump when the MEANING of a stored field changes such that old rows can no
# longer be trusted the same way as new ones. Version 2 = exit_code reflects
# the real outcome (commit 1c52395f); version 1 (implicit — the column did
# not exist) = exit_code was hardcoded to 0 for every row, a constant, not
# data.
EXIT_CODE_SCHEMA_VERSION = 2


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
                    exit_code INTEGER NOT NULL,
                    schema_version INTEGER
                )"""
            )
            # Migration for DBs created before `schema_version` existed. NULL
            # (not backfilled — the true historical version is unrecoverable)
            # is exactly what marks a row as pre-marker to
            # `exit_code_is_reliable`.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
            if "schema_version" not in columns:
                conn.execute("ALTER TABLE calls ADD COLUMN schema_version INTEGER")
        return conn
    except (OSError, sqlite3.Error):
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
                "INSERT INTO calls (ts, command, duration_ms, exit_code, schema_version) VALUES (?, ?, ?, ?, ?)",
                (time.time(), command, int(duration_ms), int(exit_code), EXIT_CODE_SCHEMA_VERSION),
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
            "SELECT ts, command, duration_ms, exit_code, schema_version FROM calls ORDER BY duration_ms DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [{"ts": r[0], "command": r[1], "duration_ms": r[2], "exit_code": r[3], "schema_version": r[4]} for r in rows]
    finally:
        conn.close()


def fetch_recent(limit: int = 20) -> list[dict]:
    """Return the most recent recorded calls (descending time)."""
    conn = _open()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT ts, command, duration_ms, exit_code, schema_version FROM calls ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [{"ts": r[0], "command": r[1], "duration_ms": r[2], "exit_code": r[3], "schema_version": r[4]} for r in rows]
    finally:
        conn.close()


def exit_code_is_reliable(row: dict) -> bool:
    """Whether ``row["exit_code"]`` reflects a real command outcome.

    Rows with ``schema_version`` missing/``None`` (pre-``EXIT_CODE_SCHEMA_
    VERSION``) recorded ``exit_code=0`` unconditionally — a constant, not
    data. A consumer computing a success/failure rate MUST exclude those
    rows (or disclose them separately) rather than trusting the value.
    """
    version = row.get("schema_version")
    return isinstance(version, int) and not isinstance(version, bool) and version >= EXIT_CODE_SCHEMA_VERSION


def partition_by_exit_code_reliability(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split ring-buffer rows into ``(reliable, unreliable)`` by schema_version.

    Documented partition query for callers that want to aggregate exit_code
    (e.g. a failure rate) without silently blending pre-fix rows — whose
    exit_code can only ever be 0 — into the result.
    """
    reliable = [r for r in rows if exit_code_is_reliable(r)]
    unreliable = [r for r in rows if not exit_code_is_reliable(r)]
    return reliable, unreliable
