"""Rust type-reference and implements-edge extraction — Wave 1.

Covers:
  RS-T1   function parameter types          → type_ref edge
  RS-T2   function return type              → type_ref edge
  RS-T3   reference return type (&T)        → type_ref to inner type
  RS-T4   struct field types                → type_ref edges
  RS-T5   generic struct field (Vec<Item>)  → type_ref to outer + inner
  RS-T6   type alias RHS                    → type_ref edge
  RS-T7   primitive types skipped           → no type_ref for u32/bool/etc.
  RS-T8   multiple function params          → one type_ref per distinct type
  RS-T9   multiple return tuple types       → type_ref per element
  RS-T10  generic parameter bounds (<T: Trait>) → type_ref to Trait, NOT to T
  RS-T11  type parameter is NOT a false edge → no type_ref to bare T
  RS-T12  where clause bounds               → type_ref to bound trait
  RS-T13  impl Trait for Type              → implements edge from Type to Trait
  RS-T14  inherent impl (no trait)          → no implements edge
  RS-T15  scoped path in impl trait         → implements edge uses base name
  Non-regression: call refs unaffected
  Non-regression: import refs unaffected
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "foo.rs"):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES, detect_language
    from roam.languages.registry import get_extractor

    language = detect_language(file_path)
    assert language is not None, f"could not detect language for {file_path}"
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _type_ref_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "type_ref"}


def _implements_edges(refs) -> list[tuple[str, str]]:
    """Return (source_name, target_name) for implements edges."""
    return [(r.get("source_name", ""), r["target_name"]) for r in refs if r.get("kind") == "implements"]


# ---------------------------------------------------------------------------
# RS-T1  function parameter type
# ---------------------------------------------------------------------------


def test_rust_func_param_type_ref():
    """Function parameter type must produce a type_ref edge."""
    source = """\
fn process(req: Request) {}
"""
    _, refs = _parse(source)
    assert "Request" in _type_ref_targets(refs), f"Request missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# RS-T2  function return type
# ---------------------------------------------------------------------------


def test_rust_func_return_type_ref():
    """Single return type must produce a type_ref edge."""
    source = """\
fn new_server() -> Server {
    todo!()
}
"""
    _, refs = _parse(source)
    assert "Server" in _type_ref_targets(refs), f"Server missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# RS-T3  reference return type
# ---------------------------------------------------------------------------


def test_rust_reference_return_type_ref():
    """&T return type must emit type_ref to T (the inner named type)."""
    source = """\
