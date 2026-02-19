"""Tests for agentic memory (annotations) and Ticket 2 edge-case fixes.

Covers:
1. Annotations table creation and schema
2. roam annotate (write) command — symbol and file targets
3. roam annotations (read) command — filtering by tag, since, expiry
4. Context integration — annotations appear in roam context output
5. Reindex survival — annotations survive force reindex
6. I.1 — Schema-prefixed table names in missing-index
7. I.2 — Raw SQL CREATE INDEX parsing in missing-index
8. I.5 — information_schema guards in migration-safety
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process, invoke_cli


# ===========================================================================
# Ticket 2 fixes: direct function-call tests (no CLI fixtures needed)
# ===========================================================================


class TestSchemaPrefix:
    """I.1 — Schema-prefixed table names should be stripped."""

    def test_schema_dot_prefix_stripped(self, tmp_path):
        """Schema::create('{$schema}.users') should index under 'users'."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "001.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'{$schema}.users\', function($table) {\n'
            '    $table->id();\n'
            '    $table->string(\'email\')->index();\n'
            '});\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/001.php"])
        # Should be keyed under 'users', not '{$schema}.users'
        assert "users" in result
        assert "{$schema}.users" not in result
        # email should be indexed
        assert ("email",) in result["users"]

    def test_plain_table_name_unchanged(self, tmp_path):
        """Schema::create('orders', ...) should remain 'orders'."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "002.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'orders\', function($table) {\n'
            '    $table->id();\n'
            '    $table->index([\'user_id\', \'created_at\']);\n'
            '});\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/002.php"])
        assert "orders" in result
        assert ("user_id", "created_at") in result["orders"]

    def test_dotted_schema_prefix(self, tmp_path):
        """Schema::create('mydb.users') should index under 'users'."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "003.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'mydb.users\', function($table) {\n'
            '    $table->id();\n'
            '    $table->string(\'name\')->index();\n'
            '});\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/003.php"])
        assert "users" in result
        assert "mydb.users" not in result


