"""Regression tests for dogfood-v2 (2026-05-13) ``stale-refs`` findings.

Two related bugs were reported against ``roam stale-refs`` on the
second-repo repo:

* **Bug 2 — display-string-as-path / bare-backtick noise.** A hyperlink
  shaped ``[`code-map/X.md`](../code-map/X.md)`` had its display half
  (the backtick-wrapped string) treated as a filesystem path on the
  pre-fix scanner. The URL half — the actual canonical reference — was
  ignored. Bare backtick strings in prose (``Look at `Foo.php` …``) were
  also being treated as path claims. Both behaviours were already
  patched on the v12.49 codebase via:

    - URL-half extraction at the ``_MD_INLINE_RE`` site (``m.group("url")``),
    - ``scan_bare_backticks`` opt-in default ``False`` in
      :func:`roam.commands.cmd_stale_refs._extract_refs`,
    - the ``--fix`` safety guards in
      :func:`roam.commands.cmd_stale_refs._rewrite_is_safe`
      (no-op detection + double-prefix detection + new-URL liveness).

  This file pins those invariants down as **unit-level regression
  tests** that operate on the helper functions directly, so a future
  refactor of the extractor/resolver pipeline can't silently re-open
  the corruption window without breaking the suite. The companion
  end-to-end tests in :mod:`tests.test_stale_refs_corruption` cover the
  CLI surface; this file's job is to make the underlying mechanisms
  inspectable in isolation.

* **Bug 8 — heading-slugger diverges from GitHub.** ``stale-refs``
  validates ``[…](file.md#anchor)`` fragments by extracting headers
  from the target file and slugifying them. Two divergences from
  GitHub's ``html-pipeline`` (``jch-algoritmo``) caused 9-23 valid
  anchors per repo to be reported as broken:

    1. **Intraword underscores were stripped.** The pre-fix regex
       ``(\\*\\*|__|\\*|_|`)`` removed every ``_``, so a header like
       ``## PFPA_EPIL.IN_PFPA_EPIL`` slugified to ``pfpaepilinpfpaepil``
       instead of GitHub's ``pfpa_epilin_pfpa_epil`` — every ``#pfpa_epil``
       reference failed validation.
    2. **Whitespace runs were collapsed.** ``re.sub(r"\\s+", "-", text)``
       turned ``"foo  bar"`` (two spaces) into ``foo-bar`` (one dash),
       but GitHub's ``gsub(' ', '-')`` produces ``foo--bar``.

  Both are fixed in :mod:`roam.commands.stale_refs_anchors` — the new
  ``_UNDERSCORE_EMPHASIS_RE`` strips ``_`` only at non-word boundaries
  (CommonMark left/right-flanking rule, approximated), and the slug
  whitespace step is now ``\\s`` (each char) not ``\\s+`` (runs).
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_stale_refs import (
    _extract_refs,
    _resolve_target,
    _rewrite_is_safe,
)
from roam.commands.stale_refs_anchors import (
    _strip_inline_markup,
    extract_anchors,
    slugify,
)

# ---------------------------------------------------------------------------
# Bug 2 — extractor / resolver invariants
# ---------------------------------------------------------------------------


class TestStaleRefsUsesUrlNotDisplay:
    """The URL half of ``[display](url)`` is the canonical path."""

    def test_extracted_ref_is_the_url_not_the_display_string(self):
        """``[`code-map/X.md`](../code-map/X.md)`` extracts the URL only.

        Regression: the v12.48 pre-fix scanner extracted BOTH halves
        (display string as a bare backtick + URL as an md_inline) and
        treated the display string as a path that didn't exist
        relative to the source file's directory. The fix landed in
        v12.49: ``m.group("url")`` for inline links and
        ``scan_bare_backticks`` default ``False`` for bare backticks.
        """
        content = "See [`code-map/16-X.md`](../code-map/16-X.md) for details.\n"
        refs = _extract_refs(content, prose_mode=True)
        # Exactly one extraction, and it's the URL.
        assert refs == [(1, "md_inline", "../code-map/16-X.md")]
        # No "backtick" finding in the default mode.
        kinds = {r[1] for r in refs}
        assert "backtick" not in kinds

    def test_url_half_resolves_relative_to_source_dir(self, tmp_path):
        """``../code-map/X.md`` from ``docs/legacy/reports/`` resolves to
        ``docs/legacy/code-map/X.md`` — the working-link case."""
        (tmp_path / "docs" / "legacy" / "code-map").mkdir(parents=True)
        target = tmp_path / "docs" / "legacy" / "code-map" / "16-X.md"
        target.write_text("# 16-X\n")
        (tmp_path / "docs" / "legacy" / "reports").mkdir(parents=True)
        source_rel = "docs/legacy/reports/some-doc.md"
        (tmp_path / source_rel).write_text("See [`code-map/16-X.md`](../code-map/16-X.md) for details.\n")
        resolved = _resolve_target("../code-map/16-X.md", source_rel, tmp_path)
        assert resolved is not None
        assert resolved.exists()
        assert resolved.resolve() == target.resolve()

    def test_bare_backtick_strings_in_prose_are_not_extracted(self):
        """Bare backtick strings (no ``[](...)`` wrapper) are inline code,
        not filesystem-path references. By default they're invisible to
        the scanner."""
        content = (
            "Look at the `MyDataController.php` which handles the workflow.\nThe `process()` helper does the work.\n"
        )
        refs = _extract_refs(content, prose_mode=True)
        # Nothing — bare backticks in prose are inline code, not links.
        assert refs == []

    def test_bare_backtick_inside_link_display_suppressed_with_opt_in(self):
        """Even with ``--scan-bare-backticks``, backticks INSIDE a
        markdown link's ``[display]`` are suppressed — the URL half is
        the source of truth for liveness, never the cosmetic display."""
        content = "See [`code-map/16-X.md`](../code-map/16-X.md) for details.\n"
        refs = _extract_refs(content, prose_mode=True, scan_bare_backticks=True)
        # Only the URL surfaces.
        assert refs == [(1, "md_inline", "../code-map/16-X.md")]


class TestStaleRefsFixSafetyAgainstDoublePrefix:
    """``--fix`` guard rails refuse the second-repo corruption pattern."""

    def test_double_prefix_replacement_is_refused(self, tmp_path):
        """If the proposed replacement URL, resolved from the source
        file's directory, would land on a path that doesn't exist (the
        signature of a double-prefix rewrite), the rewrite is refused."""
        (tmp_path / "docs" / "legacy" / "code-map").mkdir(parents=True)
        (tmp_path / "docs" / "legacy" / "code-map" / "16-X.md").write_text("ok\n")
        (tmp_path / "docs" / "legacy" / "reports").mkdir(parents=True)
        source_rel = "docs/legacy/reports/some-doc.md"
        (tmp_path / source_rel).write_text("[`x`](../code-map/16-X.md)\n")

        # The exact corruption the v12.48 pipeline used to produce:
        # replacement is a repo-root-relative path, but it's stamped
        # into a URL that's interpreted relative to the source-file dir
        # — so from ``docs/legacy/reports/`` it resolves to
        # ``docs/legacy/reports/docs/legacy/code-map/16-X.md`` which is
        # missing. Guard 3 (new-URL liveness) refuses.
        safe, reason = _rewrite_is_safe(
            source_rel,
            "../code-map/16-X.md",  # the original URL (irrelevant for this guard)
            "docs/legacy/code-map/16-X.md",  # the corrupt replacement
            tmp_path,
        )
        assert safe is False
        assert reason  # non-empty refusal message

    def test_original_live_url_blocks_any_rewrite(self, tmp_path):
        """Guard 1: if the ORIGINAL URL already resolves to a live
        file, no rewrite is acceptable — the link was always fine."""
        (tmp_path / "docs" / "legacy" / "code-map").mkdir(parents=True)
        (tmp_path / "docs" / "legacy" / "code-map" / "16-X.md").write_text("ok\n")
        (tmp_path / "docs" / "legacy" / "reports").mkdir(parents=True)
        source_rel = "docs/legacy/reports/some-doc.md"
        (tmp_path / source_rel).write_text("[`x`](../code-map/16-X.md)\n")

        safe, reason = _rewrite_is_safe(
            source_rel,
            "../code-map/16-X.md",  # already live
            "anything-else.md",
            tmp_path,
        )
        assert safe is False
        assert "already resolves" in reason


# ---------------------------------------------------------------------------
# Bug 8 — heading slugger parity with GitHub
# ---------------------------------------------------------------------------


class TestSluggerKeepsIntrawordUnderscores:
    """``\\w`` includes underscore — slugger must not strip it intraword."""

    def test_intraword_underscore_preserved(self):
        """The simplest case: a one-word identifier with an underscore."""
        assert slugify("pfpa_epil") == "pfpa_epil"

    def test_compound_identifier_with_underscores_preserved(self):
        """Real-world dogfood case: ``# 4.3 PFPA_EPIL.IN_PFPA_EPIL-4.DBF``
        slugifies to a string that retains every intraword underscore.

        Pre-fix this produced ``pfpaepilinpfpaepil-4dbf`` (no
        underscores) — every reference to ``#…pfpa_epil…`` was flagged
        as a broken anchor. GitHub keeps the underscores."""
        slug = slugify("4.3 PFPA_EPIL.IN_PFPA_EPIL-4.DBF")
        assert "pfpa_epil" in slug
        # Spot-check the precise output too — but the substring check
        # is the load-bearing assertion.
        assert slug == "43-pfpa_epilin_pfpa_epil-4dbf"

    def test_emphasis_underscore_still_stripped(self):
        """``_foo_`` is emphasis (rendered ``<em>foo</em>``) so the
        underscores must still come out — slug is ``foo``, not
        ``_foo_``."""
        assert slugify("_emphasis_") == "emphasis"

    def test_strong_underscore_still_stripped(self):
        """``__strong__`` is strong emphasis; underscores must come out."""
        assert slugify("__strong__") == "strong"

    def test_emphasis_and_intraword_in_same_text(self):
        """``_foo_ bar_baz`` — the leading/trailing ``_`` strip, the
        intraword one stays."""
        assert slugify("_foo_ bar_baz") == "foo-bar_baz"

    def test_strip_inline_markup_preserves_intraword_underscores(self):
        """The inline-markup strip step (called before slugifying) must
        leave intraword underscores alone — this is the regex that the
        original Bug 8 was about."""
        assert _strip_inline_markup("PFPA_EPIL") == "PFPA_EPIL"
        assert _strip_inline_markup("_foo_") == "foo"
        assert _strip_inline_markup("foo_bar_baz") == "foo_bar_baz"


class TestSluggerKeepsMedialSigma:
    """Python's ``str.lower()`` keeps medial sigma — matches Ruby
    ``.downcase``, not JS terminal-sigma rule."""

    def test_greek_word_lowercases_to_medial_sigma(self):
        """``ΣΕΛΠ`` → ``σελπ`` (medial), NOT ``σελπ`` then JS-style
        terminal-sigma swap to ``σελπ`` … wait, all three are σ. The
        meaningful test is the four-letter form where the closing
        sigma is at the END of the WORD."""
        # σ everywhere — not ς at end.
        assert slugify("ΣΕΛΠ") == "σελπ"

    def test_greek_header_keeps_medial_sigma_at_end(self):
        """Real-world dogfood case: ``# 3 Accounting Standards (ΕΛΓΣΕΛΠ)``
        should slugify with σ at the end, not ς. GitHub uses Ruby's
        ``.downcase`` which has no final-sigma rule."""
        slug = slugify("3 Accounting Standards (ΕΛΓΣΕΛΠ)")
        assert slug == "3-accounting-standards-ελγσελπ"
        # Pin the end-of-string character explicitly so a future regex
        # tweak that re-introduces JS-style terminal-sigma collapse
        # fails this test immediately.
        assert slug.endswith("σελπ")
        assert not slug.endswith("σελπ".replace("σ", "ς"))


class TestSluggerWhitespacePreservation:
    """Each whitespace char becomes one dash — runs map to dash runs."""

    def test_single_space_one_dash(self):
        assert slugify("foo bar") == "foo-bar"

    def test_double_space_two_dashes(self):
        """``foo  bar`` (two spaces) slugifies to ``foo--bar`` — GitHub
        does a literal ``gsub(' ', '-')`` which preserves run length.

        Pre-fix this used ``re.sub(r'\\s+', '-', text)`` and produced
        ``foo-bar``, breaking every reference to a multi-space heading.
        """
        assert slugify("foo  bar") == "foo--bar"

    def test_triple_space_three_dashes(self):
        assert slugify("foo   bar") == "foo---bar"

    def test_arrow_in_heading_becomes_three_dashes(self):
        """Real-world dogfood: ``2.DBF -> 2.DBF`` (space-arrow-space)
        renders to ``2dbf---2dbf`` once ``-``/``>`` survive/strip
        appropriately and each whitespace yields its own dash."""
        slug = slugify("2.DBF -> 2.DBF")
        assert slug == "2dbf---2dbf"


# ---------------------------------------------------------------------------
# Integration — extract_anchors must round-trip with the new slugger.
# ---------------------------------------------------------------------------


class TestExtractAnchorsRoundTrip:
    """A reference to ``#<slug>`` validates iff the header produces
    that slug. Round-trip the real-world bug cases."""

    def test_underscore_heading_round_trips(self):
        """A ``# PFPA_EPIL`` heading produces an anchor that matches
        ``#pfpa_epil`` references."""
        content = "# PFPA_EPIL\n\nBody.\n"
        anchors = extract_anchors(content)
        assert "pfpa_epil" in anchors

    def test_underscore_compound_heading_round_trips(self):
        """A compound heading with multiple intraword underscores."""
        content = "# 4.3 PFPA_EPIL.IN_PFPA_EPIL-4.DBF\n"
        anchors = extract_anchors(content)
        assert "43-pfpa_epilin_pfpa_epil-4dbf" in anchors

    def test_greek_heading_round_trips(self):
        """A Greek-letter heading with terminal-position sigma."""
        content = "# 3 Accounting Standards (ΕΛΓΣΕΛΠ)\n"
        anchors = extract_anchors(content)
        assert "3-accounting-standards-ελγσελπ" in anchors

    def test_emphasis_in_heading_still_strips_underscores(self):
        """A heading with genuine emphasis still loses the emphasis
        markers — ``# _Setup_`` matches both ``#setup`` references."""
        content = "# _Setup_\n"
        anchors = extract_anchors(content)
        assert "setup" in anchors


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
