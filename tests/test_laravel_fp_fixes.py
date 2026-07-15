"""Regression tests for the Laravel-specific FP fixes (M9-M12).

Each test reproduces a user-reported FP from the a Vue 3 + Laravel codebase feedback.
"""

from __future__ import annotations

import pytest

# ---- M9 / M13: missing-index ------------------------------------------------


def test_schema_table_regex_matches_connection_chain():
    """Schema::connection('payroll')->create('table_name', ...) must match."""
    from roam.commands.cmd_missing_index import _RE_SCHEMA_TABLE

    line = "Schema::connection('payroll')->create('payroll_advances', function (Blueprint $table) {"
    m = _RE_SCHEMA_TABLE.search(line)
    assert m is not None
    assert m.group(1) == "payroll_advances"


def test_schema_table_regex_still_matches_plain_form():
    """Plain Schema::create('table', ...) must still match."""
    from roam.commands.cmd_missing_index import _RE_SCHEMA_TABLE

    m = _RE_SCHEMA_TABLE.search("Schema::create('users', function (Blueprint $table) {")
    assert m is not None and m.group(1) == "users"


def test_schema_table_regex_handles_double_quotes_with_interpolation():
    """Schema::create("{$schema}.payroll_advances", ...) must capture the interpolated string."""
    from roam.commands.cmd_missing_index import _RE_SCHEMA_TABLE

    line = 'Schema::create("{$schema}.payroll_advances", function ($table) {'
    m = _RE_SCHEMA_TABLE.search(line)
    assert m is not None
    assert m.group(1) == "{$schema}.payroll_advances"


def test_normalise_table_name_strips_schema_prefix():
    """{$schema}.payroll_advances → payroll_advances; payroll → payroll."""
    from roam.commands.cmd_missing_index import _normalise_table_name

    assert _normalise_table_name("{$schema}.payroll_advances") == "payroll_advances"
    assert _normalise_table_name("$schema.payroll_payments") == "payroll_payments"
    assert _normalise_table_name("plain_table") == "plain_table"
    assert _normalise_table_name(None) is None


def test_class_to_table_consults_overrides(tmp_path):
    """When `_MODEL_TABLE_OVERRIDES` has the class, that wins over snake_case derivation."""
    import roam.commands.cmd_missing_index as m

    # Save + restore module-level state
    original = m._MODEL_TABLE_OVERRIDES.copy()
    try:
        m._MODEL_TABLE_OVERRIDES = {"Advance": "payroll_advances"}
        # Without override, "Advance" → "advances". With override, → "payroll_advances".
        assert m._class_to_table("Advance") == "payroll_advances"
        # Other classes still go through the snake_case fallback
        assert m._class_to_table("UserProfile") == "user_profiles"
    finally:
        m._MODEL_TABLE_OVERRIDES = original


def test_build_model_table_overrides_extracts_table_property(tmp_path):
    """Walk a model file, find $table = '...' override."""
    from roam.commands.cmd_missing_index import _build_model_table_overrides

    src = tmp_path / "PayrollAdvance.php"
    src.write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "class PayrollAdvance extends Model\n{\n"
        "    protected $table = 'payroll_advances';\n"
        "    public $timestamps = true;\n"
        "}\n",
        encoding="utf-8",
    )
    overrides = _build_model_table_overrides(tmp_path, ["PayrollAdvance.php"])
    assert overrides["PayrollAdvance"] == "payroll_advances"


def test_build_model_table_overrides_skips_models_without_table_prop(tmp_path):
    """Eloquent default — no $table — should not appear in the overrides dict."""
    from roam.commands.cmd_missing_index import _build_model_table_overrides

    src = tmp_path / "User.php"
    src.write_text("<?php\nclass User extends Model\n{\n    public $timestamps = true;\n}\n", encoding="utf-8")
    overrides = _build_model_table_overrides(tmp_path, ["User.php"])
    assert "User" not in overrides


