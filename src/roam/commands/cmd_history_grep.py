"""roam history-grep — through-history search with provenance.

Wraps ``git log -S/--pickaxe`` and emits, per pattern, the commits that
*introduced or removed* the literal string. Useful for postmortems
("when did this regex first appear?"), provenance investigations, and
auditing renames or deletions that no longer leave a trace in HEAD.

Output is grouped per pattern; each commit row carries author + date +
short SHA + summary. JSON envelope mirrors the text shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root
from roam.git_utils import worktree_git_env
from roam.output.formatter import json_envelope, to_json


def _git_pickaxe(
    root: Path,
    pattern: str,
    *,
    fixed: bool,
    case_insensitive: bool,
    since: str | None,
    until: str | None,
    limit: int,
    paths: list[str],
) -> list[dict]:
    """Run ``git log -S<pattern>`` and parse the output."""
    cmd = ["git", "log", "--no-merges", f"-n{limit}", "--pretty=format:%H%x09%an%x09%aI%x09%s"]
    cmd.append("-G" if not fixed else "-S")
    cmd.append(pattern)
    if case_insensitive:
        cmd.append("--regexp-ignore-case")
    if since:
        cmd.append(f"--since={since}")
    if until:
        cmd.append(f"--until={until}")
    if paths:
        cmd.append("--")
        cmd.extend(paths)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    commits: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        sha, author, date, summary = parts
        commits.append(
            {
                "sha": sha,
                "short_sha": sha[:8],
                "author": author,
                "date": date,
                "summary": summary,
            }
        )
    return commits


def _diff_polarity(root: Path, sha: str, pattern: str, fixed: bool) -> str | None:
    """Return 'introduced', 'removed', or 'modified' (or None on failure).

    Heuristic: count occurrences of the pattern in the +/- side of the
    diff for that commit. If only +, it was introduced; only -, removed;
    both, modified.
    """
    cmd = ["git", "show", "--unified=0", "--no-color", sha]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    plus = 0
    minus = 0
    if fixed:
        needle = pattern
        for ln in result.stdout.splitlines():
            if ln.startswith("+") and not ln.startswith("+++") and needle in ln:
                plus += 1
            elif ln.startswith("-") and not ln.startswith("---") and needle in ln:
                minus += 1
    else:
        import re

        rx = re.compile(pattern)
        for ln in result.stdout.splitlines():
            if ln.startswith("+") and not ln.startswith("+++") and rx.search(ln):
                plus += 1
            elif ln.startswith("-") and not ln.startswith("---") and rx.search(ln):
                minus += 1
    if plus and not minus:
        return "introduced"
    if minus and not plus:
        return "removed"
    if plus or minus:
        return "modified"
    return None


@roam_capability(
    name="history-grep",
    category="exploration",
    summary="Through-history search using git pickaxe (-S / -G)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("history-grep")
@click.argument("positional", required=False)
@click.option("-e", "--regex", "patterns", multiple=True, help="Pattern (repeatable).")
@click.option("-F", "--fixed-string", "fixed", is_flag=True, default=True, help="Literal mode (default).")
@click.option("-i", "--ignore-case", "ci", is_flag=True, help="Case-insensitive search.")
@click.option("--since", default=None, help="Only commits after this date (YYYY-MM-DD or relative).")
@click.option("--until", default=None, help="Only commits before this date.")
@click.option("-n", "limit", default=20, help="Max commits per pattern.")
@click.option("--polarity", is_flag=True, help="Annotate each commit as introduced/removed/modified (slower).")
@click.option(
    "-p",
    "--path",
    "paths",
    multiple=True,
    help="Restrict to these paths (repeatable).",
)
@click.pass_context
def history_grep_cmd(ctx, positional, patterns, fixed, ci, since, until, limit, polarity, paths):
    """Through-history search using git pickaxe (-S / -G).

    Examples:

      \b
      roam history-grep "DATABASE_URL"
      roam history-grep -e foo -e bar --polarity
      roam history-grep "deprecated_api" --since 2024-01-01
      roam history-grep "Article 12" -p docs/
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    pats: list[str] = []
    if positional:
        pats.append(positional)
    pats.extend(patterns)
    pats = [p for p in pats if p]
    if not pats:
        click.echo("VERDICT: no patterns provided")
        click.echo("Pass a positional pattern or -e/--regex.")
        raise SystemExit(2)

    ensure_index()
    root = find_project_root()

    per_pattern: dict[str, list[dict]] = {}
    for p in pats:
        commits = _git_pickaxe(
            root,
            p,
            fixed=fixed,
            case_insensitive=ci,
            since=since,
            until=until,
            limit=limit,
            paths=list(paths),
        )
        if polarity:
            for c in commits:
                c["polarity"] = _diff_polarity(root, c["sha"], p, fixed)
        per_pattern[p] = commits

    total = sum(len(v) for v in per_pattern.values())
    found = sum(1 for v in per_pattern.values() if v)
    verdict = f"{total} commit(s) across {found}/{len(pats)} pattern(s)"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "history-grep",
                    budget=token_budget,
                    summary={"verdict": verdict, "patterns": len(pats), "total_commits": total},
                    patterns=list(pats),
                    results=[{"pattern": p, "commits": commits} for p, commits in per_pattern.items()],
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    for p in pats:
        commits = per_pattern[p]
        click.echo(f"--- {p} — {len(commits)} commit(s) ---")
        if not commits:
            click.echo("  (no history)")
            click.echo()
            continue
        for c in commits:
            tag = f" [{c['polarity']}]" if c.get("polarity") else ""
            click.echo(f"  {c['short_sha']}  {c['date'][:10]}  {c['author']}{tag}")
            click.echo(f"    {c['summary']}")
        click.echo()
