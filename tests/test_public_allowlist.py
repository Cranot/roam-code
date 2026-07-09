"""Default-deny guard for the public tracked tree."""

from __future__ import annotations

import fnmatch
import subprocess

from tests._helpers.repo_root import repo_root

PUBLIC_ALLOWLIST = (
    ".claude-plugin/**",
    ".github/**",
    ".githooks/**",
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "README.md",
    "action.yml",
    "bench/**",
    "benchmarks/**",
    "dev/**",
    "docs/**",
    "dynamic/**",
    "dynamic",
    "glama.json",
    "llms-install.md",
    "pyproject.toml",
    "rules/**",
    "scripts/**",
    "skills/**",
    "src/**",
    "templates/**",
    "tests/**",
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".mcp.json",
    ".pre-commit-config.yaml",
    ".pre-commit-hooks.yaml",
    ".roam-leak-patterns.py",
    ".roam-suppressions.yml",
    ".roamignore",
    "server.json",
    "uv.lock",
)


def _tracked_files() -> list[str]:
    root = repo_root()
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [path for path in proc.stdout.split("\0") if path and (root / path).exists()]


def _is_public(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in PUBLIC_ALLOWLIST)


def test_tracked_tree_matches_public_allowlist() -> None:
    tracked = _tracked_files()
    missing = sorted(path for path in tracked if not _is_public(path))
    assert not missing, f"add to PUBLIC_ALLOWLIST or gitignore it. Unexpected tracked files: {missing}"
