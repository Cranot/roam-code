"""Tests for the W120 migration: hotspots detector emits to the central
findings registry.

The hotspots detector is the fifth migration onto the A4 findings table
(after W95 clones, W99 dead, W102 complexity, W115 bus-factor). It
continues to render its own JSON / text envelopes (authoritative output
surface) and ALSO, when ``--persist`` is set, emits one row per
runtime-classified symbol into ``findings``.

Hotspots is *runtime* by nature — every emitted finding comes from a
row in ``runtime_stats`` populated by ``roam ingest-trace``. All three
classifications (UPGRADE / CONFIRMED / DOWNGRADE) therefore carry the
``runtime`` confidence tier — they all require ingested production
traces. DOWNGRADE rows carry an ``evidence_json.disagreement`` flag so
agents filtering by classification can see the static-vs-runtime
delta without recomputing rank deltas.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_hotspots import (
    HOTSPOTS_DETECTOR_VERSION,
    _hotspots_finding_id,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_project(project_factory):
    """Small indexed project with three call-graph symbols ready to ingest."""
    return project_factory(
        {
            "api.py": "from service import process\ndef handle(): return process()\n",
            "service.py": "from utils import helper\ndef process(): return helper()\n",
            "utils.py": "def helper(): return 42\n",
        }
    )


@pytest.fixture
def generic_trace(tmp_path):
    """Synthetic trace covering all three classifications.

    - ``handle``  — top runtime caller, also high static rank (CONFIRMED)
    - ``process`` — top runtime caller, low static rank (UPGRADE)
    - ``helper``  — low runtime caller, also low static rank (CONFIRMED bottom-tier)

    The exact bucket each symbol lands in depends on the
    rank-discrepancy math in ``compute_hotspots``; the test asserts on
    the union, not specific labels per symbol.
    """
    trace = [
        {
            "function": "handle",
            "file": "api.py",
            "call_count": 5000,
            "p50_ms": 10,
            "p99_ms": 100,
            "error_rate": 0.01,
        },
        {
            "function": "process",
            "file": "service.py",
            "call_count": 4000,
            "p50_ms": 8,
            "p99_ms": 80,
            "error_rate": 0.0,
        },
        {
            "function": "helper",
            "file": "utils.py",
            "call_count": 50,
            "p50_ms": 2,
            "p99_ms": 5,
            "error_rate": 0.0,
        },
    ]
    p = tmp_path / "trace.json"
    p.write_text(json.dumps(trace))
    return str(p)


def _ingest_and_persist(proj, trace_path):
    """Ingest a trace into the indexed project, then run hotspots --persist.

    Returns the CliRunner result so tests can assert on the persist call
    itself when they care.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        ingest = runner.invoke(cli, ["ingest-trace", trace_path])
        assert ingest.exit_code == 0, ingest.output
        result = runner.invoke(cli, ["hotspots", "--persist"])
        assert result.exit_code == 0, result.output
        return result
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_hotspots_emits_to_findings_registry(runtime_project, generic_trace):
    """Running hotspots --persist after trace ingestion populates findings."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        _ingest_and_persist(runtime_project, generic_trace)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'hotspots'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one hotspots finding row"
        for r in rows:
            assert r["source_detector"] == "hotspots"
            assert r["source_version"] == HOTSPOTS_DETECTOR_VERSION
            # Hotspots attach to symbols (resolved from trace spans).
            assert r["subject_kind"] == "symbol"
            # All three classifications come from ingested runtime data —
            # the tier is uniform.
            assert r["confidence"] == "runtime"
            assert r["finding_id_str"].startswith("hotspots:")
    finally:
        os.chdir(old_cwd)


def test_hotspots_finding_id_is_deterministic():
    """_hotspots_finding_id returns the same id for the same (symbol_id, classification)."""
    a = _hotspots_finding_id(42, "UPGRADE")
    b = _hotspots_finding_id(42, "UPGRADE")
    assert a == b
    assert a.startswith("hotspots:upgrade:")
    # Different symbol_id → different id.
    assert _hotspots_finding_id(43, "UPGRADE") != a
    # Different classification → different id (a symbol that switches
    # buckets gets a fresh row rather than upserting the old one).
    assert _hotspots_finding_id(42, "CONFIRMED") != a
    assert _hotspots_finding_id(42, "DOWNGRADE") != a


def test_hotspots_rerun_upserts_not_duplicates(runtime_project, generic_trace):
    """Re-running hotspots --persist produces the same finding_id_str set."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        _ingest_and_persist(runtime_project, generic_trace)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'hotspots'"
                ).fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'hotspots'").fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"
        assert first_count >= 1

        # Second run — same trace, same code, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["hotspots", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'hotspots'"
                ).fetchall()
            }
            second_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'hotspots'").fetchone()[
                0
            ]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_hotspots_finding_evidence_carries_runtime_and_static_stats(runtime_project, generic_trace):
    """Evidence JSON carries the runtime + static rank pair and stats blocks."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        _ingest_and_persist(runtime_project, generic_trace)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, subject_id, claim FROM findings "
                "WHERE source_detector = 'hotspots' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in (
            "symbol_name",
            "file_path",
            "classification",
            "static_rank",
            "runtime_rank",
            "runtime_stats",
            "static_stats",
        ):
            assert k in evidence, f"evidence missing key {k}"
        # Each symbol has a subject_id (we skip un-resolvable spans).
        assert row["subject_id"] is not None
        # The classification must be one of the three documented buckets.
        assert evidence["classification"] in ("UPGRADE", "CONFIRMED", "DOWNGRADE")
        # The claim must name the classification and rank pair.
        claim_lower = (row["claim"] or "").lower()
        assert "runtime hotspot" in claim_lower
        assert "runtime_rank" in claim_lower

    finally:
        os.chdir(old_cwd)


def test_hotspots_downgrade_carries_disagreement_flag(runtime_project, tmp_path):
    """DOWNGRADE rows include an explicit static-vs-runtime disagreement note.

    We craft a trace where one symbol (``helper``) has very low traffic
    while still being indexable — the rank-discrepancy classifier
    typically marks the bottom of the runtime list as DOWNGRADE when
    the static rank ranked it higher.
    """
    # Heavy traffic on handle/process pushes helper to the runtime-bottom
    # bucket; helper has the simplest body so it carries low static
    # weight too — depending on the exact static rank distribution, the
    # detector may or may not produce a DOWNGRADE row. Either way, when
    # DOWNGRADE rows DO appear, the disagreement key must be present.
    trace = [
        {"function": "handle", "file": "api.py", "call_count": 10_000, "p50_ms": 10, "p99_ms": 100, "error_rate": 0.0},
        {
            "function": "process",
            "file": "service.py",
            "call_count": 9_500,
            "p50_ms": 8,
            "p99_ms": 80,
            "error_rate": 0.0,
        },
        {"function": "helper", "file": "utils.py", "call_count": 5, "p50_ms": 1, "p99_ms": 2, "error_rate": 0.0},
    ]
    p = tmp_path / "trace.json"
    p.write_text(json.dumps(trace))

    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        _ingest_and_persist(runtime_project, str(p))

        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'hotspots'").fetchall()
        for r in rows:
            evidence = json.loads(r["evidence_json"])
            if evidence["classification"] in ("UPGRADE", "DOWNGRADE"):
                # Discrepancy buckets must carry the explanation flag.
                assert "disagreement" in evidence, f"{evidence['classification']} row missing disagreement key"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_hotspots_findings_visible_via_cmd_findings_list(runtime_project, generic_trace):
    """`roam findings list --detector hotspots` returns rows after migration."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        _ingest_and_persist(runtime_project, generic_trace)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "hotspots"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "hotspots" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "hotspots" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_hotspots_findings_visible_via_cmd_findings_count(runtime_project, generic_trace):
    """`roam findings count` includes a non-zero entry for hotspots."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        _ingest_and_persist(runtime_project, generic_trace)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(runtime_project, "hotspots")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(runtime_project, generic_trace):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam hotspots`` without the flag must not write to ``findings``.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        runner = CliRunner()
        ingest = runner.invoke(cli, ["ingest-trace", generic_trace])
        assert ingest.exit_code == 0, ingest.output
        # No --persist.
        assert runner.invoke(cli, ["hotspots"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'hotspots'").fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist hotspots still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_hotspots_persist_no_traces_emits_nothing(runtime_project):
    """``hotspots --persist`` without runtime data exits cleanly + writes nothing.

    Hotspots only emits findings when ``runtime_stats`` has rows. With
    no traces ingested, the persist path must short-circuit before
    writing — and the command itself must still exit 0.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        runner = CliRunner()
        result = runner.invoke(cli, ["hotspots", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'hotspots'").fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "hotspots --persist wrote findings without runtime data"
    finally:
        os.chdir(old_cwd)


def test_hotspots_persist_no_findings_table_no_crash(runtime_project, generic_trace):
    """``hotspots --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after the
    trace ingest but before the persist call. The standard analysis
    path (legacy consumers depend on it) must keep working — the
    command exits 0 and writes no registry rows.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        runner = CliRunner()
        ingest = runner.invoke(cli, ["ingest-trace", generic_trace])
        assert ingest.exit_code == 0, ingest.output

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["hotspots", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


def test_hotspots_persist_rejects_security_and_danger_modes(runtime_project):
    """--persist + --security or --danger exits non-zero with a clear message.

    Both --security and --danger have their own subject surfaces (raw
    file/line for security, file-level danger score) and are not yet
    migrated to the central registry. Combining --persist with either
    mode must fail loudly rather than silently producing no findings.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(runtime_project))
        runner = CliRunner()

        r1 = runner.invoke(cli, ["hotspots", "--persist", "--security"])
        assert r1.exit_code != 0
        assert "persist" in r1.output.lower()

        r2 = runner.invoke(cli, ["hotspots", "--persist", "--danger"])
        assert r2.exit_code != 0
        assert "persist" in r2.output.lower()
    finally:
        os.chdir(old_cwd)