# ---- M14: non-model classes (Seeders / Tests / Console cmds) are NOT tables --
#
# Measured FP (real Laravel app, roam 13.8.0): missing-index treated Seeders,
# Tests, and Console commands as Eloquent models, pluralised their class names
# into phantom tables, and reported every ->where inside them as a "missing
# index" on a table that never existed:
#   database/seeders/DocumentArchiveSeeder.php  -> document_archive_seeders.usage_period_id
#   app/Console/Commands/BackfillOristiki.php   -> backfill_oristikis.kodikos
#   tests/Feature/.../DedupPerPeriodTest.php    -> dedup_per_period_tests.usage_period_id
# The fix gates the class-name -> table inference so these class families are
# skipped, while real Models (app/Models, extends Model) and controllers still
# flag. See `_is_non_model_class` / `_table_from_class_decl_window`.

# (class_name, parent, rel_path, phantom_table_that_class_to_table_produces)
_NON_MODEL_FP_EXAMPLES = [
    ("DocumentArchiveSeeder", "Seeder", "database/seeders/DocumentArchiveSeeder.php", "document_archive_seeders"),
    (
        "PayrollGlArticleSeeder",
        "Seeder",
        "database/seeders/Payroll/PayrollGlArticleSeeder.php",
        "payroll_gl_article_seeders",
    ),
    ("BackfillOristiki", "Command", "app/Console/Commands/BackfillOristiki.php", "backfill_oristikis"),
    ("MergeDefaultArticles", "Command", "app/Console/Commands/MergeDefaultArticles.php", "merge_default_articleses"),
    ("DedupPerPeriodTest", "TestCase", "tests/Feature/BankExtract/DedupPerPeriodTest.php", "dedup_per_period_tests"),
]


@pytest.mark.parametrize("class_name,parent,rel_path,phantom", _NON_MODEL_FP_EXAMPLES)
def test_class_to_table_still_pluralises_but_gate_flags_non_model(class_name, parent, rel_path, phantom):
    """Pin BOTH halves of the fix.

    The bug was never in `_class_to_table` (it still pluralises exactly as
    before — proving the fix is surgical, not a pluralisation break); the bug
    was that a non-model class ever reached it. `_is_non_model_class` must flag
    every measured FP class.
    """
    from roam.commands.cmd_missing_index import _class_to_table, _is_non_model_class

    assert _class_to_table(class_name) == phantom  # ungated pluralisation UNCHANGED
    assert _is_non_model_class(rel_path, class_name, parent) is True


@pytest.mark.parametrize("class_name,parent,rel_path,phantom", _NON_MODEL_FP_EXAMPLES)
def test_infer_table_skips_non_model_class(class_name, parent, rel_path, phantom):
    """A `->where` inside a Seeder / Test / Console class must NOT be attributed
    to the pluralised class name — no phantom table is inferred."""
    from roam.commands.cmd_missing_index import _infer_table_from_context

    content = (
        f"<?php\nclass {class_name} extends {parent}\n{{\n"
        "    public function run()\n    {\n"
        "        $this->builder->where('usage_period_id', 5)->get();\n"
        "    }\n}\n"
    )
    pos = content.index("public function run")
    table = _infer_table_from_context(content, pos, rel_path)
    assert table != phantom
    assert table is None


def test_infer_table_keeps_real_model():
    """Precision guard: a genuine Eloquent model (app/Models, extends Model) is
    STILL resolved to its table — the gate must not suppress legitimate work."""
    from roam.commands.cmd_missing_index import _infer_table_from_context

    content = (
        "<?php\nnamespace App\\Models;\n\nclass Report extends Model\n{\n"
        "    public function recent()\n    {\n"
        "        return Report::query()->where('owner_id', 5)->paginate();\n"
        "    }\n}\n"
    )
    pos = content.index("public function recent")
    table = _infer_table_from_context(content, pos, "app/Models/Report.php")
    assert table == "reports"


def test_infer_table_keeps_controller():
    """Precision guard: a controller (extends Controller) is NOT a non-model
    family — its query must still resolve (the 'Controller' suffix is stripped,
    not gated)."""
    from roam.commands.cmd_missing_index import _infer_table_from_context

    content = (
        "<?php\nnamespace App\\Http\\Controllers;\n\nclass ReportController extends Controller\n{\n"
        "    public function index()\n    {\n"
        "        return Report::query()->where('owner_id', 5)->paginate();\n"
        "    }\n}\n"
    )
    pos = content.index("public function index")
    table = _infer_table_from_context(content, pos, "app/Http/Controllers/ReportController.php")
    assert table == "reports"  # ReportController -> strip Controller -> reports


