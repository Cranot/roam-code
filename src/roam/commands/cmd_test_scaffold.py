"""Generate test file skeletons from indexed symbol data."""

from __future__ import annotations

import os
import posixpath

import click

from roam.commands.changed_files import is_test_file
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db
from roam.index.test_conventions import find_test_candidates
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json
from roam.refactor.codegen import _python_module_path

# ---------------------------------------------------------------------------
# Language-specific scaffold generators
# ---------------------------------------------------------------------------

# Supported test frameworks per language.  The first entry is the default.
_FRAMEWORKS = {
    "python": ["pytest", "unittest"],
    "javascript": ["jest", "mocha", "vitest"],
    "typescript": ["jest", "mocha", "vitest"],
    "go": ["testing"],
    "java": ["junit5", "junit4"],
    "ruby": ["rspec", "minitest"],
}


def _default_framework(language: str) -> str:
    """Return the default test framework for *language*."""
    fws = _FRAMEWORKS.get(language)
    return fws[0] if fws else "generic"


def _to_test_function_name(name: str, kind: str, language: str) -> str:
    """Derive a test function/method name from a source symbol name."""
    if language == "go":
        # Go: TestFuncName
        return f"Test{name[0].upper()}{name[1:]}" if name else "TestUnnamed"
    if language in ("javascript", "typescript"):
        return name  # used inside describe/it blocks as description text
    if language == "java":
        # JUnit: testMethodName or test_MethodName
        return f"test{name[0].upper()}{name[1:]}" if name else "testUnnamed"
    if language == "ruby":
        return name  # used inside describe/it blocks as description text
    # Python default
    return f"test_{name}"


def _normalise_path(p: str) -> str:
    """Normalise backslashes to forward slashes."""
    return p.replace("\\", "/")


# ---------------------------------------------------------------------------
# Python scaffold
# ---------------------------------------------------------------------------


def _scaffold_python(symbols, source_path, framework="pytest"):
    """Generate a Python test skeleton.

    Parameters
    ----------
    symbols:
        List of symbol dicts (name, kind, signature, qualified_name).
    source_path:
        Path to the source file being tested.
    framework:
        'pytest' (default) or 'unittest'.

    Returns (lines, test_path) where lines is a list of strings.
    """
    candidates = find_test_candidates(source_path, language="python")
    test_path = candidates[0] if candidates else f"tests/test_{os.path.splitext(os.path.basename(source_path))[0]}.py"

    module_path = _python_module_path(source_path)

    lines = []

    # Collect importable names
    importable = [s["name"] for s in symbols if s["kind"] in ("function", "class")]
    methods = [s for s in symbols if s["kind"] == "method"]

    if framework == "unittest":
        lines.append("import unittest")
        lines.append("")
        if importable:
            lines.append(f"from {module_path} import {', '.join(importable)}")
            lines.append("")
        lines.append("")

        # Group methods by parent class
        class_methods = _group_methods_by_parent(symbols)

        for sym in symbols:
            if sym["kind"] == "class":
                class_name = sym["name"]
                lines.append(f"class Test{class_name}(unittest.TestCase):")
                child_methods = class_methods.get(sym["id"], [])
                if child_methods:
                    for m in child_methods:
                        lines.append(f"    def test_{m['name']}(self):")
                        lines.append(f"        # TODO: test {class_name}.{m['name']}")
                        lines.append("        pass")
                        lines.append("")
                else:
                    lines.append(f"    def test_{class_name}_init(self):")
                    lines.append(f"        # TODO: test {class_name} initialization")
                    lines.append("        pass")
                    lines.append("")

            elif sym["kind"] == "function":
                # Top-level functions go in a general test class
                pass

        # Top-level functions
        top_level_fns = [s for s in symbols if s["kind"] == "function"]
        if top_level_fns:
            lines.append("class TestFunctions(unittest.TestCase):")
            for sym in top_level_fns:
                lines.append(f"    def test_{sym['name']}(self):")
                lines.append(f"        # TODO: test {sym['name']}")
                lines.append("        pass")
                lines.append("")

        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append("    unittest.main()")
        lines.append("")

    else:
        # pytest (default)
        if importable:
            lines.append(f"from {module_path} import {', '.join(importable)}")
            lines.append("")
        lines.append("")

        class_methods = _group_methods_by_parent(symbols)

        for sym in symbols:
            if sym["kind"] == "class":
                class_name = sym["name"]
                lines.append(f"class Test{class_name}:")
                child_methods = class_methods.get(sym["id"], [])
                if child_methods:
                    for m in child_methods:
                        lines.append(f"    def test_{m['name']}(self):")
                        lines.append(f"        # TODO: test {class_name}.{m['name']}")
                        lines.append("        pass")
                        lines.append("")
                else:
                    lines.append(f"    def test_{class_name}_init(self):")
                    lines.append(f"        # TODO: test {class_name} initialization")
                    lines.append("        pass")
                    lines.append("")

            elif sym["kind"] == "function":
                lines.append(f"def test_{sym['name']}():")
                sig = sym.get("signature") or ""
                if sig:
                    lines.append(f"    # signature: {sig}")
                lines.append(f"    # TODO: test {sym['name']}")
                lines.append("    pass")
                lines.append("")

    return lines, test_path


