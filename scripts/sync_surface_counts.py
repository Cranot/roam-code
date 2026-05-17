#!/usr/bin/env python3
"""Sync surface counts (commands / MCP tools / languages) across docs.

Single source of truth: ``roam.surface_counts.collect_surface_counts()``.
This script reads the live counts and rewrites every doc-surface that
quotes them: README.md, llms-install.md, server.json, mcp-server-card
(both copies; see note below), the Cloudflare-served landing-page HTML /
llms.txt, the Claude Code skill, and the in-repo CI integration doc.

See also: ``dev/build_readme_counts.py``
    The two scripts are intentional cousins, not duplicates. This one
    (``sync_surface_counts.py``) handles **free-form prose surfaces**
    via regex substitution: the landing-page HTML pages, llms.txt,
    ``server.json``, ``skills/roam/SKILL.md``, ``competitor_site_data.py``,
    and ``docs/ci-integration.md``. ``dev/build_readme_counts.py``
    handles **marker-protected Markdown blocks** (README, CLAUDE,
    llms-install) and the **two mcp-server-card.json files** (byte-
    identical, required by ``test_bundled_card_matches_public_card``).
    The mcp-server-card entries below are intentionally no-op
    (``repl=None``) — those cards are owned by ``build_readme_counts.py``;
    the entries are retained here only so reviewers can see the file is
    explicitly covered elsewhere. README/llms-install overlap is benign
    because the legacy regexes do not match the marker-block prose
    shape that ``build_readme_counts.py`` writes.

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
    }


def _live_languages() -> int:
    """Count of supported languages from the registry."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from roam.languages.registry import get_supported_languages

        return len(get_supported_languages())
    except Exception:
        return 0


REPLACEMENTS: list[tuple[Path, list[tuple[re.Pattern, str]]]] = []


def build_replacements(counts: dict, languages: int) -> None:
    """Build the (file, [(pattern, replacement)...]) list."""
    REPLACEMENTS.clear()

    cmds = counts["commands"]
    mcp = counts["mcp_tools"]
    spec = counts["specialised"]
    core = counts["mcp_core_tools"]
    langs = languages

    # README.md and llms-install.md — owned by dev/build_readme_counts.py
    # (marker-block writer). Retained here as inert (repl=None) entries
    # so reviewers can see the files ARE covered. Same pattern as the
    # mcp-server-card.json entries below. W23.1 reconciliation, 2026-05-13.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "README.md",
            [
                # All None — see dev/build_readme_counts.py for the real writer.
                (re.compile(r"\*\d+ commands · \d+ MCP tools · \d+ languages"), None),
                (re.compile(r"\bother \d+ specialised commands\b"), None),
                (re.compile(r"\bremaining ~\d+ commands\b"), None),
                (re.compile(r"canonical surface is \*\*\d+ commands"), None),
            ],
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

    # server.json (string description with "N languages")
    REPLACEMENTS.append(
        (
            REPO_ROOT / "server.json",
            [
                (re.compile(r"\b\d+ languages\b"), f"{langs} languages"),
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
    print(
        f"Live surface: {counts['commands']} commands ({counts['canonical']} canonical, {with_aliases} with aliases)"
    )
    print(f"               {counts['mcp_tools']} MCP tools, {langs} languages")
    print()

    build_replacements(counts, langs)

    drift_found = 0
    for path, patterns in REPLACEMENTS:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARN: cannot read {path.relative_to(REPO_ROOT)}: {e}")
            continue
        original = text
        rel = path.relative_to(REPO_ROOT).as_posix()
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
