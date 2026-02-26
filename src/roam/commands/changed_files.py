"""Shared utilities for resolving changed files from git."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from roam.index.file_roles import classify_file
from roam.index.file_roles import is_test as _roles_is_test

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
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".lock",
    ".xml",
    ".svg",
    ".png",
    ".jpg",
    ".gif",
    ".ico",
    ".csv",
    ".env",
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
    untracked: bool = False,
) -> list[str]:
    """Get list of changed files from git diff.

    Supports four mutually exclusive sources:
    - *commit_range*: arbitrary range (e.g. ``HEAD~3..HEAD``)
    - *staged*: files in the staging area
    - *pr*: files changed in ``base_ref..HEAD``
    - (default): unstaged working-tree changes

    When *untracked* is True, also includes new files that are not yet
    tracked by git (``git ls-files --others --exclude-standard``).

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
        paths = [p.replace("\\", "/") for p in result.stdout.strip().splitlines() if p.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if untracked:
        try:
            ut = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
            if ut.returncode == 0 and ut.stdout.strip():
                for line in ut.stdout.strip().splitlines():
                    line = line.strip()
                    if line:
                        paths.append(line.replace("\\", "/"))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return paths


def resolve_changed_to_db(conn, changed_paths: list[str]) -> dict[str, int]:
    """Map a list of changed paths to ``{path: file_id}`` using the index DB.

    Falls back to suffix matching when exact path fails (handles sub-directory
    prefixes and normalisation differences).  Uses a basename index for O(1)
    suffix lookups instead of scanning all files.
    """
    file_map: dict[str, int] = {}
    all_files = conn.execute("SELECT id, path FROM files").fetchall()
    exact: dict[str, tuple[int, str]] = {}
    # Suffix index: basename -> list of (id, full_path) for fast fallback
    suffix_idx: dict[str, list[tuple[int, str]]] = {}
    for f in all_files:
        f_id, f_path = f["id"], f["path"]
        exact[f_path] = (f_id, f_path)
        basename = f_path.rsplit("/", 1)[-1]
        suffix_idx.setdefault(basename, []).append((f_id, f_path))
    for path in changed_paths:
        hit = exact.get(path)
        if not hit:
            # Use suffix index: look up by basename, then verify endswith
            basename = path.rsplit("/", 1)[-1]
            for f_id, f_path in suffix_idx.get(basename, ()):
                if f_path.endswith(path):
                    hit = (f_id, f_path)
                    break
        if hit:
            file_map[hit[1]] = hit[0]
    return file_map
