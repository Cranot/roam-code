"""Tests for the shared symbol resolution module and line_start fix."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import roam, git_init
from roam.index.relations import _closest_symbol, _match_import_path
from roam.index.parser import extract_vue_template, scan_template_references


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
    # Should pick one concrete match with callers
    assert "Callers" in out


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
    """Verify _closest_symbol uses line_end containment for correct attribution."""

    def _make_file_symbols(self, symbols_data):
        """Build file_symbols dict from list of (name, line_start, line_end) tuples."""
        syms = [{"name": n, "line_start": ls, "line_end": le, "id": i}
                for i, (n, ls, le) in enumerate(symbols_data)]
        return {"test.vue": syms}

    def test_picks_enclosing_function(self):
        """Reference at line 25 should resolve to funcB (line 20-35), not funcA (line 5-15)."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 15),
            ("funcB", 20, 35),
            ("funcC", 40, 60),
        ])
        result = _closest_symbol("test.vue", 25, file_symbols)
        assert result is not None
        assert result["name"] == "funcB"

    def test_picks_first_when_before_all(self):
        """Reference at line 1 should resolve to the first symbol (no containment)."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 15),
            ("funcB", 20, 35),
        ])
        result = _closest_symbol("test.vue", 1, file_symbols)
        assert result is not None
        assert result["name"] == "funcA"

    def test_after_all_functions_returns_first(self):
        """Reference after all function bodies → module scope → returns first symbol."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 15),
            ("funcB", 20, 35),
        ])
        result = _closest_symbol("test.vue", 100, file_symbols)
        assert result is not None
        # No function contains line 100 → module scope → returns syms[0]
        assert result["name"] == "funcA"

    def test_returns_none_for_unknown_file(self):
        """Unknown file should return None."""
        result = _closest_symbol("unknown.vue", 10, {})
        assert result is None

    def test_exact_line_match(self):
        """Reference at exact function start line should match that function."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 15),
            ("funcB", 20, 35),
        ])
        result = _closest_symbol("test.vue", 20, file_symbols)
        assert result is not None
        assert result["name"] == "funcB"

    def test_with_zero_line_end_falls_to_first(self):
        """Symbols with line_end=0 (no data) → no containment → returns first symbol."""
        file_symbols = self._make_file_symbols([
            ("funcA", 0, 0),
            ("funcB", 0, 0),
            ("funcC", 0, 0),
        ])
        # With all line_end=0, no containment match → returns syms[0]
        result = _closest_symbol("test.vue", 25, file_symbols)
        assert result is not None
        assert result["name"] == "funcA"


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


# ---- Bug 1: Nested <template> extraction tests ----

class TestExtractVueTemplate:
    """Verify extract_vue_template handles nested <template #slot> tags."""

    def test_simple_template_unchanged(self):
        """No nesting — same result as before."""
        source = b'<template>\n  <div>Hello</div>\n</template>\n<script></script>'
        result = extract_vue_template(source)
        assert result is not None
        content, start_line = result
        assert "<div>Hello</div>" in content
        # Content starts right after <template>, still on line 1
        assert start_line == 1

    def test_nested_template_returns_full_content(self):
        """Source with <template #slot> nested tag — content after inner </template> is captured."""
        source = (
            b'<template>\n'           # L1: outer open
            b'  <div>\n'              # L2
            b'    <template #header>\n'  # L3: nested open
            b'      <h1>Title</h1>\n' # L4
            b'    </template>\n'      # L5: nested close (old regex stopped here!)
            b'    <p>After nested</p>\n'  # L6: must be captured
            b'    <button @click="handleClick">Go</button>\n'  # L7
            b'  </div>\n'             # L8
            b'</template>\n'          # L9: outer close
            b'<script setup lang="ts">\n'
            b'function handleClick() {}\n'
            b'</script>\n'
        )
        result = extract_vue_template(source)
        assert result is not None
        content, start_line = result
        # Content after the nested </template> must be present
        assert "After nested" in content
        assert "handleClick" in content
        # Content starts right after <template>, still on line 1
        assert start_line == 1

    def test_deeply_nested_templates(self):
        """3 levels of nesting."""
        source = (
            b'<template>\n'
            b'  <template #outer>\n'
            b'    <template #inner>\n'
            b'      <span>Deep</span>\n'
            b'    </template>\n'
            b'  </template>\n'
            b'  <div>After all nesting</div>\n'
            b'</template>\n'
        )
        result = extract_vue_template(source)
        assert result is not None
        content, start_line = result
        assert "Deep" in content
        assert "After all nesting" in content

    def test_no_template_returns_none(self):
        """File without <template> returns None."""
        source = b'<script setup>\nconst x = 1\n</script>\n'
        result = extract_vue_template(source)
        assert result is None

    def test_malformed_no_closing_returns_none(self):
        """Malformed file with no matching </template> returns None."""
        source = b'<template>\n  <div>Open forever\n'
        result = extract_vue_template(source)
        assert result is None

    def test_self_closing_template_ignored(self):
        """Self-closing <template /> shouldn't affect depth counting."""
        source = (
            b'<template>\n'
            b'  <template />\n'
            b'  <div>Still here</div>\n'
            b'</template>\n'
        )
        result = extract_vue_template(source)
        assert result is not None
        content, _ = result
        assert "Still here" in content

    def test_template_handlers_after_nested_slot(self, tmp_path):
        """Integration: Vue file with nested slot, handler after inner </template> has fan-in > 0."""
        root = tmp_path / "nested_template"
        root.mkdir()
        (root / "Modal.vue").write_text(
            '<template>\n'
            '  <div>\n'
            '    <template #header>\n'
            '      <h1>Title</h1>\n'
            '    </template>\n'
            '    <button @click="handleSubmit">Submit</button>\n'
            '  </div>\n'
            '</template>\n'
            '<script setup lang="ts">\n'
            'function handleSubmit() {\n'
            '  console.log("submitted")\n'
            '}\n'
            '</script>\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        out, rc = roam("symbol", "handleSubmit", cwd=root)
        assert rc == 0
        assert "handleSubmit" in out


# ---- Bug 2: Import-aware resolution tests ----

class TestMatchImportPath:
    """Verify _match_import_path filters candidates by import path."""

    def test_at_alias_path_match(self):
        """@/composables/kiniseis matches src/composables/kiniseis/types.ts."""
        candidates = [
            {"file_path": "src/composables/kiniseis/types.ts", "is_exported": True},
            {"file_path": "src/views/KiniseisView.vue", "is_exported": False},
        ]
        matched = _match_import_path("@/composables/kiniseis", candidates)
        assert len(matched) == 1
        assert matched[0]["file_path"] == "src/composables/kiniseis/types.ts"

    def test_at_alias_direct_file_match(self):
        """@/composables/kiniseis/types matches the exact file."""
        candidates = [
            {"file_path": "src/composables/kiniseis/types.ts", "is_exported": True},
            {"file_path": "src/composables/kiniseis/balance.ts", "is_exported": True},
        ]
        matched = _match_import_path("@/composables/kiniseis/types", candidates)
        assert len(matched) == 1
        assert matched[0]["file_path"] == "src/composables/kiniseis/types.ts"

    def test_relative_path_match(self):
        """./types matches a file in the same directory."""
        candidates = [
            {"file_path": "src/same-dir/types.ts", "is_exported": True},
            {"file_path": "src/other-dir/types.ts", "is_exported": False},
        ]
        matched = _match_import_path("./types", candidates)
        # Both match because relative stripping just does suffix match
        assert any(c["file_path"] == "src/same-dir/types.ts" for c in matched)

    def test_no_match_returns_empty(self):
        """Unrelated path returns empty list."""
        candidates = [
            {"file_path": "src/utils/helpers.ts", "is_exported": True},
        ]
        matched = _match_import_path("@/composables/kiniseis", candidates)
        assert len(matched) == 0

    def test_empty_import_path_returns_empty(self):
        """Empty import path returns empty list."""
        matched = _match_import_path("", [{"file_path": "src/foo.ts"}])
        assert len(matched) == 0

    def test_backslash_normalization(self):
        """Windows backslash paths are normalized."""
        candidates = [
            {"file_path": "src\\composables\\kiniseis\\types.ts", "is_exported": True},
        ]
        matched = _match_import_path("@/composables/kiniseis", candidates)
        assert len(matched) == 1


class TestImportAwareResolution:
    """Integration tests for import-aware symbol resolution."""

    def test_import_prefers_correct_definition(self, tmp_path):
        """Two files define same function, consumer imports from one — edge points to imported definition."""
        root = tmp_path / "import_resolve"
        root.mkdir()
        src = root / "src"
        src.mkdir()
        composables = src / "composables"
        composables.mkdir()

        # Definition A: the "correct" one (exported from composables)
        (composables / "helpers.py").write_text(
            "def formatValue(x):\n"
            "    '''Format a value.'''\n"
            "    return str(x)\n"
        )

        # Definition B: a different file also defines formatValue
        (src / "utils.py").write_text(
            "def formatValue(x):\n"
            "    '''Different formatValue.'''\n"
            "    return repr(x)\n"
        )

        # Consumer: imports from composables
        (src / "consumer.py").write_text(
            "from composables.helpers import formatValue\n\n"
            "def process():\n"
            "    formatValue(42)\n"
        )

        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        # The edge from process→formatValue should point to composables/helpers.py
        out, rc = roam("symbol", "process", cwd=root)
        assert rc == 0

    def test_python_from_import_resolution(self, tmp_path):
        """Python 'from X import Y' should resolve to correct file."""
        root = tmp_path / "py_import"
        root.mkdir()

        (root / "module_a.py").write_text(
            "def sharedFunc():\n"
            "    return 'A'\n"
        )
        (root / "module_b.py").write_text(
            "def sharedFunc():\n"
            "    return 'B'\n"
        )
        (root / "main.py").write_text(
            "from module_a import sharedFunc\n\n"
            "def run():\n"
            "    sharedFunc()\n"
        )

        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        # Verify run() resolves — should not crash or pick wrong file
        out, rc = roam("symbol", "run", cwd=root)
        assert rc == 0
        assert "run" in out


# ---- Bug 1 (v4.3.1): Multi-line template attribute tests ----

class TestMultilineTemplateAttributes:
    """Verify scan_template_references handles multi-line attribute values."""

    def test_multiline_class_binding(self):
        """:class="cn(\n  isRowFocused(row)\n)" → isRowFocused detected."""
        template = (
            '<div\n'
            '  :class="cn(\n'
            '    isRowFocused(row) && \'font-semibold\'\n'
            '  )"\n'
            '>\n'
            '</div>'
        )
        known = {"cn", "isRowFocused"}
        refs = scan_template_references(template, 1, known, "Test.vue")
        names = {r["target_name"] for r in refs}
        assert "isRowFocused" in names

    def test_multiline_vif(self):
        """v-if="condition &&\n  otherCondition" → both detected."""
        template = (
            '<div\n'
            '  v-if="condition &&\n'
            '    otherCondition"\n'
            '>\n'
            '</div>'
        )
        known = {"condition", "otherCondition"}
        refs = scan_template_references(template, 1, known, "Test.vue")
        names = {r["target_name"] for r in refs}
        assert "condition" in names
        assert "otherCondition" in names

    def test_single_line_still_works(self):
        """Existing single-line bindings still detected after the change."""
        template = '<button @click="handleClick">Go</button>'
        known = {"handleClick"}
        refs = scan_template_references(template, 1, known, "Test.vue")
        names = {r["target_name"] for r in refs}
        assert "handleClick" in names

    def test_multiline_line_number_correct(self):
        """Line number computed from match position in full content."""
        template = (
            '<div>line1</div>\n'       # line 10 (start_line=10), offset 0
            '<span\n'                   # line 11, offset 1
            '  :class="myFunc(x)"\n'   # line 12, offset 2 — :class starts here
            '></span>\n'
        )
        known = {"myFunc"}
        refs = scan_template_references(template, 10, known, "Test.vue")
        assert len(refs) == 1
        # The :class binding starts at line 12 (offset 2 from start_line 10)
        assert refs[0]["line"] == 12

    def test_vue_multiline_binding_integration(self, tmp_path):
        """Integration: Vue file with multi-line :class, verify fan-in > 0."""
        root = tmp_path / "multiline_vue"
        root.mkdir()
        (root / "App.vue").write_text(
            '<template>\n'
            '  <div\n'
            '    :class="cn(\n'
            '      isActive(item) && \'font-bold\',\n'
            '      \'p-2\'\n'
            '    )"\n'
            '  >\n'
            '    {{ label }}\n'
            '  </div>\n'
            '</template>\n'
            '<script setup lang="ts">\n'
            'function cn(...args: any[]) { return args.filter(Boolean).join(" ") }\n'
            'function isActive(item: any) { return item.active }\n'
            'const label = "hello"\n'
            '</script>\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        out, rc = roam("symbol", "isActive", cwd=root)
        assert rc == 0
        assert "isActive" in out


# ---- Bug 2 (v4.3.1): Identifier in arguments tests ----

class TestIdentifierInArguments:
    """Verify identifiers passed as function arguments are extracted as references."""

    def test_callback_identifier_extracted(self, tmp_path):
        """addEventListener('click', handler) → handler is a reference."""
        root = tmp_path / "callback_ref"
        root.mkdir()
        (root / "app.js").write_text(
            'function handler() {\n'
            '  console.log("handled")\n'
            '}\n'
            '\n'
            'function setup() {\n'
            '  document.addEventListener("click", handler)\n'
            '}\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        out, rc = roam("symbol", "handler", cwd=root)
        assert rc == 0
        # handler should have fan-in > 0 (called from setup via addEventListener)
        assert "handler" in out

    def test_nested_callback_not_duplicated(self, tmp_path):
        """setTimeout(doWork, 100) → doWork extracted once, not duplicated."""
        root = tmp_path / "nested_cb"
        root.mkdir()
        (root / "app.js").write_text(
            'function doWork() {\n'
            '  return 42\n'
            '}\n'
            '\n'
            'function init() {\n'
            '  setTimeout(doWork, 100)\n'
            '}\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        out, rc = roam("symbol", "doWork", cwd=root)
        assert rc == 0
        assert "doWork" in out


# ---- Bug 3 (v4.3.1): Shorthand property identifier tests ----

class TestShorthandPropertyIdentifier:
    """Verify shorthand properties in objects are extracted as references."""

    def test_shorthand_property_extracted(self, tmp_path):
        """defineExpose({ fn1, fn2 }) → both are references."""
        root = tmp_path / "shorthand_prop"
        root.mkdir()
        (root / "Component.vue").write_text(
            '<template>\n'
            '  <div>Test</div>\n'
            '</template>\n'
            '<script setup lang="ts">\n'
            'function fn1() { return 1 }\n'
            'function fn2() { return 2 }\n'
            'defineExpose({ fn1, fn2 })\n'
            '</script>\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        # fn1 and fn2 should have references from defineExpose
        out1, rc1 = roam("symbol", "fn1", cwd=root)
        assert rc1 == 0
        out2, rc2 = roam("symbol", "fn2", cwd=root)
        assert rc2 == 0

    def test_shorthand_vs_pair(self, tmp_path):
        """{ fn1, key: fn2() } → fn1 from shorthand, fn2 from call."""
        root = tmp_path / "shorthand_vs_pair"
        root.mkdir()
        (root / "app.js").write_text(
            'function fn1() { return 1 }\n'
            'function fn2() { return 2 }\n'
            '\n'
            'function setup() {\n'
            '  const obj = { fn1, key: fn2() }\n'
            '  return obj\n'
            '}\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        out, rc = roam("symbol", "fn1", cwd=root)
        assert rc == 0


# ---- Bug 4 (v4.3.1): _closest_symbol with line_end tests ----

class TestClosestSymbolLineEnd:
    """Verify _closest_symbol uses line_end for containment checks."""

    def _make_file_symbols(self, symbols_data):
        """Build file_symbols dict from list of (name, line_start, line_end) tuples."""
        syms = [{"name": n, "line_start": ls, "line_end": le, "id": i}
                for i, (n, ls, le) in enumerate(symbols_data)]
        return {"test.vue": syms}

    def test_ref_after_function_end_not_self(self):
        """Ref at L100, function ends at L80 → should NOT return that function."""
        file_symbols = self._make_file_symbols([
            ("funcA", 10, 30),
            ("funcB", 50, 80),
        ])
        # Ref at line 100 is after funcB ends — module scope
        result = _closest_symbol("test.vue", 100, file_symbols)
        assert result is not None
        # Should return syms[0] (funcA) as file-level fallback, NOT funcB
        assert result["name"] == "funcA"

    def test_ref_inside_function_body(self):
        """Ref at L25, function L20-50 → returns that function."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 15),
            ("funcB", 20, 50),
            ("funcC", 60, 90),
        ])
        result = _closest_symbol("test.vue", 25, file_symbols)
        assert result is not None
        assert result["name"] == "funcB"

    def test_module_scope_ref_returns_first(self):
        """Ref at module scope (between functions) → returns first symbol."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 15),
            ("funcB", 30, 50),
        ])
        # Line 20 is between funcA (ends 15) and funcB (starts 30) — module scope
        result = _closest_symbol("test.vue", 20, file_symbols)
        assert result is not None
        assert result["name"] == "funcA"

    def test_nested_functions_picks_innermost(self):
        """Nested functions: picks the innermost (last matching) containing symbol."""
        file_symbols = self._make_file_symbols([
            ("outer", 1, 100),
            ("inner", 20, 80),
        ])
        result = _closest_symbol("test.vue", 50, file_symbols)
        assert result is not None
        # Both contain line 50, but inner is last → most nested wins
        assert result["name"] == "inner"

    def test_backward_compat_no_line_end(self):
        """Symbols without line_end (le=0) fall through to first-symbol fallback."""
        file_symbols = self._make_file_symbols([
            ("funcA", 5, 0),
            ("funcB", 20, 0),
        ])
        # No symbol has valid line_end, so no containment match → returns syms[0]
        result = _closest_symbol("test.vue", 25, file_symbols)
        assert result is not None
        assert result["name"] == "funcA"


# ---- Integration: callback argument creates edge ----

class TestCallbackArgumentEdge:
    """Integration test: callback passed by reference creates a graph edge."""

    def test_event_listener_callback_has_edge(self, tmp_path):
        """onMounted with addEventListener('x', handler) → handler has fan-in > 0."""
        root = tmp_path / "callback_edge"
        root.mkdir()
        (root / "Component.vue").write_text(
            '<template>\n'
            '  <div>Test</div>\n'
            '</template>\n'
            '<script setup lang="ts">\n'
            'import { onMounted, onUnmounted } from "vue"\n'
            '\n'
            'function handleKeyboard(e: KeyboardEvent) {\n'
            '  console.log(e.key)\n'
            '}\n'
            '\n'
            'onMounted(() => {\n'
            '  document.addEventListener("keydown", handleKeyboard)\n'
            '})\n'
            'onUnmounted(() => {\n'
            '  document.removeEventListener("keydown", handleKeyboard)\n'
            '})\n'
            '</script>\n'
        )
        git_init(root)
        out, rc = roam("index", cwd=root)
        assert rc == 0, f"Index failed: {out}"

        out, rc = roam("symbol", "handleKeyboard", cwd=root)
        assert rc == 0
        assert "handleKeyboard" in out
