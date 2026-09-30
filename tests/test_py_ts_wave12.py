"""Wave 12 TypeScript type_ref regression tests — readonly_type gap.

Gap confirmed by probe (2026-09-30):
  type alias        — worked via _walk_refs fallback (readonly_type → tuple_type)
  interface property — MISSED (type_annotation → readonly_type not in context set)
  function param     — MISSED (same path)
  class property     — MISSED (same path)

Fix: add "readonly_type" to _TS_TYPE_CONTEXT_NODES so _walk_type_node recurses
into it when encountered inside an already-active type context.

Tests:
  TS-W12-1  interface property: readonly [string, MyTypeB] — MyTypeB type_ref
  TS-W12-2  function parameter: readonly [string, MyTypeC] — MyTypeC type_ref
  TS-W12-3  class property: readonly [string, MyTypeD] — MyTypeD type_ref
  TS-W12-4  type alias: readonly [string, MyTypeA] — MyTypeA type_ref (regression)
  TS-W12-5  readonly nested tuple: readonly [readonly [Inner, Outer]] — both captured
  TS-W12-6  readonly array type: readonly MyElem[] — MyElem type_ref
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")
pytest.importorskip("tree_sitter")


def _type_refs(source: str) -> set[str]:
    import sys

    sys.path.insert(0, "src")
    from tree_sitter_language_pack import get_parser

    from roam.languages.typescript_lang import TypeScriptExtractor

    parser = get_parser("typescript")
    ext = TypeScriptExtractor()
    tree = parser.parse(source.encode())
    refs: list[dict] = []
    ext._walk_refs(tree.root_node, source.encode(), refs, scope_name="test")
    return {r["target_name"] for r in refs if r.get("kind") == "type_ref"}


# TS-W12-1  interface property
def test_ts_readonly_interface_property():
    """readonly [string, MyTypeB] as interface property type — MyTypeB must be a type_ref."""
    source = "interface Foo { bar: readonly [string, MyTypeB]; }\n"
    assert "MyTypeB" in _type_refs(source), "type_ref to MyTypeB (readonly tuple in interface) missing"


# TS-W12-2  function parameter
def test_ts_readonly_function_param():
    """readonly [string, MyTypeC] as function parameter type — MyTypeC must be a type_ref."""
    source = "function fn(x: readonly [string, MyTypeC]): void {}\n"
    assert "MyTypeC" in _type_refs(source), "type_ref to MyTypeC (readonly tuple in fn param) missing"


# TS-W12-3  class property
def test_ts_readonly_class_property():
    """readonly [string, MyTypeD] as class property type — MyTypeD must be a type_ref."""
    source = "class C { foo: readonly [string, MyTypeD]; }\n"
    assert "MyTypeD" in _type_refs(source), "type_ref to MyTypeD (readonly tuple in class) missing"


# TS-W12-4  type alias (regression — already worked via _walk_refs fallback)
def test_ts_readonly_type_alias_regression():
    """type T = readonly [string, MyTypeA] — MyTypeA must remain a type_ref."""
    source = "type T = readonly [string, MyTypeA];\n"
    assert "MyTypeA" in _type_refs(source), "type_ref to MyTypeA (readonly tuple in type alias) missing"


# TS-W12-5  nested readonly tuple
def test_ts_readonly_nested_tuple():
    """readonly [readonly [Inner, Outer]] — both Inner and Outer must be type_refs."""
    source = "interface X { pair: readonly [readonly [Inner, Outer]]; }\n"
    refs = _type_refs(source)
    assert "Inner" in refs, "type_ref to Inner (nested readonly) missing"
    assert "Outer" in refs, "type_ref to Outer (nested readonly) missing"


# TS-W12-6  readonly array shorthand
def test_ts_readonly_array_shorthand():
    """readonly MyElem[] as property type — MyElem must be a type_ref."""
    source = "interface Arr { items: readonly MyElem[]; }\n"
    assert "MyElem" in _type_refs(source), "type_ref to MyElem (readonly array) missing"
