# Backend Command Improvements

Field-test findings from running `missing-index`, `migration-safety`, `auth-gaps`,
and `over-fetch` against a real-world Laravel multi-tenant codebase. All items
below are false-positive patterns or detection gaps discovered during testing.

---

## 1. missing-index: Private helper methods in migrations (HIGH)

**Problem:** `_parse_migration_indexes()` only detects indexes inside
`Schema::create('table', fn)` or `Schema::table('table', fn)` blocks matched
by `_RE_SCHEMA_TABLE`. When a migration delegates table creation to private
methods (one method per table), the regex finds the `Schema::create` call
inside the helper but loses the connection to the migration file's `up()`.

**Real-world pattern:**
```php
class BaselineMigration extends Migration {
    public function up(): void {
        $this->createUsersTable($schema);
        $this->createOrdersTable($schema);
    }

    private function createUsersTable(string $schema): void {
        Schema::connection('accounting')->create("{$schema}.users", function ($t) {
            $t->uuid('id')->primary();
            $t->uuid('company_id')->index();   // <-- this index IS parsed
            $t->index(['company_id', 'deleted_at']);
        });
    }
}
```

The `Schema::create("{$schema}.users", ...)` IS matched by `_RE_SCHEMA_TABLE`,
but the extracted table name is `{$schema}.users` (string interpolation), NOT
the bare `users`. So `_parse_migration_indexes` stores indexes under key
`{$schema}.users` while `_infer_table_from_context` in Step 2 resolves
queries to bare `users` — **no match**.

**Root cause (precise):** `_RE_SCHEMA_TABLE` captures the raw string argument
including interpolation variables. When the table is `"{$schema}.users"`,
group(1) yields `{$schema}.users` instead of `users`.

**Fix options:**
- A) Strip schema prefixes from captured table names:
  ```python
  table_name = sm.group(1)
  # Strip interpolated schema prefix: "{$schema}.users" -> "users"
  if "." in table_name:
      table_name = table_name.rsplit(".", 1)[-1]
  ```
- B) Normalize both sides: also strip prefixes from query-inferred tables.
- C) Register indexes under BOTH the raw and stripped name.

**Affected commands:** `missing-index` (Step 1)

**Impact:** Every table created with a schema-prefixed name produces false
HIGH/MEDIUM missing-index findings, even when indexes are properly defined.

---

## 2. missing-index: Raw SQL CREATE INDEX not parsed (MEDIUM)

**Problem:** `_parse_migration_indexes()` only detects Laravel Blueprint
patterns (`$table->index(...)`, `$table->unique(...)`, etc.). Raw SQL
`CREATE INDEX` statements are ignored.

**Real-world pattern:**
```php
$connection->statement("
    CREATE INDEX idx_codes_account
    ON {$schemaName}.ledger_account_tax_codes(ledger_account_id)
");
```

**Fix:** Add a regex to capture `CREATE INDEX name ON table(columns)` and
feed those into `table_indexes`. The table name extraction needs the same
schema-prefix stripping from item #1.

```python
_RE_RAW_CREATE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(\w+)\s+ON\s+(?:\w+\.)?(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
```

---

## 3. missing-index: Queries on child tables attributed to wrong columns (LOW)

**Problem:** When a service queries a parent table with `where('company_id', ...)`
and then queries a child table with `whereIn('article_id', ...)`, roam
sometimes attributes the `company_id` column to the child table if context
inference falls back to a nearby class/table reference.

**Real-world pattern (BackupExportService):**
```php
// Parent query — company_id is on articles table
$articleIds = Article::where('company_id', $companyId)
                     ->where('usage_period_id', $periodId)
                     ->pluck('id');

// Child query — article_ledger_accounts does NOT have company_id
ArticleLedgerAccount::whereIn('article_id', $articleIds)->get();
```

Roam reports: `article_ledger_accounts.company_id + usage_period_id` missing
composite index — but the table doesn't even have those columns.

**Root cause:** `_infer_table_from_context()` looks backwards up to 2000
characters for the nearest model reference. In a method that queries multiple
models sequentially, the inference can bleed across query boundaries.

**Fix options:**
- A) Require the model reference to be on the same statement chain (look for
  the nearest `;` boundary before the `where()` match).
- B) Cross-reference detected columns against the model's `$fillable` or
  migration-defined columns, discarding any column not in the table definition.

---

