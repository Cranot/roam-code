"""Tests for the roam mutate command -- syntax-less agentic editing."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, invoke_cli


# ===========================================================================
# Fixture
# ===========================================================================

@pytest.fixture
def mutate_project(project_factory):
    """A small Python project for testing mutate transforms."""
    return project_factory({
        "models.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
        ),
        "service.py": (
            "from models import User\n"
            "\n"
            "def create_user(name):\n"
            "    return User(name)\n"
            "\n"
            "def unused_helper():\n"
            "    return 42\n"
        ),
        "api.py": (
            "from service import create_user\n"
            "\n"
            "def handle_request(data):\n"
            "    return create_user(data['name'])\n"
        ),
    })


# ===========================================================================
# Codegen tests
# ===========================================================================

class TestCodegen:
    """Tests for codegen utilities."""

    def test_generate_import_python(self):
        from roam.refactor.codegen import generate_import
        result = generate_import("python", "service.py", "create_user", "api.py")
        assert "from service import create_user" == result

    def test_generate_import_javascript(self):
        from roam.refactor.codegen import generate_import
        result = generate_import("javascript", "lib/utils.js", "helper",
                                 "src/app.js")
        assert "import { helper } from" in result
        assert "lib/utils" in result

    def test_detect_language(self):
        from roam.refactor.codegen import detect_language
        assert detect_language("foo.py") == "python"
        assert detect_language("bar.js") == "javascript"
        assert detect_language("baz.go") == "go"
        assert detect_language("qux.ts") == "typescript"

    def test_compute_relative_path(self):
        from roam.refactor.codegen import compute_relative_path
        result = compute_relative_path("src/app.js", "src/utils.js")
        assert "utils" in result


# ===========================================================================
# Move tests
# ===========================================================================

class TestMoveSymbol:
    """Tests for the move_symbol transform."""

    def test_move_dry_run(self, mutate_project, monkeypatch):
        """Dry run returns changes without writing files."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import move_symbol

        with open_db(readonly=True) as conn:
            result = move_symbol(conn, "create_user", "new_service.py",
                                 dry_run=True)

        assert result["operation"] == "move"
        assert "error" not in result
        assert len(result["files_modified"]) > 0
        # Dry run should not create the target file
        assert not os.path.exists(mutate_project / "new_service.py")

    def test_move_files_modified(self, mutate_project, monkeypatch):
        """Move reports correct number of modified files."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import move_symbol

        with open_db(readonly=True) as conn:
            result = move_symbol(conn, "create_user", "new_service.py",
                                 dry_run=True)

        # Should modify: new_service.py (create), service.py (remove),
        # and at least api.py (rewrite import)
        assert len(result["files_modified"]) >= 2

    def test_move_apply(self, mutate_project, monkeypatch):
        """Apply mode actually writes files."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import move_symbol

        with open_db(readonly=True) as conn:
            result = move_symbol(conn, "create_user", "new_service.py",
                                 dry_run=False)

        assert result["operation"] == "move"
        # Target file should now exist
        target = mutate_project / "new_service.py"
        assert target.exists()
        content = target.read_text()
        assert "create_user" in content

    def test_move_updates_imports(self, mutate_project, monkeypatch):
        """Caller imports are rewritten to point to new location."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import move_symbol

        with open_db(readonly=True) as conn:
            result = move_symbol(conn, "create_user", "new_service.py",
                                 dry_run=True)

        # Check that api.py's import is planned to be rewritten
        api_changes = [f for f in result["files_modified"]
                       if f["path"].replace("\\", "/").endswith("api.py")]
        if api_changes:
            changes = api_changes[0]["changes"]
            rewrite_found = any(
                c.get("type") == "replace" and "new_service" in c.get("new_text", "")
                for c in changes
            )
            assert rewrite_found, "api.py import not rewritten to new_service"


# ===========================================================================
# Rename tests
# ===========================================================================

class TestRenameSymbol:
    """Tests for the rename_symbol transform."""

    def test_rename_dry_run(self, mutate_project, monkeypatch):
        """Dry run returns planned rename changes."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import rename_symbol

        with open_db(readonly=True) as conn:
            result = rename_symbol(conn, "create_user", "make_user",
                                   dry_run=True)

        assert result["operation"] == "rename"
        assert "error" not in result
        assert result["new_name"] == "make_user"
        assert len(result["files_modified"]) > 0

    def test_rename_updates_references(self, mutate_project, monkeypatch):
        """All references are updated in the plan."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import rename_symbol

        with open_db(readonly=True) as conn:
            result = rename_symbol(conn, "create_user", "make_user",
                                   dry_run=True)

        # Should have changes that replace create_user with make_user
        all_changes = []
        for fmod in result["files_modified"]:
            all_changes.extend(fmod["changes"])
        replace_changes = [c for c in all_changes
                           if c.get("type") == "replace"
                           and "make_user" in c.get("new_text", "")]
        assert len(replace_changes) > 0, "no rename replacements found"


# ===========================================================================
# Add-call tests
# ===========================================================================

class TestAddCall:
    """Tests for the add_call transform."""

    def test_add_call_generates_import(self, mutate_project, monkeypatch):
        """Adds import if calling a symbol from a different file."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import add_call

        with open_db(readonly=True) as conn:
            result = add_call(conn, "handle_request", "unused_helper",
                              dry_run=True)

        assert result["operation"] == "add-call"
        assert "error" not in result
        # Should have an insert change for the import
        all_changes = []
        for fmod in result["files_modified"]:
            all_changes.extend(fmod["changes"])
        import_inserts = [c for c in all_changes
                          if c.get("type") == "insert"
                          and "import" in c.get("text", "").lower()]
        assert len(import_inserts) > 0, "no import generated"

    def test_add_call_no_duplicate_import(self, mutate_project, monkeypatch):
        """Skips import if it already exists."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import add_call

        # handle_request already imports create_user from service
        with open_db(readonly=True) as conn:
            result = add_call(conn, "handle_request", "create_user",
                              dry_run=True)

        assert result["operation"] == "add-call"
        # Should NOT have an import insert (it already exists)
        all_changes = []
        for fmod in result["files_modified"]:
            all_changes.extend(fmod["changes"])
        import_inserts = [c for c in all_changes
                          if c.get("type") == "insert"
                          and "import" in c.get("text", "").lower()]
        assert len(import_inserts) == 0, "duplicate import generated"


# ===========================================================================
# Extract tests
# ===========================================================================

class TestExtractSymbol:
    """Tests for the extract_symbol transform."""

    def test_extract_creates_function(self, mutate_project, monkeypatch):
        """Extract creates a new function definition."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import extract_symbol

        with open_db(readonly=True) as conn:
            result = extract_symbol(conn, "create_user", 4, 4,
                                    "build_user", dry_run=True)

        assert result["operation"] == "extract"
        assert "error" not in result
        assert result["new_name"] == "build_user"
        # Should have an insert change with the new function
        all_changes = []
        for fmod in result["files_modified"]:
            all_changes.extend(fmod["changes"])
        func_inserts = [c for c in all_changes
                        if c.get("type") == "insert"
                        and "build_user" in c.get("text", "")]
        assert len(func_inserts) > 0, "new function not created"

    def test_extract_replaces_with_call(self, mutate_project, monkeypatch):
        """Extracted lines are replaced with a call to the new function."""
        monkeypatch.chdir(mutate_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import extract_symbol

        with open_db(readonly=True) as conn:
            result = extract_symbol(conn, "create_user", 4, 4,
                                    "build_user", dry_run=True)

        all_changes = []
        for fmod in result["files_modified"]:
            all_changes.extend(fmod["changes"])
        replace_changes = [c for c in all_changes
                           if c.get("type") == "replace"
                           and "build_user()" in c.get("new_text", "")]
        assert len(replace_changes) > 0, "extracted lines not replaced with call"


# ===========================================================================
# CLI tests
# ===========================================================================

class TestCLI:
    """Tests for the mutate CLI commands."""

    def test_cli_mutate_move_runs(self, mutate_project, monkeypatch):
        """roam mutate move exits with code 0."""
        monkeypatch.chdir(mutate_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["mutate", "move", "create_user",
                                     "new_service.py"],
                            cwd=mutate_project)
        assert result.exit_code == 0

    def test_cli_mutate_rename_runs(self, mutate_project, monkeypatch):
        """roam mutate rename exits with code 0."""
        monkeypatch.chdir(mutate_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["mutate", "rename", "create_user",
                                     "make_user"],
                            cwd=mutate_project)
        assert result.exit_code == 0

    def test_cli_mutate_move_json(self, mutate_project, monkeypatch):
        """JSON output has valid envelope."""
        monkeypatch.chdir(mutate_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["mutate", "move", "create_user",
                                     "new_service.py"],
                            cwd=mutate_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "mutate"
        assert "summary" in data
        assert "verdict" in data["summary"]

    def test_cli_mutate_help(self, mutate_project, monkeypatch):
        """--help works for mutate group."""
        monkeypatch.chdir(mutate_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["mutate", "--help"],
                            cwd=mutate_project)
        assert result.exit_code == 0
        assert "move" in result.output.lower()
        assert "rename" in result.output.lower()

    def test_mutate_unknown_symbol(self, mutate_project, monkeypatch):
        """Graceful error for unknown symbol."""
        monkeypatch.chdir(mutate_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["mutate", "move",
                                     "nonexistent_symbol_xyz",
                                     "target.py"],
                            cwd=mutate_project)
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_mutate_verdict_line(self, mutate_project, monkeypatch):
        """Text output starts with VERDICT."""
        monkeypatch.chdir(mutate_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["mutate", "move", "create_user",
                                     "new_service.py"],
                            cwd=mutate_project)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")