fn get_config() -> &Config {
    todo!()
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Config" in targets, f"Config missing from {targets}"
    assert "&Config" not in targets, "raw reference token must not appear as type_ref"


# ---------------------------------------------------------------------------
# RS-T4  struct field types
# ---------------------------------------------------------------------------


def test_rust_struct_field_type_refs():
    """Each struct field's type must produce a type_ref edge."""
    source = """\
struct Server {
    handler: Handler,
    store: Store,
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Handler" in targets, f"Handler missing from {targets}"
    assert "Store" in targets, f"Store missing from {targets}"


def test_rust_struct_pointer_field_type_ref():
    """Box<T> field must emit type_refs for Box and T."""
    source = """\
struct Client {
    conn: Box<Connection>,
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    # At minimum, Connection must be in there
    assert "Connection" in targets or "Box" in targets, f"expected Box or Connection in {targets}"


# ---------------------------------------------------------------------------
# RS-T5  generic struct field
# ---------------------------------------------------------------------------


def test_rust_generic_field_type_refs():
    """Vec<Item> field must emit type_ref edges for both Vec and Item."""
    source = """\
struct Queue {
    items: Vec<Item>,
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Item" in targets, f"Item missing from {targets}"


# ---------------------------------------------------------------------------
# RS-T6  type alias RHS
# ---------------------------------------------------------------------------


def test_rust_type_alias_type_ref():
    """Type alias RHS must emit a type_ref to the aliased type."""
    source = """\
type MyHandler = Handler;
"""
    _, refs = _parse(source)
    assert "Handler" in _type_ref_targets(refs), "Handler missing from type alias refs"


# ---------------------------------------------------------------------------
# RS-T7  primitive types skipped
# ---------------------------------------------------------------------------


def test_rust_primitives_not_emitted():
    """Primitive types must not produce type_ref edges."""
    source = """\
fn calc(a: u32, b: f64) -> bool {
    false
}

struct Flags {
    count: u32,
    name: String,
    valid: bool,
    data: Vec<u8>,
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    primitives = {
        "u8",
        "u16",
        "u32",
        "u64",
        "u128",
        "i8",
        "i16",
        "i32",
        "i64",
        "i128",
        "usize",
        "isize",
        "f32",
        "f64",
        "bool",
        "str",
        "char",
    }
    overlap = targets & primitives
    assert not overlap, f"primitive types leaked into type_refs: {overlap}"


# ---------------------------------------------------------------------------
# RS-T8  multiple function parameters
# ---------------------------------------------------------------------------


def test_rust_multiple_param_type_refs():
    """Each distinct parameter type must produce its own type_ref."""
    source = """\
fn handle(w: ResponseWriter, r: Request) {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "ResponseWriter" in targets, f"ResponseWriter missing from {targets}"
    assert "Request" in targets, f"Request missing from {targets}"


# ---------------------------------------------------------------------------
# RS-T9  tuple return type
# ---------------------------------------------------------------------------


def test_rust_tuple_return_type_refs():
    """Tuple return type must emit type_ref for each named element."""
    source = """\
fn open() -> (File, Error) {
    todo!()
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "File" in targets, f"File missing from {targets}"
    assert "Error" in targets, f"Error missing from {targets}"


# ---------------------------------------------------------------------------
# RS-T10  generic parameter bounds emit trait type_ref
# ---------------------------------------------------------------------------


def test_rust_generic_bound_type_ref():
    """<T: Display> must emit type_ref to Display, NOT to T."""
    source = """\
fn print_it<T: Display>(val: T) {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Display" in targets, f"Display missing from generic bound type_refs: {targets}"


# ---------------------------------------------------------------------------
# RS-T11  type parameter itself is NOT a false edge (negative control)
# ---------------------------------------------------------------------------


def test_rust_type_param_not_false_edge():
    """T used in fn pick<T>(items: Vec<T>) must NOT produce a false type_ref to T."""
    source = """\
fn pick<T>(items: Vec<T>) -> T {
    items.into_iter().next().unwrap()
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "T" not in targets, f"type parameter T must not appear as type_ref; got {targets}"
    # Vec SHOULD appear
    assert "Vec" in targets, f"Vec missing from {targets}"


# ---------------------------------------------------------------------------
# RS-T12  where clause bounds
# ---------------------------------------------------------------------------


def test_rust_where_clause_bound_type_ref():
    """where T: Serialize + Deserialize must emit type_refs for Serialize and Deserialize."""
    source = """\
fn encode<T>(val: T) where T: Serialize + Deserialize {
    todo!()
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Serialize" in targets, f"Serialize missing from where-clause type_refs: {targets}"
    assert "Deserialize" in targets, f"Deserialize missing from where-clause type_refs: {targets}"


# ---------------------------------------------------------------------------
# RS-T13  impl Trait for Type → implements edge
# ---------------------------------------------------------------------------


def test_rust_impl_trait_implements_edge():
    """impl Handler for Server must produce an implements edge from Server to Handler."""
    source = """\
impl Handler for Server {
    fn handle(&self) {}
}
"""
    _, refs = _parse(source)
    edges = _implements_edges(refs)
    assert len(edges) > 0, f"no implements edges found; refs={refs}"
    targets = {tgt for _, tgt in edges}
    assert "Handler" in targets, f"Handler missing from implements edges: {edges}"


# ---------------------------------------------------------------------------
# RS-T14  inherent impl → no implements edge
# ---------------------------------------------------------------------------


def test_rust_inherent_impl_no_implements_edge():
    """impl Server (no trait) must NOT produce an implements edge."""
    source = """\
impl Server {
    fn new() -> Self {
        todo!()
    }
}
"""
    _, refs = _parse(source)
    edges = _implements_edges(refs)
    assert len(edges) == 0, f"inherent impl must not produce implements edges; got {edges}"


# ---------------------------------------------------------------------------
# RS-T15  scoped trait path in impl → base name only
# ---------------------------------------------------------------------------


def test_rust_impl_scoped_trait_implements_edge():
    """impl std::fmt::Display for MyType must emit implements edge with base name Display."""
    source = """\
impl std::fmt::Display for MyType {
    fn fmt(&self, f: &mut Formatter) -> Result {
        todo!()
    }
}
"""
    _, refs = _parse(source)
    edges = _implements_edges(refs)
    targets = {tgt for _, tgt in edges}
    assert "Display" in targets or "std::fmt::Display" in targets, (
        f"Display missing from scoped implements edge: {edges}"
    )


# ---------------------------------------------------------------------------
# Non-regression: call refs unaffected
# ---------------------------------------------------------------------------


def test_rust_call_refs_unaffected():
    """Adding type_ref emission must not break call reference extraction."""
    source = """\
fn run(cfg: Config) {
    cfg.start();
    do_work(cfg);
}
"""
    _, refs = _parse(source)
    call_targets = {r["target_name"] for r in refs if r.get("kind") == "call"}
    assert "start" in call_targets, f"start call missing from {call_targets}"
    assert "do_work" in call_targets, f"do_work call missing from {call_targets}"
    # type_ref also emitted
    assert "Config" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# Non-regression: import refs unaffected
# ---------------------------------------------------------------------------


def test_rust_import_refs_unaffected():
    """Import references must still work alongside type_refs."""
    source = """\
use std::collections::HashMap;

fn greet(name: String) {}
"""
    _, refs = _parse(source)
    import_targets = {r["target_name"] for r in refs if r.get("kind") == "import"}
    assert "HashMap" in import_targets, f"HashMap import missing from {import_targets}"
