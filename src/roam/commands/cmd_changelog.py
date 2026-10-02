"""``roam changelog`` — surface or auto-suggest CHANGELOG entries.

read git commits since the last tag, classify them via prefix
heuristics (feat / fix / docs / chore / refactor / test), emit a draft
``## [Unreleased]`` markdown section. Reduces release-time toil for
projects that follow Conventional Commits but don't have a CI helper.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because changelog outputs are invocation-scoped git-repo-metadata
enumerations (commit subjects grouped by Conventional Commits prefix) —
not per-location code violations. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1221-audit memo.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
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


def _last_tag(root: Path | None = None) -> str | None:
    """Return the most recent git tag, or None when there are no tags or git is unavailable.

    ``root`` is the project root directory used as ``cwd`` for the git
    subprocess.  When omitted, ``find_project_root()`` is called so that
    parallel test workers and MCP callers with a shifted process-cwd all
    resolve to the correct repository.
    """
    project_root = root if root is not None else find_project_root()
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=str(project_root),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _commits_since(rev_range: str, root: Path | None = None) -> list[tuple[str, str]]:
    """Return (sha, subject) pairs for commits in ``rev_range``.

    ``root`` is the project root directory used as ``cwd`` for the git
    subprocess.  When omitted, ``find_project_root()`` is called so that
    parallel test workers and MCP callers with a shifted process-cwd all
    resolve to the correct repository.
    """
    project_root = root if root is not None else find_project_root()
    try:
        proc = subprocess.run(
            ["git", "log", rev_range, "--pretty=%h%x09%s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            cwd=str(project_root),
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
@click.command(name="changelog")
@click.option(
    "--since",
    "since_ref",
    type=str,
    default=None,
    help="Git rev to start from (default: last tag, or HEAD~30 if no tag).",
)
@click.option("--suggest", is_flag=True, help="Emit a draft markdown CHANGELOG section.")
@click.pass_context
def changelog_command(ctx, since_ref, suggest) -> None:
    """List commits since the last tag, optionally as a markdown draft.

    Without ``--suggest``: prints a flat list of commits.
    With ``--suggest``: groups commits into Conventional Commit buckets
    and emits a markdown ``## [Unreleased]`` section ready to paste at
    the top of CHANGELOG.md.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Resolve the project root once so every git subprocess call uses an
    # explicit cwd= regardless of the process working directory.  Under
    # pytest-xdist, workers share a process and a chdir() in one test can
    # shift the cwd for git commands running concurrently in another.
    try:
        _project_root = find_project_root()
    except Exception:  # noqa: BLE001 -- best-effort; git calls degrade gracefully
        _project_root = Path(".")

    base_rev = since_ref
    inferred_from_tag = False
    if base_rev is None:
        last = _last_tag(root=_project_root)
        if last:
            base_rev = last
            inferred_from_tag = True
        else:
            base_rev = "HEAD~30"
    rev_range = f"{base_rev}..HEAD"
    commits = _commits_since(rev_range, root=_project_root)
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
