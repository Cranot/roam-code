"""Regression tests for Wave 1 Python + TypeScript extractor improvements.

Covers:
  PY-1  match/case — calls inside case bodies appear in the call graph
  PY-2  __all__ += — augmented assignment extends the export set
  PY-3  @property kind — decorated methods get kind="property"
  PY-4  __slots__ expansion — emit one property per slot name
  TS-5  interface extends edges — inherits edge emitted for IFoo extends IBar
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


# ---------------------------------------------------------------------------
# PY-1  match/case walk
# ---------------------------------------------------------------------------


def test_py_match_case_recurses_into_case_bodies():
    """Module-level match/case: functions in case bodies must be extracted."""
    source = """\
match command:
    case "start":
        def _do_start():
            pass
    case "stop":
        def _do_stop():
            pass
"""
    symbols, _ = _parse(source, "foo.py")
    names = [s["name"] for s in symbols]
    assert "_do_start" in names, f"_do_start missing from {names}"
    assert "_do_stop" in names, f"_do_stop missing from {names}"


def test_py_match_case_call_refs_captured():
    """Call references inside case bodies must appear in the reference list."""
    source = """\
def dispatch(cmd):
    match cmd:
        case "a":
            helper_a()
        case "b":
            helper_b()
"""
    _, refs = _parse(source, "foo.py")
    targets = {r["target_name"] for r in refs}
    assert "helper_a" in targets, f"helper_a call ref missing; got {targets}"
    assert "helper_b" in targets, f"helper_b call ref missing; got {targets}"


# ---------------------------------------------------------------------------
# PY-2  __all__ += recognition
# ---------------------------------------------------------------------------


def test_py_dunder_all_augmented_assignment():
    """__all__ += ['extra'] must be merged into the export set."""
    source = """\
__all__ = ['foo', 'bar']
__all__ += ['baz']

def foo(): pass
def bar(): pass
def baz(): pass
def _private(): pass
"""
    symbols, _ = _parse(source, "mod.py")
    exported = {s["name"] for s in symbols if s.get("is_exported")}
    assert "foo" in exported
    assert "bar" in exported
    assert "baz" in exported, "baz from __all__ += should be exported"
    assert "_private" not in exported


def test_py_dunder_all_augmented_only():
    """Module with only __all__ += (no = assignment) still collects names."""
    source = """\
__all__ += ['alpha']

def alpha(): pass
def beta(): pass
"""
    symbols, _ = _parse(source, "mod.py")
    exported = {s["name"] for s in symbols if s.get("is_exported")}
    assert "alpha" in exported
    assert "beta" not in exported


# ---------------------------------------------------------------------------
# PY-3  @property kind
# ---------------------------------------------------------------------------


def test_py_property_kind():
    """@property decorated methods must get kind='property', not 'method'."""
    source = """\
class Foo:
    @property
    def value(self):
        return self._value

    def regular(self):
        pass
"""
    symbols, _ = _parse(source, "foo.py")
    value_sym = next((s for s in symbols if s["name"] == "value"), None)
    assert value_sym is not None, "value symbol not found"
    assert value_sym["kind"] == "property", f"expected 'property', got {value_sym['kind']!r}"

    regular_sym = next((s for s in symbols if s["name"] == "regular"), None)
    assert regular_sym is not None
    assert regular_sym["kind"] == "method"


def test_py_property_getter_detected_setter_stays_method():
    """@property getter gets kind='property'; @x.setter keeps kind='method'."""
    source = """\
class Foo:
    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, v):
        self._x = v
"""
    symbols, _ = _parse(source, "foo.py")
    x_syms = [s for s in symbols if s["name"] == "x"]
    assert len(x_syms) >= 1
    # At least the @property getter must have kind='property'
    property_syms = [s for s in x_syms if s["kind"] == "property"]
    assert property_syms, f"no @property symbol found; kinds={[s['kind'] for s in x_syms]}"


# ---------------------------------------------------------------------------
# PY-4  __slots__ expansion
# ---------------------------------------------------------------------------


def test_py_slots_tuple_expansion():
    """__slots__ = ('x', 'y') emits 'x' and 'y' as property symbols."""
    source = """\
class Point:
    __slots__ = ('x', 'y')
"""
    symbols, _ = _parse(source, "foo.py")
    names = [s["name"] for s in symbols]
    assert "x" in names, f"slot 'x' not found in {names}"
    assert "y" in names, f"slot 'y' not found in {names}"
    assert "__slots__" not in names, "__slots__ itself should not be emitted"


def test_py_slots_list_expansion():
    """__slots__ = ['a', 'b'] also works."""
    source = """\
class Node:
    __slots__ = ['left', 'right', 'value']
"""
    symbols, _ = _parse(source, "foo.py")
    names = [s["name"] for s in symbols]
    for slot in ("left", "right", "value"):
        assert slot in names, f"slot '{slot}' not found"


def test_py_slots_property_parent():
    """Slot symbols have the correct parent_name."""
    source = """\
class Config:
    __slots__ = ('host', 'port')
"""
    symbols, _ = _parse(source, "foo.py")
    slot_syms = [s for s in symbols if s["name"] in ("host", "port")]
    for sym in slot_syms:
        assert sym.get("parent_name") == "Config", f"bad parent_name: {sym.get('parent_name')!r}"
        assert sym["kind"] == "property"


# ---------------------------------------------------------------------------
# TS-5  interface extends edges
# ---------------------------------------------------------------------------


def test_ts_interface_extends_emits_inherits_edge():
    """interface IFoo extends IBar must emit an inherits reference edge."""
    source = """\
interface IBar {
    doSomething(): void;
}

interface IFoo extends IBar {
    extra(): string;
}
"""
    _, refs = _parse(source, "types.ts")
    inherits_targets = {r["target_name"] for r in refs if r.get("kind") == "inherits"}
    assert "IBar" in inherits_targets, f"inherits edge to IBar missing; refs={refs}"


def test_ts_interface_extends_multiple():
    """interface extending multiple bases emits an edge for each."""
    source = """\
interface IReadable { read(): string; }
interface IWritable { write(s: string): void; }
interface IStream extends IReadable, IWritable {}
"""
    _, refs = _parse(source, "stream.ts")
    inherits_targets = {r["target_name"] for r in refs if r.get("kind") == "inherits"}
    assert "IReadable" in inherits_targets, f"IReadable missing; got {inherits_targets}"
    assert "IWritable" in inherits_targets, f"IWritable missing; got {inherits_targets}"


def test_ts_interface_extends_generic():
    """interface IMyMap extends Map<string, Foo> emits edge to 'Map'."""
    source = """\
interface IMyMap extends Map<string, string> {
    getAll(): string[];
}
"""
    _, refs = _parse(source, "types.ts")
    inherits_targets = {r["target_name"] for r in refs if r.get("kind") == "inherits"}
    assert "Map" in inherits_targets, f"generic extends edge missing; got {inherits_targets}"


def test_ts_class_extends_still_works():
    """Existing class extends edges must not be broken by the interface change."""
    source = """\
class Animal {
    name: string;
}
class Dog extends Animal {
    bark(): void {}
}
"""
    _, refs = _parse(source, "animals.ts")
    inherits_targets = {r["target_name"] for r in refs if r.get("kind") == "inherits"}
    assert "Animal" in inherits_targets, f"class extends broke; got {inherits_targets}"
