"""Tests for the W146 follow-up: audit-trail-verify detector emits to
the central findings registry.

The chain verifier is the next detector migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, ``complexity`` in
W102, ``smells`` in W109, and the W110-W145 emitters). It continues to
walk the JSONL audit trail and emit issues to the caller, and ALSO
mirrors one row per chain anomaly into ``findings`` when invoked with
``--persist``.

Boundary note (cross-link with W145): ``audit-trail-conformance-check``
operates at a whole-trail granularity (a 6-check rollup) and uses a
different ``subject_kind`` by design. ``audit-trail-verify`` operates at
a per-entry granularity (one row per anomalous JSONL line). Both
detectors are queryable through ``roam findings list`` but neither
shares a subject_kind because they answer different questions.

The fixtures exercise the two issue kinds that ``_verify_chain``
emits today:

* ``previous_record_hash mismatch`` — produced by tampering with a
  record in the middle of the chain. Confidence tier: ``static_analysis``
  (deterministic SHA-256 comparison).
* ``invalid JSON`` — produced by appending a malformed line to the
  trail. Confidence tier: ``static_analysis`` (deterministic parse).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_audit_trail_verify import (
    AUDIT_TRAIL_VERIFY_DETECTOR_VERSION,
    _audit_trail_verify_finding_id,
    _emit_audit_trail_verify_findings,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _base_record(verdict: str, ts: str) -> dict:
    """Article-12-shaped record stub (mirrors test_audit_trail_verify.py)."""
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "tool": "roam-code",
        "tool_version": "12.26",
        "actor": "test@example.com",
        "verdict": verdict,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
    }


def _write_chain(path: Path, records: list[dict]) -> None:
    """Write records as JSONL with proper SHA-256 chain linking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)
            rec["previous_record_hash"] = prev_hash
            line = json.dumps(rec, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()


def _tampered_project(tmp_path):
    """Indexed project with a tampered audit trail under .roam/.

    The verifier persists into the project's ``.roam/index.db`` findings
    registry, so the fixture sets up both: a minimal indexable project
    AND a tampered audit trail at ``.roam/audit-trail.jsonl``.

    Tampering: write a 3-record chain, then mutate line 2's verdict
    field. Line 3's ``previous_record_hash`` then doesn't match the
    recomputed hash of (mutated) line 2 → one mismatch issue.
    """
    proj = _make_project(
        tmp_path,
        {
            "main.py": "def add(a, b):\n    return a + b\n",
        },
    )
    trail = proj / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
            _base_record("BLOCK", "2026-05-05T00:02:00Z"),
        ],
    )
    # Tamper with line 2.
    lines = trail.read_text(encoding="utf-8").splitlines()
    rec2 = json.loads(lines[1])
    rec2["verdict"] = "TAMPERED"
    lines[1] = json.dumps(rec2, separators=(",", ":"), sort_keys=True)
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return proj, trail


def _invalid_json_project(tmp_path):
    """Indexed project with an audit trail containing a malformed line."""
    proj = _make_project(
        tmp_path,
        {
            "main.py": "def add(a, b):\n    return a + b\n",
        },
    )
    trail = proj / ".roam" / "audit-trail.jsonl"
    _write_chain(trail, [_base_record("SAFE", "2026-05-05T00:00:00Z")])
    with trail.open("a", encoding="utf-8") as f:
        f.write("{this is not json}\n")
    return proj, trail


