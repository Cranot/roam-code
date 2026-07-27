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
  --pre-push-updates FILE
             Scan committed blobs and messages introduced by the authoritative
             ref-update records Git supplied to the pre-push hook.

Exit codes:
  0  clean — no forbidden-pattern hits.
  1  one or more hits (printed, grouped by pattern) OR a usage / git error.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


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
_HISTORY_WHITELIST_FILES = frozenset(_patterns.WHITELIST_FILES)


def _load_prepush_refs_module():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(script_dir, "prepush_refs.py")
    spec = importlib.util.spec_from_file_location("internal_language_prepush_refs", module_path)
    if spec is None or spec.loader is None:
        sys.stderr.write(f"ERROR: could not load pre-push parser from {module_path}\n")
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_prepush_refs = _load_prepush_refs_module()


def _git_bytes(repo_root: str | None, args: list[str], *, operation: str) -> bytes:
    proc = subprocess.run(
        ["git", "--no-replace-objects", *args],
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        sys.stderr.write(f"ERROR: {operation} failed (git exit {proc.returncode}): {detail}\n")
        raise SystemExit(1)
    return proc.stdout


def _decode_git_text(data: bytes, *, operation: str, encoding: str, errors: str = "strict") -> str:
    try:
        return data.decode(encoding, errors=errors)
    except UnicodeDecodeError as exc:
        sys.stderr.write(f"ERROR: {operation} produced invalid {encoding} output.\n")
        raise SystemExit(1) from exc


def _decode_content_bytes(data: bytes, *, operation: str) -> str:
    """Decode text bodies, recognizing BOM-marked UTF-16/32 content."""
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return _decode_git_text(data, operation=operation, encoding="utf-32", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return _decode_git_text(data, operation=operation, encoding="utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return _decode_git_text(data, operation=operation, encoding="utf-8-sig", errors="replace")
    return _decode_git_text(data, operation=operation, encoding="utf-8", errors="replace")


def _repo_root() -> str:
    """Resolve the canonical repo root via git (works from any subdirectory)."""
    root = _decode_git_text(
        _git_bytes(None, ["rev-parse", "--show-toplevel"], operation="resolve repository root"),
        operation="resolve repository root",
        encoding="utf-8",
    ).strip()
    if not root:
        sys.stderr.write("ERROR: not inside a git repository (git rev-parse failed).\n")
        raise SystemExit(1)
    return root


def _staged_paths(repo_root: str) -> list[str]:
    """Posix relative paths of files staged for commit (added/copied/modified).

    NUL-delimited (``-z``) so paths with spaces / unusual characters survive.
    Excludes deletions (``--diff-filter=d`` drops them) — there's nothing to
    scan in a file being removed.
    """
    raw = _git_bytes(
        repo_root,
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=d"],
        operation="list staged paths",
    )
    text = _decode_git_text(raw, operation="list staged paths", encoding="utf-8")
    return [p for p in text.split("\0") if p]


def _tracked_paths(repo_root: str) -> list[str]:
    """Posix relative paths of every git-tracked file (NUL-delimited)."""
    raw = _git_bytes(repo_root, ["ls-files", "-z"], operation="list tracked paths")
    text = _decode_git_text(raw, operation="list tracked paths", encoding="utf-8")
    return [p for p in text.split("\0") if p]


def _read_staged_blob(repo_root: str, rel_path: str) -> str:
    """Return the STAGED content of ``rel_path`` (``git show :<path>``).

    Invalid UTF-8 bytes are replaced so they cannot hide later ASCII leaks.
    Git failures block the scan.
    """
    raw = _git_bytes(repo_root, ["show", f":{rel_path}"], operation=f"read staged blob {rel_path}")
    return _decode_git_text(raw, operation=f"read staged blob {rel_path}", encoding="utf-8", errors="replace")


def _read_disk_file(repo_root: str, rel_path: str) -> str | None:
    """Return on-disk content; missing tracked paths skip, read errors block."""
    abs_path = os.path.join(repo_root, rel_path)
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        sys.stderr.write(f"ERROR: could not read tracked file {rel_path}: {exc}\n")
        raise SystemExit(1) from exc


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
    sys.stderr.write("Fix it: reword or remove the flagged line. If the hit is intentional:\n")
    sys.stderr.write("  - add the file to WHITELIST_FILES in\n")
    sys.stderr.write("    scripts/internal_language_patterns.py (with a comment explaining why), or\n")
    sys.stderr.write("  - tighten the offending rule to exclude the legitimate case (a plain\n")
    sys.stderr.write("    regex for a SHAPE pattern; a matching-logic change, e.g. an\n")
    sys.stderr.write("    adjacency exemption, for a hashed literal term — see that module's\n")
    sys.stderr.write('    "Hashed literal terms" docstring section before touching one).\n')


def _resolve_commits(repo_root: str, revision_args: str | tuple[str, ...]) -> list[str]:
    args = (revision_args,) if isinstance(revision_args, str) else revision_args
    label = " ".join(args)
    raw = _git_bytes(repo_root, ["rev-list", "--reverse", *args], operation=f"resolve revisions {label}")
    text = _decode_git_text(raw, operation=f"resolve revisions {label}", encoding="ascii")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _collect_commit_message_hits_for_commits(repo_root: str, commits: list[str]) -> list[tuple[str, str, int, str]]:
    findings: list[tuple[str, str, int, str]] = []
    for commit in commits:
        try:
            raw = _prepush_refs.read_commit_object(Path(repo_root), commit)
        except _prepush_refs.PrePushGitError as exc:
            sys.stderr.write(f"ERROR: cannot inspect commit object: {exc}\n")
            raise SystemExit(1) from exc
        body = _decode_git_text(
            raw,
            operation=f"decode commit object {commit}",
            encoding="utf-8",
            errors="replace",
        )
        label = f"{commit[:7]} (commit object)"
        for name, line_no, text_snippet in scan_text(label, body):
            findings.append((label, name, line_no, text_snippet))
    return findings


def _collect_commit_blob_hits(repo_root: str, commits: list[str]) -> list[tuple[str, str, int, str]]:
    findings: list[tuple[str, str, int, str]] = []
    for commit in commits:
        paths_raw = _git_bytes(
            repo_root,
            [
                "diff-tree",
                "--root",
                "-r",
                "-m",
                "--no-commit-id",
                "--name-only",
                "-z",
                "--diff-filter=d",
                commit,
            ],
            operation=f"list changed paths for {commit}",
        )
        paths_text = _decode_git_text(
            paths_raw,
            operation=f"list changed paths for {commit}",
            encoding="utf-8",
        )
        for rel_path in dict.fromkeys(path for path in paths_text.split("\0") if path):
            label = f"{commit[:7]}:{rel_path} (published path name)"
            for name, line_no, text_snippet in scan_text(rel_path, rel_path):
                findings.append((label, name, line_no, text_snippet))
            if rel_path in _HISTORY_WHITELIST_FILES:
                continue
            body = _decode_content_bytes(
                _git_bytes(repo_root, ["show", f"{commit}:{rel_path}"], operation=f"read blob {commit}:{rel_path}"),
                operation=f"read blob {commit}:{rel_path}",
            )
            label = f"{commit[:7]}:{rel_path}"
            for name, line_no, text_snippet in scan_text(rel_path, body):
                findings.append((label, name, line_no, text_snippet))
    return findings


def _deduplicate_findings(findings: list[tuple[str, str, int, str]]) -> list[tuple[str, str, int, str]]:
    return list(dict.fromkeys(findings))


def _collect_commit_message_hits(repo_root: str, rev_range: str) -> list[tuple[str, str, int, str]]:
    """Scan commit messages in *rev_range* and fail closed on Git errors."""
    return _collect_commit_message_hits_for_commits(repo_root, _resolve_commits(repo_root, rev_range))


def _collect_pre_push_history_hits(
    repo_root: str,
    updates_path: str,
    *,
    remote_url: str | None = None,
) -> list[tuple[str, str, int, str]]:
    try:
        updates = _prepush_refs.load_pre_push_updates(Path(updates_path).resolve())
    except _prepush_refs.PrePushUpdateError as exc:
        sys.stderr.write(f"ERROR: invalid pre-push updates: {exc}\n")
        raise SystemExit(1) from exc

    direct_findings: list[tuple[str, str, int, str]] = []
    try:
        direct_texts = _prepush_refs.collect_direct_published_texts(Path(repo_root), updates)
    except _prepush_refs.PrePushGitError as exc:
        sys.stderr.write(f"ERROR: cannot inspect pushed objects: {exc}\n")
        raise SystemExit(1) from exc
    for published in direct_texts:
        if published.kind == "blob" and published.path in _HISTORY_WHITELIST_FILES:
            continue
        body = _decode_content_bytes(
            published.data,
            operation=f"decode pushed {published.kind} {published.oid}",
        )
        label = published.path
        for name, line_no, text_snippet in scan_text(label, body):
            direct_findings.append((label, name, line_no, text_snippet))

    published_commits: tuple[str, ...] = ()
    if remote_url is not None and any(update.is_new_remote_ref and not update.is_deletion for update in updates):
        try:
            published_commits = _prepush_refs.authoritative_remote_commits(Path(repo_root), remote_url)
        except (_prepush_refs.PrePushUpdateError, _prepush_refs.PrePushGitError) as exc:
            sys.stderr.write(f"ERROR: cannot verify destination history: {exc}\n")
            raise SystemExit(1) from exc

    commits: list[str] = []
    seen_commits: set[str] = set()
    revision_groups = _prepush_refs.unique_revision_args(updates, published_commits=published_commits)
    for revision_args in revision_groups:
        for commit in _resolve_commits(repo_root, revision_args):
            if commit in seen_commits:
                continue
            seen_commits.add(commit)
            commits.append(commit)
    return _deduplicate_findings(
        direct_findings
        + _collect_commit_blob_hits(repo_root, commits)
        + _collect_commit_message_hits_for_commits(repo_root, commits)
    )


_USAGE = (
    "Usage: python scripts/scan_internal_language.py "
    "(--staged | --all | --commits <range> | "
    "--pre-push-updates <file> [--remote-url <url>])\n"
)


def _parse_mode(argv: list[str]) -> tuple[bool, str | None, str | None, str | None] | None:
    """Resolve CLI args to exactly one scan mode.

    Returns ``(staged, commits_range, updates_path, remote_url)``. A non-None
    range or update path selects history mode; otherwise ``staged`` picks
    staged vs all-tracked.
    Returns None (after printing the error) on bad arguments.
    """
    commits_range: str | None = None
    updates_path: str | None = None
    remote_url: str | None = None
    flags: list[str] = []
    it = iter(argv)
    for a in it:
        if a == "--commits":
            commits_range = next(it, None)
            if not commits_range:
                sys.stderr.write("ERROR: --commits requires a rev range (e.g. origin/main..HEAD).\n")
                return None
        elif a == "--pre-push-updates":
            updates_path = next(it, None)
            if not updates_path:
                sys.stderr.write("ERROR: --pre-push-updates requires a captured update file.\n")
                return None
        elif a == "--remote-url":
            remote_url = next(it, None)
            if not remote_url:
                sys.stderr.write("ERROR: --remote-url requires a Git destination URL.\n")
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
    if sum((staged, scan_all, commits_range is not None, updates_path is not None)) != 1:
        sys.stderr.write(
            "ERROR: pass exactly one of --staged, --all, --commits <range>, or --pre-push-updates <file>.\n"
        )
        sys.stderr.write(_USAGE)
        return None
    if remote_url is not None and updates_path is None:
        sys.stderr.write("ERROR: --remote-url requires --pre-push-updates.\n")
        return None
    return staged, commits_range, updates_path, remote_url


def main(argv: list[str]) -> int:
    mode_args = _parse_mode(argv)
    if mode_args is None:
        return 1
    staged, commits_range, updates_path, remote_url = mode_args

    repo_root = _repo_root()
    if updates_path is not None:
        findings = _collect_pre_push_history_hits(repo_root, updates_path, remote_url=remote_url)
        mode = "pushed blobs and commit messages from pre-push updates"
    elif commits_range is not None:
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
