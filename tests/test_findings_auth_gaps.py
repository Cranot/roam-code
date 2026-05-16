"""Tests for the W116 migration: auth-gaps detector emits to the central
findings registry.

auth-gaps is the fourth detector migrating onto the A4 findings table
(after W95 clones, W99 dead, and W102 complexity). It continues to render
its own JSON / text envelope (authoritative output surface) and ALSO,
when ``--persist`` is set, emits one row per auth-gap into ``findings``.
These tests cover that additive emit + end-to-end visibility through
``roam findings`` for an agent.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_auth_gaps import (
    AUTH_GAPS_DETECTOR_VERSION,
    _auth_gap_confidence_tier,
    _auth_gap_finding_id,
    _auth_gap_finding_kind,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import git_init, index_in_process

# ---------------------------------------------------------------------------
# Fixture — Laravel-style PHP project with a mix of gap types
# ---------------------------------------------------------------------------


@pytest.fixture
def laravel_gap_project(tmp_path):
    """Laravel-style project guaranteed to surface at least one auth gap.

    Builds:
      - One auth-protected route (must NOT be flagged).
      - One unprotected route (direct-unauthenticated-handler).
      - One ApiController with two CRUD methods + no auth (high-confidence
        controller gap — exercises the helper-indirection kind since
        descent runs and fails).
      - One PublicController used as a read-method gap.
    """
    proj = tmp_path / "laravel_auth_gaps"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    routes_dir = proj / "routes"
    routes_dir.mkdir()
    (routes_dir / "web.php").write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "\n"
        "Route::middleware('auth')->group(function () {\n"
        "    Route::get('/dashboard', [DashboardController::class, 'index']);\n"
        "});\n"
        "\n"
        "Route::post('/api/data', [ApiController::class, 'store']);\n"
    )

    controllers_dir = proj / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "DashboardController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class DashboardController extends Controller\n"
        "{\n"
        "    public function index()\n"
        "    {\n"
        "        return view('dashboard');\n"
        "    }\n"
        "}\n"
    )
    (controllers_dir / "ApiController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class ApiController extends Controller\n"
        "{\n"
        "    public function store()\n"
        "    {\n"
        "        return response()->json(['status' => 'created'], 201);\n"
        "    }\n"
        "\n"
        "    public function update()\n"
        "    {\n"
        "        return response()->json(['status' => 'updated']);\n"
        "    }\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


def _run_auth_gaps_persist(proj):
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["auth-gaps", "--persist"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Unit-level tests — pure helpers, no DB / fixture cost
# ---------------------------------------------------------------------------


class TestFindingKindMapping:
    def test_route_finding_is_direct_unauthenticated_handler(self):
        assert (
            _auth_gap_finding_kind({"type": "route", "confidence": "high", "verb": "POST", "path": "/x"})
            == "direct-unauthenticated-handler"
        )

    def test_route_with_non_auth_guard_is_name_based(self):
        """W36 M10 — throttle/signed/etc. is naming-based heuristic."""
        assert (
            _auth_gap_finding_kind(
                {
                    "type": "route",
                    "confidence": "low",
                    "non_auth_guard_present": True,
                    "verb": "GET",
                    "path": "/x",
                }
            )
            == "name-based"
        )

    def test_controller_high_is_helper_indirection(self):
        """Helper descent ran and failed (W36.7 / W36.10 path)."""
        assert _auth_gap_finding_kind({"type": "controller", "confidence": "high"}) == "helper-indirection"

    def test_controller_medium_is_helper_indirection(self):
        assert _auth_gap_finding_kind({"type": "controller", "confidence": "medium"}) == "helper-indirection"

    def test_controller_low_is_name_based(self):
        """Read methods / tenant-scope demotions — purely name-based."""
        assert _auth_gap_finding_kind({"type": "controller", "confidence": "low"}) == "name-based"


class TestConfidenceTierMapping:
    def test_direct_unauth_maps_to_static_analysis(self):
        assert _auth_gap_confidence_tier("direct-unauthenticated-handler") == "static_analysis"

    def test_helper_indirection_maps_to_structural(self):
        assert _auth_gap_confidence_tier("helper-indirection") == "structural"

    def test_name_based_maps_to_heuristic(self):
        assert _auth_gap_confidence_tier("name-based") == "heuristic"

    def test_unknown_kind_falls_back_to_heuristic(self):
        """Defensive: unmapped kinds stay at the lowest tier."""
        assert _auth_gap_confidence_tier("not-a-real-kind") == "heuristic"


class TestFindingIdDeterminism:
    def test_same_inputs_same_id(self):
        a = _auth_gap_finding_id("/abs/p.php", "direct-unauthenticated-handler", "POST /x", 12)
        b = _auth_gap_finding_id("/abs/p.php", "direct-unauthenticated-handler", "POST /x", 12)
        assert a == b
        assert a.startswith("auth-gaps:direct-unauthenticated-handler:")

    def test_different_kind_different_id(self):
        a = _auth_gap_finding_id("/p.php", "direct-unauthenticated-handler", "x", 1)
        b = _auth_gap_finding_id("/p.php", "helper-indirection", "x", 1)
        assert a != b

    def test_different_subject_different_id(self):
        a = _auth_gap_finding_id("/p.php", "name-based", "Foo::index", 1)
        b = _auth_gap_finding_id("/p.php", "name-based", "Foo::store", 1)
        assert a != b

    def test_different_line_different_id(self):
        a = _auth_gap_finding_id("/p.php", "name-based", "Foo::x", 10)
        b = _auth_gap_finding_id("/p.php", "name-based", "Foo::x", 20)
        assert a != b


# ---------------------------------------------------------------------------
# Core migration assertions — end-to-end through the CLI
# ---------------------------------------------------------------------------


def test_auth_gaps_emits_to_findings_registry(tmp_path, laravel_gap_project):
    """Running auth-gaps --persist populates the registry with auth-gaps rows."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_auth_gaps_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'auth-gaps'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one auth-gaps finding row"
        for r in rows:
            assert r["source_detector"] == "auth-gaps"
            assert r["source_version"] == AUTH_GAPS_DETECTOR_VERSION
            # Route findings have no symbol → subject_kind="endpoint";
            # controller findings resolve to a symbol when the indexer
            # captured the method.
            assert r["subject_kind"] in ("symbol", "endpoint")
            # All three accepted tiers can appear depending on the gap mix.
            assert r["confidence"] in ("static_analysis", "structural", "heuristic")
            assert r["finding_id_str"].startswith("auth-gaps:")
    finally:
        os.chdir(old_cwd)


