"""Shared ``**``-aware glob matcher.

A leaf module (no roam-internal imports) hosting the glob-with-``**``
matcher that ``roam.rules.engine`` and ``roam.policy.graph_clauses``
both need. Kept top-level so neither package owns the dependency
direction — the ``policy → rules.engine`` cycle hedge previously
documented at ``policy/graph_clauses.py:_matches_glob`` was real (real
import edge: ``rules/engine.py`` lazily imports ``policy.graph_clauses``)
but the duplication was nevertheless cargo-culted across other call
sites (clone cluster sim=0.852 on roam-code itself, W856 detector).
This module breaks the symmetry: both packages depend on a leaf, not on
each other.

Semantics:
- ``**`` matches zero or more directory components (including across
  ``/`` boundaries). ``*`` matches a single path segment (no ``/``).
  ``?`` matches a single non-``/`` character.
- Backslashes in both file path and pattern are normalised to forward
  slashes before matching, so Windows-style paths pass through cleanly.
- Empty pattern returns ``False`` — callers that want "no pattern means
  match everything" should test for the empty case themselves.
"""

from __future__ import annotations

import fnmatch
import re


def matches_glob(file_path: str, pattern: str) -> bool:
    """Glob match supporting ``**`` for directory wildcards."""
    norm = (file_path or "").replace("\\", "/")
    pat = (pattern or "").replace("\\", "/")
    if not pat:
        return False
    if "**" not in pat:
        return fnmatch.fnmatch(norm, pat)

    parts: list[str] = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "*":
            if i + 1 < len(pat) and pat[i + 1] == "*":
                if i + 2 < len(pat) and pat[i + 2] == "/":
                    parts.append("(?:.+/)?")
                    i += 3
                    continue
                parts.append(".*")
                i += 2
                continue
            parts.append("[^/]*")
            i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c in r".+^${}()|[]":
            parts.append("\\" + c)
            i += 1
        else:
            parts.append(c)
            i += 1
    regex = "".join(parts)
    return re.match("^" + regex + "$", norm) is not None