class TestRawCreateIndex:
    """I.2 — Raw SQL CREATE INDEX should be parsed."""

    def test_basic_create_index(self, tmp_path):
        """CREATE INDEX on table should register the columns."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "004.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'orders\', function($table) {\n'
            '    $table->id();\n'
            '});\n'
            '\n'
            'DB::statement(\'CREATE INDEX idx_orders_status ON orders(status)\');\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/004.php"])
        assert "orders" in result
        assert ("status",) in result["orders"]

    def test_create_unique_index(self, tmp_path):
        """CREATE UNIQUE INDEX should be parsed."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "005.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'users\', function($table) {\n'
            '    $table->id();\n'
            '});\n'
            '\n'
            'DB::statement(\'CREATE UNIQUE INDEX idx_users_email ON users(email)\');\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/005.php"])
        assert "users" in result
        assert ("email",) in result["users"]

    def test_create_index_composite(self, tmp_path):
        """CREATE INDEX with multiple columns should register composite."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "006.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'orders\', function($table) {\n'
            '    $table->id();\n'
            '});\n'
            '\n'
            'DB::statement(\'CREATE INDEX idx_orders_multi ON orders(user_id, status)\');\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/006.php"])
        assert "orders" in result
        assert ("user_id",) in result["orders"]
        assert ("status",) in result["orders"]

    def test_create_index_if_not_exists(self, tmp_path):
        """CREATE INDEX IF NOT EXISTS should also be captured."""
        from roam.commands.cmd_missing_index import _parse_migration_indexes

        mig = tmp_path / "migrations" / "007.php"
        mig.parent.mkdir(parents=True)
        mig.write_text(
            '<?php\n'
            'Schema::create(\'items\', function($table) {\n'
            '    $table->id();\n'
            '});\n'
            '\n'
            'DB::statement(\'CREATE INDEX IF NOT EXISTS idx_items_sku ON items(sku)\');\n',
            encoding="utf-8",
        )

        result = _parse_migration_indexes(tmp_path, ["migrations/007.php"])
        assert "items" in result
        assert ("sku",) in result["items"]


class TestInfoSchemaGuard:
    """I.5 — information_schema queries should be recognized as guards."""

    def test_info_schema_columns_guard_drop_column(self):
        """dropColumn preceded by information_schema.columns query should not be flagged."""
        from roam.commands.cmd_migration_safety import _check_drop_column

        lines = [
            '<?php\n',
            'public function up() {\n',
            '    $exists = DB::select("SELECT * FROM information_schema.columns WHERE column_name = \'old_col\'");\n',
            '    if ($exists) {\n',
            '        Schema::table(\'users\', function($table) {\n',
            '            $table->dropColumn(\'old_col\');\n',
            '        });\n',
            '    }\n',
            '}\n',
        ]
        findings = _check_drop_column(lines, up_start=2, up_end=9)
        assert len(findings) == 0

    def test_drop_column_without_guard_still_flagged(self):
        """dropColumn without any guard should still be flagged."""
        from roam.commands.cmd_migration_safety import _check_drop_column

        lines = [
            '<?php\n',
            'public function up() {\n',
            '    Schema::table(\'users\', function($table) {\n',
            '        $table->dropColumn(\'old_col\');\n',
            '    });\n',
            '}\n',
        ]
        findings = _check_drop_column(lines, up_start=2, up_end=6)
        assert len(findings) == 1
        assert findings[0]["category"] == "drop_column_without_check"

    def test_info_schema_guard_add_column(self):
        """Column addition preceded by information_schema guard should not be flagged."""
        from roam.commands.cmd_migration_safety import _check_add_column

        lines = [
            '<?php\n',
            'public function up() {\n',
            '    $exists = DB::select("SELECT * FROM information_schema.columns WHERE column_name = \'new_col\'");\n',
            '    if (!$exists) {\n',
            '        Schema::table(\'users\', function($table) {\n',
            '            $table->string(\'new_col\');\n',
            '        });\n',
            '    }\n',
            '}\n',
        ]
        findings = _check_add_column(lines, up_start=2, up_end=9)
        assert len(findings) == 0

    def test_info_schema_guard_index_creation(self):
        """Index creation preceded by information_schema.statistics guard should pass."""
        from roam.commands.cmd_migration_safety import _check_index_creation

        lines = [
            '<?php\n',
            'public function up() {\n',
            '    $exists = DB::select("SELECT * FROM information_schema.statistics WHERE index_name = \'idx_email\'");\n',
            '    if (!$exists) {\n',
            '        Schema::table(\'users\', function($table) {\n',
            '            $table->index(\'email\');\n',
            '        });\n',
            '    }\n',
            '}\n',
        ]
        findings = _check_index_creation(lines, up_start=2, up_end=9)
        assert len(findings) == 0

    def test_info_schema_guard_raw_create_index(self):
        """Raw CREATE INDEX preceded by information_schema guard should pass."""
        from roam.commands.cmd_migration_safety import _check_index_creation

        lines = [
            '<?php\n',
            'public function up() {\n',
            '    $exists = DB::select("SELECT * FROM information_schema.statistics WHERE index_name = \'idx_email\'");\n',
            '    if (!$exists) {\n',
            '        DB::statement("CREATE INDEX idx_email ON users(email)");\n',
            '    }\n',
            '}\n',
        ]
        findings = _check_index_creation(lines, up_start=2, up_end=7)
        assert len(findings) == 0


# ===========================================================================
# Ticket 1: Annotations — CLI + integration tests
# ===========================================================================


@pytest.fixture
def annotated_project(tmp_path):
    """Create an indexed Python project for annotation tests."""
    proj = tmp_path / "annproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        'class User:\n'
        '    def login(self, password):\n'
        '        pass\n'
        '\n'
        'def create_user(name):\n'
        '    return User()\n',
    )
    (src / "utils.py").write_text(
        'def helper():\n'
        '    return 42\n',
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


class TestAnnotateCommand:
    """roam annotate — write annotations."""

    def test_annotate_symbol(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        result = invoke_cli(cli_runner, [
            "annotate", "User", "Auth bypass risk via mass assignment",
            "--tag", "security", "--author", "claude",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "Annotation saved" in result.output

    def test_annotate_file(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        result = invoke_cli(cli_runner, [
            "annotate", "src/auth.py", "Needs refactor before v2",
            "--tag", "wip",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "Annotation saved" in result.output

    def test_annotate_json(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        result = invoke_cli(cli_runner, [
            "annotate", "User", "test note", "--tag", "review",
        ], cwd=annotated_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "annotate"
        assert data["summary"]["verdict"] == "Annotation saved"
        assert data["summary"]["tag"] == "review"

    def test_annotate_unresolved_target(self, annotated_project, cli_runner, monkeypatch):
        """Annotating a non-existent symbol stores as qualified_name for future linking."""
        monkeypatch.chdir(annotated_project)
        result = invoke_cli(cli_runner, [
            "annotate", "FutureClass", "Placeholder note",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "Annotation saved" in result.output


class TestAnnotationsCommand:
    """roam annotations — read annotations."""

    def test_read_annotations_empty(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        result = invoke_cli(cli_runner, ["annotations"], cwd=annotated_project)
        assert result.exit_code == 0
        assert "No annotations found" in result.output

    def test_read_after_write(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        # Write
        invoke_cli(cli_runner, [
            "annotate", "User", "Important note", "--tag", "gotcha",
        ], cwd=annotated_project)
        # Read
        result = invoke_cli(cli_runner, [
            "annotations", "User",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "Important note" in result.output
        assert "[gotcha]" in result.output

    def test_filter_by_tag(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "security issue", "--tag", "security",
        ], cwd=annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "perf issue", "--tag", "performance",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, [
            "annotations", "--tag", "security",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "security issue" in result.output
        assert "perf issue" not in result.output

    def test_expired_annotations_hidden(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "expired note",
            "--expires", "2020-01-01",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, [
            "annotations", "User",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "expired note" not in result.output

    def test_annotations_json(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "json test", "--tag", "review", "--author", "bot",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, [
            "annotations", "User",
        ], cwd=annotated_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "annotations"
        assert data["summary"]["count"] == 1
        anns = data["annotations"]
        assert len(anns) == 1
        assert anns[0]["content"] == "json test"
        assert anns[0]["tag"] == "review"
        assert anns[0]["author"] == "bot"

    def test_all_annotations(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "note1", "--tag", "security",
        ], cwd=annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "src/auth.py", "note2", "--tag", "wip",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, ["annotations"], cwd=annotated_project)
        assert result.exit_code == 0
        assert "2 annotations" in result.output


class TestContextAnnotationIntegration:
    """Annotations should appear in roam context output."""

    def test_context_text_shows_annotations(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "Watch out for mass assignment",
            "--tag", "security",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, [
            "context", "User", "--task", "review",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "Annotations" in result.output
        assert "Watch out for mass assignment" in result.output

    def test_context_default_mode_shows_annotations(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "Default mode note",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, [
            "context", "User",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "Default mode note" in result.output

    def test_context_json_includes_annotations(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "JSON mode note", "--tag", "gotcha",
        ], cwd=annotated_project)

        result = invoke_cli(cli_runner, [
            "context", "User", "--task", "debug",
        ], cwd=annotated_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "annotations" in data
        assert len(data["annotations"]) >= 1
        assert data["annotations"][0]["content"] == "JSON mode note"


class TestReindexSurvival:
    """Annotations should survive both incremental and force reindex."""

    def test_annotations_survive_incremental(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "survives incremental",
        ], cwd=annotated_project)

        # Add a new file and reindex
        (annotated_project / "src" / "new.py").write_text("def new_func(): pass\n")
        git_commit(annotated_project, "add new file")
        index_in_process(annotated_project)

        result = invoke_cli(cli_runner, [
            "annotations", "User",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "survives incremental" in result.output

    def test_annotations_survive_force_reindex(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "survives force reindex", "--tag", "important",
        ], cwd=annotated_project)

        # Force reindex (deletes and recreates DB)
        index_in_process(annotated_project, "--force")

        result = invoke_cli(cli_runner, [
            "annotations", "User",
        ], cwd=annotated_project)
        assert result.exit_code == 0
        assert "survives force reindex" in result.output

    def test_annotation_relinked_after_force(self, annotated_project, cli_runner, monkeypatch):
        monkeypatch.chdir(annotated_project)
        invoke_cli(cli_runner, [
            "annotate", "User", "relinked note",
        ], cwd=annotated_project)

        index_in_process(annotated_project, "--force")

        # Verify via JSON that symbol_id is populated (re-linked)
        result = invoke_cli(cli_runner, [
            "annotations", "User",
        ], cwd=annotated_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["count"] >= 1
        # After relinking, the annotation should have a non-null symbol_id
        ann = data["annotations"][0]
        assert ann["symbol_id"] is not None


class TestGatherAnnotationsHelper:
    """Unit test for gather_annotations helper."""

    def test_gather_annotations_returns_list(self):
        """gather_annotations should return a list of dicts."""
        from roam.commands.context_helpers import gather_annotations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER,
                qualified_name TEXT,
                file_path TEXT,
                tag TEXT,
                content TEXT NOT NULL,
                author TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO annotations (symbol_id, qualified_name, content, tag, author) "
            "VALUES (1, 'MyClass', 'test note', 'review', 'bot')"
        )
        conn.commit()

        sym = {"id": 1, "qualified_name": "MyClass", "name": "MyClass"}
        result = gather_annotations(conn, sym=sym)
        assert len(result) == 1
        assert result[0]["content"] == "test note"
        assert result[0]["tag"] == "review"
        conn.close()

    def test_gather_annotations_skips_expired(self):
        """Expired annotations should be excluded."""
        from roam.commands.context_helpers import gather_annotations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER,
                qualified_name TEXT,
                file_path TEXT,
                tag TEXT,
                content TEXT NOT NULL,
                author TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO annotations (symbol_id, qualified_name, content, expires_at) "
            "VALUES (1, 'MyClass', 'expired', '2020-01-01')"
        )
        conn.execute(
            "INSERT INTO annotations (symbol_id, qualified_name, content) "
            "VALUES (1, 'MyClass', 'active')"
        )
        conn.commit()

        sym = {"id": 1, "qualified_name": "MyClass", "name": "MyClass"}
        result = gather_annotations(conn, sym=sym)
        assert len(result) == 1
        assert result[0]["content"] == "active"
        conn.close()

    def test_gather_annotations_no_table(self):
        """Should return empty list if table doesn't exist."""
        from roam.commands.context_helpers import gather_annotations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        sym = {"id": 1, "qualified_name": "X", "name": "X"}
        result = gather_annotations(conn, sym=sym)
        assert result == []
        conn.close()
