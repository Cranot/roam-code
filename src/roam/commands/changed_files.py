"""Shared utilities for resolving changed files from git."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from roam.index.file_roles import classify_file, is_test as _roles_is_test, ROLE_SOURCE


# ---------------------------------------------------------------------------
# Test / low-risk file detection
# ---------------------------------------------------------------------------

_TEST_NAME_PATS = ["test_", "_test.", ".test.", ".spec."]
_TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/"]


def is_test_file(path: str) -> bool:
    """Check if a file path looks like a test file.

    Uses the file_roles classifier first (covers 22 language-specific test
    naming patterns) and falls back to the legacy heuristic for edge cases.
    """
    if _roles_is_test(path):
        return True
    # Legacy fallback for patterns file_roles might miss
    p = path.replace("\\", "/")
    bn = os.path.basename(p)
    return any(pat in bn for pat in _TEST_NAME_PATS) or any(d in p for d in _TEST_DIR_PATS)


_LOW_RISK_ROLES = frozenset({"docs", "config", "data", "ci", "generated", "vendored"})

_LOW_RISK_EXTS = {
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".lock", ".xml", ".svg", ".png", ".jpg",
    ".gif", ".ico", ".csv", ".env",
}


def is_low_risk_file(path: str) -> bool:
    """Check if a file is docs/config/asset with dampened risk contribution.

    Uses file_roles classifier first, falls back to extension check.
    """
    role = classify_file(path)
    if role in _LOW_RISK_ROLES:
        return True
    p = path.replace("\\", "/").lower()
    _, ext = os.path.splitext(p)
    return ext in _LOW_RISK_EXTS


# ---------------------------------------------------------------------------
# Changed file resolution
# ---------------------------------------------------------------------------


def get_changed_files(
    root: Path,
    staged: bool = False,
    commit_range: str | None = None,
    pr: bool = False,
    base_ref: str = "main",
) -> list[str]:
    """Get list of changed files from git diff.

    Supports four mutually exclusive sources:
    - *commit_range*: arbitrary range (e.g. ``HEAD~3..HEAD``)
    - *staged*: files in the staging area
    - *pr*: files changed in ``base_ref..HEAD``
    - (default): unstaged working-tree changes

    Returns normalised forward-slash paths relative to the repo root.
    """
    cmd = ["git", "diff", "--name-only"]

    if commit_range:
        cmd.append(commit_range)
    elif pr:
        cmd.append(f"{base_ref}...HEAD")
    elif staged:
        cmd.append("--cached")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return []
        return [
            p.replace("\\", "/")
            for p in result.stdout.strip().splitlines()
            if p.strip()
        ]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def resolve_changed_to_db(conn, changed_paths: list[str]) -> dict[str, int]:
    """Map a list of changed paths to ``{path: file_id}`` using the index DB.

    Falls back to LIKE matching when exact path fails (handles sub-directory
    prefixes and normalisation differences).
    """
    file_map: dict[str, int] = {}
    for path in changed_paths:
        row = conn.execute(
            "SELECT id, path FROM files WHERE path = ?", (path,)
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{path}",),
            ).fetchone()
        if row:
            file_map[row["path"]] = row["id"]
    return file_map
