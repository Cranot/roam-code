"""File discovery using git ls-files with fallback to os.walk."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Extensions that are not source code
SKIP_EXTENSIONS = frozenset({
    ".lock", ".min.js", ".min.css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".bin", ".dat", ".pak", ".wasm",
})

# Filenames to always skip
SKIP_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "poetry.lock", "composer.lock",
    "Gemfile.lock", "Pipfile.lock",
})

# Directories to skip during os.walk fallback
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".eggs", "*.egg-info",
    ".next", ".nuxt", ".output",
    "target", "bin", "obj",
    ".roam",
})

MAX_FILE_SIZE = 1_000_000  # 1MB


def _is_skippable(rel_path: str) -> bool:
    """Check whether a relative path should be skipped."""
    # Skip .roam/ directory (index storage)
    parts = rel_path.replace("\\", "/").split("/")
    if ".roam" in parts:
        return True
    name = os.path.basename(rel_path)
    if name in SKIP_NAMES:
        return True
    _, ext = os.path.splitext(name)
    if ext.lower() in SKIP_EXTENSIONS:
        return True
    return False


def _git_ls_files(root: Path) -> list[str] | None:
    """Try to list files using git ls-files. Returns None if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        return paths
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _walk_files(root: Path) -> list[str]:
    """Fallback file discovery using os.walk, respecting common ignore dirs."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out skippable directories in place
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                rel = os.path.relpath(full, root).replace("\\", "/")
            except (ValueError, OSError):
                # Skip paths that can't be made relative (e.g. Windows device names like NUL)
                continue
            result.append(rel)
    return result


def _filter_files(paths: list[str], root: Path) -> list[str]:
    """Filter out binary, oversized, and non-code files."""
    kept = []
    for rel_path in paths:
        if _is_skippable(rel_path):
            continue
        full_path = root / rel_path
        try:
            if full_path.stat().st_size > MAX_FILE_SIZE:
                continue
        except OSError:
            continue
        kept.append(rel_path)
    return kept


def discover_files(root: Path) -> list[str]:
    """Discover source files in a project directory.

    Uses git ls-files when available, falls back to os.walk.
    Filters out binary, oversized, and non-code files.
    Returns a sorted list of relative paths using forward slashes.
    """
    root = Path(root).resolve()
    raw = _git_ls_files(root)
    if raw is None:
        raw = _walk_files(root)

    # Normalise path separators
    raw = [p.replace("\\", "/") for p in raw]

    filtered = _filter_files(raw, root)
    filtered.sort()
    return filtered
