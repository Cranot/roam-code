"""Regression tests for Wave 7: abstract methods, as-expression type refs,
Python TypedDict kind, TS method/property signature fixes.

Covers:
  TS-W7-1   abstract method emits a method symbol
  TS-W7-2   abstract method signature prefix in sig
  TS-W7-3   abstract class still emits class symbol
  TS-W7-4   concrete methods in abstract class still extracted
  TS-W7-5   as-expression emits type_ref for direct type identifier
  TS-W7-6   as-expression emits type_ref for generic type
  TS-W7-7   as-expression value side keeps call edges
  TS-W7-8   chained as-expression emits type_ref for last type
  TS-W7-9   method return type no double colon
  TS-W7-10  property type no double colon
  PY-W7-1   TypedDict base → kind="typeddict"
  PY-W7-2   typing.TypedDict attribute base → kind="typeddict"
  PY-W7-3   TypedDict fields still extracted as properties
  PY-W7-4   Protocol still works alongside TypedDict
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


def _sym_by_name(symbols, name):
    return next((s for s in symbols if s["name"] == name), None)


# ---------------------------------------------------------------------------
# TS-W7-1  abstract method emits a method symbol
# ---------------------------------------------------------------------------


def test_ts_abstract_method_emitted():
    """abstract findById() must appear in the symbol list."""
    source = """\
abstract class BaseRepo {
    abstract findById(id: string): Promise<string>;
}
"""
    syms, _ = _parse(source, "repo.ts")
    sym = _sym_by_name(syms, "findById")
    assert sym is not None, "findById symbol missing"
    assert sym["kind"] == "method"
    assert sym["parent_name"] == "BaseRepo"


# ---------------------------------------------------------------------------
# TS-W7-2  abstract method signature has 'abstract' prefix
# ---------------------------------------------------------------------------


def test_ts_abstract_method_sig_prefix():
    """Abstract method signature must start with 'abstract'."""
    source = """\
abstract class Repo {
    abstract save(entity: string): Promise<void>;
}
"""
    syms, _ = _parse(source, "repo.ts")
    sym = _sym_by_name(syms, "save")
    assert sym is not None
    assert sym["signature"].startswith("abstract "), f"sig missing 'abstract': {sym['signature']}"


# ---------------------------------------------------------------------------
# TS-W7-3  abstract class symbol itself still emitted
# ---------------------------------------------------------------------------


def test_ts_abstract_class_symbol_emitted():
    """The abstract class itself must still emit a class symbol."""
    source = """\
abstract class Service {
    abstract run(): void;
}
"""
    syms, _ = _parse(source, "svc.ts")
    cls = _sym_by_name(syms, "Service")
    assert cls is not None, "Service class symbol missing"
    assert cls["kind"] == "class"


# ---------------------------------------------------------------------------
# TS-W7-4  concrete methods alongside abstract ones
# ---------------------------------------------------------------------------


def test_ts_abstract_class_concrete_method_preserved():
    """Concrete methods in an abstract class must be extracted normally."""
    source = """\
abstract class Base {
    abstract findById(id: string): string;
    async findAll(): Promise<string[]> { return []; }
}
"""
    syms, _ = _parse(source, "base.ts")
    assert _sym_by_name(syms, "findById") is not None, "abstract method missing"
    assert _sym_by_name(syms, "findAll") is not None, "concrete method missing"


# ---------------------------------------------------------------------------
# TS-W7-5  as-expression emits type_ref for bare type identifier
# ---------------------------------------------------------------------------


def test_ts_as_expression_bare_type_ref():
    """value as HTMLDivElement must emit type_ref to HTMLDivElement."""
    source = """\
const el = document.getElementById("root") as HTMLDivElement;
"""
    _, refs = _parse(source, "cast.ts")
    assert "HTMLDivElement" in _refs_of_kind(refs, "type_ref"), "type_ref to HTMLDivElement missing"


# ---------------------------------------------------------------------------
# TS-W7-6  as-expression with generic type
# ---------------------------------------------------------------------------


def test_ts_as_expression_generic_type_ref():
    """value as Array<UserType> must emit type_ref to UserType."""
    source = """\
const arr = value as Array<UserType>;
"""
    _, refs = _parse(source, "cast.ts")
    assert "UserType" in _refs_of_kind(refs, "type_ref"), "type_ref to UserType missing"


# ---------------------------------------------------------------------------
# TS-W7-7  as-expression value side keeps call edges
# ---------------------------------------------------------------------------


def test_ts_as_expression_preserves_call_edges():
    """fn() as SomeType must keep the call edge to fn."""
    source = """\
