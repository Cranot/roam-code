"""Tests for backend fixes round 3: I.10.2 and I.10.5.

Covers:
1. I.10.2 - Raw SQL CREATE INDEX parsing in missing-index command
2. I.10.5 - information_schema guard recognition in migration-safety command
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


# ===========================================================================
# 1. I.10.2 - Raw SQL CREATE INDEX detection in _parse_migration_indexes
# ===========================================================================

def _make_migration_dir(tmp_path: Path, filename: str, content: str) -> list[str]:
    """Create a migration file under a migrations/ subdir and return rel paths."""
    mig_dir = tmp_path / "database" / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)
    fpath = mig_dir / filename
    fpath.write_text(content, encoding="utf-8")
    return [str(fpath.relative_to(tmp_path))]


class TestCreateIndexRawParsing:
    """_parse_migration_indexes should detect raw SQL CREATE INDEX statements."""

    def test_basic_create_index(self, tmp_path):
        """A simple CREATE INDEX ... ON table(col) should be detected."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        content = """<?php
Schema::create('users', function (Blueprint $table) {
    $table->id();
    $table->string('email');
});

DB::statement('CREATE INDEX idx_users_email ON users (email)');
"""
        paths = _make_migration_dir(tmp_path, "2024_01_01_create_users.php", content)
        result = _parse_migration_indexes(tmp_path, paths)

        assert "users" in result
        assert ("email",) in result["users"]

    def test_create_unique_index(self, tmp_path):
        """CREATE UNIQUE INDEX should be detected."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        content = """<?php
Schema::create('orders', function (Blueprint $table) {
    $table->id();
    $table->string('order_number');
});

DB::statement('CREATE UNIQUE INDEX idx_orders_number ON orders (order_number)');
"""
        paths = _make_migration_dir(tmp_path, "2024_01_02_create_orders.php", content)
        result = _parse_migration_indexes(tmp_path, paths)

        assert "orders" in result
        assert ("order_number",) in result["orders"]

    def test_schema_prefixed_table(self, tmp_path):
        """CREATE INDEX with schema.table prefix should extract the table name."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        content = """<?php
Schema::create('invoices', function (Blueprint $table) {
    $table->id();
    $table->string('invoice_ref');
});

DB::statement('CREATE INDEX idx_inv_ref ON public.invoices (invoice_ref)');
"""
        paths = _make_migration_dir(tmp_path, "2024_01_03_create_invoices.php", content)
        result = _parse_migration_indexes(tmp_path, paths)

        assert "invoices" in result
        assert ("invoice_ref",) in result["invoices"]

    def test_multiple_columns_in_create_index(self, tmp_path):
        """CREATE INDEX with multiple columns should register both composite and singles."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        content = """<?php
Schema::create('products', function (Blueprint $table) {
    $table->id();
    $table->string('category');
    $table->string('brand');
});

DB::statement('CREATE INDEX idx_products_cat_brand ON products (category, brand)');
"""
        paths = _make_migration_dir(tmp_path, "2024_01_04_create_products.php", content)
        result = _parse_migration_indexes(tmp_path, paths)

        assert "products" in result
        # Composite tuple
        assert ("category", "brand") in result["products"]
        # Individual columns should also be registered
        assert ("category",) in result["products"]
        assert ("brand",) in result["products"]

    def test_case_insensitive_create_index(self, tmp_path):
        """CREATE INDEX matching should be case insensitive."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        content = """<?php
Schema::create('events', function (Blueprint $table) {
    $table->id();
    $table->string('event_type');
});

DB::statement('create index idx_events_type on events (event_type)');
"""
        paths = _make_migration_dir(tmp_path, "2024_01_05_create_events.php", content)
        result = _parse_migration_indexes(tmp_path, paths)

        assert "events" in result
        assert ("event_type",) in result["events"]

    def test_create_index_if_not_exists(self, tmp_path):
        """CREATE INDEX IF NOT EXISTS should also be detected."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        content = """<?php
Schema::create('logs', function (Blueprint $table) {
    $table->id();
    $table->string('level');
});

