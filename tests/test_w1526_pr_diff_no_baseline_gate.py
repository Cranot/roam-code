"""W1526 -- `pr-diff --fail-on-degradation` could not fire without a baseline, and exited 0.

The defect. `find_before_snapshot` returns None when no snapshot exists, so
`deltas_available` is False, `health_delta` is None, and
`health_degraded = health_delta is not None and health_delta < 0` is False. All
three gate sites read `fail_on_degradation and health_degraded`, so none of them
could fire. Measured on a repo onboarded with the documented `roam init` alone
-- which leaves 0 snapshot rows -- with a feature branch adding a real file::

    roam pr-diff --range master..HEAD --fail-on-degradation --json   -> rc 0
      verdict                  "minimal structural impact (footprint: 0.0% of graph)"
      partial_success          false
      metric_deltas_available  false
      agent_contract.facts     [..., "0 new issues"]

The gate was requested, the comparison was impossible, and the command
published "minimal structural impact" and "0 new issues" and authorized.
`metric_deltas_available: false` was the only signal, and it reached neither
the verdict, the facts, nor the exit code.

THE OPPOSITE PRECEDENT WAS 90 LINES ABOVE, IN THE SAME FILE. An unreadable
diff::

    roam pr-diff --range no-such-ref..HEAD --fail-on-degradation --json -> rc 5
      verdict         "diff unavailable: git_error - cannot gate"
      partial_success true

So one UNANALYZABLE path refused loudly while its sibling passed in silence.

The shape of the fix. The no-baseline branch mirrors the git_error branch:
"cannot gate" verdict naming the remedy, `partial_success: true`, and
EXIT_GATE_FAILURE. The decision is computed ONCE through `gate_should_fail`
before the first `if json_mode:`; this file had THREE hand-written copies of
`fail_on_degradation and health_degraded` (json, markdown, text), which is
exactly the duplication that helper exists to eliminate.

Also recorded: `summary.baseline_commit`. `find_before_snapshot` falls back to
the LATEST snapshot when `base_ref` has none of its own, so
`metric_deltas_available: true` never meant "compared against the base". The
claim now names its own comparand.

WHAT MUST NOT FIRE. An empty diff with the gate requested stays 0 -- a PR that
changes nothing genuinely has no degradation, and that branch returns before
the gate is reached. Without the flag, nothing changes at all.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.conftest import index_in_process, invoke_cli


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def snapshotless_project(tmp_path, monkeypatch):
    """A repo onboarded exactly as documented, with a feature branch.

    Deliberately NOT the shared `pr_diff_project` fixture: that one runs
    `trends --save`, so it has a baseline and could never catch this. The
    whole finding is about the state `roam init` leaves behind.
    """
    proj = tmp_path / "w1526_proj"
    (proj / "src").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n")
    _git(proj, "init", "-q", "-b", "master", ".")
    _git(proj, "config", "user.email", "fixture@example.invalid")
    _git(proj, "config", "user.name", "fixture")
    _git(proj, "add", ".gitignore", "src")
    _git(proj, "commit", "-q", "-m", "base")

    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out
    # Reproduce the state the DOCUMENTED onboarding leaves. Measured live:
    # `roam init` -> 0 snapshot rows, `roam index` -> 1. The harness only has
    # `index`, so the snapshot it records is removed to get back to the
    # post-`init` state, which is the state this finding is about.
    _clear_snapshots(proj)

    _git(proj, "checkout", "-q", "-b", "feature")
    (proj / "src" / "b.py").write_text("from src.a import alpha\n\n\ndef beta(y):\n    return alpha(y) * 2\n")
    _git(proj, "add", "src/b.py")
    _git(proj, "commit", "-q", "-m", "add b")
    return proj


def _clear_snapshots(proj):
    import sqlite3

    conn = sqlite3.connect(str(proj / ".roam" / "index.db"))
    try:
        with conn:
            conn.execute("DELETE FROM snapshots")
    finally:
        conn.close()


def _snapshot_count(proj):
    import sqlite3

    conn = sqlite3.connect(str(proj / ".roam" / "index.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()


def test_fixture_reproduces_the_no_baseline_state(snapshotless_project):
    """The premise of the whole finding, pinned so it cannot drift silently.

    If a future change makes something record a snapshot here, every gate test
    below would pass for the wrong reason -- so assert the premise directly.
    """
    assert _snapshot_count(snapshotless_project) == 0


# ---------------------------------------------------------------------------
# MUST FIRE -- a gate with nothing to compare against
# ---------------------------------------------------------------------------


def test_gate_refuses_without_a_baseline_json(cli_runner, snapshotless_project):
    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "master..HEAD", "--fail-on-degradation", "--json"],
        cwd=snapshotless_project,
    )
    assert result.exit_code == 5, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is True
    assert summary["metric_deltas_available"] is False
    assert "cannot gate" in summary["verdict"]
    # The verdict must no longer assert an impact it could not measure.
    assert "minimal structural impact" not in summary["verdict"]


def test_refusal_names_the_remedy(cli_runner, snapshotless_project):
    """A newly-red first PR must say how to make it green honestly."""
    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "master..HEAD", "--fail-on-degradation", "--json"],
        cwd=snapshotless_project,
    )
    verdict = json.loads(result.output)["summary"]["verdict"]
    assert "roam index" in verdict or "roam trends --save" in verdict


@pytest.mark.parametrize("extra", [[], ["--format", "markdown"]])
def test_gate_refuses_on_every_channel(cli_runner, snapshotless_project, extra):
    """Channel parity: text and markdown must answer as --json does.

    This is the point of routing through `gate_should_fail`: the three copies
    of the condition could drift, and in `roam ignore-drift` one of them did.
    """
    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "master..HEAD", "--fail-on-degradation", *extra],
        cwd=snapshotless_project,
    )
    assert result.exit_code == 5, result.output


def test_text_channel_explains_the_refusal(cli_runner, snapshotless_project):
    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "master..HEAD", "--fail-on-degradation"],
        cwd=snapshotless_project,
    )
    assert "Could not gate" in result.output
    assert "roam trends --save" in result.output


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- the outage boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [["--json"], [], ["--format", "markdown"]])
def test_without_the_gate_flag_nothing_changes(cli_runner, snapshotless_project, extra):
    """No gate requested means no refusal, on any channel."""
    result = invoke_cli(cli_runner, ["pr-diff", "--range", "master..HEAD", *extra], cwd=snapshotless_project)
    assert result.exit_code == 0, result.output


def test_report_mode_does_not_claim_incompleteness(cli_runner, snapshotless_project):
    """A report with no baseline is a complete answer to what was asked."""
    result = invoke_cli(cli_runner, ["pr-diff", "--range", "master..HEAD", "--json"], cwd=snapshotless_project)
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is False
    assert "minimal structural impact" in summary["verdict"]


def test_empty_diff_with_the_gate_still_exits_zero(cli_runner, snapshotless_project):
    """A PR that changes nothing genuinely has no degradation to find.

    The `if not changed:` branch returns before the gate. Reaching the refusal
    here would fail every no-op CI run.
    """
    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "HEAD..HEAD", "--fail-on-degradation", "--json"],
        cwd=snapshotless_project,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["verdict"] == "no changes detected"


def test_healthy_run_with_a_baseline_still_authorizes(cli_runner, snapshotless_project):
    """THE key case: once a baseline exists and health held, the gate passes."""
    saved = invoke_cli(cli_runner, ["trends", "--save"], cwd=snapshotless_project)
    assert saved.exit_code == 0, saved.output
    assert _snapshot_count(snapshotless_project) == 1

    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "master..HEAD", "--fail-on-degradation", "--json"],
        cwd=snapshotless_project,
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["metric_deltas_available"] is True
    assert summary["partial_success"] is False


def test_baseline_commit_names_the_comparand(cli_runner, snapshotless_project):
    """`metric_deltas_available: true` never said WHICH snapshot it compared."""
    invoke_cli(cli_runner, ["trends", "--save"], cwd=snapshotless_project)
    result = invoke_cli(cli_runner, ["pr-diff", "--range", "master..HEAD", "--json"], cwd=snapshotless_project)
    summary = json.loads(result.output)["summary"]
    assert summary["metric_deltas_available"] is True
    assert summary["baseline_commit"]


def test_unreadable_diff_still_refuses(cli_runner, snapshotless_project):
    """The pre-existing sibling refusal must not regress."""
    result = invoke_cli(
        cli_runner,
        ["pr-diff", "--range", "no-such-ref..HEAD", "--fail-on-degradation", "--json"],
        cwd=snapshotless_project,
    )
    assert result.exit_code == 5, result.output
    assert "cannot gate" in json.loads(result.output)["summary"]["verdict"]
