"""Tests for the W96 follow-up: dead-export detector emits to the central
findings registry.

The dead-export detector is the second migration onto the A4 findings
table (after W95's clones). It continues to render its own JSON / SARIF /
text envelopes (authoritative output surface) and ALSO, when
``--persist`` is set, emits one row per dead-export into ``findings``.
These tests cover that additive emit and the end-to-end visibility
through ``roam findings`` for an agent.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_dead import (
    DEAD_DETECTOR_VERSION,
    _dead_finding_id,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project


def _dead_project(tmp_path):
    """Tiny repo with one used + one orphan Python function.

    ``orphan_export`` is exported (top-level def) but has no callers in
    the project — the dead detector should flag it as SAFE.
    ``used_helper`` IS called by ``main``, so it should NOT appear in
    the findings set.
    """
    return _make_project(
        tmp_path,
        {
            "lib.py": """
            def used_helper(value):
                return value * 2

            def orphan_export(items):
                results = []
                for item in items:
                    results.append(item)
                return results
            """,
            "main.py": """
            from .lib import used_helper

            def main():
                return used_helper(5)
            """,
        },
    )


def _run_dead_persist(proj):
    """Index the project and run ``dead --persist``.

    Returns the CliRunner result of the dead call so tests can assert
    on its exit code if they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["dead", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_dead_emits_to_findings_registry(tmp_path):
    """Running dead --persist on a fixture with an unused export populates findings."""
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_dead_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'dead'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one dead-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "dead"
            assert r["source_version"] == DEAD_DETECTOR_VERSION
            assert r["subject_kind"] == "symbol"
            # SAFE → static_analysis, REVIEW → structural, INTENTIONAL → heuristic.
            assert r["confidence"] in ("static_analysis", "structural", "heuristic")
            assert r["finding_id_str"].startswith("dead:export:")
    finally:
        os.chdir(old_cwd)


def test_dead_finding_id_str_is_deterministic_unit():
    """_dead_finding_id returns the same id on repeated input."""
    a = _dead_finding_id(42, "src/lib.py", "function")
    b = _dead_finding_id(42, "src/lib.py", "function")
    assert a == b
    assert a.startswith("dead:export:")
    # Different inputs → different ids (no accidental hash collision in
    # this tiny set).
    assert _dead_finding_id(43, "src/lib.py", "function") != a
    assert _dead_finding_id(42, "src/other.py", "function") != a
    assert _dead_finding_id(42, "src/lib.py", "class") != a


def test_dead_finding_id_str_is_deterministic_e2e(tmp_path):
    """Re-running dead --persist produces the same finding_id_str (upsert)."""
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_dead_persist(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'dead'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'dead'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str on first run"

        # Second run — same fixture, same code, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["dead", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'dead'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'dead'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_dead_finding_evidence_links_to_export(tmp_path):
    """The finding's evidence JSON references the dead-export name + action."""
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_dead_persist(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, subject_id FROM findings "
                "WHERE source_detector = 'dead' LIMIT 1"
            ).fetchone()
            assert row is not None
            evidence = json.loads(row["evidence_json"])
            assert "name" in evidence
            assert "kind" in evidence
            assert "action" in evidence
            assert evidence["action"] in (
                "SAFE",
                "REVIEW",
                "INTENTIONAL",
                "INTENTIONAL_SCAFFOLDING",
            )
            # subject_id must resolve to a real symbol row.
            sym = conn.execute(
                "SELECT id, name FROM symbols WHERE id = ?",
                (row["subject_id"],),
            ).fetchone()
            assert sym is not None, f"orphan subject_id {row['subject_id']}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_dead_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector dead` returns rows after migration."""
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_dead_persist(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "dead"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "dead" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "dead" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_dead_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for dead."""
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_dead_persist(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "dead")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_dead_no_findings_table_no_crash(tmp_path):
    """``roam dead --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index but
    before dead --persist runs. The normal dead-export text/JSON output
    must keep working — registry emit is purely additive.
    """
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["dead", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


def test_dead_without_persist_does_not_emit_findings(tmp_path):
    """Without --persist, no findings rows are written.

    The registry mirror is gated behind the explicit ``--persist`` flag —
    running ``roam dead`` plain must remain side-effect-free, matching
    the readonly contract every other dead invocation already honours.
    """
    proj = _dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["dead"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'dead'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist dead still wrote to findings"
    finally:
        os.chdir(old_cwd)
