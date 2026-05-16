"""W168 — regression guard for unguarded optional-dependency imports.

W160 added three false-positive filters to ``orphan-imports``; the residue
was two REAL unguarded ``import yaml`` / ``import lightgbm`` sites that
would raise ``ModuleNotFoundError`` on a bare ``pip install roam-code``
in environments without those packages.

This module:

1. Verifies the two fixes (``LanguageConfig.load`` and
   ``train_from_bench``) raise ``ImportError`` with an install hint when
   the optional dep is absent, and work normally when present.
2. Provides the load-bearing regression guard
   (``test_no_remaining_unguarded_optional_imports_in_src``) that scans
   every ``src/roam/`` module for module-level ``import X`` / ``from X
   import …`` where ``X`` is a known-optional dependency, and fails with
   the file:line of the first offender if any new unguarded site appears.

The scan is intentionally CONSERVATIVE: it only flags module-level
top-level imports of known-optional packages. Function-level imports
inside the body of a callable are exempt (they're the unsafe-but-common
pattern the W168 fix targets — fine for now, may be tightened later).
The scan flags an import as ``guarded`` when:

* it sits inside a ``try:`` block (with adjacent ``except ImportError``
  or ``except Exception`` handler), or
* the import statement has a trailing ``# unguarded-import: ok`` marker
  comment (escape hatch for genuine intentional cases).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fix 1 — LanguageConfig.load() install-hint path
# ---------------------------------------------------------------------------


def test_yaml_missing_raises_with_install_hint(tmp_path, monkeypatch):
    """When PyYAML is unavailable, ``LanguageConfig.load`` raises ``ImportError``
    with a literal install hint mentioning PyYAML and the roam-code extras."""
    from roam.languages import extractor_schema

    monkeypatch.setattr(extractor_schema, "yaml", None)
    monkeypatch.setattr(
        extractor_schema,
        "_YAML_IMPORT_ERROR",
        ImportError("No module named 'yaml'"),
    )

    fake_yaml = tmp_path / "fake.yaml"
    fake_yaml.write_text("language: x\nextensions: [.x]\n", encoding="utf-8")

    with pytest.raises(ImportError) as excinfo:
        extractor_schema.LanguageConfig.load(fake_yaml)

    msg = str(excinfo.value)
    assert "PyYAML" in msg, f"install hint must name PyYAML; got: {msg!r}"
    assert "pip install" in msg, f"install hint must give a pip command; got: {msg!r}"


def test_yaml_available_path_works(tmp_path):
    """Control test: when PyYAML is installed (true in CI), ``LanguageConfig.load``
    successfully parses a minimal extractor YAML and returns a populated
    ``LanguageConfig``. Skipped when PyYAML is genuinely unavailable."""
    pytest.importorskip("yaml")

    from roam.languages.extractor_schema import LanguageConfig

    yaml_path = tmp_path / "tiny.yaml"
    yaml_path.write_text(
        "language: tiny\nextensions: [.tiny]\nsymbols:\n  - query: '(x) @def'\n    kind: function\n",
        encoding="utf-8",
    )

    cfg = LanguageConfig.load(yaml_path)
    assert cfg.language == "tiny"
    assert cfg.extensions == [".tiny"]
    assert len(cfg.symbols) == 1


# ---------------------------------------------------------------------------
# Fix 2 — train_from_bench() install-hint path
# ---------------------------------------------------------------------------


def test_lightgbm_missing_raises_with_install_hint(tmp_path, monkeypatch):
    """When LightGBM is unavailable, ``train_from_bench`` raises ``ImportError``
    with a literal install hint mentioning LightGBM and the ``[learned]``
    extra."""
    from roam.retrieve import learned_ranker

    monkeypatch.setattr(learned_ranker, "lgb", None)
    monkeypatch.setattr(
        learned_ranker,
        "_LIGHTGBM_IMPORT_ERROR",
        ImportError("No module named 'lightgbm'"),
    )

    bench = tmp_path / "bench.jsonl"
    bench.write_text("", encoding="utf-8")
    model_out = tmp_path / "out.lgbm"

    with pytest.raises(ImportError) as excinfo:
        learned_ranker.train_from_bench(bench, model_out)

    msg = str(excinfo.value)
    assert "LightGBM" in msg, f"install hint must name LightGBM; got: {msg!r}"
    assert "[learned]" in msg, f"install hint must cite the [learned] extra; got: {msg!r}"
    assert "pip install" in msg, f"install hint must give a pip command; got: {msg!r}"


# ---------------------------------------------------------------------------
# Regression guard — scan src/roam/ for unguarded optional imports
# ---------------------------------------------------------------------------


# Optional dependencies that MUST be guarded if imported at module level.
# Keep this list in sync with the optional-dependencies blocks of
# pyproject.toml. Entries here are the IMPORTED module names, not the
# pypi package names (e.g. ``yaml`` not ``pyyaml``).
_OPTIONAL_MODULES: frozenset[str] = frozenset(
    {
        "yaml",  # PyYAML — [dev] extra
        "lightgbm",  # LightGBM — [learned] extra
        "fastmcp",  # FastMCP — [mcp] extra
        "numpy",  # NumPy — [semantic] extra
        "onnxruntime",  # ONNX Runtime — [semantic] extra
        "tokenizers",  # tokenizers — [semantic] extra
        "igraph",  # python-igraph — [leiden] extra
        "leidenalg",  # leidenalg — [leiden] extra
        "rustworkx",  # rustworkx — [graph-fast] extra
        "cyclonedx",  # cyclonedx-python-lib — [sbom] extra
        "scipy",  # scipy — [dev] extra (and graph fallbacks)
        "watchdog",  # watchdog — [mcp] extra (filesystem watcher)
        "mcp",  # Anthropic MCP types — [mcp] extra
    }
)

# Escape hatch: import statements ending in this trailing comment are
# treated as intentional and skipped by the scan.
_UNGUARDED_OK_MARKER = "unguarded-import: ok"

# Known pre-existing unguarded imports the W168 fix is NOT addressing.
# Each entry is ``(relative_posix_path, lineno, module_name, rationale)``.
# Add NEW entries here ONLY with a maintainer-visible rationale; the
# preferred fix is to wrap the import in try/except at the call site.
# These entries are surfaced unchanged so a future wave (W169+) can
# clear them, and so the scan reports the residue rather than silently
# allowing all numpy imports everywhere.
_KNOWN_PRE_EXISTING_OFFENDERS: frozenset[tuple[str, int, str]] = frozenset()


def _src_roam_root() -> Path:
    """Locate src/roam/ relative to this test file."""
    return Path(__file__).resolve().parent.parent / "src" / "roam"


def _collect_module_level_imports(tree: ast.AST) -> list[ast.stmt]:
    """Return the top-level (module-level) import statements in *tree*.

    Imports inside function/method/class bodies, inside try/except, or
    inside ``if`` blocks are NOT returned — only direct children of the
    module body.
    """
    if not isinstance(tree, ast.Module):
        return []
    return [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def _collect_unguarded_imports_anywhere(tree: ast.AST) -> list[ast.stmt]:
    """Return EVERY import statement in *tree* that is not inside a ``Try``.

    Catches both module-level and function-level unguarded imports — the
    W168 bugs (``import yaml`` / ``import lightgbm`` inside function
    bodies) sit at function-level scope and are exactly what this scan
    must detect.

    Walks the AST and tracks whether the current node is a descendant
    of an ``ast.Try`` body. Imports inside the ``handlers``/``orelse``/
    ``finalbody`` of a Try are not "guarded" by that Try — only imports
    inside the ``body`` are.
    """
    found: list[ast.stmt] = []

    def visit(node: ast.AST, *, in_try_body: bool) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not in_try_body:
            found.append(node)
            return
        if isinstance(node, ast.Try):
            for child in node.body:
                visit(child, in_try_body=True)
            for handler in node.handlers:
                for child in handler.body:
                    visit(child, in_try_body=in_try_body)
            for child in node.orelse:
                visit(child, in_try_body=in_try_body)
            for child in node.finalbody:
                visit(child, in_try_body=in_try_body)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, in_try_body=in_try_body)

    visit(tree, in_try_body=False)
    return found


def _references_optional_module(node: ast.stmt) -> str | None:
    """Return the optional-module name imported by *node*, or ``None``.

    Handles both ``import X`` / ``import X.Y`` / ``import X as Z`` and
    ``from X import Y`` / ``from X.Y import Z``. Compares the
    top-level package (first dotted segment) against
    ``_OPTIONAL_MODULES``.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".", 1)[0]
            if top in _OPTIONAL_MODULES:
                return top
    elif isinstance(node, ast.ImportFrom):
        # Relative imports (level > 0) have node.module == None and are
        # always intra-package — never optional.
        if node.level == 0 and node.module:
            top = node.module.split(".", 1)[0]
            if top in _OPTIONAL_MODULES:
                return top
    return None


