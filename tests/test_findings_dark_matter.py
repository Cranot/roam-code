"""Tests for the W154 migration: dark-matter detector emits to the central
findings registry.

The dark-matter detector is the Nth detector migrating onto the A4
findings registry (after W95 clones, W99 dead, W102 complexity, W109
smells, W115 bus-factor, W134 pr-risk, …). It continues to surface
its detector-specific list of co-changing-with-no-structural-link
file pairs to the caller and ALSO emits one row per pair into
``findings`` when invoked with ``--persist``. These tests cover that
additive emit and the end-to-end visibility through ``roam findings``
for an agent.

W154 introduces a NEW ``subject_kind`` vocabulary — ``file_pair`` —
because a dark-matter coupling is an undirected edge between two
files, not a single symbol/file/commit. ``subject_id`` stays NULL
(mirrors the W134 pr-risk NULL-subject pattern); the canonical
``(path_a, path_b)`` ordering inside the deterministic
``finding_id_str`` is what guarantees ``(A, B)`` and ``(B, A)``
collapse to the same registry row.

The confidence-tier mapping is category-driven (single ``kind`` of
``arch.dark_matter`` with the tier varying by evidence):

* typed hypotheses (``SHARED_DB`` / ``EVENT_BUS`` / ``SHARED_CONFIG``
  / ``SHARED_API`` / ``TEXT_SIMILARITY`` / ``COPY_PASTE`` /
  ``NAMING``) → ``structural``
* ``UNKNOWN`` (engine ran but matched no pattern) → ``heuristic``
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_dark_matter import (
    DARK_MATTER_DETECTOR_VERSION,
    _canonical_pair,
    _dark_matter_confidence_for_category,
    _dark_matter_finding_id,
    _emit_dark_matter_findings,
)
from roam.db.connection import open_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dark_matter_project(project_factory, monkeypatch):
    """Project where billing.py <-> reporting.py co-change 3 times with no
    import edge, while models.py imports billing.py (structural edge).

    Mirrors the fixture in ``tests/test_dark_matter.py`` so the same
    threshold settings reliably surface at least one dark-matter pair on
    every host. We do NOT reuse the upstream fixture directly because
    pytest-xdist can re-order test collection across files, and a
    duplicated fixture keeps this suite self-contained.
    """
    billing_v1 = "def get_invoice(id):\n    return {'id': id, 'amount': 100}\n"
    reporting_v1 = "def monthly_report():\n    return {'total': 500}\n"
    models_v1 = (
        "from billing import get_invoice\n"
        "\n"
        "def load_model():\n"
        "    inv = get_invoice(1)\n"
        "    return inv\n"
    )

    billing_v2 = "def get_invoice(id):\n    return {'id': id, 'amount': 200, 'tax': 20}\n"
    reporting_v2 = "def monthly_report():\n    return {'total': 600, 'tax_total': 60}\n"

    billing_v3 = (
        "def get_invoice(id):\n"
        "    return {'id': id, 'amount': 300, 'tax': 30, 'discount': 10}\n"
    )
    reporting_v3 = (
        "def monthly_report():\n"
        "    return {'total': 700, 'tax_total': 70, 'discounts': 10}\n"
    )

    proj = project_factory(
        {
            "billing.py": billing_v1,
            "reporting.py": reporting_v1,
            "models.py": models_v1,
        },
        extra_commits=[
            ({"billing.py": billing_v2, "reporting.py": reporting_v2}, "add tax"),
            ({"billing.py": billing_v3, "reporting.py": reporting_v3}, "add discount"),
        ],
    )
    monkeypatch.chdir(proj)
    return proj


def _persist_dark_matter(proj, *extra_args):
    """Run ``dark-matter --persist`` on the already-indexed project.

    The ``project_factory`` fixture already indexes; we don't reindex
    here so the existing git_cochange rows survive into the persist
    branch.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(
            cli,
            ["dark-matter", "--persist", "--min-npmi", "0.0", "--min-cochanges", "2", *extra_args],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    return result


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The indexer + git history aren't needed here — we exercise
    ``_emit_dark_matter_findings`` directly on synthetic pair dicts so
    the per-category tier mapping and the canonical-ordering id logic
    are verified independently of which pairs the DB happens to
    surface on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


# ---------------------------------------------------------------------------
# Unit tests on the deterministic helpers (no DB / no CLI invocation)
# ---------------------------------------------------------------------------


def test_canonical_pair_is_lexicographic():
    """_canonical_pair always returns (lo, hi) lexicographic order."""
    assert _canonical_pair("a.py", "b.py") == ("a.py", "b.py")
    assert _canonical_pair("b.py", "a.py") == ("a.py", "b.py")
    # Same on identical strings (degenerate but well-defined).
    assert _canonical_pair("x.py", "x.py") == ("x.py", "x.py")


def test_dark_matter_finding_id_format():
    """_dark_matter_finding_id matches ``dark-matter:arch.dark_matter:<hex>``."""
    fid = _dark_matter_finding_id("src/a.py", "src/b.py")
    assert fid.startswith("dark-matter:arch.dark_matter:")
    # The suffix is sha1[:12] — 12 lowercase hex chars.
    suffix = fid.split(":")[-1]
    assert len(suffix) == 12
    int(suffix, 16)  # valid hex


def test_dark_matter_finding_id_is_canonical_pair_invariant():
    """``finding_id(A, B) == finding_id(B, A)`` — undirected coupling.

    This is the W154 contract: ``(path_a, path_b)`` and ``(path_b,
    path_a)`` are the same hidden coupling; the engine's emission
    order must not influence the registry row.
    """
    a = "src/billing.py"
    b = "src/reporting.py"
    assert _dark_matter_finding_id(a, b) == _dark_matter_finding_id(b, a)


def test_dark_matter_finding_id_differs_across_pairs():
    """Different pairs produce different ids."""
    a = _dark_matter_finding_id("src/a.py", "src/b.py")
    b = _dark_matter_finding_id("src/a.py", "src/c.py")
    assert a != b


def test_confidence_tier_typed_categories_are_structural():
    """Every typed hypothesis category maps to ``structural``."""
    typed = [
        "SHARED_DB",
        "EVENT_BUS",
        "SHARED_CONFIG",
        "SHARED_API",
        "TEXT_SIMILARITY",
        "COPY_PASTE",
        "NAMING",
    ]
    for cat in typed:
        assert _dark_matter_confidence_for_category(cat) == "structural", (
            f"category {cat!r} expected structural"
        )


def test_confidence_tier_unknown_is_heuristic():
    """UNKNOWN — and any missing/null category — falls back to heuristic."""
    assert _dark_matter_confidence_for_category("UNKNOWN") == "heuristic"
    assert _dark_matter_confidence_for_category(None) == "heuristic"
    assert _dark_matter_confidence_for_category("") == "heuristic"
    # Unrecognised new category — also heuristic (default fallback).
    assert _dark_matter_confidence_for_category("NOT_A_REAL_CATEGORY") == "heuristic"


# ---------------------------------------------------------------------------
# Direct unit tests on _emit_dark_matter_findings (no CLI / no indexer)
# ---------------------------------------------------------------------------


def test_emit_writes_pair_finding(tmp_path):
    """_emit writes one row per pair with the W154-shaped row layout."""
    with _seed_for_emit_helper(tmp_path) as conn:
        pairs = [
            {
                "path_a": "src/billing.py",
                "path_b": "src/reporting.py",
                "npmi": 0.85,
                "lift": 12.3,
                "strength": 0.9,
                "cochange_count": 7,
                "hypothesis": {
                    "category": "SHARED_DB",
                    "detail": "both reference table(s): invoices",
                    "confidence": 0.8,
                },
            },
        ]
        written = _emit_dark_matter_findings(conn, pairs, DARK_MATTER_DETECTOR_VERSION)
        conn.commit()

    assert written == 1

    with open_db(readonly=True, project_root=tmp_path / "proj") as conn:
        row = conn.execute(
            "SELECT finding_id_str, subject_kind, subject_id, confidence, "
            "       source_detector, source_version, claim, evidence_json "
            "FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchone()
    assert row is not None
    assert row["source_detector"] == "dark-matter"
    assert row["source_version"] == DARK_MATTER_DETECTOR_VERSION
    # W154 contract: NEW ``file_pair`` subject_kind, NULL subject_id.
    assert row["subject_kind"] == "file_pair"
    assert row["subject_id"] is None
    assert row["finding_id_str"].startswith("dark-matter:arch.dark_matter:")
    # Typed hypothesis → structural tier.
    assert row["confidence"] == "structural"
    # Claim mentions the coupling + category.
    claim = (row["claim"] or "").lower()
    assert "dark-matter" in claim
    assert "shared_db" in claim


def test_emit_tier_mapping_typed_vs_unknown(tmp_path):
    """Typed categories land at structural; UNKNOWN lands at heuristic."""
    with _seed_for_emit_helper(tmp_path) as conn:
        pairs = [
            {
                "path_a": "src/a.py",
                "path_b": "src/b.py",
                "npmi": 0.5,
                "lift": 5.0,
                "strength": 0.4,
                "cochange_count": 3,
                "hypothesis": {"category": "SHARED_DB", "detail": "x", "confidence": 0.8},
            },
            {
                "path_a": "src/c.py",
                "path_b": "src/d.py",
                "npmi": 0.5,
                "lift": 5.0,
                "strength": 0.4,
                "cochange_count": 3,
                "hypothesis": {"category": "EVENT_BUS", "detail": "y", "confidence": 0.7},
            },
            {
                "path_a": "src/e.py",
                "path_b": "src/f.py",
                "npmi": 0.5,
                "lift": 5.0,
                "strength": 0.4,
                "cochange_count": 3,
                "hypothesis": {"category": "SHARED_CONFIG", "detail": "z", "confidence": 0.6},
            },
            {
                "path_a": "src/g.py",
                "path_b": "src/h.py",
                "npmi": 0.5,
                "lift": 5.0,
                "strength": 0.4,
                "cochange_count": 3,
                "hypothesis": {"category": "UNKNOWN", "detail": "no match", "confidence": 0.3},
            },
        ]
        written = _emit_dark_matter_findings(conn, pairs, DARK_MATTER_DETECTOR_VERSION)
        conn.commit()
        assert written == 4

        rows = conn.execute(
            "SELECT confidence, evidence_json FROM findings "
            "WHERE source_detector = 'dark-matter' ORDER BY id ASC"
        ).fetchall()

    tier_by_cat: dict[str, str] = {}
    for r in rows:
        ev = json.loads(r["evidence_json"])
        tier_by_cat[ev["hypothesis_category"]] = r["confidence"]

    assert tier_by_cat["SHARED_DB"] == "structural"
    assert tier_by_cat["EVENT_BUS"] == "structural"
    assert tier_by_cat["SHARED_CONFIG"] == "structural"
    assert tier_by_cat["UNKNOWN"] == "heuristic"


def test_emit_canonical_ordering_collapses_swapped_pair(tmp_path):
    """Emitting (A,B) then (B,A) produces ONE row (upsert on canonical id).

    The order-independence guarantee is the reason ``finding_id_str`` is
    derived from the sorted pair: a downstream consumer that sees both
    orderings (or two engine runs that emit different orderings) must
    not double-count the coupling.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        pair_ab = {
            "path_a": "src/billing.py",
            "path_b": "src/reporting.py",
            "npmi": 0.85,
            "lift": 12.3,
            "strength": 0.9,
            "cochange_count": 7,
            "hypothesis": {"category": "SHARED_DB", "detail": "x", "confidence": 0.8},
        }
        pair_ba = {
            "path_a": "src/reporting.py",
            "path_b": "src/billing.py",
            "npmi": 0.85,
            "lift": 12.3,
            "strength": 0.9,
            "cochange_count": 7,
            "hypothesis": {"category": "SHARED_DB", "detail": "x", "confidence": 0.8},
        }
        written_a = _emit_dark_matter_findings(conn, [pair_ab], DARK_MATTER_DETECTOR_VERSION)
        written_b = _emit_dark_matter_findings(conn, [pair_ba], DARK_MATTER_DETECTOR_VERSION)
        conn.commit()
        assert written_a == 1 and written_b == 1

        rows = conn.execute(
            "SELECT finding_id_str, evidence_json FROM findings "
            "WHERE source_detector = 'dark-matter'"
        ).fetchall()
    assert len(rows) == 1, "swapped pair should upsert onto the canonical id"
    ev = json.loads(rows[0]["evidence_json"])
    # path_a/path_b in evidence are stored in canonical sorted order.
    assert ev["path_a"] == "src/billing.py"
    assert ev["path_b"] == "src/reporting.py"
    assert ev["qualified_name"] == "src/billing.py::src/reporting.py"


def test_emit_evidence_carries_full_metric_set(tmp_path):
    """evidence_json carries npmi/lift/strength/cochange_count/hypothesis_*."""
    with _seed_for_emit_helper(tmp_path) as conn:
        pair = {
            "path_a": "src/a.py",
            "path_b": "src/b.py",
            "npmi": 0.72,
            "lift": 9.4,
            "strength": 0.66,
            "cochange_count": 5,
            "hypothesis": {
                "category": "EVENT_BUS",
                "detail": "emit/subscribe event(s): order.created",
                "confidence": 0.7,
            },
        }
        _emit_dark_matter_findings(conn, [pair], DARK_MATTER_DETECTOR_VERSION)
        conn.commit()
        row = conn.execute(
            "SELECT evidence_json FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchone()
    ev = json.loads(row["evidence_json"])
    for k in (
        "qualified_name",
        "path_a",
        "path_b",
        "npmi",
        "lift",
        "strength",
        "cochange_count",
        "hypothesis_category",
        "hypothesis_detail",
        "hypothesis_confidence",
    ):
        assert k in ev, f"evidence missing key {k}"
    assert ev["npmi"] == 0.72
    assert ev["lift"] == 9.4
    assert ev["strength"] == 0.66
    assert ev["cochange_count"] == 5
    assert ev["hypothesis_category"] == "EVENT_BUS"


def test_emit_rerun_is_deterministic_upsert(tmp_path):
    """Re-running the helper on the same pair produces the same id (no dup)."""
    with _seed_for_emit_helper(tmp_path) as conn:
        pair = {
            "path_a": "src/a.py",
            "path_b": "src/b.py",
            "npmi": 0.5,
            "lift": 5.0,
            "strength": 0.4,
            "cochange_count": 3,
            "hypothesis": {"category": "SHARED_DB", "detail": "x", "confidence": 0.8},
        }
        _emit_dark_matter_findings(conn, [pair], DARK_MATTER_DETECTOR_VERSION)
        conn.commit()

        first_ids = {
            r[0]
            for r in conn.execute(
                "SELECT finding_id_str FROM findings WHERE source_detector = 'dark-matter'"
            ).fetchall()
        }
        first_count = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchone()[0]
        assert first_count == 1 and len(first_ids) == 1

        # Second emit — same pair, same hash inputs → upsert, same id.
        _emit_dark_matter_findings(conn, [pair], DARK_MATTER_DETECTOR_VERSION)
        conn.commit()

        second_ids = {
            r[0]
            for r in conn.execute(
                "SELECT finding_id_str FROM findings WHERE source_detector = 'dark-matter'"
            ).fetchall()
        }
        second_count = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchone()[0]
    assert second_count == first_count
    assert second_ids == first_ids


def test_emit_skips_pairs_with_missing_paths(tmp_path):
    """Malformed engine output (missing path_a or path_b) is silently skipped."""
    with _seed_for_emit_helper(tmp_path) as conn:
        bad_pairs = [
            {
                "path_a": "",
                "path_b": "src/b.py",
                "npmi": 0.5,
                "lift": 5.0,
                "strength": 0.4,
                "cochange_count": 3,
                "hypothesis": {"category": "UNKNOWN", "detail": "", "confidence": 0.3},
            },
            {
                "path_a": "src/a.py",
                "path_b": "",
                "npmi": 0.5,
                "lift": 5.0,
                "strength": 0.4,
                "cochange_count": 3,
                "hypothesis": {"category": "UNKNOWN", "detail": "", "confidence": 0.3},
            },
        ]
        written = _emit_dark_matter_findings(conn, bad_pairs, DARK_MATTER_DETECTOR_VERSION)
        conn.commit()
    assert written == 0


# ---------------------------------------------------------------------------
# Core migration assertions via the dark_matter_project fixture
# ---------------------------------------------------------------------------


def test_dark_matter_emits_to_findings_registry(dark_matter_project):
    """Running dark-matter --persist on a co-change fixture populates findings."""
    result = _persist_dark_matter(dark_matter_project)
    assert result.exit_code == 0, result.output

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            "SELECT finding_id_str, subject_kind, subject_id, source_detector, "
            "       source_version, confidence, claim "
            "FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchall()
    assert len(rows) >= 1, "expected at least one dark-matter-emitted finding row"
    for r in rows:
        assert r["source_detector"] == "dark-matter"
        assert r["source_version"] == DARK_MATTER_DETECTOR_VERSION
        # W154 invariant: file-pair findings use the NEW subject_kind
        # and never resolve to a symbols.id.
        assert r["subject_kind"] == "file_pair"
        assert r["subject_id"] is None
        assert r["confidence"] in ("structural", "heuristic")
        assert r["finding_id_str"].startswith("dark-matter:arch.dark_matter:")


def test_dark_matter_rerun_upserts_not_duplicates(dark_matter_project):
    """Re-running dark-matter --persist produces the same finding_id_str set."""
    r1 = _persist_dark_matter(dark_matter_project)
    assert r1.exit_code == 0, r1.output

    with open_db(readonly=True) as conn:
        first_ids = {
            r[0]
            for r in conn.execute(
                "SELECT finding_id_str FROM findings WHERE source_detector = 'dark-matter'"
            ).fetchall()
        }
        first_count = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchone()[0]
    assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"
    assert first_count >= 1

    # Second run — same git history, same engine output → same ids.
    r2 = _persist_dark_matter(dark_matter_project)
    assert r2.exit_code == 0, r2.output

    with open_db(readonly=True) as conn:
        second_ids = {
            r[0]
            for r in conn.execute(
                "SELECT finding_id_str FROM findings WHERE source_detector = 'dark-matter'"
            ).fetchall()
        }
        second_count = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE source_detector = 'dark-matter'"
        ).fetchone()[0]
    assert second_count == first_count, "row count drifted across runs"
    assert second_ids == first_ids, "finding_id_str set changed across runs"


def test_dark_matter_evidence_carries_metrics_and_category(dark_matter_project):
    """The finding's evidence JSON carries the full metric + hypothesis set."""
    r = _persist_dark_matter(dark_matter_project)
    assert r.exit_code == 0, r.output

    with open_db(readonly=True) as conn:
        row = conn.execute(
            "SELECT evidence_json FROM findings "
            "WHERE source_detector = 'dark-matter' "
            "ORDER BY id ASC LIMIT 1"
        ).fetchone()
    assert row is not None
    ev = json.loads(row["evidence_json"])
    for k in (
        "qualified_name",
        "path_a",
        "path_b",
        "npmi",
        "lift",
        "strength",
        "cochange_count",
        "hypothesis_category",
    ):
        assert k in ev, f"evidence missing key {k}"
    # Canonical ordering surfaces in evidence too — path_a <= path_b.
    assert ev["path_a"] <= ev["path_b"]
    assert ev["qualified_name"] == f"{ev['path_a']}::{ev['path_b']}"


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_dark_matter_findings_visible_via_cmd_findings_list(dark_matter_project):
    """`roam findings list --detector dark-matter` returns rows after migration."""
    r = _persist_dark_matter(dark_matter_project)
    assert r.exit_code == 0, r.output

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(dark_matter_project))
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "dark-matter"]
        )
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["command"] == "findings-list"
    assert envelope["summary"]["state"] == "populated"
    assert envelope["summary"]["total_findings"] >= 1
    assert "dark-matter" in envelope["summary"]["detectors"]
    assert all(
        r["source_detector"] == "dark-matter" for r in envelope["findings"]
    )


