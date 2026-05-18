"""Tests for the SQL DDL Tier 1 extractor."""

from __future__ import annotations

import pytest

try:
    from tree_sitter_language_pack import get_parser

    _parser = get_parser("sql")
    HAS_SQL = True
except Exception:
    HAS_SQL = False

pytestmark = pytest.mark.skipif(not HAS_SQL, reason="tree-sitter sql grammar unavailable")


def _parse(code: str):
    from roam.languages.sql_lang import SqlExtractor

    ext = SqlExtractor()
    source = code.encode()
    tree = _parser.parse(source)
    symbols = ext.extract_symbols(tree, source, "test.sql")
    refs = ext.extract_references(tree, source, "test.sql")
    return symbols, refs


def _sym_names(symbols, kind=None):
    if kind:
        return [s["name"] for s in symbols if s["kind"] == kind]
    return [s["name"] for s in symbols]


def _ref_names(refs, kind=None):
    if kind:
        return [r["target_name"] for r in refs if r["kind"] == kind]
    return [r["target_name"] for r in refs]


# ---- CREATE TABLE ----


class TestCreateTable:
    def test_basic_table(self):
        symbols, _ = _parse("CREATE TABLE users (id INT);")
        assert "users" in _sym_names(symbols, "class")

    def test_table_with_columns(self):
        code = "CREATE TABLE users (id INT, email VARCHAR(255), name TEXT);"
        symbols, _ = _parse(code)
        assert "users" in _sym_names(symbols, "class")
        fields = _sym_names(symbols, "field")
        assert "id" in fields
        assert "email" in fields
        assert "name" in fields

    def test_schema_qualified_table(self):
        symbols, _ = _parse("CREATE TABLE myschema.users (id INT);")
        s = [s for s in symbols if s["kind"] == "class"][0]
        assert s["name"] == "users"
        assert s["qualified_name"] == "myschema.users"

    def test_if_not_exists(self):
        symbols, _ = _parse("CREATE TABLE IF NOT EXISTS users (id INT);")
        assert "users" in _sym_names(symbols, "class")

    def test_multiple_tables(self):
        code = "CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);"
        symbols, _ = _parse(code)
        tables = _sym_names(symbols, "class")
        assert "a" in tables
        assert "b" in tables

    def test_table_signature(self):
        symbols, _ = _parse("CREATE TABLE orders (id INT);")
        s = [s for s in symbols if s["name"] == "orders"][0]
        assert s["signature"] == "CREATE TABLE orders"


# ---- Columns ----


class TestColumns:
    def test_int_column(self):
        symbols, _ = _parse("CREATE TABLE t (id INT);")
        s = [s for s in symbols if s["name"] == "id"][0]
        assert s["kind"] == "field"
        assert "INT" in s["signature"]

    def test_varchar_column(self):
        symbols, _ = _parse("CREATE TABLE t (email VARCHAR(255));")
        s = [s for s in symbols if s["name"] == "email"][0]
        assert s["kind"] == "field"

    def test_decimal_column(self):
        symbols, _ = _parse("CREATE TABLE t (price DECIMAL(10,2));")
        s = [s for s in symbols if s["name"] == "price"][0]
        assert s["kind"] == "field"

    def test_boolean_column(self):
        symbols, _ = _parse("CREATE TABLE t (active BOOLEAN);")
        s = [s for s in symbols if s["name"] == "active"][0]
        assert s["kind"] == "field"

    def test_text_column(self):
        symbols, _ = _parse("CREATE TABLE t (bio TEXT);")
        s = [s for s in symbols if s["name"] == "bio"][0]
        assert s["kind"] == "field"

    def test_serial_column(self):
        symbols, _ = _parse("CREATE TABLE t (id SERIAL);")
        s = [s for s in symbols if s["name"] == "id"][0]
        assert s["kind"] == "field"
        assert "SERIAL" in s["signature"]

    def test_timestamp_column(self):
        symbols, _ = _parse("CREATE TABLE t (created_at TIMESTAMP);")
        s = [s for s in symbols if s["name"] == "created_at"][0]
        assert s["kind"] == "field"

    def test_qualified_name(self):
        symbols, _ = _parse("CREATE TABLE users (email TEXT);")
        s = [s for s in symbols if s["name"] == "email"][0]
        assert s["qualified_name"] == "users.email"

    def test_parent_name(self):
        symbols, _ = _parse("CREATE TABLE users (email TEXT);")
        s = [s for s in symbols if s["name"] == "email"][0]
        assert s["parent_name"] == "users"


# ---- Column constraints ----


