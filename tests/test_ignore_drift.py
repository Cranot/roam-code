"""`roam ignore-drift` — tracked-but-ignored detection, incl. the negation trap.

The load-bearing test in this file is the CONTRAST pair:

* ``test_correct_form_finds_exactly_one_violation`` — the ``--no-index`` form
  finds the one true positive on a fixture built to contain exactly one.
* ``test_naive_form_without_no_index_finds_nothing`` — the natural audit
  (``git ls-files | xargs git check-ignore``) finds ZERO on that same fixture.

Those two together are why ``--no-index`` may never be "simplified" out of
``cmd_ignore_drift``: without it the command reports CLEAN on exactly the
defect it exists to catch. Measured 2026-08-05 on a real estate, the naive
form found 0 in every repo while the ``--no-index`` form found 43 in one and
27 in another — both had been "audited clean" beforehand.

The fixture deliberately contains one of each case:

    build.log       committed BEFORE `*.log` existed   -> the TRUE POSITIVE
    keep.log        matched by the negation `!keep.log` -> must NOT be reported
    untracked.log   matches `*.log`, never added        -> must NOT be reported
    ordinary.py     tracked, matches nothing            -> must NOT be reported

Fixture-root hygiene: every assertion goes through ``scan_tracked_ignored``
with an EXPLICIT directory and the returned ``git_root`` is asserted to equal
that directory. ``find_project_root`` is deliberately not involved — it trusts
any ``.git`` it finds while walking up, and a stray empty ``.git`` has hijacked
whole test runs in this repo before. Asserting ``git_root`` is what makes a
hijack a red test instead of a false green.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_ignore_drift import scan_tracked_ignored


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


@pytest.fixture()
def drift_repo(tmp_path: Path) -> Path:
    """A git repo with exactly one tracked-but-ignored file, plus three decoys."""
    repo = tmp_path / "drift_repo"
    repo.mkdir()
    _git(["init", "-q", "."], repo)
    _git(["config", "user.email", "fixture@example.invalid"], repo)
    _git(["config", "user.name", "fixture"], repo)
    _git(["config", "commit.gpgsign", "false"], repo)

    # Phase 1 — no .gitignore exists yet, so these commit cleanly.
    (repo / "build.log").write_text("stale build output\n", encoding="utf-8")
    (repo / "keep.log").write_text("keep me\n", encoding="utf-8")
    (repo / "ordinary.py").write_text('print("ordinary")\n', encoding="utf-8")
    _git(["add", "build.log", "keep.log", "ordinary.py"], repo)
    _git(["commit", "-q", "-m", "phase 1: commit before any ignore rule exists"], repo)

    # Phase 2 — the rules arrive AFTER the commit. gitignore does not untrack,
    # so build.log stays in the index while every reader believes it is gone.
    (repo / ".gitignore").write_text("# line 1\n# line 2\n*.log\n!keep.log\n", encoding="utf-8")
    (repo / "untracked.log").write_text("never added\n", encoding="utf-8")
    _git(["add", ".gitignore"], repo)
    _git(["commit", "-q", "-m", "phase 2: add *.log plus a !keep.log negation"], repo)
    return repo


@pytest.fixture()
def clean_repo(tmp_path: Path) -> Path:
    """A git repo whose ignore rules and index agree."""
    repo = tmp_path / "clean_repo"
    repo.mkdir()
    _git(["init", "-q", "."], repo)
    _git(["config", "user.email", "fixture@example.invalid"], repo)
    _git(["config", "user.name", "fixture"], repo)
    _git(["config", "commit.gpgsign", "false"], repo)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "ordinary.py").write_text('print("ordinary")\n', encoding="utf-8")
    (repo / "untracked.log").write_text("correctly excluded\n", encoding="utf-8")
    _git(["add", ".gitignore", "ordinary.py"], repo)
    _git(["commit", "-q", "-m", "rules and index agree"], repo)
    return repo


# ---------------------------------------------------------------------------
# The contrast — this pair is the whole point of the file
# ---------------------------------------------------------------------------


def test_correct_form_finds_exactly_one_violation(drift_repo: Path) -> None:
    report = scan_tracked_ignored(drift_repo)

    assert report["status"] == "violation"
    # Fixture-root hygiene: prove we read the repo we built, not an ancestor
    # that a stray .git could have redirected us to.
    assert Path(report["git_root"]).resolve() == drift_repo.resolve()

    paths = [v["path"] for v in report["violations"]]
    assert paths == ["build.log"], f"expected exactly the pre-rule commit, got {paths}"


def test_naive_form_without_no_index_finds_nothing(drift_repo: Path) -> None:
    """`git ls-files | xargs git check-ignore` reports clean on a dirty repo.

    This is the false all-clear the command exists to replace. If this test
    ever starts finding the violation, `--no-index` has become unnecessary and
    the comment in cmd_ignore_drift should be revisited — until then, dropping
    the flag silently converts every VIOLATION into a CLEAN.
    """
    tracked = _git(["ls-files"], drift_repo).stdout.split()
    assert "build.log" in tracked, "fixture is broken: the true positive is not tracked"

    naive = subprocess.run(
        ["git", "check-ignore", *tracked],  # no --no-index: git skips indexed paths
        cwd=str(drift_repo),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    naive_hits = [line for line in naive.stdout.splitlines() if line.strip()]
    assert naive_hits == [], f"naive form unexpectedly found {naive_hits}"

    # ...while the shipped form finds it.
    assert [v["path"] for v in scan_tracked_ignored(drift_repo)["violations"]] == ["build.log"]


# ---------------------------------------------------------------------------
# The three decoys
# ---------------------------------------------------------------------------


def test_negation_rule_path_is_not_reported(drift_repo: Path) -> None:
    """`keep.log` matches `!keep.log`, so it is NOT ignored and must not appear.

    `git check-ignore -v` DOES print it (with the negation as the matching
    rule), which is how a real audit inflated a count from 27 to 29.
    """
    # BYTES, not text=True. Python's text mode translates "\n" to os.linesep
    # on write, so on Windows git would receive "build.log\r" and match
    # nothing — the probe would pass for the wrong reason and stop guarding
    # the trap it exists to guard.
    verbose = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-v"],
        cwd=str(drift_repo),
        input=b"build.log\nkeep.log\n",
        capture_output=True,
        check=False,
        timeout=60,
    )
    verbose_out = verbose.stdout.decode("utf-8", errors="replace")
    assert "keep.log" in verbose_out, "fixture no longer exercises the negation trap"
    assert "!keep.log" in verbose_out

    report = scan_tracked_ignored(drift_repo)
    assert "keep.log" not in [v["path"] for v in report["violations"]]
    assert report["negation_excluded"] >= 1
    # candidates counts everything -v matched; violations is what survived.
    assert report["candidates"] > len(report["violations"])


def test_untracked_ignored_file_is_not_reported(drift_repo: Path) -> None:
    assert (drift_repo / "untracked.log").is_file()
    assert "untracked.log" not in [v["path"] for v in scan_tracked_ignored(drift_repo)["violations"]]


def test_ordinary_tracked_file_is_not_reported(drift_repo: Path) -> None:
    assert "ordinary.py" not in [v["path"] for v in scan_tracked_ignored(drift_repo)["violations"]]


# ---------------------------------------------------------------------------
# Rule provenance + remediation
# ---------------------------------------------------------------------------


def test_violation_names_the_rule_file_and_line(drift_repo: Path) -> None:
    violation = scan_tracked_ignored(drift_repo)["violations"][0]
    assert violation["rule_source"] == ".gitignore"
    # `*.log` is the third line of the fixture's .gitignore.
    assert violation["rule_line"] == "3"
    assert violation["rule_pattern"] == "*.log"
    assert violation["rule"] == ".gitignore:3"


def test_remediation_uses_rm_cached_and_never_bare_rm(drift_repo: Path) -> None:
    violation = scan_tracked_ignored(drift_repo)["violations"][0]
    assert violation["remediation"] == "git rm --cached -- build.log"
    assert "--cached" in violation["remediation"]


def test_scan_never_mutates_the_index(drift_repo: Path) -> None:
    before = _git(["ls-files"], drift_repo).stdout
    scan_tracked_ignored(drift_repo)
    assert _git(["ls-files"], drift_repo).stdout == before


# ---------------------------------------------------------------------------
# Three-valued reporting
# ---------------------------------------------------------------------------


def test_clean_repo_reports_clean(clean_repo: Path) -> None:
    report = scan_tracked_ignored(clean_repo)
    assert report["status"] == "clean"
    assert report["violations"] == []
    assert Path(report["git_root"]).resolve() == clean_repo.resolve()


def test_non_repo_is_unanalyzable_not_clean(tmp_path: Path) -> None:
    """The bug this command exists to catch is 'absent measurement reads clean'."""
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    report = scan_tracked_ignored(plain)
    assert report["status"] == "unanalyzable"
    assert report["status"] != "clean"
    assert report["reason"]
    assert report["violations"] == []


def test_missing_git_binary_is_unanalyzable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _no_git)
    report = scan_tracked_ignored(tmp_path)
    assert report["status"] == "unanalyzable"
    assert "git" in report["reason"]


# ---------------------------------------------------------------------------
# CLI surface: envelope, text branch, exit codes
# ---------------------------------------------------------------------------


def test_cli_json_envelope_on_violation(drift_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(drift_repo)
    result = CliRunner().invoke(cli, ["--json", "ignore-drift"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["status"] == "violation"
    assert summary["violations"] == 1
    assert summary["negation_excluded"] >= 1
    assert summary["scan_incomplete"] is False
    assert "--cached" in summary["remediation"]
    assert payload["findings"][0]["path"] == "build.log"


def test_cli_text_branch_discloses_negation_filter(drift_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1331: a caveat that reaches only --json is a caveat no human reads."""
    monkeypatch.chdir(drift_repo)
    result = CliRunner().invoke(cli, ["ignore-drift"])
    assert result.exit_code == 0, result.output
    assert "VIOLATION" in result.output
    assert "build.log" in result.output
    assert ".gitignore:3" in result.output
    assert "negation" in result.output
    assert "git rm --cached" in result.output
    assert "STAYS on disk" in result.output


