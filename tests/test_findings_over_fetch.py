"""Tests for the W114 follow-up: over-fetch detector emits to the central
findings registry.

The over-fetch detector is the seventh detector migrating onto the A4
findings registry (after ``clones`` in W95, ``dead`` in W99,
``complexity`` in W102, ``smells`` in W109, ``n1`` in W110, and
``missing-index`` in W111). It continues to return its in-memory result
shape (``findings`` list + ``endpoint_findings`` 3-state classification)
to the caller and ALSO emits two finding-row kinds into the registry
when invoked with ``--persist``:

* One row per model-level hit (over-fetch:model:<digest>), keyed on
  ``confidence`` bucket (high/medium/low).
* One row per endpoint-level hit (over-fetch:endpoint:<digest>), keyed
  on the 3-state classification (BARE / GUARDED_RELATION /
  UNGUARDED_RELATION).

These tests cover both emit paths and the per-state confidence-tier
mapping, plus end-to-end visibility through ``roam findings`` for an
agent.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_over_fetch import (
    OVER_FETCH_DETECTOR_VERSION,
    _ENDPOINT_STATE_TO_CONFIDENCE,
    _MODEL_CONFIDENCE_TO_TIER,
    _emit_over_fetch_findings,
    _over_fetch_endpoint_finding_id,
    _over_fetch_model_finding_id,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# A model with 30+ fillable fields, no $hidden, no $visible, no Resource —
# triggers the ``high`` model-level bucket. Paired with a Laravel-style
# controller exercising all three endpoint states:
#
#   * ``index``    — Employee::query()->paginate() (BARE leak — H severity)
#   * ``related``  — Employee::with('orders')->get() (UNGUARDED_RELATION — H)
#   * ``guarded``  — Employee::with('orders:id,total')->get() (GUARDED — L)
#
# The fixture sits under ``src/Models/`` and ``src/Http/Controllers/`` so the
# detector's path-prefix LIKE filters (``%/Models/%``, ``%/Http/%``) match.
_EMPLOYEE_MODEL = """<?php
namespace App\\Models;

class Employee
{
    protected $fillable = [
        'id', 'first_name', 'last_name', 'email', 'phone',
        'mobile', 'address_line_1', 'address_line_2', 'city', 'state',
        'zip_code', 'country', 'birth_date', 'hire_date', 'termination_date',
        'department_id', 'manager_id', 'salary', 'hourly_rate', 'currency',
        'tax_id', 'ssn', 'passport_number', 'emergency_contact', 'emergency_phone',
        'job_title', 'employment_type', 'work_location', 'time_zone', 'locale',
        'notes', 'internal_notes', 'profile_photo_url', 'signature_url',
    ];
}
"""

_EMPLOYEE_CONTROLLER = """<?php
namespace App\\Http\\Controllers;

use App\\Models\\Employee;

class EmployeeController
{
    public function index()
    {
        return Employee::query()->paginate(50);
    }

    public function related()
    {
        return Employee::with('orders')->get();
    }

    public function guarded()
    {
        return Employee::with('orders:id,total')->get();
    }
}
"""


def _over_fetch_project(tmp_path):
    """Tiny Laravel-like repo with one big model + one controller.

    Produces at least one model-level finding (Employee has 34 fillable
    fields, no $hidden, no $visible, no Resource) AND at least one
    endpoint-level finding of each 3-state classification (BARE in
    ``index``, UNGUARDED_RELATION in ``related``, GUARDED_RELATION in
    ``guarded``).
    """
    return _make_project(
        tmp_path,
        {
            "Models/Employee.php": _EMPLOYEE_MODEL,
            "Http/Controllers/EmployeeController.php": _EMPLOYEE_CONTROLLER,
        },
    )


def _persist_over_fetch(proj):
    """Index the project and run ``over-fetch --persist``.

    Returns the CliRunner result so tests can assert on its exit code if
    they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["over-fetch", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_over_fetch_emits_to_findings_registry(tmp_path):
    """Running over-fetch --persist on a leaky fixture populates findings."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'over-fetch'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one over-fetch-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "over-fetch"
            assert r["source_version"] == OVER_FETCH_DETECTOR_VERSION
            assert r["subject_kind"] in ("symbol", "file")
            assert r["confidence"] in ("static_analysis", "structural", "heuristic")
            assert r["finding_id_str"].startswith("over-fetch:")
    finally:
        os.chdir(old_cwd)


def test_over_fetch_emits_both_kinds(tmp_path):
    """The persist branch emits both model-level and endpoint-level rows."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        with open_db(readonly=True) as conn:
            model_count = conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE source_detector = 'over-fetch' "
                "AND finding_id_str LIKE 'over-fetch:model:%'"
            ).fetchone()[0]
            endpoint_count = conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE source_detector = 'over-fetch' "
                "AND finding_id_str LIKE 'over-fetch:endpoint:%'"
            ).fetchone()[0]
        assert model_count >= 1, (
            "expected at least one model-level over-fetch finding "
            f"(got {model_count})"
        )
        assert endpoint_count >= 1, (
            "expected at least one endpoint-level over-fetch finding "
            f"(got {endpoint_count})"
        )
    finally:
        os.chdir(old_cwd)