class TestColumnConstraints:
    def test_primary_key(self):
        symbols, _ = _parse("CREATE TABLE t (id INT PRIMARY KEY);")
        s = [s for s in symbols if s["name"] == "id"][0]
        assert "PRIMARY" in s["signature"]
        assert "KEY" in s["signature"]

    def test_not_null(self):
        symbols, _ = _parse("CREATE TABLE t (name TEXT NOT NULL);")
        s = [s for s in symbols if s["name"] == "name"][0]
        assert "NOT" in s["signature"]
        assert "NULL" in s["signature"]

    def test_unique(self):
        symbols, _ = _parse("CREATE TABLE t (email TEXT UNIQUE);")
        s = [s for s in symbols if s["name"] == "email"][0]
        assert "UNIQUE" in s["signature"]

    def test_default(self):
        symbols, _ = _parse("CREATE TABLE t (active BOOLEAN DEFAULT true);")
        s = [s for s in symbols if s["name"] == "active"][0]
        assert "DEFAULT" in s["signature"]

    def test_serial_primary_key(self):
        symbols, _ = _parse("CREATE TABLE t (id SERIAL PRIMARY KEY);")
        s = [s for s in symbols if s["name"] == "id"][0]
        assert "SERIAL" in s["signature"]
        assert "PRIMARY" in s["signature"]

    def test_multiple_constraints(self):
        symbols, _ = _parse("CREATE TABLE t (email VARCHAR(255) NOT NULL UNIQUE);")
        s = [s for s in symbols if s["name"] == "email"][0]
        assert "NOT" in s["signature"]
        assert "NULL" in s["signature"]
        assert "UNIQUE" in s["signature"]


# ---- Foreign keys ----


class TestForeignKeys:
    def test_inline_fk(self):
        code = "CREATE TABLE t (dept_id INT REFERENCES departments(id));"
        _, refs = _parse(code)
        ref_targets = _ref_names(refs, "reference")
        assert "departments" in ref_targets

    def test_inline_fk_source_name(self):
        code = "CREATE TABLE orders (user_id INT REFERENCES users(id));"
        _, refs = _parse(code)
        fk = [r for r in refs if r["kind"] == "reference"][0]
        assert fk["source_name"] == "orders"

    def test_constraint_fk(self):
        code = (
            "CREATE TABLE orders (\n"
            "    id INT,\n"
            "    user_id INT,\n"
            "    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id)\n"
            ");"
        )
        _, refs = _parse(code)
        ref_targets = _ref_names(refs, "reference")
        assert "users" in ref_targets

    def test_constraint_fk_source_name(self):
        code = (
            "CREATE TABLE orders (\n"
            "    id INT,\n"
            "    user_id INT,\n"
            "    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id)\n"
            ");"
        )
        _, refs = _parse(code)
        fk = [r for r in refs if r["kind"] == "reference"][0]
        assert fk["source_name"] == "orders"

    def test_multiple_fks(self):
        code = "CREATE TABLE t (\n    a_id INT REFERENCES table_a(id),\n    b_id INT REFERENCES table_b(id)\n);"
        _, refs = _parse(code)
        ref_targets = _ref_names(refs, "reference")
        assert "table_a" in ref_targets
        assert "table_b" in ref_targets


# ---- CREATE VIEW ----


class TestCreateView:
    def test_basic_view(self):
        symbols, _ = _parse("CREATE VIEW active_users AS SELECT * FROM users;")
        assert "active_users" in _sym_names(symbols, "class")

    def test_or_replace_view(self):
        code = "CREATE OR REPLACE VIEW summary AS SELECT * FROM orders;"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "summary"][0]
        assert "OR REPLACE" in s["signature"]

    def test_view_table_refs(self):
        code = "CREATE VIEW v AS SELECT * FROM users;"
        _, refs = _parse(code)
        calls = _ref_names(refs, "call")
        assert "users" in calls

    def test_view_multi_table_refs(self):
        code = "CREATE VIEW v AS SELECT * FROM users u JOIN orders o ON u.id = o.uid;"
        _, refs = _parse(code)
        calls = _ref_names(refs, "call")
        assert "users" in calls
        assert "orders" in calls


# ---- CREATE FUNCTION ----


class TestCreateFunction:
    def test_basic_function(self):
        code = "CREATE FUNCTION calc_tax(price DECIMAL, rate DECIMAL) RETURNS DECIMAL AS $$ BEGIN RETURN price * rate; END; $$ LANGUAGE plpgsql;"
        symbols, _ = _parse(code)
        assert "calc_tax" in _sym_names(symbols, "function")

    def test_function_params_in_signature(self):
        code = "CREATE FUNCTION calc_tax(price DECIMAL, rate DECIMAL) RETURNS DECIMAL AS $$ BEGIN RETURN 0; END; $$ LANGUAGE plpgsql;"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "calc_tax"][0]
        assert "(price DECIMAL, rate DECIMAL)" in s["signature"]

    def test_function_returns_in_signature(self):
        code = "CREATE FUNCTION calc_tax(price DECIMAL) RETURNS DECIMAL AS $$ BEGIN RETURN 0; END; $$ LANGUAGE plpgsql;"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "calc_tax"][0]
        assert "RETURNS DECIMAL" in s["signature"]

    def test_function_no_params(self):
        code = "CREATE FUNCTION now_utc() RETURNS TIMESTAMP AS $$ BEGIN RETURN NOW(); END; $$ LANGUAGE plpgsql;"
        symbols, _ = _parse(code)
        assert "now_utc" in _sym_names(symbols, "function")

    def test_function_with_language(self):
        code = "CREATE FUNCTION f() RETURNS INT AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;"
        symbols, _ = _parse(code)
        assert "f" in _sym_names(symbols, "function")


