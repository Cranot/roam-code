"""``pr-prep <range>`` must score the range it was given, not the working tree.

``pr-prep`` bundles ``diff`` + ``critique`` + ``pr-risk``. The ``diff``
sub-invocation forwarded the positional ``commit_range``; the ``pr-risk``
one never did -- it only ever forwarded ``--staged``. So ``pr-prep`` built
its ``diff`` section from the requested range while building its
``pr_risk`` section from whatever ``pr-risk`` defaults to.

That is not a cosmetic mismatch. On a clean tree ``pr-risk`` with no range
resolves to ``no-changes`` and scores 0, so ``pr-prep HEAD~20..HEAD``
reported ``risk_score: 0`` for a range that ``pr-risk HEAD~20..HEAD``
independently scores 79/100 "Critical risk". A false-clean, and the
``ready_to_open`` verdict is computed off exactly that score.

These tests pin the forwarding at the argv boundary -- the layer where the
bug lived -- rather than asserting on a score, which would make them
depend on the risk model's tuning.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process  # noqa: E402


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def two_commit_project(tmp_path, monkeypatch):
    """A repo with two commits, so ``HEAD~1..HEAD`` actually resolves."""
    proj = tmp_path / "pr_prep_range_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )
    git_init(proj)

    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    return User('test')\n",
        encoding="utf-8",
    )
    git_commit(proj, "add auth")

    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _record_subcommand_args(monkeypatch):
    """Capture every argv ``pr-prep`` hands to ``_capture_json_subcommand``."""
    from roam.commands import cmd_pr_prep as _mod

    seen: list[list[str]] = []
    original = _mod._capture_json_subcommand

    def _spy(args):
        seen.append(list(args))
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _spy)
    return seen


def _invoke(runner, cwd, *extra):
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, ["--json", "pr-prep", *extra], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _args_for(seen, subcommand):
    for args in seen:
        if args and args[0] == subcommand:
            return args
    return None


def test_pr_prep_forwards_commit_range_to_pr_risk(cli_runner, two_commit_project, monkeypatch):
    """The range reaches ``pr-risk``, not just ``diff``."""
    seen = _record_subcommand_args(monkeypatch)

    result = _invoke(cli_runner, two_commit_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output

    risk_args = _args_for(seen, "pr-risk")
    assert risk_args is not None, f"pr-risk was never invoked; saw {seen!r}"
    assert "HEAD~1..HEAD" in risk_args, f"range not forwarded to pr-risk: {risk_args!r}"


def test_pr_prep_scopes_diff_and_pr_risk_to_the_same_range(cli_runner, two_commit_project, monkeypatch):
    """``diff`` and ``pr-risk`` must describe the same change set.

    The two sections sit side by side in one envelope, so a reader takes
    them as two views of one thing. If they can disagree about *what* they
    are describing, the envelope is misleading regardless of either
    section's own correctness.
    """
    seen = _record_subcommand_args(monkeypatch)

    result = _invoke(cli_runner, two_commit_project, "HEAD~1..HEAD")
    assert result.exit_code in (0, 5), result.output

    diff_args = _args_for(seen, "diff")
    risk_args = _args_for(seen, "pr-risk")
    assert diff_args is not None and risk_args is not None, seen
    assert diff_args[1:] == risk_args[1:], f"diff scoped {diff_args[1:]!r} but pr-risk scoped {risk_args[1:]!r}"


def test_pr_prep_staged_still_forwards_staged(cli_runner, two_commit_project, monkeypatch):
    """The pre-existing ``--staged`` path is unchanged."""
    seen = _record_subcommand_args(monkeypatch)

    result = _invoke(cli_runner, two_commit_project, "--staged")
    assert result.exit_code in (0, 5), result.output

    risk_args = _args_for(seen, "pr-risk")
    assert risk_args is not None, f"pr-risk was never invoked; saw {seen!r}"
    assert "--staged" in risk_args, risk_args


def test_pr_prep_without_range_passes_no_scope(cli_runner, two_commit_project, monkeypatch):
    """No range and no ``--staged`` keeps the bare working-tree default."""
    seen = _record_subcommand_args(monkeypatch)

    result = _invoke(cli_runner, two_commit_project)
    assert result.exit_code in (0, 5), result.output

    risk_args = _args_for(seen, "pr-risk")
    assert risk_args == ["pr-risk"], risk_args
