"""Resolve the canonical repo root, surviving nested-worktree dispatch.

W572 background. Agents dispatched into nested Claude-Code worktrees
(``.claude/worktrees/.../.claude/worktrees/...``) run from a tree that
has a real ``.git`` link but lacks the project-root marker files —
chiefly ``CLAUDE.md`` — because those are uncommitted on ``main`` or
otherwise live only at the canonical top-level.

Tests that resolve a project file as ``Path(__file__).resolve().parents[1] / "CLAUDE.md"``
silently break in that environment: ``parents[1]`` lands on the
worktree root (which has ``tests/`` but not ``CLAUDE.md``), the path
exists check fails, and downstream assertions trip on missing
content. The two known instances at W572 are
``tests/test_auto_count_script.py`` and ``tests/test_compat_sweep.py``.

The fix is to ask git for the canonical toplevel
(``git rev-parse --show-toplevel``), which returns the same path
regardless of how deeply the worktree is nested under
``.claude/worktrees/`` -- nested worktrees still report the *main*
working tree's path when invoked through their linked ``.git`` file.

The helper falls back to the historical ``parents[1]`` walk if
``git`` is not available (e.g. sdist-style test runs without a
``.git`` directory) so test discovery on a vendored tarball still
works.

Public API (one name):

- ``repo_root() -> Path`` -- canonical project root containing
  ``CLAUDE.md`` and the rest of the source tree.

A drift guard in ``tests/test_repo_root_helper.py`` pins the contract:
the resolved path must contain both ``.git`` (file or dir) and
``CLAUDE.md``.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

__all__ = ["repo_root"]


_MARKER_FILES = ("CLAUDE.md", "pyproject.toml")


def _has_markers(path: Path) -> bool:
    """True iff ``path`` looks like the roam-code project root."""
    return all((path / m).exists() for m in _MARKER_FILES)


def _git_toplevel(start: Path) -> Path | None:
    """Ask git for the canonical toplevel; return None on any failure.

    Uses ``-C <start>`` so the call works whether the current process
    cwd happens to be inside the repo or not. Stdout is the absolute
    path to the *main* working tree's root even when invoked from a
    linked worktree (this is the property W572 relies on).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    candidate = Path(out).resolve()
    return candidate if candidate.exists() else None


def _walk_up_for_markers(start: Path) -> Path | None:
    """Walk up from ``start`` until a directory holds both markers."""
    for candidate in (start, *start.parents):
        if _has_markers(candidate):
            return candidate
    return None


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Return the canonical repo root (the directory containing ``CLAUDE.md``).

    Resolution order:

    1. ``git rev-parse --show-toplevel`` from this file's directory, if
       it produces a path that contains the marker files.
    2. Walk up from this file until a directory with the marker files
       is found.
    3. Fall back to ``Path(__file__).resolve().parents[2]`` (the
       historical ``parents[1]`` walk's analogue from inside
       ``tests/_helpers/``). This branch only executes if neither
       git nor a marker-file walk succeeds; tests will then fail
       loudly on the missing file rather than silently mis-resolve.
    """
    here = Path(__file__).resolve().parent  # tests/_helpers/

    # 1. Try git first -- the canonical answer in any worktree layout.
    toplevel = _git_toplevel(here)
    if toplevel is not None and _has_markers(toplevel):
        return toplevel

    # 2. Marker-file walk -- covers sdist / non-git installs.
    walked = _walk_up_for_markers(here)
    if walked is not None:
        return walked

    # 3. Last-resort historical fallback (parents[2] from
    # tests/_helpers/ == parents[1] from tests/test_*.py).
    return here.parents[1]
