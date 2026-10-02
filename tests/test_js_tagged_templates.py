"""JS tagged template literals — conservation tests (item #21).

tree-sitter-javascript parses tagged templates as call_expression(function=identifier,
arguments=template_string), so the existing _extract_call handler already emits the
call edge. These tests document and protect that behavior.

JS-TT1  tagged template tag emits call edge  (conservation — already works)
JS-TT2  non-tagged template emits no extra call edge  (conservation)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _parse(source_text: str, file_path: str = "foo.js"):
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
    references = extractor.extract_references(tree, source, file_path)
    return references


def _call_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "call"}


# ---------------------------------------------------------------------------
# JS-TT1  tagged template tag emits call edge
# ---------------------------------------------------------------------------


def test_js_tagged_template_emits_call():
    """gql`...` must produce a call edge to gql.

    Conservation test — tree-sitter parses tagged templates as call_expression
    so the existing _extract_call handler covers this already.
    """
    source = "gql`query { user { id } }`"
    refs = _parse(source)
    assert "gql" in _call_targets(refs), f"gql call edge missing from {_call_targets(refs)}"


def test_js_tagged_template_css_emits_call():
    """css`color: red` must produce a call edge to css."""
    source = "const styles = css`color: red; background: blue`"
    refs = _parse(source)
    assert "css" in _call_targets(refs), f"css call edge missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# JS-TT2  non-tagged template emits no extra call edge
# ---------------------------------------------------------------------------


def test_js_non_tagged_template_no_call():
    """Plain template literal `hello ${name}` must not produce a call edge
    to a template tag that doesn't exist."""
    source = "const msg = `hello ${name}`"
    refs = _parse(source)
    # No call to a non-existent tag
    assert "msg" not in _call_targets(refs)
    # The set may contain other calls from the expression, but no template tag
    # (there is no tag here — this is just a template literal, not a tagged one)
