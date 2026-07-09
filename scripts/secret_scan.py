#!/usr/bin/env python
"""Pre-push secret scan over the commits being pushed.

This script reuses roam's repo-local secret patterns and adds a small
client-side safety net for pushed history. It scans each commit in a rev
range, inspects the files changed by that commit, and blocks on any
credential-shaped line unless the line is explicitly allowlisted.

Allowlist options:
- A line containing ``secretsallow`` in the file text skips that line.
- A repo-root ``.secretsallow`` file can list path globs to skip whole files.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

from roam.commands.cmd_secrets import (
    _COMPILED_PATTERNS,
    _is_env_var_line,
    _is_placeholder_line,
    _match_pattern_to_finding,
)

_ALLOWLIST_MARKER = "secretsallow"


def _repo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit("not inside a git repository")
    return Path(proc.stdout.strip())


def _git_text(repo_root: Path, args: list[str]) -> str | None:
    proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_nul_list(repo_root: Path, args: list[str]) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    return [part for part in proc.stdout.split("\0") if part]


def _load_path_allowlist(repo_root: Path) -> list[str]:
    allowlist_file = repo_root / ".secretsallow"
    if not allowlist_file.is_file():
        return []
    patterns: list[str] = []
    for raw in allowlist_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _path_is_allowlisted(rel_path: str, allowlist: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel_path, pattern) for pattern in allowlist)


def _line_is_allowlisted(line: str) -> bool:
    return _ALLOWLIST_MARKER in line.lower()


def _scan_text(
    rel_path: str,
    text: str,
    *,
    commit: str,
) -> list[dict]:
    findings: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _line_is_allowlisted(line):
            continue
        if _is_placeholder_line(line) or _is_env_var_line(line):
            continue
        for pat in _COMPILED_PATTERNS:
            finding = _match_pattern_to_finding(line, pat, rel_path, line_no, -1)
            if finding is None:
                continue
            finding["commit"] = commit
            findings.append(finding)
    return findings


def scan_commit_range(repo_root: Path, rev_range: str) -> list[dict]:
    """Scan every commit in *rev_range* and return secret findings."""
    commits = _git_text(repo_root, ["rev-list", "--reverse", rev_range])
    if commits is None:
        raise SystemExit(f"failed to resolve rev range: {rev_range}")

    path_allowlist = _load_path_allowlist(repo_root)
    findings: list[dict] = []
    for commit in [line.strip() for line in commits.splitlines() if line.strip()]:
        changed_paths = _git_nul_list(
            repo_root,
            [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-z",
                "--diff-filter=ACMR",
                commit,
            ],
        )
        for rel_path in changed_paths:
            if _path_is_allowlisted(rel_path, path_allowlist):
                continue
            blob = _git_text(repo_root, ["show", f"{commit}:{rel_path}"])
            if blob is None:
                continue
            findings.extend(_scan_text(rel_path, blob, commit=commit))
    findings.sort(key=lambda row: (row["commit"], row["file"], row["line"], row["pattern_name"]))
    return findings


def _format_findings(findings: list[dict]) -> str:
    lines = [f"BLOCKED: {len(findings)} secret finding(s) in pushed commits"]
    for finding in findings:
        lines.append(
            f"{finding['commit'][:7]} {finding['file']}:{finding['line']} "
            f"[{finding['pattern_name']}] {finding['matched_text']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan pushed commits for secret-shaped values.")
    parser.add_argument(
        "--rev-range",
        default="HEAD",
        help="Git rev range to scan (default: HEAD).",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    findings = scan_commit_range(repo_root, args.rev_range)
    if findings:
        print(_format_findings(findings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
