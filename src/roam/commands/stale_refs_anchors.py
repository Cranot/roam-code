"""Markdown anchor extraction + validation for ``stale-refs``.

A reference like ``[deploy](docs/cd.md#cloudflare-pages)`` can be broken
two ways: the file may not exist (caught by the path resolvers in
:mod:`cmd_stale_refs`), OR the file exists but the ``#cloudflare-pages``
anchor doesn't. This module covers the second case.

Anchor slug rules follow GitHub-flavoured Markdown — the dominant flavour
on GitHub, GitLab, MkDocs (mostly), and Hugo. We deliberately do NOT
support every flavour out there; the goal is to cover the 95% case
without false positives. Extension points for other flavours are noted
inline.

Headers vs HTML id anchors
--------------------------

We accept anchors that match either:

* A markdown header slug (``# Cloudflare Pages`` → ``cloudflare-pages``).
* An explicit HTML id attribute (``<h2 id="custom">…``,
  ``<a id="custom">…``, ``<a name="custom">…``).

This catches handwritten ``id=""`` anchors that some doc systems sprinkle
into prose.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

# ``# Heading``, ``## Heading``, … up to 6 levels. Supports trailing
# ``#``s used in some setups (``## Heading ##``). Inline links and
# emphasis stay as raw text — slugify strips the markdown punctuation.
_ATX_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(?P<text>.+?)[ \t]*#*[ \t]*$")

# Setext-style headers — ``Heading`` followed by ``===`` or ``---``.
# We catch them by looking back one line when we see the underline.
_SETEXT_UNDERLINE_RE = re.compile(r"^[ \t]{0,3}(=+|-+)[ \t]*$")

# Fenced code blocks (```...``` or ~~~...~~~). Lines inside a fence are
# verbatim — ``# Not a header`` in a code sample must NOT register as an
# anchor target. We detect opening/closing fences by line shape and skip
# header parsing while inside.
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")

# Explicit HTML id / name attribute anchors that markdown libraries
# preserve verbatim.
_HTML_ID_RE = re.compile(
    r"""<\s*(?:h[1-6]|a|div|section|span|p)\b[^>]*?
        \s(?:id|name)\s*=\s*(?:"(?P<dq>[^"]+)"|'(?P<sq>[^']+)')""",
    re.IGNORECASE | re.VERBOSE,
)

# Strip-out elements that markdown header text may contain — emphasis
# markers, code-spans, link wrappers — and collapse to plain text before
# slugifying. ``[text](url)`` becomes ``text``.
_LINK_RE = re.compile(r"\[(?P<inner>[^\]\n]+)\]\([^)\n]+\)")
_INLINE_FORMATTING_RE = re.compile(r"(\*\*|__|\*|_|`)")


def _strip_inline_markup(text: str) -> str:
    """Reduce a header line to the visible text, ready for slugifying."""
    text = _LINK_RE.sub(lambda m: m.group("inner"), text)
    text = _INLINE_FORMATTING_RE.sub("", text)
    return text


def slugify(text: str) -> str:
    """Convert header text to a GitHub-flavoured anchor slug.

    Rules (aligned with how GitHub renders ``id`` on headers as of
    2024+):

    * Lowercase (``str.lower`` is Unicode-aware: ``Ü`` → ``ü``).
    * Replace runs of whitespace with a single ``-``.
    * Drop characters that aren't word characters (``\\w``, which
      Python 3 treats as Unicode-aware: includes letters from any
      language plus digits and underscore) or hyphen — emojis,
      punctuation, etc. fall out.
    * Trim leading/trailing dashes.

    Unicode letters are preserved so a header ``# Über`` produces slug
    ``über`` and a reference ``#über`` validates against it. Pre-polish
    we used ``[a-z0-9_\\-]`` which silently dropped accented letters and
    CJK characters — references to non-English headers always failed.
    """
    text = _strip_inline_markup(text)
    text = text.lower()
    text = re.sub(r"\s+", "-", text)
    # ``\w`` is Unicode-aware in Python 3 by default (no flag needed) and
    # matches any Unicode letter, digit, or underscore. Combined with the
    # explicit hyphen, we drop only emojis and punctuation.
    text = re.sub(r"[^\w\-]+", "", text, flags=re.UNICODE)
    text = text.strip("-")
    return text


