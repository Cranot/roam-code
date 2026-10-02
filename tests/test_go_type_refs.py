"""Go type-reference extraction — Wave 1 (BLOCKING gap fix).

Covers:
  GO-T1  function parameter types    → type_ref edge
  GO-T2  function return types       → type_ref edge
  GO-T3  pointer return type         → type_ref to pointed-at type
  GO-T4  struct field types          → type_ref edge
  GO-T5  map key/value types         → two type_ref edges
  GO-T6  slice element type          → type_ref edge
  GO-T7  qualified type (pkg.Type)   → type_ref edge
  GO-T8  var declaration type        → type_ref edge
  GO-T9  type alias                  → type_ref edge
  GO-T10 method receiver type        → NOT a type_ref (it's the symbol definition)
  GO-T11 multiple return types       → type_ref edges for each
  GO-T12 built-in types skipped      → no type_ref for int/string/error/bool
  GO-T13 generic type arguments      → type_ref edges for type args
  GO-T14 channel type                → type_ref edge
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "foo.go"):
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


# ---------------------------------------------------------------------------
# GO-T1  function parameter types
# ---------------------------------------------------------------------------


def test_go_func_param_type_ref():
    """Parameter type must produce a type_ref edge."""
    source = """\
package main

func Process(req Request) {}
"""
    _, refs = _parse(source)
    assert "Request" in _type_ref_targets(refs), f"Request missing from {_type_ref_targets(refs)}"


def test_go_func_multiple_param_type_refs():
    """Each distinct parameter type must produce its own type_ref."""
    source = """\
package main

