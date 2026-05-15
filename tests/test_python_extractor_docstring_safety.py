"""W159 — docstring-safety tests for Python import extraction.

The W149 dogfood audit found a phantom ``'or'`` import attributed to
``src/roam/index/pytest_fixtures.py:5``. Root cause: the regex-based
scanner in ``cmd_orphan_imports.py`` was matching the literal English text

    "...not visible from any\\nimport or call edge..."

inside the module docstring against ``^\\s*import\\s+([\\w.]+)`` and
capturing ``or`` as a module name. The tree-sitter AST extractor in
``src/roam/languages/python_lang.py`` was never the source of the
phantom — its import handling lives in ``_extract_import`` /
``_extract_from_import`` and only fires on real ``import_statement`` /
``import_from_statement`` AST nodes (docstring text is a ``string``
node and bypasses both code paths).

These tests pin both invariants:

1. The Python AST extractor extracts ONLY real imports — never docstring
   or comment prose, even when the docstring contains the literal words
   ``import``, ``from``, ``as``.
2. The orphan-imports regex scanner skips imports nested in docstrings
   and ``#`` line comments, eliminating the W159 phantom.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Helper: parse Python source and extract symbols + references via the AST
# ---------------------------------------------------------------------------


def _parse_py(source_text: str, file_path: str = "example.py"):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor

    grammar = GRAMMAR_ALIASES.get("python", "python")
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor("python")
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _import_targets(refs):
    return [r["target_name"] for r in refs if r["kind"] == "import"]


# ===========================================================================
# 1. AST extractor: docstrings never produce phantom import edges
# ===========================================================================


def test_module_docstring_with_word_import_does_not_phantom():
    """Module docstring containing the word ``import`` — only real imports."""
    source = '''"""Module docstring.

    This text mentions the word import or call edge but is just prose.
    """

import os
'''
    _, refs = _parse_py(source)
    targets = _import_targets(refs)
    assert "os" in targets
    assert "or" not in targets
    assert "import" not in targets
    assert "edge" not in targets


def test_module_docstring_with_word_from_does_not_phantom():
    """Module docstring containing the word ``from`` — only real imports."""
    source = '''"""Module docstring.

    Imports are pulled from various modules including os.
    """

import os
import re
'''
    _, refs = _parse_py(source)
    targets = _import_targets(refs)
    assert sorted(targets) == ["os", "re"]


def test_function_docstring_with_import_text_does_not_phantom():
    """Function docstring containing ``import X`` shouldn't extract X."""
    source = '''import json


def parse(text: str):
    """Parse JSON text.

    Note: this function uses ``import json`` internally — the prose
    here is for documentation only. Also mentions: from typing import Any.
    """
    return json.loads(text)
'''
    _, refs = _parse_py(source)
    targets = _import_targets(refs)
    assert targets == ["json"]
    # None of the docstring tokens leak through as imports
    assert "Any" not in targets
    assert "typing" not in targets


def test_pytest_fixtures_real_file_extractor_no_phantom():
    """End-to-end: parse the actual file from the W149 audit.

    ``src/roam/index/pytest_fixtures.py:5`` was the line the audit
    flagged. The AST extractor should produce only real imports
    (``__future__``, ``os``, ``re``) and never the docstring word
    ``or``.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "src" / "roam" / "index" / "pytest_fixtures.py"
    if not target.is_file():
        # Optional file — skip when running outside the roam-code repo.
        import pytest

        pytest.skip(f"{target} not present in this checkout")

    source_text = target.read_text(encoding="utf-8")
    _, refs = _parse_py(source_text, file_path=str(target))
    targets = _import_targets(refs)
    # All three real imports must be present.
    assert "os" in targets
    assert "re" in targets
    # The W149 phantom must NOT appear.
    assert "or" not in targets
    assert "call" not in targets
    assert "edge" not in targets


# ===========================================================================
# 2. orphan-imports regex scanner: docstring + comment masking eliminates phantoms
# ===========================================================================


def test_orphan_imports_mask_strips_triple_quoted_string_content():
    """``_mask_python_strings_and_comments`` blanks docstring prose.

    The mask preserves byte offsets and newlines — only the content
    inside triple-quoted strings gets replaced with spaces. Without
    this, the ``^[ \\t]*import[ \\t]+([\\w.]+)`` arm of
    ``_PY_IMPORT_RE`` would capture prose tokens (``or``, ``fails``)
    inside module docstrings as phantom modules.
    """
    from roam.commands.cmd_orphan_imports import _mask_python_strings_and_comments

    src = '"""\nimport or call edge\n"""\nimport os\n'
    masked = _mask_python_strings_and_comments(src)
    # Same length (mask preserves offsets).
    assert len(masked) == len(src)
    # Same number of newlines (line numbers stay accurate).
    assert masked.count("\n") == src.count("\n")
    # Real ``import os`` survives in the masked text.
    assert "import os" in masked
    # The docstring's ``import or`` does not.
    assert "import or" not in masked


def test_orphan_imports_mask_strips_line_comment_content():
    """``_mask_python_strings_and_comments`` also blanks ``#`` comments."""
    from roam.commands.cmd_orphan_imports import _mask_python_strings_and_comments

    src = "# import re_phantom\nimport os\n"
    masked = _mask_python_strings_and_comments(src)
    assert "import os" in masked
    assert "import re_phantom" not in masked


