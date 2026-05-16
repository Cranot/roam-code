"""Tests for the W134 migration: pr-risk detector emits to the central
findings registry.

The pr-risk detector is the sixth migration onto the A4 findings table
(after W95 clones, W99 dead, W102 complexity, W109 smells, W115
bus-factor). Unlike those five — which scan workspace state — pr-risk
is INVOCATION-SCOPED: each run produces findings tied to a specific
diff (commit range / staged / unstaged) at the moment of invocation.

The detector emits up to four kinds of findings per invocation:

* ``composite-risk-score`` (always emitted; the headline 0-100 score)
  — heuristic.
* ``high-blast-radius-symbol-touched`` (when ``blast_pct >= 20``) —
  structural (graph reverse-descendants).
* ``test-coverage-gap`` (when ``test_coverage < 0.5`` with source files)
  — structural.
* ``author-novelty-flag`` (when ``familiarity_risk >= 0.10``) —
  heuristic.

Every row carries ``evidence_json.diff_id`` so consumers can group
findings by PR / commit / branch — and tell stale (since-merged) rows
apart from fresh ones.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))

from roam.cli import cli
from roam.commands.cmd_pr_risk import (  # noqa: E402
    _PR_RISK_KIND_TO_CONFIDENCE,
    PR_RISK_DETECTOR_VERSION,
    _build_pr_risk_finding_rows,
    _diff_id,
    _emit_pr_risk_findings,
    _pr_risk_finding_id,
)
from roam.db.connection import open_db  # noqa: E402
from tests._findings_helpers import assert_detector_visible_in_findings_count  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_unstaged_change(project):
    """Modify src/models.py so pr-risk has a non-empty changeset to analyse."""
    (project / "src" / "models.py").write_text(
        "class User:\n"
        '    """A user model (modified for pr-risk findings test)."""\n'
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display_name(self):\n"
        "        return self.name.title()\n"
        "\n"
        "    def validate_email(self):\n"
        '        return "@" in self.email\n'
        "\n"
        "\n"
        "class Admin(User):\n"
        '    """An admin user (modified)."""\n'
        '    def __init__(self, name, email, role="admin"):\n'
        "        super().__init__(name, email)\n"
        "        self.role = role\n"
        "\n"
        "    def promote(self, user):\n"
        "        pass\n"
        "\n"
        "    def demote(self, user):\n"
        "        pass\n",
        encoding="utf-8",
    )


def _restore_models(project, original):
    (project / "src" / "models.py").write_text(original, encoding="utf-8")


def _run_pr_risk_persist(project, *extra_args):
    """Invoke ``roam pr-risk --persist`` in the indexed project's cwd."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        result = runner.invoke(cli, ["pr-risk", "--persist", *extra_args], catch_exceptions=False)
        return result
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests on the deterministic helpers (no DB / no CLI invocation)
# ---------------------------------------------------------------------------


def test_diff_id_is_deterministic():
    """Same inputs produce the same diff_id; different inputs produce different ids."""
    a = _diff_id(
        label="unstaged",
        commit_range=None,
        staged=False,
        file_paths=["src/a.py", "src/b.py"],
    )
    b = _diff_id(
        label="unstaged",
        commit_range=None,
        staged=False,
        file_paths=["src/a.py", "src/b.py"],
    )
    assert a == b
    # Different file set -> different id.
    c = _diff_id(
        label="unstaged",
        commit_range=None,
        staged=False,
        file_paths=["src/a.py"],
    )
    assert c != a
    # Different label / staged-flag -> different id.
    d = _diff_id(
        label="staged",
        commit_range=None,
        staged=True,
        file_paths=["src/a.py", "src/b.py"],
    )
    assert d != a
    # Different commit_range -> different id.
    e = _diff_id(
        label="HEAD~1..HEAD",
        commit_range="HEAD~1..HEAD",
        staged=False,
        file_paths=["src/a.py", "src/b.py"],
    )
    assert e != a


def test_diff_id_is_order_independent():
    """File-path order doesn't change the diff_id (sort happens inside)."""
    a = _diff_id(
        label="unstaged",
        commit_range=None,
        staged=False,
        file_paths=["src/a.py", "src/b.py", "src/c.py"],
    )
    b = _diff_id(
        label="unstaged",
        commit_range=None,
        staged=False,
        file_paths=["src/c.py", "src/a.py", "src/b.py"],
    )
    assert a == b


