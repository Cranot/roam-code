"""`no_changes` must mean the diff was READ and was empty.

``cmd_diff`` consumed the bare ``get_changed_files``, so a ``git diff`` that
FAILED produced the same empty list as a clean tree and the same
``state: "no_changes"`` envelope. ``pr-analyze`` reads that state to decide
whether a PR is empty, so a substrate failure arrived at the gate as the
affirmative fact "there is nothing to review".

Measured against the tree that shipped, in a repo with a modified tracked
file and ``GIT_INDEX_FILE`` pointed at a garbage file::

    $ roam --json pr-analyze --gate
      {"state": "no_changes", "reasons": ["no changes to analyze"],
       "rule_violations": 0, "high_severity_critique": 0,
       "blast_radius": 0, "verdict": "NOCHANGES (risk_level low)"}   rc 0
      (the text channel is identical, also rc 0)

THIS ALSO REFUTES A WRITTEN EXEMPTION
-------------------------------------
``tests/test_gate_channel_exit_parity.py`` recorded, as a measured decision
dated 2026-08-08: "``pr-analyze --gate`` reports ``state: 'no_changes'``
with no ``scan_incomplete``, and it is deliberately NOT changed here,
because 'there is no diff to gate' is a real answer rather than an absent
measurement." The run above shows ``no_changes`` was ALSO what pr-analyze
published when the diff could not be read, so the state did not distinguish
the two and the exemption rested on a property the command did not have.
That paragraph is corrected in the same change as this test.

WHAT IS NOT COVERED HERE
------------------------
``roam diff`` itself is a report-only command and still exits 0 on the
degraded path. Only its STATE changed; giving it a gate flag is a separate
decision. The other bare-helper consumers (cmd_attest, cmd_test_gaps,
cmd_affected_tests, cmd_affected) keep their W805 pins.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _run(cwd: Path, *args: str, git_index: Path | None = None) -> subprocess.CompletedProcess:
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
        timeout=600,
        env=env,
    )


def _broken_index(tmp_path: Path) -> Path:
    garbage = tmp_path / "garbage.idx"
    garbage.write_bytes(b"garbage")
    return garbage


@pytest.fixture()
def dirty_repo(tmp_path: Path) -> Path:
    """Indexed with a healthy git; only the gate run sees the broken one."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n", encoding="utf-8")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / "src" / "a.py").write_text(
        "def alpha(x):\n    return x + 2\n\n\ndef beta(x):\n    return alpha(x)\n",
        encoding="utf-8",
    )
    built = _run(root, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    return root


def _summary(out: str) -> dict:
    payload, _end = json.JSONDecoder().raw_decode(out[out.find("{") :])
    return payload["summary"]


# ---------------------------------------------------------------------------
# cmd_diff: the state vocabulary
# ---------------------------------------------------------------------------


def test_diff_distinguishes_an_unread_diff_from_an_empty_one(dirty_repo: Path, tmp_path: Path) -> None:
    broken = _summary(_run(dirty_repo, "--json", "diff", git_index=_broken_index(tmp_path)).stdout)

    assert broken["state"] == "diff_unavailable", broken
    assert broken["git_error"], broken
    assert broken["partial_success"] is True, broken


def test_diff_still_says_no_changes_on_a_genuinely_clean_tree(dirty_repo: Path) -> None:
    """The control that keeps the vocabulary meaningful.

    If both branches reported ``diff_unavailable`` the state would be as
    useless as it was when both reported ``no_changes``.
    """
    _git(dirty_repo, "checkout", "--", "src/a.py")
    clean = _summary(_run(dirty_repo, "--json", "diff").stdout)

    assert clean["state"] == "no_changes", clean
    assert clean["partial_success"] is False, clean
    assert "git_error" not in clean, clean


# ---------------------------------------------------------------------------
# pr-analyze --gate: the authorization
# ---------------------------------------------------------------------------


def test_gate_refuses_a_diff_it_could_not_read(dirty_repo: Path, tmp_path: Path) -> None:
    run = _run(dirty_repo, "pr-analyze", "--gate", git_index=_broken_index(tmp_path))

    assert run.returncode != 0, (
        f"`roam pr-analyze --gate` authorized a PR whose diff it could not read.\nstdout: {run.stdout[:600]!r}"
    )
    assert "NOCHANGES" not in run.stdout, run.stdout[:600]


def test_gate_publishes_diff_failed_not_no_changes(dirty_repo: Path, tmp_path: Path) -> None:
    run = _run(dirty_repo, "--json", "pr-analyze", "--gate", git_index=_broken_index(tmp_path))
    summary = _summary(run.stdout)

    assert summary["state"] == "diff_failed", summary
    assert summary["verdict"] != "NOCHANGES", summary
    assert "no changes to analyze" not in summary.get("reasons", []), summary
    assert summary["partial_success"] is True, summary


def test_gate_still_passes_a_readable_dirty_tree(dirty_repo: Path) -> None:
    """The must-not-fire control: an analysable PR below the thresholds."""
    run = _run(dirty_repo, "pr-analyze", "--gate")
    assert run.returncode == 0, f"stdout: {run.stdout[:600]!r}\nstderr: {run.stderr[:600]!r}"


def test_gate_still_passes_a_genuinely_empty_pr(dirty_repo: Path) -> None:
    """The narrowness control, and the claim the old exemption was reaching for.

    "There is no diff to gate" IS a real answer. If this refused, the fix
    would have made the gate useless rather than honest.
    """
    _git(dirty_repo, "checkout", "--", "src/a.py")
    run = _run(dirty_repo, "pr-analyze", "--gate")

    assert run.returncode == 0, f"stdout: {run.stdout[:600]!r}"
    assert "NOCHANGES" in run.stdout, run.stdout[:600]


def test_broken_and_clean_are_distinguishable_by_exit_code(dirty_repo: Path, tmp_path: Path) -> None:
    """The indistinguishability result, pinned directly.

    Before the fix a caller branching on ``$?`` could not tell "this PR is
    empty" from "I could not read the diff" -- both were 0 with the same
    verdict string.
    """
    _git(dirty_repo, "checkout", "--", "src/a.py")
    clean = _run(dirty_repo, "pr-analyze", "--gate")
    broken = _run(dirty_repo, "pr-analyze", "--gate", git_index=_broken_index(tmp_path))

    assert clean.returncode != broken.returncode, (
        f"an empty PR and an unreadable diff produced the SAME exit code ({clean.returncode})"
    )
