#!/usr/bin/env python
"""Anti-leak internal-language scanner — commit/push-time git-hook CLI.

Stdlib-only. Runs under a bare ``python scripts/scan_internal_language.py``
from anywhere inside the repo with NO third-party dependency and NO ``roam``
index build, so the git hook works regardless of which python / venv is
active. Imports the forbidden-pattern catalogue from the sibling
``internal_language_patterns`` module (the single source of truth shared with
the CI gate ``tests/test_no_internal_language.py``).

Modes (exactly one required):
  --staged   Scan the STAGED content of staged files (``git show :<path>``).
             This is what the pre-commit hook runs: it inspects what's about
             to be committed, not the working tree (which may differ).
  --all      Scan every git-tracked file as it sits on disk. This is what the
             pre-push hook runs: a full-tree backstop in case a leak slipped
             past commit-time (e.g. ``git commit --no-verify``).

Exit codes:
  0  clean — no forbidden-pattern hits.
  1  one or more hits (printed, grouped by pattern) OR a usage / git error.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


def _load_patterns_module():
    """Load the sibling ``internal_language_patterns.py`` by absolute path.

    ``scripts/`` is intentionally not a package (no ``__init__.py``) so it
    stays out of the wheel. Loading the catalogue by path keeps the git hook
    stdlib-only and avoids an orphan top-level import that static analysis
    cannot resolve.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(script_dir, "internal_language_patterns.py")
    spec = importlib.util.spec_from_file_location("internal_language_patterns", module_path)
    if spec is None or spec.loader is None:
        sys.stderr.write(f"ERROR: could not load pattern catalogue from {module_path}\n")
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_patterns = _load_patterns_module()
scan_text = _patterns.scan_text
should_scan = _patterns.should_scan


