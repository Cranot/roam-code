"""Ruby visibility modifier symbol refs — item #23.

RB-V1  private :method emits call ref to the method name
RB-V2  protected :method emits call ref to the method name
RB-V3  plain def without visibility — no spurious visibility ref
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _parse(source_text: str, file_path: str = "foo.rb"):
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


def _call_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "call"}


# ---------------------------------------------------------------------------
# RB-V1  private :method emits call ref
# ---------------------------------------------------------------------------


def test_ruby_private_symbol_emits_call():
    """private :handle must emit a call ref to handle."""
    refs = _parse("private :handle")
    targets = _call_targets(refs)
    assert "handle" in targets, f"handle missing from {targets}"


def test_ruby_protected_symbol_emits_call():
    """protected :process must emit a call ref to process."""
    refs = _parse("protected :process")
    targets = _call_targets(refs)
    assert "process" in targets, f"process missing from {targets}"


# ---------------------------------------------------------------------------
# RB-V3  plain def without visibility — no spurious ref
# ---------------------------------------------------------------------------


def test_ruby_plain_def_no_visibility_ref():
    """def work; end must not produce a visibility-related call ref to 'work'
    from a visibility modifier (no modifier here)."""
    refs = _parse("def work; end")
    targets = _call_targets(refs)
    # No call refs expected for a plain def — it's a symbol definition, not a call
    assert "private" not in targets
    assert "protected" not in targets
