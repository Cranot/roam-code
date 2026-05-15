"""Tests for the W90 / ROADMAP A4 findings registry substrate.

The findings table is the denormalised cross-detector surface. Every
detector continues to write to its detector-specific table AND emits a
row here. These tests cover the substrate only — per-detector emit-site
migration is deferred to follow-up waves.
"""

from __future__ import annotations

import json

import pytest

from roam.db.connection import USER_VERSION, open_db
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_STRUCTURAL,
    FindingRecord,
    count_by_detector,
    emit_finding,
    get_finding,
    list_findings,
    supersede_finding,
)


def _fresh_conn(tmp_path):
    """Create a fresh roam DB rooted under tmp_path and return a connection."""
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_findings_table_exists_on_fresh_db(tmp_path):
    """SCHEMA_SQL creates the table on first open."""
    with _fresh_conn(tmp_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings'"
        ).fetchone()
        assert row is not None, "findings table was not created on fresh DB"


def test_findings_indexes_exist(tmp_path):
    """The three supporting indexes are created so list_findings hits indexed scans."""
    with _fresh_conn(tmp_path) as conn:
        index_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='findings' AND name LIKE 'idx_findings_%'"
        ).fetchall()
        names = {r[0] for r in index_rows}
        assert "idx_findings_subject" in names
        assert "idx_findings_detector" in names
        assert "idx_findings_created" in names


def test_findings_table_has_source_version_column(tmp_path):
    """W81 reserved source_version specifically for this table — verify it landed."""
    with _fresh_conn(tmp_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)").fetchall()}
        assert "source_version" in cols, (
            "source_version column missing — W81 reserved this for the findings table"
        )


def test_emit_finding_round_trip(tmp_path):
    """Insert a FindingRecord, fetch by stable id, fields match."""
    with _fresh_conn(tmp_path) as conn:
        rec = FindingRecord(
            finding_id_str="example-detector:sym:abcd",
            subject_kind="symbol",
            subject_id=42,
            claim="too many callers (528)",
            evidence_json=json.dumps({"callers": 528}),
            confidence=CONFIDENCE_STRUCTURAL,
            source_detector="example-detector",
            source_version="1.2.3",
        )
        new_id = emit_finding(conn, rec)
        assert new_id > 0

        fetched = get_finding(conn, "example-detector:sym:abcd")
        assert fetched is not None
        assert fetched["claim"] == "too many callers (528)"
        assert fetched["subject_kind"] == "symbol"
        assert fetched["subject_id"] == 42
        assert fetched["confidence"] == "structural"
        assert fetched["source_detector"] == "example-detector"
        assert fetched["source_version"] == "1.2.3"
        assert fetched["supersedes_id"] is None
        assert fetched["suppressions_json"] == "[]"


def test_emit_finding_upserts_on_conflict(tmp_path):
    """Re-emitting the same finding_id_str refreshes evidence without duplicating rows."""
    with _fresh_conn(tmp_path) as conn:
        rec_v1 = FindingRecord(
            finding_id_str="example:sym:hash1",
            subject_kind="symbol",
            claim="initial finding",
            source_detector="example",
            evidence_json=json.dumps({"v": 1}),
        )
        id_v1 = emit_finding(conn, rec_v1)

        rec_v2 = FindingRecord(
            finding_id_str="example:sym:hash1",
            subject_kind="symbol",
            claim="refreshed finding",
            source_detector="example",
            evidence_json=json.dumps({"v": 2}),
            confidence=CONFIDENCE_STRUCTURAL,
            source_version="2.0.0",
        )
        id_v2 = emit_finding(conn, rec_v2)

        # Same row id preserved.
        assert id_v1 == id_v2

        # Only one row exists.
        count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        assert count == 1

        # Evidence + version refreshed.
        fetched = get_finding(conn, "example:sym:hash1")
        assert fetched is not None
        assert fetched["claim"] == "refreshed finding"
        assert json.loads(fetched["evidence_json"]) == {"v": 2}
        assert fetched["confidence"] == "structural"
        assert fetched["source_version"] == "2.0.0"