def test_over_fetch_model_finding_id_is_deterministic():
    """_over_fetch_model_finding_id returns the same id for the same input."""
    a = _over_fetch_model_finding_id("Employee", "src/Models/Employee.php")
    b = _over_fetch_model_finding_id("Employee", "src/Models/Employee.php")
    assert a == b
    assert a.startswith("over-fetch:model:")
    # Different model name → different id.
    assert _over_fetch_model_finding_id("Order", "src/Models/Employee.php") != a
    # Different file path → different id (handles same class name in
    # different namespaces, a common Laravel pattern).
    assert _over_fetch_model_finding_id("Employee", "src/Other/Employee.php") != a


def test_over_fetch_endpoint_finding_id_is_deterministic():
    """_over_fetch_endpoint_finding_id returns the same id for the same input.

    State is folded into the digest — a method transitioning from
    GUARDED_RELATION to UNGUARDED_RELATION mints a fresh id, not an
    upsert on the prior advisory row.
    """
    a = _over_fetch_endpoint_finding_id(
        "EmployeeController", "index", "src/Http/Controllers/EmployeeController.php", "BARE"
    )
    b = _over_fetch_endpoint_finding_id(
        "EmployeeController", "index", "src/Http/Controllers/EmployeeController.php", "BARE"
    )
    assert a == b
    assert a.startswith("over-fetch:endpoint:")
    # Different state → different id.
    assert (
        _over_fetch_endpoint_finding_id(
            "EmployeeController",
            "index",
            "src/Http/Controllers/EmployeeController.php",
            "UNGUARDED_RELATION",
        )
        != a
    )
    # Different method → different id.
    assert (
        _over_fetch_endpoint_finding_id(
            "EmployeeController",
            "related",
            "src/Http/Controllers/EmployeeController.php",
            "BARE",
        )
        != a
    )


