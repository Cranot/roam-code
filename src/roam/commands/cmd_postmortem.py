"""``roam postmortem`` — replay current detectors against past commits.

Answers the question every prospect asks: *"would Roam have caught
the incident we shipped on Q1?"* Walks a commit range, runs the
critique + diff-blast-radius detectors against each commit's
**outgoing diff** as if it were a PR, and reports which findings
would have surfaced pre-merge.

Doesn't actually re-index the historical state — that would be
slow, memory-heavy, and the index might not even build cleanly on
old commits. Instead it inspects each commit's diff with the
current detector set; the detection rules are usually stable
enough across the time-window of interest (last 30-90 days) that
this gives an honest answer.

redacted.: stand-alone OSS artifact
that becomes the redacted demo. The pull
quote we want is:

    "If it retroactively catches the incident I shipped in Q1,
    I'll sign the PO by Friday."

Usage:

    roam postmortem HEAD~30..HEAD
    roam postmortem 2026-04-01..2026-04-30
    roam postmortem 1290978..843ceca

Output:

    VERDICT: 7 of 30 commits would have surfaced findings
    Top hits:
      839abc1 fix: parse YAML — 2 high-severity (clones-not-edited)
      4c47862 feat(yaml): list-of-dicts shape — 1 medium (impact)
      ...
"""

from __future__ import annotations

import json as _json
import subprocess

import click
from click.testing import CliRunner

from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json


def _git_log_in_range(commit_range: str, *, limit: int = 100) -> list[dict]:
    """Return [{sha, short_sha, subject, author, date}, ...] for commits in range."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--max-count={limit}",
                "--pretty=format:%H%x09%h%x09%s%x09%an%x09%ad",
                "--date=short",
                commit_range,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    out: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        out.append(
            {
                "sha": parts[0],
                "short_sha": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": parts[4],
            }
        )
    return out


def _diff_for_commit(sha: str) -> str:
    """Return the unified diff `git show --pretty='' SHA` produces."""
    try:
        result = subprocess.run(
            ["git", "show", "--pretty=", "--unified=3", sha],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _critique_diff(diff_text: str) -> dict:
    """Run roam critique --json on a diff (in-process)."""
    if not diff_text.strip():
        return {"summary": {}}
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "critique"], input=diff_text, catch_exceptions=False)
    try:
        return _json.loads(result.output) if result.output else {"summary": {}}
    except _json.JSONDecodeError:
        return {"summary": {}, "_parse_error": True}


def _summarize_finding_count(critique_payload: dict) -> tuple[int, int, int]:
    """Return (high, medium, low) counts from a critique envelope."""
    summary = critique_payload.get("summary") or {}
    high = int(summary.get("high_severity_findings") or summary.get("high_severity_total") or summary.get("high") or 0)
    # medium / low aren't always exposed; default to total minus high.
    total = int(summary.get("findings") or summary.get("total") or 0)
    medium = max(0, total - high)
    return high, medium, 0


def _short_finding_summary(critique_payload: dict, *, max_kinds: int = 3) -> list[str]:
    """Pull the top-N finding kinds from a critique envelope."""
    findings = critique_payload.get("findings") or critique_payload.get("checks") or []
    kinds: dict[str, int] = {}
    for f in findings:
        if not isinstance(f, dict):
            continue
        kind = f.get("check") or f.get("kind") or f.get("rule") or "?"
        kinds[kind] = kinds.get(kind, 0) + 1
    return [f"{k} x{v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:max_kinds]]


from roam.capability import roam_capability


@roam_capability(
    category="review",
    summary="Replay current detectors against past commits — show the findings that would have surfaced pre-merge.",
    inputs=["commit_range"],
    outputs=["findings_per_commit", "summary"],
    examples=[
        "roam postmortem HEAD~30..HEAD",
        "roam postmortem v12.0..v12.40 --limit 50",
    ],
    tags=["audit", "review", "phase0", "demo"],
    ai_safe=True,
    requires_index=True,
    since="12.40",
)
@click.command(name="postmortem")
@click.argument("commit_range", required=True)
@click.option(
    "--limit",
    default=100,
    type=int,
    help="Cap the number of commits walked (default 100).",
)
@click.option(
    "--show",
    "show_n",
    default=10,
    type=int,
    help="Top-N hits to display in text mode (default 10).",
)
@click.pass_context
def postmortem_cmd(ctx, commit_range: str, limit: int, show_n: int):
    """Replay current detectors against past commits.

    Walks COMMIT_RANGE (e.g. ``HEAD~30..HEAD`` or ``v12.30..v12.39``),
    runs ``roam critique`` against each commit's diff, and reports which
    findings would have surfaced pre-merge.

    \b
    Examples:
      roam postmortem HEAD~30..HEAD
      roam postmortem v12.30..HEAD
      roam postmortem main..feature/new-thing
      roam --json postmortem HEAD~50..HEAD > postmortem.json

    A retrospective replay: would today's detector set have caught the
    incidents already in your git history? Useful pre-purchase signal —
    "if it retroactively flags my Q1 incidents, the gate is worth wiring."
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    commits = _git_log_in_range(commit_range, limit=limit)
    if not commits:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "postmortem",
                        summary={
                            "verdict": "no commits matched",
                            "commit_range": commit_range,
                            "commits_scanned": 0,
                            "commits_with_findings": 0,
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: no commits matched {commit_range}", err=True)
        # Use return — ctx.exit() can swallow output when invoked via CliRunner.
        _ = EXIT_SUCCESS  # exit code surfaces via Click's normal return path
        return

    per_commit: list[dict] = []
    commits_with_findings = 0
    total_high = 0
    total_medium = 0

    with click.progressbar(commits, label="Replaying detectors") as bar:
        for commit in bar:
            diff_text = _diff_for_commit(commit["sha"])
            critique = _critique_diff(diff_text)
            high, medium, _low = _summarize_finding_count(critique)
            total_high += high
            total_medium += medium
            kinds = _short_finding_summary(critique)
            if high + medium > 0:
                commits_with_findings += 1
            per_commit.append(
                {
                    "sha": commit["sha"],
                    "short_sha": commit["short_sha"],
                    "subject": commit["subject"],
                    "author": commit["author"],
                    "date": commit["date"],
                    "high": high,
                    "medium": medium,
                    "kinds": kinds,
                }
            )

    # Rank: high-first, then medium, then total
    per_commit.sort(key=lambda c: (-c["high"], -c["medium"], -(c["high"] + c["medium"])))

    verdict = (
        f"{commits_with_findings} of {len(commits)} commits would have surfaced findings "
        f"({total_high} high, {total_medium} medium total)"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "postmortem",
                    summary={
                        "verdict": verdict,
                        "commit_range": commit_range,
                        "commits_scanned": len(commits),
                        "commits_with_findings": commits_with_findings,
                        "total_high": total_high,
                        "total_medium": total_medium,
                    },
                    commits=per_commit,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not commits_with_findings:
        click.echo("  (no findings surfaced over this range)")
        return

    click.echo()
    click.echo(f"Top {min(show_n, len(per_commit))} hits:")
    for c in per_commit[:show_n]:
        if c["high"] + c["medium"] == 0:
            continue
        kinds_str = " — " + ", ".join(c["kinds"]) if c["kinds"] else ""
        click.echo(
            f"  {c['short_sha']}  ({c['date']})  {c['subject'][:50]:<50s}  "
            f"high={c['high']} med={c['medium']}{kinds_str}"
        )