def test_orphan_imports_regex_does_not_match_docstring_import_words():
    """The W159 root scenario — ``import or`` inside a docstring.

    Applying the mask + the regex must produce zero matches for
    ``or`` and exactly one match for the real ``import os`` below.
    """
    from roam.commands.cmd_orphan_imports import (
        _PY_IMPORT_RE,
        _mask_python_strings_and_comments,
    )

    src = (
        '"""Module docstring.\n'
        "\n"
        "    A fixture's parameters are *other fixtures*. The relationship is\n"
        "    implicit (parameter name == fixture name) and not visible from any\n"
        "    import or call edge, so the regular call-graph extractor misses it\n"
        "    entirely.\n"
        '    """\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import re\n"
    )
    masked = _mask_python_strings_and_comments(src)
    modules = []
    for m in _PY_IMPORT_RE.finditer(masked):
        modules.append(m.group(1) or m.group(2))
    # Real imports captured.
    assert "os" in modules
    assert "re" in modules
    assert "__future__" in modules
    # W159 phantom NOT captured.
    assert "or" not in modules
    assert "call" not in modules
    assert "edge" not in modules


def test_orphan_imports_regex_preserves_indented_relative_imports():
    """Indented ``from .x import Y`` inside a function must NOT be flagged.

    Regression guard for an off-by-one bug that surfaced while
    tightening ``_PY_IMPORT_RE``: changing the leading ``^\\s*`` to
    ``^[ \\t]*`` (so blank lines aren't greedily consumed) preserves
    the ``_PY_RELATIVE_PREFIX_RE`` filter for indented relative
    imports — which previously broke when an immediately-preceding
    comment line got blanked to whitespace.
    """
    from roam.commands.cmd_orphan_imports import (
        _PY_IMPORT_RE,
        _PY_RELATIVE_PREFIX_RE,
        _mask_python_strings_and_comments,
    )

    src = (
        "def factory(language):\n"
        "    if language == 'generic':\n"
        "        # Use the generic extractor for tier-2 languages\n"
        "        from .generic_lang import GenericExtractor\n"
        "        return GenericExtractor()\n"
        "    return None\n"
    )
    masked = _mask_python_strings_and_comments(src)
    flagged_non_relative = []
    for m in _PY_IMPORT_RE.finditer(masked):
        line_start = m.start()
        line_end = masked.find("\n", line_start)
        line = masked[line_start:line_end] if line_end > 0 else masked[line_start:]
        if _PY_RELATIVE_PREFIX_RE.match(line):
            continue
        flagged_non_relative.append(m.group(1) or m.group(2))
    assert flagged_non_relative == []


def test_orphan_imports_scanner_no_phantom_or_on_pytest_fixtures():
    """End-to-end regression: ``_scan_python`` on the real W149 fixture file.

    Build a minimal in-memory sqlite DB matching the columns
    ``_scan_python`` queries, point ``files.path`` at the real
    ``src/roam/index/pytest_fixtures.py``, and assert no orphan
    has ``module == 'or'``.
    """
    from pathlib import Path
    import sqlite3

    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "src" / "roam" / "index" / "pytest_fixtures.py"
    if not target.is_file():
        import pytest

        pytest.skip(f"{target} not present in this checkout")

    from roam.commands.cmd_orphan_imports import _scan_python

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (path TEXT, language TEXT)")
    rel = target.relative_to(repo_root).as_posix()
    conn.execute(
        "INSERT INTO files (path, language) VALUES (?, 'python')",
        (rel,),
    )
    conn.commit()

    import os as _os

    cwd_before = _os.getcwd()
    try:
        _os.chdir(repo_root)
        orphans, _files_scanned = _scan_python(conn)
    finally:
        _os.chdir(cwd_before)

    flagged = [
        (o.get("module"), o.get("line"))
        for o in orphans
        if (o.get("file") or "").endswith("pytest_fixtures.py")
    ]
    # W149 phantom MUST NOT appear.
    assert ("or", 5) not in flagged
    assert all(m != "or" for m, _ln in flagged), (
        f"phantom 'or' import still surfaced for pytest_fixtures.py: {flagged}"
    )
