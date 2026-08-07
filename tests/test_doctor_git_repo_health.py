"""Doctor check for git misconfiguration that makes git MISREPORT the worktree.

Every case here was observed in the wild on 2026-07-19, when an interrupted
integration flow left different residue in three separate repositories. The
shared property is that git keeps working perfectly while answering about the
wrong tree -- so nothing errors, and the damage looks like lost work.

Each test reproduces one real corruption in a throwaway repo and asserts the
check fires. The negative controls matter as much: two of these checks were
written wrong the first time and passed on the exact case they existed to
catch, which is why every positive case has a matching "must not fire" pair.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from roam.commands.cmd_doctor import _check_git_repo_health

# Deliberately NOT imported from the module under test. These tests must be
# runnable against a tree that predates the staleness threshold, so that the
# must-not-fire case below fails on its ASSERTION rather than on an ImportError
# -- an import error proves the module changed, not that the behaviour did.
_AN_HOUR = 3600.0


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A minimal, healthy git repo with one commit."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", ".")
    (r / "file.txt").write_text("hello\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return r


def _check_in(repo_dir: Path) -> dict:
    """Run the check with ``repo_dir`` as cwd, restoring cwd afterwards."""
    prev = os.getcwd()
    try:
        os.chdir(repo_dir)
        return _check_git_repo_health()
    finally:
        os.chdir(prev)


def test_healthy_repo_passes(repo: Path) -> None:
    assert _check_in(repo)["passed"] is True


def test_outside_a_repo_is_not_applicable(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _check_in(plain)
    assert result["passed"] is True
    assert result.get("_state") == "not_applicable"


def test_bare_true_on_populated_checkout_is_caught(repo: Path) -> None:
    """roam-code's case: git refuses worktree ops, so the tree looks absent."""
    _git(repo, "config", "--local", "core.bare", "true")
    result = _check_in(repo)
    assert result["passed"] is False
    assert "core.bare=true" in result["detail"]


def test_worktree_pointing_at_another_directory_is_caught(repo: Path, tmp_path: Path) -> None:
    """compile-code's case: git silently reports on a DIFFERENT tree.

    Regression guard for the first version of this check, which compared
    core.worktree against `git rev-parse --show-toplevel`. Git derives
    toplevel FROM core.worktree, so the two always agree and the check passed
    on the very misconfiguration it was written to detect. It must compare
    against the directory that owns the .git instead.
    """
    other = tmp_path / "other"
    other.mkdir()
    _git(repo, "config", "--local", "core.worktree", str(other))
    result = _check_in(repo)
    assert result["passed"] is False
    assert "DIFFERENT tree" in result["detail"]


def test_worktree_with_unresolvable_foreign_path_is_caught(repo: Path) -> None:
    """stoa's case: a WSL /mnt path in a config read by a non-WSL git.

    git itself cannot run here, so the check must read the config file
    directly rather than treating the rev-parse failure as "no repo".
    """
    config = repo / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8") + "\tworktree = /mnt/d/does/not/exist/anywhere\n",
        encoding="utf-8",
    )
    result = _check_in(repo)
    assert result["passed"] is False
    assert "core.worktree" in result["detail"] or "cannot operate" in result["detail"]


def test_stale_index_lock_is_caught(repo: Path) -> None:
    """A lock old enough that no writer could still hold it."""
    lock = repo / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    old = time.time() - _AN_HOUR
    os.utime(lock, (old, old))
    result = _check_in(repo)
    assert result["passed"] is False
    assert "index.lock" in result["detail"]
    assert "stale" in result["detail"]


def test_a_lock_a_live_writer_is_holding_is_not_called_stale(repo: Path) -> None:
    """The must-not-fire pair this check shipped without.

    git holds ``.git/index.lock`` for the duration of every index write, so a
    lock created moments ago is a LIVE writer. The original check fired on
    ``lock.exists()`` alone and reported "a git process was killed mid-write"
    -- a cause it never measured. Under pytest-xdist that made ``roam doctor``
    exit 2 on a healthy repo, which on 2026-08-07 took one of four CI lanes red
    at a commit the other three passed, and blocked a release.
    """
    (repo / ".git" / "index.lock").write_text("", encoding="utf-8")
    result = _check_in(repo)
    assert result["passed"] is True, f"a freshly-created index.lock was reported as a problem: {result['detail']}"


# NOT COVERED, deliberately, and recorded rather than left as a silent gap: the
# unreadable-age branch (stat raises OSError -> report anyway). Driving it needs
# Path.stat to fail for one path, but Path.exists() is itself implemented via
# stat, so any patch that reaches the branch also defeats the exists() guard
# that precedes it, and the test then proves nothing. That branch's behaviour is
# unchanged by this fix -- it reported before and reports now -- so it is a
# pre-existing coverage gap, not one this change introduced.


def test_detached_head_with_unreachable_commit_is_caught(repo: Path) -> None:
    """Commits reachable only from a detached HEAD are garbage-collectable."""
    _git(repo, "checkout", "-q", "--detach")
    (repo / "file.txt").write_text("orphan\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "orphan")
    result = _check_in(repo)
    assert result["passed"] is False
    assert "detached HEAD" in result["detail"]


def test_detached_head_on_a_branch_commit_does_not_fire(repo: Path) -> None:
    """Negative control, and a real regression guard.

    `git branch --contains` prints the detached HEAD itself as a pseudo-entry
    ("* (HEAD detached at abc1234)"). Counting that as a containing branch made
    the first version of this check pass on the orphan case above, so the
    parenthesised pseudo-ref must be filtered out -- but a genuinely
    branch-reachable detached HEAD must still be silent.
    """
    _git(repo, "checkout", "-q", "--detach")
    assert _check_in(repo)["passed"] is True
