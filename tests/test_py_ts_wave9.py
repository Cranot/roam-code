"""Regression tests for Wave 9: tuple rest/optional types and mapped-type index signatures.

Covers:
  TS-W9-1   rest_type in tuple emits type_ref for the spread type
  TS-W9-2   rest_type works with a generic spread type
  TS-W9-3   optional_type in tuple emits type_ref for the base type
  TS-W9-4   optional_type with multiple elements — all emitted
  TS-W9-5   mapped_type keyof reference emits type_ref for the source type
  TS-W9-6   mapped_type value annotation emits type_ref
  TS-W9-7   regular index signature value type emits type_ref
  TS-W9-8   infer_type declaration does NOT emit type_ref for the infer variable
  TS-W9-9   conditional_type true/false branches still emit type_refs (pre-existing)
  TS-W9-10  abstract class implements clause still emits type_ref (pre-existing)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str):
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
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _refs_of_kind(refs, kind):
    return {r["target_name"] for r in refs if r.get("kind") == kind}


# ---------------------------------------------------------------------------
# TS-W9-1  rest_type in tuple emits type_ref for the spread type
# ---------------------------------------------------------------------------


def test_ts_rest_type_bare():
    """type WithRest = [FirstItem, ...OtherTypes] must emit type_ref to OtherTypes."""
    source = "type WithRest = [FirstItem, ...OtherTypes];\n"
    _, refs = _parse(source, "tuples.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "OtherTypes" in type_refs, "type_ref to OtherTypes missing"
    assert "FirstItem" in type_refs, "type_ref to FirstItem missing"


# ---------------------------------------------------------------------------
# TS-W9-2  rest_type works with a generic spread type
# ---------------------------------------------------------------------------


def test_ts_rest_type_generic():
    """type T = [Head, ...Partial<Tail>] — Head and Tail must appear as type_refs."""
    source = "type T = [Head, ...MyWrapper<Tail>];\n"
    _, refs = _parse(source, "tuples.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "Head" in type_refs, "type_ref to Head missing"
    assert "MyWrapper" in type_refs, "type_ref to MyWrapper missing"
    assert "Tail" in type_refs, "type_ref to Tail missing"


# ---------------------------------------------------------------------------
# TS-W9-3  optional_type in tuple emits type_ref for the base type
# ---------------------------------------------------------------------------


def test_ts_optional_type_single():
    """type Opts = [MyType?] must emit type_ref to MyType."""
    source = "type Opts = [MyType?];\n"
    _, refs = _parse(source, "tuples.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyType" in type_refs, "type_ref to MyType missing"


# ---------------------------------------------------------------------------
# TS-W9-4  optional_type with multiple elements — all emitted
# ---------------------------------------------------------------------------


def test_ts_optional_type_multiple():
    """type Opts = [RequiredItem, MyType?, OtherType?] — all user types emitted."""
    source = "type Opts = [RequiredItem, MyType?, OtherType?];\n"
    _, refs = _parse(source, "tuples.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "RequiredItem" in type_refs, "type_ref to RequiredItem missing"
    assert "MyType" in type_refs, "type_ref to MyType missing"
    assert "OtherType" in type_refs, "type_ref to OtherType missing"


# ---------------------------------------------------------------------------
# TS-W9-5  mapped_type keyof reference emits type_ref for the source type
# ---------------------------------------------------------------------------


def test_ts_mapped_type_keyof_source():
    """type M = { [K in keyof MyConfig]: string } must emit type_ref to MyConfig."""
    source = "type M = { [K in keyof MyConfig]: string };\n"
    _, refs = _parse(source, "mapped.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyConfig" in type_refs, "type_ref to MyConfig missing"


# ---------------------------------------------------------------------------
# TS-W9-6  mapped_type value annotation emits type_ref
# ---------------------------------------------------------------------------


def test_ts_mapped_type_value_type():
    """type M = { [K in keyof MyConfig]: MyValue } must emit type_ref to both."""
    source = "type M = { [K in keyof MyConfig]: MyValue };\n"
    _, refs = _parse(source, "mapped.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyConfig" in type_refs, "type_ref to MyConfig missing"
    assert "MyValue" in type_refs, "type_ref to MyValue missing"


# ---------------------------------------------------------------------------
# TS-W9-7  regular index signature value type emits type_ref
# ---------------------------------------------------------------------------


def test_ts_index_signature_value_type():
    """type Rec = { [key: string]: MyValue } must emit type_ref to MyValue."""
    source = "type Rec = { [key: string]: MyValue };\n"
    _, refs = _parse(source, "indexed.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyValue" in type_refs, "type_ref to MyValue missing"
    # The string key type is a builtin, must not appear as a type_ref
    assert "string" not in type_refs, "'string' must not appear as a type_ref"


# ---------------------------------------------------------------------------
# TS-W9-8  infer_type declaration does NOT emit type_ref for the infer variable
# ---------------------------------------------------------------------------


def test_ts_infer_type_no_decl_ref():
    """infer U in 'T extends Promise<infer U>' — U inside infer_type node is not a type_ref."""
    # The only way to check this precisely: use a conditional where infer declares U
    # but we DON'T use U in the true/false branch. If U appears as a type_ref, the
    # infer declaration is being incorrectly traversed.
    source = "type DropHead<T extends any[]> = T extends [infer _H, ...infer Tail] ? Tail : never;\n"
    _, refs = _parse(source, "infer.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    # _H and Tail are infer variables — declared, not referenced.
    # Note: Tail appears in '? Tail :' as a type_identifier USE after the '?',
    # so it will be emitted (that is correct). _H is only in infer position so
    # it must NOT be emitted.
    assert "_H" not in type_refs, "_H (infer-only variable) must not appear as type_ref"


# ---------------------------------------------------------------------------
# TS-W9-9  conditional_type true/false branches still emit type_refs (pre-existing)
# ---------------------------------------------------------------------------


def test_ts_conditional_type_branches():
    """type C<T> = T extends Base ? TrueType : FalseType — Base/TrueType/FalseType emitted."""
    source = "type C<T> = T extends BaseClass ? TrueResult : FalseResult;\n"
    _, refs = _parse(source, "cond.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "BaseClass" in type_refs, "type_ref to BaseClass missing"
    assert "TrueResult" in type_refs, "type_ref to TrueResult missing"
    assert "FalseResult" in type_refs, "type_ref to FalseResult missing"


# ---------------------------------------------------------------------------
# TS-W9-10  abstract class implements clause still emits type_ref (pre-existing)
# ---------------------------------------------------------------------------


def test_ts_abstract_class_implements():
    """abstract class Foo implements Bar — Bar must produce a type_ref."""
    source = "abstract class Foo implements BarInterface { abstract run(): void; }\n"
    _, refs = _parse(source, "abstract.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "BarInterface" in type_refs, "type_ref to BarInterface missing"
