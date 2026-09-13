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
from html.parser import HTMLParser
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


class _HTMLAnchorParser(HTMLParser):
    """Collect global IDs and legacy named links, not markup-looking text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # The first occurrence of a duplicate attribute wins in HTML. ID is
        # global; name is an anchor only on <a>, not on e.g. <input> or <div>.
        for key in ("id", "name") if tag == "a" else ("id",):
            value = next((value for name, value in attrs if name == key), None)
            if value:
                self.anchors.add(value.strip().lower())


# Strip-out elements that markdown header text may contain — emphasis
# markers, code-spans, link wrappers — and collapse to plain text before
# slugifying. ``[text](url)`` becomes ``text``.
_LINK_RE = re.compile(r"\[(?P<inner>[^\]\n]+)\]\([^)\n]+\)")
# Asterisk emphasis (``*foo*`` / ``**foo**``) and inline code (`` `foo` ``)
# strip unconditionally — none of them are commonly intraword in
# identifiers, so removing them everywhere matches what GitHub renders.
_AST_BACKTICK_RE = re.compile(r"(\*\*|\*|`)")
# Underscore emphasis is a different beast: GitHub-flavoured Markdown
# (and CommonMark) only treats ``_`` as an emphasis marker when it's at
# a *left-flanking* or *right-flanking* position, i.e. adjacent to a
# non-word character (or string boundary) on at least one side.
# ``foo_bar_baz`` is NOT emphasis — the underscores are intraword and
# stay in the rendered HTML, which means GitHub's heading-id slugger
# preserves them too. The pre-fix regex ``(\*\*|__|\*|_|`)`` stripped
# every ``_`` indiscriminately, causing slugs like
# ``inv_no_in_inv_no-4-dbf`` to collapse to ``invnoinvno-4-dbf``
# and silently breaking every ``#inv_no`` reference on disk (Bug 8
# from an external dogfood report, May 2026). The two halves of this
# alternation strip ``_+`` only when at least one side is a non-word
# character — covering ``_foo_`` (emphasis), ``__foo__`` (strong),
# leading/trailing underscores around headers — but leaving every
# intraword ``_`` exactly where the author wrote it.
_UNDERSCORE_EMPHASIS_RE = re.compile(r"(?<!\w)_+|_+(?!\w)")


def _strip_inline_markup(text: str) -> str:
    """Reduce a header line to the visible text, ready for slugifying."""
    text = _LINK_RE.sub(lambda m: m.group("inner"), text)
    text = _AST_BACKTICK_RE.sub("", text)
    text = _UNDERSCORE_EMPHASIS_RE.sub("", text)
    return text


def slugify(text: str) -> str:
    """Convert header text to a GitHub-flavoured anchor slug.

    Rules (aligned with how GitHub renders ``id`` on headers as of
    2024+, via the Ruby ``html-pipeline`` / ``jch-algoritmo`` gem):

    * Lowercase via ``str.lower`` — Unicode-aware (``Ü`` → ``ü``) and
      crucially does NOT apply the JS-style terminal-sigma rule
      (``Σ`` → ``σ`` everywhere, never ``ς``). Pre-fix we relied on
      this implicitly; the comment below documents the invariant so a
      well-meaning refactor doesn't reach for ``unicodedata`` or
      ``casefold`` and silently regress non-Latin slugs.
    * Replace EACH whitespace character with one ``-`` (not runs with a
      single ``-``). GitHub's algorithm does a literal ``gsub(' ', '-')``;
      collapsing runs would change ``"foo  bar"`` from ``foo--bar`` to
      ``foo-bar`` and fail to match the deployed anchor (Bug 8 cousin).
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
    # Replace each whitespace char individually — runs map to runs of
    # dashes, matching GitHub's behaviour where multiple spaces in a
    # heading produce ``--`` / ``---`` in the slug.
    text = re.sub(r"\s", "-", text)
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
    fence_length = 0
    html_lines: list[str] = []

    def _add_slug(text: str) -> None:
        slug = slugify(text)
        if not slug:
            return
        # GitHub appends ``-1``, ``-2``, … on collision (case-insensitive).
        count = slug_counts.get(slug, 0)
        anchors.add(slug if count == 0 else f"{slug}-{count}")
        slug_counts[slug] = count + 1

    for idx, line in enumerate(lines):
        # A shorter fence or a same-marker line with trailing info does not
        # close a code sample. Keep examples out of BOTH anchor channels.
        fence = _FENCE_OPEN_RE.match(line)
        if fence:
            marker = fence.group("fence")[0]  # ` or ~
            if not in_fence:
                in_fence = True
                fence_marker = marker
                fence_length = len(fence.group("fence"))
            elif (
                fence_marker == marker and len(fence.group("fence")) >= fence_length and not line[fence.end() :].strip()
            ):
                in_fence = False
                fence_marker = None
            continue
        if in_fence:
            continue
        html_lines.append(line)

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

    # Parse attributes rather than regex-shaped tags: preserve quoted '>',
    # multiline/unquoted values and entities while ignoring comments and
    # script/style bodies. IDs keep the historical case-normalized contract.
    parser = _HTMLAnchorParser()
    parser.feed("\n".join(html_lines))
    parser.close()
    anchors.update(parser.anchors)
    return anchors


def _read_anchors_for(path: Path, max_bytes: int = 1_000_000) -> set[str] | None:
    """Read *path* and return its anchor set; ``None`` on read failure or oversize."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        # Bound the actual read too: a file may grow after stat(). Do not
        # treat a truncated prefix as a complete set of anchor definitions.
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        return extract_anchors(text)
    except (OSError, ValueError, AssertionError):
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

    def __init__(self, project_root: Path, *, max_bytes: int = 1_000_000) -> None:
        self._project_root = project_root
        self._max_bytes = max_bytes
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
        anchors = _read_anchors_for(full, max_bytes=self._max_bytes)
        self._cache[norm] = anchors
        return anchors

    @property
    def unreadable_paths(self) -> tuple[str, ...]:
        """Name attempted anchor reads that returned no measurement."""
        return tuple(sorted(path for path, anchors in self._cache.items() if anchors is None))

    @staticmethod
    def is_anchor_validatable(rel_path: str) -> bool:
        """Only validate anchors for prose-shaped files where slugs apply.

        HTML files use raw ``id="..."`` attributes which we already pick
        up via :class:`_HTMLAnchorParser`, so they're validatable too. Source
        files (``.py``, ``.ts``) almost never carry meaningful anchor
        targets and parsing them as markdown would surface false
        positives, so they're excluded here.
        """
        ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
        return ext in {"md", "markdown", "rst", "html", "htm"}