const result = fetchUser(id) as UserModel;
"""
    _, refs = _parse(source, "cast.ts")
    assert "fetchUser" in _refs_of_kind(refs, "call"), "call edge to fetchUser missing"
    assert "UserModel" in _refs_of_kind(refs, "type_ref"), "type_ref to UserModel missing"


# ---------------------------------------------------------------------------
# TS-W7-8  chained as expressions
# ---------------------------------------------------------------------------


def test_ts_as_expression_chained():
    """fn() as unknown as SomeType must emit type_ref to SomeType."""
    source = """\
const x = fn() as unknown as SomeType;
"""
    _, refs = _parse(source, "cast.ts")
    assert "SomeType" in _refs_of_kind(refs, "type_ref"), "type_ref to SomeType missing"


# ---------------------------------------------------------------------------
# TS-W7-9  method return type no double colon
# ---------------------------------------------------------------------------


def test_ts_method_return_type_no_double_colon():
    """Method signature must not contain a double colon before the return type."""
    source = """\
class Svc {
    getValue(): string { return ""; }
}
interface IRepo {
    findById(id: string): Promise<string>;
}
"""
    syms, _ = _parse(source, "svc.ts")
    gv = _sym_by_name(syms, "getValue")
    assert gv is not None
    assert ": :" not in gv["signature"], f"double colon in sig: {gv['signature']}"

    fb = _sym_by_name(syms, "findById")
    assert fb is not None
    assert ": :" not in fb["signature"], f"double colon in interface sig: {fb['signature']}"


# ---------------------------------------------------------------------------
# TS-W7-10  property type no double colon
# ---------------------------------------------------------------------------


def test_ts_property_type_no_double_colon():
    """Property signature must not contain a double colon before the type."""
    source = """\
class Point {
    x: number;
    y: number;
}
interface Foo {
    name: string;
}
"""
    syms, _ = _parse(source, "point.ts")
    x = _sym_by_name(syms, "x")
    assert x is not None
    assert ": :" not in x["signature"], f"double colon in property sig: {x['signature']}"

    name = _sym_by_name(syms, "name")
    assert name is not None
    assert ": :" not in name["signature"], f"double colon in interface property sig: {name['signature']}"


# ---------------------------------------------------------------------------
# PY-W7-1  TypedDict base → kind="typeddict"
# ---------------------------------------------------------------------------


def test_py_typeddict_kind():
    """class Foo(TypedDict) must emit kind='typeddict'."""
    source = """\
from typing import TypedDict
class UserDict(TypedDict):
    name: str
    age: int
"""
    syms, _ = _parse(source, "dicts.py")
    sym = _sym_by_name(syms, "UserDict")
    assert sym is not None
    assert sym["kind"] == "typeddict", f"expected typeddict, got {sym['kind']}"


# ---------------------------------------------------------------------------
# PY-W7-2  typing.TypedDict attribute base → kind="typeddict"
# ---------------------------------------------------------------------------


def test_py_typing_typeddict_attribute_kind():
    """class Foo(typing.TypedDict) must emit kind='typeddict'."""
    source = """\
import typing
class Config(typing.TypedDict, total=False):
    debug: bool
"""
    syms, _ = _parse(source, "dicts.py")
    sym = _sym_by_name(syms, "Config")
    assert sym is not None
    assert sym["kind"] == "typeddict"


# ---------------------------------------------------------------------------
# PY-W7-3  TypedDict fields still extracted as properties
# ---------------------------------------------------------------------------


def test_py_typeddict_fields_are_properties():
    """Fields of a TypedDict class must still be extracted as properties."""
    source = """\
from typing import TypedDict
class Point(TypedDict):
    x: float
    y: float
"""
    syms, _ = _parse(source, "dicts.py")
    x = _sym_by_name(syms, "x")
    y = _sym_by_name(syms, "y")
    assert x is not None, "x property missing"
    assert y is not None, "y property missing"
    assert x["kind"] == "property"
    assert x["parent_name"] == "Point"


# ---------------------------------------------------------------------------
# PY-W7-4  Protocol and TypedDict coexist correctly
# ---------------------------------------------------------------------------


def test_py_protocol_and_typeddict_coexist():
    """Protocol and TypedDict in same file must each get the right kind."""
    source = """\
from typing import Protocol, TypedDict

class Shape(Protocol):
    def area(self) -> float: ...

class BBox(TypedDict):
    x: float
    y: float

class Plain:
    pass
"""
    syms, _ = _parse(source, "types.py")
    shape = _sym_by_name(syms, "Shape")
    bbox = _sym_by_name(syms, "BBox")
    plain = _sym_by_name(syms, "Plain")
    assert shape is not None and shape["kind"] == "protocol"
    assert bbox is not None and bbox["kind"] == "typeddict"
    assert plain is not None and plain["kind"] == "class"
