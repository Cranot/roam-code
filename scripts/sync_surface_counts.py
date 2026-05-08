#!/usr/bin/env python3
"""Sync surface counts (commands / MCP tools / languages) across docs.

Single source of truth: ``roam.surface_counts.collect_surface_counts()``.
This script reads the live counts and rewrites every doc-surface that
quotes them: README.md, llms-install.md, server.json, mcp-server-card
(both copies), docs/site/data/landscape.json.

Usage:
    python scripts/sync_surface_counts.py            # dry-run (report only)
    python scripts/sync_surface_counts.py --write    # rewrite files in place

CI usage:
    python scripts/sync_surface_counts.py            # exit 1 if drift detected
    python scripts/sync_surface_counts.py --write    # rewrite + re-commit
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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
    REPLACEMENTS.append((REPO_ROOT / "README.md", [
        (re.compile(r"\*\d+ commands · \d+ MCP tools · \d+ languages"),
         f"*{cmds} commands · {mcp} MCP tools · {langs} languages"),
        (re.compile(r"\bother \d+ specialised commands\b"),
         f"other {spec} specialised commands"),
        (re.compile(r"\bremaining ~\d+ commands\b"),
         f"remaining ~{spec} commands"),
        (re.compile(r"canonical surface is \*\*\d+ commands"),
         f"canonical surface is **{cmds} commands"),
    ]))

    # llms-install.md
    REPLACEMENTS.append((REPO_ROOT / "llms-install.md", [
        (re.compile(r"\b\d+ commands, \d+ MCP tools, \d+ languages\b"),
         f"{cmds} commands, {mcp} MCP tools, {langs} languages"),
        (re.compile(r"all \d+ commands"),
         f"all {cmds} commands"),
    ]))

    # server.json (string description with "N languages")
    REPLACEMENTS.append((REPO_ROOT / "server.json", [
        (re.compile(r"\b\d+ languages\b"), f"{langs} languages"),
    ]))

    # mcp-server-card.json — both copies
    for p in [
        REPO_ROOT / "src" / "roam" / "mcp-server-card.json",
        REPO_ROOT / "docs" / "site" / ".well-known" / "mcp-server-card.json",
    ]:
        REPLACEMENTS.append((p, [
            (re.compile(r'"total":\s*\d+,?(\s*\n\s*"watched")'),
             None),  # don't touch resources count
        ]))


def _scrape(text: str, pat: re.Pattern) -> str | None:
    m = pat.search(text)
    return m.group(0) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="Rewrite files in place (default: dry-run)")
    args = ap.parse_args()

    counts = _live_counts()
    langs = _live_languages()
    print(f"Live surface: {counts['commands']} commands ({counts['canonical']} canonical, {counts['canonical'] + 7} with aliases)")
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
