"""Regression tests for Wave 10: function/constructor type return types (TS)
and TypeVar bound/constraint type references (Python).

TypeScript cases:
  TS-W10-1   function_type return type emits type_ref
  TS-W10-2   function_type parameter type still emits type_ref
  TS-W10-3   function_type both parameter and return type emitted
  TS-W10-4   constructor_type return type emits type_ref
  TS-W10-5   constructor_type parameter and return type both emitted
  TS-W10-6   nested function_type (callback param) emits inner types
  TS-W10-7   function_type inside union_type emits all types
  TS-W10-8   pre-existing: conditional_type still works (regression guard)
  TS-W10-9   pre-existing: index_signature still works (regression guard)

Python cases:
  PY-W10-1   TypeVar with bound= emits type_ref for the bound type
  PY-W10-2   TypeVar with positional constraints emits type_refs
  PY-W10-3   TypeVar with no extras emits no spurious type_refs
  PY-W10-4   ParamSpec call emits no spurious type_refs
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse_ts(source_text: str):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES, detect_language
    from roam.languages.registry import get_extractor

    language = detect_language("test.ts")
    assert language is not None
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, "test.ts")
    references = extractor.extract_references(tree, source, "test.ts")
    return symbols, references


def _parse_py(source_text: str):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES, detect_language
    from roam.languages.registry import get_extractor

    language = detect_language("test.py")
    assert language is not None
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, "test.py")
    references = extractor.extract_references(tree, source, "test.py")
    return symbols, references


def _refs_of_kind(refs, kind):
    return {r["target_name"] for r in refs if r.get("kind") == kind}


# ---------------------------------------------------------------------------
# TS-W10-1  function_type return type emits type_ref
# ---------------------------------------------------------------------------


def test_ts_function_type_return_emitted():
    """type F = (x: string) => MyReturn — MyReturn must produce a type_ref."""
    source = "type F = (x: string) => MyReturn;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyReturn" in type_refs, "type_ref to MyReturn (function_type return) missing"
    assert "string" not in type_refs, "'string' must not appear as type_ref"


# ---------------------------------------------------------------------------
# TS-W10-2  function_type parameter type still emits type_ref
# ---------------------------------------------------------------------------


def test_ts_function_type_param_emitted():
    """type F = (x: MyArg) => void — MyArg (param type) must still produce a type_ref."""
    source = "type F = (x: MyArg) => void;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyArg" in type_refs, "type_ref to MyArg (function_type param) missing"


# ---------------------------------------------------------------------------
# TS-W10-3  function_type both parameter and return type emitted
# ---------------------------------------------------------------------------


def test_ts_function_type_param_and_return():
    """type F = (x: MyArg) => MyReturn — both MyArg and MyReturn emitted."""
    source = "type F = (x: MyArg) => MyReturn;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyArg" in type_refs, "type_ref to MyArg missing"
    assert "MyReturn" in type_refs, "type_ref to MyReturn missing"


# ---------------------------------------------------------------------------
# TS-W10-4  constructor_type return type emits type_ref
# ---------------------------------------------------------------------------


def test_ts_constructor_type_return_emitted():
    """type C = new (x: string) => MyInstance — MyInstance must produce a type_ref."""
    source = "type C = new (x: string) => MyInstance;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyInstance" in type_refs, "type_ref to MyInstance (constructor_type return) missing"


# ---------------------------------------------------------------------------
# TS-W10-5  constructor_type parameter and return type both emitted
# ---------------------------------------------------------------------------


def test_ts_constructor_type_param_and_return():
    """type C = new (x: MyArg) => MyInstance — both emitted."""
    source = "type C = new (x: MyArg) => MyInstance;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyArg" in type_refs, "type_ref to MyArg (constructor_type param) missing"
    assert "MyInstance" in type_refs, "type_ref to MyInstance (constructor_type return) missing"


# ---------------------------------------------------------------------------
# TS-W10-6  nested function_type (callback param) emits inner types
# ---------------------------------------------------------------------------


def test_ts_nested_function_type():
    """type F = (cb: (x: Inner) => OuterRet) => void — Inner and OuterRet emitted."""
    source = "type F = (cb: (x: Inner) => OuterRet) => void;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "Inner" in type_refs, "type_ref to Inner (nested callback param) missing"
    assert "OuterRet" in type_refs, "type_ref to OuterRet (nested callback return) missing"


# ---------------------------------------------------------------------------
# TS-W10-7  function_type inside union_type emits all types
# ---------------------------------------------------------------------------


def test_ts_function_type_in_union():
    """type U = MyType | ((x: ArgType) => RetType) — all three emitted."""
    source = "type U = MyType | ((x: ArgType) => RetType);\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyType" in type_refs, "type_ref to MyType (union member) missing"
    assert "ArgType" in type_refs, "type_ref to ArgType (function_type param in union) missing"
    assert "RetType" in type_refs, "type_ref to RetType (function_type return in union) missing"


# ---------------------------------------------------------------------------
# TS-W10-8  pre-existing: conditional_type still works (regression guard)
# ---------------------------------------------------------------------------


def test_ts_regression_conditional_type():
    """Regression: conditional_type branches still emit type_refs after Wave 10."""
    source = "type C<T> = T extends BaseClass ? TrueResult : FalseResult;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "BaseClass" in type_refs, "type_ref to BaseClass (conditional extends) missing"
    assert "TrueResult" in type_refs, "type_ref to TrueResult (conditional true branch) missing"
    assert "FalseResult" in type_refs, "type_ref to FalseResult (conditional false branch) missing"


# ---------------------------------------------------------------------------
# TS-W10-9  pre-existing: index_signature still works (regression guard)
# ---------------------------------------------------------------------------


def test_ts_regression_index_signature():
    """Regression: index_signature value type still emits type_ref after Wave 10."""
    source = "type Rec = { [key: string]: MyValue };\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyValue" in type_refs, "type_ref to MyValue (index_signature value) missing"
    assert "string" not in type_refs, "'string' builtin must not appear as type_ref"


# ---------------------------------------------------------------------------
# PY-W10-1  TypeVar with bound= emits type_ref for the bound type
# ---------------------------------------------------------------------------


def test_py_typevar_bound_emitted():
    """T = TypeVar('T', bound=MyBase) — MyBase must produce a type_ref."""
    source = 'T = TypeVar("T", bound=MyBase)\n'
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyBase" in type_refs, "type_ref to MyBase (TypeVar bound) missing"


# ---------------------------------------------------------------------------
# PY-W10-2  TypeVar with positional constraints emits type_refs
# ---------------------------------------------------------------------------


def test_py_typevar_constraints_emitted():
    """T = TypeVar('T', MyType, OtherType) — both constraint types emitted."""
    source = 'T = TypeVar("T", MyType, OtherType)\n'
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyType" in type_refs, "type_ref to MyType (TypeVar constraint) missing"
    assert "OtherType" in type_refs, "type_ref to OtherType (TypeVar constraint) missing"


# ---------------------------------------------------------------------------
# PY-W10-3  TypeVar with no extras emits no spurious type_refs
# ---------------------------------------------------------------------------


def test_py_typevar_no_extras_no_spurious_refs():
    """T = TypeVar('T') — no spurious type_refs emitted."""
    source = 'T = TypeVar("T")\n'
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "T" not in type_refs, "'T' (TypeVar name string) must not appear as type_ref"


# ---------------------------------------------------------------------------
# PY-W10-4  ParamSpec call emits no spurious type_refs
# ---------------------------------------------------------------------------


def test_py_paramspec_no_spurious_refs():
    """P = ParamSpec('P') — no spurious type_refs emitted."""
    source = 'P = ParamSpec("P")\n'
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert not type_refs, f"No type_refs expected for ParamSpec, got {type_refs}"
