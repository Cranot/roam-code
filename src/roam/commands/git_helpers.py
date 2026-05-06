"""Shared git invocation helpers.

Extracted from cmd_pr_analyze.py and cmd_metrics_push.py — both commands
shell out to ``git`` for the same fingerprint pieces (actor email, origin
URL, HEAD SHA, branch). Centralising avoids drift; mirrors the existing
shared-helper pattern (``codeowners_helpers``, ``changed_files``).

All functions are defensive: any subprocess failure returns the documented
sentinel (`""`, `"<unknown>"`, or `{}`) so the caller can degrade gracefully
without try/except boilerplate.
"""

from __future__ import annotations

import datetime as _dt
import subprocess

GIT_TIMEOUT_SECONDS = 5


def _run_git(args: list[str]) -> str:
    """Run a git command and return its stripped stdout, or '' on any failure."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def git_actor() -> str:
    """Return ``user.email`` (or ``user.name``) — the invoking actor.

    Returns ``"<unknown>"`` if neither is set, so audit-trail records
    still have a stable string for that field.
    """
    for args in (["git", "config", "user.email"], ["git", "config", "user.name"]):
        out = _run_git(args)
        if out:
            return out
    return "<unknown>"


def git_origin_url() -> str:
    """Return the configured ``remote.origin.url`` or empty string."""
    return _run_git(["git", "config", "--get", "remote.origin.url"])


def git_head_sha() -> str:
    """Return ``HEAD``'s full commit SHA or empty string."""
    return _run_git(["git", "rev-parse", "HEAD"])


def git_branch() -> str:
    """Return the current branch name (or ``HEAD`` when detached) or empty string."""
    return _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"])


def git_metadata() -> dict[str, str]:
    """Return git_sha + git_branch + git_origin in one shot.

    Used by metrics-push and any future command that wants a small
    repo-fingerprint dict. Empty values are omitted so consumers can
    test for presence with ``if "git_sha" in meta``.
    """
    out: dict[str, str] = {}
    sha = git_head_sha()
    if sha:
        out["git_sha"] = sha
    branch = git_branch()
    if branch:
        out["git_branch"] = branch
    origin = git_origin_url()
    if origin:
        out["git_origin"] = origin
    return out


def detect_roam_version() -> str:
    """Return the installed roam-code package version, or ``"unknown"``."""
    try:
        from roam import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def utc_timestamp() -> str:
    """Return a stable, suffix-Z UTC timestamp (e.g. ``2026-05-06T12:34:56.789012Z``).

    Centralised so audit-trail records stay byte-stable across Python
    versions — ``datetime.isoformat()`` formatting of timezone offsets
    has shifted between 3.9 / 3.10 / 3.11 / 3.12.
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