def test_pr_risk_finding_id_format():
    """_pr_risk_finding_id always begins with ``pr-risk:<kind>:<diff_id>``."""
    fid = _pr_risk_finding_id("composite-risk-score", "abc123")
    assert fid == "pr-risk:composite-risk-score:abc123"
    fid2 = _pr_risk_finding_id("composite-risk-score", "abc123")
    assert fid == fid2
    # Different kind -> different id (same diff still gets multiple kinds).
    other = _pr_risk_finding_id("test-coverage-gap", "abc123")
    assert other != fid
    # Different diff_id -> different id.
    other2 = _pr_risk_finding_id("composite-risk-score", "def456")
    assert other2 != fid


def test_confidence_tier_table_covers_emitted_kinds():
    """Every kind the emit helper writes has a confidence tier."""
    expected_kinds = {
        "composite-risk-score",
        "high-blast-radius-symbol-touched",
        "test-coverage-gap",
        "author-novelty-flag",
    }
    assert expected_kinds <= set(_PR_RISK_KIND_TO_CONFIDENCE.keys())
    # Composite is heuristic (multiplicative weighted sum of fuzzy signals).
    assert _PR_RISK_KIND_TO_CONFIDENCE["composite-risk-score"] == "heuristic"
    # Blast / coverage are graph-derived -> structural.
    assert _PR_RISK_KIND_TO_CONFIDENCE["high-blast-radius-symbol-touched"] == "structural"
    assert _PR_RISK_KIND_TO_CONFIDENCE["test-coverage-gap"] == "structural"
    # Novelty rolls up time-decayed churn -> heuristic.
    assert _PR_RISK_KIND_TO_CONFIDENCE["author-novelty-flag"] == "heuristic"


# ---------------------------------------------------------------------------
# Core migration assertions via the indexed_project fixture
# ---------------------------------------------------------------------------


