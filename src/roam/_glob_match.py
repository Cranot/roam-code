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

Semantics (two branches, split on whether the pattern contains ``**``):
- With ``**``: ``**`` matches zero or more directory components
  (including across ``/`` boundaries); ``*`` matches within a single
  path segment (no ``/``); ``?`` matches a single non-``/`` character.
- Without ``**``: the pattern is delegated to ``fnmatch.fnmatch``,
  where ``*`` and ``?`` DO cross ``/`` boundaries (``*.py`` matches
  ``a/b/c.py``). This looser fallback is deliberate and pinned by
  ``tests/test_glob_match.py``.
- Backslashes in both file path and pattern are normalised to forward
  slashes before matching, so Windows-style paths pass through cleanly.
- Empty pattern returns ``False`` — callers that want "no pattern means
  match everything" should test for the empty case themselves.

Dead-export note: production consumers import this symbol via an
underscore alias (``from roam._glob_match import matches_glob as
_matches_glob`` in ``rules/engine.py`` and ``policy/graph_clauses.py``),
so aliased call sites do not resolve back to ``matches_glob`` in the
static symbol graph and ``roam dead`` may report it as a test-only
export. That is a resolver blind spot, not dead code — do not delete.
"""

from __future__ import annotations

from collections.abc import Iterator
import fnmatch
import re


_REGEX_META_CHARS = frozenset(r".+^${}()|[]")


def _literal_regex_fragment(c: str) -> str:
    return "\\" + c if c in _REGEX_META_CHARS else c


def _next_segment_safe_fragment(pat: str, i: int) -> tuple[str, int]:
    c = pat[i]
    if c == "?":
        return "[^/]", i + 1
    if c != "*":
        return _literal_regex_fragment(c), i + 1
    if i + 1 >= len(pat) or pat[i + 1] != "*":
        return "[^/]*", i + 1
    if i + 2 < len(pat) and pat[i + 2] == "/":
        return "(?:.+/)?", i + 3
    return ".*", i + 2


def _segment_safe_fragments(pat: str) -> Iterator[str]:
    i = 0
    while i < len(pat):
        fragment, i = _next_segment_safe_fragment(pat, i)
        yield fragment


def _regex_preserving_doublestar_segments(pat: str) -> str:
    return "".join(_segment_safe_fragments(pat))


def matches_glob(file_path: str, pattern: str) -> bool:
    """Glob match supporting ``**`` for directory wildcards."""
    norm = (file_path or "").replace("\\", "/")
    pat = (pattern or "").replace("\\", "/")
    if not pat:
        return False
    if "**" not in pat:
        return fnmatch.fnmatch(norm, pat)

    regex = _regex_preserving_doublestar_segments(pat)
    return re.match(f"^{regex}$", norm) is not None
