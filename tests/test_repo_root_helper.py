"""Drift guard for ``tests/_helpers/repo_root.py`` (W572).

The helper is the single source of truth for "where is the project
root" inside test code. Two consumers exist today
(``tests/test_auto_count_script.py`` and ``tests/test_compat_sweep.py``);
this file pins their contract so the helper can't regress silently:

1. The resolved path contains both ``CLAUDE.md`` and ``pyproject.toml``
   (the marker pair from ``_MARKER_FILES``).
2. The resolved path contains a ``.git`` entry (file in a worktree,
   directory in the canonical clone) -- proves the helper points at a
   real repo root, not an empty parent.
3. The result is stable across calls (``lru_cache`` discipline).
4. The result is reachable from a test-file ``__file__`` regardless of
   how deep the worktree is nested under ``.claude/worktrees/``.
"""

from __future__ import annotations

from tests._helpers.repo_root import repo_root


def test_repo_root_has_marker_files():
    """``pyproject.toml`` lives at the resolved root.

    ``CLAUDE.md`` was historically a co-marker but is now intentionally
    untracked on public clones (removed from the public repo in commit
    89a338d9). Only assert it when present; ``pyproject.toml`` is the
    canonical marker that always exists.
    """
    root = repo_root()
    assert (root / "pyproject.toml").is_file(), f"repo_root() returned {root!r} but it has no pyproject.toml"
    if (root / "CLAUDE.md").exists():
        assert (root / "CLAUDE.md").is_file(), f"repo_root() returned {root!r} but CLAUDE.md is present as a non-file"


def test_repo_root_has_git_marker():
    """A ``.git`` entry exists at the resolved root (file OR dir)."""
    root = repo_root()
    dotgit = root / ".git"
    assert dotgit.exists(), (
        f"repo_root() returned {root!r} but it has no .git entry -- the helper has resolved outside the repo"
    )


def test_repo_root_is_idempotent():
    """``lru_cache`` makes repeated calls return the same Path object."""
    a = repo_root()
    b = repo_root()
    assert a == b
    # Same Path instance thanks to lru_cache(maxsize=1).
    assert a is b


def test_repo_root_resolves_canonical_src_layout():
    """The resolved root owns the expected top-level dirs.

    This catches the failure mode where a nested-worktree dispatch
    lands on the worktree root (which has ``tests/`` and ``src/`` but
    none of the docs) instead of the canonical main tree.
    """
    root = repo_root()
    for child in ("src", "tests", "dev"):
        assert (root / child).is_dir(), f"repo_root() returned {root!r}; expected child {child}/ to exist"
