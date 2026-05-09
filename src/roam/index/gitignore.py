"""Gitignore-compatible pattern matching for .roamignore and CODEOWNERS.

Supports the full gitignore spec:
- ``*`` matches anything except ``/``
- ``**`` matches anything including ``/``
- ``?`` matches any single character except ``/``
- ``[abc]`` / ``[!abc]`` character classes
- Leading ``/`` anchors to repo root
- Trailing ``/`` matches directories (prefix match)
- ``!pattern`` negation (un-excludes)
- Pattern with ``/`` in middle is implicitly anchored
- Pattern without ``/`` matches basename anywhere
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile_pattern(pattern: str) -> tuple[re.Pattern[str], bool]:
    """Compile a gitignore-style pattern to a regex.

    Returns (compiled_regex, is_basename_match).
    ``is_basename_match`` is True when the pattern has no slash and should
    match against basename anywhere in the tree.
    """
    raw = pattern

    # Trailing / means directory match (prefix + anything under)
    dir_only = raw.endswith("/")
    if dir_only:
        raw = raw.rstrip("/")

    # Leading / means anchored to root
    anchored = raw.startswith("/")
    if anchored:
        raw = raw[1:]

    # If pattern contains a / (after stripping leading /), it's implicitly anchored
    basename_match = not anchored and "/" not in raw

    # Convert gitignore glob to regex.
    # Collect parts in a list and ``"".join`` at the end — building a
    # string with ``+=`` inside a loop is O(n²) for n pattern segments.
    parts: list[str] = []
    i = 0
    plen = len(raw)
    while i < plen:
        c = raw[i]
        if c == "*" and i + 1 < plen and raw[i + 1] == "*":
            # ** — recursive match
            end = i + 2
            # Absorb trailing / after **: a/**/ → a/ or a/x/y/
            if end < plen and raw[end] == "/":
                end += 1
            if end >= plen:
                # ** at end of pattern: match anything remaining
                parts.append(".*")
            else:
                parts.append("(.*/)?")
            i = end
        elif c == "*":
            parts.append("[^/]*")
            i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c == "[":
            # Character class — pass through, converting [! to [^
            j = i + 1
            cls_parts: list[str] = ["["]
            if j < plen and raw[j] == "!":
                cls_parts.append("^")
                j += 1
            # Find closing ]
            while j < plen and raw[j] != "]":
                cls_parts.append(raw[j])
                j += 1
            if j < plen:
                cls_parts.append("]")
                j += 1
            parts.append("".join(cls_parts))
            i = j
        elif c == ".":
            parts.append(r"\.")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1

    body = "".join(parts)
    if dir_only:
        # Directory pattern: match the dir itself or anything underneath
        if basename_match:
            # Unanchored directory: match anywhere
            regex = f"(^|.*/){body}(/.*)?$"
        else:
            regex = f"^{body}(/.*)?$"
    elif basename_match:
        # No slash in pattern: match against any path component (basename)
        regex = f"(^|.*/){body}$"
    else:
        # Anchored or has slash: match from root
        regex = f"^{body}$"

    return re.compile(regex), basename_match


def matches_gitignore(rel_path: str, pattern: str) -> bool:
    """Check if *rel_path* matches a single gitignore-style *pattern*.

    *rel_path* must use forward slashes and be relative to the repo root.
    """
    rel_path = rel_path.replace("\\", "/")
    compiled, _basename = _compile_pattern(pattern)
    return bool(compiled.search(rel_path))


def matches_exclude_patterns(rel_path: str, patterns: list[str]) -> bool:
    """Check if *rel_path* is excluded by a list of gitignore-style patterns.

    Supports ``!pattern`` negation: the last matching pattern wins.
    Comments (``#``) and blank lines are skipped.
    """
    rel_path = rel_path.replace("\\", "/")
    excluded = False
    for pat in patterns:
        if not pat or pat.startswith("#"):
            continue
        if pat.startswith("!"):
            # Negation: un-exclude
            actual = pat[1:]
            if not actual:
                continue
            compiled, _basename = _compile_pattern(actual)
            if compiled.search(rel_path):
                excluded = False
        else:
            compiled, _basename = _compile_pattern(pat)
            if compiled.search(rel_path):
                excluded = True
    return excluded
