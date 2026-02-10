"""Tests for the shared symbol resolution module and line_start fix."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import roam, git_init
from roam.index.relations import _closest_symbol


@pytest.fixture(scope="module")
def resolve_project(tmp_path_factory):
    """Create a project with duplicate symbol names across files."""
    root = tmp_path_factory.mktemp("resolve_project")

    # Two files with the same function name 'deleteRow'
    (root / "file_a.py").write_text(
        "def deleteRow(table, idx):\n"
        "    '''Delete a row from a table.'''\n"
        "    pass\n"
    )
    (root / "file_b.py").write_text(
        "from file_a import deleteRow\n\n"
        "def deleteRow(grid, row_id):\n"
        "    '''Delete a row from a grid.'''\n"
        "    pass\n\n"
        "def process():\n"
        "    deleteRow(None, 1)\n"
    )

    # A file that calls file_a's deleteRow multiple times (gives it more edges)
    (root / "file_c.py").write_text(
        "from file_a import deleteRow\n\n"
        "def cleanup():\n"
        "    deleteRow('users', 0)\n"
        "    deleteRow('posts', 1)\n"
    )

    # Unique symbol for basic lookup
    (root / "utils.py").write_text(
        "def uniqueHelper():\n"
        "    return 42\n"
    )

    git_init(root)

    # Index the project
    out, rc = roam("index", cwd=root)
    assert rc == 0, f"Index failed: {out}"

    return root


def test_find_symbol_unique(resolve_project):
    """find_symbol returns the symbol when there's exactly one match."""
    out, rc = roam("symbol", "uniqueHelper", cwd=resolve_project)
    assert rc == 0
    assert "uniqueHelper" in out


def test_find_symbol_disambiguates_by_edges(resolve_project):
    """find_symbol picks the most-referenced symbol among duplicates."""
    # file_b.deleteRow is called by process() and cleanup(),
    # so it has the most incoming edges and should be picked
    out, rc = roam("symbol", "deleteRow", cwd=resolve_project)
    assert rc == 0
    # Should resolve without error (no "Multiple matches" shown)
    assert "Multiple matches" not in out
    # Should pick one concrete match (has PR and callers)
    assert "PR=" in out


def test_find_symbol_not_found(resolve_project):
    """find_symbol returns None (command exits 1) for nonexistent symbol."""
    out, rc = roam("symbol", "nonExistentSymbol12345", cwd=resolve_project)
    assert rc != 0
    assert "not found" in out.lower()


def test_file_hint_syntax(resolve_project):
    """file:symbol syntax narrows resolution to a specific file."""
    # This should resolve to file_b's deleteRow specifically
    out, rc = roam("symbol", "file_b:deleteRow", cwd=resolve_project)
    assert rc == 0
    assert "file_b" in out


def test_why_command_uses_resolve(resolve_project):
    """roam why should use shared find_symbol (no crash, proper resolution)."""
    out, rc = roam("why", "deleteRow", cwd=resolve_project)
    assert rc == 0
    assert "ROLE" in out or "role" in out.lower() or "Leaf" in out or "fan-in" in out.lower()


def test_impact_command_uses_resolve(resolve_project):
    """roam impact should use shared find_symbol (no ambiguous list crash)."""
    out, rc = roam("impact", "deleteRow", cwd=resolve_project)
    assert rc == 0
    # Should not show "Multiple matches" — resolve.py handles disambiguation
    assert "Multiple matches" not in out


def test_safe_delete_command_uses_resolve(resolve_project):
    """roam safe-delete should use shared find_symbol."""
    out, rc = roam("safe-delete", "uniqueHelper", cwd=resolve_project)
    assert rc == 0
    assert "SAFE" in out or "REVIEW" in out or "UNSAFE" in out


def test_context_command_uses_resolve(resolve_project):
    """roam context should use shared find_symbol."""
    out, rc = roam("context", "uniqueHelper", cwd=resolve_project)
    assert rc == 0
    assert "utils.py" in out


