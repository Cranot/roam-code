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

``{a,b,c}`` brace alternation (shipped 2026-07-27, same defect class as the
``**`` scope hole above, one metacharacter over): expanded as a pure
textual preprocessing step, BEFORE either branch runs. ``*.{ts,tsx}``
becomes the two patterns ``*.ts`` / ``*.tsx``, each matched exactly as
before — a file matches if ANY expansion matches. This means ``*``/``?``/
``**`` semantics are untouched; only brace groups are new. Previously
`{`/`}`/`,` were escaped as literal regex/fnmatch characters, so a brace
pattern only ever matched a filename containing a literal ``{`` — i.e.
never, on any real path. See ``_expand_braces`` for the edge-case
decisions (nesting, single item, empty alternative, unmatched ``{``,
``/`` inside a brace).

The canonical implementation is named ``_matches_glob``; the public
``matches_glob`` alias is kept so existing imports
(``from roam._glob_match import matches_glob[ as _matches_glob]``)
continue to work. The aliased production call sites in
``rules/engine.py`` and ``policy/graph_clauses.py`` resolve to the
private implementation in the static symbol graph, avoiding a false
positive dead-export report on the public name.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterator
from functools import lru_cache

_REGEX_META_CHARS = frozenset(r".+^${}()|[]")


def _find_matching_brace(pat: str, start: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at ``start``.

    Depth-counts so nested groups (``{a,{b,c}}``) resolve to the OUTER
    close, not the first ``}`` encountered. Returns -1 if unmatched — the
    caller's job to decide what an unmatched brace means (see
    ``_expand_braces``: it means "literal character", not an error).
    """
    depth = 0
    i = start
    n = len(pat)
    while i < n:
        if pat[i] == "{":
            depth += 1
        elif pat[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level_commas(s: str) -> list[str]:
    """Split ``s`` on commas that are not inside a nested ``{...}`` group."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


@lru_cache(maxsize=512)
def _expand_braces(pat: str) -> tuple[str, ...]:
    """Expand ``{a,b}``-style alternation into concrete pattern strings.

    Pure textual preprocessing, independent of ``*``/``?``/``**`` — those
    are interpreted downstream, per expansion, exactly as before. Decisions
    for the awkward cases (deliberately explicit, not left to the regex):

    - Nested braces (``{a,{b,c}}``): expand outside-in, recursively —
      yields ``a``, ``b``, ``c``.
    - Single-item brace (``{ts}``): one alternative, so it collapses to
      the literal text ``ts`` — equivalent to not having braces at all.
    - Empty alternative (``{ts,}``): a legal branch matching zero
      characters, so it expands to ``ts`` AND `` (empty) — lets a rule
      author write "optional suffix" patterns like ``file{.bak,}``.
    - Literal unmatched ``{`` (no matching ``}`` anywhere in the string):
      treated as an ordinary literal character, not an alternation and
      not an error — the whole (sub)pattern passes through unexpanded and
      is matched byte-for-byte by the existing engines, which already
      escape ``{`` as literal. This preserves old behaviour exactly for
      the malformed case instead of guessing at author intent.
    - ``/`` inside a brace alternative (``{api/v1,api/v2}/*.ts``): not
      special-cased — a comma-delimited alternative is just literal text
      like any other, may itself contain ``/`` or further wildcards, and
      is spliced back into the surrounding pattern before the normal
      matcher runs.
    """
    i = pat.find("{")
    if i == -1:
        return (pat,)
    close = _find_matching_brace(pat, i)
    if close == -1:
        return (pat,)
    prefix, inner, suffix = pat[:i], pat[i + 1 : close], pat[close + 1 :]
    alternatives = _split_top_level_commas(inner)
    out: list[str] = []
    seen: set[str] = set()
    for alt in alternatives:
        for expanded in _expand_braces(prefix + alt + suffix):
            if expanded not in seen:
                seen.add(expanded)
                out.append(expanded)
    return tuple(out)


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


def _matches_glob(file_path: str, pattern: str) -> bool:
    """Glob match supporting ``**`` for directory wildcards and ``{a,b}`` alternation."""
    norm = (file_path or "").replace("\\", "/")
    pat = (pattern or "").replace("\\", "/")
    if not pat:
        return False
    # Brace expansion is a preprocessing step: each expansion is matched by
    # the exact same **-or-fnmatch logic as an un-braced pattern, so */?/**
    # semantics are untouched. A path matches if ANY expansion matches.
    for expanded in _expand_braces(pat):
        if "**" not in expanded:
            if fnmatch.fnmatch(norm, expanded):
                return True
            continue
        regex = _regex_preserving_doublestar_segments(expanded)
        if re.match(f"^{regex}$", norm) is not None:
            return True
    return False


# Public alias kept for existing ``from roam._glob_match import matches_glob``
# import sites in ``rules/engine.py`` and ``policy/graph_clauses.py``.
matches_glob = _matches_glob
