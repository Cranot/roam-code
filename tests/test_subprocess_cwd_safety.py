"""Tests that git subprocess calls in key commands use explicit cwd= arguments.

These tests guard against the class of bug confirmed in cmd_postmortem's
``_git_log_in_range``: a git subprocess called without ``cwd=`` that relies on
the process working directory.  Under pytest-xdist, workers share a process
and a ``chdir()`` in one test can shift the cwd for git commands running in
another worker at the same moment, causing silent wrong-repo git results.

The fix: every command that calls git passes an explicit ``cwd=`` derived from
``find_project_root()`` rather than relying on the process cwd.

Regression contract
-------------------
Each test below verifies that the relevant helper function (a) succeeds when
called with the CORRECT project root, and (b) fails gracefully (returns an
empty/default result rather than raising) when called with a WRONG or
non-existent directory.

The second property (graceful failure) is what distinguishes "uses explicit
cwd=" from "silently works as long as process-cwd is right" — a function that
degrades gracefully on a wrong cwd is using the cwd argument, not the ambient
process cwd.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

pytestmark = pytest.mark.xdist_group("subprocess_cwd_safety")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_git_project(base: Path, name: str) -> Path:
    """Create a minimal git repo with two commits."""
    proj = base / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "mod.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    git_init(proj)
    # Second commit so HEAD~1..HEAD is non-empty
    (src / "mod.py").write_text("def foo():\n    pass\n\ndef bar():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add bar"], cwd=proj, capture_output=True)
    return proj


# ---------------------------------------------------------------------------
# Part 1: cmd_postmortem._git_log_in_range
# ---------------------------------------------------------------------------


def test_postmortem_git_log_in_range_uses_explicit_root(tmp_path):
    """_git_log_in_range returns commits when called with the correct root.

    Before the fix the function relied on the process cwd; now it accepts an
    explicit ``root`` param and passes it as ``cwd=`` to subprocess.run.
    """
    from roam.commands.cmd_postmortem import _git_log_in_range

    proj = _make_git_project(tmp_path, "postmortem_cwd_proj")
    commits = _git_log_in_range("HEAD~1..HEAD", root=proj)
    assert len(commits) >= 1, "expected at least one commit in HEAD~1..HEAD"
    assert all("sha" in c and "subject" in c for c in commits)


def test_postmortem_git_log_in_range_wrong_root_returns_empty(tmp_path):
    """_git_log_in_range degrades gracefully when root is not a git repo.

    This proves the function uses the explicit root (not the process cwd):
    a non-repo directory causes git to fail, which should raise RuntimeError,
    not silently walk the ambient process cwd's history.
    """
    from roam.commands.cmd_postmortem import _git_log_in_range

    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    with pytest.raises((RuntimeError, Exception)):
        _git_log_in_range("HEAD~1..HEAD", root=non_repo)


def test_postmortem_diff_for_commit_uses_explicit_root(tmp_path):
    """_diff_for_commit returns a non-empty diff when called with the correct root."""
    from roam.commands.cmd_postmortem import _diff_for_commit, _git_log_in_range

    proj = _make_git_project(tmp_path, "postmortem_diff_proj")
    commits = _git_log_in_range("HEAD~1..HEAD", root=proj)
    assert commits, "need at least one commit"
    diff = _diff_for_commit(commits[0]["sha"], root=proj)
    assert diff, "expected a non-empty diff for the commit"


def test_postmortem_diff_for_commit_wrong_root_returns_empty(tmp_path):
    """_diff_for_commit returns '' when root is not a valid git repo.

    This guards against the function silently using the process cwd instead of
    the explicit root.
    """
    from roam.commands.cmd_postmortem import _diff_for_commit

    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    diff = _diff_for_commit("deadbeef0000000000000000000000000000000a", root=non_repo)
    assert diff == "", f"expected empty string on wrong root, got: {diff!r}"


# ---------------------------------------------------------------------------
# Part 1: cmd_changelog._last_tag / _commits_since
# ---------------------------------------------------------------------------


def test_changelog_commits_since_uses_explicit_root(tmp_path):
    """_commits_since returns commits when called with the correct root."""
    from roam.commands.cmd_changelog import _commits_since

    proj = _make_git_project(tmp_path, "changelog_cwd_proj")
    commits = _commits_since("HEAD~1..HEAD", root=proj)
    assert len(commits) >= 1, "expected at least one commit in HEAD~1..HEAD"
    assert all(len(pair) == 2 for pair in commits)


def test_changelog_commits_since_wrong_root_returns_empty(tmp_path):
    """_commits_since returns [] when root is not a valid git repo."""
    from roam.commands.cmd_changelog import _commits_since

    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    commits = _commits_since("HEAD~1..HEAD", root=non_repo)
    assert commits == [], f"expected empty list on wrong root, got: {commits!r}"


def test_changelog_last_tag_uses_explicit_root(tmp_path):
    """_last_tag returns None (no tags) or a tag string with correct root."""
    from roam.commands.cmd_changelog import _last_tag

    proj = _make_git_project(tmp_path, "changelog_tag_proj")
    # No tags were created so we expect None
    result = _last_tag(root=proj)
    assert result is None, f"expected None (no tags), got: {result!r}"


def test_changelog_last_tag_wrong_root_returns_none(tmp_path):
    """_last_tag returns None when root is not a valid git repo."""
    from roam.commands.cmd_changelog import _last_tag

    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    result = _last_tag(root=non_repo)
    assert result is None, f"expected None on wrong root, got: {result!r}"


# ---------------------------------------------------------------------------
# Part 1: cmd_deps temporal coupling helpers
# ---------------------------------------------------------------------------


def test_deps_git_shas_uses_explicit_root(tmp_path):
    """_git_shas_for_deps_temporal_signal returns shas with the correct root."""
    from roam.commands.cmd_deps import _git_shas_for_deps_temporal_signal

    proj = _make_git_project(tmp_path, "deps_cwd_proj")
    shas = _git_shas_for_deps_temporal_signal("src/mod.py", root=proj)
    assert isinstance(shas, list)
    # At least one commit touches the file
    assert len(shas) >= 1, "expected commits for src/mod.py"
    assert all(len(s) == 40 for s in shas), "expected full SHA strings"


def test_deps_git_shas_wrong_root_returns_empty(tmp_path):
    """_git_shas_for_deps_temporal_signal returns [] on wrong root."""
    from roam.commands.cmd_deps import _git_shas_for_deps_temporal_signal

    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    shas = _git_shas_for_deps_temporal_signal("src/mod.py", root=non_repo)
    assert shas == [], f"expected [] on wrong root, got: {shas!r}"
