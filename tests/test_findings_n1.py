"""Tests for the W110 follow-up: n1 detector emits to the central
findings registry.

The n1 detector is the fourth detector migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, and ``complexity``
in W102). It continues to render its own JSON / text envelopes
(authoritative output surface) and ALSO, when ``--persist`` is set,
emits one row per implicit N+1 pattern into ``findings``. These tests
cover that additive emit and the end-to-end visibility through
``roam findings`` for an agent.

Note on fixtures: n1 detection requires a tree-sitter parse of PHP /
Python / Ruby / Java sources plus a non-trivial graph of model →
accessor → relationship edges. Reliably reproducing that across CI
environments is tricky (parser variance, framework heuristic
sensitivity), so the bulk of the migration assertions inject synthetic
finding dicts directly into :func:`_emit_n1_findings`. A separate
smoke test runs the full ``roam n1 --persist`` end-to-end on the
canonical Laravel fixture and asserts the command exits 0 — registry
rows are checked when present but the test does not fail if the
detector finds nothing (matches the "Never N/A without running it"
operational rule from CLAUDE.md: an empty result is legitimate signal,
not a test failure).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_n1 import (
    N1_DETECTOR_VERSION,
    _emit_n1_findings,
    _n1_finding_id,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import index_in_process

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _laravel_project(tmp_path):
    """Laravel-style PHP project matching the canonical n1 fixture.

    Mirrors ``tests/test_n1.py::laravel_project`` so the smoke test
    here exercises the same well-trodden detector path. The detector
    may or may not surface a finding depending on parser variance —
    the smoke assertion only requires the command exits 0.
    """
    proj = tmp_path / "laravel_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    models_dir = proj / "app" / "Models"
    models_dir.mkdir(parents=True)
    (models_dir / "Order.php").write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "\n"
        "class Order extends Model {\n"
        "    protected $fillable = ['total', 'status'];\n"
        "    protected $appends = ['total_display', 'item_count'];\n"
        "\n"
        "    public function getTotalDisplayAttribute() {\n"
        "        return '$' . number_format($this->total, 2);\n"
        "    }\n"
        "\n"
        "    public function getItemCountAttribute() {\n"
        "        return $this->items()->count();\n"
        "    }\n"
        "\n"
        "    public function items() {\n"
        "        return $this->hasMany(OrderItem::class);\n"
        "    }\n"
        "\n"
        "    public function user() {\n"
        "        return $this->belongsTo(User::class);\n"
        "    }\n"
        "}\n"
    )
    (models_dir / "OrderItem.php").write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "\n"
        "class OrderItem extends Model {\n"
        "    protected $fillable = ['order_id', 'product_id', 'quantity', 'price'];\n"
        "\n"
        "    public function order() {\n"
        "        return $this->belongsTo(Order::class);\n"
        "    }\n"
        "}\n"
    )

    controllers_dir = proj / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "use App\\Models\\Order;\n"
        "\n"
        "class OrderController extends Controller {\n"
        "    public function index() {\n"
        "        return Order::paginate(20);\n"
        "    }\n"
        "\n"
        "    public function show($id) {\n"
        "        return Order::find($id);\n"
        "    }\n"
        "}\n"
    )

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True)
    index_in_process(proj)
    return proj


def _python_only_project(tmp_path):
    """A pure Python project with no ORM models — n1 finds nothing."""
    proj = tmp_path / "python_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def compute(x):\n    return x * 2\n\ndef main():\n    return compute(21)\n")
    (proj / "utils.py").write_text("def format_value(v):\n    return str(v)\n")
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True)
    index_in_process(proj)
    return proj


def _synthetic_n1_finding(
    model_name: str = "App\\Models\\Order",
    accessor_name: str = "getItemCountAttribute",
    accessor_location: str = "app/Models/Order.php:13",
    appended: str = "item_count",
    relationship: str = "items",
    confidence: str = "high",
    io_type: str = "relationship (hasMany)",
) -> dict:
    """Build one synthetic n1-finding dict in the shape ``analyze_n1`` produces.

    The shape mirrors the actual analyzer output. ``_emit_n1_findings``
    consumes this dict directly, so the synthetic injection exercises
    the same code path the live detector does without depending on
    PHP-parser variance.
    """
    return {
        "model_name": model_name,
        "model_location": "app/Models/Order.php:5",
        "accessor_name": accessor_name,
        "accessor_location": accessor_location,
        "appended_attribute": appended,
        "relationship": relationship,
        "io_type": io_type,
        "eager_loaded": False,
        "confidence": confidence,
        "severity": "per-item query on serialization",
        "collection_contexts": [
            {
                "location": "app/Http/Controllers/OrderController.php:6",
                "type": "controller",
                "symbol": "OrderController::index",
            }
        ],
        "suggestion": (
            f"Add '{relationship}' to eagerLoad in config/resources.php, "
            f"or add '{relationship}' to $with on {model_name}, "
            f"or use ::with('{relationship}') in the controller query"
        ),
    }


# ---------------------------------------------------------------------------
# Unit tests on the helpers
# ---------------------------------------------------------------------------


def test_n1_finding_id_is_deterministic():
    """_n1_finding_id returns the same id on repeated input."""
    a = _n1_finding_id(
        "App\\Models\\Order",
        "getItemCountAttribute",
        "items",
        "item_count",
    )
    b = _n1_finding_id(
        "App\\Models\\Order",
        "getItemCountAttribute",
        "items",
        "item_count",
    )
    assert a == b
    assert a.startswith("n1:pattern:")

    # Different inputs → different ids.
    assert (
        _n1_finding_id(
            "App\\Models\\Other",
            "getItemCountAttribute",
            "items",
            "item_count",
        )
        != a
    )
    assert (
        _n1_finding_id(
            "App\\Models\\Order",
            "getOther",
            "items",
            "item_count",
        )
        != a
    )
    assert (
        _n1_finding_id(
            "App\\Models\\Order",
            "getItemCountAttribute",
            "user",
            "item_count",
        )
        != a
    )
    assert (
        _n1_finding_id(
            "App\\Models\\Order",
            "getItemCountAttribute",
            "items",
            "other_attr",
        )
        != a
    )


def test_emit_n1_findings_writes_rows(tmp_path):
    """_emit_n1_findings writes one row per synthetic finding."""
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        findings_in = [
            _synthetic_n1_finding(
                accessor_name="getItemCountAttribute",
                relationship="items",
                appended="item_count",
            ),
            _synthetic_n1_finding(
                accessor_name="getUserDisplayAttribute",
                relationship="user",
                appended="user_display",
                confidence="medium",
            ),
        ]
        with open_db(readonly=False) as conn:
            emitted = _emit_n1_findings(conn, findings_in)
            conn.commit()
        assert emitted == 2

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'n1' "
                "ORDER BY finding_id_str"
            ).fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["source_detector"] == "n1"
            assert r["source_version"] == N1_DETECTOR_VERSION
            assert r["subject_kind"] == "symbol"
            # All n1 findings are structural by design (deterministic
            # graph pattern). The high / medium / low refactor-priority
            # signal lives inside the evidence payload, not on the
            # registry-level confidence tier.
            assert r["confidence"] == "structural"
            assert r["finding_id_str"].startswith("n1:pattern:")
    finally:
        os.chdir(old_cwd)


def test_emit_n1_findings_evidence_carries_payload(tmp_path):
    """The evidence JSON carries the analyzer's full finding payload."""
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        finding = _synthetic_n1_finding(
            accessor_name="getItemCountAttribute",
            relationship="items",
            appended="item_count",
            confidence="high",
            io_type="relationship (hasMany)",
        )
        with open_db(readonly=False) as conn:
            _emit_n1_findings(conn, [finding])
            conn.commit()
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings WHERE source_detector = 'n1' LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        # Required evidence keys for an agent triaging an N+1 finding.
        for k in (
            "model_name",
            "accessor_name",
            "appended_attribute",
            "relationship",
            "io_type",
            "eager_loaded",
            "confidence_label",
            "collection_contexts",
            "suggestion",
        ):
            assert k in evidence, f"evidence missing key {k!r}"
        assert evidence["relationship"] == "items"
        assert evidence["appended_attribute"] == "item_count"
        # The pre-collapse confidence label is preserved for triage —
        # consumers that want the original high/medium/low signal can
        # still read it from evidence even though the registry tier
        # itself is uniform.
        assert evidence["confidence_label"] == "high"
        assert evidence["eager_loaded"] is False
        # The human-form claim names the model and the relationship.
        claim_lower = (row["claim"] or "").lower()
        assert "n+1" in claim_lower
        assert "items" in claim_lower
    finally:
        os.chdir(old_cwd)


