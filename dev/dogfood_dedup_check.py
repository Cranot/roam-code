"""Pre-flag dogfood findings that match already-fixed evals.

Surfaced by W36.8's "50% already-fixed" pattern in the 2026-05-13 dogfood
batch — the matching eval docs already carried `status: fixed-in-*` but
the dispatcher didn't grep first.

Usage:
    # From a list of command names:
    python dev/dogfood_dedup_check.py --commands sbom,stale-refs,dead

    # From stdin (one command per line):
    cat findings.txt | python dev/dogfood_dedup_check.py

    # From a markdown findings list (best-effort extract):
    python dev/dogfood_dedup_check.py --from-md path/to/dogfood-v2.md

Output: a table for each input command showing the most recent eval doc
and its `status:` frontmatter value (if any). Exit code 0 always; this is
informational, not a gate.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EVALS_DIR = _REPO_ROOT / "internal" / "dogfood" / "evals"
_STATUS_RE = re.compile(r"^status:\s*(.+?)\s*$", re.MULTILINE)
_FIX_REF_RE = re.compile(r"^fix_ref:\s*(.+?)\s*$", re.MULTILINE)


def _normalize_command(name: str) -> str:
    """Convert `sbom`, `stale-refs`, or `staleRefs` to the eval directory form."""
    name = name.strip().lower()
    return name.replace("-", "_").rstrip(":,;")


def _find_evals(command: str) -> list[Path]:
    """Return all eval markdown files for a command directory, newest first."""
    cmd_dir = _EVALS_DIR / _normalize_command(command)
    if not cmd_dir.is_dir():
        return []
    return sorted(cmd_dir.glob("*.md"), reverse=True)  # date-prefixed -> newest first


def _extract_status(eval_path: Path) -> tuple[str | None, str | None]:
    """Extract status + fix_ref frontmatter from an eval doc."""
    text = eval_path.read_text(encoding="utf-8", errors="replace")
    status = (m.group(1) if (m := _STATUS_RE.search(text)) else None)
    fix_ref = (m.group(1) if (m := _FIX_REF_RE.search(text)) else None)
    return status, fix_ref


def _classify_verdict(status: str | None, fix_ref: str | None) -> str:
    """Classify an eval's status into a dispatch decision.

    W37.4: presence of `fix_ref` is itself a "shipped" signal — some evals
    carry non-standard status labels (e.g. `unverifiable-on-this-repo`) but
    still cite a fix. Treat fix_ref alone as fixed so the dispatcher doesn't
    re-dispatch shipped work.
    """
    if status and "fixed" in status.lower():
        return "fixed"
    if fix_ref:  # has a fix-reference -> shipped, just with non-standard status label
        return "fixed"
    if status:
        return "open"
    return "unknown"


def check_commands(commands: list[str]) -> list[dict]:
    """For each command, return a row with the latest eval + status info."""
    rows = []
    for cmd in commands:
        evals = _find_evals(cmd)
        if not evals:
            rows.append({"command": cmd, "evals_found": 0, "latest": None,
                         "status": None, "fix_ref": None, "verdict": "no_evals"})
            continue
        latest = evals[0]
        status, fix_ref = _extract_status(latest)
        verdict = _classify_verdict(status, fix_ref)
        rows.append({
            "command": cmd,
            "evals_found": len(evals),
            "latest": str(latest.relative_to(_REPO_ROOT)),
            "status": status,
            "fix_ref": fix_ref,
            "verdict": verdict,
        })
    return rows


def _format_table(rows: list[dict]) -> str:
    lines = ["command           evals  verdict      latest"]
    lines.append("-" * 80)
    for r in rows:
        lines.append(
            f"{r['command']:<18}"
            f"{r['evals_found']:<7}"
            f"{r['verdict']:<13}"
            f"{r['latest'] or '-'}"
        )
        if r["status"]:
            lines.append(f"  status: {r['status']}")
        if r["fix_ref"]:
            lines.append(f"  fix_ref: {r['fix_ref']}")
    fixed = sum(1 for r in rows if r["verdict"] == "fixed")
    lines.append("-" * 80)
    lines.append(f"summary: {fixed}/{len(rows)} have a 'fixed-*' eval - likely already-shipped")
    return "\n".join(lines)


def _parse_commands_from_md(path: Path) -> list[str]:
    """Best-effort extract: scan lines for `roam <command>` references."""
    text = path.read_text(encoding="utf-8", errors="replace")
    commands = []
    seen = set()
    for m in re.finditer(r"\broam\s+([a-z][-a-z0-9_]*)", text):
        cmd = m.group(1)
        if cmd not in seen:
            seen.add(cmd)
            commands.append(cmd)
    return commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--commands", help="comma-separated command list")
    src.add_argument("--from-md", help="markdown file to extract commands from")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of table")
    args = parser.parse_args(argv)

    if args.commands:
        commands = [c.strip() for c in args.commands.split(",") if c.strip()]
    elif args.from_md:
        commands = _parse_commands_from_md(Path(args.from_md))
    else:
        commands = [line.strip() for line in sys.stdin if line.strip()]

    if not commands:
        print("error: no commands given", file=sys.stderr)
        return 2

    rows = check_commands(commands)
    if args.json:
        import json
        print(json.dumps(rows, indent=2))
    else:
        print(_format_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
