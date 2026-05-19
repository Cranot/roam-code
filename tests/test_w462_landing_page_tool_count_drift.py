"""W462 — drift-guard for MCP tool-count integers on landing-page assets.

W458 audited the landing-page HTML and found two count integers
(``57`` core preset / ``224`` full registry) repeated across multiple
pages. The earlier-today W462 fix-forward (224 → 227) was a hand-rolled
3-file edit (``index.html`` + ``press.html`` + ``llms.txt``) — a classic
3+ batch manual campaign with no structural guard.

Per CLAUDE.md "Drift-guard with campaign" rule, this test was extended
to scan ALL landing-page assets (``*.html`` / ``*.txt`` / ``*.md``)
recursively and pin every tool-count claim against the canonical
AST-derived counts from :func:`roam.surface_counts.mcp_surface_counts`
(``_CORE_TOOLS`` + the ``@_tool(name=...)`` decorator scan). Any future
preset/registry change flips a single, clear failure here.

Allowlist policy. Two kinds of legitimate references are exempted:

1. ``changelog.html`` is exempted in full — it is an append-only
   release-history document. Every count there is intentionally
   historical (e.g. ``"224 MCP tools (was: 137)"``,
   ``"35 MCP tools (was 33)"``).

2. Per-line transitional-context markers — when a count appears on a
   line containing ``was``, ``previously``, ``earlier``, ``→`` (or
   ``-&gt;`` HTML-escaped), ``from N to``, ``old:``, etc., the match
   is treated as documenting drift rather than asserting truth. This
   matters when a non-changelog file legitimately quotes a past state.

Lightweight by design: no auto-wire substrate (W460 deferred).
"""

from __future__ import annotations

import re

from tests._helpers.repo_root import repo_root

ROOT = repo_root()

# Scope: every static asset shipped under the landing-page tree that
# might quote a tool count. ``*.json`` is intentionally out of scope
# (.well-known/mcp-server-card.json is structurally-checked elsewhere).
_LANDING_DIR = ROOT / "templates" / "distribution" / "landing-page"
_FILE_SUFFIXES = (".html", ".txt", ".md")

# Full-file exemptions: append-only release-history documents whose
# entire purpose is to record historical counts. Every count there is
# intentionally drift documentation, not a present-tense claim.
_EXEMPT_FILES = frozenset(
    {
        "changelog.html",
    }
)

# Per-line transitional-context markers. A scraped count whose line
# contains any of these tokens is treated as historical (e.g. a release
# note inside a non-changelog file). Case-insensitive substring match.
_TRANSITION_MARKERS = (
    " was ",
    "(was ",
    "(was:",
    "was: ",
    "previously",
    "earlier",
    "before ",
    " from ",
    " → ",
    "-&gt;",
    "->",
    "old:",
    "stale",
    "outdated",
    "deprecated",
    "historical",
    "legacy",
    " up from ",
    " grew to ",
    " expanded ",
)

# MCP-tool-count phrase regex. Permissive (case-insensitive, hyphen
# tolerant) so we catch the variants seen across the landing-page tree
# without re-introducing the W462 leak class.
#
# Captured shapes (current tree, all matching):
#   - "227 MCP tools"
#   - "227 MCP-tools"  (hyphen variant)
#   - "227 tools registered"
#   - "227 tools (..."  (parenthetical preset annotation)
#   - "57 tools plus"
#   - "57 core / 227 full preset tools"
#   - "57 core agent tools"
#   - "57 core structured questions"
#   - "227 total MCP tools"
#   - "57-tool core preset"
#   - "227 tool wrappers"
#   - "in <code>full</code>"  (HTML preset-name suffix)
#   - "227 in the default core preset"
#
# Multiline note: re.DOTALL is OFF so ``\s+`` matches inline whitespace
# but not arbitrary line spans. ``\n`` IS allowed inside the same
# regex segment via ``\s`` -- llms.txt has "227 MCP\ntools" wrapped
# across a line boundary, and we want to catch it.
_MCP_NUM = re.compile(
    r"""(?ix)\b(\d{2,4})(?:[\s\-/]+)(?:
        core\s+(?:agent\s+)?tools?
      | core\s+structured\s+questions
      | core\s*/?\s*\d+
      | tools?\s+plus
      | tools?\s+registered
      | tools?\s+\(
      | tool\s+wrappers?
      | (?:total\s+)?MCP[\s\-]+tools?
      | full\s+preset\s+tools?
      | in\s+<code>full</code>
      | tool\s+core\s+preset
      | in\s+the\s+default\s+core\s+preset
    )""",
)
# HTML-wrapped variant: <strong>227</strong> MCP tools / <strong>57</strong> tools
_MCP_HTML = re.compile(
    r"<strong>(\d{2,4})</strong>\s+(?:MCP\s+)?tools?",
    re.IGNORECASE,
)