def test_emit_n1_findings_idempotent_on_rerun(tmp_path):
    """Re-running emit on the same synthetic input upserts (no duplicates)."""
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        finding = _synthetic_n1_finding()
        with open_db(readonly=False) as conn:
            _emit_n1_findings(conn, [finding])
            conn.commit()
        with open_db(readonly=False) as conn:
            _emit_n1_findings(conn, [finding])
            conn.commit()
        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'n1'").fetchone()[0]
        assert count == 1, "rerun produced duplicate registry rows"
    finally:
        os.chdir(old_cwd)


def test_emit_n1_findings_zero_input_returns_zero(tmp_path):
    """Empty findings list is a no-op — no rows, no exception."""
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            emitted = _emit_n1_findings(conn, [])
            conn.commit()
        assert emitted == 0
        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'n1'").fetchone()[0]
        assert count == 0
    finally:
        os.chdir(old_cwd)


def test_emit_n1_findings_skips_malformed_rows(tmp_path):
    """Findings missing model/accessor/relationship are silently skipped."""
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        well_formed = _synthetic_n1_finding(
            accessor_name="getOkAttribute",
            relationship="ok_rel",
            appended="ok_attr",
        )
        missing_model = dict(well_formed, model_name="")
        missing_accessor = dict(well_formed, accessor_name="")
        missing_rel = dict(well_formed, relationship="")
        with open_db(readonly=False) as conn:
            emitted = _emit_n1_findings(
                conn,
                [missing_model, missing_accessor, missing_rel, well_formed],
            )
            conn.commit()
        # Only the well-formed row should land.
        assert emitted == 1
        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'n1'").fetchone()[0]
        assert count == 1
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# End-to-end CLI tests (`roam n1 --persist`)
# ---------------------------------------------------------------------------


