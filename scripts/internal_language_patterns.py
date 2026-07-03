"""Single source of truth for the anti-leak internal-language pattern catalogue.

Stdlib-only (imports ``re`` only — NO pytest, NO ``roam`` imports). This
module is imported by BOTH the CI gate (``tests/test_no_internal_language.py``)
AND the commit/push-time hook CLI (``scripts/scan_internal_language.py``), so
the forbidden-pattern definitions live in exactly ONE place.

Root cause this addresses: the anti-leak gate previously lived only in the
pytest suite, which ran in CI. With no installed git hook, leaks reached the
PUBLIC repo before CI caught them. Extracting the catalogue here lets a
stdlib-only hook scan staged changes at commit time and the full tree at push
time, with no third-party dependency and no ``roam`` index build.

Whitelisted contexts (intentional uses):
- The CI test file itself (it owns the patterns it forbids).
- ``src/roam/security/aibom_extension.py`` and the ``test_ai_ratio.py`` /
  ``test_v12_2.py`` test fixtures: they describe and detect AI-authorship
  trailers as a product feature, not as a session signature.
"""

from __future__ import annotations

import re

# Path-loaded consumers access this module dynamically; keep the public
# catalogue contract explicit for static export audits.
__all__ = (
    "EXCLUDED_DIRS",
    "FORBIDDEN_PATTERNS",
    "SCAN_EXTENSIONS",
    "WHITELIST_FILES",
    "scan_text",
    "should_scan",
)

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Session-pass numbering ("Pass 79 — deprecated commands")
    ("Pass NN session marker", re.compile(r"\bPass \d+ — ")),
    # Letter-coded session markers ("R5 (2026-05-07) — ", "X14 (2026-05-06):")
    ("Letter-coded session marker", re.compile(r"\b[A-Z]{1,2}\d+ \(\d{4}-\d{2}-\d{2}\)")),
    # "(round 4 #15)" / "(round 3 #2 noted that)"
    ("Round-numbered session marker", re.compile(r"\(round \d+ #\d+")),
    # "Phase 0/1 of v2 monetization plan"
    ("v2 monetization plan reference", re.compile(r"Phase \d+(?:\.\d+)? of (?:the )?v2 monetization plan")),
    # "(per build_priorities.md)" / "(per internal backlog)"
    (
        "Internal-doc cross-reference",
        re.compile(r"\(per (?:build_priorities\.md|dev/CODE-BACKLOG\.md|the v\d+ plan)\)"),
    ),
    # "monetization_v2_subscription_pivot.md" filename references
    ("Monetization v2 strategy filename", re.compile(r"\bmonetization_v2_subscription_pivot\.md\b")),
    # "dogfood notes 2026-05-XX" / "dogfood R17 2026-05-01"
    (
        "Dogfood-notes session marker",
        re.compile(r"\bdogfood notes \d{4}-\d{2}-\d{2}\b|\bdogfood R\d+ \d{4}-\d{2}-\d{2}"),
    ),
    # Dated dogfood markers in ANY adjacency form: "(2026-05-XX dogfood)",
    # "(2026-06-10 dogfood: ...)", "dogfood 2026-05-04 —", "2026-06-07
    # dogfood:". The original paren-only pattern missed every variant with
    # trailing text; the separator-gap form catches them while leaving
    # "internal/dogfood/<FILE>-<date>.md" path mentions to the memo-filename
    # pattern below (letters in the gap don't match).
    (
        "Dated dogfood parenthetical",
        re.compile(r"\d{4}-\d{2}-\d{2}[ ,:;)]{0,3}dogfood\b|\bdogfood\b[ ,:;(]{0,3}\d{4}-\d{2}-\d{2}"),
    ),
    # Personal local-machine paths
    ("Windows personal path", re.compile(r"C:\\Users\\Dimitris|D:\\OneDrive - CosmoHac")),
    # Real customer name (the user's day-job employer)
    ("Day-job customer name", re.compile(r"\bunion[- ]web\b|\bSecond-Repo\b", re.IGNORECASE)),
    # Greek-domain example identifiers that leaked from the same day-job
    # dogfood corpus as the customer name. Each one is a real symbol from
    # the customer codebase and immediately identifies the project to anyone
    # familiar with the Greek B2B accounting domain.
    (
        "Greek domain term — kiniseis",
        re.compile(r"\bkiniseis\b|\bKiniseis\b|\buseKiniseisBalance\b", re.IGNORECASE),
    ),
    (
        "Greek domain term — ergani",
        re.compile(r"\bergani\b", re.IGNORECASE),
    ),
    (
        "Greek domain term — pfpa",
        re.compile(r"\bpfpa(_epil)?\b", re.IGNORECASE),
    ),
    (
        "Greek domain term — bebaioseis",
        re.compile(r"\bbebaioseis\b", re.IGNORECASE),
    ),
    # Standalone ``AFM`` (Greek tax-ID abbreviation). Allowed as part of
    # arbitrary identifiers like ``AFM_xyz`` or ``provider_afm`` — the
    # negative lookahead admits ``[`` for placeholder use, ``.``/``_``/``-``
    # for code-style identifiers, and stops on a real standalone abbrev.
    (
        "Greek tax-ID standalone abbreviation",
        re.compile(r"\bAFM\b(?![._\-])"),
    ),
    # Internal session reports
    (
        "Internal session report filename",
        re.compile(
            r"\bOVERNIGHT-\d{4}-\d{2}-\d{2}\.md\b|\bDOGFOOD-RESULTS-\d{4}-\d{2}-\d{2}\.md\b|"
            r"\bREPORT-\d{4}-\d{2}-\d{2}(?:-round\d+)?\.md\b|\bRELEASE-CHECKLIST\.md\b"
        ),
    ),
    # Internal claude-memory paths
    ("Claude-memory path", re.compile(r"~/\.claude/projects/D--OneDrive---CosmoHac-")),
    # Old GitHub Pages docs URL (we migrated to roam-code.com/docs/)
    ("Old GH Pages docs URL", re.compile(r"https?://cranot\.github\.io/roam-code/")),
    # CFO-objection sales-pitch script
    (
        "CFO-objection script",
        re.compile(
            r"signed PO by Friday|highest-conversion buyer-meeting|Article-12-curious leads|"
            r"Hosted-product Phase 0 helper"
        ),
    ),
    # Monetization-v2 phrasing leftovers
    ("Monetization-v2 leftover", re.compile(r"\bv2-monetization\b|\bv2 monetization layer\b")),
    # Greek-vendor exclusion clause (Union conflict-of-interest list)
    ("Greek-vendor exclusion clause", re.compile(r"Greek B2B accounting/ERP/POS|Union exclusion list|Union conflict")),
    # Stripe Atlas / Greek IKE corporate-structure decisions in the wrong place
    (
        "Corporate-structure decision leak",
        re.compile(r"Stripe Atlas Delaware C-corp / Greek freelancer|Greek IKE vs Atlas"),
    ),
    # Old git-config OneDrive-folder name that leaks the user's local
    # filesystem layout. Was the default committer name pre-author-rewrite,
    # then crept back into a generated audit-report sample. Matches stand-
    # alone occurrences; CHANGELOG history references in passing are OK
    # via the CHANGELOG.md whitelist below.
    ("Old git-config CosmoHac string", re.compile(r"\bCosmoHac\b")),
    # Internal-roadmap phrasing that crept into shipped module docstrings
    # ("deferred from MVP", "deferred to phase 2", "(future)"). Customers
    # don't need to know our internal sequencing.
    (
        "Internal-roadmap phrasing in shipped docs",
        re.compile(r"\bdeferred from MVP\b|\bdeferred to (phase|wave|sprint)\b", re.IGNORECASE),
    ),
    # Sales / strategy positioning words that have meaning in our internal
    # docs but make customer-facing comments read like a strategy memo.
    # "buyer wedge" / "wedge identified by …" / "first dollar" /
    # "closes Roam Review deals" — all collected from real leaks.
    (
        "Sales-positioning shorthand",
        re.compile(
            r"\bbuyer wedge\b|wedge identified by|"
            r"\bfirst dollar\b|closes Roam Review deals|"
            r"\bproduct agent\b",
            re.IGNORECASE,
        ),
    ),
    # Internal-pricing-doc cross-references in shipped files.
    # ``Per pricing_v3 build priorities``, ``per pricing_v4 P2``, etc.
    (
        "Pricing-doc cross-reference",
        re.compile(r"pricing_v\d+ build priorities|pricing_v\d+ P\d+", re.IGNORECASE),
    ),
    # Phasing of unrelated-to-this-file design work in module docstrings.
    # ``Phase 1 of the daemon design``, ``Phase 2 of the agent rollout``.
    # Sequencing belongs in commits / planning docs, not shipped code.
    (
        "Phase-of-design module docstring",
        re.compile(r"Phase \d+ of (?:the )?[a-z][a-z\- ]+ (?:design|rollout|plan)", re.IGNORECASE),
    ),
    # Date-stamped internal memo filenames in dev/ (catch-all).
    # Public dev/ docs are NOT date-suffixed (only MCP-SECURITY-POSTURE.md,
    # the example-plugin subtree, and dev scripts). Anything matching
    # ``dev/<ALLCAPS>-YYYY-MM-DD.md`` is session-cadence planning content
    # that belongs under ``internal/planning/``.
    (
        "Date-stamped dev/ memo filename",
        re.compile(r"\bdev/[A-Z][A-Z0-9_-]*-\d{4}-\d{2}-\d{2}[A-Za-z0-9_-]*\.md\b"),
    ),
    # Internal-planning-doc dev/ filenames that exist without date suffix
    # but are still session/strategy-cadence rather than user-facing.
    (
        "Internal-planning dev/ filename",
        re.compile(
            r"\bdev/(?:ROAM-STRATEGY|NEXT-BUILD-PRIORITIES|DOCS-CLEANUP-PLAN|"
            r"SESSION-HANDOVER|MCP-EVOLUTION|MCP-SERVER-CARD|MCP-TASKS-EVAL|"
            r"MCP-ELICITATION-CANDIDATES|DETECTOR-FP-METHODOLOGY|"
            r"OWASP-TAINT-RULE-PACK-RESEARCH|CROSSWALK-ADDITIONS|"
            r"PERF-PHASES|ROADMAP|BACKLOG|ARCHITECTURE-FUTURES|"
            r"D[0-9]-[A-Z][A-Z0-9_-]+-SPIKE|MONETIZATION-OPPORTUNITIES|"
            r"NEXT-PRIORITIES|V\d+\.\d+-RELEASE-READINESS|"
            r"SPRINT-\d{4})\b"
        ),
    ),
    # Fork-author attribution. Credit goes via LICENSE/CONTRIBUTING, not by
    # name-dropping individual GitHub handles in shipped code.
    # NOTE: the regex pattern source is intentionally split with `+` so the
    # individual names embedded in this catalog file don't trip a future
    # blob-level scrub of the same strings.
    (
        "Fork-author attribution",
        re.compile(r"@Lukas" + r"Berka|@river" + r"-mounts|credit Lukas" + r" Berka|credit Sam" + r" Hannan"),
    ),
    # "cash path" / "revenue path" — internal revenue framing.
    (
        "Cash-path framing",
        re.compile(r"\brevenue path\b|\bthe (?:current )?cash path\b", re.IGNORECASE),
    ),
    # Internal/ folder revenue-ops cross-references from shipped code.
    # ``internal/dogfood/`` and ``internal/smoke/`` references are
    # legitimate (dogfood corpus is cited extensively in AGENTS.md; smoke
    # is the output path of dev/roam_smoke.py). Block only the revenue-ops
    # / planning cross-refs.
    (
        "Internal/ folder revenue-ops or planning cross-reference",
        re.compile(r"\binternal/(?:pr-replay-engagement-playbook|planning/[A-Z])"),
    ),
    # Date-stamped ALLCAPS memo filenames ANYWHERE (generalizes the dev/-
    # scoped pattern above). Session-cadence memos follow the
    # ``<TOPIC>-YYYY-MM-DD[-slug].md`` convention; naming one from a shipped
    # docstring points readers at a private file and leaks the cadence.
    # Cite the content neutrally ("the dogfood synthesis notes") instead.
    (
        "Dated internal memo filename",
        re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{4}-\d{2}-\d{2}[A-Za-z0-9-]*\.md\b"),
    ),
    # Claude-memory slug references in shipped code/docs. Memory names are
    # session-private; describing the decision ("per the Roam Guard pivot")
    # carries the same information without naming the memory system.
    (
        "Claude-memory slug reference",
        re.compile(
            r"\bproject_pivot_to_roam_guard\b|\bproject_all_levers_breakthrough\b|"
            r"\bproject_deep_levers_inventory\b|\bproject_x3_haiku_l1_breakthrough\b|"
            r"\[\[(?:project|feedback|reference|user)_[a-z0-9_]+\]\]"
        ),
    ),
    # Host-platform name. The agent-orchestration platform this project is
    # developed alongside is private; "the host platform" is the shipped
    # phrasing (10 mentions scrubbed in a prior sweep — this pins them out).
    ("Host-platform name", re.compile(r"\bStoa\b")),
    # Absolute VPS filesystem paths — leak the deployment box's layout.
    # Tracked configs must use PATH-resolved commands / relative paths.
    (
        "VPS absolute path",
        re.compile(r"/root/(?:apps|services|repos|legacy|accounting-source)/"),
    ),
]


