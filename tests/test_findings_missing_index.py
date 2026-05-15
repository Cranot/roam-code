"""Tests for the W111 follow-up: missing-index detector emits to the
central findings registry.

The missing-index detector is the fourth migration onto the A4 findings
table (after W95 clones, W99 dead, W102 complexity). It continues to
render its own JSON / text envelopes (authoritative output surface) and
ALSO, when ``--persist`` is set, emits one row per query-shape finding
into ``findings``. These tests cover that additive emit and the
end-to-end visibility through ``roam findings`` for an agent.

Unlike the prior three detectors, missing-index emits at *file* + query
granularity (no symbol id — the detector is a regex scan, not a real
PHP AST). subject_kind is "file" with subject_id resolved from
``files.path`` when possible.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_missing_index import (
    MISSING_INDEX_DETECTOR_VERSION,
    _missing_index_confidence_tier,
    _missing_index_finding_id,
)
from roam.db.connection import open_db

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402


# ---------------------------------------------------------------------------
# PHP fixture content — mirrors the one in test_missing_index.py so the
# detector reliably surfaces at least one finding on every platform.
# ---------------------------------------------------------------------------

# Migration creates `users` with an index on `email` but NOT on `phone`.
_MIGRATION_PHP = """\
<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

class CreateUsersTable extends Migration
{
    public function up()
    {
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('email')->unique();
            $table->string('phone');
            $table->timestamps();

            $table->index('email');
        });
    }

    public function down()
    {
        Schema::dropIfExists('users');
    }
}
"""

# Controller: paginated WHERE on the unindexed `phone` column — should
# land at confidence=high (paginated + unindexed + unconditional equality).
_CONTROLLER_PHP = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\User;
use Illuminate\\Http\\Request;

class UserController extends Controller
{
    public function findByPhone(Request $request)
    {
        $phone = $request->input('phone');
        $users = User::query()->where('phone', $phone)->paginate(20);
        return response()->json($users);
    }

    public function listByEmail(Request $request)
    {
        $email = $request->input('email');
        $user = User::query()->where('email', $email)->first();
        return response()->json($user);
    }

    public function sortByName(Request $request)
    {
        $users = User::query()->orderBy('name')->get();
        return response()->json($users);
    }
}
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def php_project(tmp_path):
    """Create a PHP Laravel-style project with a migration + controller.

    Index on ``email`` exists; ``phone`` is queried but not indexed —
    paginated, so the missing-index detector should emit at least one
    finding at confidence ``high`` (paginated + unindexed unconditional).
    """
    proj = tmp_path / "php_app"
    proj.mkdir()

    (proj / ".gitignore").write_text(".roam/\nvendor/\n")

    migration_dir = proj / "database" / "migrations"
    migration_dir.mkdir(parents=True)
    (migration_dir / "2024_01_01_000000_create_users_table.php").write_text(
        _MIGRATION_PHP
    )

    controller_dir = proj / "app" / "Http" / "Controllers"
    controller_dir.mkdir(parents=True)
    (controller_dir / "UserController.php").write_text(_CONTROLLER_PHP)

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


def _run_missing_index_persist(proj):
    """Run ``missing-index --persist`` from inside ``proj``.

    Returns the CliRunner result so tests can assert on exit code.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["missing-index", "--persist"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Unit tests on the deterministic helpers (no DB / no CLI invocation)
# ---------------------------------------------------------------------------


def test_missing_index_finding_id_is_deterministic():
    """_missing_index_finding_id returns the same id for the same input."""
    a = _missing_index_finding_id(
        "users", ("phone",), "single_where", "app/Http/Controllers/UserController.php", 12
    )
    b = _missing_index_finding_id(
        "users", ("phone",), "single_where", "app/Http/Controllers/UserController.php", 12
    )
    assert a == b
    assert a.startswith("missing-index:query:")

    # Different table -> different id.
    assert (
        _missing_index_finding_id(
            "orders", ("phone",), "single_where", "app/Http/Controllers/UserController.php", 12
        )
        != a
    )
    # Different columns -> different id.
    assert (
        _missing_index_finding_id(
            "users", ("email",), "single_where", "app/Http/Controllers/UserController.php", 12
        )
        != a
    )
    # Different pattern_type -> different id.
    assert (
        _missing_index_finding_id(
            "users", ("phone",), "orderby", "app/Http/Controllers/UserController.php", 12
        )
        != a
    )
    # Different file path -> different id.
    assert (
        _missing_index_finding_id(
            "users", ("phone",), "single_where", "app/Http/Controllers/Other.php", 12
        )
        != a
    )
    # Different line -> different id.
    assert (
        _missing_index_finding_id(
            "users", ("phone",), "single_where", "app/Http/Controllers/UserController.php", 99
        )
        != a
    )


def test_missing_index_finding_id_is_column_order_invariant():
    """Column-tuple ordering does not change the finding_id (sorted internally).

    The detector's column_ordering rationale is on the evidence_json
    payload — the id stays stable regardless of regex match order so
    re-runs always upsert.
    """
    a = _missing_index_finding_id(
        "users", ("phone", "status"), "composite_where", "app/X.php", 5
    )
    b = _missing_index_finding_id(
        "users", ("status", "phone"), "composite_where", "app/X.php", 5
    )
    assert a == b


def test_missing_index_confidence_tier_mapping():
    """high → static_analysis, medium → structural, low → heuristic."""
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_STRUCTURAL,
    )

    assert _missing_index_confidence_tier("high") == CONFIDENCE_STATIC_ANALYSIS
    assert _missing_index_confidence_tier("medium") == CONFIDENCE_STRUCTURAL
    assert _missing_index_confidence_tier("low") == CONFIDENCE_HEURISTIC
    # Unknown / empty input falls through to heuristic — never silently
    # over-claim static_analysis.
    assert _missing_index_confidence_tier("") == CONFIDENCE_STRUCTURAL  # "medium" default
    assert _missing_index_confidence_tier("bogus") == CONFIDENCE_HEURISTIC


