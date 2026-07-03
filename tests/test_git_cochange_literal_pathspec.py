"""``_git_cochange_counts`` must treat ``target`` as a LITERAL pathspec.

The task-resolved target reaches ``git log -- <target>`` in
``src/roam/plan/compiler.py``. Without ``GIT_LITERAL_PATHSPECS=1``, git
interprets pathspec glob/magic chars in the target: a file named
``[a].py`` globs the char class ``[a]`` and ALSO matches ``a.py``, so
``git log -- '[a].py'`` returns the commits of BOTH files — broadening
the commit set and surfacing ``a.py`` as a spurious co-change partner.

Coverage:
1. Behavioural: a bracket-named target reports no spurious co-change
   partner (glob broadening disabled). Verified empirically against git.
2. Regression guard: normal sibling-file co-change is still detected
   (the literal-pathspec change does not break the happy path).
3. Unit: ``_git_literal_pathspec_env`` sets the var and inherits the
   process environment (as a copy).
4. Source pin: the ``git log`` call wires ``env=_git_literal_pathspec_env()``
   so a refactor can't silently drop it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402,F401  (re-exported convention)

from roam.plan import compiler as _compiler
from tests._helpers.repo_root import repo_root


def _bootstrap_repo(proj: Path) -> None:
    """Init a git repo with test identity (no files committed yet)."""
    proj.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=proj, check=True)


def _commit_all(proj: Path, msg: str) -> None:
    """Stage the whole working tree (no pathspec → no glob) and commit."""
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=proj, check=True)


def test_cochange_literal_pathspec_no_glob_broadening(tmp_path):
    """A bracket-named target must not broaden the commit set via globbing.

    Setup: ``a.py`` and ``[a].py`` are committed in SEPARATE commits, so
    they do not genuinely co-change. Without GIT_LITERAL_PATHSPECS=1,
    ``git log -- '[a].py'`` globs ``[a]`` → also matches ``a.py``, pulling
    the ``a.py`` commit in and reporting ``a.py`` as a co-change partner.
    The fix disables globbing → only the literal ``[a].py`` commit counts
    → no partner returned.
    """
    proj = tmp_path / "bracket_proj"
    _bootstrap_repo(proj)

    (proj / "a.py").write_text("a = 1\n", encoding="utf-8")
    _commit_all(proj, "add a")

    (proj / "[a].py").write_text("b = 1\n", encoding="utf-8")
    _commit_all(proj, "add bracket")

    result = _compiler._git_cochange_counts("[a].py", str(proj))
    assert result == [], (
        f"expected no co-change partners (files committed separately); got glob-broadened spurious result: {result!r}"
    )


def test_cochange_counts_normal_pair_still_detected(tmp_path):
    """Regression guard: genuine sibling-file co-change is still reported."""
    proj = tmp_path / "normal_proj"
    _bootstrap_repo(proj)

    (proj / "main.py").write_text("import util\n", encoding="utf-8")
    (proj / "util.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(proj, "add both")

    result = _compiler._git_cochange_counts("main.py", str(proj))
    assert ("util.py", 1) in result, f"expected util.py as a co-change partner of main.py; got {result!r}"


def test_git_literal_pathspec_env_sets_var(monkeypatch):
    """The helper stamps GIT_LITERAL_PATHSPECS=1 and inherits the env."""
    monkeypatch.setenv("ROAM_PATHSPEC_TEST_MARKER", "present")
    env = _compiler._git_literal_pathspec_env()
    assert env.get("GIT_LITERAL_PATHSPECS") == "1"
    # Inherits the process env (the marker set above survives)...
    assert env.get("ROAM_PATHSPEC_TEST_MARKER") == "present"
    # ...but is a distinct dict (mutations must not leak into os.environ).
    assert env is not os.environ
    env["ROAM_LEAK_GUARD"] = "1"
    assert "ROAM_LEAK_GUARD" not in os.environ


def test_source_pin_git_log_uses_literal_pathspec_env():
    """Source pin: the ``git log -- <target>`` call wires the literal-pathspec env."""
    src = (repo_root() / "src" / "roam" / "plan" / "compiler.py").read_text(encoding="utf-8")
    assert "def _git_literal_pathspec_env" in src
    assert "GIT_LITERAL_PATHSPECS" in src
    # The git-log subprocess call carries the env kwarg.
    assert "env=_git_literal_pathspec_env()" in src
