"""``roam changelog`` — surface or auto-suggest CHANGELOG entries.

read git commits since the last tag, classify them via prefix
heuristics (feat / fix / docs / chore / refactor / test), emit a draft
``## [Unreleased]`` markdown section. Reduces release-time toil for
projects that follow Conventional Commits but don't have a CI helper.
"""

from __future__ import annotations

import re
import subprocess

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_PREFIX_BUCKETS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(feat|feature)(\(.+?\))?:\s*", re.IGNORECASE), "Features"),
    (re.compile(r"^fix(\(.+?\))?:\s*", re.IGNORECASE), "Bug fixes"),
    (re.compile(r"^perf(\(.+?\))?:\s*", re.IGNORECASE), "Performance"),
    (re.compile(r"^refactor(\(.+?\))?:\s*", re.IGNORECASE), "Refactor"),
    (re.compile(r"^docs(\(.+?\))?:\s*", re.IGNORECASE), "Docs"),
    (re.compile(r"^test(\(.+?\))?:\s*", re.IGNORECASE), "Tests"),
    (re.compile(r"^chore(\(.+?\))?:\s*", re.IGNORECASE), "Chore"),
    (re.compile(r"^build(\(.+?\))?:\s*", re.IGNORECASE), "Build"),
    (re.compile(r"^ci(\(.+?\))?:\s*", re.IGNORECASE), "CI"),
    (re.compile(r"^release(\(.+?\))?:\s*", re.IGNORECASE), "Release"),
    (re.compile(r"^revert(\(.+?\))?:\s*", re.IGNORECASE), "Reverts"),
]
_FALLBACK_BUCKET = "Other"


def _last_tag() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _commits_since(rev_range: str) -> list[tuple[str, str]]:
    try:
        proc = subprocess.run(
            ["git", "log", rev_range, "--pretty=%h%x09%s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        sha = sha.strip()
        subject = subject.strip()
        if not sha or not subject:
            continue
        out.append((sha, subject))
    return out


def _classify(subject: str) -> tuple[str, str]:
    """Return (bucket, cleaned_subject)."""
    for pattern, bucket in _PREFIX_BUCKETS:
        m = pattern.match(subject)
        if m:
            return bucket, subject[m.end() :].strip()
    return _FALLBACK_BUCKET, subject


@roam_capability(
    name="changelog",
    category="getting-started",
    summary="List commits since the last tag, optionally as a markdown draft",
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
@click.command()
@click.option(
    "--since",
    "since_ref",
    type=str,
    default=None,
    help="Git rev to start from (default: last tag, or HEAD~30 if no tag).",
)
@click.option("--suggest", is_flag=True, help="Emit a draft markdown CHANGELOG section.")
@click.pass_context
def changelog(ctx, since_ref, suggest) -> None:
    """List commits since the last tag, optionally as a markdown draft.

    Without ``--suggest``: prints a flat list of commits.
    With ``--suggest``: groups commits into Conventional Commit buckets
    and emits a markdown ``## [Unreleased]`` section ready to paste at
    the top of CHANGELOG.md.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    base_rev = since_ref
    inferred_from_tag = False
    if base_rev is None:
        last = _last_tag()
        if last:
            base_rev = last
            inferred_from_tag = True
        else:
            base_rev = "HEAD~30"
    rev_range = f"{base_rev}..HEAD"
    commits = _commits_since(rev_range)
    buckets: dict[str, list[dict]] = {}
    for sha, subject in commits:
        bucket, cleaned = _classify(subject)
        buckets.setdefault(bucket, []).append({"sha": sha, "subject": cleaned, "raw": subject})
    bucket_counts = {k: len(v) for k, v in buckets.items()}
    verdict = f"{len(commits)} commit(s) in {rev_range}" if commits else f"no commits in {rev_range}"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "changelog",
                    summary={
                        "verdict": verdict,
                        "commit_count": len(commits),
                        "since": base_rev,
                        "inferred_from_tag": inferred_from_tag,
                        "buckets": bucket_counts,
                    },
                    range=rev_range,
                    commits=[{"sha": sha, "subject": subj} for sha, subj in commits],
                    buckets=buckets,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not commits:
        return
    click.echo()
    if not suggest:
        for sha, subject in commits:
            click.echo(f"  {sha}  {subject}")
        return
    click.echo("## [Unreleased]")
    click.echo()
    bucket_order = [
        "Features",
        "Bug fixes",
        "Performance",
        "Refactor",
        "Docs",
        "Tests",
        "Chore",
        "Build",
        "CI",
        "Reverts",
        "Release",
        _FALLBACK_BUCKET,
    ]
    for bucket in bucket_order:
        items = buckets.get(bucket)
        if not items:
            continue
        click.echo(f"### {bucket}")
        click.echo()
        for entry in items:
            click.echo(f"- {entry['subject']} ({entry['sha']})")
        click.echo()