def extract_anchors(content: str) -> set[str]:
    r"""Return the set of valid ``#anchor`` slugs declared by *content*.

    Stored slugs are lowercased so callers can match case-insensitively
    (GitHub matches ``#Setup`` against header ``# Setup`` regardless of
    case). The set includes:

    * Header-derived slugs (ATX + setext), with GitHub-style duplicate
      suffixes (``setup``, ``setup-1``, ``setup-2``, …) when the same
      heading text appears twice or more.
    * Explicit HTML ``id``/``name`` attributes, lowercased for symmetry.

    Lines inside fenced code blocks (triple-backtick or ``~~~``) are
    skipped — ``# Not a header`` inside a code sample must NOT
    contaminate the anchor set.
    """
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    lines = content.splitlines()
    in_fence = False
    fence_marker: str | None = None

    def _add_slug(text: str) -> None:
        slug = slugify(text)
        if not slug:
            return
        # GitHub appends ``-1``, ``-2``, … on collision (case-insensitive).
        count = slug_counts.get(slug, 0)
        anchors.add(slug if count == 0 else f"{slug}-{count}")
        slug_counts[slug] = count + 1

    for idx, line in enumerate(lines):
        # Fence open/close — entry/exit only on lines that start a fence
        # of length >= the opener's length. We approximate with the
        # simpler "any fence-shaped line toggles the state" rule, which
        # is correct for the 99% case of well-formed markdown.
        fence = _FENCE_OPEN_RE.match(line)
        if fence:
            marker = fence.group("fence")[0]  # ` or ~
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif fence_marker == marker:
                in_fence = False
                fence_marker = None
            continue
        if in_fence:
            continue

        # ATX-style: ``# Heading`` … ``###### Heading``
        m = _ATX_HEADER_RE.match(line)
        if m:
            _add_slug(m.group("text"))
            continue
        # Setext-style: previous non-blank line is the heading text.
        if _SETEXT_UNDERLINE_RE.match(line) and idx > 0:
            prev = lines[idx - 1].strip()
            if prev:
                _add_slug(prev)

    # Inline HTML ids — scan the whole content (multi-line spans
    # included), lowercased for case-insensitive lookup symmetry. We do
    # NOT apply duplicate suffixing here because raw ``id`` attributes
    # are author-controlled; if two ``id="foo"`` appear in one file
    # that's the author's bug, not ours to mimic.
    for m in _HTML_ID_RE.finditer(content):
        anchor = m.group("dq") or m.group("sq")
        if anchor:
            anchors.add(anchor.strip().lower())
    return anchors


def _read_anchors_for(path: Path, max_bytes: int = 1_000_000) -> set[str] | None:
    """Read *path* and return its anchor set; ``None`` on read failure or oversize."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return extract_anchors(fh.read())
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Cache facade
# ---------------------------------------------------------------------------


class AnchorCache:
    """Memoise anchor extraction across one ``stale-refs`` invocation.

    A single ``README.md`` may be referenced from dozens of places via
    different ``#anchor`` fragments; we re-parse the file once. The cache
    is per-invocation — there's no on-disk persistence — because anchor
    extraction is cheap and the tradeoff isn't worth the staleness risk.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._cache: dict[str, set[str] | None] = {}

    def anchors_for(self, rel_path: str) -> set[str] | None:
        """Return the anchor set for *rel_path*, parsing on first hit.

        Returns ``None`` when the file can't be read; callers should
        treat that as "we can't validate anchors for this file" and skip
        emitting an anchor finding rather than fabricating one.
        """
        norm = rel_path.replace("\\", "/")
        if norm in self._cache:
            return self._cache[norm]
        full = self._project_root / norm
        anchors = _read_anchors_for(full)
        self._cache[norm] = anchors
        return anchors

    @staticmethod
    def is_anchor_validatable(rel_path: str) -> bool:
        """Only validate anchors for prose-shaped files where slugs apply.

        HTML files use raw ``id="..."`` attributes which we already pick
        up via :data:`_HTML_ID_RE`, so they're validatable too. Source
        files (``.py``, ``.ts``) almost never carry meaningful anchor
        targets and parsing them as markdown would surface false
        positives, so they're excluded here.
        """
        ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
        return ext in {"md", "markdown", "rst", "html", "htm"}
