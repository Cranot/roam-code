"""Tests for the W122 follow-up: taint detector emits to the central
findings registry.

The taint detector is the fifth migration onto the A4 findings table
(after W95's clones, W99's dead, W102's complexity, and W110's n1). It
continues to render its own JSON / SARIF / text envelopes (authoritative
output surface) and ALSO, when ``--persist`` is set, emits one row per
taint flow into ``findings``. These tests cover that additive emit and
the end-to-end visibility through ``roam findings`` for an agent.

Fixture choice: a tiny Flask-ish Python project with a request-input
source flowing into a ``subprocess.run`` sink. The ``python-command-
injection`` rule pack catches this — the source (``request.args``) and
sink (``subprocess.run``) are name-matched and connected by a real
forward call edge in the indexed graph, so the BFS emits a finding.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_taint import (
    TAINT_DETECTOR_VERSION,
    _taint_finding_id,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project


def _taint_project(tmp_path):
    """Tiny Python project with a taint flow source -> sink.

    The taint engine's name-based matching only fires on symbols that
    appear in the indexed ``symbols`` table — external module members
    like ``subprocess.run`` or ``request.args`` are NOT indexed when
    they aren't defined locally. We therefore define local stub
    functions named ``input`` (a python-command-injection source) and
    ``eval`` (a sink), then a ``handler`` that calls both.

    The resulting flow is the **intraprocedural co-call shape**:
    ``handler`` has edges TO both ``input`` and ``eval`` but no forward
    edge connects source -> sink. The W122 emit tags this as
    ``flow_shape=co_call`` and assigns ``confidence=structural``.
    """
    return _make_project(
        tmp_path,
        {
            "tainted.py": """
            # Local stand-ins for python builtins — match the
            # python-command-injection rule's `input` source and `eval`
            # sink. Defining them locally puts them in the symbols
            # table so the name-based matcher finds them.
            def input():
                return "untrusted"

            def eval(code):
                return code

            def handler():
                # Calls source then sink with no forward edge between
                # them — co-call shape -> structural confidence tier.
                data = input()
                return eval(data)
            """,
        },
    )


def _run_taint_persist(proj):
    """Index the project and run ``taint --rule command-injection --persist``.

    Restricts to the ``command-injection`` rule pack so unrelated rules
    (php, javascript, etc.) don't run on the Python fixture — keeps the
    test fast and the assertion set focused on the one flow the fixture
    is designed to surface.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["taint", "--rule", "command-injection", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_taint_finding_id_str_is_deterministic_unit():
    """``_taint_finding_id`` returns the same id on repeated input."""
    a = _taint_finding_id("python-command-injection", 42, 99, [42, 7, 99])
    b = _taint_finding_id("python-command-injection", 42, 99, [42, 7, 99])
    assert a == b
    assert a.startswith("taint:python-command-injection:")
    # Different inputs -> different ids (no accidental hash collision).
    assert _taint_finding_id("python-sqli", 42, 99, [42, 7, 99]) != a
    assert _taint_finding_id("python-command-injection", 43, 99, [42, 7, 99]) != a
    assert _taint_finding_id("python-command-injection", 42, 99, [42, 99]) != a


def test_taint_persist_flag_default_off(tmp_path):
    """Without --persist, no findings rows are written.

    The registry mirror is gated behind the explicit ``--persist`` flag —
    running ``roam taint`` plain must remain side-effect-free, matching
    the readonly contract every other taint invocation already honours.
    """
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["taint"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'taint'").fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist taint still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_taint_emits_to_findings_registry(tmp_path):
    """Running ``taint --persist`` on a fixture with a real flow populates findings."""
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_taint_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'taint'"
            ).fetchall()
        # We assert >= 1 (not == 1) because the engine's two passes
        # (forward BFS + co-call) can both fire on the same fixture
        # depending on how the call-graph extractor resolved
        # ``request.args.get`` vs ``request.args`` symbols. Either way,
        # at least one row must exist.
        assert len(rows) >= 1, "expected at least one taint-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "taint"
            assert r["source_version"] == TAINT_DETECTOR_VERSION
            assert r["subject_kind"] == "symbol"
            # forward_bfs -> static_analysis, co_call -> structural.
            assert r["confidence"] in ("static_analysis", "structural")
            assert r["finding_id_str"].startswith("taint:")
    finally:
        os.chdir(old_cwd)


def test_taint_finding_id_str_is_deterministic_e2e(tmp_path):
    """Re-running ``taint --persist`` produces the same finding_id_str (upsert)."""
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_taint_persist(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'taint'").fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'taint'").fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str on first run"

        # Second run — same fixture, same code, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["taint", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'taint'").fetchall()
            }
            second_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'taint'").fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_taint_finding_evidence_links_to_flow(tmp_path):
    """The finding's evidence JSON references rule_id, source, sink, and path."""
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_taint_persist(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, subject_id, claim FROM findings WHERE source_detector = 'taint' LIMIT 1"
            ).fetchone()
            assert row is not None
            evidence = json.loads(row["evidence_json"])
            assert "rule_id" in evidence
            assert "source" in evidence
            assert "sink" in evidence
            assert "path" in evidence
            assert "flow_shape" in evidence
            assert evidence["flow_shape"] in ("forward_bfs", "co_call")
            # source / sink must carry a name + file + line.
            assert evidence["source"]["name"]
            assert evidence["sink"]["name"]
            # claim must mention the rule_id and the flow direction
            # ("source -> sink") so an agent reading roam findings list
            # can act on the row without loading evidence_json.
            assert evidence["rule_id"] in row["claim"]
            assert "->" in row["claim"]
            # subject_id (the sink) must resolve to a real symbols row
            # when populated.
            if row["subject_id"] is not None:
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


def test_taint_findings_visible_via_cmd_findings_list(tmp_path):
    """``roam findings list --detector taint`` returns rows after migration."""
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_taint_persist(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "taint"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "taint" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "taint" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_taint_findings_visible_via_cmd_findings_count(tmp_path):
    """``roam findings count`` includes a non-zero entry for taint."""
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_taint_persist(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "taint")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_taint_no_findings_table_no_crash(tmp_path):
    """``roam taint --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index but
    before taint --persist runs. The normal taint text/JSON output must
    keep working — registry emit is purely additive.
    """
    proj = _taint_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["taint", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
