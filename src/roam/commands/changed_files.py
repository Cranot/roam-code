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
# Test directory components — matched case-insensitively against the
# lowercased path so mixed-case directories (``Tests/``, ``__TESTS__/``,
# ``SPEC/``, ``TESTING/``) on case-insensitive filesystems still
# classify as test files. ``testing/`` is included for parity with
# ``catalog._shared.is_test_path`` and the canonical
# ``test_conventions._TEST_DIR_PATTERNS`` (W898 — port of catalog's
# permissive directory match).
_TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/", "testing/"]


def is_test_file(path: str | None) -> bool:
    """Check if a file path looks like a test file.

    Uses the file_roles classifier first (covers 22 language-specific test
    naming patterns) and falls back to the legacy heuristic for edge cases.
    The legacy fallback matches test-directory components
    case-insensitively (W898) so mixed-case ``Tests/`` / ``__TESTS__/`` /
    ``SPEC/`` / ``TESTING/`` directories on case-insensitive filesystems
    still classify as test paths.

    Returns ``False`` for empty / falsy inputs.
    """
    if not path:
        return False
    if _roles_is_test(path):
        return True
    # Legacy fallback for patterns file_roles might miss.
    p_cs = path.replace("\\", "/")
    p_lower = p_cs.lower()
    bn = os.path.basename(p_cs)
    if any(pat in bn for pat in _TEST_NAME_PATS):
        return True
    return any(d in p_lower for d in _TEST_DIR_PATS)


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


def is_low_risk_file(path: str | None) -> bool:
    """Check if a file is docs/config/asset with dampened risk contribution.

    Uses file_roles classifier first, falls back to extension check.

    Returns ``False`` for empty / falsy inputs.
    """
    if not path:
        return False
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

    from roam.git_utils import worktree_git_env

    git_env = worktree_git_env(root)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
            env=git_env,
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
                env=git_env,
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


# ---------------------------------------------------------------------------
# Ref-anchored git helpers (W-vibe-check DRY consolidation)
#
# These helpers were previously duplicated across cmd_api_changes,
# cmd_breaking, cmd_semantic_diff, and graph/diff.py. The detect-paste-
# functions roster picked them up as a clone group of 4 (`_git_show`) and
# two clone groups of 3 (`_git_changed_files`, `_parse_source_bytes`).
# Hoisted here so every diff-against-ref command shares the same impl.
# ---------------------------------------------------------------------------


def git_changed_files_against_ref(root: Path, ref: str) -> list[str]:
    """Return files changed between *ref* and the working tree (indexed state).

    Distinct from :func:`get_changed_files` which handles staged / commit-range
    / pr / working-tree sources via a single entry point. This helper is the
    narrow "diff against an explicit ref" form used by the api-changes,
    breaking, and semantic-diff commands.
    """
    cmd = ["git", "diff", "--name-only", ref]
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
        return [p.replace("\\", "/") for p in result.stdout.strip().splitlines() if p.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def git_show_at_ref(root: Path, ref: str, filepath: str) -> bytes | None:
    """Return the content of *filepath* at *ref*, or None if it didn't exist."""
    cmd = ["git", "show", f"{ref}:{filepath}"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def parse_source_with_grammar(source: bytes, language: str):
    """Parse *source* bytes with tree-sitter for the given language.

    Returns ``(tree, source_bytes, effective_language)`` or
    ``(None, None, None)`` on parser unavailability or parse failure.
    Resolves grammar aliases (e.g. ``apex`` -> ``java``) via the indexer's
    ``GRAMMAR_ALIASES`` map.
    """
    from roam.index.parser import GRAMMAR_ALIASES

    grammar = GRAMMAR_ALIASES.get(language, language)

    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(grammar)
    except Exception:
        return None, None, None

    try:
        tree = parser.parse(source)
    except Exception:
        return None, None, None

    return tree, source, language
