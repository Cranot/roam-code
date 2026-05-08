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

    Rules (approximate but aligned with how GitHub renders ``id`` on
    headers as of 2024+):

    * Lowercase.
    * Replace runs of whitespace with a single ``-``.
    * Drop characters outside ``[a-z0-9_\\-]`` (emojis, punctuation, etc.).
    * Trim leading/trailing dashes.

    Unicode letters that aren't ASCII are dropped — GitHub keeps them in
    practice but the resulting slug isn't very predictable; agents linking
    in English-language READMEs almost never need them.
    """
    text = _strip_inline_markup(text)
    text = text.lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9_\-]+", "", text)
    text = text.strip("-")
    return text


def extract_anchors(content: str) -> set[str]:
    """Return the set of valid ``#anchor`` slugs declared by *content*.

    Includes both header-derived slugs and any explicit HTML
    ``id``/``name`` attributes. Multiple headers slugifying to the same
    string just collapse to one entry — we don't model GitHub's numeric
    suffix scheme (``-1``, ``-2``) because most prose links don't rely on
    it, and counting collisions across an entire repo is fragile.
    """
    anchors: set[str] = set()
    lines = content.splitlines()

    for idx, line in enumerate(lines):
        # ATX-style: ``# Heading`` … ``###### Heading``
        m = _ATX_HEADER_RE.match(line)
        if m:
            slug = slugify(m.group("text"))
            if slug:
                anchors.add(slug)
            continue
        # Setext-style: previous non-blank line is the heading text.
        if _SETEXT_UNDERLINE_RE.match(line) and idx > 0:
            prev = lines[idx - 1].strip()
            if prev:
                slug = slugify(prev)
                if slug:
                    anchors.add(slug)

    # Inline HTML ids — scan the whole content (multi-line spans included).
    for m in _HTML_ID_RE.finditer(content):
        anchor = m.group("dq") or m.group("sq")
        if anchor:
            anchors.add(anchor.strip())
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
