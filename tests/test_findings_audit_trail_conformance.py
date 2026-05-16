"""Tests for the W145 follow-up: audit-trail-conformance detector emits
to the central findings registry.

The audit-trail-conformance detector is one of the detectors migrating
onto the A4 findings registry (after ``clones`` in W95, ``dead`` in
W99, ``complexity`` in W102, ``smells`` in W109, ``orphan-imports`` in
W132, and others). It continues to return its in-memory 6-check
verdict shape to the caller and ALSO emits one row per FAILED check
into ``findings`` when invoked with ``--persist``. These tests cover
that additive emit and the end-to-end visibility through
``roam findings`` for an agent.

The fixtures lean on the canonical 6 Article 12 checks:

* ``chain_integrity`` (HMAC/SHA-256 chain verification) — ``static_analysis``.
* ``timestamp_completeness`` (every record parses as ISO-8601) — ``static_analysis``.
* ``actor_attribution`` (every record has a non-empty actor) — ``static_analysis``.
* ``reproducibility_metadata`` (diff_sha256 + git_sha + tool_version) — ``static_analysis``.
* ``verdict_and_rationale`` (verdict set + rationale_summary non-empty) — ``static_analysis``.
* ``retention`` (oldest record older than threshold) — ``heuristic``.

Passed checks aren't findings — only failures are persisted. This
mirrors the SARIF emit (which only surfaces failures as results) and
follows the W118/W131 precedent of "don't fabricate".
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_audit_trail_conformance import (
    _AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE,
    AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
    _audit_trail_conformance_finding_id,
    _emit_audit_trail_conformance_findings,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_chain(path, records):
    """Write a JSONL audit trail with a valid SHA-256 hash chain.

    Mirrors ``tests/test_audit_trail_conformance._write_chain`` — keep
    the bodies aligned so per-test fixture drift is loud.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)
            rec["previous_record_hash"] = prev_hash
            line = json.dumps(rec, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()


def _full_record(verdict, ts, *, actor="alice@x"):
    """Article 12-shaped record (mirrors ``_full_record`` in the
    conformance-check test suite).
    """
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "tool": "roam-code",
        "tool_version": "12.26",
        "actor": actor,
        "repo": "github.com/o/r",
        "git_sha": "abc123def456",
        "diff_sha256": "deadbeef" * 8,
        "verdict": verdict,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
        "high_severity_critique": 0,
        "intent_marker": None,
        "rationale_summary": f"Verdict: **{verdict}**. Sample rationale text.",
    }


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _old_iso(days):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _failing_project(tmp_path):
    """Tiny project + audit trail that fails several conformance checks.

    The trail contains a single record with:

    * actor=``<unknown>`` (fails actor_attribution — static_analysis kind).
    * Recent-only timestamp (fails retention — heuristic kind).

    Other 4 checks pass, so the persist branch emits exactly 2 rows.
    The project has one Python source file so ``roam index`` succeeds —
    the conformance check itself doesn't read the index, but
    ``--persist`` calls ``ensure_index()`` to guarantee the findings
    table exists.
    """
    proj = _make_project(
        tmp_path,
        {"app.py": "def main():\n    return 1\n"},
    )
    trail = proj / ".roam" / "audit-trail.jsonl"
    _write_chain(trail, [_full_record("SAFE", _now_iso(), actor="<unknown>")])
    return proj, trail


def _multi_failure_project(tmp_path):
    """Project + audit trail that fails 3+ checks across both confidence tiers.

    * actor=``<unknown>`` → actor_attribution FAIL (static_analysis).
    * recent-only timestamps → retention FAIL (heuristic).
    * record missing rationale_summary → verdict_and_rationale FAIL
      (static_analysis).
    """
    proj = _make_project(
        tmp_path,
        {"app.py": "def main():\n    return 1\n"},
    )
    rec = _full_record("SAFE", _now_iso(), actor="<unknown>")
    rec["rationale_summary"] = ""
    trail = proj / ".roam" / "audit-trail.jsonl"
    _write_chain(trail, [rec])
    return proj, trail


