"""W1460 — ``snapshots.metrics_version``: a stored snapshot must never be
compared against live metrics computed under a different definition.

THE INCIDENT this prevents
--------------------------
Three metric-moving fixes shipped together:

* the health-score formula was implemented twice and the copies disagreed,
* betweenness sampling was unseeded,
* the reference resolver applied a case-INSENSITIVE fallback to every
  language, manufacturing ~6.5% of all edges (``Path`` from pathlib bound to
  an unrelated ``PATH`` constant, and so on).

None of them changed a single line of user code. All three changed what the
numbers in ``snapshots`` MEAN. With SHIPPED defaults (``_DEFAULT_BUDGETS``,
used whenever ``.roam/budget.yaml`` is absent), the first post-upgrade
``roam budget`` on an UNCHANGED tree reported::

    [FAIL] No new cycles:  38 -> 50  (delta +12, budget max +0)   EXCEEDED
    exit 5

``cycles`` RISES while ``tangle_ratio`` FALLS, which looks contradictory
until you see the mechanism: the fabricated edges had fused unrelated code
into one 1484-symbol SCC. Removing them splits it into many small genuine
cycles with far fewer symbols trapped. More cycles, less tangle, better
graph — reported as a hard CI failure.

WHY THE VERSION IS ON THE METRICS, NOT THE SCORE
------------------------------------------------
Measured on this repository: running ``collect_metrics`` over one identical
DB under pre-fix code and post-fix code produces BYTE-IDENTICAL output,
health_score included. The movement is entirely in what the INDEX contains,
not in how it is read. A ``HEALTH_SCORE_VERSION`` would therefore have
caught nothing at all. The stamp has to be written when the index is built
and cover graph construction, which is what ``SNAPSHOT_METRICS_VERSION``
does.

NEGATIVE CONTROLS ARE LOAD-BEARING
-----------------------------------
"Skip everything, always" also prevents the incident, and is useless. Every
gate below is paired with a matching-version case proving the gate still
evaluates normally and can still FAIL on a real regression.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli

from roam.commands.metrics_history import (
    LEGACY_METRICS_VERSION,
    SNAPSHOT_METRICS_VERSION,
    is_current_metrics_version,
    snapshot_metrics_version,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SNAPSHOT_COLUMNS = (
    "timestamp, tag, source, git_branch, git_commit, files, symbols, edges, "
    "cycles, god_components, bottlenecks, dead_exports, layer_violations, "
    "health_score, tangle_ratio, avg_complexity, brain_methods"
)


def _seed(
    project: Path,
    *,
    metrics_version: int | None,
    timestamp: int | None = None,
    git_commit: str = "abc1234",
    git_branch: str = "main",
    source: str = "snapshot",
    **metrics,
) -> int:
    """Insert one snapshot row with an explicit metrics_version stamp.

    ``metrics_version=None`` writes a PRE-VERSION row — the shape every
    existing user's DB is in the moment they upgrade.
    """
    from roam.db.connection import open_db

    values = {
        "files": 3,
        "symbols": 10,
        "edges": 100,
        "cycles": 38,
        "god_components": 0,
        "bottlenecks": 0,
        "dead_exports": 0,
        "layer_violations": 0,
        "health_score": 80,
        "tangle_ratio": 3.6,
        "avg_complexity": 1.0,
        "brain_methods": 0,
    }
    values.update(metrics)
    ts = timestamp if timestamp is not None else int(time.time()) - 3600

    prev = Path.cwd()
    os.chdir(project)
    try:
        with open_db() as conn:
            cur = conn.execute(
                f"INSERT INTO snapshots ({_SNAPSHOT_COLUMNS}, metrics_version) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    None,
                    source,
                    git_branch,
                    git_commit,
                    values["files"],
                    values["symbols"],
                    values["edges"],
                    values["cycles"],
                    values["god_components"],
                    values["bottlenecks"],
                    values["dead_exports"],
                    values["layer_violations"],
                    values["health_score"],
                    values["tangle_ratio"],
                    values["avg_complexity"],
                    values["brain_methods"],
                    metrics_version,
                ),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        os.chdir(prev)


def _clear_snapshots(project: Path) -> None:
    from roam.db.connection import open_db

    prev = Path.cwd()
    os.chdir(project)
    try:
        with open_db() as conn:
            conn.execute("DELETE FROM snapshots")
            conn.commit()
    finally:
        os.chdir(prev)


# ---------------------------------------------------------------------------
# The version helper itself
# ---------------------------------------------------------------------------


def test_null_stamp_reads_as_legacy_never_as_current():
    """A NULL stamp must resolve to version 1, never to the current version.

    This is the whole safety property in one assertion: if "no stamp"
    resolved to the current version (e.g. via a ``or SNAPSHOT_METRICS_VERSION``
    default), every pre-upgrade row would silently pass every gate and the
    incident would be entirely unprevented.
    """
    assert snapshot_metrics_version({"metrics_version": None}) == LEGACY_METRICS_VERSION
    assert snapshot_metrics_version({}) == LEGACY_METRICS_VERSION
    assert snapshot_metrics_version(None) == LEGACY_METRICS_VERSION
    assert LEGACY_METRICS_VERSION != SNAPSHOT_METRICS_VERSION

    assert not is_current_metrics_version({"metrics_version": None})
    assert is_current_metrics_version({"metrics_version": SNAPSHOT_METRICS_VERSION})


def test_metrics_version_is_ahead_of_legacy():
    """The constant must actually have moved, or nothing is gated."""
    assert SNAPSHOT_METRICS_VERSION > LEGACY_METRICS_VERSION


# ---------------------------------------------------------------------------
# THE INCIDENT: roam budget
#
# The pair below is the whole proof. Both tests seed the IDENTICAL baseline
# via ``_REGRESSING_BASELINE`` — values chosen (and verified against pre-fix
# code) to make the SHIPPED default rules FAIL and exit 5. The ONLY
# difference between them is the ``metrics_version`` stamp. One bit,
# opposite outcomes: stale -> SKIP + exit 0, current -> FAIL + exit 5.
# ---------------------------------------------------------------------------

# Verified pre-fix: this baseline drives `roam budget` to exit 5 on the
# ``indexed_project`` fixture ("Complexity budget: 0 -> 0.2 EXCEEDED").
_REGRESSING_BASELINE = {
    "cycles": 0,
    "health_score": 100,
    "brain_methods": 0,
    "avg_complexity": 0.0,
    "layer_violations": 0,
    "god_components": 0,
}


def test_budget_skips_and_exits_zero_on_pre_version_baseline(cli_runner, indexed_project):
    """A pre-version (NULL) baseline + current code: SKIP, exit 0.

    THE INCIDENT, prevented. Against pre-fix code this exact seed produces::

        VERDICT: 1 of 6 budgets exceeded
        [FAIL] Complexity budget: 0 -> 0.2  (delta: +0.2, budget: max +10%)
        exit 5

    on a tree where nothing changed.
    """
    _clear_snapshots(indexed_project)
    _seed(indexed_project, metrics_version=None, **_REGRESSING_BASELINE)

    result = invoke_cli(cli_runner, ["budget"], cwd=indexed_project)

    assert result.exit_code == 0, (
        "roam budget must exit 0 when the only baseline predates the current "
        f"metrics definition. Got exit {result.exit_code}:\n{result.output}"
    )
    assert "FAIL" not in result.output, f"No rule may FAIL against a stale-definition baseline:\n{result.output}"
    assert "skipped" in result.output.lower()


def test_budget_json_discloses_the_version_mismatch(cli_runner, indexed_project):
    """Exit 0 alone is not enough — a skipped run must not read as a clean pass."""
    _clear_snapshots(indexed_project)
    _seed(indexed_project, metrics_version=None, **_REGRESSING_BASELINE)

    result = invoke_cli(cli_runner, ["budget"], cwd=indexed_project, json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    summary = data["summary"]

    assert summary["reason"] == "baseline_metrics_version_mismatch"
    assert summary["partial_success"] is True
    assert summary["baseline_metrics_version"] == LEGACY_METRICS_VERSION
    assert summary["metrics_version"] == SNAPSHOT_METRICS_VERSION
    assert summary["failed"] == 0
    assert summary["skipped"] == summary["rules_checked"]
    for rule in data["rules"]:
        assert rule["status"] == "SKIP"
        assert "metrics_version" in (rule["reason"] or "")


def test_budget_still_fails_on_a_real_regression_with_matching_version(cli_runner, indexed_project):
    """NEGATIVE CONTROL — the gate must not disarm budget.

    Same command, same rules, baseline stamped at the CURRENT version and
    seeded with metric values good enough that the live tree is a genuine
    regression against them. Exit 5 must still happen. Without this test,
    an implementation that skips unconditionally passes the test above.
    """
    _clear_snapshots(indexed_project)
    # IDENTICAL seed to the incident test above — only the stamp differs.
    _seed(indexed_project, metrics_version=SNAPSHOT_METRICS_VERSION, **_REGRESSING_BASELINE)

    result = invoke_cli(cli_runner, ["budget"], cwd=indexed_project, json_mode=True)
    data = json.loads(result.stdout)
    summary = data["summary"]

    assert summary.get("reason") != "baseline_metrics_version_mismatch"
    assert summary["skipped"] == 0, f"A current-version baseline must be EVALUATED, not skipped. summary={summary}"
    assert result.exit_code == 5, (
        "A real regression against a same-version baseline must still exit 5 "
        f"(the gate must not disarm budget). Got {result.exit_code}:\n{result.stdout}"
    )
    assert summary["failed"] > 0


def test_budget_passes_normally_when_versions_match_and_nothing_regressed(cli_runner, indexed_project):
    """NEGATIVE CONTROL — a same-version baseline equal to the live tree PASSES.

    Distinguishes "the gate skipped everything" from "the rules evaluated and
    held". Seeds the baseline from the live metrics themselves, so every
    delta is exactly zero.
    """
    from roam.commands.metrics_history import collect_metrics
    from roam.db.connection import open_db

    _clear_snapshots(indexed_project)
    prev = Path.cwd()
    os.chdir(indexed_project)
    try:
        with open_db(readonly=True) as conn:
            live = collect_metrics(conn)
    finally:
        os.chdir(prev)

    _seed(
        indexed_project,
        metrics_version=SNAPSHOT_METRICS_VERSION,
        **{k: v for k, v in live.items() if k not in ("spectral_gap",)},
    )

    result = invoke_cli(cli_runner, ["budget"], cwd=indexed_project, json_mode=True)
    data = json.loads(result.stdout)
    summary = data["summary"]

    assert result.exit_code == 0
    assert summary["skipped"] == 0, f"rules must have been evaluated, not skipped: {summary}"
    assert summary["failed"] == 0
    assert summary["passed"] == summary["rules_checked"]


# ---------------------------------------------------------------------------
# roam health --baseline
# ---------------------------------------------------------------------------


def test_health_baseline_suppresses_the_WHOLE_delta_on_mismatch(cli_runner, indexed_project):
    """Not just the score line — the entire delta block.

    The surviving columns lie in BOTH directions. ``cycles`` would show a
    real-looking regression that is not one, while ``god_components`` and
    ``bottlenecks`` are counts over SQL-LIMIT-capped lists that saturate at
    the cap and read as phantom IMPROVEMENTS. A partially-suppressed delta is
    worse than none: it looks like it was already corrected.
    """
    _clear_snapshots(indexed_project)
    _seed(indexed_project, metrics_version=None, cycles=38, god_components=50, bottlenecks=15)

    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project, json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.stdout)

    assert "delta" not in data, f"the WHOLE delta block must be suppressed, got keys: {sorted(data)}"
    assert data["summary"]["verdict"] == "DEGRADED"
    assert data["summary"]["reason"] == "baseline_metrics_version_mismatch"
    assert data["summary"]["baseline_metrics_version"] == LEGACY_METRICS_VERSION
    assert data["summary"]["metrics_version"] == SNAPSHOT_METRICS_VERSION


def test_health_baseline_mismatch_reason_is_distinct_from_no_baseline(cli_runner, indexed_project):
    """The two DEGRADED paths must stay distinguishable.

    They need different remediation: "run ``roam trends --save``" vs
    "re-run ``roam index``". Collapsing them into one reason string would
    send every upgrading user to the wrong fix.
    """
    _clear_snapshots(indexed_project)
    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project, json_mode=True)
    data = json.loads(result.stdout)
    assert data["summary"]["reason"] == "no_baseline_snapshot"
    assert "baseline_metrics_version" not in data["summary"]


def test_health_baseline_emits_a_full_delta_when_versions_match(cli_runner, indexed_project):
    """NEGATIVE CONTROL — a same-version baseline still produces the delta."""
    _clear_snapshots(indexed_project)
    _seed(indexed_project, metrics_version=SNAPSHOT_METRICS_VERSION, cycles=0, health_score=50)

    result = invoke_cli(cli_runner, ["health", "--baseline", "main"], cwd=indexed_project, json_mode=True)
    data = json.loads(result.stdout)

    assert "delta" in data, f"a current-version baseline must yield a delta; keys={sorted(data)}"
    assert data["summary"]["verdict"] != "DEGRADED"
    assert "score_delta" in data["delta"]


# ---------------------------------------------------------------------------
# roam bisect — blame must not name the upgrade as the culprit commit
# ---------------------------------------------------------------------------


def test_bisect_skips_the_straddling_pair():
    """The upgrade transition must not be attributed to a commit.

    ``_HIGHER_IS_BETTER["edges"] = True`` and the case-fold guard REMOVED
    edges, so the boundary pair reads as the single largest "degradation" in
    the history and sorts to rank 1 — every upgrading user would be told the
    fix was their worst regression.
    """
    from roam.commands.cmd_bisect import _compute_deltas

    # newest-first, as the command fetches them
    snapshots = [
        {"id": 3, "metrics_version": SNAPSHOT_METRICS_VERSION, "edges": 92000, "git_commit": "ccc"},
        {"id": 2, "metrics_version": None, "edges": 98746, "git_commit": "bbb"},
        {"id": 1, "metrics_version": None, "edges": 98700, "git_commit": "aaa"},
    ]
    deltas, straddling = _compute_deltas(snapshots, "edges")

    assert straddling == 1, "the version-boundary pair must be counted"
    assert [d["git_commit"] for d in deltas] == ["bbb"], (
        f"only the within-version pair may be attributed to a commit; got {[d['git_commit'] for d in deltas]}"
    )


def test_bisect_still_reports_deltas_within_one_version():
    """NEGATIVE CONTROL — same-version pairs are still compared and ranked."""
    from roam.commands.cmd_bisect import _compute_deltas

    snapshots = [
        {"id": 3, "metrics_version": SNAPSHOT_METRICS_VERSION, "edges": 90000, "git_commit": "ccc"},
        {"id": 2, "metrics_version": SNAPSHOT_METRICS_VERSION, "edges": 98746, "git_commit": "bbb"},
        {"id": 1, "metrics_version": SNAPSHOT_METRICS_VERSION, "edges": 98700, "git_commit": "aaa"},
    ]
    deltas, straddling = _compute_deltas(snapshots, "edges")

    assert straddling == 0
    assert len(deltas) == 2
    worst = max(deltas, key=lambda d: d["abs_delta"])
    assert worst["git_commit"] == "ccc"
    assert worst["direction"] == "degraded"


# ---------------------------------------------------------------------------
# roam fitness / alerts / forecast — series truncation at the boundary
# ---------------------------------------------------------------------------


def test_fitness_trend_drops_rows_from_a_superseded_definition(tmp_path):
    """``prev_avg`` must be computed over one definition only."""
    from roam.commands.cmd_fitness import _check_trend_rule

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (timestamp INTEGER, health_score INTEGER, metrics_version INTEGER)")
    # Three legacy rows at 90, then one current-version row at 60. Averaging
    # across the boundary yields a 30-point "drop" that is a unit change.
    conn.executemany(
        "INSERT INTO snapshots (timestamp, health_score, metrics_version) VALUES (?, ?, ?)",
        [(1, 90, None), (2, 90, None), (3, 90, None), (4, 60, SNAPSHOT_METRICS_VERSION)],
    )
    conn.commit()

    rule = {"name": "no health drop", "metric": "health_score", "window": 3, "max_decrease": 5}
    violations = _check_trend_rule(rule, conn)

    assert violations == [], (
        "the legacy rows must be dropped, leaving < 2 same-version rows and "
        f"therefore no judgeable trend; got {violations}"
    )


def test_fitness_trend_still_fires_within_one_version():
    """NEGATIVE CONTROL — a genuine drop inside one definition still violates."""
    from roam.commands.cmd_fitness import _check_trend_rule

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (timestamp INTEGER, health_score INTEGER, metrics_version INTEGER)")
    conn.executemany(
        "INSERT INTO snapshots (timestamp, health_score, metrics_version) VALUES (?, ?, ?)",
        [
            (1, 90, SNAPSHOT_METRICS_VERSION),
            (2, 90, SNAPSHOT_METRICS_VERSION),
            (3, 90, SNAPSHOT_METRICS_VERSION),
            (4, 60, SNAPSHOT_METRICS_VERSION),
        ],
    )
    conn.commit()

    rule = {"name": "no health drop", "metric": "health_score", "window": 3, "max_decrease": 5}
    violations = _check_trend_rule(rule, conn)

    assert len(violations) == 1, f"a real 30-point drop must still violate; got {violations}"
    assert "dropped by" in violations[0]["message"]


def test_alerts_series_truncates_at_the_version_boundary():
    """``_build_snap_dicts`` returns only the newest-version run."""
    from roam.commands.cmd_alerts import _build_snap_dicts

    def _row(ts, cycles, version):
        return {
            "timestamp": ts,
            "files": 1,
            "symbols": 1,
            "edges": 1,
            "cycles": cycles,
            "god_components": 0,
            "bottlenecks": 0,
            "dead_exports": 0,
            "layer_violations": 0,
            "health_score": 80,
            "metrics_version": version,
        }

    # newest-first, as get_snapshots returns
    raw = [
        _row(4, 50, SNAPSHOT_METRICS_VERSION),
        _row(3, 38, None),
        _row(2, 38, None),
        _row(1, 38, None),
    ]
    out = _build_snap_dicts(raw)

    assert len(out) == 1, f"only the current-version row survives; got {out}"
    assert out[0]["cycles"] == 50
    assert "metrics_version" not in out[0], (
        "metrics_version must NOT leak into the metric dicts — "
        "_delta_baseline_alerts treats every numeric value as a metric and "
        "would alert on the version number itself"
    )


def test_alerts_series_keeps_a_homogeneous_history_intact():
    """NEGATIVE CONTROL — a uniform history is not truncated at all."""
    from roam.commands.cmd_alerts import _build_snap_dicts

    def _row(ts, version):
        return {
            "timestamp": ts,
            "files": 1,
            "symbols": 1,
            "edges": 1,
            "cycles": ts,
            "god_components": 0,
            "bottlenecks": 0,
            "dead_exports": 0,
            "layer_violations": 0,
            "health_score": 80,
            "metrics_version": version,
        }

    all_legacy = [_row(3, None), _row(2, None), _row(1, None)]
    assert len(_build_snap_dicts(all_legacy)) == 3, "an all-legacy history is internally consistent — keep all of it"

    all_current = [_row(i, SNAPSHOT_METRICS_VERSION) for i in (3, 2, 1)]
    out = _build_snap_dicts(all_current)
    assert len(out) == 3
    assert [d["cycles"] for d in out] == [1, 2, 3], "chronological order (oldest-first) must be preserved"


def test_forecast_truncates_and_discloses():
    """Theil-Sen must not be fitted across a definition boundary.

    Theil-Sen is robust to OUTLIERS, not to a step change in the unit: a
    definition shift moves every later point by roughly the same amount, so
    the median pairwise slope tracks the step instead of rejecting it. That
    number is rendered as "<N> snapshots to structural failure".
    """
    from roam.commands.cmd_forecast import _snapshot_version_boundary

    rows = [
        {"metrics_version": None, "health_score": 90},
        {"metrics_version": None, "health_score": 89},
        {"metrics_version": None, "health_score": 88},
        {"metrics_version": SNAPSHOT_METRICS_VERSION, "health_score": 60},
    ]
    kept, dropped, version = _snapshot_version_boundary(rows)

    assert dropped == 3
    assert version == SNAPSHOT_METRICS_VERSION
    assert [r["health_score"] for r in kept] == [60]


def test_forecast_keeps_a_uniform_series_whole():
    """NEGATIVE CONTROL — nothing is dropped when the history is homogeneous."""
    from roam.commands.cmd_forecast import _snapshot_version_boundary

    rows = [{"metrics_version": None, "health_score": s} for s in (90, 89, 88, 87)]
    kept, dropped, version = _snapshot_version_boundary(rows)
    assert dropped == 0
    assert version == LEGACY_METRICS_VERSION
    assert len(kept) == 4


# ---------------------------------------------------------------------------
# workspace aggregator — never average two definitions
# ---------------------------------------------------------------------------


def test_workspace_average_excludes_stale_version_repos(monkeypatch):
    """A partly-reindexed workspace must not silently average two definitions."""
    from roam.workspace import aggregator

    repos = [
        {"name": "alpha", "health_score": 60, "metrics_version": SNAPSHOT_METRICS_VERSION},
        {"name": "beta", "health_score": 90, "metrics_version": None},
    ]
    monkeypatch.setattr(aggregator, "_query_repo_health", lambda info: dict(repos[info["i"]]))
    monkeypatch.setattr(aggregator, "get_cross_edges", lambda _c: [])

    out = aggregator.aggregate_health(None, [{"i": 0}, {"i": 1}])

    assert out["workspace_health"] == 60, "only the current-version repo may enter the mean"
    assert out["metrics_version_mixed"] is True
    assert out["partial_success"] is True
    assert out["repos_excluded_metrics_version"] == ["beta"]
    assert out["repos_scored"] == 1


def test_workspace_average_includes_every_repo_when_uniform(monkeypatch):
    """NEGATIVE CONTROL — a uniform workspace averages all repos, no disclosure."""
    from roam.workspace import aggregator

    repos = [
        {"name": "alpha", "health_score": 60, "metrics_version": SNAPSHOT_METRICS_VERSION},
        {"name": "beta", "health_score": 90, "metrics_version": SNAPSHOT_METRICS_VERSION},
    ]
    monkeypatch.setattr(aggregator, "_query_repo_health", lambda info: dict(repos[info["i"]]))
    monkeypatch.setattr(aggregator, "get_cross_edges", lambda _c: [])

    out = aggregator.aggregate_health(None, [{"i": 0}, {"i": 1}])

    assert out["workspace_health"] == 75
    assert out["repos_scored"] == 2
    assert "metrics_version_mixed" not in out
    assert "partial_success" not in out


def test_workspace_all_legacy_is_uniform_and_fully_included(monkeypatch):
    """An entirely un-upgraded workspace is internally consistent — keep it whole.

    Gating on ``== SNAPSHOT_METRICS_VERSION`` instead of "newest present"
    would zero out the workspace score for every user who has not reindexed
    anything yet.
    """
    from roam.workspace import aggregator

    repos = [
        {"name": "alpha", "health_score": 60, "metrics_version": None},
        {"name": "beta", "health_score": 90, "metrics_version": None},
    ]
    monkeypatch.setattr(aggregator, "_query_repo_health", lambda info: dict(repos[info["i"]]))
    monkeypatch.setattr(aggregator, "get_cross_edges", lambda _c: [])

    out = aggregator.aggregate_health(None, [{"i": 0}, {"i": 1}])

    assert out["workspace_health"] == 75
    assert out["repos_scored"] == 2
    assert "metrics_version_mixed" not in out


# ---------------------------------------------------------------------------
# Migration: an existing v18 DB opens, migrates, and keeps its rows
# ---------------------------------------------------------------------------


def test_old_db_at_user_version_18_migrates_to_19_and_rows_read_as_version_1(tmp_path):
    """The upgrade path, end to end.

    Builds a DB with the pre-W1460 ``snapshots`` shape at USER_VERSION 18,
    opens it through ``ensure_schema``, and asserts the column appears, the
    version moves, and the PRE-EXISTING row survives reading as version 1 —
    not as the current version, which would defeat every gate above.
    """
    from roam.db.connection import USER_VERSION, ensure_schema

    assert USER_VERSION == 19, "W1460 bumps the schema contract version in lockstep with the column"

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            tag TEXT,
            source TEXT NOT NULL,
            git_branch TEXT,
            git_commit TEXT,
            files INTEGER, symbols INTEGER, edges INTEGER, cycles INTEGER,
            god_components INTEGER, bottlenecks INTEGER, dead_exports INTEGER,
            layer_violations INTEGER, health_score INTEGER, tangle_ratio REAL,
            avg_complexity REAL, brain_methods INTEGER, spectral_gap REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO snapshots (timestamp, source, git_commit, cycles, health_score) VALUES (?, 'index', ?, ?, ?)",
        (1785505097, "8bf1f5c0", 38, 69),
    )
    conn.execute("PRAGMA user_version = 18")
    conn.commit()

    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert "metrics_version" not in cols_before

    ensure_schema(conn)
    conn.commit()

    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    assert "metrics_version" in cols_after, "migration 63 must add the column to an existing table"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 19

    row = conn.execute("SELECT * FROM snapshots").fetchone()
    assert row["cycles"] == 38, "the pre-existing data point must survive the migration"
    assert row["metrics_version"] is None
    assert snapshot_metrics_version(row) == LEGACY_METRICS_VERSION
    assert not is_current_metrics_version(row)
    conn.close()


def test_migration_is_idempotent(tmp_path):
    """``ensure_schema`` runs on every ``open_db`` — a second pass must be a no-op."""
    from roam.db.connection import ensure_schema

    db = tmp_path / "twice.db"
    conn = sqlite3.connect(str(db))
    ensure_schema(conn)
    ensure_schema(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(snapshots)")]
    assert cols.count("metrics_version") == 1
    conn.close()


# ---------------------------------------------------------------------------
# The DELETE: reindexing must not destroy the previous definition's data point
# ---------------------------------------------------------------------------


def test_reindex_of_the_same_commit_preserves_the_previous_definition_row(tmp_path, monkeypatch):
    """``append_snapshot`` dedups WITHIN a version, not across versions.

    The pre-existing ``DELETE FROM snapshots WHERE git_commit = ? AND
    source = ?`` ran regardless of version, so the first post-upgrade
    reindex of the same commit silently overwrote the only row describing
    what that commit scored under the old definition. Adding a column does
    not fix that on its own — the DELETE had to become version-aware.
    """
    from roam.commands import metrics_history
    from roam.db.connection import ensure_schema

    db = tmp_path / "reindex.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    # A pre-upgrade row for commit deadbee, source='index'.
    conn.execute(
        "INSERT INTO snapshots (timestamp, source, git_commit, cycles, metrics_version) "
        "VALUES (?, 'index', 'deadbee', 38, NULL)",
        (1000,),
    )
    conn.commit()

    monkeypatch.setattr(metrics_history, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(metrics_history, "_git_info", lambda _root: ("main", "deadbee"))
    monkeypatch.setattr(
        metrics_history,
        "collect_metrics",
        lambda _c: {
            "files": 1,
            "symbols": 1,
            "edges": 1,
            "cycles": 50,
            "god_components": 0,
            "bottlenecks": 0,
            "dead_exports": 0,
            "layer_violations": 0,
            "health_score": 70,
            "tangle_ratio": 0.7,
            "avg_complexity": 0.0,
            "brain_methods": 0,
            "spectral_gap": None,
        },
    )

    metrics_history.append_snapshot(conn, source="index")

    rows = conn.execute("SELECT cycles, metrics_version FROM snapshots ORDER BY id").fetchall()
    assert len(rows) == 2, (
        f"the pre-upgrade data point must survive a same-commit reindex — got {[dict(r) for r in rows]}"
    )
    assert rows[0]["cycles"] == 38 and rows[0]["metrics_version"] is None
    assert rows[1]["cycles"] == 50 and rows[1]["metrics_version"] == SNAPSHOT_METRICS_VERSION

    # NEGATIVE CONTROL: a SECOND reindex under the SAME definition still
    # dedups. Without this, the version-aware DELETE would let identical
    # rows accumulate at one commit and make Theil-Sen / Mann-Kendall
    # confidently vacuous — the exact failure the original DELETE prevented.
    metrics_history.append_snapshot(conn, source="index")
    rows = conn.execute("SELECT cycles, metrics_version FROM snapshots ORDER BY id").fetchall()
    assert len(rows) == 2, f"same-version re-index must still replace, not append: {[dict(r) for r in rows]}"
    assert rows[1]["metrics_version"] == SNAPSHOT_METRICS_VERSION
    conn.close()


def test_find_before_snapshot_prefers_the_newest_row_at_a_commit(tmp_path, monkeypatch):
    """Multiple rows per commit are now possible — the lookup must be ordered.

    ``find_before_snapshot`` matched ``WHERE git_commit = ? LIMIT 1`` with no
    ORDER BY. Once a commit can hold both a legacy and a current row, that
    returns whichever SQLite reaches first — in rowid order, the OLDEST. The
    version-aware DELETE is only safe together with this.
    """
    from roam.db.connection import ensure_schema
    from roam.graph import diff as graph_diff

    db = tmp_path / "order.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.executemany(
        "INSERT INTO snapshots (timestamp, source, git_commit, cycles, metrics_version) VALUES (?, 'index', ?, ?, ?)",
        [(1000, "deadbee", 38, None), (2000, "deadbee", 50, SNAPSHOT_METRICS_VERSION)],
    )
    conn.commit()

    monkeypatch.setattr(graph_diff, "resolve_base_commit", lambda _root, _ref: "deadbee")
    snap = graph_diff.find_before_snapshot(conn, tmp_path, "HEAD~1")

    assert snap["cycles"] == 50, f"the newest row at the commit must win, got {snap}"
    assert is_current_metrics_version(snap)
    conn.close()


# ---------------------------------------------------------------------------
# Writer contract
# ---------------------------------------------------------------------------


def test_append_snapshot_stamps_the_current_version(tmp_path, monkeypatch):
    """Every row written by this build carries the stamp — no NULL leaks."""
    from roam.commands import metrics_history
    from roam.db.connection import ensure_schema

    db = tmp_path / "write.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    monkeypatch.setattr(metrics_history, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(metrics_history, "_git_info", lambda _root: ("main", "cafe123"))
    monkeypatch.setattr(
        metrics_history,
        "collect_metrics",
        lambda _c: {
            "files": 1,
            "symbols": 1,
            "edges": 1,
            "cycles": 2,
            "god_components": 0,
            "bottlenecks": 0,
            "dead_exports": 0,
            "layer_violations": 0,
            "health_score": 70,
            "tangle_ratio": 0.7,
            "avg_complexity": 0.0,
            "brain_methods": 0,
            "spectral_gap": None,
        },
    )

    returned = metrics_history.append_snapshot(conn, source="snapshot")
    assert returned["metrics_version"] == SNAPSHOT_METRICS_VERSION

    row = conn.execute("SELECT metrics_version FROM snapshots").fetchone()
    assert row["metrics_version"] == SNAPSHOT_METRICS_VERSION
    conn.close()


# ---------------------------------------------------------------------------
# The un-migrated read-only path — the FIRST post-upgrade run
# ---------------------------------------------------------------------------


_PRE_W1460_SNAPSHOTS_DDL = """
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    tag TEXT,
    source TEXT NOT NULL,
    git_branch TEXT,
    git_commit TEXT,
    files INTEGER, symbols INTEGER, edges INTEGER, cycles INTEGER,
    god_components INTEGER, bottlenecks INTEGER, dead_exports INTEGER,
    layer_violations INTEGER, health_score INTEGER, tangle_ratio REAL,
    avg_complexity REAL, brain_methods INTEGER, spectral_gap REAL
);
"""


def _pre_w1460_conn(tmp_path, name="legacy.db"):
    """A DB with the OLD snapshots shape — no ``metrics_version`` column.

    ``open_db(readonly=True)`` does NOT run ``ensure_schema``, so this is
    exactly what every read-only consumer sees on the user's FIRST run after
    upgrading, before anything has reopened the DB read-write.
    """
    conn = sqlite3.connect(str(tmp_path / name))
    conn.row_factory = sqlite3.Row
    conn.executescript(_PRE_W1460_SNAPSHOTS_DDL)
    conn.executemany(
        "INSERT INTO snapshots (timestamp, source, git_commit, health_score, cycles, spectral_gap) "
        "VALUES (?, 'index', ?, ?, ?, ?)",
        [
            (1, "aaa", 90, 38, 0.45),
            (2, "bbb", 90, 38, 0.34),
            (3, "ccc", 90, 38, 0.22),
            (4, "ddd", 60, 50, 0.13),
        ],
    )
    conn.commit()
    return conn


def test_column_probe_reports_absence_on_a_pre_w1460_db(tmp_path):
    from roam.commands.metrics_history import snapshots_have_metrics_version

    old = _pre_w1460_conn(tmp_path)
    assert snapshots_have_metrics_version(old) is False
    old.close()

    from roam.db.connection import ensure_schema

    new = sqlite3.connect(str(tmp_path / "new.db"))
    ensure_schema(new)
    assert snapshots_have_metrics_version(new) is True
    new.close()


def test_fitness_does_not_crash_on_an_unmigrated_db(tmp_path):
    """The gate must not be the thing that breaks the run it protects."""
    from roam.commands.cmd_fitness import _check_trend_rule

    conn = _pre_w1460_conn(tmp_path)
    rule = {"name": "no health drop", "metric": "health_score", "window": 3, "max_decrease": 5}
    violations = _check_trend_rule(rule, conn)

    # All rows read as legacy => homogeneous => the rule evaluates normally
    # and still catches the genuine 90 -> 60 drop.
    assert len(violations) == 1, f"an un-migrated DB must still be judgeable; got {violations}"
    conn.close()


def test_forecast_does_not_crash_on_an_unmigrated_db(tmp_path):
    from roam.commands.cmd_forecast import _aggregate_forecasts, _spectral_gap_series

    conn = _pre_w1460_conn(tmp_path)

    results, n_rows, dropped = _aggregate_forecasts(conn, horizon=30)
    assert n_rows == 4, "every row is legacy, so none may be dropped"
    assert dropped == 0
    assert results, "a uniform legacy history must still forecast"

    series, series_dropped = _spectral_gap_series(conn)
    assert series == [0.45, 0.34, 0.22, 0.13], (
        "the whole gap series must survive an un-migrated DB — the missing "
        f"column must not be mistaken for a missing table; got {series}"
    )
    assert series_dropped == 0
    conn.close()


def test_aggregator_keeps_the_health_score_on_an_unmigrated_repo_db(tmp_path):
    """A repo that has not been reopened read-write still reports its score."""
    from roam.workspace.aggregator import _query_repo_health

    conn = _pre_w1460_conn(tmp_path, name="repo.db")
    conn.executescript("CREATE TABLE files (id INTEGER); CREATE TABLE symbols (id INTEGER);")
    conn.commit()
    conn.close()

    out = _query_repo_health({"name": "alpha", "db_path": str(tmp_path / "repo.db")})

    assert out["health_score"] == 60, f"the missing stamp column must not swallow the repo's health_score; got {out}"
    assert out["metrics_version"] == LEGACY_METRICS_VERSION


def test_budget_and_health_read_the_missing_column_as_legacy(tmp_path):
    """``SELECT *`` consumers need no probe — a missing key IS legacy."""
    conn = _pre_w1460_conn(tmp_path)
    row = conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()

    assert "metrics_version" not in row.keys()
    assert snapshot_metrics_version(row) == LEGACY_METRICS_VERSION
    assert not is_current_metrics_version(row), (
        "an un-migrated row must gate as stale — otherwise the first "
        "post-upgrade `roam budget` is exactly the un-fixed incident"
    )
    conn.close()
