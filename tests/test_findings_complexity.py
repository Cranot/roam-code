"""Tests for the W93 follow-up: complexity detector emits to the central
findings registry.

The complexity detector is the third detector migrating onto the A4
findings registry (after ``clones`` in W95 and ``dead`` in W99). It
continues to read from ``symbol_metrics`` (the authoritative per-symbol
metrics table populated at index time) and ALSO emits one row per
HIGH/CRITICAL hotspot into ``findings``. These tests cover that additive
emit and the end-to-end visibility through ``roam findings`` for an agent.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_complexity import (
    COMPLEXITY_DETECTOR_VERSION,
    COMPLEXITY_FINDING_THRESHOLD,
    _complexity_finding_id,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _hotspot_project(tmp_path):
    """Tiny repo with one function whose cognitive complexity > 15.

    The function below is structured to push cognitive complexity well
    past the registry's emit threshold (15.0): nested conditionals,
    boolean operator chains, multiple loops, multiple returns. Keep the
    body intentionally messy — we want the detector to flag it.
    """
    return _make_project(
        tmp_path,
        {
            "complex.py": """
            def messy_dispatcher(records, mode, deep, flags, log):
                results = []
                for r in records:
                    if mode == "a" and deep:
                        if r.value > 0 and flags.get("ok"):
                            for sub in r.children or []:
                                if sub.kind == "x" or sub.kind == "y":
                                    if sub.ready and not sub.skipped:
                                        if log and r.tag != "z":
                                            results.append(sub)
                                        else:
                                            results.append(None)
                                    elif sub.ready and sub.skipped:
                                        return []
                                else:
                                    continue
                        else:
                            if flags.get("emergency"):
                                return None
                            results.append(r)
                    elif mode == "b" or mode == "c":
                        if not r.value:
                            continue
                        for i in range(r.value):
                            if i % 2 == 0 and r.tag:
                                if r.tag.startswith("p"):
                                    results.append(i)
                                elif r.tag.startswith("q"):
                                    if log:
                                        results.append(-i)
                                    else:
                                        results.append(None)
                            elif i % 3 == 0:
                                results.append(i * 2)
                    else:
                        results.append(r)
                if not results:
                    return None
                if mode == "a":
                    return results
                return list(reversed(results))

            def simple_one(x):
                return x + 1
        """,
        },
    )


def _persist_complexity(proj):
    """Index the project and run ``complexity --persist``.

    Returns the CliRunner result so tests can assert on its exit code if
    they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["complexity", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_complexity_emits_to_findings_registry(tmp_path):
    """Running complexity --persist on a high-complexity fixture populates findings."""
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'complexity'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one complexity-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "complexity"
            assert r["source_version"] == COMPLEXITY_DETECTOR_VERSION
            assert r["subject_kind"] == "symbol"
            # Complexity is a deterministic AST measurement — confidence tier
            # is always ``structural`` for this detector.
            assert r["confidence"] == "structural"
            assert r["finding_id_str"].startswith("complexity:hotspot:")
    finally:
        os.chdir(old_cwd)


def test_complexity_finding_id_is_deterministic():
    """_complexity_finding_id returns the same id for the same (symbol_id, score)."""
    a = _complexity_finding_id(42, 27.0)
    b = _complexity_finding_id(42, 27.0)
    assert a == b
    assert a.startswith("complexity:hotspot:")
    # Different symbol -> different id.
    assert _complexity_finding_id(43, 27.0) != a
    # Different (rounded) score -> different id.
    assert _complexity_finding_id(42, 28.0) != a
    # Same rounded score -> same id (parser jitter tolerance).
    assert _complexity_finding_id(42, 27.4) == _complexity_finding_id(42, 27.0)


def test_complexity_rerun_upserts_not_duplicates(tmp_path):
    """Re-running complexity --persist produces the same finding_id_str set."""
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'complexity'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'complexity'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same code, same score → same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["complexity", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'complexity'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'complexity'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_complexity_finding_evidence_carries_metric_factors(tmp_path):
    """The finding's evidence JSON carries the cognitive_complexity score + factors."""
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, subject_id, claim FROM findings "
                "WHERE source_detector = 'complexity' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        # The score must be present and at/above the emit threshold.
        assert "cognitive_complexity" in evidence
        assert evidence["cognitive_complexity"] >= COMPLEXITY_FINDING_THRESHOLD
        # Severity label must be one of the structured values.
        assert evidence["severity"] in {"HIGH", "CRITICAL"}
        # Per-factor breakdown should be retrievable for follow-up triage.
        for k in (
            "nesting_depth",
            "param_count",
            "bool_op_count",
            "callback_depth",
            "halstead_volume",
        ):
            assert k in evidence, f"evidence missing factor {k}"
        # The claim must name the symbol and the score in human form.
        assert "cognitive complexity" in (row["claim"] or "").lower()
    finally:
        os.chdir(old_cwd)


def test_complexity_finding_subject_links_to_symbols_row(tmp_path):
    """subject_id resolves to a real symbols row."""
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id FROM findings "
                "WHERE source_detector = 'complexity' AND subject_id IS NOT NULL"
            ).fetchall()
            assert len(rows) >= 1, (
                "expected at least one complexity finding with a resolved subject_id"
            )
            for r in rows:
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?", (r["subject_id"],)
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
    finally:
        os.chdir(old_cwd)


def test_complexity_below_threshold_not_flagged(tmp_path):
    """Low-complexity symbols don't pollute the registry.

    The ``simple_one`` function in the fixture is a one-liner. Its
    cognitive_complexity is 0 — well below the 15.0 emit threshold. No
    finding row should exist whose claim references it.
    """
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT claim FROM findings WHERE source_detector = 'complexity'"
            ).fetchall()
            for r in rows:
                claim = (r["claim"] or "").lower()
                assert "simple_one" not in claim, (
                    f"low-complexity symbol leaked into registry: {r['claim']!r}"
                )
            # And every emitted finding must be at/above the threshold.
            scores = conn.execute(
                "SELECT evidence_json FROM findings WHERE source_detector = 'complexity'"
            ).fetchall()
            for r in scores:
                ev = json.loads(r["evidence_json"])
                assert ev["cognitive_complexity"] >= COMPLEXITY_FINDING_THRESHOLD
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_complexity_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector complexity` returns rows after migration."""
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "complexity"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "complexity" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "complexity" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_complexity_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for complexity."""
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_complexity(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "complexity")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam complexity`` without the flag must not write to ``findings``.
    """
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["complexity"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'complexity'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist complexity still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_complexity_persist_no_findings_table_no_crash(tmp_path):
    """``complexity --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard symbol_metrics read path (which
    legacy consumers depend on) must keep working — the command exits 0
    and writes no registry rows.
    """
    proj = _hotspot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["complexity", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output

        # And the underlying symbol_metrics read path must still produce data.
        with open_db(readonly=True) as conn:
            metric_count = conn.execute(
                "SELECT COUNT(*) FROM symbol_metrics"
            ).fetchone()[0]
        assert metric_count >= 1, "symbol_metrics read path broke when findings table absent"
    finally:
        os.chdir(old_cwd)