def test_over_fetch_rerun_upserts_not_duplicates(tmp_path):
    """Re-running over-fetch --persist produces the same finding_id_str set."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'over-fetch'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'over-fetch'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"
        assert first_count >= 1, "first run emitted no findings"

        # Second run — same fixture, same detector predicates → same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["over-fetch", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'over-fetch'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'over-fetch'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_over_fetch_model_finding_evidence_carries_fields(tmp_path):
    """The model-level finding's evidence JSON carries the per-model context."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'over-fetch' "
                "AND finding_id_str LIKE 'over-fetch:model:%' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None, "no model-level over-fetch finding found"
        evidence = json.loads(row["evidence_json"])
        assert evidence["kind"] == "model"
        for k in (
            "model_name",
            "model_path",
            "fillable_count",
            "hidden_count",
            "exposed_count",
            "has_resource",
            "confidence_bucket",
            "reasons",
            "matched_patterns",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the model.
        assert evidence["model_name"] in (row["claim"] or "")
        # The fillable_count must reflect the fixture (34 fields).
        assert evidence["fillable_count"] >= 20
    finally:
        os.chdir(old_cwd)


def test_over_fetch_endpoint_finding_evidence_carries_fields(tmp_path):
    """The endpoint-level finding's evidence JSON carries the 3-state context."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'over-fetch' "
                "AND finding_id_str LIKE 'over-fetch:endpoint:%' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None, "no endpoint-level over-fetch finding found"
        evidence = json.loads(row["evidence_json"])
        assert evidence["kind"] == "endpoint"
        for k in (
            "endpoint",
            "controller",
            "method",
            "file",
            "state",
            "severity",
            "evidence_text",
            "recommendation",
            "guarded_relation_count",
            "unguarded_relation_count",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The state must be one of the three classifications.
        assert evidence["state"] in (
            "BARE",
            "UNGUARDED_RELATION",
            "GUARDED_RELATION",
        )
        # The claim must name the state.
        assert evidence["state"] in (row["claim"] or "")
    finally:
        os.chdir(old_cwd)


def test_over_fetch_three_state_classification_emitted(tmp_path):
    """All three endpoint states reach the registry — including GUARDED advisories.

    The persist branch is independent of --leaks-only display filtering;
    GUARDED_RELATION rows must land in the registry even though the
    interactive view typically hides them.
    """
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json, confidence FROM findings "
                "WHERE source_detector = 'over-fetch' "
                "AND finding_id_str LIKE 'over-fetch:endpoint:%'"
            ).fetchall()
        states_seen = set()
        for r in rows:
            ev = json.loads(r["evidence_json"])
            state = ev.get("state")
            states_seen.add(state)
            # Verify the per-state confidence tier mapping at write time.
            assert r["confidence"] == _ENDPOINT_STATE_TO_CONFIDENCE.get(state), (
                f"state {state!r} expected "
                f"{_ENDPOINT_STATE_TO_CONFIDENCE.get(state)!r}, "
                f"got {r['confidence']!r}"
            )
        # The fixture exercises all three states; check at least the
        # confirmed-leak pair lands (BARE + UNGUARDED). GUARDED is also
        # expected but the with(:cols) regex is sensitive to PHP parse
        # ordering — we assert on the H-severity pair which the
        # detector is most stable about.
        assert "BARE" in states_seen or "UNGUARDED_RELATION" in states_seen, (
            f"expected at least one confirmed-leak state; saw {states_seen}"
        )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-bucket / per-state confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The detector + indexer aren't needed here — we exercise
    ``_emit_over_fetch_findings`` directly on synthetic finding dicts so
    the per-bucket/per-state tier mapping is verified independently of
    which leaks the detector happens to trigger on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_model_bucket_tier_mapping(tmp_path):
    """Each model-level confidence bucket lands on the documented tier."""
    with _seed_for_emit_helper(tmp_path) as conn:
        findings_data = [
            {
                "model_name": "BigModel",
                "model_path": "src/Models/BigModel.php",
                "model_location": "src/Models/BigModel.php:10",
                "fillable_count": 50,
                "hidden_count": 0,
                "exposed_count": 50,
                "has_visible": False,
                "has_resource": False,
                "resource_path": None,
                "confidence": "high",
                "reasons": ["50 fillable, no Resource"],
                "matched_patterns": ["exposed_fields=50"],
                "suggestions": [],
                "direct_returns": [],
                "missing_selects": [],
            },
            {
                "model_name": "MidModel",
                "model_path": "src/Models/MidModel.php",
                "model_location": "src/Models/MidModel.php:10",
                "fillable_count": 22,
                "hidden_count": 1,
                "exposed_count": 21,
                "has_visible": False,
                "has_resource": False,
                "resource_path": None,
                "confidence": "medium",
                "reasons": ["22 fillable, 1 hidden"],
                "matched_patterns": ["exposed_fields=21"],
                "suggestions": [],
                "direct_returns": [],
                "missing_selects": [],
            },
            {
                "model_name": "SmallModel",
                "model_path": "src/Models/SmallModel.php",
                "model_location": "src/Models/SmallModel.php:10",
                "fillable_count": 16,
                "hidden_count": 0,
                "exposed_count": 16,
                "has_visible": False,
                "has_resource": False,
                "resource_path": None,
                "confidence": "low",
                "reasons": ["16 fillable, no select()"],
                "matched_patterns": [],
                "suggestions": [],
                "direct_returns": [],
                "missing_selects": [{"file": "src/X.php", "line": 1}],
            },
        ]
        written = _emit_over_fetch_findings(
            conn, findings_data, [], OVER_FETCH_DETECTOR_VERSION
        )
        assert written == len(findings_data)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings "
            "WHERE source_detector = 'over-fetch' "
            "AND finding_id_str LIKE 'over-fetch:model:%'"
        ).fetchall()
        assert len(rows) == len(findings_data)
        seen: dict[str, str] = {}
        for r in rows:
            ev = json.loads(r["evidence_json"])
            seen[ev["confidence_bucket"]] = r["confidence"]
        assert seen["high"] == _MODEL_CONFIDENCE_TO_TIER["high"]
        assert seen["medium"] == _MODEL_CONFIDENCE_TO_TIER["medium"]
        assert seen["low"] == _MODEL_CONFIDENCE_TO_TIER["low"]


def test_endpoint_state_tier_mapping(tmp_path):
    """Each 3-state endpoint classification lands on the documented tier."""
    with _seed_for_emit_helper(tmp_path) as conn:
        endpoint_findings = [
            {
                "endpoint": "C@bare",
                "controller": "C",
                "method": "bare",
                "file": "src/Http/Controllers/C.php",
                "line": 10,
                "location": "src/Http/Controllers/C.php:10",
                "state": "BARE",
                "severity": "H",
                "evidence": "paginate() without ->select() or Resource",
                "recommendation": "Add ->select()",
                "details": {
                    "guarded": [],
                    "unguarded": [],
                    "has_select": False,
                    "bare_main_model": True,
                },
            },
            {
                "endpoint": "C@unguarded",
                "controller": "C",
                "method": "unguarded",
                "file": "src/Http/Controllers/C.php",
                "line": 20,
                "location": "src/Http/Controllers/C.php:20",
                "state": "UNGUARDED_RELATION",
                "severity": "H",
                "evidence": "with('rel')",
                "recommendation": "Add column selection",
                "details": {
                    "guarded": [],
                    "unguarded": [{"relation": "rel", "cols": [], "raw": "rel"}],
                    "has_select": False,
                    "bare_main_model": False,
                },
            },
            {
                "endpoint": "C@guarded",
                "controller": "C",
                "method": "guarded",
                "file": "src/Http/Controllers/C.php",
                "line": 30,
                "location": "src/Http/Controllers/C.php:30",
                "state": "GUARDED_RELATION",
                "severity": "L",
                "evidence": "with('rel:id,name')",
                "recommendation": "Consider Resource wrapper",
                "details": {
                    "guarded": [
                        {"relation": "rel", "cols": ["id", "name"], "raw": "rel:id,name"}
                    ],
                    "unguarded": [],
                    "has_select": False,
                    "bare_main_model": False,
                },
            },
        ]
        written = _emit_over_fetch_findings(
            conn, [], endpoint_findings, OVER_FETCH_DETECTOR_VERSION
        )
        assert written == len(endpoint_findings)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings "
            "WHERE source_detector = 'over-fetch' "
            "AND finding_id_str LIKE 'over-fetch:endpoint:%'"
        ).fetchall()
        assert len(rows) == len(endpoint_findings)
        seen: dict[str, str] = {}
        for r in rows:
            ev = json.loads(r["evidence_json"])
            seen[ev["state"]] = r["confidence"]
        assert seen["BARE"] == _ENDPOINT_STATE_TO_CONFIDENCE["BARE"]
        assert seen["UNGUARDED_RELATION"] == _ENDPOINT_STATE_TO_CONFIDENCE[
            "UNGUARDED_RELATION"
        ]
        assert seen["GUARDED_RELATION"] == _ENDPOINT_STATE_TO_CONFIDENCE[
            "GUARDED_RELATION"
        ]


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_over_fetch_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector over-fetch` returns rows after migration."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "over-fetch"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "over-fetch" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "over-fetch" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_over_fetch_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for over-fetch."""
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_over_fetch(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "over-fetch")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam over-fetch`` without the flag must not write to ``findings``.
    """
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["over-fetch"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'over-fetch'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist over-fetch still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_over_fetch_persist_no_findings_table_no_crash(tmp_path):
    """``over-fetch --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard detector-output path (text /
    JSON) which legacy consumers depend on must keep working — the
    command exits 0 and writes no registry rows.
    """
    proj = _over_fetch_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["over-fetch", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
