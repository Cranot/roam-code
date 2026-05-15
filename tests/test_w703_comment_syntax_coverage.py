"""W703 drift-guard: _CommentSyntax language coverage.

Asserts that every canonical roam-supported language is either modelled by
``_COMMENT_SYNTAX_BY_LANG`` in ``roam.catalog.smells`` (so
``detect_comment_density`` actually scans it) OR is listed in
``_COMMENT_DENSITY_NO_SUPPORT`` with a documented rationale. Disjointness
and covert-skip (an empty ``_CommentSyntax`` masquerading as coverage)
are both blocked.

W650 / W705 / W720 widened coverage; this guard prevents silent regressions
when a new language lands in ``roam.languages.registry._SUPPORTED_LANGUAGES``
without a matching ``_CommentSyntax`` entry or skip-set decision.
"""

from __future__ import annotations

from pathlib import Path

# _REPO_ROOT kept for parity with sibling drift-guards; not strictly needed
# because the imports resolve via the installed package.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_every_canonical_language_is_covered_or_explicitly_skipped() -> None:
    """No canonical language may be silently absent from comment-density.

    Either model the language in ``_COMMENT_SYNTAX_BY_LANG`` or add it to
    ``_COMMENT_DENSITY_NO_SUPPORT`` with a comment naming the reason.
    """
    from roam.catalog.smells import (
        _COMMENT_DENSITY_NO_SUPPORT,
        _COMMENT_SYNTAX_BY_LANG,
    )
    from roam.languages.registry import _SUPPORTED_LANGUAGES

    covered = set(_COMMENT_SYNTAX_BY_LANG.keys())
    skipped = set(_COMMENT_DENSITY_NO_SUPPORT)
    canonical = set(_SUPPORTED_LANGUAGES)

    gap = canonical - covered - skipped
    assert not gap, (
        f"W703: {len(gap)} canonical languages missing from both "
        f"_COMMENT_SYNTAX_BY_LANG and _COMMENT_DENSITY_NO_SUPPORT: "
        f"{sorted(gap)}. Either add a _CommentSyntax entry to the map "
        f"or append the language to _COMMENT_DENSITY_NO_SUPPORT with a "
        f"rationale comment."
    )


def test_map_and_skip_set_are_disjoint() -> None:
    """A language must not be both covered AND skipped (Pattern 3 vocabulary)."""
    from roam.catalog.smells import (
        _COMMENT_DENSITY_NO_SUPPORT,
        _COMMENT_SYNTAX_BY_LANG,
    )

    overlap = set(_COMMENT_SYNTAX_BY_LANG.keys()) & set(_COMMENT_DENSITY_NO_SUPPORT)
    assert not overlap, (
        f"W703: {len(overlap)} languages appear in BOTH the comment-syntax "
        f"map and the no-support skip-set: {sorted(overlap)}. A language is "
        f"covered XOR skipped, never both."
    )


def test_no_covert_skip_via_empty_comment_syntax_entries() -> None:
    """A _CommentSyntax entry must contribute at least one marker.

    Reject ``_CommentSyntax()`` / ``_CommentSyntax(line=(), block=())`` —
    those look like coverage but yield zero findings, which is silent
    fallback (Pattern 2). The dataclass defaults to empty tuples for
    both fields; together they mean "no markers".
    """
    from roam.catalog.smells import _COMMENT_SYNTAX_BY_LANG

    covert: list[str] = []
    for lang, syntax in _COMMENT_SYNTAX_BY_LANG.items():
        if not syntax.line and not syntax.block:
            covert.append(lang)
    assert not covert, (
        f"W703: {len(covert)} entries in _COMMENT_SYNTAX_BY_LANG have "
        f"neither line nor block markers: {sorted(covert)}. Empty "
        f"entries look like coverage but produce no findings — either "
        f"populate the markers or move the language to "
        f"_COMMENT_DENSITY_NO_SUPPORT."
    )


def test_skip_set_entries_are_all_canonical_languages() -> None:
    """The skip-set should not carry phantom languages.

    Every entry in ``_COMMENT_DENSITY_NO_SUPPORT`` must be a real
    canonical language — otherwise the entry is dead weight and the
    rationale is suspect.
    """
    from roam.catalog.smells import _COMMENT_DENSITY_NO_SUPPORT
    from roam.languages.registry import _SUPPORTED_LANGUAGES

    canonical = set(_SUPPORTED_LANGUAGES)
    phantom = set(_COMMENT_DENSITY_NO_SUPPORT) - canonical
    assert not phantom, (
        f"W703: {len(phantom)} languages in _COMMENT_DENSITY_NO_SUPPORT "
        f"are not in roam.languages.registry._SUPPORTED_LANGUAGES: "
        f"{sorted(phantom)}. Remove dead skip-set entries."
    )
