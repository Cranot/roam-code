"""Regression tests for Wave 6: JSX edges, TS namespaces, Python semantic kinds.

Covers:
  TS-W6-1   JSX self-closing component emits call edge
  TS-W6-2   JSX opening/closing component emits call edge
  TS-W6-3   native HTML element (lowercase) does NOT emit a call edge
  TS-W6-4   JSX attribute expression emits reference to identifier
  TS-W6-5   multiple JSX prop callbacks all emitted
  TS-W6-6   locally-defined JSX component is NOT dead (has call edge)
  TS-W6-7   JSX in .jsx file works
  TS-W6-8   TS namespace emits a namespace symbol
  TS-W6-9   members inside namespace have namespace-qualified names
  TS-W6-10  nested namespace types emit type_ref edges
  PY-W6-1   TypeVar assignment gets kind=\"typevar\"
  PY-W6-2   ParamSpec gets kind=\"typevar\"
  PY-W6-3   TypeVarTuple gets kind=\"typevar\"
  PY-W6-4   Protocol base class → kind=\"protocol\"
  PY-W6-5   typing.Protocol (attribute base) → kind=\"protocol\"
  PY-W6-6   @dataclass → kind=\"dataclass\"
  PY-W6-7   @dataclass subclass → kind=\"dataclass\"
  PY-W6-8   plain class is still kind=\"class\"
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
# TS-W6-1  JSX self-closing component emits call edge
# ---------------------------------------------------------------------------


def test_jsx_self_closing_component_call_edge():
    """<UserCard /> must emit a call edge to UserCard."""
    source = """\
function App() {
    return <UserCard />;
}
"""
    _, refs = _parse(source, "app.tsx")
    assert "UserCard" in _refs_of_kind(refs, "call"), "call edge to UserCard missing"


# ---------------------------------------------------------------------------
# TS-W6-2  JSX opening element emits call edge
# ---------------------------------------------------------------------------


def test_jsx_opening_element_call_edge():
    """<Modal>...</Modal> must emit a call edge to Modal."""
    source = """\
