"""Rust brace-list import splitting — item #17.

RS-BI1  brace import emits one edge per item (not one raw-text edge)
RS-BI2  simple use (no braces) still works — non-regression
RS-BI3  use with self — self is skipped, named items are emitted
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _parse(source_text: str, file_path: str = "foo.rs"):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES, detect_language
    from roam.languages.registry import get_extractor

    language = detect_language(file_path)
    assert language is not None
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor(language)
    return extractor.extract_references(tree, source, file_path)


def _import_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "import"}


# ---------------------------------------------------------------------------
# RS-BI1  brace import splits into individual edges
# ---------------------------------------------------------------------------


def test_rust_brace_import_splits():
    """use std::io::{Read, Write} must emit two separate import edges."""
    refs = _parse("use std::io::{Read, Write};")
    targets = _import_targets(refs)
    assert "Read" in targets, f"Read missing from {targets}"
    assert "Write" in targets, f"Write missing from {targets}"
    # Must NOT emit the raw combined text as a target
    assert "Read, Write" not in targets, f"raw combined text leaked: {targets}"


def test_rust_brace_import_three_items():
    """use std::io::{Read, Write, BufReader} must emit three import edges."""
    refs = _parse("use std::io::{Read, Write, BufReader};")
    targets = _import_targets(refs)
    assert "Read" in targets
    assert "Write" in targets
    assert "BufReader" in targets
    assert len(targets) == 3, f"expected exactly 3 import targets, got {targets}"


# ---------------------------------------------------------------------------
# RS-BI2  simple use (no braces) still works
# ---------------------------------------------------------------------------


def test_rust_simple_use_unaffected():
    """use std::io (no braces) must still emit one import edge to io."""
    refs = _parse("use std::io;")
    targets = _import_targets(refs)
    assert "io" in targets, f"io missing from {targets}"


# ---------------------------------------------------------------------------
# RS-BI3  use with self — skip self, keep named items
# ---------------------------------------------------------------------------


def test_rust_brace_import_with_self():
    """use std::io::{self, Read} — Read is emitted; self may be skipped or emitted as 'io'."""
    refs = _parse("use std::io::{self, Read};")
    targets = _import_targets(refs)
    assert "Read" in targets, f"Read missing from {targets}"
    # self as a raw import target name is not useful — verify it's not the raw text
    assert "self, Read" not in targets
