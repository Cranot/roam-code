"""article-12-check must not report an INCOMPLETE HR scan as a clean pass.

The Annex III item (`_classify_high_risk_likelihood`) decides "is this
codebase high-risk?" with ``hits > 0`` over a scan that can silently come
up short in three ways — a file past the scan cap, an unparseable file,
an unreadable file — every one of which can only drag ``hits`` DOWN. The
verdict used to cross into ``items[].passed``, ``[OK] PASS`` in the
rendered DPO report and ``summary.high_risk_classification`` carrying only
the surviving sample count, with no sibling saying the sample was not the
total. So "no HR identifiers found in the 200 files we managed to read"
was published as "NOT high-risk", which is a different claim.

Two measured pre-fix flips this file pins shut:

  (a) UNPARSEABLE — a repo whose ONLY HR-referencing file has a syntax
      error went from ``REVIEW - 1 file(s) reference HR/employment
      workflows`` to ``NOT high-risk - ... across 0 files scanned`` with
      ``passed: True``, and the readiness score ROSE 1/6 -> 2/6.
  (b) CAP-AS-TOTAL — the cap is applied to a deterministic alphabetical
      sort, so files past it are never scanned in ANY run. A repo with
      HR code in its last-sorted file reported ``NOT high-risk ... across
      200 files scanned``, ``passed: True``.

Absence of evidence is only evidence of absence when the scan was
complete; otherwise the honest value is INCONCLUSIVE, and it fails
closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_article_12_check import (
    _HR_SCAN_FILE_CAP,
    _classify_high_risk_likelihood,
)

HR_SOURCE = "def score_employee_promotion(record):\n    return record\n"
BENIGN_SOURCE = "def add(a, b):\n    return a + b\n"
# Valid HR code followed by a genuine syntax error: the file both matches
# the classifier AND cannot be parsed, which is exactly the case where
# dropping it from the denominator hides the only hit.
UNPARSEABLE_HR_SOURCE = HR_SOURCE + "\n\ndef broken(:\n    pass\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scan root under pytest's tmp_path.

    `_is_test_path` is path-CONVENTION based (an exact tests/ component or
    a test_*.py basename), so a pytest tmp dir named after this test
    function is NOT treated as a test directory and the files below really
    do get scanned. `test_positive_control_*` asserts that end-to-end, so
    this fixture cannot rot into a silently-empty scan.
    """
    return tmp_path


# ---------------------------------------------------------------------------
# POSITIVE CONTROLS — the healthy path still decides, both ways.
# These are what stop a "fail closed on everything" fix from passing.
# ---------------------------------------------------------------------------


def test_positive_control_clean_repo_still_passes(repo: Path):
    """A fully-scanned repo with no HR identifiers is still a clean PASS."""
    (repo / "clean.py").write_text(BENIGN_SOURCE)
    res = _classify_high_risk_likelihood(repo)

    assert res["passed"] is True, res["evidence"]
    assert "NOT high-risk" in res["evidence"]
    assert "INCONCLUSIVE" not in res["evidence"]
    assert res["scan_coverage_complete"] is True
    assert res["scan_incomplete_reason"] is None
    assert res["files_scanned"] == 1
    assert res["files_total"] == 1


def test_positive_control_hr_repo_still_flags_review(repo: Path):
    """A fully-scanned repo WITH HR identifiers is still REVIEW, not INCONCLUSIVE."""
    (repo / "hr.py").write_text(HR_SOURCE)
    res = _classify_high_risk_likelihood(repo)

    assert res["passed"] is False, res["evidence"]
    assert "REVIEW" in res["evidence"]
    assert "1 file(s)" in res["evidence"]
    assert res["scan_coverage_complete"] is True


# ---------------------------------------------------------------------------
# (a) UNPARSEABLE — a dropped file must not shrink the denominator silently
# ---------------------------------------------------------------------------


def test_unparseable_only_hr_file_does_not_become_a_clean_pass(repo: Path):
    """PRE-FIX: passed=True + 'NOT high-risk ... across 0 files scanned'."""
    (repo / "hr.py").write_text(UNPARSEABLE_HR_SOURCE)
    res = _classify_high_risk_likelihood(repo)

    assert res["passed"] is False, "an unparseable file must not resolve to a clean pass"
    assert "NOT high-risk" not in res["evidence"]
    assert "INCONCLUSIVE" in res["evidence"]
    assert res["files_unparseable"] == 1
    assert res["scan_coverage_complete"] is False
    assert "could not be parsed" in res["scan_incomplete_reason"]


def test_unparseable_file_alongside_clean_code_is_still_disclosed(repo: Path):
    """The gap is reported even when the unparseable file itself is innocent.

    The point is that nobody KNOWS what was in it — an unclassifiable file
    is UNKNOWN, not CLEAN.
    """
    (repo / "clean.py").write_text(BENIGN_SOURCE)
    (repo / "zbroken.py").write_text("def broken(:\n    pass\n")
    res = _classify_high_risk_likelihood(repo)

    assert res["passed"] is False
    assert res["files_scanned"] == 1
    assert res["files_total"] == 2
    assert res["files_unparseable"] == 1


def test_unparseable_file_does_not_suppress_a_hit_found_elsewhere(repo: Path):
    """A real hit still reads REVIEW — coverage gaps can only add, never retract."""
    (repo / "hr.py").write_text(HR_SOURCE)
    (repo / "zbroken.py").write_text("def broken(:\n    pass\n")
    res = _classify_high_risk_likelihood(repo)

    assert res["passed"] is False
    assert "REVIEW" in res["evidence"], "a positive match must not be downgraded to INCONCLUSIVE"