def test_pr_risk_emits_composite_finding(indexed_project):
    """Running pr-risk --persist on a diff writes at least the composite row."""
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)
        result = _run_pr_risk_persist(indexed_project)
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, subject_kind, subject_id, "
                "       source_detector, source_version, confidence, claim "
                "FROM findings WHERE source_detector = 'pr-risk'"
            ).fetchall()

        # Composite row always emits; others are conditional on signal
        # thresholds. At least the composite must exist.
        assert len(rows) >= 1, "expected at least one pr-risk finding row"
        kinds = {r["finding_id_str"].split(":")[1] for r in rows}
        assert "composite-risk-score" in kinds

        for r in rows:
            assert r["source_detector"] == "pr-risk"
            assert r["source_version"] == PR_RISK_DETECTOR_VERSION
            # pr-risk operates on a changeset, not on a workspace symbol.
            assert r["subject_kind"] == "commit"
            assert r["subject_id"] is None
            assert r["finding_id_str"].startswith("pr-risk:")
            # Every emitted kind has a confidence tier.
            kind = r["finding_id_str"].split(":")[1]
            assert r["confidence"] == _PR_RISK_KIND_TO_CONFIDENCE[kind]
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_evidence_carries_diff_id_and_label(indexed_project):
    """evidence_json carries diff_id, label, file_list — the audit-trail hooks."""
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)
        result = _run_pr_risk_persist(indexed_project)
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'pr-risk' "
                "  AND finding_id_str LIKE 'pr-risk:composite-risk-score:%' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None, "expected a composite-risk-score finding"
        evidence = json.loads(row["evidence_json"])

        # Audit-trail keys — every kind shares this base envelope.
        for key in (
            "diff_id",
            "label",
            "commit_range",
            "staged",
            "file_list",
            "changed_files_count",
            "created_at_epoch",
        ):
            assert key in evidence, f"evidence missing key {key}"

        # Composite-specific signal keys.
        for key in (
            "risk_score",
            "risk_level",
            "blast_radius_pct",
            "hotspot_score",
            "test_coverage_pct",
            "bus_factor_risk",
            "coupling_score",
            "novelty_score",
            "familiarity_risk",
            "minor_risk",
        ):
            assert key in evidence, f"composite evidence missing key {key}"

        # The default invocation is unstaged.
        assert evidence["staged"] is False
        assert evidence["label"] == "unstaged"
        assert isinstance(evidence["file_list"], list)
        assert evidence["changed_files_count"] == len(evidence["file_list"])

        # The claim must name a recognisable pr-risk verdict.
        claim = (row["claim"] or "").lower()
        assert "pr-risk" in claim
        # Risk score range check.
        assert 0 <= int(evidence["risk_score"]) <= 100
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_rerun_upserts_not_duplicates(indexed_project):
    """Re-running pr-risk --persist on the SAME diff produces the same id set."""
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)
        r1 = _run_pr_risk_persist(indexed_project)
        assert r1.exit_code == 0, r1.output

        with open_db(readonly=True) as conn:
            first_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'pr-risk'"
                ).fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'pr-risk'").fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"
        assert first_count >= 1

        # Second run — same diff, same file set, same author -> same ids.
        r2 = _run_pr_risk_persist(indexed_project)
        assert r2.exit_code == 0, r2.output

        with open_db(readonly=True) as conn:
            second_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'pr-risk'"
                ).fetchall()
            }
            second_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'pr-risk'").fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_different_diff_gets_fresh_finding_id(indexed_project):
    """Changing the diff (label / file set) writes a NEW row, not an upsert.

    The audit-trail design: prior PR findings stay in the registry so a
    consumer can compare risk profiles across iterations of the same
    branch. Only a rerun against the *same* diff upserts.
    """
    models = indexed_project / "src" / "models.py"
    service = indexed_project / "src" / "service.py"
    original_models = models.read_text(encoding="utf-8")
    original_service = service.read_text(encoding="utf-8")
    try:
        # First diff: touch models.py only.
        _make_unstaged_change(indexed_project)
        r1 = _run_pr_risk_persist(indexed_project)
        assert r1.exit_code == 0, r1.output

        with open_db(readonly=True) as conn:
            first_composite_id = conn.execute(
                "SELECT finding_id_str FROM findings "
                "WHERE source_detector = 'pr-risk' "
                "  AND finding_id_str LIKE 'pr-risk:composite-risk-score:%' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()[0]

        # Second diff: ALSO touch service.py so the file set differs.
        service.write_text(
            original_service + "\n\ndef freshly_added_for_test():\n    return 99\n",
            encoding="utf-8",
        )

        r2 = _run_pr_risk_persist(indexed_project)
        assert r2.exit_code == 0, r2.output

        with open_db(readonly=True) as conn:
            composite_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'pr-risk' "
                    "  AND finding_id_str LIKE 'pr-risk:composite-risk-score:%' "
                    "ORDER BY id ASC"
                ).fetchall()
            ]

        # The first diff's id must still be present (audit trail), and a
        # second, distinct composite id must have been inserted.
        assert first_composite_id in composite_ids
        assert len(set(composite_ids)) >= 2, f"expected two distinct composite ids across diffs, got {composite_ids}"
    finally:
        _restore_models(indexed_project, original_models)
        service.write_text(original_service, encoding="utf-8")


def test_no_persist_does_not_emit_findings(indexed_project):
    """Without --persist, pr-risk stays side-effect-free.

    The registry mirror lives strictly inside the ``--persist`` branch —
    running ``roam pr-risk`` without the flag must not write to
    ``findings``. (Matches the W115 bus-factor invariant.)
    """
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(indexed_project))
            result = runner.invoke(cli, ["pr-risk"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'pr-risk'").fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist pr-risk still wrote to findings"
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_persist_no_findings_table_no_crash(indexed_project):
    """``pr-risk --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard analysis path (which the
    JSON / text envelope still has to produce) must keep working — the
    command exits 0 and writes no registry rows.
    """
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)

        # Drop the findings table to simulate pre-W89 schema.
        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = _run_pr_risk_persist(indexed_project)
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        _restore_models(indexed_project, original)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_pr_risk_findings_visible_via_cmd_findings_list(indexed_project):
    """`roam findings list --detector pr-risk` returns rows after migration."""
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)
        r = _run_pr_risk_persist(indexed_project)
        assert r.exit_code == 0, r.output

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(indexed_project))
            result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "pr-risk"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "pr-risk" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "pr-risk" for r in envelope["findings"])
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_findings_visible_via_cmd_findings_count(indexed_project):
    """`roam findings count` includes a non-zero entry for pr-risk."""
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change(indexed_project)
        r = _run_pr_risk_persist(indexed_project)
        assert r.exit_code == 0, r.output
        assert_detector_visible_in_findings_count(indexed_project, "pr-risk")
    finally:
        _restore_models(indexed_project, original)