def _line_has_ok_marker(source_lines: list[str], lineno: int) -> bool:
    """Return True when the import line carries the ``unguarded-import: ok`` marker."""
    if lineno <= 0 or lineno > len(source_lines):
        return False
    return _UNGUARDED_OK_MARKER in source_lines[lineno - 1]


def _iter_python_sources(root: Path):
    """Yield every ``*.py`` file under *root*."""
    return (p for p in root.rglob("*.py") if p.is_file())


def test_no_remaining_unguarded_optional_imports_in_src():
    """Regression guard. Scan ``src/roam/`` for ANY import (module-level
    OR function-level) of a known-optional package that is NOT inside a
    ``try`` block's body. The fix pattern is::

        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None  # graceful degradation

    This catches the W168 bug class: ``import yaml`` / ``import
    lightgbm`` inside a function body with no try/except.

    If a genuine intentional unguarded import is needed, append
    ``# unguarded-import: ok`` to the import line.

    On failure, prints the file:line of every offender so the fix is
    one-grep away.
    """
    root = _src_roam_root()
    assert root.is_dir(), f"expected src/roam/ to exist at {root}"

    offenders: list[tuple[str, int, str, str]] = []  # (file, lineno, module, line)

    for path in _iter_python_sources(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue

        source_lines = text.splitlines()
        for node in _collect_unguarded_imports_anywhere(tree):
            mod = _references_optional_module(node)
            if mod is None:
                continue
            if _line_has_ok_marker(source_lines, node.lineno):
                continue
            rel = path.relative_to(root.parent.parent)
            # Normalise to POSIX for cross-platform allowlist keys.
            rel_posix = rel.as_posix()
            if (rel_posix, node.lineno, mod) in _KNOWN_PRE_EXISTING_OFFENDERS:
                continue
            line_text = source_lines[node.lineno - 1] if node.lineno - 1 < len(source_lines) else "<eof>"
            offenders.append((rel_posix, node.lineno, mod, line_text.strip()))

    if offenders:
        lines = ["Unguarded optional-dependency imports detected:"]
        for f, ln, mod, src in offenders:
            lines.append(f"  {f}:{ln}: imports optional '{mod}' unguarded -> {src!r}")
        lines.append(
            "Fix: wrap in try/except ImportError and degrade gracefully, "
            "or append '# unguarded-import: ok' if the import is genuinely required."
        )
        pytest.fail("\n".join(lines))


# ---------------------------------------------------------------------------
# Meta — the regression guard must find the two fixed sites when stripped
# ---------------------------------------------------------------------------


def test_scan_helpers_detect_known_optional_modules():
    """Sanity check: the AST helper flags an obviously-bad import string."""
    bad = ast.parse("import yaml\n")
    nodes = _collect_module_level_imports(bad)
    assert len(nodes) == 1
    assert _references_optional_module(nodes[0]) == "yaml"

    good = ast.parse("import os\n")
    nodes = _collect_module_level_imports(good)
    assert len(nodes) == 1
    assert _references_optional_module(nodes[0]) is None

    from_import = ast.parse("from lightgbm import LGBMRanker\n")
    nodes = _collect_module_level_imports(from_import)
    assert len(nodes) == 1
    assert _references_optional_module(nodes[0]) == "lightgbm"

    # Inside a try: the import is NOT a module-level child of ast.Module —
    # it sits inside the Try node's body. Our scan correctly excludes it.
    guarded = ast.parse("try:\n    import yaml\nexcept ImportError:\n    yaml = None\n")
    nodes = _collect_module_level_imports(guarded)
    assert nodes == []


def test_unguarded_anywhere_scan_finds_function_level_imports():
    """The anywhere-scan must catch the W168 bug class: function-level imports."""
    src = "def f():\n    import yaml\n    return yaml.safe_load('')\n"
    tree = ast.parse(src)
    found = _collect_unguarded_imports_anywhere(tree)
    assert len(found) == 1
    assert _references_optional_module(found[0]) == "yaml"

    # And the guarded variant inside a function is NOT flagged.
    guarded_src = (
        "def f():\n    try:\n        import yaml\n    except ImportError:\n        yaml = None\n    return yaml\n"
    )
    found_guarded = _collect_unguarded_imports_anywhere(ast.parse(guarded_src))
    assert _references_optional_module(found_guarded[0]) is None if found_guarded else True
    # Stronger: among optional-module imports, the count is zero.
    assert [n for n in found_guarded if _references_optional_module(n) is not None] == []
