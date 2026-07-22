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
      | tools?\s+\([^\n)]{0,80}\)
      | tool\s+wrappers?
      | (?:total\s+)?MCP[\s\-]+tools?
      | full\s+preset\s+tools?
      | in\s+<code>full</code>
      | in\s+the\s+(?:default\s+)?(?:<code>)?core(?:</code>)?\s+preset
      | tool\s+core\s+preset
      | in\s+the\s+default\s+core\s+preset
    )""",
)
# HTML-wrapped variants: direct text and the complete label span used by index.html.
_MCP_HTML_DIRECT = re.compile(
    r"<strong>(\d{2,4})</strong>\s*((?:MCP\s+)?tools?[^<\n]{0,80})",
    re.IGNORECASE,
)
_MCP_HTML_SPAN = re.compile(
    r"<strong>(\d{2,4})</strong>\s*<span[^>]*>([^<\n]{0,120})</span>",
    re.IGNORECASE,
)
_MCP_CORE_FULL = re.compile(
    r"\b(\d{2,4})\s+core\s*/\s*(\d{2,4})\s+full\s+preset\s+tools?",
    re.IGNORECASE,
)
_MCP_TABLE_LABEL_VALUE = re.compile(
    r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>\s*<(?:td|th)[^>]*>\s*(\d{2,4})\s*</(?:td|th)>",
    re.IGNORECASE,
)
_MCP_VISIBLE_ROLE_PATTERNS = (
    (re.compile(r"\b(\d{2,4})\s+in\s+the\s+default\s+core\s+preset\b", re.IGNORECASE), "core"),
    (re.compile(r"\b(\d{2,4})\s+tools?\s+plus\b", re.IGNORECASE), "core"),
    (re.compile(r"\ball\s+(\d{2,4})\s+tools?\b", re.IGNORECASE), "full"),
    (re.compile(r"\b(\d{2,4})\s+tools?\s+registered\b", re.IGNORECASE), "full"),
    (re.compile(r"\b(\d{2,4})\s+MCP\s+tools?\b", re.IGNORECASE), "full"),
    (
        re.compile(
            r"\b(\d{2,4})\s+tools?\b(?=[^\n]{0,100}(?:preset\s*=\s*[\"']?full|full\s+set))",
            re.IGNORECASE,
        ),
        "full",
    ),
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


def _expected_role(raw_match: str) -> str | None:
    """Infer a closed preset role whenever the matched phrase states one."""
    phrase = re.sub(r"<[^>]+>", " ", raw_match).lower()
    if "mcp tools" in phrase and len(re.findall(r"\b\d{2,4}\b", phrase)) > 1:
        # ``244 MCP tools (16 in the default core preset)`` gives each count
        # its own label; the later core qualifier belongs to the second count.
        return "full"
    if "core" in phrase or "tools plus" in phrase:
        return "core"
    if any(marker in phrase for marker in ("full", "mcp", "registered", "wrappers")):
        return "full"
    return None


def _scrape_counts(text: str):
    """Yield (lineno, line, scraped_int, raw_match, expected_role)."""
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

    # Capture both integers in compound claims. The generic regex consumes the
    # full phrase after matching the core count, so a non-overlapping second
    # search would otherwise never inspect the full-preset count.
    for m in _MCP_CORE_FULL.finditer(text):
        lineno, line = lineno_of(m.start())
        yield lineno, line, int(m.group(1)), f"{m.group(1)} core", "core"
        yield lineno, line, int(m.group(2)), f"{m.group(2)} full preset tools", "full"
    for m in _MCP_NUM.finditer(text):
        n = int(m.group(1))
        lineno, line = lineno_of(m.start())
        raw = m.group(0)
        yield lineno, line, n, raw, _expected_role(raw)
    for pattern in (_MCP_HTML_DIRECT, _MCP_HTML_SPAN):
        for m in pattern.finditer(text):
            label = m.group(2)
            if "tool" not in label.lower() and "mcp" not in label.lower():
                continue
            n = int(m.group(1))
            lineno, line = lineno_of(m.start())
            raw = f"{m.group(1)} {label}"
            yield lineno, line, n, raw, _expected_role(raw)
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _MCP_TABLE_LABEL_VALUE.finditer(line):
            label = re.sub(r"<[^>]+>", " ", m.group(1)).replace("`", " ")
            if "tool" not in label.lower() and "mcp" not in label.lower():
                continue
            yield lineno, line, int(m.group(2)), f"{label} => {m.group(2)}", _expected_role(label)
        visible = re.sub(r"<[^>]+>", " ", line).replace("`", " ")
        visible = " ".join(visible.split())
        for pattern, role in _MCP_VISIBLE_ROLE_PATTERNS:
            for m in pattern.finditer(visible):
                yield lineno, line, int(m.group(1)), m.group(0), role


def test_landing_page_mcp_tool_counts_match_canonical():
    """Every present-tense MCP-tool-count claim on the landing-page must match canonical."""
    from roam.surface_counts import mcp_preset_counts, mcp_surface_counts

    counts = mcp_surface_counts()
    core, full = mcp_preset_counts()["core"], counts["registered_tools"]
    canonical = {core, full}

    failures: list[str] = []
    scanned_files = 0
    total_matches = 0
    skipped_transitional = 0

    for rel, path in _iter_landing_files():
        scanned_files += 1
        text = path.read_text(encoding="utf-8")
        for lineno, line, n, raw, expected_role in _scrape_counts(text):
            total_matches += 1
            if _line_is_transitional(line):
                skipped_transitional += 1
                continue
            expected_count = core if expected_role == "core" else full if expected_role == "full" else None
            if (expected_count is not None and n != expected_count) or (expected_count is None and n not in canonical):
                failures.append(
                    f"{rel}:{lineno}: scraped {n} does not match "
                    f"{{core={core}, full={full}}} via {raw!r} "
                    f"-- expected role={expected_role or 'either'} count="
                    f"{expected_count if expected_count is not None else canonical}; "
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
        f"<p>{core} core / {drifted} full preset tools</p>\n"
        f'<p data-case="swapped">{full} core / {core} full preset tools</p>\n'
        f'<p data-case="duplicated">{core} core / {core} full preset tools</p>\n'
        f'<p data-case="explicit-core">{full} core agent tools</p>\n'
        f'<p data-case="explicit-full">{core} total MCP tools</p>\n'
        f'<p data-case="span-markup"><strong>{drifted}</strong><span>MCP tools for agents</span></p>\n'
        f'<p data-case="span-core-swap"><strong>{full}</strong><span>core agent tools</span></p>\n'
        f'<p data-case="span-full-swap"><strong>{core}</strong><span>tools registered</span></p>\n'
        f'<p data-case="span-core-suffix"><strong>{full}</strong><span>MCP tools in core preset</span></p>\n'
        f'<p data-case="paren-core">{full} tools (default core preset)</p>\n'
        f'<table><tr data-case="table-full"><td>MCP tools registered</td><td>{core}</td></tr></table>\n'
        f'<table><tr data-case="table-core"><td>MCP tools in <code>core</code> preset</td><td>{full}</td></tr></table>\n'
        f'<p data-case="bare-full">{core} tools. Set <code>ROAM_MCP_PRESET=full</code>.</p>\n'
        f'<p data-case="all-full">One JSON shape across all {core} tools.</p>\n'
        f'<p data-case="markup-core">{full} in the default <code>core</code> preset.</p>\n'
        f'<p data-case="wrapped-markup-core">{full} in the\n'
        "default <code>core</code> preset.</p>\n"
        f"<p>Previously {drifted - 100} MCP tools, was: {drifted - 50} earlier.</p>\n",
        encoding="utf-8",
    )

    text = fixture.read_text(encoding="utf-8")
    drift_hits = []
    transitional_hits = []
    for lineno, line, n, raw, expected_role in _scrape_counts(text):
        if _line_is_transitional(line):
            transitional_hits.append((lineno, n, raw))
            continue
        expected_count = core if expected_role == "core" else full if expected_role == "full" else None
        if (expected_count is not None and n != expected_count) or (expected_count is None and n not in canonical):
            drift_hits.append((lineno, line, n, raw, expected_role))

    # The synthetic current claims must trip the detector, including swapped
    # and duplicated canonical values. The historical line must still be
    # skipped by the transitional-marker allowlist.
    assert drift_hits, (
        f"Drift sanity-check failed: synthetic drift {drifted} on line 1 "
        "was not flagged by the scrape pipeline. The drift-guard would "
        "miss real regressions."
    )
    assert any(n == drifted and role == "full" for _lineno, _line, n, _raw, role in drift_hits), (
        "Compound core/full claims must validate the full-preset integer independently"
    )
    assert any('data-case="swapped"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="duplicated"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="explicit-core"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="explicit-full"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="span-markup"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="span-core-swap"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="span-full-swap"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="span-core-suffix"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="paren-core"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="table-full"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="table-core"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="bare-full"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="all-full"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="markup-core"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert any('data-case="wrapped-markup-core"' in line for _lineno, line, _n, _raw, _role in drift_hits)
    assert transitional_hits, (
        "Transitional-marker allowlist failed: the 'Previously ... was: "
        "... earlier' line should have been skipped, but no transitional "
        "hits were recorded."
    )


def test_homepage_repeated_github_star_claims_are_consistent():
    text = (_LANDING_DIR / "index.html").read_text(encoding="utf-8")
    claims = [int(value) for value in re.findall(r"\b(\d{2,6})\s+GitHub stars\b", text)]
    claims.extend(int(value) for value in re.findall(r"<strong>(\d{2,6})</strong><span>GitHub stars</span>", text))

    assert len(claims) >= 2
    assert len(set(claims)) == 1, f"Contradictory GitHub star claims: {claims}"
