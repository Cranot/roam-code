"""Indexed persistence for ``.roam/responses`` envelopes.

The responses directory is shared by CLI side-car envelopes and MCP response
handles.  Readers used to rediscover ordering by stat-ing every JSON file on
every call.  This module keeps that ordering in a tiny SQLite index outside the
payload directory, so the normal read path is an indexed lookup while legacy
or externally-created files are imported once.

All in-process writers take the same SQLite write transaction before replacing
the payload and recording its mtime.  That transaction is the concurrency
boundary: a reader sees either the complete old state or the complete new
state, never a pointer to a half-written envelope.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from roam.atomic_io import atomic_write_text

_INDEX_FILENAME = "response-index.sqlite3"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS response_files (
    name TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_response_files_mtime
    ON response_files(mtime_ns, name);
CREATE TABLE IF NOT EXISTS response_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
_DIRECTORY_MTIME_KEY = "directory_mtime_ns"


def _responses_dir(root: Path) -> Path:
    return root / ".roam" / "responses"


def _index_path(root: Path) -> Path:
    return root / ".roam" / _INDEX_FILENAME


def _connect(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_index_path(root), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def _directory_mtime_ns(responses_dir: Path) -> int:
    return responses_dir.stat().st_mtime_ns


def _scan_response_entries(responses_dir: Path) -> list[tuple[str, int, int]]:
    """Return ``(name, mtime_ns, size)`` rows for one stable dir snapshot."""
    rows: list[tuple[str, int, int]] = []
    with os.scandir(responses_dir) as entries:
        for entry in entries:
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                # A non-cooperating external cleaner raced this bootstrap.
                # The directory mtime check below forces another pass.
                continue
            rows.append((entry.name, stat.st_mtime_ns, stat.st_size))
    return rows


def _stable_response_snapshot(responses_dir: Path) -> tuple[list[tuple[str, int, int]], int]:
    """Scan until the directory entry set stays unchanged for one pass."""
    rows: list[tuple[str, int, int]] = []
    after = _directory_mtime_ns(responses_dir)
    for _attempt in range(3):
        before = _directory_mtime_ns(responses_dir)
        rows = _scan_response_entries(responses_dir)
        after = _directory_mtime_ns(responses_dir)
        if before == after:
            break
    return rows, after


def _indexed_directory_mtime(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM response_meta WHERE key = ?",
        (_DIRECTORY_MTIME_KEY,),
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _set_indexed_directory_mtime(conn: sqlite3.Connection, mtime_ns: int) -> None:
    conn.execute(
        "INSERT INTO response_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_DIRECTORY_MTIME_KEY, str(mtime_ns)),
    )


def _synchronize_index(conn: sqlite3.Connection, responses_dir: Path) -> None:
    """Import legacy/external files only when the directory entry set changed."""
    current_mtime = _directory_mtime_ns(responses_dir)
    if _indexed_directory_mtime(conn) == current_mtime:
        return

    rows, stable_mtime = _stable_response_snapshot(responses_dir)
    conn.execute("DELETE FROM response_files")
    conn.executemany(
        "INSERT INTO response_files(name, mtime_ns, size_bytes) VALUES (?, ?, ?)",
        rows,
    )
    _set_indexed_directory_mtime(conn, stable_mtime)


def _begin_synchronized(conn: sqlite3.Connection, responses_dir: Path) -> None:
    # BEGIN IMMEDIATE serializes readers with the payload+index writer.  A
    # reader therefore cannot observe the directory after replace but the
    # index before commit.
    conn.execute("BEGIN IMMEDIATE")
    _synchronize_index(conn, responses_dir)


def store_response_text(
    root: Path,
    filename: str,
    text: str,
    *,
    overwrite: bool = True,
) -> Path:
    """Atomically persist one JSON response and update its index transaction.

    ``overwrite=False`` implements the MCP content-addressed handle contract:
    an existing identical handle is reused without making it newest again.
    """
    if Path(filename).name != filename or not filename.endswith(".json"):
        raise ValueError("response filename must be a basename ending in .json")

    responses_dir = _responses_dir(root)
    responses_dir.mkdir(parents=True, exist_ok=True)
    target = responses_dir / filename
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(root)
        _begin_synchronized(conn, responses_dir)
        if overwrite or not target.is_file():
            atomic_write_text(target, text)
        stat = target.stat()
        conn.execute(
            "INSERT INTO response_files(name, mtime_ns, size_bytes) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "mtime_ns = excluded.mtime_ns, size_bytes = excluded.size_bytes",
            (filename, stat.st_mtime_ns, stat.st_size),
        )
        _set_indexed_directory_mtime(conn, _directory_mtime_ns(responses_dir))
        conn.commit()
        return target
    except sqlite3.Error as exc:
        if conn is not None:
            conn.rollback()
        raise OSError(f"response index update failed: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def _legacy_newest_response(responses_dir: Path, max_age_seconds: int) -> Path | None:
    """Compatibility fallback when the local index cannot be opened."""
    try:
        entries = [p for p in responses_dir.iterdir() if p.suffix == ".json" and p.is_file()]
        newest = max(entries, key=lambda p: p.stat().st_mtime)
        if max_age_seconds > 0 and time.time() - newest.stat().st_mtime > max_age_seconds:
            return None
        return newest
    except (OSError, ValueError):
        return None


def newest_response(root: Path, *, max_age_seconds: int = 0) -> Path | None:
    """Return the newest indexed response, honoring an optional age cutoff."""
    responses_dir = _responses_dir(root)
    if not responses_dir.is_dir():
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(root)
        _begin_synchronized(conn, responses_dir)
        params: tuple[int, ...] = ()
        where = ""
        if max_age_seconds > 0:
            cutoff_ns = time.time_ns() - (max_age_seconds * 1_000_000_000)
            where = "WHERE mtime_ns >= ?"
            params = (cutoff_ns,)
        row = conn.execute(
            f"SELECT name FROM response_files {where} ORDER BY mtime_ns DESC, name DESC LIMIT 1",
            params,
        ).fetchone()
        conn.commit()
        return responses_dir / row[0] if row is not None else None
    except (OSError, sqlite3.Error):
        if conn is not None:
            conn.rollback()
        return _legacy_newest_response(responses_dir, max_age_seconds)
    finally:
        if conn is not None:
            conn.close()


def _legacy_response_candidates(responses_dir: Path, since_epoch: float | None) -> list[Path]:
    """Compatibility fallback for a missing/unusable response index."""
    try:
        files = [p for p in responses_dir.iterdir() if p.suffix == ".json" and p.is_file()]
        if since_epoch is not None:
            files = [p for p in files if p.stat().st_mtime >= (since_epoch - 1)]
        files.sort(key=lambda p: p.stat().st_mtime)
        return files
    except OSError:
        return []


def response_candidates(root: Path, *, since_epoch: float | None = None) -> list[Path]:
    """Return indexed responses in mtime order, optionally from a narrow window."""
    responses_dir = _responses_dir(root)
    if not responses_dir.is_dir():
        return []
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(root)
        _begin_synchronized(conn, responses_dir)
        params: tuple[int, ...] = ()
        where = ""
        if since_epoch is not None:
            where = "WHERE mtime_ns >= ?"
            params = (int((since_epoch - 1) * 1_000_000_000),)
        rows = conn.execute(
            f"SELECT name FROM response_files {where} ORDER BY mtime_ns, name",
            params,
        ).fetchall()
        conn.commit()
        return [responses_dir / row[0] for row in rows]
    except (OSError, sqlite3.Error):
        if conn is not None:
            conn.rollback()
        return _legacy_response_candidates(responses_dir, since_epoch)
    finally:
        if conn is not None:
            conn.close()