def test_finding_unique_constraint_enforced(tmp_path):
    """The UNIQUE(finding_id_str) constraint exists at the SQL layer.

    emit_finding uses ON CONFLICT DO UPDATE, but a raw INSERT bypassing
    that helper should still hit the constraint — this test catches a
    regression where the UNIQUE constraint accidentally drops from
    schema.py.
    """
    import sqlite3

    with _fresh_conn(tmp_path) as conn:
        conn.execute(
            "INSERT INTO findings (finding_id_str, subject_kind, claim, source_detector) "
            "VALUES (?, ?, ?, ?)",
            ("unique-test", "symbol", "first", "det"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings (finding_id_str, subject_kind, claim, source_detector) "
                "VALUES (?, ?, ?, ?)",
                ("unique-test", "symbol", "duplicate", "det"),
            )


def test_supersede_finding_links_chain(tmp_path):
    """supersede_finding sets supersedes_id on the new row, preserves the old row."""
    with _fresh_conn(tmp_path) as conn:
        original = FindingRecord(
            finding_id_str="det:sym:old",
            subject_kind="symbol",
            claim="old verdict",
            source_detector="det",
        )
        old_id = emit_finding(conn, original)

        successor = FindingRecord(
            finding_id_str="det:sym:new",
            subject_kind="symbol",
            claim="new verdict",
            source_detector="det",
        )
        new_id = supersede_finding(conn, "det:sym:old", successor)

        # Both rows still exist.
        rows = conn.execute(
            "SELECT id, finding_id_str, supersedes_id FROM findings ORDER BY id"
        ).fetchall()
        assert len(rows) == 2

        # Old row unchanged; new row links back.
        new_row = get_finding(conn, "det:sym:new")
        assert new_row is not None
        assert new_row["id"] == new_id
        assert new_row["supersedes_id"] == old_id

        old_row = get_finding(conn, "det:sym:old")
        assert old_row is not None
        assert old_row["supersedes_id"] is None


def test_supersede_missing_finding_raises(tmp_path):
    """Calling supersede_finding on a non-existent id raises ValueError."""
    with _fresh_conn(tmp_path) as conn:
        rec = FindingRecord(
            finding_id_str="det:sym:new",
            subject_kind="symbol",
            claim="new",
            source_detector="det",
        )
        with pytest.raises(ValueError):
            supersede_finding(conn, "det:sym:does-not-exist", rec)


def test_list_findings_filters_by_detector(tmp_path):
    """list_findings(detector=X) returns only that detector's rows."""
    with _fresh_conn(tmp_path) as conn:
        for det in ("alpha", "alpha", "beta"):
            emit_finding(conn, FindingRecord(
                finding_id_str=f"{det}:sym:{id(det)}-{det}",
                subject_kind="symbol",
                claim=f"{det} finding",
                source_detector=det,
            ))
        # Force unique ids since id() above may collide on interned strings.
        conn.execute("DELETE FROM findings")
        emit_finding(conn, FindingRecord(
            finding_id_str="alpha:sym:1", subject_kind="symbol",
            claim="a1", source_detector="alpha",
        ))
        emit_finding(conn, FindingRecord(
            finding_id_str="alpha:sym:2", subject_kind="symbol",
            claim="a2", source_detector="alpha",
        ))
        emit_finding(conn, FindingRecord(
            finding_id_str="beta:sym:1", subject_kind="symbol",
            claim="b1", source_detector="beta",
        ))

        alpha_only = list_findings(conn, detector="alpha")
        assert len(alpha_only) == 2
        assert all(r["source_detector"] == "alpha" for r in alpha_only)

        beta_only = list_findings(conn, detector="beta")
        assert len(beta_only) == 1
        assert beta_only[0]["claim"] == "b1"


def test_list_findings_filters_by_subject(tmp_path):
    """Subject-kind + subject-id composes correctly."""
    with _fresh_conn(tmp_path) as conn:
        emit_finding(conn, FindingRecord(
            finding_id_str="d:sym:1", subject_kind="symbol",
            subject_id=10, claim="s10", source_detector="d",
        ))
        emit_finding(conn, FindingRecord(
            finding_id_str="d:file:1", subject_kind="file",
            subject_id=10, claim="f10", source_detector="d",
        ))
        emit_finding(conn, FindingRecord(
            finding_id_str="d:sym:2", subject_kind="symbol",
            subject_id=20, claim="s20", source_detector="d",
        ))

        sym_only = list_findings(conn, subject_kind="symbol")
        assert len(sym_only) == 2

        sym_10 = list_findings(conn, subject_kind="symbol", subject_id=10)
        assert len(sym_10) == 1
        assert sym_10[0]["claim"] == "s10"


def test_count_by_detector(tmp_path):
    """count_by_detector returns the right per-detector totals."""
    with _fresh_conn(tmp_path) as conn:
        for i in range(3):
            emit_finding(conn, FindingRecord(
                finding_id_str=f"a:sym:{i}", subject_kind="symbol",
                claim=f"a{i}", source_detector="alpha",
            ))
        emit_finding(conn, FindingRecord(
            finding_id_str="b:sym:1", subject_kind="symbol",
            claim="b1", source_detector="beta",
        ))

        counts = count_by_detector(conn)
        assert counts == {"alpha": 3, "beta": 1}


def test_count_by_detector_empty(tmp_path):
    """count_by_detector on an empty table returns an empty dict."""
    with _fresh_conn(tmp_path) as conn:
        assert count_by_detector(conn) == {}


def test_user_version_bumped_to_16(tmp_path):
    """A4 migration ran; user_version is at the expected era."""
    assert USER_VERSION >= 16, (
        f"USER_VERSION is {USER_VERSION}; A4 (findings registry) must bump it to >= 16."
    )

    with _fresh_conn(tmp_path) as conn:
        row = conn.execute("PRAGMA user_version").fetchone()
        live = int(row[0]) if row else 0
        assert live == USER_VERSION


def test_default_confidence_is_heuristic(tmp_path):
    """A record built with only required fields defaults to heuristic confidence.

    Detectors that don't explicitly set confidence land at the lowest
    tier — forces them to opt in to higher confidence rather than
    silently overclaiming.
    """
    with _fresh_conn(tmp_path) as conn:
        emit_finding(conn, FindingRecord(
            finding_id_str="def:sym:1", subject_kind="symbol",
            claim="default", source_detector="def",
        ))
        fetched = get_finding(conn, "def:sym:1")
        assert fetched is not None
        assert fetched["confidence"] == CONFIDENCE_HEURISTIC
