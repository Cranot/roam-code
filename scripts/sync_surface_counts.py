#!/usr/bin/env python3
"""Sync surface counts (commands / MCP tools / languages) across docs.

Single source of truth: ``roam.surface_counts.collect_surface_counts()``.
This script reads the live counts and rewrites every doc-surface that
quotes them: server.json, the mcp-server-card family (see note below),
the Cloudflare-served landing-page HTML / llms.txt / docs pages, the
Claude Code skill, the in-repo CI integration doc, AND the **free-form
(non-marker) count phrases** in README.md / CLAUDE.md / AGENTS.md /
CONTRIBUTING.md (directory-tree comments, MCP-section prose, the
contributor reference table).

See also: ``dev/build_readme_counts.py``
    The two scripts are intentional cousins, not duplicates. This one
    (``sync_surface_counts.py``) handles **free-form prose surfaces**
    via regex substitution: the landing-page HTML pages, llms.txt,
    ``server.json``, ``skills/roam/SKILL.md``, ``competitor_site_data.py``,
    ``docs/ci-integration.md``, and the prose count phrases in
    README/CLAUDE/AGENTS/CONTRIBUTING that fall OUTSIDE the auto-count
    marker blocks. ``dev/build_readme_counts.py`` handles the
    **marker-protected Markdown blocks** (README, CLAUDE, AGENTS,
    llms-install) and the **two mcp-server-card.json files** (byte-
    identical, required by ``test_bundled_card_matches_public_card``).
    To keep the two scripts strictly non-overlapping, the README /
    CLAUDE / AGENTS entries here are flagged ``marker_aware=True`` — they
    substitute on a marker-MASKED copy so they can never rewrite a byte
    the cousin script owns. The mcp-server-card entries below are
    intentionally no-op (``repl=None``) — those cards are owned by
    ``build_readme_counts.py``; the entries are retained here only so
    reviewers can see the file is explicitly covered elsewhere.

CI runs both scripts back-to-back in the ``doc-hygiene`` job
(.github/workflows/roam-ci.yml). Either failing is a hard gate.

Usage:
    python scripts/sync_surface_counts.py            # dry-run (report only)
    python scripts/sync_surface_counts.py --write    # rewrite files in place

CI usage:
    python scripts/sync_surface_counts.py            # exit 1 if drift detected
    python scripts/sync_surface_counts.py --write    # rewrite + re-commit
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _live_counts() -> dict:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from roam.surface_counts import collect_surface_counts

    surface = collect_surface_counts()
    return {
        "commands": int(surface["cli"]["command_names"]),
        "canonical": int(surface["cli"]["canonical_commands"]),
        "alias_names": int(surface["cli"]["alias_names"]),
        "specialised": int(surface["cli"]["command_names"]) - 5,  # 5-verb model
        "mcp_tools": int(surface["mcp"]["registered_tools"]),
        # Core-preset tool count from the live AST parser; never hardcode
        # this literal — it drifts the moment _CORE_TOOLS in mcp_server.py
        # changes (W933-class stale-literal hazard). See preset_counts in
        # roam.surface_counts.mcp_surface_counts.
        "mcp_core_tools": int(surface["mcp"]["preset_counts"]["core"]),
        "mcp_preset_counts": {str(name): int(count) for name, count in surface["mcp"]["preset_counts"].items()},
    }


def _live_languages() -> int:
    """Count of supported languages from the registry.

    Hard-fails on import error: this script is the source of truth for
    the language count quoted in README/llms-install/landing-page. A
    silent ``return 0`` would write ``0 languages`` into every doc
    surface — exactly the W933-class stale-literal hazard the sibling
    ``_live_counts`` deliberately avoids by letting errors propagate
    (see lines 58-64 above). Lineage rule (CLAUDE.md "Make fallback
    chains loud"): a sync tool with no producer must crash loudly so
    CI catches it, not silently mis-publish.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from roam.languages.registry import get_supported_languages

    return len(get_supported_languages())


# Each entry is one of:
#   (path, [(pattern, replacement)...])              -- legacy whole-file path
#   (path, [(pattern, replacement)...], marker_aware) -- marker-masked path
# ``marker_aware=True`` runs substitution on a copy with the auto-count
# marker blocks masked out, so this script never rewrites bytes owned by
# the cousin ``dev/build_readme_counts.py``. Surfaces with no marker blocks
# use the 2-tuple legacy form. ``main()`` tolerates both.
REPLACEMENTS: list[tuple] = []


def _mcp_preset_description(preset_counts: dict[str, int]) -> str:
    """Render the server.json preset help from the complete runtime map."""
    parts = []
    for name, count in preset_counts.items():
        if name == "core":
            parts.append(f"core (default, {count} — lean prompt surface)")
        else:
            parts.append(f"{name} ({count})")
    return "Tool preset: " + ", ".join(parts)


def build_replacements(counts: dict, languages: int) -> None:
    """Build the (file, [(pattern, replacement)...], marker_aware) list."""
    REPLACEMENTS.clear()

    cmds = counts["commands"]
    canon = counts["canonical"]
    aliases = counts["alias_names"]
    mcp = counts["mcp_tools"]
    core = counts["mcp_core_tools"]
    preset_description = _mcp_preset_description(counts["mcp_preset_counts"])
    langs = languages

    # README.md — the auto-count MARKER blocks (headline / canonical-mention
    # / default-preset / tool-table) are owned by dev/build_readme_counts.py;
    # those entries below stay inert (repl=None). The FREE-FORM count phrases
    # OUTSIDE the markers — the MCP-section prose ("N tools, 10 resources"),
    # the directory-tree comments ("MCP server (N tools, ...)"), and the
    # "M canonical + K aliases" CLI annotation — had no guard at all and are
    # the surfaces this extension adds. marker_aware=True masks the cousin
    # script's territory so the two scripts cannot fight over bytes.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "README.md",
            [
                # --- Inert: owned by dev/build_readme_counts.py marker blocks.
                (re.compile(r"\*\d+ commands · \d+ MCP tools · \d+ languages"), None),
                (re.compile(r"\bother \d+ specialised commands\b"), None),
                (re.compile(r"\bremaining ~\d+ commands\b"), None),
                (re.compile(r"canonical surface is \*\*\d+ commands"), None),
                # --- Active: free-form prose OUTSIDE the marker blocks.
                # "N tools, 10 resources, and 5 prompts are available in the full preset."
                (
                    re.compile(r"\b\d+ tools, (\d+ resources, and \d+ prompts are available)"),
                    rf"{mcp} tools, \1",
                ),
                # Directory-tree comment: "MCP server (N tools, 10 resources, 6 prompts)".
                (
                    re.compile(r"MCP server \(\d+ tools(, \d+ resources, \d+ prompts\))"),
                    rf"MCP server ({mcp} tools\1",
                ),
                # Directory-tree comment: "Click CLI (M canonical + K aliases)".
                (
                    re.compile(r"Click CLI \(\d+ canonical \+ \d+ aliases\)"),
                    f"Click CLI ({canon} canonical + {aliases} aliases)",
                ),
                # NOTE: the "are 90 of the N tools dead weight?" phrase in the
                # README MCP-tool table is NOT guarded here. It lives inside the
                # auto-count `readme-mcp-tool-list-table` marker block, sourced
                # verbatim from the `roam_session_metrics` docstring in
                # src/roam/mcp_server.py. Its `224` is stale vs the live 227,
                # but the fix belongs in mcp_server.py's docstring — the cousin
                # script regenerates the table from it. A pattern here could
                # only ever match inside the marker block (which marker-aware
                # masking skips), so it would be dead. Fix at the source.
            ],
            True,
        )
    )

    # CLAUDE.md — headline + authoritative blocks are marker-protected (owned
    # by build_readme_counts.py's claude-* blocks). The free-form architecture
    # prose ("N command names (M canonical + K aliases)", "57 tools in core
    # preset; up to N in full", "all N command names") had no guard.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "CLAUDE.md",
            [
                (
                    re.compile(r"\b\d+ command names \(\d+ canonical \+ \d+ aliases\)"),
                    f"{cmds} command names ({canon} canonical + {aliases} aliases)",
                ),
                (
                    re.compile(r"FastMCP server \(\d+ tools in core preset; up to \d+ in `full`\)"),
                    f"FastMCP server ({core} tools in core preset; up to {mcp} in `full`)",
                ),
                (
                    re.compile(r"\bfor all \d+ command names\b"),
                    f"for all {cmds} command names",
                ),
            ],
            True,
        )
    )

    # AGENTS.md — same shape as CLAUDE.md. Codex-headline + Codex-authoritative
    # blocks are marker-protected; the free-form prose below is not.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "AGENTS.md",
            [
                (
                    re.compile(r"\b\d+ command names \(\d+ canonical \+ \d+ aliases\)"),
                    f"{cmds} command names ({canon} canonical + {aliases} aliases)",
                ),
                (
                    re.compile(r"FastMCP server \(\d+ tools in core preset; \d+ in `full`\)"),
                    f"FastMCP server ({core} tools in core preset; {mcp} in `full`)",
                ),
                (
                    re.compile(r"\bfor all \d+ command names\b"),
                    f"for all {cmds} command names",
                ),
            ],
            True,
        )
    )

    # CONTRIBUTING.md — no marker blocks; one count-bearing reference-table row.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "CONTRIBUTING.md",
            [
                (
                    re.compile(r"MCP server with \d+ tools \(\d+ in the default `core` preset\)"),
                    f"MCP server with {mcp} tools ({core} in the default `core` preset)",
                ),
            ],
            False,
        )
    )

    REPLACEMENTS.append(
        (
            REPO_ROOT / "llms-install.md",
            [
                # All None — see dev/build_readme_counts.py for the real writer.
                (re.compile(r"\b\d+ commands, \d+ MCP tools, \d+ languages\b"), None),
                (re.compile(r"all \d+ commands"), None),
            ],
        )
    )

    # server.json (language count + complete ROAM_MCP_PRESET description)
    REPLACEMENTS.append(
        (
            REPO_ROOT / "server.json",
            [
                (re.compile(r"\b\d+ languages\b"), f"{langs} languages"),
                (re.compile(r"Tool preset: [^\"]+"), preset_description),
            ],
        )
    )

    # cli.py top-of-file current-surface comment. The registry itself remains
    # the source of truth; this replacement keeps the human-facing summary
    # from silently lagging after command additions/removals.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "src" / "roam" / "cli.py",
            [
                (
                    re.compile(
                        r"# Total: \d+ invokable command names "
                        r"\(\d+ canonical commands \+ \d+ alias names\)\."
                    ),
                    f"# Total: {cmds} invokable command names ({canon} canonical commands + {aliases} alias names).",
                ),
            ],
        )
    )

    # mcp-server-card.json — both copies
    # The second copy used to live at ``docs/site/.well-known/`` (served
    # via GitHub Pages at cranot.github.io). After GH Pages was disabled
    # on 2026-05-08, the canonical public copy moved under the
    # Cloudflare-served landing-page tree so the card_url claim
    # (``roam-code.com/.well-known/mcp-server-card.json``) keeps working.
    for p in [
        REPO_ROOT / "src" / "roam" / "mcp-server-card.json",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json",
    ]:
        REPLACEMENTS.append(
            (
                p,
                [
                    (re.compile(r'"total":\s*\d+,?(\s*\n\s*"watched")'), None),  # don't touch resources count
                ],
            )
        )

    # ----- Public landing page + docs site -----
    # Reviewer (2026-05-08) found 5 different command counts on
    # different surfaces because these files weren't in the script.
    # All of them must match the live counts.

    # Cardinal pattern across the landing-page HTML files: any standalone
    # ``N CLI commands``, ``N commands``, ``N MCP tools``, or
    # ``N languages``. The regex deliberately uses word boundaries so we
    # don't catch e.g. "v12.50" or unrelated numerics.
    # Marketing-tone pages where the user has chosen the soft-count
    # framing ("200+ CLI capabilities", "130+ MCP tools", "28 language
    # families") deliberately, per the strategic-reframe directive on
    # 2026-05-09. These pages are EXCLUDED from auto-sync; otherwise
    # the script would clobber the soft framing with hard counts and
    # contradict the positioning. Reference / docs / press surfaces
    # below still get hard counts.
    SOFT_COUNT_PAGES = {
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "index.html",
    }

    landing_pages = [
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "index.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "setup.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "pricing.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "compare.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "press.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "llms.txt",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "index.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "getting-started.html",
        # Added 2026-05-21: docs pages that quote the same hard counts but
        # were never walked by this script — the gap the 224-vs-227 drift
        # cascade exposed. mcp-usage.html also gets the cardinal patterns
        # here (the explicit ``exposes all N`` pin below is additive).
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "mcp-usage.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "integration-tutorials.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "canonical-demo.html",
    ]
    for p in landing_pages:
        if p in SOFT_COUNT_PAGES:
            continue
        REPLACEMENTS.append(
            (
                p,
                [
                    (re.compile(r"\b\d+ CLI commands\b"), f"{cmds} CLI commands"),
                    (re.compile(r"\b\d+ commands\b"), f"{cmds} commands"),
                    (re.compile(r"\b\d+ MCP tools\b"), f"{mcp} MCP tools"),
                    (re.compile(r"\((\d+) tools\)"), f"({mcp} tools)"),
                    (re.compile(r"\bRoam's \d+ tools\b"), f"Roam's {mcp} tools"),
                    (re.compile(r"\b\d+ languages\b"), f"{langs} languages"),
                ],
            )
        )

    # Explicit MCP preset counts on the command-reference / MCP usage pages.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html",
            [
                (re.compile(r"all (\d+) MCP tools"), f"all {mcp} MCP tools"),
                (re.compile(r"All (\d+) commands"), f"All {cmds} commands"),
                (
                    re.compile(
                        r"default: \d+ core tools(?: plus the <code>roam_expand_toolset</code> meta-tool)?; \d+ in <code>full</code>"
                    ),
                    f"default: {core} core tools plus the <code>roam_expand_toolset</code> meta-tool; {mcp} in <code>full</code>",
                ),
            ],
        )
    )
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "mcp-usage.html",
            [
                (re.compile(r"exposes all\s+\d+(?: tools)?\."), f"exposes all\n        {mcp} tools."),
            ],
        )
    )

    # agent-contract.html — the "Surface scale" count table has one labeled
    # row per count, so each substitution is anchored on the row label
    # (``<td>LABEL</td><td>N</td>``). Anchoring on the label keeps the
    # 1-row ("Canonical envelope" -> 1) untouched.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "agent-contract.html",
            [
                (
                    re.compile(r"(<td>CLI commands</td><td>)\d+(</td>)"),
                    rf"\g<1>{cmds}\g<2>",
                ),
                (
                    re.compile(r"(<td>MCP tools registered</td><td>)\d+(</td>)"),
                    rf"\g<1>{mcp}\g<2>",
                ),
                (
                    re.compile(r"(<td>MCP tools in <code>core</code> preset</td><td>)\d+(</td>)"),
                    rf"\g<1>{core}\g<2>",
                ),
                (
                    re.compile(r"(<td>Languages</td><td>)\d+(</td>)"),
                    rf"\g<1>{langs}\g<2>",
                ),
                # "234 canonical + 7 aliases." note cell + "all 227 tools" prose.
                (
                    re.compile(r"\b\d+ canonical \+ \d+ aliases\b"),
                    f"{canon} canonical + {aliases} aliases",
                ),
                (
                    re.compile(r"\ball \d+ tools\b"),
                    f"all {mcp} tools",
                ),
            ],
        )
    )

    # ``docs/site/data/landscape.json`` was deleted on 2026-05-08 when
    # GitHub Pages was disabled. The competitor data still lives in
    # ``src/roam/competitor_site_data.py`` (Python module) and the
    # gitignored ``internal/competitor_tracker.md`` (source of truth).

    # src/roam/competitor_site_data.py — peer-entry self-reference.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "src" / "roam" / "competitor_site_data.py",
            [
                (re.compile(r"\b\d+ MCP tools, \d+ CLI commands\b"), f"{mcp} MCP tools, {cmds} CLI commands"),
            ],
        )
    )

    # skills/roam/SKILL.md — Claude Code skill mentions the count.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "skills" / "roam" / "SKILL.md",
            [
                (re.compile(r"\broam has \d+ commands\b"), f"roam has {cmds} commands"),
            ],
        )
    )

    # docs/ci-integration.md — "all N commands" footer.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "docs" / "ci-integration.md",
            [
                (re.compile(r"all \d+ commands"), f"all {cmds} commands"),
            ],
        )
    )


