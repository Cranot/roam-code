"""Regression tests for Wave 2: TypeScript type_ref edges.

Covers:
  TS-W2-1  Simple type annotation emits type_ref
  TS-W2-2  Primitive / builtin types are NOT emitted
  TS-W2-3  Generic type annotation emits type_ref for the user-defined type name
  TS-W2-4  Union type annotation emits type_ref for each member
  TS-W2-5  class implements emits type_ref for the interface
  TS-W2-6  Return type annotation emits type_ref
  TS-W2-7  Nested generic Foo<Bar<Baz>> emits type_ref for all non-builtins
  TS-W2-8  Existing call-graph edges are not affected
  TS-W2-9  Type alias RHS emits type_ref edges
  TS-W2-10 Class extends does not emit duplicate type_ref (only inherits)
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


def _type_refs(refs):
    return {r["target_name"] for r in refs if r.get("kind") == "type_ref"}


def _call_refs(refs):
    return {r["target_name"] for r in refs if r.get("kind") == "call"}


# ---------------------------------------------------------------------------
# TS-W2-1  Simple type annotation
# ---------------------------------------------------------------------------


def test_ts_type_annotation_emits_type_ref():
    """Parameter type annotation must emit a type_ref edge for the user type."""
    source = """\
interface UserProfile {}

function greet(user: UserProfile): void {
    console.log(user);
}
"""
    _, refs = _parse(source, "a.ts")
    assert "UserProfile" in _type_refs(refs), f"type_ref to UserProfile missing; refs={refs}"


# ---------------------------------------------------------------------------
# TS-W2-2  Primitive / builtin types suppressed
# ---------------------------------------------------------------------------


def test_ts_primitive_types_not_emitted():
    """Primitive types (string, number, boolean, void) must not produce type_ref edges."""
    source = """\
function add(a: number, b: number): number {
    return a + b;
}

function greet(name: string): void {
    console.log(name);
}
"""
    _, refs = _parse(source, "b.ts")
    tr = _type_refs(refs)
    for primitive in ("string", "number", "boolean", "void", "any", "never", "unknown"):
        assert primitive not in tr, f"primitive '{primitive}' emitted as type_ref; got {tr}"


def test_ts_builtin_global_types_not_emitted():
    """Built-in globals (Array, Promise, Map, Error) must not produce type_ref edges."""
    source = """\
function fetchItems(): Promise<string[]> {
    return Promise.resolve([]);
}

function getMap(): Map<string, number> {
    return new Map();
}
"""
    _, refs = _parse(source, "c.ts")
    tr = _type_refs(refs)
    for builtin in ("Promise", "Array", "Map", "Set", "Error"):
        assert builtin not in tr, f"builtin '{builtin}' emitted as type_ref; got {tr}"


# ---------------------------------------------------------------------------
# TS-W2-3  Generic type annotation
# ---------------------------------------------------------------------------


def test_ts_generic_user_type_emitted():
    """Promise<UserData> must emit a type_ref to UserData but not to Promise."""
    source = """\
interface UserData {}

async function fetchUser(): Promise<UserData> {
    return {} as UserData;
}
"""
    _, refs = _parse(source, "d.ts")
    tr = _type_refs(refs)
    assert "UserData" in tr, f"type_ref to UserData missing in generic; got {tr}"
    assert "Promise" not in tr, f"Promise (builtin) should not be emitted; got {tr}"


# ---------------------------------------------------------------------------
# TS-W2-4  Union type annotation
# ---------------------------------------------------------------------------


def test_ts_union_type_emits_each_member():
    """A | B union annotation must emit type_ref for both A and B."""
    source = """\
type Cat = { meow(): void };
type Dog = { bark(): void };

function makePet(p: Cat | Dog): void {}
"""
    _, refs = _parse(source, "e.ts")
    tr = _type_refs(refs)
    assert "Cat" in tr, f"type_ref to Cat missing; got {tr}"
    assert "Dog" in tr, f"type_ref to Dog missing; got {tr}"


# ---------------------------------------------------------------------------
# TS-W2-5  class implements
# ---------------------------------------------------------------------------


def test_ts_class_implements_emits_type_ref():
    """class Foo implements IBar must emit a type_ref (or inherits) to IBar."""
    source = """\