# ---------------------------------------------------------------------------
# Direct unit test on _emit_pr_risk_findings (no CLI / no indexer)
# ---------------------------------------------------------------------------


def test_emit_helper_writes_composite_only_when_signals_below_thresholds(
    indexed_project,
):
    """Below all sub-kind thresholds, _emit_pr_risk_findings writes ONLY composite.

    Drives the helper directly with a synthetic data dict that's tuned
    so blast (< 20%), coverage (>= 0.5), and familiarity (< 0.10) all
    stay below their emit thresholds. The composite row always emits;
    the three conditional sub-kinds must NOT.
    """
    # The helper writes to whatever conn we hand it — use the indexed
    # project's DB so the findings table exists.
    with open_db(readonly=False) as conn:
        # Clear any prior rows so the count assertion is exact.
        conn.execute("DELETE FROM findings WHERE source_detector = 'pr-risk'")
        conn.commit()

        synthetic = {
            "diff_id": "synth0000",
            "label": "synthetic",
            "commit_range": None,
            "staged": False,
            "file_list": ["src/synthetic.py"],
            "risk": 12,
            "level": "LOW",
            "blast_pct": 1.0,  # below 20% -> no blast row
            "hotspot_score": 0.0,
            "test_coverage": 1.0,  # 100% -> no gap row
            "bus_factor_risk": 0.0,
            "coupling_score": 0.0,
            "novelty": 0.0,
            "familiarity_risk": 0.0,  # below 0.10 -> no novelty row
            "minor_risk": 0.0,
            "reductive_change": False,
            "driver_label": None,
            "total_added": 0,
            "total_removed": 0,
            "resolved_author": "Alice",
            "affected_count": 0,
            "total_syms_repo": 100,
            "changed_syms_count": 1,
            "source_files_count": 1,
            "covered_files": 1,
            "familiarity_details": {
                "avg_familiarity": 1.0,
                "files_assessed": 1,
                "files_familiar": 1,
                "files": [],
            },
        }
        written = _emit_pr_risk_findings(conn, synthetic, PR_RISK_DETECTOR_VERSION)
        conn.commit()

    assert written == 1, "only composite-risk-score should fire below thresholds"

    with open_db(readonly=True) as conn:
        kinds = {
            row[0].split(":")[1]
            for row in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'pr-risk'").fetchall()
        }
    assert kinds == {"composite-risk-score"}


def test_emit_helper_writes_all_kinds_when_signals_trigger(indexed_project):
    """Above thresholds, _emit_pr_risk_findings writes the full four-kind set."""
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'pr-risk'")
        conn.commit()

        synthetic = {
            "diff_id": "synth9999",
            "label": "synthetic-hot",
            "commit_range": None,
            "staged": False,
            "file_list": ["src/hot.py", "src/cold.py"],
            "risk": 85,
            "level": "CRITICAL",
            "blast_pct": 35.0,  # >= 20% -> blast row
            "hotspot_score": 0.8,
            "test_coverage": 0.10,  # < 50% -> gap row
            "bus_factor_risk": 0.5,
            "coupling_score": 0.4,
            "novelty": 0.6,
            "familiarity_risk": 0.20,  # >= 0.10 -> novelty row
            "minor_risk": 0.10,
            "reductive_change": False,
            "driver_label": "test_coverage_low",
            "total_added": 200,
            "total_removed": 30,
            "resolved_author": "Bob",
            "affected_count": 35,
            "total_syms_repo": 100,
            "changed_syms_count": 3,
            "source_files_count": 2,
            "covered_files": 0,
            "familiarity_details": {
                "avg_familiarity": 0.20,
                "files_assessed": 2,
                "files_familiar": 0,
                "files": [],
            },
        }
        written = _emit_pr_risk_findings(conn, synthetic, PR_RISK_DETECTOR_VERSION)
        conn.commit()

    assert written == 4, f"expected all four kinds above their thresholds, got {written}"

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            "SELECT finding_id_str, confidence FROM findings WHERE source_detector = 'pr-risk'"
        ).fetchall()
    kinds = {row[0].split(":")[1] for row in rows}
    assert kinds == {
        "composite-risk-score",
        "high-blast-radius-symbol-touched",
        "test-coverage-gap",
        "author-novelty-flag",
    }
    # Confidence-tier assignment on the persisted rows.
    by_kind = {row[0].split(":")[1]: row[1] for row in rows}
    assert by_kind["composite-risk-score"] == "heuristic"
    assert by_kind["high-blast-radius-symbol-touched"] == "structural"
    assert by_kind["test-coverage-gap"] == "structural"
    assert by_kind["author-novelty-flag"] == "heuristic"