def _persist_verify(proj, trail):
    """Index the project and run ``audit-trail-verify --persist --input <trail>``.

    The verifier ignores the index DB content (it walks the JSONL); the
    index step exists only so ``.roam/index.db`` is materialised with
    the findings table that --persist writes into.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(
        cli, ["audit-trail-verify", "--input", str(trail), "--persist"]
    )
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_audit_trail_verify_emits_to_findings_registry(tmp_path):
    """``audit-trail-verify --persist`` on a tampered trail populates findings."""
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_verify(proj, trail)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, subject_id, confidence "
                "FROM findings WHERE source_detector = 'audit-trail-verify'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one verify-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "audit-trail-verify"
            assert r["source_version"] == AUDIT_TRAIL_VERIFY_DETECTOR_VERSION
            # Chain anomalies are per-JSONL-entry — subject_kind is
            # ``ledger_entry`` (not ``symbol``) and subject_id stays NULL.
            assert r["subject_kind"] == "ledger_entry"
            assert r["subject_id"] is None
            # Both current issue kinds are deterministic checks.
            assert r["confidence"] == "static_analysis"
            assert r["finding_id_str"].startswith("audit-trail-verify:")
    finally:
        os.chdir(old_cwd)


def test_audit_trail_verify_finding_id_is_deterministic():
    """``_audit_trail_verify_finding_id`` is stable across calls."""
    fid_a = _audit_trail_verify_finding_id(
        ".roam/audit-trail.jsonl", 3, "previous_record_hash mismatch"
    )
    fid_b = _audit_trail_verify_finding_id(
        ".roam/audit-trail.jsonl", 3, "previous_record_hash mismatch"
    )
    assert fid_a == fid_b
    assert fid_a.startswith("audit-trail-verify:hash_mismatch:")
    # Different line → different id.
    fid_c = _audit_trail_verify_finding_id(
        ".roam/audit-trail.jsonl", 4, "previous_record_hash mismatch"
    )
    assert fid_c != fid_a
    # Different issue kind → different slug, different id.
    fid_d = _audit_trail_verify_finding_id(
        ".roam/audit-trail.jsonl", 3, "invalid JSON"
    )
    assert fid_d != fid_a
    assert fid_d.startswith("audit-trail-verify:invalid_json:")
    # Different path → different id (per-run trails don't collide with
    # the canonical .roam/audit-trail.jsonl rows).
    fid_e = _audit_trail_verify_finding_id(
        ".roam/runs/abc/audit-trail.jsonl", 3, "previous_record_hash mismatch"
    )
    assert fid_e != fid_a


def test_audit_trail_verify_rerun_upserts_not_duplicates(tmp_path):
    """Re-running ``audit-trail-verify --persist`` produces the same id set."""
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_verify(proj, trail)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'audit-trail-verify'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE source_detector = 'audit-trail-verify'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same tampered trail → same ids.
        runner = CliRunner()
        result = runner.invoke(
            cli, ["audit-trail-verify", "--input", str(trail), "--persist"]
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'audit-trail-verify'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE source_detector = 'audit-trail-verify'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_audit_trail_verify_finding_evidence_carries_chain_fields(tmp_path):
    """The finding's evidence JSON carries the per-anomaly context."""
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_verify(proj, trail)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'audit-trail-verify' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in (
            "audit_trail_path",
            "line",
            "issue",
            "expected_prev",
            "computed_prev",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the line number and the issue kind.
        assert str(evidence["line"]) in (row["claim"] or "")
        assert evidence["issue"] in (row["claim"] or "")
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-issue-kind confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    We exercise ``_emit_audit_trail_verify_findings`` directly on
    synthetic issue dicts so the per-kind tier mapping is verified
    independently of which anomalies a particular trail tampering
    produces.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_audit_trail_verify_hash_mismatch_is_static_analysis(tmp_path):
    """previous_record_hash mismatch lands at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        issues = [
            {
                "line": 3,
                "issue": "previous_record_hash mismatch",
                "expected_prev": "abc",
                "computed_prev": "def",
                "timestamp": "2026-05-05T00:00:00Z",
                "verdict": "REVIEW",
            }
        ]
        written = _emit_audit_trail_verify_findings(
            conn, issues, ".roam/audit-trail.jsonl",
            AUDIT_TRAIL_VERIFY_DETECTOR_VERSION,
        )
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings "
            "WHERE source_detector = 'audit-trail-verify'"
        ).fetchone()
        assert row["confidence"] == "static_analysis"


def test_audit_trail_verify_invalid_json_is_static_analysis(tmp_path):
    """invalid JSON anomaly lands at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        issues = [
            {
                "line": 5,
                "issue": "invalid JSON",
                "detail": "Expecting property name enclosed in double quotes",
            }
        ]
        written = _emit_audit_trail_verify_findings(
            conn, issues, ".roam/audit-trail.jsonl",
            AUDIT_TRAIL_VERIFY_DETECTOR_VERSION,
        )
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings "
            "WHERE source_detector = 'audit-trail-verify'"
        ).fetchone()
        assert row["confidence"] == "static_analysis"