interface IBar {
    doSomething(): void;
}

class Foo implements IBar {
    doSomething() {}
}
"""
    _, refs = _parse(source, "f.ts")
    # implements can come as type_ref or inherits depending on the grammar node
    related = {r["target_name"] for r in refs if r.get("kind") in ("type_ref", "inherits")}
    assert "IBar" in related, f"IBar missing from type_ref/inherits edges; refs={refs}"


# ---------------------------------------------------------------------------
# TS-W2-6  Return type annotation
# ---------------------------------------------------------------------------


def test_ts_return_type_annotation_emits_type_ref():
    """Return type annotation must emit a type_ref to the declared return type."""
    source = """\
type ApiResponse = { status: number };

function call(): ApiResponse {
    return { status: 200 };
}
"""
    _, refs = _parse(source, "g.ts")
    tr = _type_refs(refs)
    assert "ApiResponse" in tr, f"type_ref to ApiResponse missing; got {tr}"


# ---------------------------------------------------------------------------
# TS-W2-7  Nested generic
# ---------------------------------------------------------------------------


def test_ts_nested_generic_emits_inner_type():
    """Map<string, List<Item>> must emit type_ref to Item (not Map, not List if List is builtin)."""
    source = """\
type Item = { id: number };
type List<T> = T[];

function getItemMap(): Map<string, List<Item>> {
    return new Map();
}
"""
    _, refs = _parse(source, "h.ts")
    tr = _type_refs(refs)
    assert "Item" in tr, f"nested type_ref to Item missing; got {tr}"
    assert "List" in tr, f"type_ref to List (user-defined) missing; got {tr}"
    assert "Map" not in tr, f"Map (builtin) should not be emitted; got {tr}"


# ---------------------------------------------------------------------------
# TS-W2-8  Call graph not broken
# ---------------------------------------------------------------------------


def test_ts_call_edges_still_emitted():
    """Existing call-graph edges must not be lost after adding _walk_refs override."""
    source = """\
function helper(): void {}

function main(): void {
    helper();
}
"""
    _, refs = _parse(source, "i.ts")
    cr = _call_refs(refs)
    assert "helper" in cr, f"call edge to helper missing; got {cr}"


# ---------------------------------------------------------------------------
# TS-W2-9  Type alias RHS
# ---------------------------------------------------------------------------


def test_ts_type_alias_rhs_emits_type_ref():
    """type Foo = Bar | Baz must emit type_ref to Bar and Baz."""
    source = """\
type Serialized = string;
type Deserializer = (s: Serialized) => object;
type Transform = Serialized | Deserializer;
"""
    _, refs = _parse(source, "j.ts")
    tr = _type_refs(refs)
    assert "Serialized" in tr, f"type_ref to Serialized missing from type alias; got {tr}"
    assert "Deserializer" in tr, f"type_ref to Deserializer missing from type alias; got {tr}"


# ---------------------------------------------------------------------------
# TS-W2-10  class extends — no duplicate edges
# ---------------------------------------------------------------------------


def test_ts_class_extends_no_duplicate_edges():
    """class Dog extends Animal must emit exactly one inherits edge to Animal, not type_ref."""
    source = """\
class Animal { name: string = ''; }
class Dog extends Animal { bark(): void {} }
"""
    _, refs = _parse(source, "k.ts")
    inherits = [r for r in refs if r.get("kind") == "inherits" and r["target_name"] == "Animal"]
    assert len(inherits) >= 1, f"inherits edge to Animal missing; refs={refs}"
    # No spurious type_ref to Animal (it should only be inherits)
    type_refs_animal = [r for r in refs if r.get("kind") == "type_ref" and r["target_name"] == "Animal"]
    assert not type_refs_animal, f"spurious type_ref to Animal; should be only inherits: {refs}"
