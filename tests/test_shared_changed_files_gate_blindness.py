"""A git that could not answer is not a tree with no changes.

``changed_files.get_changed_files`` returns ``list[str]``. That type has no
channel for "I could not ask git", so every failure mode of
``git diff --name-only`` -- binary missing, subprocess timeout, non-zero
return from a corrupt index or an unreadable object store -- arrived at all
19 caller modules as the same ``[]`` that means "the tree is clean". The
``returncode != 0`` branch did not even call ``log_swallowed``: the failure
was discarded with no record anywhere.

Measured against the tree that shipped, in a repo with a genuinely modified
tracked file and ``GIT_INDEX_FILE`` pointed at a garbage file::

    $ git diff --name-only          -> fatal: index file smaller than expected, rc 128
    $ roam adversarial --fail-on-critical
      VERDICT: No changes detected
      No uncommitted changes found.                                   rc 0
    $ roam --json adversarial --fail-on-critical
      {"changed_files": 0, "partial_success": false,
       "verdict": "No changes detected", "critical": 0}               rc 0
    $ roam pr-diff --fail-on-degradation
      No changed files detected.                                      rc 0

The correct behaviour already existed in this repo, on a PRIVATE helper:
``cmd_delete_check._git_diff`` returns ``(diff_text, error_kind)`` and the
command refuses under ``--ci`` (CP45/CP46). It had simply never reached the
shared helper.

WHAT IS NOT COVERED HERE
------------------------
``pr-analyze --gate`` reads the same substrate and publishes
``state: "no_changes"`` for a diff it could not read. That is a distinct
defect -- the state VOCABULARY is wrong, not just the exit code -- and it is
pinned in its own file.

The 15 non-gated callers of ``get_changed_files`` keep the bare-list
signature deliberately. A command that only reports cannot authorize
anything, so an empty change set there is a display question, not a false
clean.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.commands.changed_files import (
    GIT_ERROR,
    GIT_NOT_AVAILABLE,
    GIT_TIMEOUT,
    get_changed_files,
    get_changed_files_status,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _dirty_repo(root: Path) -> Path:
    """A committed repo with one REAL uncommitted edit to a tracked file."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n", encoding="utf-8")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / "src" / "a.py").write_text(
        "def alpha(x):\n    return x + 2\n\n\ndef beta(x):\n    return alpha(x)\n",
        encoding="utf-8",
    )
    return root


def _broken_index(tmp_path: Path) -> Path:
    """A file git will reject as an index: `index file smaller than expected`."""
    garbage = tmp_path / "garbage.idx"
    garbage.write_bytes(b"garbage")
    return garbage