# Files where these patterns are intentional product behaviour or test
# fixtures FOR the patterns themselves — not real leaks.
#
# CHANGELOG.md is INTENTIONALLY NOT WHITELISTED: it is served publicly
# at roam-code.com/changelog and via raw GitHub. A "we removed these
# phrases" entry that names the phrases verbatim is itself the leak.
# Cleanup acknowledgements should describe scrubs in neutral terms —
# refer to "the pattern catalogue" rather than enumerating phrases.
WHITELIST_FILES = {
    # The CI test file owns the pattern catalogue.
    "tests/test_no_internal_language.py",
    # This module is the extracted single-source catalogue (same role).
    "scripts/internal_language_patterns.py",
    # The exemplar ratchet suite: synthetic leak-shaped lines that pin every
    # pattern class so a regex tidy-up can't silently weaken the gate.
    "tests/test_leak_gate_exemplars.py",
    # The phantom-memo detector's regression suite. Tests the matcher
    # that flags backtick-fenced ``dev/<MEMO>-YYYY-MM-DD.md`` paths in
    # CHANGELOG.md; the synthetic fixture has to LITERALLY look like
    # one (e.g. ``dev/NONEXISTENT-2026-05-18.md``) to exercise the
    # regex, otherwise the test silently passes on an empty input.
    "tests/test_changelog_phantoms.py",
    # AI-authorship detector + test fixtures around it.
    "src/roam/security/aibom_extension.py",
    "tests/test_ai_ratio.py",
    "tests/test_v12_2.py",
    # Anchor-slugifier regression suite. ``PFPA_EPIL.IN_PFPA_EPIL-4.DBF``
    # is a real header from the dogfood corpus that broke the slugifier
    # by producing ``pfpaepilinpfpaepil-4dbf``; the test fixtures need
    # the literal underscore-bearing identifier to assert the regression
    # is fixed. Generic replacement names destroy the test signal.
    "tests/test_stale_refs_dogfood_fixes.py",
    # Public legal template that explains ``AFM`` is the Greek tax-ID
    # abbreviation, with a bracketed placeholder ``[PROVIDER_AFM]`` for
    # the SOW signatory to fill in. The mention is intentional and
    # customer-facing, not a session-context leak.
    "templates/legal/sow-pr-replay.md",
    # Customer-facing changelogs. By definition they document historical
    # state and reference resources that no longer exist (e.g.
    # ``cranot.github.io/roam-code/*`` which 404s post-migration to
    # roam-code.com/docs/). The historical record is the changelog's
    # entire point — rephrasing post-hoc would erase context.
    "CHANGELOG.md",
    "templates/distribution/landing-page/changelog.html",
}


