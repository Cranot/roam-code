"""Integration tests for the indexing pipeline.

Covers:
- Full indexing (DB creation, files/symbols/edges population, exit codes)
- Incremental indexing (skip unchanged, detect modified/new/deleted)
- Language detection (.py, .js, .ts, .go)
- Schema correctness (required columns, safe migrations)
- Edge cases (empty project, --force flag, .gitignore respected)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process


# ===========================================================================
# Shared fixtures
# ===========================================================================

@pytest.fixture
def index_project(tmp_path):
    """A minimal Python project with cross-file references."""
    proj = tmp_path / "idx_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def hello():\n"
        "    return 'world'\n"
    )
    (proj / "lib.py").write_text(
        "from app import hello\n"
        "\n"
        "def greet():\n"
        "    return hello()\n"
    )
    git_init(proj)
    return proj


@pytest.fixture
def multilang_project(tmp_path):
    """A project with files in several languages."""
    proj = tmp_path / "ml_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def main(): pass\n")
    (proj / "app.js").write_text("function run() { return 1; }\n")
    (proj / "util.ts").write_text("export function helper(): number { return 2; }\n")
    (proj / "server.go").write_text(
        "package main\n"
        "\n"
        "func serve() {}\n"
    )
    git_init(proj)
    return proj


def _open_db_for(proj):
    """Open the roam DB for a project (read-only), passing project_root explicitly."""
    from roam.db.connection import open_db
    return open_db(readonly=True, project_root=proj)


# ===========================================================================
# Full indexing (5 tests)
# ===========================================================================

class TestFullIndexing:
    """Tests that verify a full index run creates the expected DB artefacts."""

    def test_index_creates_db(self, index_project):
        """Running `roam index` creates the .roam/index.db file."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        # DB file may be at .roam/index.db (current default)
        db_path = index_project / ".roam" / "index.db"
        assert db_path.exists(), (
            f"Expected DB at {db_path} but it does not exist. "
            f"Contents of .roam/: {list((index_project / '.roam').iterdir()) if (index_project / '.roam').exists() else 'dir missing'}"
        )

    def test_index_populates_files(self, index_project):
        """The files table has one entry per source file."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        with _open_db_for(index_project) as conn:
            rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
            paths = [r["path"] for r in rows]
            assert "app.py" in paths
            assert "lib.py" in paths
            # .gitignore is not a parseable source — may or may not appear
            # The key point is that our two Python files are indexed
            assert len(paths) >= 2

    def test_index_populates_symbols(self, index_project):
        """The symbols table has entries for functions defined in source files."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        with _open_db_for(index_project) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert count >= 2, f"Expected at least 2 symbols (hello, greet), got {count}"
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "hello" in names
            assert "greet" in names

    def test_index_populates_edges(self, index_project):
        """The edges table has entries for cross-file references."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        with _open_db_for(index_project) as conn:
            count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            # lib.py imports and calls hello from app.py -> at least 1 edge
            assert count >= 1, f"Expected at least 1 edge, got {count}"

    def test_index_exit_code_zero(self, index_project):
        """Index exits with code 0 on a clean project."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index exited {rc}:\n{out}"


# ===========================================================================
# Incremental indexing (4 tests)
# ===========================================================================

class TestIncrementalIndexing:
    """Tests that verify incremental re-indexing behaviour."""

    def test_incremental_skips_unchanged(self, index_project):
        """Re-running index on an unchanged project reports 'up to date'."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0, f"First index failed:\n{out1}"

        # Wait briefly so mtimes are stable
        time.sleep(0.05)

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Second index failed:\n{out2}"
        # The indexer should report "up to date" when nothing changed
        assert "up to date" in out2.lower(), (
            f"Expected 'up to date' in output, got:\n{out2}"
        )

    def test_incremental_detects_modified(self, index_project):
        """Modifying a file triggers re-indexing of that file."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        # Modify app.py — add a new function
        time.sleep(0.1)  # Ensure mtime differs
        (index_project / "app.py").write_text(
            "def hello():\n"
            "    return 'world'\n"
            "\n"
            "def farewell():\n"
            "    return 'goodbye'\n"
        )
        git_commit(index_project, "add farewell")

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Incremental index failed:\n{out2}"

        with _open_db_for(index_project) as conn:
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "farewell" in names, (
                f"New symbol 'farewell' not found after incremental index. Symbols: {names}"
            )

    def test_incremental_detects_new_file(self, index_project):
        """Adding a new file indexes its symbols."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        # Add a new file
        time.sleep(0.1)
        (index_project / "util.py").write_text(
            "def helper():\n"
            "    return 42\n"
        )
        git_commit(index_project, "add util")

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Incremental index failed:\n{out2}"

        with _open_db_for(index_project) as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "util.py" in paths, f"New file 'util.py' not found in files table: {paths}"
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "helper" in names, f"Symbol 'helper' from new file not found: {names}"

    def test_incremental_detects_deleted_file(self, index_project):
        """Deleting a file removes its symbols from the index."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        with _open_db_for(index_project) as conn:
            initial_paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "app.py" in initial_paths

        # Delete app.py
        (index_project / "app.py").unlink()
        git_commit(index_project, "remove app.py")

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Incremental index failed:\n{out2}"

        with _open_db_for(index_project) as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "app.py" not in paths, (
                f"Deleted file 'app.py' still present in files table: {paths}"
            )
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "hello" not in names, (
                f"Symbol 'hello' from deleted file still present: {names}"
            )


