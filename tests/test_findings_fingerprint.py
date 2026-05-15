"""Tests for the W155 migration: fingerprint detector emits to the central
findings registry.

The fingerprint detector is the next migration onto the A4 findings table
(after W95 clones, W99 dead, W102 complexity, W109 smells, W115 bus-factor,
W134 pr-risk, W151 health). Unlike health -- which emits four kinds across
cycles / god components / bottlenecks / layer violations -- fingerprint
mirrors only the cluster-level surface that legacy ``antipatterns`` glossed
as bare counts:

* ``arch.bad_cluster_pattern`` (clusters whose ``_classify_cluster_pattern``
  label flags ``monolith`` or ``leaky``) -> ``structural`` confidence.
* ``arch.cyclic_cluster`` (Tarjan SCCs that span more than one cluster) ->
  ``static_analysis`` confidence.

god-component rows are explicitly NOT emitted from fingerprint -- the W151
health migration owns the ``arch.god_component`` vocabulary via
``roam.quality.god_components``. The boundary check below pins that
contract so a future refactor doesn't quietly re-introduce double-counting.

The bulk of these tests exercise ``_emit_fingerprint_findings`` directly on
synthetic cluster / SCC dicts rather than via the full indexer + graph
pipeline, because reliably triggering a monolith cluster AND a leaky
cluster AND a cross-cluster SCC at fixture-size is awkward and slow. The
end-to-end smoke (CLI invocation) covers the wiring.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_fingerprint import (
    _BAD_CLUSTER_PATTERNS,
    _FINGERPRINT_KIND_TO_CONFIDENCE,
    _emit_fingerprint_findings,
    _fingerprint_bad_cluster_finding_id,
    _fingerprint_cyclic_cluster_finding_id,
    FINGERPRINT_DETECTOR_VERSION,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tiny_project(tmp_path):
    """Tiny indexable Python repo for the smoke CLI invocation.

    The fixture is intentionally minimal -- we don't try to provoke a
    monolith / leaky cluster AND a cross-cluster SCC at once. The
    per-kind emit assertions exercise ``_emit_fingerprint_findings``
    directly on synthetic inputs; the smoke test below just verifies
    the CLI wiring is intact.
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

    The detector + indexer aren't needed here -- we exercise
    ``_emit_fingerprint_findings`` directly on synthetic cluster /
    SCC dicts so each kind is verified independently of which a tiny
    fixture happens to produce.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def _synth_cluster(label, pattern, *, size_pct=15.0, conductance=0.3, layer=0):
    """Build a synthetic cluster summary dict matching compute_fingerprint."""
    return {
        "label": label,
        "layer": layer,
        "size_pct": size_pct,
        "conductance": conductance,
        "roles": {"function": 5, "class": 1},
        "pattern": pattern,
    }


def _synth_cyclic_scc(member_names, *, cluster_ids=(0, 1), cluster_labels=None, files=None):
    """Build a synthetic cross-cluster SCC dict matching _gather_cyclic_sccs."""
    return {
        "member_ids": list(range(100, 100 + len(member_names))),
        "member_names": list(member_names),
        "cluster_ids": list(cluster_ids),
        "cluster_labels": list(cluster_labels or [f"cluster-{i}" for i in cluster_ids]),
        "files": list(files or ["src/a.py", "src/b.py"]),
    }


# ---------------------------------------------------------------------------
# Stable finding-id helpers
# ---------------------------------------------------------------------------


def test_bad_cluster_finding_id_is_deterministic():
    """Same (label, pattern) -> same id; different inputs -> different ids."""
    a = _fingerprint_bad_cluster_finding_id("graph/Builder", "monolith")
    b = _fingerprint_bad_cluster_finding_id("graph/Builder", "monolith")
    assert a == b
    assert a.startswith("fingerprint:arch.bad_cluster_pattern:")
    # Different label -> different id.
    assert _fingerprint_bad_cluster_finding_id("commands/cli", "monolith") != a
    # Different pattern -> different id.
    assert _fingerprint_bad_cluster_finding_id("graph/Builder", "leaky") != a


def test_cyclic_cluster_finding_id_is_deterministic_and_order_independent():
    """_fingerprint_cyclic_cluster_finding_id is stable and order-independent.

    Mirrors the cmd_health._health_cycle_finding_id contract -- SCCs are
    unordered, so the digest must collapse permutations of the same
    member set onto the same id.
    """
    a = _fingerprint_cyclic_cluster_finding_id(["foo", "bar", "baz"])
    b = _fingerprint_cyclic_cluster_finding_id(["baz", "bar", "foo"])
    assert a == b
    assert a.startswith("fingerprint:arch.cyclic_cluster:")
    # Adding a member -> different id (structurally different SCC).
    assert (
        _fingerprint_cyclic_cluster_finding_id(["foo", "bar", "baz", "qux"]) != a
    )
    # Removing a member -> different id.
    assert _fingerprint_cyclic_cluster_finding_id(["foo", "bar"]) != a


