"""Tests for the shared ``**``-aware glob matcher (``roam._glob_match``).

Covers the documented semantics of ``matches_glob``:
- ``**`` matches zero or more directory components (across ``/``).
- ``*`` matches a single path segment (no ``/``) in the ``**`` branch,
  but crosses ``/`` in the plain-``fnmatch`` branch (no ``**`` present).
- ``?`` matches a single non-``/`` character in the ``**`` branch.
- Backslashes in both path and pattern normalise to ``/``.
- Empty/None pattern returns ``False``; ``None`` path coerces to ``""``.
- Regex metacharacters are treated literally in the ``**`` branch, so
  ``[...]`` character classes are NOT special there (unlike fnmatch).
- ``{a,b,c}`` brace alternation (2026-07-27 fix, same defect class as the
  ``**`` scope hole above, one metacharacter over): expanded as a textual
  preprocessing step before either branch runs, so it works in BOTH the
  fnmatch branch and the ``**`` regex branch. See ``_expand_braces`` for
  the documented edge-case decisions this file also pins.
"""

from __future__ import annotations

import pytest

from roam._glob_match import _expand_braces, matches_glob

# --- Empty / None handling -------------------------------------------------


@pytest.mark.parametrize("path", ["x", "a/b/c.py", "", None])
def test_empty_pattern_returns_false(path):
    """Empty pattern never matches — callers must special-case 'match all'."""
    assert matches_glob(path, "") is False


def test_none_pattern_returns_false():
    assert matches_glob("anything.py", None) is False


def test_none_path_coerces_to_empty_string():
    # None path -> "" ; pattern "x" is non-empty, no ** -> fnmatch("", "x")
    assert matches_glob(None, "x") is False
    # but ** pattern ".*" matches the empty string
    assert matches_glob(None, "**") is True


def test_return_type_is_bool():
    assert isinstance(matches_glob("a.py", "*.py"), bool)
    assert isinstance(matches_glob("a/b.py", "**/b.py"), bool)
    assert isinstance(matches_glob("x", ""), bool)


# --- Plain fnmatch branch (no ``**`` in pattern) ---------------------------


def test_fnmatch_literal_match():
    assert matches_glob("src/foo.py", "src/foo.py") is True
    assert matches_glob("src/foo.py", "src/bar.py") is False


def test_fnmatch_single_star_matches_within_segment():
    assert matches_glob("src/foo.py", "src/*.py") is True


def test_fnmatch_single_star_crosses_slash():
    # In the fnmatch branch, ``*`` is fnmatch's ``*`` which crosses ``/``.
    assert matches_glob("a/b/c.py", "*.py") is True
    assert matches_glob("src/a/b/test.py", "src/*/test.py") is True


def test_fnmatch_char_class_is_honored():
    # No ``**`` -> fnmatch handles ``[...]`` as a real character class.
    assert matches_glob("a.py", "[abc].py") is True
    assert matches_glob("d.py", "[abc].py") is False
    assert matches_glob("foo.py", "[abc].py") is False


# --- ``**/`` : zero-or-more directory components ----------------------------


def test_doublestar_slash_matches_zero_dirs():
    assert matches_glob("foo.py", "**/foo.py") is True


def test_doublestar_slash_matches_one_dir():
    assert matches_glob("a/foo.py", "**/foo.py") is True


def test_doublestar_slash_matches_many_dirs():
    assert matches_glob("a/b/c/foo.py", "**/foo.py") is True


def test_doublestar_slash_with_prefix_zero_dirs():
    assert matches_glob("src/test.py", "src/**/test.py") is True


def test_doublestar_slash_with_prefix_many_dirs():
    assert matches_glob("src/a/b/test.py", "src/**/test.py") is True


def test_doublestar_slash_requires_basename_match():
    assert matches_glob("a/b/other.py", "**/foo.py") is False


# --- ``**`` not followed by ``/`` : ``.*`` (crosses slashes) ----------------


def test_trailing_doublestar_requires_slash_prefix():
    # ``src/**`` -> ``src/.*`` ; bare ``src`` lacks the slash, so no match.
    assert matches_glob("src", "src/**") is False
    assert matches_glob("src/a", "src/**") is True
    assert matches_glob("src/a/b/c", "src/**") is True


def test_bare_doublestar_matches_everything():
    assert matches_glob("anything/at/all.py", "**") is True
    assert matches_glob("", "**") is True


def test_doublestar_without_slash_crosses_segments():
    # ``**.py`` -> ``.*\.py`` which spans ``/``.
    assert matches_glob("a/b.py", "**.py") is True
    assert matches_glob("a.py", "**.py") is True


# --- ``*`` inside the ``**`` branch : single segment (no slash) -------------


def test_single_star_in_doublestar_branch_stays_in_segment():
    # ``**/*.py`` matches a basename in any directory depth.
    assert matches_glob("foo.py", "**/*.py") is True
    assert matches_glob("a/foo.py", "**/*.py") is True
    assert matches_glob("a/b/foo.py", "**/*.py") is True


def test_single_star_in_doublestar_branch_does_not_cross_slash():
    # ``src/*.py`` would normally be fnmatch, but adding ``**`` forces the
    # regex branch where ``*`` -> ``[^/]*`` cannot cross ``/``.
    assert matches_glob("src/a/b.py", "src/*.py/**") is False


# --- ``?`` inside the ``**`` branch : single non-slash char -----------------


def test_question_mark_matches_single_char():
    assert matches_glob("a.py", "**/?.py") is True
    assert matches_glob("a/b.py", "**/?.py") is True