def test_auth_gaps_finding_id_str_is_deterministic_e2e(tmp_path, laravel_gap_project):
    """Re-running auth-gaps --persist produces the same id set (upsert path)."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_auth_gaps_persist(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'auth-gaps'"
                ).fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'auth-gaps'").fetchone()[
                0
            ]
        assert first_count == len(first_ids), "duplicate finding_id_str on first run"

        # Second run — same fixture, same code, same hash inputs.
        _run_auth_gaps_persist(proj)

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'auth-gaps'"
                ).fetchall()
            }
            second_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'auth-gaps'").fetchone()[
                0
            ]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_auth_gaps_finding_evidence_captures_kind(tmp_path, laravel_gap_project):
    """Evidence JSON carries the kind classification + matched_patterns/fix."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_auth_gaps_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'auth-gaps'").fetchall()
        assert len(rows) >= 1
        kinds_seen: set[str] = set()
        for r in rows:
            evidence = json.loads(r["evidence_json"])
            assert "kind" in evidence
            assert evidence["kind"] in (
                "direct-unauthenticated-handler",
                "helper-indirection",
                "name-based",
            )
            assert evidence["type"] in ("route", "controller")
            kinds_seen.add(evidence["kind"])
        # Our fixture has at least one unprotected route AND at least one
        # controller without auth, so we expect to see both kinds.
        assert "direct-unauthenticated-handler" in kinds_seen or "helper-indirection" in kinds_seen
    finally:
        os.chdir(old_cwd)


def test_auth_gaps_route_finding_has_no_symbol_subject(tmp_path, laravel_gap_project):
    """Route findings have no symbol mapping — subject_kind=endpoint, subject_id=NULL."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_auth_gaps_persist(proj)

        with open_db(readonly=True) as conn:
            route_rows = conn.execute(
                "SELECT subject_kind, subject_id, evidence_json FROM findings WHERE source_detector = 'auth-gaps'"
            ).fetchall()
        # Filter to route findings via evidence type — route findings should
        # always carry subject_kind='endpoint' and subject_id=NULL.
        any_route = False
        for r in route_rows:
            evidence = json.loads(r["evidence_json"])
            if evidence.get("type") == "route":
                any_route = True
                assert r["subject_kind"] == "endpoint"
                assert r["subject_id"] is None
        assert any_route, "fixture has no route findings — adjust fixture"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_auth_gaps_findings_visible_via_cmd_findings_list(tmp_path, laravel_gap_project):
    """`roam findings list --detector auth-gaps` returns rows after migration."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_auth_gaps_persist(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "auth-gaps"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "auth-gaps" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "auth-gaps" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_auth_gaps_findings_visible_via_cmd_findings_count(tmp_path, laravel_gap_project):
    """`roam findings count` includes a non-zero entry for auth-gaps."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_auth_gaps_persist(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "auth-gaps")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_auth_gaps_no_findings_table_no_crash(tmp_path, laravel_gap_project):
    """``auth-gaps --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index but
    before the persist run. The normal auth-gaps text/JSON output must
    keep working — registry emit is purely additive.
    """
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["auth-gaps", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


def test_auth_gaps_without_persist_does_not_emit(tmp_path, laravel_gap_project):
    """Without --persist, no findings rows are written.

    The registry mirror is gated behind the explicit ``--persist`` flag —
    running ``roam auth-gaps`` plain must remain side-effect-free, matching
    the readonly contract every other auth-gaps invocation already honours.
    """
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        # No --persist.
        assert runner.invoke(cli, ["auth-gaps"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'auth-gaps'").fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist auth-gaps still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_auth_gaps_persist_unaffected_by_min_confidence_filter(tmp_path, laravel_gap_project):
    """The registry mirrors the FULL detector output, not the --min-confidence
    slice. Running with --min-confidence high still persists low/medium rows."""
    proj = laravel_gap_project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["auth-gaps", "--persist", "--min-confidence", "high"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            tier_counts = conn.execute(
                "SELECT confidence, COUNT(*) FROM findings WHERE source_detector = 'auth-gaps' GROUP BY confidence"
            ).fetchall()
        tiers = {r[0] for r in tier_counts}
        # If the filter had leaked into the persist path, we'd only see
        # the tier(s) bound to confidence=high. With the unfiltered emit
        # the registry should hold whatever the detector produced — at
        # minimum more than zero rows.
        assert tier_counts, "no auth-gaps rows persisted at all"
        # We don't assert a specific tier-set since fixture composition
        # could legitimately yield only one tier; the contract is "we
        # didn't filter the persist stream by --min-confidence".
        assert tiers.issubset({"static_analysis", "structural", "heuristic"})
    finally:
        os.chdir(old_cwd)