## 4. missing-index: Table name pluralization mismatches (LOW)

**Problem:** `_class_to_table()` applies simple English pluralization rules,
which can produce wrong table names for irregular plurals or non-English names.

**Examples:**
- `Company` -> `companys` (wrong, should be `companies`) — but this IS handled
  by the `y -> ies` rule
- `Kinisi` -> `kinisis` — correct for this app but non-standard English
- `Branch` -> `branchs` — WRONG, should be `branches` (missing `-ch -> -ches`)
- `KiniseisExtrait` -> `kiniseis_extraits` — table is actually `kiniseis_extrait`

**Fix:** Add more pluralization rules or look up `$table` property first
(which already happens in `_infer_table_from_context`, but only when
resolving queries — not when resolving model-to-table mappings in index
collection).

---

## 5. migration-safety: Raw SQL idempotency guards not recognized (MEDIUM)

**Problem:** `_RE_HAS_COLUMN` only matches the Laravel `hasColumn()` call.
When a migration uses raw SQL `information_schema` queries as guards, the
detection logic doesn't recognize them as valid idempotency checks.

**Real-world pattern:**
```php
$exists = $connection->select("
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = ? AND table_name = 'ledger_accounts' AND column_name = 'pelpro_id'
", [$schemaName]);

if (empty($exists)) {
    // Add column safely
}
```

This is functionally equivalent to `hasColumn()` but roam flags the migration
as non-idempotent.

**Fix:** Add `information_schema` as an alternative guard pattern in the
column-existence detection, similar to how `_RE_PG_INDEXES` guards index
operations:

```python
_RE_INFO_SCHEMA_GUARD = re.compile(
    r"""information_schema\s*\.\s*(?:columns|tables)""",
    re.IGNORECASE,
)
```

Then check for this regex alongside `_RE_HAS_COLUMN` in
`_detect_columns_without_guard()` and `_detect_drops_without_guard()`.

---

## 6. auth-gaps: Service provider route registration (KNOWN LIMITATION)

**Problem (already documented but worth noting here):** Routes registered
programmatically in a `ServiceProvider::boot()` method are not detected by
the route-file scanner. This means controllers referenced only from service
providers appear as unprotected even when the service provider wraps them in
`Route::middleware(['auth:sanctum', ...])`.

**Real-world pattern:**
```php
class ResourceServiceProvider extends ServiceProvider {
    public function boot(): void {
        Route::middleware(['auth:sanctum', 'office.context'])
             ->prefix('api/resource')
             ->group(function () {
                 Route::get('{resource}', [DynamicResourceController::class, 'index']);
             });
    }
}
```

**Fix options:**
- A) Scan `app/Providers/*.php` in addition to `routes/*.php` for route
  definitions (check for `Route::middleware` / `Route::group` patterns).
- B) Accept a config file listing additional protected controller classes.
- C) Detect `ServiceProvider` extends and scan boot() for Route:: calls.

---

## 7. over-fetch: $hidden vs API Resources tradeoff (DESIGN NOTE)

Not a bug but a design consideration for documentation and messaging.

When `over-fetch` flags a model with many fields, the typical suggestion is
to add `$hidden` or create an API Resource. However, in CRUD applications
where ALL fields are user-editable:

- `$hidden` breaks edit forms (fields stop appearing in responses)
- `$visible` has the same issue
- API Resources are the correct fix but require per-model effort

The `over-fetch` finding message should acknowledge this nuance, perhaps:

```
Suggestion: Create a Laravel Resource (App\Http\Resources\XxxResource)
to expose only list-view fields. Use the full model for detail/edit views.
Note: $hidden/$visible will also hide fields from edit endpoints.
```

---

## Priority ranking

| # | Command          | Issue                                  | Priority | Effort |
|---|------------------|----------------------------------------|----------|--------|
| 1 | missing-index    | Schema-prefixed table names            | HIGH     | Small  |
| 5 | migration-safety | Raw SQL guards (information_schema)    | MEDIUM   | Small  |
| 2 | missing-index    | Raw SQL CREATE INDEX                   | MEDIUM   | Small  |
| 3 | missing-index    | Cross-model column attribution         | LOW      | Medium |
| 6 | auth-gaps        | Service provider routes                | LOW      | Medium |
| 4 | missing-index    | Pluralization edge cases               | LOW      | Small  |
| 7 | over-fetch       | $hidden messaging                      | LOW      | Tiny   |