# ---------------------------------------------------------------------------
# Per-kind emit + tier mapping
# ---------------------------------------------------------------------------


def test_emit_bad_cluster_monolith_emits_structural_tier(tmp_path):
    """arch.bad_cluster_pattern findings on a monolith land at structural tier."""
    with _seed_for_emit_helper(tmp_path) as conn:
        clusters = [
            _synth_cluster("graph/Builder", "monolith", size_pct=55.0, conductance=0.2),
        ]
        written = _emit_fingerprint_findings(
            conn, clusters, [], FINGERPRINT_DETECTOR_VERSION
        )
        assert written == 1
        row = conn.execute(
            "SELECT subject_kind, subject_id, confidence, evidence_json, "
            "       finding_id_str, claim, source_detector, source_version "
            "FROM findings WHERE source_detector = 'fingerprint'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == "structural"
        # Cluster is the NEW subject_kind for fingerprint -- first
        # cluster-level subject vocabulary entry in the registry.
        assert row["subject_kind"] == "cluster"
        # NULL subject_id mirrors the W134 pr-risk pattern (cluster
        # subjects don't map to a single symbols.id row).
        assert row["subject_id"] is None
        assert row["source_version"] == FINGERPRINT_DETECTOR_VERSION
        assert row["finding_id_str"].startswith(
            "fingerprint:arch.bad_cluster_pattern:"
        )
        ev = json.loads(row["evidence_json"])
        assert ev["kind"] == "arch.bad_cluster_pattern"
        assert ev["label"] == "graph/Builder"
        assert ev["pattern"] == "monolith"
        assert ev["size_pct"] == 55.0
        assert ev["qualified_name"] == "cluster:graph/Builder:monolith"
        # Claim names the cluster + the triggering metric.
        assert "graph/Builder" in (row["claim"] or "")
        assert "monolith" in (row["claim"] or "")


def test_emit_bad_cluster_leaky_emits_structural_tier(tmp_path):
    """arch.bad_cluster_pattern findings on a leaky cluster land at structural tier."""
    with _seed_for_emit_helper(tmp_path) as conn:
        clusters = [
            _synth_cluster(
                "commands/cli", "leaky", size_pct=12.0, conductance=0.65
            ),
        ]
        written = _emit_fingerprint_findings(
            conn, clusters, [], FINGERPRINT_DETECTOR_VERSION
        )
        assert written == 1
        row = conn.execute(
            "SELECT confidence, evidence_json, claim "
            "FROM findings WHERE source_detector = 'fingerprint'"
        ).fetchone()
        assert row["confidence"] == "structural"
        ev = json.loads(row["evidence_json"])
        assert ev["pattern"] == "leaky"
        assert ev["conductance"] == 0.65
        # Leaky claim must mention the conductance triggering metric.
        assert "conductance" in (row["claim"] or "")


def test_emit_skips_island_and_module_patterns(tmp_path):
    """Only ``monolith`` and ``leaky`` flag as findings; ``island`` / ``module`` do not.

    The pattern vocabulary is defined in
    ``roam.graph.fingerprint._classify_cluster_pattern``. island
    (well-isolated) and module (default) are the desirable outcomes and
    must not produce registry rows.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        clusters = [
            _synth_cluster("a", "island", conductance=0.05),
            _synth_cluster("b", "module", conductance=0.3),
            _synth_cluster("c", "monolith", size_pct=60.0),
        ]
        written = _emit_fingerprint_findings(
            conn, clusters, [], FINGERPRINT_DETECTOR_VERSION
        )
        # Only the monolith fires.
        assert written == 1
        rows = conn.execute(
            "SELECT evidence_json FROM findings WHERE source_detector = 'fingerprint'"
        ).fetchall()
        assert len(rows) == 1
        ev = json.loads(rows[0]["evidence_json"])
        assert ev["pattern"] == "monolith"


def test_emit_cyclic_cluster_emits_static_analysis_tier(tmp_path):
    """arch.cyclic_cluster findings land at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        scc = _synth_cyclic_scc(
            ["foo", "bar", "baz"],
            cluster_ids=(0, 1),
            cluster_labels=["graph/Builder", "commands/cli"],
            files=["src/a.py", "src/b.py"],
        )
        written = _emit_fingerprint_findings(
            conn, [], [scc], FINGERPRINT_DETECTOR_VERSION
        )
        assert written == 1
        row = conn.execute(
            "SELECT subject_kind, subject_id, confidence, evidence_json, "
            "       finding_id_str, claim "
            "FROM findings WHERE source_detector = 'fingerprint'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == "static_analysis"
        # Cross-cluster SCCs share the ``cycle`` subject_kind with
        # health's arch.cycle vocabulary -- both are SCC-shaped subjects.
        assert row["subject_kind"] == "cycle"
        assert row["subject_id"] is None
        assert row["finding_id_str"].startswith("fingerprint:arch.cyclic_cluster:")
        ev = json.loads(row["evidence_json"])
        assert ev["kind"] == "arch.cyclic_cluster"
        assert ev["size"] == 3
        assert ev["spanned_cluster_count"] == 2
        assert set(ev["member_names"]) == {"foo", "bar", "baz"}
        assert ev["cluster_labels"] == ["graph/Builder", "commands/cli"]
        # Claim names cluster span + SCC size.
        claim = row["claim"] or ""
        assert "3 symbols" in claim
        assert "2 clusters" in claim


def test_emit_writes_both_kinds_in_one_pass(tmp_path):
    """One emit call covering both kinds produces both rows."""
    with _seed_for_emit_helper(tmp_path) as conn:
        clusters = [
            _synth_cluster("big", "monolith", size_pct=50.0),
            _synth_cluster("loose", "leaky", conductance=0.7),
        ]
        sccs = [
            _synth_cyclic_scc(["foo", "bar"], cluster_ids=(0, 1)),
        ]
        written = _emit_fingerprint_findings(
            conn, clusters, sccs, FINGERPRINT_DETECTOR_VERSION
        )
        assert written == 3
        kinds = {
            row["finding_id_str"].split(":")[1]
            for row in conn.execute(
                "SELECT finding_id_str FROM findings "
                "WHERE source_detector = 'fingerprint'"
            ).fetchall()
        }
        assert kinds == {"arch.bad_cluster_pattern", "arch.cyclic_cluster"}


def test_fingerprint_kind_tier_table_covers_both_kinds():
    """Drift guard: every emitted kind has an explicit tier entry.

    Mirrors the W151 health-side coverage test -- if a new fingerprint
    kind is added to ``_emit_fingerprint_findings`` without a matching
    entry in ``_FINGERPRINT_KIND_TO_CONFIDENCE``, the emit will KeyError;
    this test keeps the failure mode obvious during code review.
    """
    expected = {"arch.bad_cluster_pattern", "arch.cyclic_cluster"}
    assert set(_FINGERPRINT_KIND_TO_CONFIDENCE.keys()) == expected


def test_bad_cluster_pattern_vocabulary_matches_classifier():
    """The bad-pattern set tracks ``_classify_cluster_pattern``'s flagged labels.

    Drift guard: ``roam.graph.fingerprint._classify_cluster_pattern`` is
    the single source of truth for cluster pattern labels (monolith /
    island / leaky / module). Only ``monolith`` (size > 40%) and
    ``leaky`` (conductance > 0.5) are architectural smells -- the other
    two are healthy outcomes. If a new pattern label lands in the
    classifier, this test reminds the author to decide whether it should
    flag here.
    """
    assert _BAD_CLUSTER_PATTERNS == frozenset({"monolith", "leaky"})


# ---------------------------------------------------------------------------
# Upsert + deterministic id behaviour
# ---------------------------------------------------------------------------


def test_emit_rerun_upserts_not_duplicates(tmp_path):
    """Re-running the emit helper produces the same ``finding_id_str`` set."""
    with _seed_for_emit_helper(tmp_path) as conn:
        clusters = [_synth_cluster("big", "monolith", size_pct=55.0)]
        sccs = [_synth_cyclic_scc(["a", "b"], cluster_ids=(0, 1))]
        first = _emit_fingerprint_findings(
            conn, clusters, sccs, FINGERPRINT_DETECTOR_VERSION
        )
        first_ids = {
            r[0]
            for r in conn.execute(
                "SELECT finding_id_str FROM findings "
                "WHERE source_detector = 'fingerprint'"
            ).fetchall()
        }
        # Second emit on identical inputs.
        second = _emit_fingerprint_findings(
            conn, clusters, sccs, FINGERPRINT_DETECTOR_VERSION
        )
        second_ids = {
            r[0]
            for r in conn.execute(
                "SELECT finding_id_str FROM findings "
                "WHERE source_detector = 'fingerprint'"
            ).fetchall()
        }
        total = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE source_detector = 'fingerprint'"
        ).fetchone()[0]
        assert first == 2
        assert second == 2
        assert total == 2, "rerun should upsert, not duplicate"
        assert first_ids == second_ids


# ---------------------------------------------------------------------------
# Boundary check: no god_object / god_component rows from fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_does_not_emit_god_component_rows(tmp_path):
    """fingerprint must NOT emit any arch.god_component / god_object rows.

    The W151 health migration owns the ``arch.god_component`` vocabulary
    via ``roam.quality.god_components``. fingerprint's legacy
    ``antipatterns.god_objects`` count stays in the envelope payload as
    a back-compat alias, but never lands in the registry under the
    fingerprint detector -- emitting from both would double-count.

    This is a contract check on the emit helper: even when handed
    arbitrary cluster / SCC payloads, it never writes a row whose kind
    or finding_id_str references god_objects / god_component.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        clusters = [
            _synth_cluster("big", "monolith", size_pct=55.0),
            _synth_cluster("loose", "leaky", conductance=0.65),
        ]
        sccs = [_synth_cyclic_scc(["a", "b"], cluster_ids=(0, 1))]
        _emit_fingerprint_findings(
            conn, clusters, sccs, FINGERPRINT_DETECTOR_VERSION
        )
        rows = conn.execute(
            "SELECT finding_id_str, evidence_json, claim FROM findings "
            "WHERE source_detector = 'fingerprint'"
        ).fetchall()
        for r in rows:
            fid = r["finding_id_str"] or ""
            ev = json.loads(r["evidence_json"] or "{}")
            kind = ev.get("kind") or ""
            assert "god_component" not in fid
            assert "god_object" not in fid
            assert "god_component" not in kind
            assert "god_object" not in kind


# ---------------------------------------------------------------------------
# End-to-end CLI smoke (small project)
# ---------------------------------------------------------------------------


def test_fingerprint_persist_smoke_no_crash(tmp_path):
    """`roam fingerprint --persist` runs cleanly on a tiny indexed repo.

    The fixture doesn't necessarily produce a monolith / leaky cluster
    or a cross-cluster SCC (it's deliberately minimal) -- the smoke is
    that the persist branch wires through without crashing and leaves
    the findings table in a queryable state.
    """
    proj = _tiny_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["fingerprint", "--persist"])
        assert result.exit_code == 0, result.output
        # Whether or not findings were emitted is fixture-dependent,
        # but the table must exist and be queryable.
        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE source_detector = 'fingerprint'"
            ).fetchone()[0]
            assert count >= 0
    finally:
        os.chdir(old_cwd)


def test_fingerprint_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch -- running
    ``roam fingerprint`` without the flag must not write to ``findings``.
    """
    proj = _tiny_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        result = runner.invoke(cli, ["fingerprint"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings "
                    "WHERE source_detector = 'fingerprint'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist fingerprint still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_fingerprint_persist_no_findings_table_no_crash(tmp_path):
    """``fingerprint --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index
    but before the persist call. The standard detector-output path
    (text / JSON) which legacy consumers depend on must keep working --
    the command exits 0 and writes no registry rows.
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

        result = runner.invoke(cli, ["fingerprint", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_fingerprint_findings_visible_via_cmd_findings_list_when_populated(tmp_path):
    """`roam findings list --detector fingerprint` returns the emitted rows.

    Drives ``_emit_fingerprint_findings`` directly with synthetic
    findings, then checks the read-side CLI sees them. This decouples
    the visibility check from whether a tiny fixture happens to produce
    a monolith / leaky cluster or a cross-cluster SCC under the
    indexer + Louvain.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    # Seed the findings table on the project's index DB by going
    # through open_db so the schema migration runs.
    with open_db(readonly=False, project_root=proj) as conn:
        clusters = [_synth_cluster("big", "monolith", size_pct=55.0)]
        sccs = [_synth_cyclic_scc(["a", "b"], cluster_ids=(0, 1))]
        _emit_fingerprint_findings(
            conn, clusters, sccs, FINGERPRINT_DETECTOR_VERSION
        )
        conn.commit()

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "fingerprint"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 2
        assert "fingerprint" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "fingerprint" for r in envelope["findings"]
        )

        result = runner.invoke(cli, ["--json", "findings", "count"])
        assert result.exit_code == 0, result.output
        env2 = json.loads(result.output)
        assert env2["summary"]["state"] == "populated"
        assert "fingerprint" in env2["counts"]
        assert env2["counts"]["fingerprint"] >= 2
    finally:
        os.chdir(old_cwd)