function App() {
    return (
        <Modal title="Hi">
            <span>content</span>
        </Modal>
    );
}
"""
    _, refs = _parse(source, "app.tsx")
    assert "Modal" in _refs_of_kind(refs, "call"), "call edge to Modal missing"


# ---------------------------------------------------------------------------
# TS-W6-3  native HTML element does NOT produce a call edge
# ---------------------------------------------------------------------------


def test_jsx_native_element_no_call_edge():
    """<div> and <span> must NOT produce call edges (they're DOM elements)."""
    source = """\
function App() {
    return <div><span>hello</span></div>;
}
"""
    _, refs = _parse(source, "app.tsx")
    call_targets = _refs_of_kind(refs, "call")
    assert "div" not in call_targets, "native 'div' should not be a call target"
    assert "span" not in call_targets, "native 'span' should not be a call target"


# ---------------------------------------------------------------------------
# TS-W6-4  JSX attribute expression emits reference
# ---------------------------------------------------------------------------


def test_jsx_attribute_expression_reference():
    """onClick={handleClick} must emit a reference to handleClick."""
    source = """\
function App() {
    return <Button onClick={handleClick} />;
}
"""
    _, refs = _parse(source, "app.tsx")
    assert "handleClick" in _refs_of_kind(refs, "reference"), "reference to handleClick missing"


# ---------------------------------------------------------------------------
# TS-W6-5  multiple JSX prop callbacks
# ---------------------------------------------------------------------------


def test_jsx_multiple_attribute_expression_references():
    """All identifier-valued JSX props must produce reference edges."""
    source = """\
function Dashboard() {
    return (
        <UserCard
            user={currentUser}
            onClick={handleClick}
            onHover={handleHover}
        />
    );
}
"""
    _, refs = _parse(source, "app.tsx")
    ref_targets = _refs_of_kind(refs, "reference")
    assert "currentUser" in ref_targets, "reference to currentUser missing"
    assert "handleClick" in ref_targets, "reference to handleClick missing"
    assert "handleHover" in ref_targets, "reference to handleHover missing"


# ---------------------------------------------------------------------------
# TS-W6-6  locally-defined JSX component is NOT dead
# ---------------------------------------------------------------------------


def test_jsx_locally_defined_component_has_call_edge():
    """UserCard defined and used as <UserCard /> in the same file must not appear dead."""
    source = """\
function UserCard({ user }) {
    return <div>{user.name}</div>;
}
function Dashboard() {
    return <UserCard user={currentUser} />;
}
"""
    syms, refs = _parse(source, "app.tsx")
    uc_sym = _sym_by_name(syms, "UserCard")
    assert uc_sym is not None, "UserCard symbol missing"
    uc_calls = [r for r in refs if r["target_name"] == "UserCard" and r["kind"] == "call"]
    assert uc_calls, "UserCard has no call edge — would appear as dead code"


# ---------------------------------------------------------------------------
# TS-W6-7  JSX in .jsx file
# ---------------------------------------------------------------------------


def test_jsx_in_js_file():
    """JSX component call edges must work in .jsx files too."""
    source = """\
function App() {
    return <MyWidget onPress={doPress} />;
}
"""
    _, refs = _parse(source, "app.jsx")
    assert "MyWidget" in _refs_of_kind(refs, "call"), "call edge missing in .jsx"
    assert "doPress" in _refs_of_kind(refs, "reference"), "reference to doPress missing in .jsx"


# ---------------------------------------------------------------------------
# TS-W6-8  TS namespace emits a namespace symbol
# ---------------------------------------------------------------------------


def test_ts_namespace_symbol_emitted():
    """namespace Foo {} must emit a symbol with kind='namespace'."""
    source = """\
namespace MyNS {
    export interface IFoo { x: number; }
}
"""
    syms, _ = _parse(source, "ns.ts")
    ns = _sym_by_name(syms, "MyNS")
    assert ns is not None, "MyNS namespace symbol missing"
    assert ns["kind"] == "namespace"


# ---------------------------------------------------------------------------
# TS-W6-9  namespace members get qualified names
# ---------------------------------------------------------------------------


def test_ts_namespace_members_qualified():
    """Members inside namespace Foo get qualified name Foo.Member."""
    source = """\
namespace MyNS {
    export interface IFoo { x: number; }
    export class Bar {}
    export function doThing(): void {}
}
"""
    syms, _ = _parse(source, "ns.ts")
    names = {s["qualified_name"] for s in syms}
    assert "MyNS.IFoo" in names, f"MyNS.IFoo not in qualified names: {names}"
    assert "MyNS.Bar" in names, f"MyNS.Bar not in qualified names: {names}"
    assert "MyNS.doThing" in names, f"MyNS.doThing not in qualified names: {names}"


# ---------------------------------------------------------------------------
# TS-W6-10  namespace type refs
# ---------------------------------------------------------------------------


def test_ts_namespace_type_refs():
    """implements/extends inside namespace still emit type_ref edges."""
    source = """\
namespace NS {
    export interface IBase { id: number; }
    export class Concrete implements IBase { id = 0; }
}
"""
    _, refs = _parse(source, "ns.ts")
    type_refs = _refs_of_kind(refs, "type_ref") | _refs_of_kind(refs, "implements")
    assert "IBase" in type_refs, f"IBase type_ref missing; got {type_refs}"


# ---------------------------------------------------------------------------
# PY-W6-1  TypeVar → kind="typevar"
# ---------------------------------------------------------------------------


def test_py_typevar_kind():
    """T = TypeVar('T') must emit kind='typevar'."""
    source = """\
from typing import TypeVar
T = TypeVar('T')
"""
    syms, _ = _parse(source, "types.py")
    sym = _sym_by_name(syms, "T")
    assert sym is not None, "T symbol missing"
    assert sym["kind"] == "typevar", f"expected typevar, got {sym['kind']}"


# ---------------------------------------------------------------------------
# PY-W6-2  ParamSpec → kind="typevar"
# ---------------------------------------------------------------------------


def test_py_paramspec_kind():
    """P = ParamSpec('P') must emit kind='typevar'."""
    source = """\
from typing import ParamSpec
P = ParamSpec('P')
"""
    syms, _ = _parse(source, "types.py")
    sym = _sym_by_name(syms, "P")
    assert sym is not None, "P symbol missing"
    assert sym["kind"] == "typevar"


# ---------------------------------------------------------------------------
# PY-W6-3  TypeVarTuple → kind="typevar"
# ---------------------------------------------------------------------------


def test_py_typevartuple_kind():
    """Ts = TypeVarTuple('Ts') must emit kind='typevar'."""
    source = """\
from typing import TypeVarTuple
Ts = TypeVarTuple('Ts')
"""
    syms, _ = _parse(source, "types.py")
    sym = _sym_by_name(syms, "Ts")
    assert sym is not None, "Ts symbol missing"
    assert sym["kind"] == "typevar"


# ---------------------------------------------------------------------------
# PY-W6-4  Protocol base → kind="protocol"
# ---------------------------------------------------------------------------


def test_py_protocol_class_kind():
    """class Foo(Protocol) must emit kind='protocol'."""
    source = """\
from typing import Protocol
class Drawable(Protocol):
    def draw(self) -> None: ...
"""
    syms, _ = _parse(source, "interfaces.py")
    sym = _sym_by_name(syms, "Drawable")
    assert sym is not None, "Drawable symbol missing"
    assert sym["kind"] == "protocol", f"expected protocol, got {sym['kind']}"


# ---------------------------------------------------------------------------
# PY-W6-5  typing.Protocol attribute base → kind="protocol"
# ---------------------------------------------------------------------------


def test_py_typing_protocol_attribute_base_kind():
    """class Foo(typing.Protocol) must emit kind='protocol'."""
    source = """\
import typing
class MyProto(typing.Protocol):
    def run(self) -> None: ...
"""
    syms, _ = _parse(source, "interfaces.py")
    sym = _sym_by_name(syms, "MyProto")
    assert sym is not None
    assert sym["kind"] == "protocol"


# ---------------------------------------------------------------------------
# PY-W6-6  @dataclass → kind="dataclass"
# ---------------------------------------------------------------------------


def test_py_dataclass_kind():
    """@dataclass class Point must emit kind='dataclass'."""
    source = """\
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float
"""
    syms, _ = _parse(source, "models.py")
    sym = _sym_by_name(syms, "Point")
    assert sym is not None, "Point symbol missing"
    assert sym["kind"] == "dataclass", f"expected dataclass, got {sym['kind']}"


# ---------------------------------------------------------------------------
# PY-W6-7  @dataclass subclass → kind="dataclass"
# ---------------------------------------------------------------------------


def test_py_dataclass_subclass_kind():
    """@dataclass applied to a subclass must still use kind='dataclass'."""
    source = """\
from dataclasses import dataclass

@dataclass
class Base:
    id: int

@dataclass
class Child(Base):
    name: str
"""
    syms, _ = _parse(source, "models.py")
    child = _sym_by_name(syms, "Child")
    assert child is not None
    assert child["kind"] == "dataclass"


# ---------------------------------------------------------------------------
# PY-W6-8  plain class unchanged
# ---------------------------------------------------------------------------


def test_py_plain_class_still_class():
    """A class without Protocol or @dataclass must remain kind='class'."""
    source = """\
class PlainService:
    def run(self) -> None: ...
"""
    syms, _ = _parse(source, "svc.py")
    sym = _sym_by_name(syms, "PlainService")
    assert sym is not None
    assert sym["kind"] == "class"