# ---------------------------------------------------------------------------
# JavaScript / TypeScript scaffold
# ---------------------------------------------------------------------------


def _scaffold_js(symbols, source_path, framework="jest"):
    """Generate a JS/TS test skeleton using describe/it blocks."""
    candidates = find_test_candidates(source_path, language="javascript")
    test_path = candidates[0] if candidates else source_path.replace(".js", ".test.js").replace(".ts", ".test.ts")

    # Compute relative import path
    from roam.refactor.codegen import compute_relative_path

    rel_path = compute_relative_path(test_path, source_path)

    lines = []

    importable = [s["name"] for s in symbols if s["kind"] in ("function", "class")]
    if importable:
        lines.append(f"import {{ {', '.join(importable)} }} from '{rel_path}';")
        lines.append("")

    # Module-level describe block
    module_name = os.path.splitext(os.path.basename(source_path))[0]
    lines.append(f"describe('{module_name}', () => {{")

    class_methods = _group_methods_by_parent(symbols)

    for sym in symbols:
        if sym["kind"] == "class":
            class_name = sym["name"]
            lines.append(f"  describe('{class_name}', () => {{")
            child_methods = class_methods.get(sym["id"], [])
            if child_methods:
                for m in child_methods:
                    mname = m["name"]
                    lines.append(f"    it('should {mname}', () => {{")
                    lines.append(f"      // TODO: test {class_name}.{mname}")
                    lines.append("    });")
                    lines.append("")
            else:
                lines.append("    it('should create instance', () => {")
                lines.append(f"      // TODO: test {class_name} constructor")
                lines.append("    });")
                lines.append("")
            lines.append("  });")
            lines.append("")

        elif sym["kind"] == "function":
            lines.append(f"  describe('{sym['name']}', () => {{")
            lines.append("    it('should work correctly', () => {")
            lines.append(f"      // TODO: test {sym['name']}")
            lines.append("    });")
            lines.append("  });")
            lines.append("")

    lines.append("});")
    lines.append("")

    return lines, test_path


# ---------------------------------------------------------------------------
# Go scaffold
# ---------------------------------------------------------------------------


def _scaffold_go(symbols, source_path, framework="testing"):
    """Generate a Go test skeleton."""
    candidates = find_test_candidates(source_path, language="go")
    test_path = candidates[0] if candidates else source_path.replace(".go", "_test.go")

    lines = []

    # Detect package name from source path
    pkg_dir = posixpath.dirname(_normalise_path(source_path))
    pkg_name = posixpath.basename(pkg_dir) if pkg_dir else "main"

    lines.append(f"package {pkg_name}")
    lines.append("")
    lines.append('import "testing"')
    lines.append("")

    for sym in symbols:
        if sym["kind"] in ("function", "method"):
            name = sym["name"]
            test_name = f"Test{name[0].upper()}{name[1:]}" if name else "TestUnnamed"
            lines.append(f"func {test_name}(t *testing.T) {{")
            sig = sym.get("signature") or ""
            if sig:
                lines.append(f"\t// signature: {sig}")
            lines.append(f"\t// TODO: test {name}")
            lines.append("\tt.Skip(\"not implemented\")")
            lines.append("}")
            lines.append("")

    return lines, test_path