# ---------------------------------------------------------------------------
# Core migration assertions (e2e through the CLI)
# ---------------------------------------------------------------------------


def test_missing_index_emits_to_findings_registry(php_project):
    """Running missing-index --persist on a fixture populates findings."""
    _run_missing_index_persist(php_project)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'missing-index'"
            ).fetchall()
    finally:
        os.chdir(old_cwd)

    assert len(rows) >= 1, "expected at least one missing-index emitted finding row"
    valid_tiers = {"static_analysis", "structural", "heuristic"}
    for r in rows:
        assert r["source_detector"] == "missing-index"
        assert r["source_version"] == MISSING_INDEX_DETECTOR_VERSION
        assert r["subject_kind"] == "file"
        assert r["confidence"] in valid_tiers
        assert r["finding_id_str"].startswith("missing-index:query:")


def test_missing_index_emits_static_analysis_tier_on_paginated_unindexed(php_project):
    """The paginated WHERE on unindexed `phone` lands at static_analysis tier.

    Confirms the "high → static_analysis" branch of the confidence-tier
    mapping is exercised on a real fixture (not just the unit test).
    """
    _run_missing_index_persist(php_project)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json, confidence FROM findings "
                "WHERE source_detector = 'missing-index'"
            ).fetchall()
    finally:
        os.chdir(old_cwd)

    assert len(rows) >= 1
    # Confirm at least one expected confidence tier appears. The fixture
    # has a paginated WHERE on the unindexed `phone` column, which the
    # detector marks at confidence="high" → "static_analysis".
    tiers = {r["confidence"] for r in rows}
    assert "static_analysis" in tiers, (
        f"expected at least one static_analysis-tier finding (paginated unindexed "
        f"WHERE on `phone`); got tiers={tiers}"
    )
    # The `phone` column must appear in at least one finding's evidence.
    has_phone = False
    for r in rows:
        ev = json.loads(r["evidence_json"])
        if "phone" in (ev.get("columns") or []):
            has_phone = True
            break
    assert has_phone, "expected at least one finding for the unindexed `phone` column"


