"""Regression tests for the Laravel-specific FP fixes (M9-M12).

Each test reproduces a user-reported FP from the a Vue 3 + Laravel codebase feedback.
"""

from __future__ import annotations

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