def _repo_root() -> str:
    """Resolve the canonical repo root via git (works from any subdirectory)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        sys.stderr.write("ERROR: not inside a git repository (git rev-parse failed).\n")
        sys.exit(1)
    return proc.stdout.strip()


def _staged_paths(repo_root: str) -> list[str]:
    """Posix relative paths of files staged for commit (added/copied/modified).

    NUL-delimited (``-z``) so paths with spaces / unusual characters survive.
    Excludes deletions (``--diff-filter=d`` drops them) — there's nothing to
    scan in a file being removed.
    """
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=d"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write("ERROR: `git diff --cached` failed.\n")
        sys.stderr.write(proc.stderr)
        sys.exit(1)
    return [p for p in proc.stdout.split("\0") if p]


def _tracked_paths(repo_root: str) -> list[str]:
    """Posix relative paths of every git-tracked file (NUL-delimited)."""
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write("ERROR: `git ls-files` failed.\n")
        sys.stderr.write(proc.stderr)
        sys.exit(1)
    return [p for p in proc.stdout.split("\0") if p]


def _read_staged_blob(repo_root: str, rel_path: str) -> str | None:
    """Return the STAGED content of ``rel_path`` (``git show :<path>``).

    Returns None when the blob can't be read as UTF-8 text (binary file) or
    the git call fails (e.g. a path that exists in the index but is
    unreadable) — the caller skips it, matching the CI gate's UnicodeDecode
    skip behaviour.
    """
    proc = subprocess.run(
        ["git", "show", f":{rel_path}"],
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_disk_file(repo_root: str, rel_path: str) -> str | None:
    """Return the on-disk content of ``rel_path`` as UTF-8, or None to skip."""
    abs_path = os.path.join(repo_root, rel_path)
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, encoding="utf-8") as fh:
            return fh.read()
    except (UnicodeDecodeError, OSError):
        return None


def _collect_hits(repo_root: str, *, staged: bool) -> list[tuple[str, str, int, str]]:
    """Return [(rel_path, pattern_name, line_no, line_text)] for every hit."""
    if staged:
        paths = _staged_paths(repo_root)
        reader = _read_staged_blob
    else:
        paths = _tracked_paths(repo_root)
        reader = _read_disk_file

    findings: list[tuple[str, str, int, str]] = []
    for rel in paths:
        if not should_scan(rel):
            continue
        text = reader(repo_root, rel)
        if text is None:
            continue
        for name, line_no, text_snippet in scan_text(rel, text):
            findings.append((rel, name, line_no, text_snippet))
    return findings


def _print_hits(findings: list[tuple[str, str, int, str]], *, mode: str) -> None:
    """Print findings grouped by pattern, plus a how-to-fix footer."""
    by_pattern: dict[str, list[tuple[str, int, str]]] = {}
    for rel, name, line_no, text in findings:
        by_pattern.setdefault(name, []).append((rel, line_no, text))

    sys.stderr.write(f"\n{len(findings)} internal-language leak(s) found ({mode} scan):\n")
    for name in sorted(by_pattern):
        hits = by_pattern[name]
        sys.stderr.write(f"\n  [{name}] — {len(hits)} hit(s):\n")
        for rel, line_no, text in hits[:8]:
            sys.stderr.write(f"    {rel}:{line_no}  {text}\n")
        if len(hits) > 8:
            sys.stderr.write(f"    ... and {len(hits) - 8} more\n")

    sys.stderr.write("\n")
    sys.stderr.write("Each pattern was deliberately removed during the 2026-05 stealth sweeps.\n")
    sys.stderr.write("If a hit is intentional:\n")
    sys.stderr.write("  - add the file to WHITELIST_FILES in\n")
    sys.stderr.write("    scripts/internal_language_patterns.py (with a comment explaining why), or\n")
    sys.stderr.write("  - tighten the offending regex to exclude the legitimate case.\n")


def _collect_commit_message_hits(repo_root: str, rev_range: str) -> list[tuple[str, str, int, str]]:
    """Scan COMMIT MESSAGES in *rev_range* (e.g. ``origin/main..HEAD``).

    Commit messages are published with the code — a leaky message reaches the
    public repo even when every file is clean (and rewriting pushed history
    is far costlier than rewording a file). Returns the same finding tuples
    as :func:`_collect_hits`, with ``<short-sha> (commit message)`` standing
    in for the file path.
    """
    proc = subprocess.run(
        ["git", "log", "--format=%h%x00%B%x01", rev_range],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        # Range doesn't resolve (no upstream yet, shallow clone) — nothing to
        # scan is the correct fail-open behaviour for a hook context.
        return []
    findings: list[tuple[str, str, int, str]] = []
    for chunk in proc.stdout.split("\x01"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, body = chunk.partition("\x00")
        for name, line_no, text_snippet in scan_text(f"{sha.strip()} (commit message)", body):
            findings.append((f"{sha.strip()} (commit message)", name, line_no, text_snippet))
    return findings


_USAGE = "Usage: python scripts/scan_internal_language.py (--staged | --all | --commits <range>)\n"


def _parse_mode(argv: list[str]) -> tuple[bool, str | None] | None:
    """Resolve CLI args to exactly one scan mode.

    Returns ``(staged, commits_range)`` — ``commits_range`` set means
    commit-message mode; otherwise ``staged`` picks staged vs all-tracked.
    Returns None (after printing the error) on bad arguments.
    """
    commits_range: str | None = None
    flags: list[str] = []
    it = iter(argv)
    for a in it:
        if a == "--commits":
            commits_range = next(it, None)
            if not commits_range:
                sys.stderr.write("ERROR: --commits requires a rev range (e.g. origin/main..HEAD).\n")
                return None
        else:
            flags.append(a)
    staged = "--staged" in flags
    scan_all = "--all" in flags
    unknown = [a for a in flags if a not in ("--staged", "--all")]
    if unknown:
        sys.stderr.write(f"ERROR: unknown argument(s): {' '.join(unknown)}\n")
        sys.stderr.write(_USAGE)
        return None
    if sum((staged, scan_all, commits_range is not None)) != 1:
        sys.stderr.write("ERROR: pass exactly one of --staged, --all, or --commits <range>.\n")
        sys.stderr.write(_USAGE)
        return None
    return staged, commits_range


def main(argv: list[str]) -> int:
    mode_args = _parse_mode(argv)
    if mode_args is None:
        return 1
    staged, commits_range = mode_args

    repo_root = _repo_root()
    if commits_range is not None:
        findings = _collect_commit_message_hits(repo_root, commits_range)
        mode = f"commit-messages {commits_range}"
    else:
        findings = _collect_hits(repo_root, staged=staged)
        mode = "staged" if staged else "all-tracked"
    if not findings:
        return 0

    _print_hits(findings, mode=mode)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