# ---------------------------------------------------------------------------
# W242: top-level ``findings[]`` array on the JSON envelope
# ---------------------------------------------------------------------------
#
# Before W242, the ``roam pr-risk`` envelope carried only ``per_file`` /
# ``suggested_reviewers`` / scalar factor fields — the W134 registry rows
# were written ONLY when ``--persist`` was passed. The collector's
# ``pr_risk_envelope`` kwarg expected a top-level ``findings[]`` array
# (W219) and emitted a ``"no 'findings' array"`` warning when it found
# none. W242 closes the gap: the envelope now carries the SAME row dicts
# that ``--persist`` mirrors into the registry — built from a single
# source (``_build_pr_risk_finding_rows``) so the two surfaces cannot
# drift.


def _make_unstaged_change_with_signal(project):
    """Make a non-trivial unstaged change that fires the pr-risk signals."""
    _make_unstaged_change(project)


def test_pr_risk_envelope_includes_findings_array(indexed_project):
    """W242: invocation -> envelope has a non-empty top-level ``findings[]``."""
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change_with_signal(indexed_project)
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(indexed_project))
            result = runner.invoke(cli, ["--json", "pr-risk"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output

        envelope = json.loads(result.output)
        assert envelope["command"] == "pr-risk"
        assert "findings" in envelope, "expected top-level findings[] on pr-risk envelope (W242)"
        assert isinstance(envelope["findings"], list)
        assert len(envelope["findings"]) >= 1, "composite-risk-score must always emit"
        # Composite is always present.
        kinds = {f["kind"] for f in envelope["findings"]}
        assert "pr-risk:composite-risk-score" in kinds
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_envelope_findings_match_persisted_rows(indexed_project):
    """W242: ``--persist`` writes N registry rows; envelope emits the SAME N.

    Single source of truth: ``_build_pr_risk_finding_rows`` builds the
    rows; both the registry-write path and the envelope-stamp path
    consume that list. The two counts must match row-for-row.
    """
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change_with_signal(indexed_project)

        # Clear any prior pr-risk rows so the registry-vs-envelope diff
        # is exact on this invocation.
        with open_db(readonly=False) as conn:
            conn.execute("DELETE FROM findings WHERE source_detector = 'pr-risk'")
            conn.commit()

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(indexed_project))
            result = runner.invoke(
                cli,
                ["--json", "pr-risk", "--persist"],
                catch_exceptions=False,
            )
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output

        envelope = json.loads(result.output)
        envelope_ids = {f["finding_id_str"] for f in envelope["findings"]}

        with open_db(readonly=True) as conn:
            persisted_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'pr-risk'"
                ).fetchall()
            }

        assert envelope_ids == persisted_ids, (
            f"envelope <-> registry drift: envelope={envelope_ids}, registry={persisted_ids}"
        )
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_envelope_findings_use_w134_shape(indexed_project):
    """W242: envelope rows carry the W134 canonical key set.

    Required keys: ``finding_id_str`` / ``source_detector`` /
    ``source_version`` / ``subject_kind`` / ``subject_id`` /
    ``confidence`` / ``claim`` / ``kind`` / ``severity`` / ``evidence``.
    The composite row also stamps ``source_detector="pr-risk"`` and
    ``source_version=PR_RISK_DETECTOR_VERSION``.
    """
    models = indexed_project / "src" / "models.py"
    original = models.read_text(encoding="utf-8")
    try:
        _make_unstaged_change_with_signal(indexed_project)
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(indexed_project))
            result = runner.invoke(cli, ["--json", "pr-risk"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output

        envelope = json.loads(result.output)
        rows = envelope["findings"]
        assert rows, "expected at least the composite-risk-score row"

        required_keys = {
            "finding_id_str",
            "source_detector",
            "source_version",
            "subject_kind",
            "subject_id",
            "confidence",
            "claim",
            "kind",
            "severity",
            "evidence",
        }
        for row in rows:
            missing = required_keys - set(row.keys())
            assert not missing, f"row {row.get('kind')} missing keys: {missing}"
            assert row["source_detector"] == "pr-risk"
            assert row["source_version"] == PR_RISK_DETECTOR_VERSION
            assert row["subject_kind"] == "commit"
            assert row["subject_id"] is None
            assert row["kind"].startswith("pr-risk:")
            assert row["severity"] in {
                "critical",
                "high",
                "medium",
                "low",
                "info",
            }

        # Composite is always present and is the headline row.
        composite = next(
            (r for r in rows if r["kind"] == "pr-risk:composite-risk-score"),
            None,
        )
        assert composite is not None
        # Composite confidence is heuristic per _PR_RISK_KIND_TO_CONFIDENCE.
        assert composite["confidence"] == "heuristic"
        # Composite evidence carries diff_id + risk_score.
        assert "diff_id" in composite["evidence"]
        assert "risk_score" in composite["evidence"]
    finally:
        _restore_models(indexed_project, original)


def test_pr_risk_findings_threshold_gating():
    """W242 threshold gating: below all sub-kind thresholds, only the
    composite row is emitted in the envelope.

    Drives ``_build_pr_risk_finding_rows`` directly with a synthetic
    data dict whose signals all sit below the emit thresholds
    (blast < 20%, coverage >= 50%, familiarity_risk < 0.10). The
    composite row is invariant; the three conditional rows must NOT
    appear.
    """
    synthetic = {
        "diff_id": "synth0000",
        "label": "synthetic",
        "commit_range": None,
        "staged": False,
        "file_list": ["src/synthetic.py"],
        "risk": 12,
        "level": "LOW",
        "blast_pct": 1.0,  # below 20% -> no blast row
        "hotspot_score": 0.0,
        "test_coverage": 1.0,  # 100% -> no gap row
        "bus_factor_risk": 0.0,
        "coupling_score": 0.0,
        "novelty": 0.0,
        "familiarity_risk": 0.0,  # below 0.10 -> no novelty row
        "minor_risk": 0.0,
        "reductive_change": False,
        "driver_label": None,
        "total_added": 0,
        "total_removed": 0,
        "resolved_author": "Alice",
        "affected_count": 0,
        "total_syms_repo": 100,
        "changed_syms_count": 1,
        "source_files_count": 1,
        "covered_files": 1,
        "familiarity_details": {
            "avg_familiarity": 1.0,
            "files_assessed": 1,
            "files_familiar": 1,
            "files": [],
        },
    }
    rows = _build_pr_risk_finding_rows(synthetic, PR_RISK_DETECTOR_VERSION)
    assert len(rows) == 1, f"only composite expected below thresholds, got {[r['kind'] for r in rows]}"
    assert rows[0]["kind"] == "pr-risk:composite-risk-score"
    # Composite severity tracks the bucketed risk level.
    assert rows[0]["severity"] == "low"

    # Above all thresholds -> full four-kind set.
    hot = {
        **synthetic,
        "diff_id": "synth9999",
        "label": "synthetic-hot",
        "file_list": ["src/hot.py", "src/cold.py"],
        "risk": 85,
        "level": "CRITICAL",
        "blast_pct": 35.0,  # >= 20% -> blast row
        "test_coverage": 0.10,  # < 50% -> gap row
        "familiarity_risk": 0.20,  # >= 0.10 -> novelty row
        "covered_files": 0,
        "source_files_count": 2,
        "affected_count": 35,
        "familiarity_details": {
            "avg_familiarity": 0.20,
            "files_assessed": 2,
            "files_familiar": 0,
            "files": [],
        },
        "resolved_author": "Bob",
    }
    hot_rows = _build_pr_risk_finding_rows(hot, PR_RISK_DETECTOR_VERSION)
    kinds_hot = {r["kind"] for r in hot_rows}
    assert kinds_hot == {
        "pr-risk:composite-risk-score",
        "pr-risk:high-blast-radius-symbol-touched",
        "pr-risk:test-coverage-gap",
        "pr-risk:author-novelty-flag",
    }
    composite_hot = next(r for r in hot_rows if r["kind"] == "pr-risk:composite-risk-score")
    assert composite_hot["severity"] == "critical"
