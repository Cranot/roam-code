"""W1461 — a health measurement that could not be TAKEN is never reported clean.

INCIDENT
--------
``metrics_history.collect_metrics`` builds the symbol graph inside a
``try`` whose handler was the ONLY one in the module without a
``log_swallowed`` sibling::

    except (ImportError, sqlite3.Error):
        cycles = 0
        G = None

A failed graph build floors THREE penalty inputs to their BEST possible
values at once — ``cycles`` 0, an empty SCC list making ``tangle_ratio``
0.0, and ``G = None`` making ``layer_violations`` 0 — so
``compute_health_score`` returns a near-perfect number.

MEASURED, on a 3-file fixture holding one real import cycle
(``a -> b -> c -> a``), with the graph build broken by SCHEMA DRIFT alone
(an older index lacking ``symbols.qualified_name``, which
``build_symbol_graph`` SELECTs — no monkeypatching)::

    intact schema   cycles 1  tangle_ratio 75.0  health_score  9
    drifted schema  cycles 0  tangle_ratio  0.0  health_score 85   (+76)

The inflation SCALES WITH HOW TANGLED THE CODE ACTUALLY IS: on an acyclic
fixture the same drift moves health_score by 0. The floor helps most
exactly where the code is worst.

The returned dict's key set was byte-identical to the honest one and held
zero disclosure keys, so no consumer could tell. ``append_snapshot`` then
INSERTed the fabricated score into ``snapshots``, a table with no column
able to record that the graph build failed — permanently indistinguishable
from a genuinely healthy measurement. ``roam budget`` ships a DEFAULT rule
``{"metric": "health_score", "max_decrease": 5}`` and calls
``ctx.exit(EXIT_GATE_FAILURE)``, so a degraded run silently PASSED the
health gate and the next HONEST run read as a 76-point regression and
failed it spuriously.

THE SHAPE
---------
A value crossed a boundary without the identity of the producer that made
it, and the receiver's default for "producer identity ABSENT" was
"MATCHES" rather than "UNKNOWN". Absent resolved to EQUAL.

THE FIX
-------
1. ``collect_metrics`` logs the swallow and returns an explicit
   ``degraded_metrics`` list (empty on every healthy run).
2. ``append_snapshot`` REFUSES to persist a degraded measurement
   (``stored: False``) — the previous honest baseline stays the baseline.
3. ``cmd_budget._evaluate_rule`` FAILS any rule whose metric is degraded.
   A gate cannot certify a value it did not compute. This is the choke
   point shared by ``roam budget`` and ``roam attest``.

Every assertion below was confirmed to FAIL against pre-fix HEAD.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

from roam.commands.metrics_history import append_snapshot, collect_metrics

# Pinned HERE, independently of the implementation: the metrics that are
# unmeasurable without the symbol graph. If a new graph-derived metric is
# added to ``collect_metrics`` it must be added here too, or it will floor
# silently exactly as these four did.
GRAPH_DERIVED_METRICS = ("cycles", "tangle_ratio", "layer_violations", "health_score")

# --------------------------------------------------------------------------
# Fixtures: one TANGLED project (a real import cycle) and one HEALTHY project.
# The pair is the point — the floor is invisible on healthy input, so a test
# that only exercises a clean tree cannot see this defect at all.
# --------------------------------------------------------------------------

_TANGLED_SOURCES = {
    "a.py": "from b import beta\ndef alpha():\n    return beta()\n",
    "b.py": "from c import gamma\ndef beta():\n    return gamma()\n",
    "c.py": "from a import alpha\ndef gamma():\n    return alpha()\n",
}

_HEALTHY_SOURCES = {
    "h_base.py": "X = 1\n",
    "h_mid.py": "from h_base import X\nY = X + 1\n",
    "h_top.py": "from h_mid import Y\nZ = Y + 1\n",
}


def _make_project(tmp_path: Path, name: str, sources: dict[str, str]) -> Path:
    project = tmp_path / name
    project.mkdir()
    for fname, body in sources.items():
        (project / fname).write_text(body, encoding="utf-8")
    git_init(project)
    index_in_process(project)
    return project


@pytest.fixture
def tangled_project(tmp_path):
    return _make_project(tmp_path, "tangled", _TANGLED_SOURCES)


@pytest.fixture
def healthy_project(tmp_path):
    return _make_project(tmp_path, "healthy", _HEALTHY_SOURCES)


def _break_symbol_graph(project: Path) -> None:
    """Break the graph build the way a real legacy index does.

    ``build_symbol_graph`` SELECTs ``s.qualified_name``; an index written
    before that column existed raises ``sqlite3.OperationalError`` there
    while every plain ``COUNT(*)`` probe in ``collect_metrics`` still
    succeeds. No monkeypatching — this is schema drift, the exact family
    ``metrics_history``'s own comments name.
    """
    db = project / ".roam" / "index.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("DROP INDEX IF EXISTS idx_symbols_qualified")
        conn.execute("ALTER TABLE symbols DROP COLUMN qualified_name")
        conn.commit()
    finally:
        conn.close()


def _open(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project / ".roam" / "index.db")
    conn.row_factory = sqlite3.Row
    return conn


def _snapshot_count(project: Path) -> int:
    conn = _open(project)
    try:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 1. collect_metrics — the disclosure sibling
# --------------------------------------------------------------------------


def test_module_names_every_graph_derived_metric(tangled_project):
    """The implementation's list and this test's list must not drift apart."""
    from roam.commands import metrics_history

    declared = getattr(metrics_history, "GRAPH_DERIVED_METRICS", None)
    assert declared is not None, (
        "metrics_history must NAME the metrics that are unmeasurable without "
        "the symbol graph; without a name there is nothing for the sinks to check."
    )
    assert set(declared) == set(GRAPH_DERIVED_METRICS), (
        f"drifted: module={sorted(declared)!r} test={sorted(GRAPH_DERIVED_METRICS)!r}"
    )


def test_unbuildable_graph_is_disclosed_not_floored_silently(tangled_project):
    """The incident. A failed graph build must announce itself."""
    _break_symbol_graph(tangled_project)
    conn = _open(tangled_project)
    try:
        metrics = collect_metrics(conn)
    finally:
        conn.close()

    degraded = metrics.get("degraded_metrics")
    assert degraded, (
        "collect_metrics floored cycles/tangle_ratio/layer_violations/health_score "
        "to their BEST possible values with no disclosure whatsoever. "
        f"health_score={metrics.get('health_score')} on a tree with a real import cycle. "
        f"degraded_metrics={degraded!r}"
    )
    assert "health_score" in degraded, (
        "health_score is the value a budget gate acts on; it MUST be named as "
        f"unmeasured. degraded_metrics={degraded!r}"
    )
    assert set(degraded) == set(GRAPH_DERIVED_METRICS), (
        "every graph-derived metric floors together — naming only some of them "
        f"leaves the rest silently fabricated. got {sorted(degraded)!r}"
    )


def test_the_floor_actually_inflates_the_score_on_tangled_code(tangled_project):
    """Guards the PREMISE, so the test above can never pass vacuously.

    If a future refactor makes the graph build unbreakable by this route, or
    makes the floor harmless, this test fails and tells us the guard above is
    no longer measuring anything.
    """
    conn = _open(tangled_project)
    try:
        honest = collect_metrics(conn)
    finally:
        conn.close()

    assert honest["cycles"] >= 1, f"fixture must hold a real cycle; got {honest['cycles']}"
    assert not honest["degraded_metrics"], "intact index must not report degradation"

    _break_symbol_graph(tangled_project)
    conn = _open(tangled_project)
    try:
        floored = collect_metrics(conn)
    finally:
        conn.close()

    assert floored["health_score"] > honest["health_score"], (
        "the whole defect is that an unmeasurable graph reads BETTER than a "
        f"measured tangled one: honest={honest['health_score']} floored={floored['health_score']}"
    )


def test_healthy_project_is_never_marked_degraded(healthy_project):
    """NEGATIVE CONTROL.

    An implementation that marks everything degraded — or that simply
    hard-fails on any index — would satisfy every assertion above. It must
    fail here. An acyclic, intact project reports a real score and an EMPTY
    degradation list.
    """
    conn = _open(healthy_project)
    try:
        metrics = collect_metrics(conn)
    finally:
        conn.close()

    assert metrics["degraded_metrics"] == [], (
        "a healthy index must report NO degradation; a guard that always fires "
        f"is not a guard. got {metrics['degraded_metrics']!r}"
    )
    assert isinstance(metrics["health_score"], (int, float))


# --------------------------------------------------------------------------
# 2. append_snapshot — the persistence sink
# --------------------------------------------------------------------------


def test_degraded_metrics_are_never_written_into_snapshots(tangled_project):
    """The stored row is permanent and has no column able to disclose this."""
    before_rows = _snapshot_count(tangled_project)
    _break_symbol_graph(tangled_project)

    prev = Path.cwd()
    os.chdir(tangled_project)
    try:
        conn = _open(tangled_project)
        try:
            result = append_snapshot(conn, tag="degraded", source="test")
        finally:
            conn.close()
    finally:
        os.chdir(prev)

    assert result.get("stored") is False, (
        f"append_snapshot must refuse a measurement it could not take; stored={result.get('stored')!r}"
    )
    assert _snapshot_count(tangled_project) == before_rows, (
        "a fabricated health score was persisted into `snapshots`. The table has "
        "no column able to record that the graph build failed, so the row is "
        "permanently indistinguishable from a genuinely healthy measurement."
    )
    cols = {r[1] for r in _open(tangled_project).execute("PRAGMA table_info(snapshots)")}
    assert not (cols & {"degraded", "partial", "status"}), (
        "if a disclosure column is ever added, this refusal can be relaxed to a "
        "disclosed write — update this test deliberately, do not delete it."
    )


def test_healthy_snapshot_is_still_written(healthy_project):
    """NEGATIVE CONTROL — refusing to write ALWAYS would break trend tracking."""
    before_rows = _snapshot_count(healthy_project)

    prev = Path.cwd()
    os.chdir(healthy_project)
    try:
        conn = _open(healthy_project)
        try:
            result = append_snapshot(conn, tag="ok", source="test")
        finally:
            conn.close()
    finally:
        os.chdir(prev)

    assert result.get("stored") is True, f"a healthy snapshot must be persisted; got {result.get('stored')!r}"
    assert _snapshot_count(healthy_project) == before_rows + 1, (
        "the honest write path must still append a row — a guard that blocks everything is not a fix."
    )


# --------------------------------------------------------------------------
# 3. cmd_budget._evaluate_rule — the terminal verdict
# --------------------------------------------------------------------------


def _health_rule():
    from roam.commands.cmd_budget import _DEFAULT_BUDGETS

    for rule in _DEFAULT_BUDGETS:
        if rule.get("metric") == "health_score":
            return rule
    raise AssertionError(f"no shipped default rule reads health_score: {_DEFAULT_BUDGETS!r}")


def test_budget_rule_cannot_pass_on_an_unmeasured_metric():
    """A gate cannot certify a value it did not compute."""
    from roam.commands.cmd_budget import _evaluate_rule

    rule = _health_rule()
    before = {"health_score": 9}
    # exactly what the floor produced in the measured incident
    after = {"health_score": 85, "degraded_metrics": list(GRAPH_DERIVED_METRICS)}

    result = _evaluate_rule(rule, before, after)

    assert result["status"] != "PASS", (
        "a floored health_score satisfied the shipped 'Health score floor' rule, "
        f"so a broken index silently passed the gate. result={result!r}"
    )
    assert result["status"] == "FAIL", (
        f"an unmeasured metric must fail closed, not be quietly skipped. result={result!r}"
    )
    assert "could not be measured" in (result.get("reason") or ""), (
        f"exit 5 must say WHY, or it is unactionable. reason={result.get('reason')!r}"
    )


def test_budget_rule_still_passes_and_still_fails_normally():
    """NEGATIVE CONTROL — the guard must not disarm or hijack the gate."""
    from roam.commands.cmd_budget import _evaluate_rule

    rule = _health_rule()
    healthy = {"degraded_metrics": []}

    held = _evaluate_rule(rule, {"health_score": 80}, {"health_score": 80, **healthy})
    assert held["status"] == "PASS", f"an unchanged healthy score must PASS. got {held!r}"

    regressed = _evaluate_rule(rule, {"health_score": 80}, {"health_score": 20, **healthy})
    assert regressed["status"] == "FAIL", f"a real 60-point drop must still FAIL. got {regressed!r}"
    assert "could not be measured" not in (regressed.get("reason") or ""), (
        "a genuine regression must not be mislabelled as an unmeasured metric"
    )


# --------------------------------------------------------------------------
# 4. End to end through the CLI
# --------------------------------------------------------------------------


def _break_only_the_graph_build(monkeypatch):
    """Fail ONLY ``build_symbol_graph``, leaving every other query working.

    The schema-drift trigger used above is caught by the CLI's top-level
    schema guard before ``_handle_save`` runs (verified: `roam trends --save`
    on a drifted index exits 1 with "Database schema error"), so it cannot
    exercise the command layer. The wider ``sqlite3.Error`` family can:
    a lock/busy/disk-I/O failure on the ``edges`` read fails the graph build
    while the plain ``COUNT(*)`` probes around it succeed. That is what this
    simulates.
    """
    import roam.graph.builder as builder

    def _boom(_conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(builder, "build_symbol_graph", _boom)


def test_roam_trends_save_does_not_claim_a_snapshot_it_refused(cli_runner, tangled_project, monkeypatch):
    """The user-facing string must not say 'snapshot saved' when none was."""
    _break_only_the_graph_build(monkeypatch)
    before_rows = _snapshot_count(tangled_project)

    result = invoke_cli(cli_runner, ["trends", "--save"], cwd=tangled_project, json_mode=True)
    data = json.loads(result.stdout)
    summary = data["summary"]

    assert summary.get("partial_success") is True, (
        f"a run that could not measure health is DEGRADED, not clean. summary={summary!r}"
    )
    assert "not saved" in summary["verdict"].lower(), (
        f"the verdict claimed a save that did not happen. verdict={summary['verdict']!r}"
    )
    assert _snapshot_count(tangled_project) == before_rows


def test_roam_trends_save_still_saves_on_a_healthy_project(cli_runner, healthy_project):
    """NEGATIVE CONTROL — the normal save path is untouched."""
    before_rows = _snapshot_count(healthy_project)

    result = invoke_cli(cli_runner, ["trends", "--save"], cwd=healthy_project, json_mode=True)
    data = json.loads(result.stdout)
    summary = data["summary"]

    assert summary.get("partial_success") is not True, f"a healthy save is not degraded. summary={summary!r}"
    assert "snapshot saved" in summary["verdict"].lower(), f"verdict={summary['verdict']!r}"
    assert summary["health_score"] is not None
    assert _snapshot_count(healthy_project) == before_rows + 1