# ---- M14b: queue / domain non-model classes also gated (hardening) ----
# Jobs, Observers, Policies, Listeners, Events, Mailables, Notifications, Rules
# hold ->where() queries too; their class names must not pluralise into phantom
# tables (SendInvoiceJob -> send_invoice_jobs). Matched by PSR-4 dir so a real
# model that happens to end that way (a `PrivacyPolicy` in app/Models/) is kept.
_QUEUE_DOMAIN_NON_MODELS = [
    ("app/Jobs/SendInvoiceJob.php", "SendInvoiceJob"),
    ("app/Observers/OrderObserver.php", "OrderObserver"),
    ("app/Policies/OrderPolicy.php", "OrderPolicy"),
    ("app/Listeners/DispatchShipment.php", "DispatchShipment"),
    ("app/Events/OrderShipped.php", "OrderShipped"),
    ("app/Mail/InvoiceMail.php", "InvoiceMail"),
    ("app/Notifications/InvoicePaid.php", "InvoicePaid"),
    ("app/Rules/ValidVat.php", "ValidVat"),
]


@pytest.mark.parametrize("rel_path,class_name", _QUEUE_DOMAIN_NON_MODELS)
def test_queue_domain_classes_are_gated(rel_path, class_name):
    from roam.commands.cmd_missing_index import _is_non_model_class

    assert _is_non_model_class(rel_path, class_name, None) is True


@pytest.mark.parametrize(
    "rel_path,class_name",
    [
        ("app/Models/PrivacyPolicy.php", "PrivacyPolicy"),  # ends 'Policy' but IS a model
        ("app/Models/CalendarEvent.php", "CalendarEvent"),  # ends 'Event' but IS a model
        ("app/Models/PrintJob.php", "PrintJob"),  # ends 'Job' but IS a model
    ],
)
def test_real_models_with_non_model_suffix_are_not_gated(rel_path, class_name):
    from roam.commands.cmd_missing_index import _is_non_model_class

    # PSR-4 dir signal (app/Models/) wins — a real model is never gated by a
    # coincidental name suffix, so its missing-index findings are preserved.
    assert _is_non_model_class(rel_path, class_name, "Model") is False


def test_seeder_test_console_produce_no_missing_index_finding(tmp_path):
    """End-to-end (parse -> cross-reference): a Seeder, a Test, and a Console
    command each issuing a `->where` produce ZERO missing-index findings and
    NO phantom `*_seeders` / `*_tests` / `*_commands` table."""
    from roam.commands.cmd_missing_index import _build_findings, _parse_query_patterns

    files = {
        "database/seeders/DocumentArchiveSeeder.php": (
            "<?php\nnamespace Database\\Seeders;\nuse Illuminate\\Database\\Seeder;\n\n"
            "class DocumentArchiveSeeder extends Seeder\n{\n"
            "    public function run()\n    {\n"
            "        $this->builder->where('usage_period_id', 5)->update(['x' => 1]);\n"
            "    }\n}\n"
        ),
        "tests/Feature/BankExtract/DedupPerPeriodTest.php": (
            "<?php\nnamespace Tests\\Feature\\BankExtract;\nuse Tests\\TestCase;\n\n"
            "class DedupPerPeriodTest extends TestCase\n{\n"
            "    public function test_dedup()\n    {\n"
            "        $this->service->where('usage_period_id', 1)->count();\n"
            "    }\n}\n"
        ),
        "app/Console/Commands/BackfillOristiki.php": (
            "<?php\nnamespace App\\Console\\Commands;\nuse Illuminate\\Console\\Command;\n\n"
            "class BackfillOristiki extends Command\n{\n"
            "    public function handle()\n    {\n"
            "        $this->repo->where('kodikos', 'X')->get();\n"
            "    }\n}\n"
        ),
    }
    rels = []
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        rels.append(rel)

    patterns = _parse_query_patterns(tmp_path, rels)
    # Every parsed pattern came from a non-model class -> no table inferred.
    for pat in patterns:
        assert pat.table is None, f"Non-model class produced a phantom table: {pat.table}"
    findings = _build_findings(patterns, {})
    assert findings == [], f"Expected NO findings for non-model classes, got: {findings}"


