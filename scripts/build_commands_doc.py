#!/usr/bin/env python3
"""Generate docs/COMMANDS.md — the full command index — from `roam surface`.

The command index was dropped once in a history reconcile (W169). This script
regenerates it deterministically from the live surface (the source of truth),
and `--check` verifies the committed doc is present + in sync — the invariant
enforced by tests/test_commands_doc_synced.py so a drop/drift fails CI.

Usage:
  python scripts/build_commands_doc.py            # regenerate docs/COMMANDS.md
  python scripts/build_commands_doc.py --check     # exit 1 if missing/out-of-sync
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "COMMANDS.md"


def _surface() -> dict:
    from click.testing import CliRunner

    from roam.cli import cli

    res = CliRunner().invoke(cli, ["surface", "--json"])
    if res.exit_code != 0:
        raise SystemExit(f"roam surface failed: {res.output[:300]}")
    return json.loads(res.output)


def render(surface: dict) -> str:
    cmds = surface.get("commands", [])
    cats: dict[str, list[dict]] = {}
    for c in cmds:
        cats.setdefault(c.get("category") or "Other", []).append(c)
    lines = [
        "# roam Command Index",
        "",
        "> **Generated — do not hand-edit.** Regenerate with "
        "`python scripts/build_commands_doc.py`. Kept in sync by "
        "`tests/test_commands_doc_synced.py` (a command dropped from this index, "
        "or a new command left undocumented, fails CI — the reconcile-survival invariant).",
        "",
        f"**{surface.get('command_count', len(cmds))} commands** "
        f"({surface.get('canonical_count', '?')} canonical + aliases) across "
        f"{surface.get('category_count', len(cats))} categories · "
        f"{surface.get('mcp_tool_count', '?')} MCP tools · roam v{surface.get('version', '?')}",
        "",
    ]
    # Iterate the canonical category order first, then any leftover categories
    # (e.g. None->"Other" or a non-canonical label) so NO command is dropped.
    canon = list(surface.get("categories", []))
    ordered = canon + [c for c in sorted(cats) if c not in canon]
    for cat in ordered:
        items = sorted(cats.get(cat, []), key=lambda c: c["name"])
        if not items:
            continue
        lines.append(f"## {cat} ({len(items)})")
        lines.append("")
        lines.append("| Command | Maturity | MCP | Aliases |")
        lines.append("|---------|----------|-----|---------|")
        for c in items:
            al = ", ".join(c.get("aliases") or []) or "—"
            mcp = "✓" if c.get("mcp_exposed") else "—"
            lines.append(f"| `{c['name']}` | {c.get('maturity', '?')} | {mcp} | {al} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    surface = _surface()
    want = render(surface)
    if check:
        if not DOC.exists():
            print(f"MISSING: {DOC} — run `python scripts/build_commands_doc.py` (W169 reconcile-survival invariant)")
            return 1
        have = DOC.read_text(encoding="utf-8")
        if have != want:
            print(f"OUT-OF-SYNC: {DOC} — run `python scripts/build_commands_doc.py`")
            return 1
        print(f"ok  {DOC.name} in sync ({surface.get('command_count')} commands)")
        return 0
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(want, encoding="utf-8")
    print(f"wrote {DOC} ({surface.get('command_count')} commands, {surface.get('category_count')} categories)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
