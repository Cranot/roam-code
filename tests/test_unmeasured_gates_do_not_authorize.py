"""Three more gates that answered "clean" about something they never read.

These are the instances the first pass of this work missed. They are grouped
here rather than split across three per-command files because they are ONE
property under three names, and the property is the point::

    a gate flag may return 0 only when a measurement happened and was clean

Each test below was demonstrated RED against the tree that shipped the first
pass, and each names the exact input that produced the false authorization.

WHAT IS NOT COVERED HERE
------------------------
The 17 gate flags that still exit 0 in an unmeasurable workspace pass Law 2 of
``test_gate_channel_exit_parity`` VACUOUSLY -- they never publish
``scan_incomplete``, so nothing holds them to anything. Deciding which of them
are blind and which are genuinely answering is a separate audit, recorded in
that file's docstring and not attempted here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# guard-pr: an unreadable repository was indistinguishable from a clean one
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _committed_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n", encoding="utf-8")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _run_guard_pr(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Subprocess, because the thing under test is a process exit status.

    A CI gate reads ``$?``. Running the callback in-process would test a
    different object than the one the shipped templates invoke.
    """
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1"),
    )


def test_guard_pr_ci_refuses_a_repository_it_could_not_read(tmp_path: Path) -> None:
    """The merge gate may not print a green verdict over a repo it cannot open.

    Measured against 14.0.0 in a worktree with a REAL uncommitted edit on disk
    and ``.git/HEAD`` overwritten with a non-ref, so ``git status`` answers
    ``fatal: not a git repository`` and rc 128::

        $ roam ignore-drift --fail-on-found   -> 5
        $ roam secrets --fail-on-found        -> 5
        $ roam guard-pr --ci                  -> 0   ## OK Roam Guard verdict: `pass`
                                                     > 0 of 0 required checks ran

    ``--ci`` implies ``--strict``, so that run WAS gating. The change set came
    back empty because git refused, the verification contract is derived from
    the change set, and an empty ``required`` list makes "all required passed"
    vacuously true.
    """
    repo = _committed_repo(tmp_path / "broken")
    (repo / "src" / "a.py").write_text("def alpha(x):\n    return x + 2\n", encoding="utf-8")
    (repo / ".git" / "HEAD").write_text("garbage-not-a-ref\n", encoding="utf-8")

    run = _run_guard_pr(repo, "guard-pr", "--ci")

    assert run.returncode != 0, (
        f"`roam guard-pr --ci` authorized a repository it could not read.\nstdout: {run.stdout.strip()[:600]!r}"
    )
    assert "blocked" in run.stdout, run.stdout[:600]
    assert "change_set_unanalyzable" in run.stdout, run.stdout[:600]


def test_guard_pr_ci_still_passes_a_readable_repository(tmp_path: Path) -> None:
    """The control, and the reason this fix is narrow.

    "Nothing was required of this change" is an ANSWER. Only "I could not find
    out what changed" is the absent measurement. If both refused, the fix would
    have made the gate useless rather than honest -- so the healthy copy of the
    same fixture, with the same uncommitted edit, must still exit 0.
    """
    repo = _committed_repo(tmp_path / "healthy")
    (repo / "src" / "a.py").write_text("def alpha(x):\n    return x + 2\n", encoding="utf-8")

    run = _run_guard_pr(repo, "guard-pr", "--ci")

    assert run.returncode == 0, (
        "`roam guard-pr --ci` refused a readable repository -- the gate now over-refuses.\n"
        f"stdout: {run.stdout.strip()[:600]!r}\nstderr: {run.stderr.strip()[:600]!r}"
    )
    assert "change_set_unanalyzable" not in run.stdout


def test_guard_pr_broken_and_healthy_repos_are_distinguishable(tmp_path: Path) -> None:
    """The refutation was an INDISTINGUISHABILITY result, so pin that directly.

    Before the fix the two runs differed only in a provenance footer -- head
    ``03174137dd41`` versus head ``--`` -- while the verdict line, the check
    counts and the exit code were identical. A caller branching on the exit
    code could not tell them apart, which is what made the pass dangerous
    rather than merely generous.
    """
    healthy = _committed_repo(tmp_path / "h")
    (healthy / "src" / "a.py").write_text("def alpha(x):\n    return x + 2\n", encoding="utf-8")
    broken = _committed_repo(tmp_path / "b")
    (broken / "src" / "a.py").write_text("def alpha(x):\n    return x + 2\n", encoding="utf-8")
    (broken / ".git" / "HEAD").write_text("garbage-not-a-ref\n", encoding="utf-8")

    h = _run_guard_pr(healthy, "guard-pr", "--ci")
    b = _run_guard_pr(broken, "guard-pr", "--ci")

    assert h.returncode != b.returncode, (
        "a readable and an unreadable repository produced the SAME exit code from "
        f"`roam guard-pr --ci` ({h.returncode}); the gate cannot tell them apart."
    )