def test_question_mark_does_not_match_two_chars():
    assert matches_glob("ab.py", "**/?.py") is False


def test_question_mark_does_not_cross_slash():
    # In the ``**`` branch ``?`` -> ``[^/]`` and cannot consume a ``/``.
    # ``a?c/**`` lines the ``?`` up with the slash in ``a/c/x`` -> no match,
    # while the contrast input ``axc/x`` (real char at that spot) matches.
    assert matches_glob("a/c/x", "a?c/**") is False
    assert matches_glob("axc/x", "a?c/**") is True


# --- Regex metacharacters are literal in the ``**`` branch ------------------


def test_dot_is_literal_in_doublestar_branch():
    # The ``.`` must match a literal dot, not any char.
    assert matches_glob("foo.py", "**/foo.py") is True
    assert matches_glob("fooXpy", "**/foo.py") is False


def test_char_class_is_literal_in_doublestar_branch():
    # Unlike the fnmatch branch, ``[abc]`` is NOT a class here — the
    # brackets are escaped, so only the literal text ``[abc]`` matches.
    assert matches_glob("a.py", "**/[abc].py") is False
    assert matches_glob("[abc].py", "**/[abc].py") is True


def test_other_regex_metachars_are_literal():
    # ``+ ( ) $`` etc. are escaped, matched literally.
    assert matches_glob("a+b/c.py", "**/c.py") is True
    assert matches_glob("a+b.py", "a+b.py/**") is False  # needs trailing seg
    assert matches_glob("(x).py", "**/(x).py") is True
    assert matches_glob("x.py", "**/(x).py") is False


# --- Backslash normalisation -----------------------------------------------


def test_backslashes_in_path_normalised():
    assert matches_glob("a\\b\\c.py", "a/b/*.py") is True


def test_backslashes_in_pattern_normalised():
    assert matches_glob("a/b/c.py", "a\\b\\*.py") is True


def test_backslashes_both_sides_with_doublestar():
    assert matches_glob("a\\b\\c\\d.py", "a\\**\\d.py") is True


# --- Anchoring (full-path match, not substring) ----------------------------


def test_match_is_anchored_at_start():
    assert matches_glob("xsrc/foo.py", "src/**") is False


def test_match_is_anchored_at_end():
    assert matches_glob("a/foo.py.bak", "**/foo.py") is False


# --- ``{a,b,c}`` brace alternation ------------------------------------------
#
# THE DEFECT THIS GUARDS (fixed 2026-07-27, same class as the ``**`` hole
# above): `{`/`}`/`,` were escaped as literal regex/fnmatch characters, so
# `*.{ts,tsx}` only ever matched a filename containing the literal text
# `{ts,tsx}` -- never a real `.ts` or `.tsx` file. 13 rules in the shipped
# TypeScript starter pack alone used this shape (6 of them BLOCK severity)
# and were permanently dead.


def test_brace_alternation_plain_fnmatch_branch():
    # No ``**`` -> fnmatch branch, but braces are expanded before fnmatch runs.
    assert matches_glob("a.ts", "*.{ts,tsx}") is True
    assert matches_glob("a.tsx", "*.{ts,tsx}") is True
    assert matches_glob("a.js", "*.{ts,tsx}") is False


def test_brace_alternation_doublestar_branch():
    assert matches_glob("src/app.ts", "src/**/*.{ts,tsx}") is True
    assert matches_glob("app.ts", "src/**/*.{ts,tsx}") is False  # wrong prefix
    assert matches_glob("src/a/b/app.tsx", "src/**/*.{ts,tsx}") is True
    assert matches_glob("src/app.js", "src/**/*.{ts,tsx}") is False


def test_brace_nested():
    # {a,{b,c}} -> a | b | c.
    assert matches_glob("app.ts", "app.{js,{ts,tsx}}") is True
    assert matches_glob("app.tsx", "app.{js,{ts,tsx}}") is True
    assert matches_glob("app.jsx", "app.{js,{ts,tsx}}") is False


def test_brace_single_item_collapses_to_literal():
    # {ts} has exactly one alternative -- equivalent to no braces at all.
    assert matches_glob("app.ts", "app.{ts}") is True
    assert matches_glob("app.tsx", "app.{ts}") is False


def test_brace_empty_alternative_matches_zero_chars():
    # {ts,} -> "ts" OR "" (empty branch), e.g. an optional suffix.
    assert matches_glob("file.bak", "file{.bak,}") is True
    assert matches_glob("file", "file{.bak,}") is True
    assert matches_glob("file.txt", "file{.bak,}") is False


def test_brace_unmatched_open_is_literal_not_alternation():
    # No matching `}` anywhere -> treated as an ordinary literal character,
    # not an error and not an alternation. `*.{ts` only matches a filename
    # that literally ends in the text `.{ts`.
    assert matches_glob("app.{ts", "*.{ts") is True
    assert matches_glob("app.ts", "*.{ts") is False
    assert _expand_braces("*.{ts") == ("*.{ts",)


def test_brace_containing_path_separator():
    # A brace alternative may itself contain `/` -- not special-cased, just
    # literal text spliced back into the surrounding pattern.
    assert matches_glob("src/api/x.ts", "{src/api,src/web}/*.ts") is True
    assert matches_glob("src/web/x.ts", "{src/api,src/web}/*.ts") is True
    assert matches_glob("src/db/x.ts", "{src/api,src/web}/*.ts") is False


def test_expand_braces_dedupes_and_preserves_order():
    assert _expand_braces("{a,a,b}") == ("a", "b")
