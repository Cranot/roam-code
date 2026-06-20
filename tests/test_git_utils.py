"""Tests for ``roam.git_utils.worktree_git_env``.

The helper exists to fix index.lock contention when multiple agents run
roam in sibling worktrees of the same repo. It must:

1. Return ``None`` for the main worktree (so callers fall through to the
   inherited environment).
2. Return ``None`` when ``.git`` doesn't exist at all (not-a-repo).
3. Return a sanitized env with ``GIT_INDEX_FILE`` for a real worktree.
4. Resolve relative ``gitdir`` paths against ``cwd``.

The test fixture builds a real ``git worktree add`` so we exercise the
on-disk shape rather than mocking it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from roam.git_utils import _worktree_index_path, worktree_git_env


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    """A minimal git repo with one commit (so worktrees can detach off HEAD)."""
    repo = tmp_path / "main"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(repo), check=True, env=env)
    return repo


class TestMainWorktree:
    def test_main_worktree_returns_none(self, main_repo: Path):
        """In the main worktree, ``.git`` is a directory — no override needed."""
        assert (main_repo / ".git").is_dir()
        assert _worktree_index_path(main_repo) is None
        assert worktree_git_env(main_repo) is None

    def test_not_a_repo_returns_none(self, tmp_path: Path):
        """No ``.git`` at all — let the caller see the not-a-repo error."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert worktree_git_env(empty) is None

    def test_dot_git_is_unreadable_directory(self, tmp_path: Path):
        """If ``.git`` is some other thing (e.g. permission-denied or symlink),
        the helper should not crash — it returns ``None``."""
        weird = tmp_path / "weird"
        weird.mkdir()
        (weird / ".git").mkdir()  # technically a main worktree shape
        assert worktree_git_env(weird) is None


class TestWorktree:
    def test_real_git_worktree_gets_index_file_override(self, main_repo: Path, tmp_path: Path):
        """``git worktree add`` produces ``.git`` as a *file* containing
        ``gitdir: ...``. The helper must surface that worktree's own
        index path — not the shared ``main/.git/index``.
        """
        wt_path = tmp_path / "wt"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", "branch1"],
            cwd=str(main_repo),
            check=True,
            env=env,
            capture_output=True,
        )
        # ``.git`` in the worktree is a file, not a dir.
        dot_git = wt_path / ".git"
        assert dot_git.is_file(), "expected .git as gitdir-pointer file"

        idx = _worktree_index_path(wt_path)
        assert idx is not None
        # The index path must point under the main repo's worktrees subdir,
        # NOT the main repo's own ``.git/index``.
        assert "worktrees" in str(idx), idx
        assert idx.name == "index", idx
        assert idx != main_repo / ".git" / "index"

    def test_env_dict_contains_launch_basics_plus_override(self, main_repo: Path, tmp_path: Path):
        """Returned env keeps launch essentials and adds ``GIT_INDEX_FILE``."""
        wt_path = tmp_path / "wt2"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", "branch2"],
            cwd=str(main_repo),
            check=True,
            env=env,
            capture_output=True,
        )

        out = worktree_git_env(wt_path)
        assert out is not None
        assert "GIT_INDEX_FILE" in out
        for key in ("PATH",):
            if key in os.environ:
                assert key in out

    def test_git_control_environment_is_not_forwarded(
        self, main_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Git control-plane env must not leak into linked-worktree commands."""
        wt_path = tmp_path / "wt-sanitized"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", "branch-sanitized"],
            cwd=str(main_repo),
            check=True,
            env=env,
            capture_output=True,
        )

        monkeypatch.setenv("GIT_DIR", "/tmp/attacker-git-dir")
        monkeypatch.setenv("GIT_WORK_TREE", "/tmp/attacker-work-tree")
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/tmp/attacker-hook")
        monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/tmp/attacker-diff")

        out = worktree_git_env(wt_path)
        assert out is not None
        assert "GIT_INDEX_FILE" in out
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0",
            "GIT_CONFIG_VALUE_0",
            "GIT_EXTERNAL_DIFF",
        ):
            assert key not in out

    def test_str_arg_accepted(self, main_repo: Path, tmp_path: Path):
        """The public helper accepts ``str`` as well as ``Path``."""
        wt_path = tmp_path / "wt3"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", "branch3"],
            cwd=str(main_repo),
            check=True,
            env=env,
            capture_output=True,
        )

        # str path → still works
        out = worktree_git_env(str(wt_path))
        assert out is not None
        assert "GIT_INDEX_FILE" in out


class TestParallelLockSafety:
    """Smoke test the original bug: two ``git ls-files`` calls in sibling
    worktrees should both succeed, not collide on ``index.lock``.
    """

    def test_two_worktrees_can_ls_files_concurrently(self, main_repo: Path, tmp_path: Path):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
        wt_a = tmp_path / "a"
        wt_b = tmp_path / "b"
        for wt, branch in ((wt_a, "branch-a"), (wt_b, "branch-b")):
            subprocess.run(
                ["git", "worktree", "add", str(wt), "-b", branch],
                cwd=str(main_repo),
                check=True,
                env=env,
                capture_output=True,
            )

        # Run ls-files in both worktrees back-to-back with the helper-provided env.
        for wt in (wt_a, wt_b):
            r = subprocess.run(
                ["git", "ls-files"],
                cwd=str(wt),
                env=worktree_git_env(wt),
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert r.returncode == 0, (r.stdout, r.stderr)
            # Each worktree sees its own README.md (inherited from main).
            assert "README.md" in r.stdout, r.stdout