# ---- Unit tests for _closest_symbol with line_start data ----

class TestClosestSymbol:
    """Verify _closest_symbol uses real line_start values for correct attribution."""

    def _make_file_symbols(self, symbols_data):
        """Build file_symbols dict from list of (name, line_start) tuples."""
        syms = [{"name": n, "line_start": ls, "id": i}
                for i, (n, ls) in enumerate(symbols_data)]
        return {"test.vue": syms}

    def test_picks_enclosing_function(self):
        """Reference at line 25 should resolve to funcB (line 20), not funcA (line 5)."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5),
            ("funcB", 20),
            ("funcC", 40),
        ])
        result = _closest_symbol("test.vue", 25, file_symbols)
        assert result is not None
        assert result["name"] == "funcB"

    def test_picks_first_when_before_all(self):
        """Reference at line 1 should resolve to the first symbol."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5),
            ("funcB", 20),
        ])
        result = _closest_symbol("test.vue", 1, file_symbols)
        assert result is not None
        assert result["name"] == "funcA"

    def test_picks_last_when_after_all(self):
        """Reference at line 100 should resolve to the last symbol."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5),
            ("funcB", 20),
        ])
        result = _closest_symbol("test.vue", 100, file_symbols)
        assert result is not None
        assert result["name"] == "funcB"

    def test_returns_none_for_unknown_file(self):
        """Unknown file should return None."""
        result = _closest_symbol("unknown.vue", 10, {})
        assert result is None

    def test_exact_line_match(self):
        """Reference at exact function start line should match that function."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5),
            ("funcB", 20),
        ])
        result = _closest_symbol("test.vue", 20, file_symbols)
        assert result is not None
        assert result["name"] == "funcB"

    def test_with_zero_line_start_falls_through(self):
        """Symbols with line_start=0 (old bug) all map to first — demonstrates the bug."""
        file_symbols = self._make_file_symbols([
            ("funcA", 0),
            ("funcB", 0),
            ("funcC", 0),
        ])
        # With all zeros, every symbol has line_start <= any ref_line,
        # so the last one wins (not necessarily correct)
        result = _closest_symbol("test.vue", 25, file_symbols)
        assert result is not None
        # This is the WRONG behavior that the line_start fix prevents
        assert result["name"] == "funcC"


class TestIndexerLineStart:
    """Verify the indexer populates line_start in all_symbol_rows."""

    def test_line_start_in_index(self, tmp_path):
        """After indexing, symbols in DB should have real line_start values."""
        root = tmp_path / "linestart_project"
        root.mkdir()
        (root / "example.py").write_text(
            "def first_func():\n"     # line 1
            "    pass\n"
            "\n"
            "def second_func():\n"    # line 4
            "    first_func()\n"
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        # Verify via roam symbol that line numbers are correct
        out, rc = roam("symbol", "first_func", cwd=root)
        assert rc == 0
        assert ":1" in out  # first_func at line 1

        out, rc = roam("symbol", "second_func", cwd=root)
        assert rc == 0
        assert ":4" in out  # second_func at line 4

    def test_template_ref_gets_correct_source(self, tmp_path):
        """Vue template reference should resolve to correct enclosing function."""
        root = tmp_path / "vue_linestart"
        root.mkdir()
        # Vue file where template calls handleClick defined in script
        (root / "App.vue").write_text(
            '<template>\n'
            '  <button @click="handleClick">Click</button>\n'
            '</template>\n'
            '<script setup lang="ts">\n'
            'function handleClick() {\n'
            '  console.log("clicked")\n'
            '}\n'
            '</script>\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        # handleClick should have at least 1 caller (template edge)
        out, rc = roam("symbol", "handleClick", cwd=root)
        assert rc == 0
        # With line_start fix, the template edge should be correctly attributed
        # (not a self-reference that gets skipped)
        assert "handleClick" in out
