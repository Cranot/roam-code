"""W1524 -- `trends --assert` / `--fail-on-anomaly` passed on a history that did not exist.

The defect. On a repo with no recorded snapshots -- which is the state
`roam init` leaves behind; measured: `roam init` -> 0 snapshot rows,
`roam index` -> 1 -- every gate flag on `roam trends` reported success::

    roam trends --assert "health_score>=99999" --json   -> rc 0
      summary.partial_success  false
      summary.snapshots        0
      assertions               ABSENT from the envelope entirely
    roam trends --fail-on-anomaly                       -> rc 0

The positive control proves the gate is otherwise fully functional: after a
single `roam trends --save`, the identical impossible assertion returns rc 1
with `assertions.failures == ["health_score=99 (expected >=99999)"]`. So the
gate flipped to a guaranteed PASS purely on the absence of history, and
`roam trends --assert "health_score>=80" || exit 1` in CI passed forever on a
repo that never recorded a snapshot.

A second, wider blind window sat behind the same shape. Trend analysis needs
>= 4 chronological snapshots; with 1 the verdict text was honest ("insufficient
history for trend analysis") while the machine-readable `partial_success`
still asserted the run was complete and `--fail-on-anomaly` exited 0.

Why "no history" is NOT a bootstrap exemption. `cmd_reachability_triage`
deliberately fails OPEN on a missing baseline, and that is correct THERE
because that gate is DIFFERENTIAL -- with no baseline, nothing can be "new".
`--assert health_score>=80` is ABSOLUTE: it is fully evaluable from a single
snapshot. "No history" is therefore an unrun check, not a bootstrap, and
importing the fail-open would have preserved the defect under a respectable
name.

The shape of the fix. Both windows route through `gate_should_fail` with
`scan_incomplete=True`, matching `cmd_taint.py`'s "no rules loaded" precedent,
and the exit stays `SystemExit(1)` -- the code the command's two existing
assertion exits already use. The refusal names its remedy, because a first-ever
CI run that goes red without saying `roam trends --save` reads as a broken tool
rather than a refusal.

THE MUST-NOT-FIRE SET IS THE POINT. A gate flag that was never passed must not
change colour, and `roam trends` with no flags on a fresh repo must keep
reporting an empty history as the complete answer it is.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli


def _seed_flat_snapshots(proj, n=6):
    """Insert `n` identical snapshots so analysis RUNS and finds no anomaly.

    Flat metrics are the only way to reach the "analysis ran, verdict clean"
    arm deterministically: a naturally grown history moves, and a moving
    history legitimately produces anomalies. Rows differ only in timestamp and
    git_commit, because `append_snapshot` dedups on commit.
    """
    db = proj / ".roam" / "index.db"
    conn = sqlite3.connect(str(db))
    with conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO snapshots (timestamp, tag, source, git_branch, git_commit, "
                "files, symbols, edges, cycles, god_components, bottlenecks, dead_exports, "
                "layer_violations, health_score, metrics_version) "
                "VALUES (?, ?, 'snapshot', 'main', ?, 3, 9, 4, 0, 0, 0, 0, 0, 90, 1)",
                (1_700_000_000 + i * 3600, f"flat{i}", f"commit{i}" + "0" * 33),
            )
    conn.close()


@pytest.fixture
def empty_history_project(tmp_path, monkeypatch):
    """Indexed project with ZERO snapshots -- the post-`roam init` state."""
    proj = tmp_path / "w1524_empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 1\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out
    conn = sqlite3.connect(str(proj / ".roam" / "index.db"))
    with conn:
        conn.execute("DELETE FROM snapshots")
    conn.close()
    return proj


@pytest.fixture
def flat_history_project(empty_history_project):
    """Project with 6 identical snapshots: analysis runs, finds nothing."""
    _seed_flat_snapshots(empty_history_project)
    return empty_history_project


def _summary(result):
    return json.loads(result.output)["summary"]


# ---------------------------------------------------------------------------
# MUST FIRE -- a gate over an empty history is UNANALYZABLE
# ---------------------------------------------------------------------------


def test_assert_refuses_with_no_snapshots_json(cli_runner, empty_history_project):
    result = invoke_cli(
        cli_runner,
        ["trends", "--assert", "health_score>=80", "--json"],
        cwd=empty_history_project,
    )
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["scan_incomplete"] is True
    # The assertions block must EXIST. Omitting it made "never evaluated"
    # indistinguishable from "not requested" for any JSON consumer.
    assert data["assertions"]["passed"] is False
    assert data["assertions"]["expression"] == "health_score>=80"
    assert len(data["assertions"]["failures"]) == 1


def test_assert_refusal_names_the_remedy(cli_runner, empty_history_project):
    """A newly-red first CI run must say how to make it green honestly."""
    result = invoke_cli(
        cli_runner,
        ["trends", "--assert", "health_score>=80", "--json"],
        cwd=empty_history_project,
    )
    failure = json.loads(result.output)["assertions"]["failures"][0]
    assert "never evaluated" in failure
    assert "roam trends --save" in failure


def test_assert_refuses_with_no_snapshots_text(cli_runner, empty_history_project):
    result = invoke_cli(cli_runner, ["trends", "--assert", "health_score>=80"], cwd=empty_history_project)
    assert result.exit_code == 1, result.output
    assert "ASSERTIONS FAILED" in result.output
    assert "roam trends --save" in result.output


@pytest.mark.parametrize("json_flag", [True, False])
def test_fail_on_anomaly_refuses_with_no_snapshots(cli_runner, empty_history_project, json_flag):
    """Channel parity: the answer may not depend on how it was asked for."""
    argv = ["trends", "--fail-on-anomaly"] + (["--json"] if json_flag else [])
    result = invoke_cli(cli_runner, argv, cwd=empty_history_project)
    assert result.exit_code == 1, result.output


def test_fail_on_anomaly_refuses_on_sub_four_history(cli_runner, empty_history_project):
    """1 snapshot: the anomaly detector never ran, so nothing is proven clean."""
    _seed_flat_snapshots(empty_history_project, n=1)
    result = invoke_cli(cli_runner, ["trends", "--fail-on-anomaly", "--json"], cwd=empty_history_project)
    assert result.exit_code == 1, result.output
    summary = _summary(result)
    assert summary["snapshots"] == 1
    assert summary["partial_success"] is True
    assert summary["scan_incomplete"] is True


def test_sub_four_text_channel_names_the_window(cli_runner, empty_history_project):
    _seed_flat_snapshots(empty_history_project, n=2)
    result = invoke_cli(cli_runner, ["trends", "--fail-on-anomaly"], cwd=empty_history_project)
    assert result.exit_code == 1, result.output
    assert "ANOMALY GATE UNANALYZABLE" in result.output
    assert "4 snapshots" in result.output


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- proving this is a refusal, not an outage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("json_flag", [True, False])
def test_plain_trends_on_empty_history_still_exits_zero(cli_runner, empty_history_project, json_flag):
    """No gate flag was passed, so there is no gate to refuse. THE key case.

    A fix that refuses on plain `roam trends` would be an outage, not a fix:
    an empty history is a complete and correct answer to "show me the
    timeline".
    """
    argv = ["trends"] + (["--json"] if json_flag else [])
    result = invoke_cli(cli_runner, argv, cwd=empty_history_project)
    assert result.exit_code == 0, result.output


def test_plain_trends_on_empty_history_claims_no_incompleteness(cli_runner, empty_history_project):
    """Without a gate flag the run is complete -- do not smear partial_success."""
    result = invoke_cli(cli_runner, ["trends", "--json"], cwd=empty_history_project)
    summary = _summary(result)
    assert summary["partial_success"] is False
    assert "scan_incomplete" not in summary


def test_plain_trends_on_short_history_is_a_complete_answer(cli_runner, empty_history_project):
    """2 snapshots and no analysis requested: a timeline, fully delivered."""
    _seed_flat_snapshots(empty_history_project, n=2)
    result = invoke_cli(cli_runner, ["trends", "--json"], cwd=empty_history_project)
    assert result.exit_code == 0, result.output
    summary = _summary(result)
    assert summary["snapshots"] == 2
    assert summary["partial_success"] is False
    assert "scan_incomplete" not in summary


@pytest.mark.parametrize("json_flag", [True, False])
def test_fail_on_anomaly_passes_when_analysis_ran_and_found_nothing(cli_runner, flat_history_project, json_flag):
    """The measured-CLEAN case: 6 snapshots, analysis ran, no anomalies -> 0.

    This is what separates the fix from "refuse whenever the history is
    short". The gate must still authorize a run it actually completed.
    """
    argv = ["trends", "--fail-on-anomaly"] + (["--json"] if json_flag else [])
    result = invoke_cli(cli_runner, argv, cwd=flat_history_project)
    assert result.exit_code == 0, result.output


def test_measured_clean_run_is_not_marked_partial(cli_runner, flat_history_project):
    result = invoke_cli(cli_runner, ["trends", "--fail-on-anomaly", "--json"], cwd=flat_history_project)
    summary = _summary(result)
    assert summary["partial_success"] is False
    assert "scan_incomplete" not in summary


def test_assertion_that_holds_still_passes(cli_runner, flat_history_project):
    """With history present, --assert keeps its ordinary evaluate-and-pass path."""
    result = invoke_cli(cli_runner, ["trends", "--assert", "health_score>=0", "--json"], cwd=flat_history_project)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["assertions"]["passed"] is True


def test_assertion_that_fails_still_fails_on_its_own_measurement(cli_runner, flat_history_project):
    """The pre-existing positive control, pinned: a MEASURED failure exits 1."""
    result = invoke_cli(
        cli_runner,
        ["trends", "--assert", "health_score>=99999", "--json"],
        cwd=flat_history_project,
    )
    assert result.exit_code == 1, result.output
    failures = json.loads(result.output)["assertions"]["failures"]
    assert any("health_score=90" in f for f in failures)