def _conformant_project(tmp_path):
    """Project + audit trail that passes all 6 checks (score 100/100).

    The persist branch should emit ZERO finding rows — passed checks
    aren't findings.
    """
    proj = _make_project(
        tmp_path,
        {"app.py": "def main():\n    return 1\n"},
    )
    trail = proj / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            _full_record("SAFE", _old_iso(200)),
            _full_record("REVIEW", _now_iso()),
        ],
    )
    return proj, trail


def _persist_conformance(trail):
    """Run ``audit-trail-conformance-check --persist`` on a given trail.

    Returns the CliRunner result so tests can assert on the exit code
    or output if they care.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(
        cli,
        [
            "audit-trail-conformance-check",
            "--input",
            str(trail),
            "--persist",
        ],
    )
    # The CLI's gate behaviour is independent of --persist; without
    # --gate the exit code stays 0 even when the score is < 100.
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_emits_to_findings_registry(tmp_path):
    """Running --persist on a failing trail populates the findings table."""
    proj, trail = _failing_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conformance(trail)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, subject_id, confidence "
                "FROM findings WHERE source_detector = 'audit-trail-conformance'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one audit-trail-conformance finding row"
        kinds_seen = set()
        for r in rows:
            assert r["source_detector"] == "audit-trail-conformance"
            assert r["source_version"] == AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION
            # subject_kind="file"; subject_id=None because the audit
            # trail lives under .roam/ which is gitignored and never
            # in the indexed files table.
            assert r["subject_kind"] == "file"
            assert r["subject_id"] is None
            assert r["confidence"] in (
                "static_analysis",
                "heuristic",
            )
            assert r["finding_id_str"].startswith("audit-trail-conformance:")
            # Extract the check_id token from the finding id.
            kinds_seen.add(r["finding_id_str"].split(":")[1])
        # The failing fixture trips actor_attribution + retention.
        assert "actor_attribution" in kinds_seen
        assert "retention" in kinds_seen
    finally:
        os.chdir(old_cwd)


def test_audit_trail_conformance_finding_id_is_deterministic():
    """_audit_trail_conformance_finding_id returns the same id for the same input."""
    a = _audit_trail_conformance_finding_id("chain_integrity", ".roam/audit-trail.jsonl")
    b = _audit_trail_conformance_finding_id("chain_integrity", ".roam/audit-trail.jsonl")
    assert a == b
    assert a.startswith("audit-trail-conformance:chain_integrity:")
    # Different check_id → different id.
    assert _audit_trail_conformance_finding_id("retention", ".roam/audit-trail.jsonl") != a
    # Different audit_trail_path → different id (so two repos with
    # both failing the same check get distinct registry rows).
    assert _audit_trail_conformance_finding_id("chain_integrity", ".roam/other-trail.jsonl") != a


def test_audit_trail_conformance_rerun_upserts_not_duplicates(tmp_path):
    """Re-running --persist on the same fixture produces the same id set."""
    proj, trail = _failing_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conformance(trail)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'audit-trail-conformance'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'audit-trail-conformance'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same predicates → same ids.
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "audit-trail-conformance-check",
                "--input",
                str(trail),
                "--persist",
            ],
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'audit-trail-conformance'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'audit-trail-conformance'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_audit_trail_conformance_finding_evidence_carries_per_check_fields(tmp_path):
    """The finding's evidence JSON carries the per-check failure context."""
    proj, trail = _failing_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conformance(trail)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'audit-trail-conformance' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in (
            "check_id",
            "audit_trail_path",
            "message",
            "retention_days_required",
            "schema_reference",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the check_id.
        assert evidence["check_id"] in (row["claim"] or "")
        # The claim must reference the trail path so a consumer can
        # locate the failing artefact without re-deriving it.
        assert evidence["audit_trail_path"] in (row["claim"] or "")
        # Article 12 reference is the canonical schema anchor.
        assert "Article 12" in evidence["schema_reference"]
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-kind confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The detector + indexer aren't needed here — we exercise
    ``_emit_audit_trail_conformance_findings`` directly on synthetic
    check dicts so the per-kind tier mapping is verified independently
    of which Article 12 checks the live conformance command happens
    to trip on a given trail.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_audit_trail_conformance_kind_tier_mapping_static_analysis(tmp_path):
    """The 5 schema/crypto checks land at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        checks = [
            {"id": "chain_integrity", "passed": False, "message": "chain has 2 issues"},
            {
                "id": "timestamp_completeness",
                "passed": False,
                "message": "1 record lacks a parseable timestamp",
            },
            {
                "id": "actor_attribution",
                "passed": False,
                "message": "1 record lacks an actor",
            },
            {
                "id": "reproducibility_metadata",
                "passed": False,
                "message": "1 record lacks full reproducibility metadata",
            },
            {
                "id": "verdict_and_rationale",
                "passed": False,
                "message": "1 record missing rationale_summary",
            },
        ]
        written = _emit_audit_trail_conformance_findings(
            conn,
            checks,
            ".roam/audit-trail.jsonl",
            180,
            AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
        )
        assert written == len(checks)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings WHERE source_detector = 'audit-trail-conformance'"
        ).fetchall()
        assert len(rows) == len(checks)
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert r["confidence"] == "static_analysis", (
                f"check_id {ev['check_id']!r} expected static_analysis, got {r['confidence']!r}"
            )


def test_audit_trail_conformance_kind_tier_mapping_heuristic(tmp_path):
    """The retention check lands at heuristic confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        checks = [
            {
                "id": "retention",
                "passed": False,
                "message": "oldest record is only 5 days old; minimum 180",
            },
        ]
        written = _emit_audit_trail_conformance_findings(
            conn,
            checks,
            ".roam/audit-trail.jsonl",
            180,
            AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
        )
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings WHERE source_detector = 'audit-trail-conformance'"
        ).fetchone()
        assert row["confidence"] == "heuristic"