# ---- CREATE TRIGGER ----


class TestCreateTrigger:
    def test_before_update_trigger(self):
        code = "CREATE TRIGGER update_ts BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_timestamp();"
        symbols, _ = _parse(code)
        assert "update_ts" in _sym_names(symbols, "function")

    def test_after_insert_trigger(self):
        code = "CREATE TRIGGER audit_insert AFTER INSERT ON orders FOR EACH ROW EXECUTE FUNCTION log_insert();"
        symbols, _ = _parse(code)
        assert "audit_insert" in _sym_names(symbols, "function")

    def test_trigger_table_ref(self):
        code = "CREATE TRIGGER t BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION f();"
        _, refs = _parse(code)
        calls = _ref_names(refs, "call")
        assert "users" in calls

    def test_trigger_function_ref(self):
        code = "CREATE TRIGGER t BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_ts();"
        _, refs = _parse(code)
        calls = _ref_names(refs, "call")
        assert "update_ts" in calls

    def test_trigger_signature(self):
        code = "CREATE TRIGGER update_ts BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION f();"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "update_ts"][0]
        assert "BEFORE" in s["signature"]
        assert "UPDATE" in s["signature"]
        assert "ON users" in s["signature"]


# ---- CREATE SCHEMA ----


class TestCreateSchema:
    def test_basic_schema(self):
        symbols, _ = _parse("CREATE SCHEMA inventory;")
        assert "inventory" in _sym_names(symbols, "module")

    def test_schema_signature(self):
        symbols, _ = _parse("CREATE SCHEMA inventory;")
        s = [s for s in symbols if s["name"] == "inventory"][0]
        assert s["signature"] == "CREATE SCHEMA inventory"


# ---- CREATE TYPE ----


class TestCreateType:
    def test_enum_type(self):
        code = "CREATE TYPE status AS ENUM ('active', 'inactive', 'pending');"
        symbols, _ = _parse(code)
        assert "status" in _sym_names(symbols, "type_alias")

    def test_type_signature(self):
        code = "CREATE TYPE status AS ENUM ('active', 'inactive');"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "status"][0]
        assert "CREATE TYPE status" in s["signature"]
        assert "ENUM" in s["signature"]


# ---- CREATE SEQUENCE ----


class TestCreateSequence:
    def test_basic_sequence(self):
        code = "CREATE SEQUENCE order_seq START WITH 1 INCREMENT BY 1;"
        symbols, _ = _parse(code)
        assert "order_seq" in _sym_names(symbols, "variable")

    def test_sequence_signature(self):
        code = "CREATE SEQUENCE order_seq START WITH 1;"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "order_seq"][0]
        assert s["signature"] == "CREATE SEQUENCE order_seq"


# ---- ALTER TABLE ----


class TestAlterTable:
    def test_add_column(self):
        code = "ALTER TABLE users ADD COLUMN phone VARCHAR(20);"
        symbols, _ = _parse(code)
        assert "phone" in _sym_names(symbols, "field")

    def test_add_column_parent(self):
        code = "ALTER TABLE users ADD COLUMN phone VARCHAR(20);"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "phone"][0]
        assert s["parent_name"] == "users"


# ---- CREATE INDEX (no symbol, reference only) ----


class TestCreateIndex:
    def test_no_symbol(self):
        code = "CREATE INDEX idx_email ON users (email);"
        symbols, _ = _parse(code)
        # Indexes produce no symbols
        assert "idx_email" not in _sym_names(symbols)

    def test_index_table_ref(self):
        code = "CREATE INDEX idx_email ON users (email);"
        _, refs = _parse(code)
        ref_targets = _ref_names(refs, "reference")
        assert "users" in ref_targets


# ---- Docstrings ----


class TestDocstrings:
    def test_line_comment(self):
        code = "-- Users table\nCREATE TABLE users (id INT);"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "users"][0]
        assert s["docstring"] is not None
        assert "Users table" in s["docstring"]

    def test_block_comment(self):
        code = "/* Multi-line\n   comment */\nCREATE TABLE test (id INT);"
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "test"][0]
        assert s["docstring"] is not None
        assert "comment" in s["docstring"]

    def test_no_comment(self):
        symbols, _ = _parse("CREATE TABLE t (id INT);")
        s = [s for s in symbols if s["name"] == "t"][0]
        assert s["docstring"] is None


# ---- Registry integration ----


class TestRegistry:
    def test_dedicated_extractor(self):
        from roam.languages.registry import _DEDICATED_EXTRACTORS, get_extractor

        assert "sql" in _DEDICATED_EXTRACTORS
        ext = get_extractor("sql")
        assert ext.__class__.__name__ == "SqlExtractor"

    def test_file_extension(self):
        from roam.languages.registry import get_language_for_file

        assert get_language_for_file("schema.sql") == "sql"
