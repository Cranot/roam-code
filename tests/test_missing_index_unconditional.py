"""Tests for predicate-classification-driven composite-index column ordering.

External dogfood feedback (212-eval corpus) reported that ``missing-index``
ranked ``->when()``-conditional columns BEFORE ``->whereIn()`` /
unconditional ``->where()`` columns that always fire — which is exactly
backwards for B-tree index efficacy. A composite index can only seek on
leading columns that are guaranteed to be supplied; conditional columns
must trail.

These tests exercise the new predicate classifier:

  - unconditional equality (where / whereIn at top level)   leads
  - conditional equality (inside ->when())                  middle
  - range predicates (whereBetween, where >, whereDate)     trails
  - sort columns (orderBy)                                  last

Each fixture is a small synthetic PHP project (migration + controller)
exercised through the CLI so the full extractor + ranker path is covered.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Migration shared by most fixtures — `forms` table with NO composite index
# on any combination of (employee_id, status, form_type, company_id, ...) so
# every multi-column query is guaranteed to surface a composite_where finding.
# ---------------------------------------------------------------------------

_FORMS_MIGRATION_PHP = """\
<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

class CreateFormsTable extends Migration
{
    public function up()
    {
        Schema::create('forms', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('employee_id');
            $table->unsignedBigInteger('company_id');
            $table->string('status');
            $table->string('form_type');
            $table->string('priority');
            $table->string('type');
            $table->date('due_date');
            $table->timestamps();
        });
    }

    public function down()
    {
        Schema::dropIfExists('forms');
    }
}
"""


def _write_project(tmp_path, controller_php: str, name: str = "app") -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\nvendor/\n")

    migrations = proj / "database" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "2024_01_01_000000_create_forms_table.php").write_text(_FORMS_MIGRATION_PHP)

    controllers = proj / "app" / "Http" / "Controllers"
    controllers.mkdir(parents=True)
    (controllers / "FormController.php").write_text(controller_php)

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def cli_runner():
    # Click 8.3+ removed mix_stderr; use result.stderr_bytes manually if needed
    return CliRunner()


def _run_missing_index(cli_runner, proj, monkeypatch) -> dict:
    monkeypatch.chdir(proj)
    result = invoke_cli(cli_runner, ["missing-index", "--limit", "200"], cwd=proj, json_mode=True)
    return parse_json_output(result, "missing-index")


def _composite_findings(data: dict) -> list[dict]:
    """Return the `value` payloads of composite_where findings."""
    out = []
    for f in data["findings"]:
        v = f["value"]
        if v.get("pattern_type") == "composite_where":
            out.append(v)
    return out


# ===========================================================================
# Test 1 — eager-resolved whereIn must lead conditional ->when() columns
# ===========================================================================


_CONTROLLER_WHERE_IN_LEADS = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\Form;
use Illuminate\\Http\\Request;

class FormController extends Controller
{
    public function index(Request $request)
    {
        $companyEmployeeIds = [1, 2, 3];
        $status = $request->input('status');
        $formType = $request->input('form_type');

        $query = Form::query();
        $query
            ->whereIn('employee_id', $companyEmployeeIds)
            ->when($status, fn ($q) => $q->where('status', $status))
            ->when($formType, fn ($q) => $q->where('form_type', $formType));

        return $query->paginate(20);
    }
}
"""


def test_unconditional_wherein_leads(cli_runner, tmp_path, monkeypatch):
    """Pattern 1: whereIn is eager-resolved (always applied); ->when() is
    conditional. The suggested composite must put employee_id first."""
    proj = _write_project(tmp_path, _CONTROLLER_WHERE_IN_LEADS, name="p1")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, f"Expected at least one composite_where finding, got: {data['findings']}"

    f = composite[0]
    cols = f["columns"]
    assert cols[0] == "employee_id", (
        f"Expected employee_id (unconditional whereIn) to LEAD, got: {cols}"
    )
    # Conditional cols come after employee_id
    assert set(cols) >= {"employee_id", "status", "form_type"}, (
        f"Expected all three cols in suggestion, got: {cols}"
    )
    # Verify the classification of employee_id is 'unconditional'
    co = {entry["column"]: entry["classification"] for entry in f["column_ordering"]}
    assert co["employee_id"] == "unconditional", co
    assert co["status"] == "conditional", co
    assert co["form_type"] == "conditional", co


