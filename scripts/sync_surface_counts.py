#!/usr/bin/env python3
"""Sync surface counts (commands / MCP tools / languages) across docs.

Single source of truth: ``roam.surface_counts.collect_surface_counts()``.
This script reads the live counts and rewrites every doc-surface that
quotes them: README.md, llms-install.md, server.json, mcp-server-card
(both copies), the Cloudflare-served landing-page HTML / llms.txt, the
Claude Code skill, and the in-repo CI integration doc.

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
        "specialised": int(surface["cli"]["command_names"]) - 5,  # 5-verb model
        "mcp_tools": int(surface["mcp"]["registered_tools"]),
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
    langs = languages

    # README.md
    REPLACEMENTS.append(
        (
            REPO_ROOT / "README.md",
            [
                (
                    re.compile(r"\*\d+ commands · \d+ MCP tools · \d+ languages"),
                    f"*{cmds} commands · {mcp} MCP tools · {langs} languages",
                ),
                (re.compile(r"\bother \d+ specialised commands\b"), f"other {spec} specialised commands"),
                (re.compile(r"\bremaining ~\d+ commands\b"), f"remaining ~{spec} commands"),
                (re.compile(r"canonical surface is \*\*\d+ commands"), f"canonical surface is **{cmds} commands"),
            ],
        )
    )

    # llms-install.md
    REPLACEMENTS.append(
        (
            REPO_ROOT / "llms-install.md",
            [
                (
                    re.compile(r"\b\d+ commands, \d+ MCP tools, \d+ languages\b"),
                    f"{cmds} commands, {mcp} MCP tools, {langs} languages",
                ),
                (re.compile(r"all \d+ commands"), f"all {cmds} commands"),
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

    # Print all 137 MCP tools — explicit number on the command-reference page.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html",
            [
                (re.compile(r"all (\d+) MCP tools"), f"all {mcp} MCP tools"),
                (re.compile(r"All (\d+) commands"), f"All {cmds} commands"),
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
    print(
        f"Live surface: {counts['commands']} commands ({counts['canonical']} canonical, {counts['canonical'] + 7} with aliases)"
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