def _scrape(text: str, pat: re.Pattern) -> str | None:
    m = pat.search(text)
    return m.group(0) if m else None


def iter_replacements() -> "list[tuple[Path, list, bool]]":
    """Yield every REPLACEMENTS entry normalised to ``(path, patterns, marker_aware)``.

    ``build_replacements`` must have been called first. Entries are stored
    as either 2-tuples (legacy whole-file) or 3-tuples (marker-aware); this
    helper hides that distinction so callers — including tests — iterate one
    stable shape. Adding a new ``marker_aware`` surface no longer breaks any
    consumer that unpacks the list.
    """
    out: list[tuple[Path, list, bool]] = []
    for entry in REPLACEMENTS:
        if len(entry) == 3:
            path, patterns, marker_aware = entry
        else:
            path, patterns = entry
            marker_aware = False
        out.append((path, patterns, marker_aware))
    return out


# Files whose count phrases live OUTSIDE the auto-count marker blocks owned by
# ``dev/build_readme_counts.py``. The cousin script writes the marker-protected
# headline / authoritative blocks; this script owns the free-form prose count
# phrases scattered through the rest of the same Markdown files (directory-tree
# comments, MCP-section prose, the contributor reference table). To keep the
# two scripts strictly non-overlapping, every substitution below is applied to
# a *marker-masked* copy of the text — see ``_apply_marker_aware``.
_MARKER_BLOCK = re.compile(
    r"<!--\s*BEGIN auto-count:.*?-->.*?<!--\s*END auto-count:.*?-->",
    flags=re.DOTALL,
)


