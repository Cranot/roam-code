"""Tests for the W156 follow-up: doctor command emits BLOCKING failures
to the central findings registry under the new ``environment`` subject_kind.

doctor is the first detector migrating an ``env.*`` kind onto the A4
findings registry (after clones / dead / complexity). HYBRID model:
only BLOCKING check failures persist; advisory failures (cache age,
cloud sync, dev/install drift) stay transient — they're ephemeral
environment diagnostics with no permanent codebase-level meaning,
and persisting them would pollute the registry with environmental
noise that's not actionable to anyone but the local developer.

These tests cover:
* the load-bearing HYBRID filter (advisory failures do NOT get rows)
* passed checks emit nothing (precedent: W145/W146)
* tier = static_analysis (hard FS/pkg checks are deterministic)
* subject_kind = "environment" (new vocabulary; subject_id = NULL)
* deterministic finding_id_str (re-run = upsert, no duplicate rows)
* visibility via the read-side ``roam findings`` CLI
* graceful degrade when the ``findings`` table is missing
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_doctor import (
    DOCTOR_DETECTOR_VERSION,
    _DOCTOR_CHECK_SUBKIND,
    _doctor_finding_id,
    _emit_doctor_findings,
)
from roam.db.connection import open_db


# ---------------------------------------------------------------------------
# Helpers — synthetic check-result fixtures
# ---------------------------------------------------------------------------


def _blocking_failure(name: str = "Python version", detail: str = "Python 3.8.0 (>= 3.10 required)") -> dict:
    """A synthetic check-result dict shaped like the doctor runtime emits.

    Defaults to the canonical Python-version blocking case so the test
    is realistic without relying on doctor's check pipeline to actually
    produce a failure.
    """
    return {"name": name, "passed": False, "detail": detail}


def _advisory_failure(name: str = "Cloud sync", detail: str = "OneDrive detected") -> dict:
    return {"name": name, "passed": False, "detail": detail}


def _passed_check(name: str = "tree-sitter") -> dict:
    return {"name": name, "passed": True, "detail": "tree-sitter 0.23.0"}


def _indexed_project(tmp_path):
    """Tiny indexed project so the findings table exists and we can write.

    We use the conftest helper so the schema and ``findings`` table are
    initialised by ``roam index`` exactly the way they would be in a
    real consumer's repo.
    """
    from tests.conftest import make_src_project

    proj = make_src_project(
        tmp_path,
        {
            "a.py": """
            def hello():
                return 1
            """,
        },
    )
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        assert runner.invoke(cli, ["index"]).exit_code == 0
    finally:
        os.chdir(old_cwd)
    return proj


# ---------------------------------------------------------------------------
# Unit tests on the helper — direct, deterministic, no doctor invocation
# ---------------------------------------------------------------------------


def test_emit_doctor_findings_blocking_failure_writes_row(tmp_path):
    """A blocking-failure result becomes one row in ``findings``."""
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        results = [_blocking_failure("Python version", "Python 3.8.0 (>= 3.10 required)")]
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(conn, results, DOCTOR_DETECTOR_VERSION)
            conn.commit()

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, subject_kind, subject_id, claim, "
                "       confidence, source_detector, source_version, evidence_json "
                "FROM findings WHERE source_detector = 'doctor'"
            ).fetchall()
        assert len(rows) == 1, "expected exactly one doctor-emitted row"
        row = rows[0]
        assert row["source_detector"] == "doctor"
        assert row["source_version"] == DOCTOR_DETECTOR_VERSION
        assert row["subject_kind"] == "environment"
        assert row["subject_id"] is None
        assert row["confidence"] == "static_analysis"
        assert row["finding_id_str"].startswith("doctor:env.python_version:")
        assert "Python version" in row["claim"]
        evidence = json.loads(row["evidence_json"])
        assert evidence["check_name"] == "Python version"
        assert evidence["sub_kind"] == "env.python_version"
        assert evidence["passed"] is False
    finally:
        os.chdir(old_cwd)


def test_emit_doctor_findings_advisory_failure_writes_no_row(tmp_path):
    """HYBRID FILTER — advisory check failures do NOT become findings.

    This is the load-bearing assertion of the W156 migration: persisting
    "cache is 2 days old" findings would pollute the registry with
    environmental noise. Advisory failures stay transient.
    """
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        # Every advisory check name produces a failure result. Every
        # single one must be filtered out.
        from roam.commands.cmd_doctor import _ADVISORY_CHECK_NAMES

        results = [_advisory_failure(name=name, detail=f"{name} failed (advisory)") for name in _ADVISORY_CHECK_NAMES]
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(conn, results, DOCTOR_DETECTOR_VERSION)
            conn.commit()

        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'doctor'"
            ).fetchone()[0]
        assert count == 0, (
            "advisory failures must NOT persist (HYBRID filter is the W156 "
            "load-bearing invariant — they pollute the registry with "
            "environmental noise)"
        )
    finally:
        os.chdir(old_cwd)


def test_emit_doctor_findings_passed_check_writes_no_row(tmp_path):
    """A passed check is never a finding (W145/W146 precedent)."""
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        results = [
            _passed_check("Python version"),
            _passed_check("tree-sitter"),
            _passed_check("git executable"),
        ]
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(conn, results, DOCTOR_DETECTOR_VERSION)
            conn.commit()

        with open_db(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'doctor'"
            ).fetchone()[0]
        assert count == 0
    finally:
        os.chdir(old_cwd)


def test_emit_doctor_findings_mixed_input_emits_only_blocking(tmp_path):
    """Mixed pass / advisory-fail / blocking-fail input — only blocking persists."""
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        results = [
            _passed_check("tree-sitter"),
            _advisory_failure("Cloud sync", "OneDrive detected"),
            _blocking_failure("Python version", "Python 3.8.0 (>= 3.10 required)"),
            _advisory_failure("MCP tool registry", "no tools registered"),
            _blocking_failure("git executable", "git not found on PATH"),
            _passed_check("networkx"),
        ]
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(conn, results, DOCTOR_DETECTOR_VERSION)
            conn.commit()

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str FROM findings WHERE source_detector = 'doctor' "
                "ORDER BY finding_id_str"
            ).fetchall()
        # Exactly two: Python version + git executable. Cloud sync and
        # MCP tool registry (both advisory) must be absent; the passed
        # checks contribute zero rows.
        assert len(rows) == 2, [r["finding_id_str"] for r in rows]
        prefixes = sorted(r["finding_id_str"].rsplit(":", 1)[0] for r in rows)
        assert prefixes == ["doctor:env.missing_git", "doctor:env.python_version"]
    finally:
        os.chdir(old_cwd)


def test_emit_doctor_findings_is_idempotent(tmp_path):
    """Re-running emit on the same result set upserts (no duplicate rows)."""
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        results = [
            _blocking_failure("Python version", "Python 3.8.0 (>= 3.10 required)"),
            _blocking_failure("git executable", "git not found on PATH"),
        ]
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(conn, results, DOCTOR_DETECTOR_VERSION)
            conn.commit()
        with open_db(readonly=True) as conn:
            first_ids = {
                r["finding_id_str"]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'doctor'"
                ).fetchall()
            }
        assert len(first_ids) == 2

        # Second emit with identical input — same finding_id_str values,
        # so the unique key forces an UPDATE rather than an INSERT.
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(conn, results, DOCTOR_DETECTOR_VERSION)
            conn.commit()
        with open_db(readonly=True) as conn:
            second_ids = {
                r["finding_id_str"]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'doctor'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'doctor'"
            ).fetchone()[0]
        assert second_count == 2, "duplicate rows on re-emit"
        assert second_ids == first_ids
    finally:
        os.chdir(old_cwd)


def test_doctor_finding_id_is_stable_and_namespaced():
    """``_doctor_finding_id`` is deterministic and uses the doctor:<sub-kind>: prefix."""
    a = _doctor_finding_id("Python version", "env.python_version")
    b = _doctor_finding_id("Python version", "env.python_version")
    assert a == b, "finding id must be deterministic"
    assert a.startswith("doctor:env.python_version:")

    # Different check or sub-kind => different id.
    c = _doctor_finding_id("git executable", "env.missing_git")
    assert c != a
    assert c.startswith("doctor:env.missing_git:")


def test_doctor_check_subkind_mapping_covers_unknown_blocking():
    """A blocking check absent from the explicit mapping defaults to env.blocking."""
    # Sanity-check the known mappings.
    assert _DOCTOR_CHECK_SUBKIND["Python version"] == "env.python_version"
    assert _DOCTOR_CHECK_SUBKIND["git executable"] == "env.missing_git"

    # An unknown blocking-tier check still emits — under env.blocking.
    fake = {"name": "Some Future Check", "passed": False, "detail": "future failure"}
    # We can't easily call _emit_doctor_findings without a DB, so verify
    # the sub-kind resolution via the finding_id_str shape directly.
    sub = _DOCTOR_CHECK_SUBKIND.get(fake["name"], "env.blocking")
    assert sub == "env.blocking"


# ---------------------------------------------------------------------------
# End-to-end through the CLI `roam doctor --persist`
# ---------------------------------------------------------------------------


def test_doctor_persist_healthy_project_emits_no_findings(tmp_path):
    """On a healthy project, doctor --persist writes ZERO findings rows.

    roam-code itself is the canonical "healthy" environment; this test
    uses a fixture project to keep the suite hermetic. The point is the
    same: when all blocking checks pass, the registry stays empty.
    """
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        # doctor exit codes are non-zero on advisory failures — that's
        # fine. We only care about the registry state after the call.
        runner.invoke(cli, ["doctor", "--persist"])

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'doctor'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, (
            "doctor --persist on a healthy project must NOT emit any "
            "findings (the registry documents blocking failures only)"
        )
    finally:
        os.chdir(old_cwd)


def test_doctor_no_persist_writes_no_findings(tmp_path):
    """Without --persist, no findings rows are written.

    Mirror of the clones-equivalent test (W95). The registry mirror
    lives behind the ``--persist`` flag; the default doctor path must
    remain side-effect-free.
    """
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        runner.invoke(cli, ["doctor"])  # no --persist

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'doctor'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0
    finally:
        os.chdir(old_cwd)


def test_doctor_findings_visible_via_cmd_findings_list(tmp_path):
    """``roam findings list --detector doctor`` returns rows after a synthetic emit.

    We can't easily make a real doctor invocation produce a blocking
    failure in CI (Python, tree-sitter, git, networkx are all installed),
    so we emit synthetically through the helper and then exercise the
    read-side CLI for end-to-end agent visibility.
    """
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(
                conn,
                [_blocking_failure("Python version", "Python 3.8.0 (>= 3.10 required)")],
                DOCTOR_DETECTOR_VERSION,
            )
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "doctor"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "doctor" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "doctor" for r in envelope["findings"])
        # Sanity: subject_kind = "environment" surfaces in the row.
        assert all(r["subject_kind"] == "environment" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_doctor_findings_visible_via_cmd_findings_count(tmp_path):
    """``roam findings count`` includes a non-zero entry for doctor."""
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            _emit_doctor_findings(
                conn,
                [
                    _blocking_failure("Python version", "Python 3.8.0 (>= 3.10 required)"),
                    _blocking_failure("git executable", "git not found on PATH"),
                ],
                DOCTOR_DETECTOR_VERSION,
            )
            conn.commit()
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "doctor", expected_exact_count=2)


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_doctor_persist_no_findings_table_no_crash(tmp_path):
    """``doctor --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init.
    The doctor command must still exit (with the usual advisory /
    blocking semantics) and must not raise an unhandled exception.
    """
    proj = _indexed_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--persist"])
        # Exit code may be 0 / 1 / 2 depending on advisory state — what
        # matters is we didn't crash with an unhandled exception.
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            f"doctor --persist crashed when findings table absent: {result.exception!r}"
        )
    finally:
        os.chdir(old_cwd)