def test_guard_pr_publishes_scan_incomplete_when_the_change_set_is_unknown(tmp_path: Path) -> None:
    """The machine-readable half. A gate that refuses must SAY why, in the envelope.

    ``required_count: 0`` reads identically in both cases, and the pre-fix
    envelope went further than omitting the signal: it asserted
    ``partial_success: false``. Publishing ``scan_incomplete`` is also what
    brings this command under Law 2 of ``test_gate_channel_exit_parity``,
    which it previously passed vacuously.
    """
    repo = _committed_repo(tmp_path / "broken")
    (repo / ".git" / "HEAD").write_text("garbage-not-a-ref\n", encoding="utf-8")

    run = _run_guard_pr(repo, "--json", "guard-pr", "--ci")
    payload = json.loads(run.stdout[run.stdout.find("{") :])
    summary = payload["summary"]

    assert summary["scan_incomplete"] is True, summary
    assert summary["verdict"] == "blocked", summary
    assert run.returncode == 5


# ---------------------------------------------------------------------------
# rules-validate: two gate flags, opposite answers, same unmeasurable state
# ---------------------------------------------------------------------------


def _rules_dir(tmp_path: Path, body: str | None) -> Path:
    root = tmp_path / "proj"
    (root / ".roam").mkdir(parents=True, exist_ok=True)
    if body is not None:
        (root / ".roam" / "rules.yml").write_text(body, encoding="utf-8")
    return root


def _invoke_in(root: Path, args: list[str]) -> int:
    old = os.getcwd()
    try:
        os.chdir(root)
        return CliRunner().invoke(cli, args, catch_exceptions=False).exit_code
    finally:
        os.chdir(old)


def test_rules_validate_strict_gates_on_an_unreadable_rules_file(tmp_path: Path) -> None:
    """``--strict`` was inert on its own, and the two gates disagreed.

    Measured against 14.0.0 with no ``.roam/rules.yml`` on disk -- the same
    directory, the same message, the same absent measurement::

        $ roam rules-validate --gate     VERDICT: load failed: file not found  -> 5
        $ roam rules-validate --strict   VERDICT: load failed: file not found  -> 0

    ``--strict`` consumed a ``fail`` term that only ``if gate and fail`` ever
    read, so the flag whose help promised "exit 5 on any warning" could not
    change the answer of any run that did not also pass ``--gate``.
    """
    root = _rules_dir(tmp_path, None)
    assert _invoke_in(root, ["rules-validate", "--gate"]) == 5
    assert _invoke_in(root, ["rules-validate", "--strict"]) == 5
    assert _invoke_in(root, ["--json", "rules-validate", "--strict"]) == 5
    # Reporting mode is untouched: no gate flag, no refusal.
    assert _invoke_in(root, ["rules-validate"]) == 0


def test_rules_validate_strict_gates_on_warnings_as_its_help_promises(tmp_path: Path) -> None:
    """A valid pack that only WARNS: the case ``--strict`` exists for."""
    root = _rules_dir(
        tmp_path,
        # No source_glob and no description -> two warnings, zero errors.
        "rules:\n  - id: r1\n    pattern: import_from\n    forbidden_target_glob: 'src/legacy/*'\n    severity: BLOCK\n",
    )
    assert _invoke_in(root, ["rules-validate", "--gate"]) == 0
    assert _invoke_in(root, ["rules-validate", "--strict"]) == 5


def test_rules_validate_publishes_scan_incomplete_when_nothing_was_linted(tmp_path: Path) -> None:
    """Nothing was linted, so nothing is proven valid -- said in the envelope."""
    root = _rules_dir(tmp_path, None)
    old = os.getcwd()
    try:
        os.chdir(root)
        result = CliRunner().invoke(cli, ["--json", "rules-validate"], catch_exceptions=False)
    finally:
        os.chdir(old)
    payload = json.loads(result.output[result.output.find("{") :])
    assert payload["summary"]["scan_incomplete"] is True, payload["summary"]


# ---------------------------------------------------------------------------
# taint: zero rules is zero findings by construction
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed_project(tmp_path: Path):
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "vuln.py").write_text(
        "import os\nfrom flask import request\n\n\n"
        "def handle():\n    q = request.args.get('q')\n    os.system('echo ' + q)\n",
        encoding="utf-8",
    )
    old = os.getcwd()
    try:
        os.chdir(proj)
        assert CliRunner().invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old)


def test_taint_ci_refuses_when_a_rule_filter_matched_no_rules(indexed_project: Path) -> None:
    """A one-character typo turned the security gate into a no-op that passed.

    ``--rules-pack`` is Click-validated; ``--rule`` is a free substring filter
    and is not. Measured against 14.0.0 on this repository::

        $ roam taint --ci --rule python-sqli    1 finding(s) across 1 rule(s)  -> 5
        $ roam taint --ci --rule python-sqlii   VERDICT: No rules in ...       -> 0

    Zero findings out of zero rules is not evidence of zero taint. The
    empty-corpus branch of this same command was closed in the first pass; the
    zero-rules branch returns before ever reaching it.
    """
    runner = CliRunner()
    typo = runner.invoke(cli, ["taint", "--ci", "--rule", "python-sqlii"], catch_exceptions=False)
    assert typo.exit_code == 5, typo.output

    typo_json = runner.invoke(cli, ["--json", "taint", "--ci", "--rule", "python-sqlii"], catch_exceptions=False)
    assert typo_json.exit_code == 5
    payload = json.loads(typo_json.output[typo_json.output.find("{") :])
    assert payload["summary"]["scan_incomplete"] is True, payload["summary"]
    assert payload["summary"]["rules"] == 0

    # Reporting mode is untouched: without --ci this is still a report.
    assert runner.invoke(cli, ["taint", "--rule", "python-sqlii"], catch_exceptions=False).exit_code == 0