# ===========================================================================
# Test 2 — unconditional where + orderBy lead conditional ->when() columns
# ===========================================================================


_CONTROLLER_WHERE_LEADS = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\Form;
use Illuminate\\Http\\Request;

class FormController extends Controller
{
    public function index(Request $request)
    {
        $status = $request->input('status');
        $priority = $request->input('priority');
        $type = $request->input('type');

        $query = Form::query();
        $query
            ->where('company_id', $request->user()->company_id)
            ->orderBy('due_date', 'desc')
            ->when($status, fn ($q) => $q->where('status', $status))
            ->when($priority, fn ($q) => $q->where('priority', $priority))
            ->when($type, fn ($q) => $q->where('type', $type));

        return $query->paginate(20);
    }
}
"""


def test_unconditional_where_leads(cli_runner, tmp_path, monkeypatch):
    """Pattern 2: where('company_id', ...) is always applied; orderBy is
    always applied; ->when() filters are conditional. The composite must be
    (company_id, status, priority, type, due_date)."""
    proj = _write_project(tmp_path, _CONTROLLER_WHERE_LEADS, name="p2")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, f"Expected at least one composite_where finding, got: {data['findings']}"

    f = composite[0]
    cols = f["columns"]
    assert cols[0] == "company_id", (
        f"Expected company_id (unconditional where) to LEAD, got: {cols}"
    )
    # due_date is the orderBy — must be last
    assert cols[-1] == "due_date", (
        f"Expected due_date (orderBy) to TRAIL, got: {cols}"
    )
    # Conditional columns sit between
    assert "status" in cols and "priority" in cols and "type" in cols, cols
    company_idx = cols.index("company_id")
    due_idx = cols.index("due_date")
    for cc in ("status", "priority", "type"):
        cidx = cols.index(cc)
        assert company_idx < cidx < due_idx, (
            f"Expected {cc} between company_id and due_date, got: {cols}"
        )


# ===========================================================================
# Test 3 — orderBy columns are last in the suggested composite
# ===========================================================================


_CONTROLLER_ORDERBY_TRAILS = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\Form;
use Illuminate\\Http\\Request;

class FormController extends Controller
{
    public function index(Request $request)
    {
        return Form::query()
            ->where('company_id', 1)
            ->where('employee_id', 5)
            ->orderBy('due_date')
            ->paginate(20);
    }
}
"""


def test_orderby_trails(cli_runner, tmp_path, monkeypatch):
    """Sort columns trail equality columns in the composite recommendation."""
    proj = _write_project(tmp_path, _CONTROLLER_ORDERBY_TRAILS, name="p3")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, f"Expected composite finding, got: {data['findings']}"
    f = composite[0]
    cols = f["columns"]
    # The orderBy column 'due_date' must be the last in the ordering.
    assert cols[-1] == "due_date", f"orderBy column must trail, got: {cols}"
    co = {entry["column"]: entry["classification"] for entry in f["column_ordering"]}
    assert co["due_date"] == "sort", co


# ===========================================================================
# Test 4 — pure-conditional query ranks predicates by source order, all
# classified as conditional. (Don't regress prior behavior when nothing is
# unconditional.)
# ===========================================================================


_CONTROLLER_PURE_CONDITIONAL = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\Form;
use Illuminate\\Http\\Request;