def _marker_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans of every auto-count marker block."""
    return [(m.start(), m.end()) for m in _MARKER_BLOCK.finditer(text)]


def _apply_marker_aware(text: str, patterns: list[tuple[re.Pattern, str | None]]) -> tuple[str, list[tuple[str, str]]]:
    """Apply (pattern, replacement) substitutions OUTSIDE marker blocks only.

    Returns ``(new_text, hits)`` where ``hits`` is a list of
    ``(before, after)`` pairs for reporting. Substitutions whose only
    match falls inside an auto-count marker block are skipped — those
    sites are owned by ``dev/build_readme_counts.py`` and rewriting them
    here would make the two scripts fight over the same bytes.
    """
    spans = _marker_spans(text)

    def _in_marker(pos: int) -> bool:
        return any(start <= pos < end for start, end in spans)

    hits: list[tuple[str, str]] = []
    for pat, repl in patterns:
        if repl is None:
            continue
        # Re-scan from scratch each iteration so positions stay valid.
        out: list[str] = []
        last = 0
        spans = _marker_spans(text)
        for m in pat.finditer(text):
            if _in_marker(m.start()):
                continue
            out.append(text[last : m.start()])
            replaced = m.expand(repl)
            out.append(replaced)
            if replaced != m.group(0):
                hits.append((m.group(0), replaced))
            last = m.end()
        out.append(text[last:])
        text = "".join(out)
    return text, hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="Rewrite files in place (default: dry-run)")
    args = ap.parse_args()

    counts = _live_counts()
    langs = _live_languages()
    # ``with aliases`` reuses live alias_names from surface_counts rather than
    # a literal "+ 7" magic number — the moment a new alias lands in
    # ``cli._COMMANDS``, the header reflects it without an edit here
    # (W933-class stale-literal hazard).
    with_aliases = counts["canonical"] + counts["alias_names"]
    print(f"Live surface: {counts['commands']} commands ({counts['canonical']} canonical, {with_aliases} with aliases)")
    print(f"               {counts['mcp_tools']} MCP tools, {langs} languages")
    print()

    build_replacements(counts, langs)

    drift_found = 0
    for path, patterns, marker_aware in iter_replacements():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"WARN: cannot read {path.relative_to(REPO_ROOT)}: {e}")
            continue
        original = text
        rel = path.relative_to(REPO_ROOT).as_posix()
        if marker_aware:
            # Substitute only OUTSIDE auto-count marker blocks — those are
            # owned by dev/build_readme_counts.py.
            text, hits = _apply_marker_aware(text, patterns)
            for before, after in hits:
                drift_found += 1
                print(f"  {rel}: '{before}' -> '{after}'")
        else:
            for pat, repl in patterns:
                if repl is None:
                    continue
                new_text = pat.sub(repl, text)
                if new_text != text:
                    drift_found += 1
                    # Show before / after of the first match
                    m_before = pat.search(text)
                    if m_before:
                        print(f"  {rel}: '{m_before.group(0)}' -> '{repl}'")
                    text = new_text
        if args.write and text != original:
            path.write_text(text, encoding="utf-8")
            print(f"  -> wrote {rel}")

    print()
    if drift_found == 0:
        print("All surface counts in sync.")
        return 0
    if args.write:
        print(f"Synced {drift_found} pattern(s).")
        return 0
    print(f"{drift_found} drifted pattern(s). Run with --write to fix.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