# ===========================================================================
# Language detection (4 tests)
# ===========================================================================

class TestLanguageDetection:
    """Tests that verify correct language tagging in the files table."""

    def test_detects_python(self, multilang_project):
        """.py files are detected as Python."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute(
                "SELECT language FROM files WHERE path = 'main.py'"
            ).fetchone()
            assert row is not None, "main.py not found in files table"
            assert row["language"] == "python"

    def test_detects_javascript(self, multilang_project):
        """.js files are detected as JavaScript."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute(
                "SELECT language FROM files WHERE path = 'app.js'"
            ).fetchone()
            assert row is not None, "app.js not found in files table"
            assert row["language"] == "javascript"

    def test_detects_typescript(self, multilang_project):
        """.ts files are detected as TypeScript."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute(
                "SELECT language FROM files WHERE path = 'util.ts'"
            ).fetchone()
            assert row is not None, "util.ts not found in files table"
            assert row["language"] == "typescript"

    def test_detects_go(self, multilang_project):
        """.go files are detected as Go."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute(
                "SELECT language FROM files WHERE path = 'server.go'"
            ).fetchone()
            assert row is not None, "server.go not found in files table"
            assert row["language"] == "go"


# ===========================================================================
# Schema correctness (4 tests)
# ===========================================================================

class TestSchemaCorrectness:
    """Tests that verify the DB schema has required columns and survives migrations."""

    def test_schema_files_table(self, index_project):
        """The files table has the required columns."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        with _open_db_for(index_project) as conn:
            info = conn.execute("PRAGMA table_info(files)").fetchall()
            col_names = {r["name"] for r in info}
            for required in ("id", "path", "language", "hash"):
                assert required in col_names, (
                    f"Missing column '{required}' in files table. Columns: {col_names}"
                )

    def test_schema_symbols_table(self, index_project):
        """The symbols table has the required columns."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        with _open_db_for(index_project) as conn:
            info = conn.execute("PRAGMA table_info(symbols)").fetchall()
            col_names = {r["name"] for r in info}
            for required in ("id", "name", "kind", "file_id", "line_start", "line_end"):
                assert required in col_names, (
                    f"Missing column '{required}' in symbols table. Columns: {col_names}"
                )

    def test_schema_edges_table(self, index_project):
        """The edges table has the required columns."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        with _open_db_for(index_project) as conn:
            info = conn.execute("PRAGMA table_info(edges)").fetchall()
            col_names = {r["name"] for r in info}
            for required in ("source_id", "target_id", "kind"):
                assert required in col_names, (
                    f"Missing column '{required}' in edges table. Columns: {col_names}"
                )

    def test_schema_migrations_safe(self, index_project):
        """Running ensure_schema twice does not crash (idempotent migrations)."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        from roam.db.connection import open_db, ensure_schema
        old_cwd = os.getcwd()
        try:
            os.chdir(str(index_project))
            # Open in write mode and re-apply schema + migrations
            with open_db(readonly=False) as conn:
                ensure_schema(conn)
                # Verify tables still work after double-migration
                count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                assert count >= 2
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# Edge cases (3 tests)
# ===========================================================================

class TestEdgeCases:
    """Tests for unusual but important scenarios."""

    def test_index_empty_project(self, tmp_path):
        """A project with no source files indexes without error."""
        proj = tmp_path / "empty_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        git_init(proj)

        out, rc = index_in_process(proj)
        assert rc == 0, f"Index of empty project failed:\n{out}"

    def test_index_force_flag(self, index_project):
        """The --force flag re-indexes everything even when unchanged."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        # Normal re-index should be a no-op
        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0
        assert "up to date" in out2.lower()

        # Force re-index should process files again
        out3, rc3 = index_in_process(index_project, "--force")
        assert rc3 == 0
        # After force, the output should NOT say "up to date" — it should
        # report files processed
        assert "up to date" not in out3.lower(), (
            f"Expected force reindex to process files, but got:\n{out3}"
        )

        # Verify data is still correct after force reindex
        with _open_db_for(index_project) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert count >= 2, f"Expected at least 2 symbols after force reindex, got {count}"

    def test_index_gitignore_respected(self, tmp_path):
        """Files listed in .gitignore are not indexed."""
        proj = tmp_path / "gi_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\nbuild/\nsecret.py\n")
        (proj / "app.py").write_text("def main(): pass\n")

        # Create files that should be ignored
        build_dir = proj / "build"
        build_dir.mkdir()
        (build_dir / "output.py").write_text("def compiled(): pass\n")
        (proj / "secret.py").write_text("API_KEY = 'hunter2'\n")

        git_init(proj)

        out, rc = index_in_process(proj)
        assert rc == 0, f"Index failed:\n{out}"

        with _open_db_for(proj) as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "app.py" in paths, f"app.py should be indexed but is missing: {paths}"
            assert "secret.py" not in paths, (
                f"secret.py should be gitignored but was indexed: {paths}"
            )
            assert "build/output.py" not in paths, (
                f"build/output.py should be gitignored but was indexed: {paths}"
            )