def test_real_model_still_flags_missing_index(tmp_path):
    """Precision guard (end-to-end): a real Eloquent model querying a genuinely
    unindexed column STILL produces a missing-index finding after the gate."""
    from roam.commands.cmd_missing_index import (
        _build_findings,
        _parse_migration_indexes,
        _parse_query_patterns,
    )

    mig_dir = tmp_path / "database" / "migrations"
    mig_dir.mkdir(parents=True)
    (mig_dir / "2024_01_01_000000_create_reports_table.php").write_text(
        "<?php\nSchema::create('reports', function ($table) {\n    $table->id();\n});\n",
        encoding="utf-8",
    )
    models_dir = tmp_path / "app" / "Models"
    models_dir.mkdir(parents=True)
    (models_dir / "Report.php").write_text(
        "<?php\nnamespace App\\Models;\nuse Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class Report extends Model\n{\n"
        "    public function recent()\n    {\n"
        "        return Report::query()->where('owner_id', 5)->paginate();\n"
        "    }\n}\n",
        encoding="utf-8",
    )

    indexes = _parse_migration_indexes(tmp_path, ["database/migrations/2024_01_01_000000_create_reports_table.php"])
    patterns = _parse_query_patterns(tmp_path, ["app/Models/Report.php"])
    findings = _build_findings(patterns, indexes)

    tables = {f.get("table") for f in findings}
    cols = {c for f in findings for c in f.get("columns", [])}
    assert "reports" in tables, f"Real model query should flag on 'reports'; got {findings}"
    assert "owner_id" in cols, f"'owner_id' should be flagged as unindexed; got {findings}"


# ---- M10: rate-limit middleware recognised by auth-gaps --------------------


def test_non_auth_guard_re_matches_throttle():
    from roam.commands.cmd_auth_gaps import _NON_AUTH_GUARD_RE

    assert _NON_AUTH_GUARD_RE.search("->middleware('throttle:60,1')") is not None


def test_non_auth_guard_re_matches_signed():
    from roam.commands.cmd_auth_gaps import _NON_AUTH_GUARD_RE

    assert _NON_AUTH_GUARD_RE.search("->middleware('signed')") is not None


def test_non_auth_guard_re_matches_verified():
    from roam.commands.cmd_auth_gaps import _NON_AUTH_GUARD_RE

    assert _NON_AUTH_GUARD_RE.search("->middleware('verified')") is not None


def test_non_auth_guard_re_does_not_match_plain_route():
    from roam.commands.cmd_auth_gaps import _NON_AUTH_GUARD_RE

    assert _NON_AUTH_GUARD_RE.search("Route::get('/foo', [Controller::class, 'index']);") is None


def test_non_auth_guard_re_does_not_match_auth_middleware():
    """auth and throttle look similar; auth must NOT match the non-auth regex."""
    from roam.commands.cmd_auth_gaps import _NON_AUTH_GUARD_RE

    assert _NON_AUTH_GUARD_RE.search("->middleware('auth:sanctum')") is None


# ---- M11: tenant-scoped controller recognition -----------------------------


def test_tenant_scope_re_matches_office_scoped():
    from roam.commands.cmd_auth_gaps import _TENANT_SCOPE_RE

    assert _TENANT_SCOPE_RE.search("Resource::for($user)->officeScoped()->multiTenant()") is not None


def test_tenant_scope_re_matches_resource_for():
    from roam.commands.cmd_auth_gaps import _TENANT_SCOPE_RE

    assert _TENANT_SCOPE_RE.search("Resource::for($currentUser, $action)") is not None


def test_tenant_scope_re_does_not_match_unrelated():
    from roam.commands.cmd_auth_gaps import _TENANT_SCOPE_RE

    assert _TENANT_SCOPE_RE.search("$user = User::find($id);") is None


# ---- M12: over-fetch model-scoped check ------------------------------------


def test_extract_method_bodies_with_lines():
    """Helper extracts methods + tracks 1-based start lines."""
    from roam.commands.cmd_over_fetch import _extract_method_bodies_with_lines

    src = (
        "<?php\nclass C\n{\n"
        "    public function show(): array\n    {\n"
        "        return ['a' => 1];\n"
        "    }\n"
        "    public function index(): array\n    {\n"
        "        return [];\n"
        "    }\n"
        "}\n"
    )
    methods = _extract_method_bodies_with_lines(src)
    names = [m["name"] for m in methods]
    assert "show" in names
    assert "index" in names
    show = next(m for m in methods if m["name"] == "show")
    assert "return ['a' => 1]" in show["body"]