# Glob-style allowlist for paths excluded from the sweep.
EXCLUDED_DIRS = (
    "internal/",
    "reports/",
    "bench-repos/",
    ".roam/",
    "__pycache__",
    ".egg-info",
    "venv/",
    ".venv/",
    "node_modules/",
    "dist/",
    "build/",
    ".git/",
)

# Only check these file extensions.
SCAN_EXTENSIONS = (".py", ".md", ".html", ".yml", ".yaml", ".json", ".txt", ".tmpl", ".css", ".js")


def should_scan(rel_posix_path: str) -> bool:
    """Return True iff a posix relative-path string should be scanned.

    Same logic as the CI test's ``_should_scan``, but takes the already-
    normalised posix relative-path string (e.g. ``"src/roam/cli.py"``) rather
    than a ``pathlib.Path``, so the stdlib-only hook CLI can call it without
    materialising filesystem ``Path`` objects for git-listed entries.
    """
    if rel_posix_path in WHITELIST_FILES:
        return False
    if not rel_posix_path.endswith(SCAN_EXTENSIONS):
        return False
    for excluded in EXCLUDED_DIRS:
        if excluded in rel_posix_path:
            return False
    return True


def scan_text(rel_posix_path: str, text: str) -> list[tuple[str, int, str]]:
    """Scan one file's text for forbidden patterns.

    Returns ``[(pattern_name, line_no, stripped_line[:200])]`` for every hit,
    at most one pattern per line (mirrors the CI test's ``_scan_for_leaks``
    inner loop: the first matching pattern on a line wins, then move on).

    The caller is responsible for having decided ``should_scan(rel_posix_path)``
    is True; this function does not re-check.
    """
    hits: list[tuple[str, int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for name, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                hits.append((name, line_no, line.strip()[:200]))
                break  # one pattern per line is enough; stop checking others
    return hits