func Handle(w ResponseWriter, r Request) {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "ResponseWriter" in targets
    assert "Request" in targets


# ---------------------------------------------------------------------------
# GO-T2  function return type
# ---------------------------------------------------------------------------


def test_go_func_return_type_ref():
    """Single return type must produce a type_ref edge."""
    source = """\
package main

func NewServer() Server {}
"""
    _, refs = _parse(source)
    assert "Server" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# GO-T3  pointer return type
# ---------------------------------------------------------------------------


def test_go_pointer_return_type_ref():
    """Pointer return type must emit type_ref to the pointed-at type."""
    source = """\
package main

func New() *Config {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Config" in targets, f"Config missing from {targets}"
    # Should NOT emit a type_ref for the pointer itself (not a named type)
    assert "*Config" not in targets


# ---------------------------------------------------------------------------
# GO-T4  struct field types
# ---------------------------------------------------------------------------


def test_go_struct_field_type_ref():
    """Struct field types must produce type_ref edges."""
    source = """\
package main

type Server struct {
    handler Handler
    store   Store
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Handler" in targets
    assert "Store" in targets


def test_go_struct_pointer_field_type_ref():
    """Pointer struct fields must emit type_ref to the pointed-at type."""
    source = """\
package main

type Client struct {
    conn *Connection
}
"""
    _, refs = _parse(source)
    assert "Connection" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# GO-T5  map key/value types
# ---------------------------------------------------------------------------


def test_go_map_type_refs():
    """Map field must emit type_ref for both key and value types."""
    source = """\
package main

type Cache struct {
    items map[Key]Value
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Key" in targets
    assert "Value" in targets


# ---------------------------------------------------------------------------
# GO-T6  slice element type
# ---------------------------------------------------------------------------


def test_go_slice_type_ref():
    """Slice field must emit type_ref for the element type."""
    source = """\
package main

type Queue struct {
    items []Item
}
"""
    _, refs = _parse(source)
    assert "Item" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# GO-T7  qualified type (pkg.Type)
# ---------------------------------------------------------------------------


def test_go_qualified_type_ref():
    """pkg.Type must emit a type_ref edge with the qualified name."""
    source = """\
package main

import "net/http"

func Serve(w http.ResponseWriter, r *http.Request) {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "http.ResponseWriter" in targets or "ResponseWriter" in targets, f"expected http.ResponseWriter in {targets}"


# ---------------------------------------------------------------------------
# GO-T8  var declaration type
# ---------------------------------------------------------------------------


def test_go_var_decl_type_ref():
    """Typed var declaration must emit a type_ref."""
    source = """\
package main

var handler Handler
"""
    _, refs = _parse(source)
    assert "Handler" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# GO-T9  type alias
# ---------------------------------------------------------------------------


def test_go_type_alias_type_ref():
    """Type alias (non-struct, non-interface) must emit type_ref to the aliased type."""
    source = """\
package main

type MyHandler = Handler
"""
    _, refs = _parse(source)
    assert "Handler" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# GO-T11 multiple return types
# ---------------------------------------------------------------------------


def test_go_multiple_return_type_refs():
    """Multiple return types must each emit a type_ref."""
    source = """\
package main

func Open() (*File, error) {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "File" in targets, f"File missing from {targets}"
    # error is a built-in — must NOT produce a type_ref
    assert "error" not in targets, "error is a built-in and must not produce type_ref"


# ---------------------------------------------------------------------------
# GO-T12 built-in types skipped
# ---------------------------------------------------------------------------


def test_go_builtin_types_not_emitted():
    """Built-in types must not produce type_ref edges."""
    source = """\
package main

func Calc(a int, b float64) string {
    return ""
}

type Flags struct {
    count  int
    name   string
    valid  bool
    data   []byte
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    builtins = {"int", "float64", "string", "bool", "byte", "error", "uint", "int64", "float32", "rune", "uint8"}
    overlap = targets & builtins
    assert not overlap, f"built-in types leaked into type_refs: {overlap}"


# ---------------------------------------------------------------------------
# GO-T13 generic type arguments
# ---------------------------------------------------------------------------


def test_go_generic_type_argument_refs():
    """Type arguments of a generic type must emit type_ref edges."""
    source = """\
package main

type Registry struct {
    store sync.Map[Key, Value]
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Key" in targets or "Value" in targets, f"expected generic type args in {targets}"


# ---------------------------------------------------------------------------
# GO-T14 channel type
# ---------------------------------------------------------------------------


def test_go_channel_type_ref():
    """Channel element type must emit a type_ref."""
    source = """\
package main

type Worker struct {
    jobs chan Job
}
"""
    _, refs = _parse(source)
    assert "Job" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# Non-regression: existing call refs still work alongside type_refs
# ---------------------------------------------------------------------------


def test_go_call_refs_unaffected():
    """Adding type_ref emission must not break call reference extraction."""
    source = """\
package main

func Run(cfg Config) {
    cfg.Start()
    doWork(cfg)
}
"""
    _, refs = _parse(source)
    call_targets = {r["target_name"] for r in refs if r.get("kind") == "call"}
    assert "Start" in call_targets, f"Start call missing from {call_targets}"
    assert "doWork" in call_targets, f"doWork call missing from {call_targets}"
    # type_ref also emitted
    assert "Config" in _type_ref_targets(refs)


# ---------------------------------------------------------------------------
# GO-T15  qualified type emits unqualified name (cross-package types resolve)
# ---------------------------------------------------------------------------


def test_go_qualified_type_emits_name_only():
    """pkg.Type must emit just the type name, not pkg.Type, to match symbol storage."""
    source = """\
package main

import "net/http"

func Serve(w http.ResponseWriter, r *http.Request) {}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "ResponseWriter" in targets, f"ResponseWriter missing from {targets}"
    assert "Request" in targets, f"Request missing from {targets}"
    # Fully-qualified form must NOT appear (symbols aren't stored that way)
    assert "http.ResponseWriter" not in targets
    assert "http.Request" not in targets


# ---------------------------------------------------------------------------
# GO-T16  generic type alias RHS resolved correctly
# ---------------------------------------------------------------------------


def test_go_generic_type_alias_rhs():
    """Generic type alias must emit type_ref to the RHS base type."""
    source = """\
package main

type Set[T comparable] = Map[T, Marker]
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Map" in targets or "Marker" in targets, f"expected Map or Marker from generic alias RHS in {targets}"


# ---------------------------------------------------------------------------
# GO-T17  interface method parameter types emit type_refs
# ---------------------------------------------------------------------------


def test_go_interface_method_type_refs():
    """Interface method parameter and return types must produce type_ref edges."""
    source = """\
package main

type Handler interface {
    Handle(req Request) Response
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Request" in targets, f"Request missing from interface method params in {targets}"
    assert "Response" in targets, f"Response missing from interface method return in {targets}"


# ---------------------------------------------------------------------------
# Non-regression: existing import refs still work alongside type_refs
# ---------------------------------------------------------------------------


def test_go_import_refs_unaffected():
    """Import references must still work alongside type_refs."""
    source = """\
package main

import "fmt"

func Greet(name string) {
    fmt.Println(name)
}
"""
    _, refs = _parse(source)
    import_targets = {r["target_name"] for r in refs if r.get("kind") == "import"}
    assert "fmt" in import_targets, f"fmt import missing from {import_targets}"
