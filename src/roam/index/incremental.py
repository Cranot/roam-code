"""Change detection for incremental re-indexing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def file_hash(path: str | Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def file_matches_index(path: str | Path, mtime: float | None, digest: str | None) -> bool:
    """Use the stored mtime as a fast path before comparing content."""
    current_mtime = os.stat(path).st_mtime
    if mtime is not None and abs(current_mtime - mtime) < 0.001:
        return True
    return file_hash(path) == digest


def get_index_changed_files(
    conn,
    file_paths: list[str],
    root: Path,
) -> tuple[list[str], list[str], list[str]]:
    """Determine which files have been added, modified, or removed.

    Compares the given file_paths against what is stored in the database.
    Uses mtime as a fast check, falling back to sha256 hash when mtime differs.

    Args:
        conn: SQLite connection with files table populated.
        file_paths: Current list of relative file paths on disk.
        root: Project root directory.

    Returns:
        (added, modified, removed) - three lists of relative file paths.
    """
    # Load stored file state
    rows = conn.execute("SELECT path, mtime, hash FROM files").fetchall()
    stored = {row["path"]: (row["mtime"], row["hash"]) for row in rows}

    current_set = set(file_paths)
    stored_set = set(stored.keys())

    added = sorted(current_set - stored_set)
    removed = sorted(stored_set - current_set)

    modified = []
    for path in sorted(current_set & stored_set):
        full_path = root / path
        stored_mtime, stored_hash = stored[path]
        try:
            if not file_matches_index(full_path, stored_mtime, stored_hash):
                modified.append(path)
        except OSError:
            # File disappeared between discovery and check
            removed.append(path)

    return added, modified, removed