def test_cli_fail_on_found_exits_gate_failure(drift_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from roam.exit_codes import EXIT_GATE_FAILURE

    monkeypatch.chdir(drift_repo)
    result = CliRunner().invoke(cli, ["ignore-drift", "--fail-on-found"])
    assert result.exit_code == EXIT_GATE_FAILURE


def test_cli_fail_on_found_is_clean_on_a_clean_repo(clean_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(clean_repo)
    result = CliRunner().invoke(cli, ["ignore-drift", "--fail-on-found"])
    assert result.exit_code == 0, result.output
    assert "CLEAN" in result.output


def test_cli_fail_on_found_fails_when_unanalyzable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate that cannot measure must not report success."""
    from roam.exit_codes import EXIT_GATE_FAILURE

    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    monkeypatch.chdir(plain)
    result = CliRunner().invoke(cli, ["--json", "ignore-drift", "--fail-on-found"])
    assert result.exit_code == EXIT_GATE_FAILURE
    # The envelope is pretty-printed and the gate appends its "Error: ..." line
    # after it, so decode the leading JSON value rather than a single line.
    payload, _ = json.JSONDecoder().raw_decode(result.output)
    assert payload["summary"]["status"] == "unanalyzable"
    assert payload["summary"]["partial_success"] is True


def test_command_is_registered_and_categorised() -> None:
    from roam.cli import _CATEGORIES, _COMMANDS
    from roam.modes.policy import _MODE_EXTRAS

    assert _COMMANDS["ignore-drift"] == ("roam.commands.cmd_ignore_drift", "ignore_drift")
    assert any("ignore-drift" in names for names in _CATEGORIES.values())
    assert "ignore-drift" in _MODE_EXTRAS["read_only"]