class FormController extends Controller
{
    public function index(Request $request)
    {
        $a = $request->input('status');
        $b = $request->input('form_type');
        $c = $request->input('priority');

        $query = Form::query();
        $query
            ->when($a, fn ($q) => $q->where('status', $a))
            ->when($b, fn ($q) => $q->where('form_type', $b))
            ->when($c, fn ($q) => $q->where('priority', $c));

        return $query->paginate(20);
    }
}
"""


def test_pure_conditional_query_ranks_as_before(cli_runner, tmp_path, monkeypatch):
    """When ALL predicates are inside ->when(), they all get classified as
    conditional and the ordering matches source order (status, form_type,
    priority) — matching the pre-fix behavior for this case."""
    proj = _write_project(tmp_path, _CONTROLLER_PURE_CONDITIONAL, name="p4")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, f"Expected composite finding, got: {data['findings']}"
    f = composite[0]
    cols = f["columns"]
    # Source order is preserved within a single classification bucket.
    assert cols == ["status", "form_type", "priority"], (
        f"Expected source order for pure-conditional query, got: {cols}"
    )
    # All three classified as conditional.
    for entry in f["column_ordering"]:
        assert entry["classification"] == "conditional", entry
    # Confidence: paginated but NO unconditional column → medium (not high).
    assert f["confidence"] == "medium" or any(
        item["confidence"] == "medium"
        for item in data["findings"]
        if item["value"].get("pattern_type") == "composite_where"
    )


# ===========================================================================
# Test 5 — range predicates trail equality
# ===========================================================================


_CONTROLLER_RANGE_AFTER_EQUALITY = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\Form;

class FormController extends Controller
{
    public function index()
    {
        return Form::query()
            ->where('employee_id', 5)
            ->whereBetween('due_date', ['2024-01-01', '2024-12-31'])
            ->paginate(20);
    }
}
"""


def test_mixed_ranges_after_equality(cli_runner, tmp_path, monkeypatch):
    """Range predicates (whereBetween, whereDate, where col > x) must trail
    equality columns — a range breaks B-tree seekability for any trailing
    column."""
    proj = _write_project(tmp_path, _CONTROLLER_RANGE_AFTER_EQUALITY, name="p5")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, f"Expected composite finding, got: {data['findings']}"
    f = composite[0]
    cols = f["columns"]
    assert cols.index("employee_id") < cols.index("due_date"), (
        f"Equality column employee_id must precede range column due_date; got: {cols}"
    )
    co = {entry["column"]: entry["classification"] for entry in f["column_ordering"]}
    assert co["employee_id"] == "unconditional", co
    assert co["due_date"] == "range", co


# ===========================================================================
# Test 6 — finding envelope includes per-column rationale
# ===========================================================================


def test_recommendation_envelope_includes_rationale(cli_runner, tmp_path, monkeypatch):
    """Composite_where findings expose column_ordering and ranking_explanation
    so consumers can see WHY each column is in its position. The dogfood
    explicitly asked for this transparency."""
    proj = _write_project(tmp_path, _CONTROLLER_WHERE_IN_LEADS, name="p6")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, f"Expected composite finding, got: {data['findings']}"
    f = composite[0]

    assert "column_ordering" in f, f"Missing column_ordering: {f}"
    assert "ranking_explanation" in f, f"Missing ranking_explanation: {f}"

    co_list = f["column_ordering"]
    assert isinstance(co_list, list) and co_list, co_list
    # Every entry has the triple of keys we promised.
    for entry in co_list:
        assert {"column", "classification", "rationale"} <= set(entry.keys()), entry
        assert isinstance(entry["rationale"], str) and entry["rationale"], entry

    # The first entry's rationale should mention "unconditional" or
    # "leading" (i.e., explain WHY it leads).
    first = co_list[0]
    assert first["column"] == "employee_id", first
    assert (
        "unconditional" in first["rationale"].lower()
        or "leading" in first["rationale"].lower()
    ), first

    # The ranking_explanation should mention every column in order.
    cols_in_order = [e["column"] for e in co_list]
    expl = f["ranking_explanation"]
    last_pos = -1
    for c in cols_in_order:
        idx = expl.find(c)
        assert idx > last_pos, (
            f"Expected {c} to appear after the previous column in: {expl!r}"
        )
        last_pos = idx