def test_audit_trail_verify_skips_not_found_synthetic_issue(tmp_path):
    """The 'audit trail not found' state issue is NOT emitted as a finding.

    Missing trail is a state flag (the verdict already reports
    ``state: "uninitialized"``), not a per-entry tamper. The helper
    must filter it out so consumers don't see false findings for a
    repo that simply hasn't bootstrapped an audit trail yet.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        issues = [
            {"line": 0, "issue": "audit trail not found: /missing"},
            {
                "line": 3,
                "issue": "previous_record_hash mismatch",
                "expected_prev": "abc",
                "computed_prev": "def",
            },
        ]
        written = _emit_audit_trail_verify_findings(
            conn, issues, ".roam/audit-trail.jsonl",
            AUDIT_TRAIL_VERIFY_DETECTOR_VERSION,
        )
        assert written == 1, "expected exactly one row (the real anomaly)"
        rows = conn.execute(
            "SELECT claim FROM findings "
            "WHERE source_detector = 'audit-trail-verify'"
        ).fetchall()
        assert all("not found" not in r["claim"] for r in rows)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_audit_trail_verify_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector audit-trail-verify` returns rows."""
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_verify(proj, trail)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list",
                  "--detector", "audit-trail-verify"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "audit-trail-verify" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "audit-trail-verify"
            for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_audit_trail_verify_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for audit-trail-verify."""
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_verify(proj, trail)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "audit-trail-verify")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_audit_trail_verify_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard verify path stays side-effect-free."""
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(
            cli, ["audit-trail-verify", "--input", str(trail)]
        ).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings "
                    "WHERE source_detector = 'audit-trail-verify'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist verify still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_audit_trail_verify_persist_no_findings_table_no_crash(tmp_path):
    """``audit-trail-verify --persist`` degrades cleanly when findings is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard verify output path (text /
    JSON) that legacy consumers depend on must keep working — the
    command exits 0 and writes no registry rows.
    """
    proj, trail = _tampered_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(
            cli, ["audit-trail-verify", "--input", str(trail), "--persist"]
        )
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


def test_audit_trail_verify_valid_chain_writes_no_findings(tmp_path):
    """A clean chain produces zero registry rows under --persist.

    The verifier is per-anomaly; a fully-valid chain has no anomalies,
    so the findings table should stay empty for this detector even
    when --persist is requested. Distinguishes "no chain to verify"
    (uninitialized) from "chain has zero issues" (valid).
    """
    proj = _make_project(
        tmp_path,
        {"main.py": "def add(a, b):\n    return a + b\n"},
    )
    trail = proj / ".roam" / "audit-trail.jsonl"
    _write_chain(
        trail,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli, ["audit-trail-verify", "--input", str(trail), "--persist"]
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE source_detector = 'audit-trail-verify'"
            ).fetchone()[0]
        assert count == 0, "clean chain wrote findings rows"
    finally:
        os.chdir(old_cwd)


def test_audit_trail_verify_invalid_json_in_trail_emits_finding(tmp_path):
    """A malformed JSONL line surfaces as one ``invalid_json`` finding."""
    proj, trail = _invalid_json_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_verify(proj, trail)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, evidence_json, confidence FROM findings "
                "WHERE source_detector = 'audit-trail-verify'"
            ).fetchall()
        # At least one invalid_json finding present.
        invalid_json_rows = [
            r for r in rows if "invalid_json" in r["finding_id_str"]
        ]
        assert len(invalid_json_rows) >= 1, (
            f"expected an invalid_json finding; got {[r['finding_id_str'] for r in rows]}"
        )
        for r in invalid_json_rows:
            assert r["confidence"] == "static_analysis"
            evidence = json.loads(r["evidence_json"])
            assert evidence["issue"] == "invalid JSON"
    finally:
        os.chdir(old_cwd)
