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
from pathlib import Path

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