def test_dark_matter_findings_visible_via_cmd_findings_count(dark_matter_project):
    """`roam findings count` includes a non-zero entry for dark-matter."""
    r = _persist_dark_matter(dark_matter_project)
    assert r.exit_code == 0, r.output
    assert_detector_visible_in_findings_count(dark_matter_project, "dark-matter")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(dark_matter_project):
    """Without --persist, dark-matter stays side-effect-free.

    The registry mirror lives strictly inside the ``--persist`` branch —
    running ``roam dark-matter`` without the flag must not write to
    ``findings``. (Matches the W109 / W134 invariant.)
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(dark_matter_project))
        result = runner.invoke(
            cli,
            ["dark-matter", "--min-npmi", "0.0", "--min-cochanges", "2"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output

    with open_db(readonly=True) as conn:
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'dark-matter'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            count = 0
    assert count == 0, "non-persist dark-matter still wrote to findings"


def test_dark_matter_persist_no_findings_table_no_crash(dark_matter_project):
    """``dark-matter --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard analysis path (which the
    JSON / text envelope still has to produce) must keep working — the
    command exits 0 and writes no registry rows.
    """
    # Drop the findings table to simulate pre-W89 schema.
    with open_db(readonly=False) as conn:
        conn.execute("DROP TABLE IF EXISTS findings")
        conn.commit()

    result = _persist_dark_matter(dark_matter_project)
    # Must succeed despite the missing findings table.
    assert result.exit_code == 0, result.output
