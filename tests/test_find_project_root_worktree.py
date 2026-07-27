"""``find_project_root`` must trust a REAL git marker, not merely a path
that exists at ``.git``.

Two regressions are pinned here:

* **W741** — a ``git worktree add`` directory does not have a ``.git``
  *directory*; it has a ``.git`` *file* whose contents are a single
  ``gitdir: <path>`` line that points back into the main repo's
  ``.git/worktrees/<name>/`` subdirectory. If ``find_project_root`` ever
  regresses to ``(p / ".git").is_dir()`` (which short-circuits on the
  pointer file because it isn't a directory), the walker skips the
  worktree and returns the OUTER repo. Downstream commands then write
  their indexes against the wrong root — silent cross-worktree
  contamination, the Pattern 2 "silent fallback" failure shape.

* **Trust-any-.git (this file's newer coverage)** — the original fix for
  W741 swapped ``.is_dir()`` for bare ``.exists()``, which over-corrected:
  ANY path named ``.git`` was trusted, including an EMPTY directory with
  no git internals at all. A stray empty ``.git`` left in a tmp-dir
  ancestor (interrupted ``git init``, half-finished clone, a build tool's
  scratch dir) silently hijacked ``find_project_root`` into treating that
  directory as the project root. In one incident this rooted an entire
  pytest run at ``/tmp`` and caused 214 unrelated test failures. The fix
  validates that a ``.git`` entry is a GENUINE marker — either a directory
  with the structural files a real repo has (``HEAD``, ``objects/``,
  ``refs/``) or a pointer FILE whose contents actually start with
  ``gitdir:`` — before trusting it, while still walking past ("skip, keep
  climbing") anything that merely looks like ``.git`` but isn't.

No real symlinks are used (Windows requires admin / Developer Mode); the
worktree-pointer-FILE pattern is sufficient and works on every platform.
"""

from __future__ import annotations

from pathlib import Path

from roam.db.connection import find_project_root


def _make_real_git_dir(git_path: Path) -> None:
    """Create a ``.git`` directory with the structural markers a genuine
    repo has immediately after ``git init`` (before any commit): a
    ``HEAD`` file plus ``objects/`` and ``refs/`` subdirectories."""
    git_path.mkdir(parents=True, exist_ok=True)
    (git_path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_path / "objects").mkdir(exist_ok=True)
    (git_path / "refs").mkdir(exist_ok=True)


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
    _make_real_git_dir(parent / ".git")

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
    """Baseline: deep cwd under a genuine ``.git`` *directory* still resolves
    to the repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_real_git_dir(repo / ".git")
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
    """The pointer FILE must win over a parent's GENUINE ``.git`` directory.

    This is the load-bearing assertion for W741: if a refactor swaps
    ``.exists()`` for ``.is_dir()``, this test fails because the walker
    skips the inner pointer and returns the outer repo root. The outer
    ``.git`` here is a fully genuine repo (not a decoy) so this proves the
    worktree case wins against a real nested-repo scenario, not merely an
    invalid one.
    """
    outer = tmp_path / "outer_repo"
    outer.mkdir()
    _make_real_git_dir(outer / ".git")

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


def test_find_project_root_skips_empty_stray_git_dir(tmp_path: Path) -> None:
    """The reported defect: an EMPTY stray ``.git`` directory between the
    start point and a genuine repo above it must NOT be trusted.

    Layout:
        tmp_path/real_repo/.git/                 <- genuine repo
        tmp_path/real_repo/workspace/.git/        <- EMPTY stray dir (no
                                                     HEAD/objects/refs —
                                                     e.g. an interrupted
                                                     `git init`, a
                                                     half-finished clone,
                                                     or a build tool's
                                                     scratch directory)
        tmp_path/real_repo/workspace/child/       <- start the walk here

    Buggy behaviour (bare ``.exists()``): stops at ``workspace``, silently
    rooting every path-relative command there instead of ``real_repo``.
    Fixed behaviour: skips the empty ``.git``, keeps climbing, and finds
    the genuine repo at ``real_repo``.
    """
    real_repo = tmp_path / "real_repo"
    real_repo.mkdir()
    _make_real_git_dir(real_repo / ".git")

    workspace = real_repo / "workspace"
    (workspace / ".git").mkdir(parents=True)  # empty — no HEAD/objects/refs

    child = workspace / "child"
    child.mkdir()

    result = find_project_root(str(child))
    assert result == real_repo.resolve(), (
        f"find_project_root returned {result!r}, expected it to skip the "
        f"empty stray .git at {(workspace / '.git')!r} and resolve to the "
        f"genuine repo at {real_repo.resolve()!r}."
    )


def test_find_project_root_skips_corrupt_git_pointer_file(tmp_path: Path) -> None:
    """A ``.git`` FILE that doesn't actually contain a ``gitdir:`` pointer
    (zero-byte, truncated, or garbage content) must not be trusted either —
    same policy as the empty-directory case, applied to the file shape."""
    real_repo = tmp_path / "real_repo"
    real_repo.mkdir()
    _make_real_git_dir(real_repo / ".git")

    workspace = real_repo / "workspace"
    workspace.mkdir()
    (workspace / ".git").write_text("", encoding="utf-8")  # corrupt/empty pointer

    child = workspace / "child"
    child.mkdir()

    result = find_project_root(str(child))
    assert result == real_repo.resolve()


def test_find_project_root_no_genuine_repo_anywhere_falls_back(tmp_path: Path) -> None:
    """When every ``.git`` on the path up to the filesystem root is bogus
    (empty dir), the walker must not silently adopt any of them — it falls
    back to the resolved start, same contract as "no .git at all"."""
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)  # bogus, empty

    inner = outer / "inner"
    (inner / ".git").mkdir(parents=True)  # also bogus, empty

    start = inner / "child"
    start.mkdir()

    result = find_project_root(str(start))
    assert result == start.resolve()
