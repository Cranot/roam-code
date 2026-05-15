"""W741 regression: ``find_project_root`` must recognise worktree-pointer files.

A ``git worktree add`` directory does not have a ``.git`` *directory*; it has
a ``.git`` *file* whose contents are a single ``gitdir: <path>`` line that
points back into the main repo's ``.git/worktrees/<name>/`` subdirectory.

If ``find_project_root`` ever regresses to ``(p / ".git").is_dir()`` (which
short-circuits on the pointer file because it isn't a directory), the walker
will skip the worktree and return the OUTER repo. Downstream commands then
write their indexes against the wrong root — silent cross-worktree
contamination, exactly the Pattern 2 "silent fallback" failure shape.

The current implementation already uses ``.exists()``, which handles
directories AND pointer files AND symlinks uniformly. This test pins that
behaviour so the next refactor cannot regress it.

No real symlinks are used (Windows requires admin / Developer Mode); the
worktree-pointer-FILE pattern is sufficient and works on every platform.
"""

from __future__ import annotations

from pathlib import Path

from roam.db.connection import find_project_root


def test_find_project_root_recognises_worktree_pointer_file(tmp_path: Path) -> None:
    """A ``.git`` *file* (worktree pointer) must terminate the upward walk.

    Layout:
        tmp_path/main_repo/.git/                  <- real .git directory
        tmp_path/main_repo/worktrees/feature/.git <- pointer FILE (gitdir: ...)
                                          ^
                                          start the walk here

    Correct behaviour: return ``worktrees/feature`` (the inner worktree).
    Buggy behaviour: walk past the FILE because ``.is_dir()`` is False, then
    stop at ``main_repo`` (the outer real-.git directory).
    """
    parent = tmp_path / "main_repo"
    parent.mkdir()
    (parent / ".git").mkdir()

    worktree = parent / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    pointer = worktree / ".git"
    pointer.write_text("gitdir: /some/path/.git/worktrees/feature\n", encoding="utf-8")

    # Sanity: the pointer is a FILE, not a directory.
    assert pointer.is_file()
    assert not pointer.is_dir()

    result = find_project_root(str(worktree))
    assert result == worktree.resolve(), (
        f"find_project_root returned {result!r}, expected the worktree "
        f"{worktree.resolve()!r}. A walk past the pointer file would land on "
        f"{parent.resolve()!r}."
    )


def test_find_project_root_with_real_git_directory(tmp_path: Path) -> None:
    """Baseline: deep cwd under a ``.git`` *directory* still resolves to the repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    deep = repo / "src" / "pkg" / "subpkg"
    deep.mkdir(parents=True)

    result = find_project_root(str(deep))
    assert result == repo.resolve()


def test_find_project_root_no_repo_returns_start(tmp_path: Path) -> None:
    """No ``.git`` anywhere: the walker hits the filesystem root and falls
    back to the original start. The contract is "return *something*"; the
    fallback to ``Path(start).resolve()`` is documented behaviour.

    This is the Pattern 2 boundary: absent state is explicit (the caller
    gets a usable path), not silent-broken.
    """
    start = tmp_path / "not_a_repo"
    start.mkdir()
    result = find_project_root(str(start))
    # The walker walks ALL the way up past tmp_path until it hits the
    # filesystem root, then falls back to the resolved start. We only
    # care that the call doesn't crash and yields a real Path.
    assert isinstance(result, Path)
    assert result.is_absolute()


def test_find_project_root_inner_worktree_beats_outer_repo(tmp_path: Path) -> None:
    """The pointer FILE must win over a parent's real ``.git`` directory.

    This is the load-bearing assertion for W741: if a refactor swaps
    ``.exists()`` for ``.is_dir()``, this test fails because the walker
    skips the inner pointer and returns the outer repo root.
    """
    outer = tmp_path / "outer_repo"
    outer.mkdir()
    (outer / ".git").mkdir()

    inner = outer / "nested" / "worktree"
    inner.mkdir(parents=True)
    (inner / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")

    result = find_project_root(str(inner))
    assert result == inner.resolve()
    assert result != outer.resolve(), (
        "Regression: walker skipped the worktree pointer file and landed on "
        "the outer repo. find_project_root must detect .git via .exists(), "
        "not .is_dir()."
    )
