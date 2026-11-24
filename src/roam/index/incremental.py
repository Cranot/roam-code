"""Change detection for incremental re-indexing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from roam.db.connection import find_project_root
from roam.git_utils import git_status_paths, project_relative_git_paths
from roam.index.manifest import GitSnapshot


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


def file_matches_index(
    path: str | Path, mtime: float | None, digest: str | None, *, compare_content: bool = False
) -> bool:
    """Use the stored mtime as a fast path before comparing content."""
    if not compare_content and mtime is not None and abs(os.stat(path).st_mtime - mtime) < 0.001:
        return True
    return file_hash(path) == digest


def _git_content_paths(conn, root: Path, snapshot: GitSnapshot) -> set[str] | None:
    """Bound timestamp-independent checks using current and previously dirty paths."""
    from roam.db.freshness import (
        _CheckDeadline,
        _git_diff_paths,
        _is_git_oid,
        _latest_index_manifest,
        _manifest_dirty_paths,
        _manifest_notes,
    )

    root = root.resolve()
    git_root = find_project_root(root)
    if not (git_root / ".git").exists():
        return set()
    row = _latest_index_manifest(conn)
    if row is None or snapshot.status is None:
        return None
    _, indexed_head, dirty_hash, notes, _ = row
    prior = _manifest_dirty_paths(dirty_hash, _manifest_notes(notes))
    if prior is None or not (_is_git_oid(indexed_head) and _is_git_oid(snapshot.head)):
        return None
    diff_paths, _, _ = _git_diff_paths(git_root, indexed_head, snapshot.head, _CheckDeadline())
    if diff_paths is None:
        # When Git cannot bound the changes, verify the whole retained set.
        return None
    return project_relative_git_paths(git_status_paths(snapshot.status).union(prior, diff_paths), root, git_root)


def get_index_changed_files(
    conn,
    file_paths: list[str],
    root: Path,
    *,
    git_snapshot: GitSnapshot | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Determine which files have been added, modified, or removed.

    Compares the given file_paths against what is stored in the database.
    Git-reported paths always use content. Other paths can use the mtime fast path.

    Args:
        conn: SQLite connection with files table populated.
        file_paths: Current list of relative file paths on disk.
        root: Project root directory.
        git_snapshot: Git state captured before reading source files.

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
    content_paths = _git_content_paths(conn, root, git_snapshot) if git_snapshot is not None and stored else set()
    for path in sorted(current_set & stored_set):
        full_path = root / path
        stored_mtime, stored_hash = stored[path]
        try:
            if not file_matches_index(
                full_path, stored_mtime, stored_hash, compare_content=content_paths is None or path in content_paths
            ):
                modified.append(path)
        except OSError:
            # File disappeared between discovery and check
            removed.append(path)

    return added, modified, removed
