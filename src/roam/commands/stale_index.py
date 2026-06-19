"""Stale-index detection helper.

Commands that depend on graph accuracy call ``check_stale()`` to
get a bool + reason. By default they should warn on stderr and
proceed; commands can opt into ``--allow-stale`` semantics by
checking the bool and refusing to run when stale.

The check uses the existing ``index_manifest`` table when it exists
(written by the indexer) and falls back to mtime + git-HEAD checks.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

_STALE_AGE_HOURS = 24
_STALE_AGE_HOURS_HIGH_SENSITIVITY = 1


def _git_head_short() -> str | None:
    """Current git HEAD (short) or None if not a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _git_dirty() -> bool:
    """True if there are uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def check_stale(
    db_path: Path | str | None = None,
    *,
    sensitivity: str = "medium",
) -> tuple[bool, str | None]:
    """Determine whether the local index is stale.

    Returns (is_stale, reason). ``reason`` is None when fresh.

    sensitivity:
      - 'high'   — anything older than 1h or any git-HEAD difference is stale.
      - 'medium' — older than 24h is stale; git-HEAD difference is a soft warning.
      - 'low'    — only mtime > 7d is stale; git-HEAD differences ignored.
    """
    if db_path is None:
        try:
            from roam.db.connection import StaleDbDirError, get_db_path

            db_path = get_db_path()
        except StaleDbDirError:
            return True, "index db path could not be resolved"

    p = Path(db_path)
    if not p.exists():
        return True, "no index found (run `roam init`)"

    age_hours = (time.time() - p.stat().st_mtime) / 3600.0

    if sensitivity == "high":
        threshold = _STALE_AGE_HOURS_HIGH_SENSITIVITY
    elif sensitivity == "low":
        threshold = 24 * 7
    else:
        threshold = _STALE_AGE_HOURS

    if age_hours > threshold:
        return True, f"index mtime {age_hours:.1f}h old (threshold {threshold}h)"

    # Manifest-aware check: if the manifest table exists, compare git HEAD.
    try:
        import sqlite3

        conn = sqlite3.connect(str(p), timeout=2)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='index_manifest'")
        has_manifest = cur.fetchone() is not None
        if has_manifest:
            row = conn.execute(
                "SELECT git_head, roam_version FROM index_manifest ORDER BY indexed_at DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row is not None:
                indexed_head, _ = row
                current_head = _git_head_short()
                if indexed_head and current_head and indexed_head[:7] != current_head[:7]:
                    if sensitivity == "high":
                        return True, f"git HEAD changed since index ({indexed_head[:7]} -> {current_head[:7]})"
                    return (
                        False,
                        f"git HEAD differs from index ({indexed_head[:7]} -> {current_head[:7]}); fresh enough but rebuild for accuracy",
                    )
        else:
            conn.close()
    except Exception:  # noqa: BLE001 — manifest read is best-effort; fall back to mtime
        # Manifest not available — fall back to mtime-only result.
        pass

    return False, None