def test_missing_index_finding_evidence_carries_query_metadata(php_project):
    """The finding's evidence JSON carries table / columns / pattern_type / location."""
    _run_missing_index_persist(php_project)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'missing-index' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
    finally:
        os.chdir(old_cwd)

    assert row is not None
    evidence = json.loads(row["evidence_json"])
    for key in (
        "table",
        "columns",
        "issue",
        "query_location",
        "pattern_type",
        "suggestion",
        "detector_confidence",
    ):
        assert key in evidence, f"evidence missing key {key!r}: {evidence}"
    assert isinstance(evidence["columns"], list)
    assert evidence["pattern_type"] in (
        "composite_where",
        "single_where",
        "orderby",
        "orderby_with_where",
    )
    assert evidence["detector_confidence"] in ("high", "medium", "low")
    # The human-readable claim must name the pattern type and table.
    assert "missing index" in (row["claim"] or "").lower()


def test_missing_index_finding_subject_links_to_files_row(php_project):
    """subject_id, when populated, resolves to a real files row."""
    _run_missing_index_persist(php_project)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id FROM findings "
                "WHERE source_detector = 'missing-index' AND subject_id IS NOT NULL"
            ).fetchall()
            assert len(rows) >= 1, (
                "expected at least one missing-index finding with a resolved subject_id"
            )
            for r in rows:
                f_row = conn.execute(
                    "SELECT id, path FROM files WHERE id = ?", (r["subject_id"],)
                ).fetchone()
                assert f_row is not None, f"orphan subject_id {r['subject_id']}"
                # The file resolved must be a PHP file (the detector only
                # scans PHP source).
                assert (f_row["path"] or "").endswith(".php")
    finally:
        os.chdir(old_cwd)


def test_missing_index_rerun_upserts_not_duplicates(php_project):
    """Re-running missing-index --persist produces the same finding_id_str set."""
    _run_missing_index_persist(php_project)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'missing-index'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'missing-index'"
            ).fetchone()[0]
        assert first_count == len(first_ids), (
            "duplicate finding_id_str rows on first run"
        )

        # Second run — same fixture, same regex matches, same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["missing-index", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'missing-index'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'missing-index'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_missing_index_persist_ignores_display_filters(php_project):
    """--persist writes the full finding set regardless of --confidence filter.

    Re-running with a narrower --confidence filter must not truncate the
    registry. The persisted set is independent of the display slice —
    same discipline W102 complexity established.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        # First persist run: no filter (writes everything).
        r1 = runner.invoke(cli, ["missing-index", "--persist"])
        assert r1.exit_code == 0, r1.output

        with open_db(readonly=True) as conn:
            baseline_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'missing-index'"
            ).fetchone()[0]

        # Second persist run with a confidence filter — the display
        # slice may shrink, but the persisted row count must NOT.
        r2 = runner.invoke(
            cli, ["missing-index", "--persist", "--confidence", "high"]
        )
        assert r2.exit_code == 0, r2.output

        with open_db(readonly=True) as conn:
            filtered_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'missing-index'"
            ).fetchone()[0]
    finally:
        os.chdir(old_cwd)

    assert filtered_count == baseline_count, (
        f"--confidence filter truncated the registry: baseline={baseline_count} "
        f"filtered={filtered_count}"
    )


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_missing_index_findings_visible_via_cmd_findings_list(php_project):
    """``roam findings list --detector missing-index`` returns the rows."""
    _run_missing_index_persist(php_project)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "missing-index"]
        )
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["command"] == "findings-list"
    assert envelope["summary"]["state"] == "populated"
    assert envelope["summary"]["total_findings"] >= 1
    assert "missing-index" in envelope["summary"]["detectors"]
    assert all(r["source_detector"] == "missing-index" for r in envelope["findings"])


def test_missing_index_findings_visible_via_cmd_findings_count(php_project):
    """``roam findings count`` includes a non-zero entry for missing-index."""
    _run_missing_index_persist(php_project)
    assert_detector_visible_in_findings_count(php_project, "missing-index")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(php_project):
    """Without --persist, the standard read path stays side-effect-free."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))
        # No --persist.
        assert runner.invoke(cli, ["missing-index"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings "
                    "WHERE source_detector = 'missing-index'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may be absent on some test schemas — still
                # a "no findings emitted" outcome from this command path.
                count = 0
    finally:
        os.chdir(old_cwd)
    assert count == 0, "non-persist missing-index still wrote to findings"


def test_missing_index_persist_no_findings_table_no_crash(php_project):
    """``missing-index --persist`` degrades cleanly when findings table absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index but
    before the persist call. The detector's own JSON / text output must
    keep working.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(php_project))

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["missing-index", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
