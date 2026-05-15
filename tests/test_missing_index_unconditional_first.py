"""Regression sentinels for external dogfood Pattern 1 + Pattern 2 cases.

Dogfood finding #4 (212-eval corpus, real Laravel workload): 3 of 5
``missing-index`` suggestions ranked optional ``->when()`` predicates BEFORE
unconditional ``->whereIn()`` / ``->where()`` columns — exactly backwards for
B-tree composite-index efficacy. A composite index can only seek on leading
columns that are GUARANTEED to be supplied; conditional ``->when()`` columns
may be absent at runtime and must therefore trail.

The fix lives in ``cmd_missing_index.py`` (predicate classifier +
``_rank_columns_for_index``). The companion file
``test_missing_index_unconditional.py`` already exercises six classification
scenarios. This file pins the EXACT TWO PATTERNS reported in the dogfood as
named regression sentinels so future refactors that drift away from the
production-validated behavior fail loudly with the dogfood case in the test
name.

  Pattern 1 — eager-resolved whereIn must lead conditional ->when() columns.
  Pattern 2 — unconditional where + orderBy lead conditional ->when() filters.
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
# Shared migration — `forms` table with NO composite index on any combination
# of (employee_id, status, form_type, company_id, priority, type, due_date) so
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


def _write_project(tmp_path, controller_php: str, name: str) -> Path:
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
# Pattern 1 — dogfood production case verbatim:
#
#   ->whereIn('employee_id', $companyEmployeeIds)              // unconditional
#   ->when($status,   fn ($q) => $q->where('status', $status)) // optional
#   ->when($formType, fn ($q) => $q->where('form_type', $formType))
#
# Pre-fix roam suggested:   (status, form_type, employee_id, ...)  ← WRONG
# Post-fix roam suggests:   (employee_id, status, form_type, ...)  ← correct
# ===========================================================================


_PATTERN_1_CONTROLLER = """\
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

        return Form::query()
            ->whereIn('employee_id', $companyEmployeeIds)
            ->when($status, fn ($q) => $q->where('status', $status))
            ->when($formType, fn ($q) => $q->where('form_type', $formType))
            ->paginate(20);
    }
}
"""


def test_unconditional_whereIn_comes_first(cli_runner, tmp_path, monkeypatch):
    """Pattern 1: whereIn outside ->when() must be leading."""
    proj = _write_project(tmp_path, _PATTERN_1_CONTROLLER, name="dogfood_p1")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, (
        f"Expected at least one composite_where finding, got: {data['findings']}"
    )

    f = composite[0]
    cols = f["columns"]

    # The dogfood failure: roam ranked conditional ->when() columns BEFORE the
    # always-applied whereIn. Pin the corrected order.
    assert cols[0] == "employee_id", (
        f"Pattern 1 regression: expected employee_id (unconditional whereIn) "
        f"to LEAD the composite, got: {cols}"
    )
    assert set(cols) >= {"employee_id", "status", "form_type"}, (
        f"Expected all three columns in suggestion, got: {cols}"
    )

    classifications = {
        entry["column"]: entry["classification"] for entry in f["column_ordering"]
    }
    assert classifications["employee_id"] == "unconditional", classifications
    assert classifications["status"] == "conditional", classifications
    assert classifications["form_type"] == "conditional", classifications


# ===========================================================================
# Pattern 2 — dogfood production case verbatim:
#
#   ->where('company_id', $request->user()->company_id)  // unconditional
#   ->orderBy('due_date', 'desc')                         // unconditional sort
#   ->when($status,   ...)                                // optional
#   ->when($priority, ...)                                // optional
#   ->when($type,     ...)                                // optional
#
# Pre-fix roam suggested:  (status, priority, type, due_date, company_id) ← WRONG
# Post-fix roam suggests:  (company_id, status, priority, type, due_date) ← correct
# ===========================================================================


_PATTERN_2_CONTROLLER = """\
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

        return Form::query()
            ->where('company_id', $request->user()->company_id)
            ->orderBy('due_date', 'desc')
            ->when($status, fn ($q) => $q->where('status', $status))
            ->when($priority, fn ($q) => $q->where('priority', $priority))
            ->when($type, fn ($q) => $q->where('type', $type))
            ->paginate(20);
    }
}
"""


def test_user_scope_and_sort(cli_runner, tmp_path, monkeypatch):
    """Pattern 2: unconditional where + orderBy + optional where →
    correct order is (where, optionals, orderBy)."""
    proj = _write_project(tmp_path, _PATTERN_2_CONTROLLER, name="dogfood_p2")
    data = _run_missing_index(cli_runner, proj, monkeypatch)

    composite = _composite_findings(data)
    assert composite, (
        f"Expected at least one composite_where finding, got: {data['findings']}"
    )

    f = composite[0]
    cols = f["columns"]

    # Leading: unconditional where('company_id').
    assert cols[0] == "company_id", (
        f"Pattern 2 regression: expected company_id (unconditional where) "
        f"to LEAD the composite, got: {cols}"
    )
    # Trailing: orderBy('due_date').
    assert cols[-1] == "due_date", (
        f"Pattern 2 regression: expected due_date (orderBy) to TRAIL the "
        f"composite, got: {cols}"
    )
    # Middle: all three conditional ->when() columns are present, between
    # company_id and due_date.
    for opt in ("status", "priority", "type"):
        assert opt in cols, f"Expected conditional column {opt} in {cols}"
        assert cols.index("company_id") < cols.index(opt) < cols.index("due_date"), (
            f"Expected {opt} between company_id and due_date, got: {cols}"
        )

    classifications = {
        entry["column"]: entry["classification"] for entry in f["column_ordering"]
    }
    assert classifications["company_id"] == "unconditional", classifications
    assert classifications["due_date"] == "sort", classifications
    for opt in ("status", "priority", "type"):
        assert classifications[opt] == "conditional", classifications
