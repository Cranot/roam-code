"""Tests for the W151 follow-up: health detector emits to the central
findings registry.

The health detector is the fifth detector migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, ``complexity`` in
W102, and ``smells`` in W109). It continues to compute its 4
architecture-level finding arrays (cycles, god components, bottlenecks,
layer violations) and ALSO emits one row per finding to ``findings``
when invoked with ``--persist``. These tests cover that additive emit
and the end-to-end visibility through ``roam findings`` for an agent.

The 4 kinds use 2-segment dotted vocabulary (``arch.cycle``,
``arch.god_component``, ``arch.bottleneck``, ``arch.layer_violation``)
to set up future kind-namespace grouping. ``arch.layer_violation`` is
the first edge-level (``subject_kind="edge"``) user of the registry —
the from / to symbol ids are encoded in ``evidence_json`` because the
registry's ``subject_id`` is single-valued.

The bulk of these tests exercise ``_emit_health_findings`` directly on
synthetic finding lists rather than via the full indexer + graph
pipeline, because hand-rolling a fixture that reliably triggers all 4
arch-level kinds (SCC, degree > 20, high betweenness, layer-violating
edge) at once is awkward at fixture-size and slow. The end-to-end
smoke (CLI invocation) is covered by ``test_health_persist_smoke``
below.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_health import (
    _HEALTH_KIND_TO_CONFIDENCE,
    HEALTH_DETECTOR_VERSION,
    _emit_health_findings,
    _health_bottleneck_finding_id,
    _health_cycle_finding_id,
    _health_god_finding_id,
    _health_layer_violation_finding_id,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tiny_project(tmp_path):
    """Tiny indexable Python repo for the smoke CLI invocation.

    The fixture is intentionally minimal — we don't try to provoke an
    SCC + a god component + a bottleneck + a layer violation all at
    once. The per-kind emit assertions exercise
    ``_emit_health_findings`` directly on synthetic finding lists; the
    smoke test below just verifies the CLI wiring is intact.
    """
    return _make_project(
        tmp_path,
        {
            "a.py": """
            def alpha():
                return beta()

            def beta():
                return 1
            """,
            "b.py": """
            from .a import alpha

            def gamma():
                return alpha()
            """,
        },
    )


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The detector + indexer aren't needed here — we exercise
    ``_emit_health_findings`` directly on synthetic finding dicts so
    each of the 4 arch-level kinds is verified independently of which
    of them a tiny fixture happens to produce.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def _synth_cycle(member_names, files, severity="warning", actionable=True):
    # W718: lowercase canonical severity (W547). The emit helper
    # normalises pre-W718 UPPER-cased input transparently, so synth
    # fixtures can pass either case; the lowercase form is canonical.
    """Build a synthetic formatted-cycle dict.

    Mirrors the shape produced by ``format_cycles`` +
    ``mark_actionable_cycles`` so the emit helper sees the same fields
    it would in production.
    """
    symbols = [{"id": None, "name": n, "kind": "function", "file_path": files[0]} for n in member_names]
    return {
        "symbols": symbols,
        "files": list(files),
        "size": len(member_names),
        "actionable": actionable,
        "local_only": False,
        "has_test_file": False,
        "file_count": len(set(files)),
        "severity": severity,
    }


def _synth_god(name, *, degree=60, file="src/svc.py", severity="critical"):
    return {
        "name": name,
        "kind": "function",
        "degree": degree,
        "file": file,
        "category": "actionable",
        "severity": severity,
    }


def _synth_bottleneck(name, *, betweenness=120.0, file="src/svc.py", severity="warning"):
    return {
        "name": name,
        "kind": "function",
        "betweenness": betweenness,
        "file": file,
        "category": "actionable",
        "severity": severity,
    }


def _synth_layer_violation(
    *,
    source=101,
    target=202,
    source_name="lo",
    target_name="hi",
    source_layer=1,
    target_layer=0,
):
    """A find_violations-style row + the matching v_lookup entry."""
    v = {
        "source": source,
        "target": target,
        "source_layer": source_layer,
        "target_layer": target_layer,
        "layer_distance": source_layer - target_layer,
        "severity": "warning",
    }
    v_lookup = {
        source: {"id": source, "name": source_name, "file_path": "src/lo.py"},
        target: {"id": target, "name": target_name, "file_path": "src/hi.py"},
    }
    return v, v_lookup


# ---------------------------------------------------------------------------
# Stable finding-id helpers
# ---------------------------------------------------------------------------


def test_health_cycle_finding_id_is_deterministic():
    """_health_cycle_finding_id is stable and order-independent."""
    a = _health_cycle_finding_id(["foo", "bar", "baz"])
    b = _health_cycle_finding_id(["baz", "bar", "foo"])  # different order
    assert a == b
    assert a.startswith("health:arch.cycle:")
    # Adding a member changes the id (structurally different SCC).
    assert _health_cycle_finding_id(["foo", "bar", "baz", "qux"]) != a
    # Removing a member also changes it.
    assert _health_cycle_finding_id(["foo", "bar"]) != a


def test_health_god_finding_id_is_deterministic():
    """_health_god_finding_id keys on the qualified name."""
    a = _health_god_finding_id("src/svc.py::handle")
    b = _health_god_finding_id("src/svc.py::handle")
    assert a == b
    assert a.startswith("health:arch.god_component:")
    assert _health_god_finding_id("src/svc.py::other") != a


def test_health_bottleneck_finding_id_is_deterministic():
    a = _health_bottleneck_finding_id("src/svc.py::dispatch")
    b = _health_bottleneck_finding_id("src/svc.py::dispatch")
    assert a == b
    assert a.startswith("health:arch.bottleneck:")
    assert _health_bottleneck_finding_id("src/svc.py::other") != a


def test_health_layer_violation_finding_id_keys_on_edge_endpoints():
    a = _health_layer_violation_finding_id("src/lo.py::lo", "src/hi.py::hi")
    b = _health_layer_violation_finding_id("src/lo.py::lo", "src/hi.py::hi")
    assert a == b
    assert a.startswith("health:arch.layer_violation:")
    # Different direction → different id (the edge is directional).
    assert _health_layer_violation_finding_id("src/hi.py::hi", "src/lo.py::lo") != a


# ---------------------------------------------------------------------------
# Per-kind emit + tier mapping
# ---------------------------------------------------------------------------


def test_emit_health_cycle_emits_static_analysis_tier(tmp_path):
    """arch.cycle findings land at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        cyc = _synth_cycle(["foo", "bar", "baz"], ["src/a.py", "src/b.py"])
        written = _emit_health_findings(conn, [cyc], [], [], [], HEALTH_DETECTOR_VERSION)
        assert written == 1
        row = conn.execute(
            "SELECT subject_kind, confidence, evidence_json, claim, "
            "       source_detector, source_version, finding_id_str "
            "FROM findings WHERE source_detector = 'health'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == "static_analysis"
        assert row["source_version"] == HEALTH_DETECTOR_VERSION
        assert row["finding_id_str"].startswith("health:arch.cycle:")
        # No anchor resolution possible in this synthetic fixture — the
        # symbols table is empty, so subject_kind falls back to 'cycle'.
        assert row["subject_kind"] in ("symbol", "cycle")
        ev = json.loads(row["evidence_json"])
        assert ev["kind"] == "arch.cycle"
        assert ev["size"] == 3
        assert ev["actionable"] is True
        assert {m["name"] for m in ev["cycle_members"]} == {"foo", "bar", "baz"}
        assert ev["files"] == ["src/a.py", "src/b.py"]


def test_emit_health_god_component_emits_static_analysis_tier(tmp_path):
    """arch.god_component findings land at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        god = _synth_god("BigService", degree=80)
        written = _emit_health_findings(conn, [], [god], [], [], HEALTH_DETECTOR_VERSION)
        assert written == 1
        row = conn.execute(
            "SELECT subject_kind, confidence, evidence_json, finding_id_str "
            "FROM findings WHERE source_detector = 'health'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == "static_analysis"
        assert row["subject_kind"] == "symbol"
        assert row["finding_id_str"].startswith("health:arch.god_component:")
        ev = json.loads(row["evidence_json"])
        assert ev["kind"] == "arch.god_component"
        assert ev["name"] == "BigService"
        assert ev["degree"] == 80
        assert ev["severity"] == "critical"  # W718: lowercase canonical (W547)


def test_emit_health_bottleneck_emits_structural_tier(tmp_path):
    """arch.bottleneck findings land at structural confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        bn = _synth_bottleneck("dispatch", betweenness=250.0)
        written = _emit_health_findings(conn, [], [], [bn], [], HEALTH_DETECTOR_VERSION)
        assert written == 1
        row = conn.execute(
            "SELECT subject_kind, confidence, evidence_json, finding_id_str "
            "FROM findings WHERE source_detector = 'health'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == "structural"
        assert row["subject_kind"] == "symbol"
        assert row["finding_id_str"].startswith("health:arch.bottleneck:")
        ev = json.loads(row["evidence_json"])
        assert ev["kind"] == "arch.bottleneck"
        assert ev["name"] == "dispatch"
        assert ev["betweenness"] == 250.0


def test_emit_health_layer_violation_uses_edge_subject_kind(tmp_path):
    """arch.layer_violation uses the new ``subject_kind='edge'`` vocabulary."""
    with _seed_for_emit_helper(tmp_path) as conn:
        v, v_lookup = _synth_layer_violation(source_name="low_level", target_name="high_level")
        written = _emit_health_findings(conn, [], [], [], [v], HEALTH_DETECTOR_VERSION, v_lookup=v_lookup)
        assert written == 1
        row = conn.execute(
            "SELECT subject_kind, subject_id, confidence, evidence_json, "
            "       finding_id_str, claim "
            "FROM findings WHERE source_detector = 'health'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == "static_analysis"
        # First edge-level user of the registry — subject_kind is the
        # free-form W89 vocabulary entry; subject_id is NULL because the
        # registry's single subject_id column can't carry a (from, to)
        # pair. The edge endpoints live in evidence_json instead.
        assert row["subject_kind"] == "edge"
        assert row["subject_id"] is None
        assert row["finding_id_str"].startswith("health:arch.layer_violation:")
        ev = json.loads(row["evidence_json"])
        assert ev["kind"] == "arch.layer_violation"
        assert ev["from_symbol_id"] == v["source"]
        assert ev["to_symbol_id"] == v["target"]
        assert ev["from_symbol_name"] == "low_level"
        assert ev["to_symbol_name"] == "high_level"
        assert ev["from_layer"] == 1
        assert ev["to_layer"] == 0
        # Claim names both endpoints so a human or agent can act on it.
        assert "low_level" in (row["claim"] or "")
        assert "high_level" in (row["claim"] or "")


def test_emit_health_writes_all_four_kinds_in_one_pass(tmp_path):
    """One emit call covering all 4 arrays produces 4 rows."""
    with _seed_for_emit_helper(tmp_path) as conn:
        cyc = _synth_cycle(["foo", "bar"], ["src/a.py"])
        god = _synth_god("BigService")
        bn = _synth_bottleneck("dispatch")
        v, v_lookup = _synth_layer_violation()
        written = _emit_health_findings(
            conn,
            [cyc],
            [god],
            [bn],
            [v],
            HEALTH_DETECTOR_VERSION,
            v_lookup=v_lookup,
        )
        assert written == 4
        kinds = {
            row["finding_id_str"].split(":")[1]
            for row in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'health'").fetchall()
        }
        assert kinds == {
            "arch.cycle",
            "arch.god_component",
            "arch.bottleneck",
            "arch.layer_violation",
        }


def test_health_kind_tier_table_covers_all_four_kinds():
    """Drift guard: every emitted kind has an explicit tier entry.

    Mirrors the smells-side coverage test — if a new arch-level kind
    is added to ``_emit_health_findings`` without a matching entry in
    ``_HEALTH_KIND_TO_CONFIDENCE`` the emit will KeyError, but this
    keeps the failure mode obvious during code review.
    """
    expected = {
        "arch.cycle",
        "arch.god_component",
        "arch.bottleneck",
        "arch.layer_violation",
    }
    assert set(_HEALTH_KIND_TO_CONFIDENCE.keys()) == expected


# ---------------------------------------------------------------------------
# Upsert + deterministic id behaviour
# ---------------------------------------------------------------------------


def test_emit_health_rerun_upserts_not_duplicates(tmp_path):
    """Re-running the emit helper produces the same ``finding_id_str`` set."""
    with _seed_for_emit_helper(tmp_path) as conn:
        cyc = _synth_cycle(["foo", "bar"], ["src/a.py"])
        god = _synth_god("BigService")
        bn = _synth_bottleneck("dispatch")
        v, v_lookup = _synth_layer_violation()
        first = _emit_health_findings(conn, [cyc], [god], [bn], [v], HEALTH_DETECTOR_VERSION, v_lookup=v_lookup)
        first_ids = {
            r[0]
            for r in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'health'").fetchall()
        }
        # Second emit on identical inputs.
        second = _emit_health_findings(conn, [cyc], [god], [bn], [v], HEALTH_DETECTOR_VERSION, v_lookup=v_lookup)
        second_ids = {
            r[0]
            for r in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'health'").fetchall()
        }
        total = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'health'").fetchone()[0]
        assert first == 4
        assert second == 4
        assert total == 4, "rerun should upsert, not duplicate"
        assert first_ids == second_ids


# ---------------------------------------------------------------------------
# End-to-end CLI smoke (small project)
# ---------------------------------------------------------------------------


def test_health_persist_smoke_no_crash(tmp_path):
    """`roam health --persist` runs cleanly on a tiny indexed repo.

    The fixture doesn't necessarily produce any of the 4 arch-level
    findings (it's deliberately minimal) — the smoke is that the
    persist branch wires through without crashing and leaves the
    findings table in a queryable state.
    """
    proj = _tiny_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["health", "--persist"])
        assert result.exit_code == 0, result.output
        # Whether or not findings were emitted is fixture-dependent,
        # but the table must exist and be queryable.
        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'health'").fetchone()[0]
            assert count >= 0
    finally:
        os.chdir(old_cwd)


def test_health_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free."""
    proj = _tiny_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        result = runner.invoke(cli, ["health"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'health'").fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist health still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_health_persist_no_findings_table_no_crash(tmp_path):
    """``health --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index
    but before the persist call. The standard detector-output path
    (text / JSON / SARIF) which legacy consumers depend on must keep
    working — the command exits 0 and writes no registry rows.
    """
    proj = _tiny_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["health", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_health_findings_visible_via_cmd_findings_list_when_populated(tmp_path):
    """`roam findings list --detector health` returns the emitted rows.

    Drives ``_emit_health_findings`` directly with synthetic findings,
    then checks the read-side CLI sees them. This decouples the
    visibility check from whether a tiny fixture happens to produce
    an actionable cycle / god component / bottleneck / layer
    violation under the indexer.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    # Seed the findings table on the project's index DB by going
    # through open_db so the schema migration runs.
    with open_db(readonly=False, project_root=proj) as conn:
        cyc = _synth_cycle(["foo", "bar"], ["src/a.py"])
        god = _synth_god("BigService")
        bn = _synth_bottleneck("dispatch")
        v, v_lookup = _synth_layer_violation()
        _emit_health_findings(conn, [cyc], [god], [bn], [v], HEALTH_DETECTOR_VERSION, v_lookup=v_lookup)
        conn.commit()

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "health"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 4
        assert "health" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "health" for r in envelope["findings"])

        result = runner.invoke(cli, ["--json", "findings", "count"])
        assert result.exit_code == 0, result.output
        env2 = json.loads(result.output)
        assert env2["summary"]["state"] == "populated"
        assert "health" in env2["counts"]
        assert env2["counts"]["health"] >= 4
    finally:
        os.chdir(old_cwd)