DB::statement('CREATE INDEX IF NOT EXISTS idx_logs_level ON logs (level)');
"""
        paths = _make_migration_dir(tmp_path, "2024_01_06_create_logs.php", content)
        result = _parse_migration_indexes(tmp_path, paths)

        assert "logs" in result
        assert ("level",) in result["logs"]


# ===========================================================================
# 2. I.10.5 - information_schema guard recognition in migration-safety
# ===========================================================================

class TestInfoSchemaGuardRegex:
    """The _RE_INFO_SCHEMA_GUARD regex should match various information_schema patterns."""

    def test_information_schema_columns(self):
        """information_schema.columns should be recognized as a guard."""
        from roam.commands.cmd_migration_safety import _RE_INFO_SCHEMA_GUARD

        text = "DB::select(\"SELECT * FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'email'\")"
        assert _RE_INFO_SCHEMA_GUARD.search(text) is not None

    def test_information_schema_tables(self):
        """information_schema.tables should be recognized as a guard."""
        from roam.commands.cmd_migration_safety import _RE_INFO_SCHEMA_GUARD

        text = "DB::select(\"SELECT * FROM information_schema.tables WHERE table_name = 'users'\")"
        assert _RE_INFO_SCHEMA_GUARD.search(text) is not None

    def test_information_schema_statistics(self):
        """information_schema.statistics should be recognized as a guard."""
        from roam.commands.cmd_migration_safety import _RE_INFO_SCHEMA_GUARD

        text = "SELECT * FROM information_schema.statistics WHERE index_name = 'idx_foo'"
        assert _RE_INFO_SCHEMA_GUARD.search(text) is not None


class TestHasTableGuard:
    """Schema::hasTable() should be recognized as a valid idempotency guard."""

    def test_has_table_recognized(self):
        """hasTable() should match the _RE_HAS_TABLE regex."""
        from roam.commands.cmd_migration_safety import _RE_HAS_TABLE

        text = "if (Schema::hasTable('users')) {"
        assert _RE_HAS_TABLE.search(text) is not None

    def test_has_table_guards_add_column(self):
        """A column addition guarded by hasTable() should NOT produce a finding."""
        from roam.commands.cmd_migration_safety import _check_add_column

        lines = [
            "<?php\n",
            "class AddEmailToUsers extends Migration\n",
            "{\n",
            "    public function up()\n",
            "    {\n",
            "        if (Schema::hasTable('users')) {\n",
            "            Schema::table('users', function (Blueprint $table) {\n",
            "                $table->string('nickname');\n",
            "            });\n",
            "        }\n",
            "    }\n",
            "}\n",
        ]
        # up() starts at line 4, ends at line 11
        findings = _check_add_column(lines, 4, 11)
        assert len(findings) == 0, f"Expected no findings, got {findings}"

    def test_no_guard_produces_finding(self):
        """A column addition without any guard should produce a finding."""
        from roam.commands.cmd_migration_safety import _check_add_column

        lines = [
            "<?php\n",
            "class AddEmailToUsers extends Migration\n",
            "{\n",
            "    public function up()\n",
            "    {\n",
            "        Schema::table('users', function (Blueprint $table) {\n",
            "            $table->string('nickname');\n",
            "        });\n",
            "    }\n",
            "}\n",
        ]
        findings = _check_add_column(lines, 4, 9)
        assert len(findings) >= 1, "Expected at least one finding for unguarded column addition"


class TestDBSelectInfoSchemaGuard:
    """DB::select with information_schema should be recognized as an idempotency guard."""

    def test_db_select_info_schema_guards_drop_column(self):
        """A dropColumn guarded by DB::select + information_schema.columns
        should NOT produce a finding."""
        from roam.commands.cmd_migration_safety import _check_drop_column

        lines = [
            "<?php\n",
            "class DropNicknameFromUsers extends Migration\n",
            "{\n",
            "    public function up()\n",
            "    {\n",
            "        $exists = DB::select(\"SELECT * FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'nickname'\");\n",
            "        if (count($exists) > 0) {\n",
            "            Schema::table('users', function (Blueprint $table) {\n",
            "                $table->dropColumn('nickname');\n",
            "            });\n",
            "        }\n",
            "    }\n",
            "}\n",
        ]
        findings = _check_drop_column(lines, 4, 12)
        assert len(findings) == 0, f"Expected no findings, got {findings}"

    def test_info_schema_tables_guards_index_creation(self):
        """A raw CREATE INDEX guarded by information_schema.tables should NOT
        produce a finding."""
        from roam.commands.cmd_migration_safety import _check_index_creation

        lines = [
            "<?php\n",
            "class AddIndexToUsers extends Migration\n",
            "{\n",
            "    public function up()\n",
            "    {\n",
            "        $exists = DB::select(\"SELECT * FROM information_schema.tables WHERE table_name = 'users'\");\n",
            "        if (count($exists) > 0) {\n",
            "            DB::statement('CREATE INDEX idx_users_email ON users (email)');\n",
            "        }\n",
            "    }\n",
            "}\n",
        ]
        findings = _check_index_creation(lines, 4, 10)
        assert len(findings) == 0, f"Expected no findings, got {findings}"

    def test_unguarded_create_index_produces_finding(self):
        """A raw CREATE INDEX without any guard should produce a finding."""
        from roam.commands.cmd_migration_safety import _check_index_creation

        lines = [
            "<?php\n",
            "class AddIndexToUsers extends Migration\n",
            "{\n",
            "    public function up()\n",
            "    {\n",
            "        DB::statement('CREATE INDEX idx_users_email ON users (email)');\n",
            "    }\n",
            "}\n",
        ]
        findings = _check_index_creation(lines, 4, 7)
        assert len(findings) >= 1, "Expected at least one finding for unguarded CREATE INDEX"
        assert findings[0]["category"] == "index_without_check"
