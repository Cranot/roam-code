"""Shared CODEOWNERS parsing and resolution helpers.

Extracted from cmd_codeowners.py to avoid duplication across commands that
need CODEOWNERS data (codeowners, drift, simulate-departure).
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# CODEOWNERS locations (checked in order)
# ---------------------------------------------------------------------------

_CODEOWNERS_LOCATIONS = [
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
    ".gitlab/CODEOWNERS",
]


# ---------------------------------------------------------------------------
# Finder
# ---------------------------------------------------------------------------


def find_codeowners(project_root: Path) -> Path | None:
    """Find the CODEOWNERS file in standard locations.

    Returns the first matching path, or None if no file exists.
    """
    for loc in _CODEOWNERS_LOCATIONS:
        candidate = project_root / loc
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_codeowners(codeowners_path: str | Path) -> list[tuple[str, list[str]]]:
    """Parse a CODEOWNERS file into (pattern, owners) tuples.

    Format:
    - Lines starting with # are comments
    - Empty lines are ignored
    - Pattern followed by one or more owners: ``*.py @backend-team @alice``
    - Later rules override earlier ones (last match wins)
    """
    path = Path(codeowners_path)
    if not path.is_file():
        return []

    rules: list[tuple[str, list[str]]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue
        # Inline comments (# after whitespace)
        if " #" in line:
            line = line[: line.index(" #")].strip()
        parts = line.split()
        if len(parts) < 2:
            # Pattern with no owner = explicitly unowned (clears ownership)
            rules.append((parts[0], []))
            continue
        pattern = parts[0]
        owners = parts[1:]
        rules.append((pattern, owners))

    return rules


# ---------------------------------------------------------------------------
# Pattern matching (gitignore-style)
# ---------------------------------------------------------------------------


def _codeowners_match(pattern: str, filepath: str) -> bool:
    """Match a CODEOWNERS pattern against a file path.

    Delegates to the shared gitignore matcher in ``roam.index.gitignore``.
    """
    from roam.index.gitignore import matches_gitignore

    return matches_gitignore(filepath, pattern)


def resolve_owners(rules: list[tuple[str, list[str]]], filepath: str) -> list[str]:
    """Determine the owner(s) of a file by applying CODEOWNERS rules.

    Last matching rule wins (standard CODEOWNERS semantics).
    Returns an empty list if no rule matches.
    """
    owners: list[str] = []
    for pattern, rule_owners in rules:
        if _codeowners_match(pattern, filepath):
            owners = rule_owners
    return owners