def test_n1_persist_flag_accepted_on_laravel(tmp_path):
    """``roam n1 --persist`` on the canonical Laravel fixture exits 0.

    Whether the detector surfaces findings depends on parser variance
    across PHP grammars and the Laravel-specific heuristics. The
    assertion here mirrors the existing ``test_n1`` smoke tests — exit
    0 is the bar; any rows written are an additional bonus.
    """
    proj = _laravel_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["n1", "--persist"])
        assert result.exit_code == 0, result.output

        # If the detector found rows, every one must be properly stamped.
        with open_db(readonly=True) as conn:
            try:
                rows = conn.execute(
                    "SELECT source_detector, source_version, subject_kind, "
                    "       confidence, finding_id_str "
                    "FROM findings WHERE source_detector = 'n1'"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        for r in rows:
            assert r["source_detector"] == "n1"
            assert r["source_version"] == N1_DETECTOR_VERSION
            assert r["subject_kind"] == "symbol"
            assert r["confidence"] == "structural"
            assert r["finding_id_str"].startswith("n1:pattern:")
    finally:
        os.chdir(old_cwd)


def test_n1_persist_zero_findings_python_project(tmp_path):
    """On a pure-Python project, n1 finds nothing — and --persist still exits 0.

    The "Never N/A without running it" operational rule: empty output
    is a legitimate signal. The command must succeed and write zero
    n1 rows to the registry. No exception escapes ``_emit_n1_findings``.
    """
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["n1", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'n1'").fetchone()[0]
        assert count == 0
    finally:
        os.chdir(old_cwd)


def test_n1_without_persist_does_not_emit_findings(tmp_path):
    """Without --persist, no findings rows are written.

    Matches the readonly contract — the registry mirror is gated behind
    the explicit ``--persist`` flag. Running plain ``roam n1`` must
    remain side-effect-free.
    """
    proj = _laravel_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        # No --persist.
        result = runner.invoke(cli, ["n1"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'n1'").fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist n1 still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_n1_persist_no_findings_table_no_crash(tmp_path):
    """``roam n1 --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after index
    but before the persist call. The standard n1 output (text / JSON)
    must keep working — the command exits 0 and writes no registry
    rows.
    """
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["n1", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_n1_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector n1` returns rows after synthetic injection.

    Uses the synthetic-injection path so the test is independent of
    PHP-parser variance — the registry-visibility contract holds
    whether the live detector ran or rows were emitted in another way.
    """
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            _emit_n1_findings(conn, [_synthetic_n1_finding()])
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "n1"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "n1" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "n1" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_n1_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for n1 after synthetic injection."""
    proj = _python_only_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=False) as conn:
            _emit_n1_findings(conn, [_synthetic_n1_finding()])
            conn.commit()
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "n1")