def test_audit_trail_conformance_kind_tier_fallback_is_heuristic(tmp_path):
    """An unknown future check_id falls back to ``heuristic``.

    Drift guard — if someone adds a 7th check to the Article 12 scorer
    without updating ``_AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE``,
    the emit helper still classifies it conservatively rather than
    over-claiming ``static_analysis``.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        written = _emit_audit_trail_conformance_findings(
            conn,
            [
                {
                    "id": "speculative_future_check",
                    "passed": False,
                    "message": "future check failed",
                }
            ],
            ".roam/audit-trail.jsonl",
            180,
            AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
        )
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings WHERE source_detector = 'audit-trail-conformance'"
        ).fetchone()
        assert row["confidence"] == "heuristic"


def test_audit_trail_conformance_kind_mapping_covers_all_six_checks():
    """The per-kind tier table covers every check id the scorer emits.

    Drift guard: if a new check is added to the scorer's ``checks``
    list (e.g., a 7th Article 12 requirement) without a matching entry
    in ``_AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE``, the emit helper
    falls back to the default ``heuristic`` tier silently. Surface the
    omission loudly so the tier choice is intentional.
    """
    expected_kinds = {
        "chain_integrity",
        "timestamp_completeness",
        "actor_attribution",
        "reproducibility_metadata",
        "verdict_and_rationale",
        "retention",
    }
    mapped_kinds = set(_AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE.keys())
    missing = expected_kinds - mapped_kinds
    assert not missing, (
        f"checks emitted by the scorer but missing from _AUDIT_TRAIL_CONFORMANCE_KIND_TO_CONFIDENCE: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# "Only failures become findings" — the no-fabrication rule
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_passing_checks_do_not_emit_findings(tmp_path):
    """Passed checks are not findings — they're the absence of one.

    A conformant trail (100/100) should produce ZERO finding rows even
    when --persist is set. Mirrors the SARIF emit, which surfaces
    results only for failures (W118/W131 precedent).
    """
    proj, trail = _conformant_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conformance(trail)

        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'audit-trail-conformance'"
            ).fetchone()[0]
        assert count == 0, f"expected 0 findings on a conformant trail, got {count}"
    finally:
        os.chdir(old_cwd)


def test_audit_trail_conformance_emit_helper_skips_passed_checks(tmp_path):
    """The emit helper itself skips ``passed=True`` rows."""
    with _seed_for_emit_helper(tmp_path) as conn:
        checks = [
            {"id": "chain_integrity", "passed": True, "message": "verified"},
            {
                "id": "retention",
                "passed": False,
                "message": "oldest 5 days, minimum 180",
            },
            {
                "id": "actor_attribution",
                "passed": True,
                "message": "all records attributed",
            },
        ]
        written = _emit_audit_trail_conformance_findings(
            conn,
            checks,
            ".roam/audit-trail.jsonl",
            180,
            AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
        )
        assert written == 1, "only the failing retention check should be emitted"
        rows = conn.execute(
            "SELECT finding_id_str FROM findings WHERE source_detector = 'audit-trail-conformance'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0].startswith("audit-trail-conformance:retention:")


def test_audit_trail_conformance_emit_helper_skips_not_run_state(tmp_path):
    """The emit helper skips ``state='not_run'`` rows (no-trail branch).

    The conformance command emits all 6 checks marked
    ``state='not_run'`` when the trail is absent. Persisting those
    would fabricate per-check failure rows for a check that never
    executed — exactly the W118/W131 anti-pattern.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        checks = [
            {
                "id": "chain_integrity",
                "passed": False,
                "state": "not_run",
                "message": "no audit trail to check",
            },
            {
                "id": "retention",
                "passed": False,
                "state": "not_run",
                "message": "no audit trail to check",
            },
        ]
        written = _emit_audit_trail_conformance_findings(
            conn,
            checks,
            ".roam/audit-trail.jsonl",
            180,
            AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION,
        )
        assert written == 0, "not_run checks must not become findings"
        rows = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE source_detector = 'audit-trail-conformance'"
        ).fetchone()
        assert rows[0] == 0


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector audit-trail-conformance` returns rows."""
    proj, trail = _multi_failure_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conformance(trail)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "findings",
                "list",
                "--detector",
                "audit-trail-conformance",
            ],
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "audit-trail-conformance" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "audit-trail-conformance" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_audit_trail_conformance_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for the detector."""
    proj, trail = _multi_failure_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conformance(trail)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "audit-trail-conformance")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam audit-trail-conformance-check`` without the flag must not
    write to ``findings``.
    """
    proj, trail = _failing_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        result = runner.invoke(
            cli,
            [
                "audit-trail-conformance-check",
                "--input",
                str(trail),
            ],
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'audit-trail-conformance'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist audit-trail-conformance still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_audit_trail_conformance_persist_no_findings_table_no_crash(tmp_path):
    """``--persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init
    but before the persist call. The standard detector-output path
    (text / JSON / SARIF) which legacy consumers depend on must keep
    working — the command exits 0 and writes no registry rows.
    """
    proj, trail = _failing_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(
            cli,
            [
                "audit-trail-conformance-check",
                "--input",
                str(trail),
                "--persist",
            ],
        )
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


def test_audit_trail_conformance_persist_no_trail_no_crash(tmp_path):
    """``--persist`` on an absent trail emits zero findings and exits 0.

    The no-trail branch returns early before the checks list is built;
    the persist path must not fabricate per-check failure rows for
    checks that never executed.
    """
    proj = _make_project(
        tmp_path,
        {"app.py": "def main():\n    return 1\n"},
    )
    missing_trail = proj / ".roam" / "nope.jsonl"
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        result = runner.invoke(
            cli,
            [
                "audit-trail-conformance-check",
                "--input",
                str(missing_trail),
                "--persist",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "no audit trail to check" in result.output

        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'audit-trail-conformance'"
            ).fetchone()[0]
        assert count == 0, "absent-trail persist branch must not fabricate findings"
    finally:
        os.chdir(old_cwd)