def _run(cwd: Path, *args: str, git_index: Path | None = None) -> subprocess.CompletedProcess:
    """Subprocess, because the thing under test is a process exit status.

    A CI gate reads ``$?``. Invoking the callback in-process would test a
    different object than the one a workflow runs.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1")
    if git_index is not None:
        env["GIT_INDEX_FILE"] = str(git_index)
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )


@pytest.fixture()
def indexed_dirty_repo(tmp_path: Path) -> Path:
    """Index with a HEALTHY git, so only the gate run sees the broken one.

    Keeps the property under test to "the gate read a failed diff as clean"
    rather than entangling it with what the indexer does on a broken repo.
    """
    repo = _dirty_repo(tmp_path / "repo")
    built = _run(repo, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    return repo


# ---------------------------------------------------------------------------
# The shared helper itself
# ---------------------------------------------------------------------------


def test_helper_reports_git_error_rather_than_an_empty_changeset(tmp_path: Path) -> None:
    """The `returncode != 0` branch used to `return []` with no record at all.

    The repo below has a modified tracked file, so ``[]`` is not merely
    uninformative here -- it is false.
    """
    repo = _dirty_repo(tmp_path / "repo")
    garbage = _broken_index(tmp_path)

    healthy, healthy_err = get_changed_files_status(repo)
    assert healthy_err is None
    assert healthy == ["src/a.py"], healthy

    old = os.environ.get("GIT_INDEX_FILE")
    os.environ["GIT_INDEX_FILE"] = str(garbage)
    try:
        paths, error_kind = get_changed_files_status(repo)
    finally:
        if old is None:
            os.environ.pop("GIT_INDEX_FILE", None)
        else:
            os.environ["GIT_INDEX_FILE"] = old

    assert error_kind == GIT_ERROR, (
        "a `git diff` that exited non-zero was reported as a successful "
        f"measurement (error_kind={error_kind!r}, paths={paths!r})"
    )
    assert paths == []


def test_helper_reports_git_missing_and_git_timeout_distinctly(monkeypatch, tmp_path: Path) -> None:
    """Three failure modes, three names -- they were one empty list.

    ``git_not_available`` and ``git_timeout`` are the two the old code DID
    log; they still returned the clean-tree shape to the caller, which is the
    half that mattered.
    """
    repo = _dirty_repo(tmp_path / "repo")

    def _raise_missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _raise_missing)
    paths, error_kind = get_changed_files_status(repo)
    assert (paths, error_kind) == ([], GIT_NOT_AVAILABLE)

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git diff", timeout=10)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    paths, error_kind = get_changed_files_status(repo)
    assert (paths, error_kind) == ([], GIT_TIMEOUT)

    assert len({GIT_NOT_AVAILABLE, GIT_TIMEOUT, GIT_ERROR}) == 3


def test_bare_helper_keeps_its_list_signature(tmp_path: Path) -> None:
    """The 15 report-only callers must not have to change.

    This is the reason the fix ADDS a function rather than rewriting the
    existing one's return type across 19 modules.
    """
    repo = _dirty_repo(tmp_path / "repo")
    assert get_changed_files(repo) == ["src/a.py"]


# ---------------------------------------------------------------------------
# adversarial --fail-on-critical
# ---------------------------------------------------------------------------


def test_adversarial_gate_refuses_a_diff_it_could_not_read(indexed_dirty_repo: Path, tmp_path: Path) -> None:
    run = _run(
        indexed_dirty_repo,
        "adversarial",
        "--fail-on-critical",
        git_index=_broken_index(tmp_path),
    )
    assert run.returncode != 0, (
        "`roam adversarial --fail-on-critical` authorized a tree whose diff it "
        f"could not read.\nstdout: {run.stdout.strip()[:600]!r}"
    )
    assert "No changes detected" not in run.stdout, run.stdout[:600]
    assert "cannot gate" in run.stdout, run.stdout[:600]


def test_adversarial_gate_still_passes_a_readable_dirty_tree(indexed_dirty_repo: Path) -> None:
    """The control. "Nothing critical changed" is an ANSWER and must still be 0.

    Without this the fix would have made the gate useless rather than honest.
    """
    run = _run(indexed_dirty_repo, "adversarial", "--fail-on-critical")
    assert run.returncode == 0, (
        f"the gate now over-refuses a readable repo.\nstdout: {run.stdout.strip()[:600]!r}\n"
        f"stderr: {run.stderr.strip()[:600]!r}"
    )
    assert "cannot gate" not in run.stdout


def test_adversarial_json_publishes_the_git_error(indexed_dirty_repo: Path, tmp_path: Path) -> None:
    """The machine half. ``partial_success: false`` was the load-bearing lie.

    An agent reading the envelope had no field to branch on: the degraded run
    and the clean run were byte-comparable apart from nothing at all.
    """
    run = _run(
        indexed_dirty_repo,
        "--json",
        "adversarial",
        "--fail-on-critical",
        git_index=_broken_index(tmp_path),
    )
    payload = json.loads(run.stdout[run.stdout.find("{") :])
    summary = payload["summary"]
    assert summary.get("partial_success") is True, summary
    assert summary.get("git_error") == GIT_ERROR, summary
    assert summary["verdict"] != "No changes detected", summary


# ---------------------------------------------------------------------------
# pr-diff --fail-on-degradation
# ---------------------------------------------------------------------------


def test_pr_diff_gate_refuses_a_diff_it_could_not_read(indexed_dirty_repo: Path, tmp_path: Path) -> None:
    run = _run(
        indexed_dirty_repo,
        "pr-diff",
        "--fail-on-degradation",
        git_index=_broken_index(tmp_path),
    )
    assert run.returncode != 0, (
        "`roam pr-diff --fail-on-degradation` authorized a tree whose diff it "
        f"could not read.\nstdout: {run.stdout.strip()[:600]!r}"
    )
    assert "cannot gate" in run.stdout, run.stdout[:600]


def test_pr_diff_gate_still_passes_a_readable_tree(indexed_dirty_repo: Path) -> None:
    """The must-not-fire control for the git_error refusal, and ONLY that.

    This file's subject is whether the gate reads a failed diff as clean, so
    this control exists to prove that teaching it to refuse an unreadable diff
    did not teach it to refuse everything.

    It needs a baseline to say that. ``roam init`` alone leaves zero snapshot
    rows (measured in W1526), so without this save the repo is missing a
    baseline as well as being readable, and the control fires on the SECOND
    property -- the one W1526 deliberately made refusing, for a reason that has
    nothing to do with this file. Asserting exit 0 here without a snapshot is
    asserting that a requested gate may authorize a comparison it never made.

    So: record a baseline, then require the pass. Readable tree, computable
    delta, exit 0.
    """
    saved = _run(indexed_dirty_repo, "trends", "--save")
    assert saved.returncode == 0, (
        f"could not record the baseline this control needs.\nstdout: {saved.stdout.strip()[:600]!r}"
    )

    run = _run(indexed_dirty_repo, "pr-diff", "--fail-on-degradation")
    assert run.returncode == 0, (
        f"the gate now over-refuses a readable repo WITH a baseline.\n"
        f"stdout: {run.stdout.strip()[:600]!r}\nstderr: {run.stderr.strip()[:600]!r}"
    )


def test_pr_diff_json_publishes_the_git_error(indexed_dirty_repo: Path, tmp_path: Path) -> None:
    run = _run(
        indexed_dirty_repo,
        "--json",
        "pr-diff",
        "--fail-on-degradation",
        git_index=_broken_index(tmp_path),
    )
    payload = json.loads(run.stdout[run.stdout.find("{") :])
    summary = payload["summary"]
    assert summary.get("partial_success") is True, summary
    assert summary.get("git_error") == GIT_ERROR, summary
    assert summary["verdict"] != "no changes detected", summary


# ---------------------------------------------------------------------------
# The indistinguishability result, pinned directly
# ---------------------------------------------------------------------------


def test_broken_and_healthy_are_distinguishable_by_exit_code(indexed_dirty_repo: Path, tmp_path: Path) -> None:
    """Before the fix the two runs were identical in text, JSON and exit code.

    A caller branching on ``$?`` could not tell "clean" from "unmeasured",
    which is what made the pass dangerous rather than merely generous.
    """
    healthy = _run(indexed_dirty_repo, "adversarial", "--fail-on-critical")
    broken = _run(
        indexed_dirty_repo,
        "adversarial",
        "--fail-on-critical",
        git_index=_broken_index(tmp_path),
    )
    assert healthy.returncode != broken.returncode, (
        "a readable and an unreadable diff produced the SAME exit code "
        f"({healthy.returncode}); the gate cannot tell them apart."
    )