# ---------------------------------------------------------------------------
# Java scaffold
# ---------------------------------------------------------------------------


def _scaffold_java(symbols, source_path, framework="junit5"):
    """Generate a Java JUnit test skeleton."""
    candidates = find_test_candidates(source_path, language="java")
    test_path = candidates[0] if candidates else source_path.replace(".java", "Test.java")

    base_name = os.path.splitext(os.path.basename(source_path))[0]

    lines = []

    if framework == "junit5":
        lines.append("import org.junit.jupiter.api.Test;")
        lines.append("import static org.junit.jupiter.api.Assertions.*;")
    else:
        # junit4
        lines.append("import org.junit.Test;")
        lines.append("import static org.junit.Assert.*;")

    lines.append("")
    lines.append(f"class {base_name}Test {{")
    lines.append("")

    class_methods = _group_methods_by_parent(symbols)

    for sym in symbols:
        if sym["kind"] in ("function", "method"):
            name = sym["name"]
            test_name = f"test{name[0].upper()}{name[1:]}" if name else "testUnnamed"
            lines.append("    @Test")
            lines.append(f"    void {test_name}() {{")
            lines.append(f"        // TODO: test {name}")
            lines.append("    }")
            lines.append("")

        elif sym["kind"] == "class" and sym["name"] != base_name:
            # Inner class — add a constructor test
            lines.append("    @Test")
            lines.append(f"    void test{sym['name']}Creation() {{")
            lines.append(f"        // TODO: test {sym['name']} creation")
            lines.append("    }")
            lines.append("")

    lines.append("}")
    lines.append("")

    return lines, test_path


# ---------------------------------------------------------------------------
# Ruby scaffold
# ---------------------------------------------------------------------------


def _scaffold_ruby(symbols, source_path, framework="rspec"):
    """Generate a Ruby test skeleton."""
    candidates = find_test_candidates(source_path, language="ruby")
    test_path = candidates[0] if candidates else source_path.replace(".rb", "_spec.rb")

    lines = []

    module_name = os.path.splitext(os.path.basename(source_path))[0]

    if framework == "rspec":
        # Compute relative require path
        rel = _normalise_path(source_path)
        if rel.startswith("lib/"):
            rel = rel[4:]
        rel = os.path.splitext(rel)[0]
        lines.append(f"require '{rel}'")
        lines.append("")

        class_methods = _group_methods_by_parent(symbols)

        for sym in symbols:
            if sym["kind"] == "class":
                class_name = sym["name"]
                lines.append(f"RSpec.describe {class_name} do")
                child_methods = class_methods.get(sym["id"], [])
                if child_methods:
                    for m in child_methods:
                        mname = m["name"]
                        lines.append(f"  describe '#{mname}' do")
                        lines.append(f"    it '{mname} works correctly' do")
                        lines.append(f"      # TODO: test {class_name}#{mname}")
                        lines.append("    end")
                        lines.append("  end")
                        lines.append("")
                else:
                    lines.append("  it 'creates an instance' do")
                    lines.append(f"    # TODO: test {class_name} instantiation")
                    lines.append("  end")
                    lines.append("")
                lines.append("end")
                lines.append("")

            elif sym["kind"] == "function":
                lines.append(f"RSpec.describe '#{sym['name']}' do")
                lines.append("  it 'works correctly' do")
                lines.append(f"    # TODO: test {sym['name']}")
                lines.append("  end")
                lines.append("end")
                lines.append("")

    else:
        # minitest
        lines.append("require 'minitest/autorun'")
        rel = _normalise_path(source_path)
        if rel.startswith("lib/"):
            rel = rel[4:]
        rel = os.path.splitext(rel)[0]
        lines.append(f"require '{rel}'")
        lines.append("")

        class_name = module_name.replace("_", " ").title().replace(" ", "")
        lines.append(f"class Test{class_name} < Minitest::Test")

        for sym in symbols:
            if sym["kind"] in ("function", "method"):
                lines.append(f"  def test_{sym['name']}")
                lines.append(f"    # TODO: test {sym['name']}")
                lines.append("    skip 'not implemented'")
                lines.append("  end")
                lines.append("")

        lines.append("end")
        lines.append("")

    return lines, test_path