def _iter_landing_files():
    """Yield (rel_path, abs_path) for every in-scope asset under landing-page."""
    for path in sorted(_LANDING_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _FILE_SUFFIXES:
            continue
        if path.name in _EXEMPT_FILES:
            continue
        yield path.relative_to(ROOT).as_posix(), path


def _line_is_transitional(line: str) -> bool:
    """True iff this line carries a transitional-context marker.

    Case-insensitive substring match; any one marker is enough. The
    intent: a count next to "was", "previously", "→" etc. is
    documenting drift, not asserting present-tense truth.
    """
    lower = line.lower()
    return any(marker in lower for marker in _TRANSITION_MARKERS)


def _scrape_counts(text: str):
    """Yield (lineno, line, scraped_int, raw_match) for every count phrase."""
    # Build a line index so we can map match offsets to (lineno, line_text).
    line_starts = [0]
    for m in re.finditer(r"\n", text):
        line_starts.append(m.end())
    line_starts.append(len(text) + 1)

    def lineno_of(offset: int) -> tuple[int, str]:
        # Binary search would be faster but the doc count is tiny.
        for i in range(len(line_starts) - 1):
            if line_starts[i] <= offset < line_starts[i + 1]:
                start = line_starts[i]
                end = line_starts[i + 1] - 1
                return i + 1, text[start:end]
        return -1, ""

    for m in _MCP_NUM.finditer(text):
        n = int(m.group(1))
        lineno, line = lineno_of(m.start())
        yield lineno, line, n, m.group(0)
    for m in _MCP_HTML.finditer(text):
        n = int(m.group(1))
        lineno, line = lineno_of(m.start())
        yield lineno, line, n, m.group(0)


def test_landing_page_mcp_tool_counts_match_canonical():
    """Every present-tense MCP-tool-count claim on the landing-page must match canonical."""
    from roam.surface_counts import mcp_surface_counts

    counts = mcp_surface_counts()
    core, full = counts["core_tools"], counts["registered_tools"]
    canonical = {core, full}

    failures: list[str] = []
    scanned_files = 0
    total_matches = 0
    skipped_transitional = 0

    for rel, path in _iter_landing_files():
        scanned_files += 1
        text = path.read_text(encoding="utf-8")
        for lineno, line, n, raw in _scrape_counts(text):
            total_matches += 1
            if _line_is_transitional(line):
                skipped_transitional += 1
                continue
            if n not in canonical:
                failures.append(
                    f"{rel}:{lineno}: scraped {n} not in "
                    f"{{core={core}, full={full}}} via {raw!r} "
                    f"-- expected {core} (core) or {full} (full); "
                    f"update the page or refresh from `roam surface --json`."
                )

    # Sanity: the walk must have found SOMETHING. A silent empty walk
    # (e.g. wrong path after a directory rename) would let drift slip
    # through unnoticed -- the W462 leak class itself.
    assert scanned_files > 0, (
        f"No landing-page assets scanned under {_LANDING_DIR}; check the path or update _LANDING_DIR."
    )
    assert total_matches > 0, (
        "Scanned landing-page assets but matched zero tool-count phrases; "
        "the regex may have regressed -- check the current tree manually."
    )

    assert not failures, (
        "Landing-page MCP-tool-count drift (canonical: "
        f"core={core}, full={full}; transitional refs skipped: "
        f"{skipped_transitional}):\n  " + "\n  ".join(failures)
    )


def test_landing_page_drift_guard_actually_catches_drift(tmp_path):
    """Sanity: prove the assertion would fire if drift were introduced.

    Writes a synthetic landing-page asset containing a deliberately
    wrong count and asserts the scrape+compare pipeline flags it.
    Mirrors the production scrape logic (regex + transitional-marker
    skip) but on an isolated fixture so the real tree stays untouched.
    """
    from roam.surface_counts import mcp_surface_counts

    counts = mcp_surface_counts()
    core, full = counts["core_tools"], counts["registered_tools"]
    canonical = {core, full}

    # Synthesise drift: pick an integer that is NOT either canonical
    # count. Using `full + 1` is a safe choice (>= 3 digits, never
    # collides with the live values).
    drifted = full + 1
    fixture = tmp_path / "fake_landing.html"
    fixture.write_text(
        f"<p><strong>{drifted}</strong> MCP tools (default core preset)</p>\n"
        f"<p>Previously {drifted - 100} MCP tools, was: {drifted - 50} earlier.</p>\n",
        encoding="utf-8",
    )

    text = fixture.read_text(encoding="utf-8")
    drift_hits = []
    transitional_hits = []
    for lineno, line, n, raw in _scrape_counts(text):
        if _line_is_transitional(line):
            transitional_hits.append((lineno, n, raw))
            continue
        if n not in canonical:
            drift_hits.append((lineno, n, raw))

    # The first line must trip the drift detector; the second line
    # ("Previously ..." / "was: ... earlier") must be skipped via the
    # transitional-marker allowlist even though it also carries a
    # wrong count.
    assert drift_hits, (
        f"Drift sanity-check failed: synthetic drift {drifted} on line 1 "
        "was not flagged by the scrape pipeline. The drift-guard would "
        "miss real regressions."
    )
    assert transitional_hits, (
        "Transitional-marker allowlist failed: the 'Previously ... was: "
        "... earlier' line should have been skipped, but no transitional "
        "hits were recorded."
    )
