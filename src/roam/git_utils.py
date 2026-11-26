"""Git worktree helpers — avoid index.lock contention in sibling worktrees.

When multiple Claude Code sessions (or any parallel agents) run ``roam`` in
sibling worktrees of the same repository, they can race on
``.git/index.lock`` and produce ``fatal: Unable to create '.git/index.lock'``
errors. The fix is to point each subprocess at the worktree's own index
file via ``GIT_INDEX_FILE`` rather than letting git fall back to the shared
``.git/index`` of the main worktree.

Ported from upstream fork work.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

GIT_STATUS_PORCELAIN = ("git", "status", "--porcelain=v1", "-z", "--no-renames")
GIT_STATUS_WITH_HEAD = ("git", "status", "--porcelain=v2", "--branch", "--no-ahead-behind", "-z", "--no-renames")


def git_status_paths(status: bytes) -> set[str]:
    """Extract both sides of renames from status run with --no-renames."""
    return {record[3:].replace("\\", "/") for record in status.decode("utf-8", errors="replace").split("\0") if record}


def git_status_head_and_paths(status: bytes) -> tuple[str | None, set[str]]:
    """Read HEAD and paths from porcelain v2 with rename detection disabled."""
    # Ordinary, unmerged and untracked records have different field counts.
    path_offsets = {"1 ": 8, "u ": 10, "? ": 1}
    head = None
    paths: set[str] = set()
    for record in status.decode("utf-8", errors="replace").split("\0"):
        if record.startswith("# branch.oid "):
            oid = record.removeprefix("# branch.oid ")
            if oid != "(initial)":
                head = oid
            continue
        # A fixed number of fields precedes the path, which may contain spaces.
        fields = path_offsets.get(record[:2])
        if fields is not None:
            paths.add(record.split(" ", fields)[-1].replace("\\", "/"))
    return head, paths


def project_relative_git_paths(paths: set[str], root: Path, git_root: Path) -> set[str]:
    """Scope Git's worktree-relative names to a possibly nested project."""
    if root == git_root:
        return paths
    prefix = root.relative_to(git_root).as_posix() + "/"
    scoped_prefix = os.path.normcase(prefix)
    return {path[len(prefix) :] for path in paths if os.path.normcase(path).startswith(scoped_prefix)}


_GIT_SUBPROCESS_ENV_ALLOWLIST = frozenset(
    {
        # Needed for subprocess executable lookup on systems where git is not
        # on os.defpath, plus Windows process startup basics.
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        # Preserve ordinary locale/temp behavior without forwarding Git's own
        # control-plane variables such as GIT_DIR or GIT_CONFIG_COUNT.
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
    }
)


def _sanitized_git_subprocess_env() -> dict[str, str]:
    """Return the small ambient env needed to launch local git commands."""
    return {key: value for key in _GIT_SUBPROCESS_ENV_ALLOWLIST if (value := os.environ.get(key)) is not None}


def _worktree_index_path(cwd: Path) -> Path | None:
    """Return the worktree-specific git index path, or ``None`` for the main worktree.

    In a git worktree, ``cwd/.git`` is a **file** containing
    ``gitdir: /path/to/.git/worktrees/<name>``. The index for that worktree
    lives at that gitdir path + ``/index``.

    Returns ``None`` when *cwd* is the main worktree (where ``.git`` is a
    directory) or when no ``.git`` exists at all (callers should handle the
    not-a-repo case anyway).
    """
    dot_git = cwd / ".git"
    if not dot_git.is_file():
        return None
    try:
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = text.split(":", 1)[1].strip()
    gitdir_path = Path(gitdir)
    if not gitdir_path.is_absolute():
        gitdir_path = (cwd / gitdir_path).resolve()
    return gitdir_path / "index"


def worktree_git_env(cwd: Path | str) -> dict[str, str] | None:
    """Return an ``env`` dict with ``GIT_INDEX_FILE`` set for worktrees.

    Returns ``None`` when *cwd* is the main worktree (or not a worktree at
    all), which lets callers pass the result straight to
    ``subprocess.run(env=...)`` — ``None`` falls through to the inherited
    process environment.

    For linked worktrees this intentionally returns a sanitized environment
    instead of copying ``os.environ`` wholesale. Git-specific environment
    variables such as ``GIT_DIR`` / ``GIT_CONFIG_COUNT`` can redirect or
    reconfigure the subprocess; the worktree helper only needs to add
    ``GIT_INDEX_FILE``.

    Usage::

        env = worktree_git_env(repo_root)
        subprocess.run(["git", "ls-files"], cwd=repo_root, env=env)
    """
    cwd = Path(cwd) if not isinstance(cwd, Path) else cwd
    wt_index = _worktree_index_path(cwd)
    if wt_index is None:
        return None
    env = _sanitized_git_subprocess_env()
    env["GIT_INDEX_FILE"] = str(wt_index)
    return env


# These reports expose churn, co-change, or author/ownership evidence directly
# or use it to score and recommend changes.
HISTORY_REPORT_COMMANDS = frozenset(
    {
        "agent-export",
        "ai-ratio",
        "attest",
        "audit",
        "blame-reviewers",
        "bus-factor",
        "clean",
        "codeowners",
        "congestion",
        "context",
        "coupling",
        "dashboard",
        "dark-matter",
        "dead",
        "debt",
        "dev-profile",
        "diagnose",
        "diff",
        "drift",
        "fn-coupling",
        "forecast",
        "history-grep",
        "hotspots",
        "index-stats",
        "metrics",
        "minimap",
        "owner",
        "partition",
        "postmortem",
        "preflight",
        "pr-analyze",
        "pr-prep",
        "pr-risk",
        "recommend",
        "simulate-departure",
        "smells",
        "stats",
        "suggest-refactoring",
        "suggest-reviewers",
        "timeline",
        "trends",
        "understand",
        "vibe-check",
        "weather",
        "why-fail",
    }
)

HISTORY_WARNING_CONTEXT_KEY = "roam.git_history_warning"
# Composite status reflects whether the checks ran. Their history notice is
# advisory, as it is in the child envelopes they collect.
HISTORY_COMPOSITE_COMMANDS = frozenset({"audit", "pr-analyze", "pr-prep"})
history_depth_advisory: ContextVar[bool] = ContextVar("history_depth_advisory", default=False)


@contextmanager
def advisory_history_depth():
    """Keep history notices advisory during an in-process child invocation."""
    token = history_depth_advisory.set(True)
    try:
        yield
    finally:
        history_depth_advisory.reset(token)


def git_history_shallow(root: Path) -> bool | None:
    """Check the checkout once; None means Git could not establish its depth."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository", "--git-path", "shallow"],
            cwd=root,
            env=worktree_git_env(root),
            capture_output=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        return None
    depth, _, metadata = result.stdout.partition(b"\n")
    depth = depth.removesuffix(b"\r")
    metadata = metadata.removesuffix(b"\n").removesuffix(b"\r")
    if not metadata or b"\0" in metadata or depth not in (b"true", b"false"):
        return None
    marker = root / os.fsdecode(metadata)
    if marker.name != "shallow":
        return None
    if depth == b"true":
        return True

    # Git reports false when it cannot open the shallow file, even for a
    # broken symlink. Only an absent marker is consistent with a full clone.
    try:
        marker.lstat()
    except FileNotFoundError:
        try:
            return False if marker.parent.is_dir() else None
        except OSError:
            return None
    except OSError:
        return None
    return None