# ---------------------------------------------------------------------------
# Generic / fallback scaffold
# ---------------------------------------------------------------------------


def _scaffold_generic(symbols, source_path, language="unknown", framework="generic"):
    """Generate a generic comment-based test skeleton."""
    ext = os.path.splitext(source_path)[1]
    base_name = os.path.splitext(os.path.basename(source_path))[0]
    test_path = f"tests/test_{base_name}{ext}"

    lines = [
        f"# Test scaffold for {source_path}",
        f"# Language: {language}",
        "# Generated by: roam test-scaffold",
        "",
    ]

    for sym in symbols:
        lines.append(f"# TODO: test {sym['kind']} {sym['name']}")
        sig = sym.get("signature") or ""
        if sig:
            lines.append(f"#   signature: {sig}")
        lines.append("")

    return lines, test_path


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_SCAFFOLD_DISPATCH = {
    "python": _scaffold_python,
    "javascript": _scaffold_js,
    "typescript": _scaffold_js,
    "go": _scaffold_go,
    "java": _scaffold_java,
    "ruby": _scaffold_ruby,
}


def _generate_scaffold(symbols, source_path, language, framework=None):
    """Dispatch to the correct language scaffold generator.

    Returns (lines, test_path).
    """
    fw = framework or _default_framework(language)
    generator = _SCAFFOLD_DISPATCH.get(language)
    if generator:
        return generator(symbols, source_path, framework=fw)
    return _scaffold_generic(symbols, source_path, language=language, framework=fw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _group_methods_by_parent(symbols):
    """Group method symbols by their parent_id.

    Returns dict {parent_id: [method_symbol, ...]}.
    """
    result = {}
    for s in symbols:
        if s["kind"] == "method" and s.get("parent_id"):
            result.setdefault(s["parent_id"], []).append(s)
    return result


def _collect_symbols_for_file(conn, file_id):
    """Fetch testable symbols (function, class, method) for a file.

    Returns a list of dicts with keys: id, name, kind, signature,
    qualified_name, parent_id, line_start, line_end, visibility.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.signature, s.qualified_name, "
        "s.parent_id, s.line_start, s.line_end, s.visibility "
        "FROM symbols s "
        "WHERE s.file_id = ? AND s.kind IN ('function', 'class', 'method') "
        "ORDER BY s.line_start",
        (file_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _collect_symbols_for_symbol(conn, sym):
    """Collect the target symbol and its children (methods) for scaffolding.

    If *sym* is a class, also fetches its method children.
    If *sym* is a function/method, returns just that symbol.

    Returns a list of dicts.
    """
    result = [dict(sym)]
    if sym["kind"] == "class":
        children = conn.execute(
            "SELECT s.id, s.name, s.kind, s.signature, s.qualified_name, "
            "s.parent_id, s.line_start, s.line_end, s.visibility "
            "FROM symbols s "
            "WHERE s.parent_id = ? AND s.kind = 'method' "
            "ORDER BY s.line_start",
            (sym["id"],),
        ).fetchall()
        result.extend(dict(r) for r in children)
    return result


def _find_already_tested(conn, symbols, test_file_path):
    """Check which symbols already have tests in an existing test file.

    Uses the test-map approach: looks for edges from symbols in the test file
    to the target symbols, and also checks for test function names matching
    the naming convention (test_<name>).

    Returns a set of symbol names that already have test coverage.
    """
    tested = set()

    # Check if test file exists in the DB
    test_file = conn.execute(
        "SELECT id FROM files WHERE path = ?", (test_file_path,)
    ).fetchone()
    if not test_file:
        # Also try suffix match
        test_file = conn.execute(
            "SELECT id FROM files WHERE path LIKE ?", (f"%{test_file_path}",)
        ).fetchone()
    if not test_file:
        return tested

    test_file_id = test_file["id"]

    # Get all test symbols in the test file
    test_syms = conn.execute(
        "SELECT name FROM symbols WHERE file_id = ? AND kind IN ('function', 'method')",
        (test_file_id,),
    ).fetchall()
    test_names = {r["name"].lower() for r in test_syms}

    for sym in symbols:
        name = sym["name"]
        # Check naming convention matches
        if f"test_{name}".lower() in test_names:
            tested.add(name)
        elif f"test_{name}_".lower() in {tn.rsplit("_", 1)[0] if "_" in tn else tn for tn in test_names}:
            # Partial match: test_foo_returns_bar matches foo
            pass

    # Also check edge-based coverage: symbols in test file referencing target symbols
    sym_ids = [s["id"] for s in symbols]
    if sym_ids:
        ph = ",".join("?" for _ in sym_ids)
        edges = conn.execute(
            f"SELECT DISTINCT e.target_id FROM edges e "
            f"JOIN symbols s ON e.source_id = s.id "
            f"WHERE s.file_id = ? AND e.target_id IN ({ph})",
            [test_file_id] + sym_ids,
        ).fetchall()
        edge_target_ids = {r["target_id"] for r in edges}
        for sym in symbols:
            if sym["id"] in edge_target_ids:
                tested.add(sym["name"])

    return tested


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("test-scaffold")
@click.argument("name")
@click.option("--write", is_flag=True, help="Write the scaffold to disk (default: dry-run)")
@click.option(
    "--framework",
    default=None,
    help="Override test framework (e.g. pytest, unittest, jest, mocha, junit5, rspec, minitest)",
)
@click.pass_context
def test_scaffold(ctx, name, write, framework):
    """Generate test file skeletons from indexed symbols.

    Accepts a symbol name or file path.  Looks up testable symbols
    (functions, classes, methods) and generates a test file skeleton
    with proper imports and stubs.  If the test file already exists,
    only adds stubs for untested symbols.

    By default shows the scaffold (dry-run).  Use --write to create
    the file on disk.

    \b
    Examples:
      roam test-scaffold src/roam/graph/builder.py
      roam test-scaffold MyClass
      roam test-scaffold src/utils.py --framework unittest
      roam test-scaffold src/utils.py --write
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    name_norm = _normalise_path(name)

    with open_db(readonly=True) as conn:
        # Determine if this is a file path or a symbol name
        file_row = None
        target_symbols = []
        source_path = None
        language = None

        # Try file lookup first (if has slash or common extension)
        if "/" in name_norm or "." in name_norm:
            file_row = conn.execute(
                "SELECT * FROM files WHERE path = ?", (name_norm,)
            ).fetchone()
            if not file_row:
                file_row = conn.execute(
                    "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
                    (f"%{name_norm}",),
                ).fetchone()

        if file_row:
            source_path = file_row["path"]
            language = file_row["language"] or "unknown"
            file_id = file_row["id"]

            if is_test_file(source_path):
                msg = f"Skipping: {source_path} is already a test file"
                if json_mode:
                    click.echo(to_json(json_envelope(
                        "test-scaffold",
                        summary={"verdict": msg, "scaffolded": 0},
                    )))
                else:
                    click.echo(f"VERDICT: {msg}")
                return

            target_symbols = _collect_symbols_for_file(conn, file_id)
        else:
            # Symbol lookup
            sym = find_symbol(conn, name)
            if not sym:
                click.echo(symbol_not_found(conn, name, json_mode=json_mode))
                raise SystemExit(1)

            source_path = sym["file_path"]
            # Look up language from the file
            frow = conn.execute(
                "SELECT language FROM files WHERE path = ?", (source_path,)
            ).fetchone()
            language = (frow["language"] if frow else None) or "unknown"

            target_symbols = _collect_symbols_for_symbol(conn, sym)

        if not target_symbols:
            msg = f"No testable symbols found in {source_path or name}"
            if json_mode:
                click.echo(to_json(json_envelope(
                    "test-scaffold",
                    summary={"verdict": msg, "scaffolded": 0},
                    symbols=[],
                )))
            else:
                click.echo(f"VERDICT: {msg}")
            return

        # Validate framework choice
        if framework:
            valid_frameworks = _FRAMEWORKS.get(language, [])
            # Allow any framework, but warn if not in the known list
            if valid_frameworks and framework not in valid_frameworks and framework != "generic":
                click.echo(
                    f"Warning: '{framework}' is not a standard framework for {language}. "
                    f"Known: {', '.join(valid_frameworks)}"
                )

        # Generate scaffold
        lines, test_path = _generate_scaffold(target_symbols, source_path, language, framework=framework)

        # Check for existing test file and filter already-tested symbols
        already_tested = _find_already_tested(conn, target_symbols, test_path)
        test_file_exists = os.path.exists(test_path)

        if already_tested:
            # Filter out already-tested symbols and regenerate
            untested = [s for s in target_symbols if s["name"] not in already_tested]
            if not untested:
                msg = f"All {len(target_symbols)} symbols already have tests in {test_path}"
                if json_mode:
                    click.echo(to_json(json_envelope(
                        "test-scaffold",
                        summary={
                            "verdict": msg,
                            "scaffolded": 0,
                            "already_tested": len(already_tested),
                        },
                        test_path=test_path,
                        already_tested=sorted(already_tested),
                    )))
                else:
                    click.echo(f"VERDICT: {msg}")
                    click.echo(f"Test file: {test_path}")
                    click.echo(f"Already tested: {', '.join(sorted(already_tested))}")
                return

            # Regenerate with only untested symbols
            lines, test_path = _generate_scaffold(untested, source_path, language, framework=framework)

        scaffold_text = "\n".join(lines)
        total_symbols = len(target_symbols)
        skipped = len(already_tested)
        scaffolded = total_symbols - skipped
        testable_kinds = {}
        for s in target_symbols:
            if s["name"] not in already_tested:
                testable_kinds[s["kind"]] = testable_kinds.get(s["kind"], 0) + 1

        verdict = (
            f"Scaffolded {scaffolded} symbol(s) for {source_path} -> {test_path}"
            if scaffolded > 0
            else f"No new symbols to scaffold for {source_path}"
        )

        # --- JSON output ---
        if json_mode:
            symbols_data = [
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "line": s.get("line_start"),
                    "signature": s.get("signature"),
                    "status": "skipped" if s["name"] in already_tested else "scaffolded",
                }
                for s in target_symbols
            ]

            click.echo(to_json(json_envelope(
                "test-scaffold",
                summary={
                    "verdict": verdict,
                    "scaffolded": scaffolded,
                    "skipped": skipped,
                    "total_symbols": total_symbols,
                    "language": language,
                    "framework": framework or _default_framework(language),
                    "test_file_exists": test_file_exists,
                    "written": write,
                },
                source_path=source_path,
                test_path=test_path,
                symbols=symbols_data,
                kinds=testable_kinds,
                scaffold=scaffold_text,
            )))
            if write and scaffolded > 0:
                _write_scaffold(test_path, scaffold_text, test_file_exists)
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"Source:  {source_path} ({language})")
        click.echo(f"Test:    {test_path}")
        fw_name = framework or _default_framework(language)
        click.echo(f"Framework: {fw_name}")
        click.echo()

        if skipped:
            click.echo(f"Already tested ({skipped}): {', '.join(sorted(already_tested))}")
            click.echo()

        if scaffolded > 0:
            click.echo(f"Symbols to scaffold ({scaffolded}):")
            for s in target_symbols:
                if s["name"] not in already_tested:
                    click.echo(f"  {abbrev_kind(s['kind'])} {s['name']}  {loc(source_path, s.get('line_start'))}")
            click.echo()

            if test_file_exists:
                click.echo(f"NOTE: {test_path} exists. New stubs will be appended.")
                click.echo()

            click.echo("--- scaffold ---")
            click.echo(scaffold_text)
            click.echo("--- end scaffold ---")

            if write:
                _write_scaffold(test_path, scaffold_text, test_file_exists)
                click.echo(f"\nWritten to {test_path}")
            else:
                click.echo(f"\nDry-run mode. Use --write to create {test_path}")


def _write_scaffold(test_path, scaffold_text, exists):
    """Write scaffold text to disk."""
    # Ensure parent directory exists
    parent = os.path.dirname(test_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if exists:
        # Append to existing file
        with open(test_path, "a", encoding="utf-8") as f:
            f.write("\n\n")
            f.write("# --- roam test-scaffold: new stubs below ---\n\n")
            f.write(scaffold_text)
    else:
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(scaffold_text)