# ---------------------------------------------------------------------------
# (b) CAP-AS-TOTAL — a cap is not a total
# ---------------------------------------------------------------------------


def test_hr_code_past_the_scan_cap_does_not_read_as_not_high_risk(repo: Path):
    """PRE-FIX: 'NOT high-risk - ... across 200 files scanned', passed=True.

    The cap is applied to a deterministic alphabetical sort, so a file
    sorting after it is never scanned in ANY run — not sampling, a
    permanent blind spot.
    """
    for i in range(_HR_SCAN_FILE_CAP + 50):
        (repo / f"a{i:04d}.py").write_text(BENIGN_SOURCE)
    (repo / "zz_hr.py").write_text(HR_SOURCE)  # sorts last -> never scanned

    res = _classify_high_risk_likelihood(repo)

    assert res["passed"] is False, "a capped scan cannot support a NOT-high-risk verdict"
    assert "NOT high-risk" not in res["evidence"]
    assert "INCONCLUSIVE" in res["evidence"]
    assert res["files_unscanned_over_cap"] == 51
    assert res["files_total"] == _HR_SCAN_FILE_CAP + 51
    assert res["files_scanned"] == _HR_SCAN_FILE_CAP
    assert "scan cap" in res["scan_incomplete_reason"]


def test_cap_is_not_reported_as_the_total(repo: Path):
    """files_total counts CANDIDATES, files_scanned counts what was read."""
    for i in range(_HR_SCAN_FILE_CAP + 10):
        (repo / f"a{i:04d}.py").write_text(BENIGN_SOURCE)
    res = _classify_high_risk_likelihood(repo)

    assert res["files_total"] > res["files_scanned"]
    assert res["files_scanned"] == _HR_SCAN_FILE_CAP
    assert res["files_total"] == _HR_SCAN_FILE_CAP + 10


def test_repo_exactly_at_the_cap_is_complete_coverage(repo: Path):
    """Boundary: cap files means nothing was dropped, so a PASS is honest."""
    for i in range(_HR_SCAN_FILE_CAP):
        (repo / f"a{i:04d}.py").write_text(BENIGN_SOURCE)
    res = _classify_high_risk_likelihood(repo)

    assert res["files_unscanned_over_cap"] == 0
    assert res["scan_coverage_complete"] is True
    assert res["passed"] is True


# ---------------------------------------------------------------------------
# The disclosure must survive the boundary into the published envelope
# ---------------------------------------------------------------------------


def _run_json(repo: Path) -> dict:
    """Run `roam --json article-12-check` with *repo* as cwd.

    The first invocation in a fresh repo auto-indexes and prints a human
    progress preamble ahead of the envelope, so index first and then take
    the envelope from the first ``{`` — the assertions below are about the
    envelope's contents, not stdout framing.
    """
    import os

    prev = os.getcwd()
    os.chdir(repo)
    try:
        runner = CliRunner()
        runner.invoke(cli, ["index"], catch_exceptions=False)
        result = runner.invoke(cli, ["--json", "article-12-check"], catch_exceptions=False)
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    start = result.output.find("{")
    assert start != -1, f"no JSON envelope in output: {result.output!r}"
    return json.loads(result.output[start:])


def test_envelope_discloses_an_incomplete_scan(repo: Path):
    """summary must not publish a degraded classification with no disclosure sibling."""
    (repo / ".git").mkdir()  # make find_project_root stop here
    (repo / "hr.py").write_text(UNPARSEABLE_HR_SOURCE)

    env = _run_json(repo)
    summ = env["summary"]

    assert summ["partial_success"] is True, "a degraded scan must set the canonical partial_success flag"
    assert summ["high_risk_scan_coverage_complete"] is False
    assert summ["high_risk_scan_incomplete_reason"], "no disclosure sibling for the degraded value"
    assert "NOT high-risk" not in summ["high_risk_classification"]


def test_envelope_on_a_complete_scan_is_not_marked_partial(repo: Path):
    """NEGATIVE CONTROL at the envelope: a healthy repo must NOT be flagged degraded.

    A fix that just stamps partial_success=True unconditionally, or that
    fails every repo closed, dies here.
    """
    (repo / ".git").mkdir()
    (repo / "clean.py").write_text(BENIGN_SOURCE)

    env = _run_json(repo)
    summ = env["summary"]

    assert summ["partial_success"] is False
    assert summ["high_risk_scan_coverage_complete"] is True
    assert summ["high_risk_scan_incomplete_reason"] is None
    assert "NOT high-risk" in summ["high_risk_classification"]


def test_incomplete_scan_does_not_inflate_the_readiness_score(repo: Path):
    """The measured pre-fix flip: score ROSE 1/6 -> 2/6 when the HR file broke.

    Breaking a file must never buy a compliance point.
    """
    (repo / ".git").mkdir()
    (repo / "hr.py").write_text(HR_SOURCE)
    healthy = _run_json(repo)["summary"]

    (repo / "hr.py").write_text(UNPARSEABLE_HR_SOURCE)
    degraded = _run_json(repo)["summary"]

    assert degraded["passed"] <= healthy["passed"], (
        f"corrupting the only HR file raised the readiness score {healthy['verdict']} -> {degraded['verdict']}"
    )
    assert degraded["governance_compliance_score"] <= healthy["governance_compliance_score"]
