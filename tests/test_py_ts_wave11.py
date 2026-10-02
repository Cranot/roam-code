"""Wave 11: remaining TypeScript and Python type_ref gaps.

TypeScript gaps found and fixed:
  template_type node (substitutions inside template_literal_type) was not in
  _TS_TYPE_CONTEXT_NODES, so ``type EventName = `on${EventKind}``` missed EventKind.
  Fix: added template_type to _TS_TYPE_CONTEXT_NODES.

TypeScript candidates investigated but already working:
  conditional_type, type_predicate, asserts (via _walk_refs recursion → type_predicate),
  mapped_type value types (via index_signature in context nodes), abstract_method_signature
  (type annotations reached via _walk_refs recursion into formal_parameters/type_annotation).

Python gaps found and fixed:
  Annotated[MyType, MetadataMarker] — metadata arg was incorrectly emitted as type_ref
  because the generic_type handler recursed into all args. Fix: special-case Annotated
  to walk only the first arg (the type), skipping subsequent metadata args.

Python candidates investigated but already working or not applicable:
  TypeAlias / type_alias_statement — already handled in _extract_type_alias_refs.
  TypeVarTuple — no bound mechanism, no type_refs expected.
  ParamSpec — no bound mechanism (regression guard from wave 10).

Test cases:
  TS-W11-1   asserts x is MyType — MyType emitted as type_ref
  TS-W11-2   asserts x is UserModel — UserModel emitted as type_ref
  TS-W11-3   mapped_type complex value type (DerivedValue | null) emitted
  TS-W11-4   abstract_method_signature return type emitted
  TS-W11-5   abstract_method_signature parameter type emitted
  TS-W11-6   abstract_method_signature both param and return emitted
  TS-W11-7   template_literal_type substitution (EventKind) emitted (was failing)
  TS-W11-8   conditional_type branches regression guard
  TS-W11-9   type_predicate regression guard
  PY-W11-1   Annotated first arg emitted as type_ref
  PY-W11-2   Annotated metadata NOT emitted as type_ref (was failing)
  PY-W11-3   Annotated multiple metadata args NOT emitted (was failing)
  PY-W11-4   Python 3.12 type_alias_statement emits type_ref
  PY-W11-5   TypeVarTuple emits no spurious type_refs
  PY-W11-6   ParamSpec regression guard
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
# TS-W11-1  asserts_type_predicate — top-level function form
# ---------------------------------------------------------------------------


def test_ts_asserts_type_predicate_function():
    """function assert(x: unknown): asserts x is MyType — MyType must be a type_ref."""
    source = "function assert(x: unknown): asserts x is MyType { throw x; }\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyType" in type_refs, "type_ref to MyType (asserts_type_predicate) missing"


# ---------------------------------------------------------------------------
# TS-W11-2  asserts_type_predicate — arrow function / generic form
# ---------------------------------------------------------------------------


def test_ts_asserts_type_predicate_generic():
    """function assertIs<T>(x: unknown): asserts x is T — T must be a type_ref."""
    source = "function assertIs<T>(x: unknown): asserts x is UserModel { throw x; }\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "UserModel" in type_refs, "type_ref to UserModel (asserts_type_predicate generic) missing"


# ---------------------------------------------------------------------------
# TS-W11-3  mapped_type value type — non-string value type (regression extension)
# ---------------------------------------------------------------------------


def test_ts_mapped_type_complex_value():
    """type M = { [K in keyof BaseConfig]: DerivedValue | null } — DerivedValue emitted."""
    source = "type M = { [K in keyof BaseConfig]: DerivedValue | null };\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "BaseConfig" in type_refs, "type_ref to BaseConfig (keyof source) missing"
    assert "DerivedValue" in type_refs, "type_ref to DerivedValue (mapped value type) missing"


# ---------------------------------------------------------------------------
# TS-W11-4  abstract_method_signature return type emits type_ref
# ---------------------------------------------------------------------------


def test_ts_abstract_method_return_type():
    """abstract class Foo { abstract create(): MyResult; } — MyResult must be type_ref."""
    source = "abstract class Foo { abstract create(): MyResult; }\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyResult" in type_refs, "type_ref to MyResult (abstract method return) missing"


# ---------------------------------------------------------------------------
# TS-W11-5  abstract_method_signature parameter type emits type_ref
# ---------------------------------------------------------------------------


def test_ts_abstract_method_param_type():
    """abstract class Foo { abstract process(x: MyParam): void; } — MyParam must be type_ref."""
    source = "abstract class Foo { abstract process(x: MyParam): void; }\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyParam" in type_refs, "type_ref to MyParam (abstract method param) missing"


# ---------------------------------------------------------------------------
# TS-W11-6  abstract_method_signature — both param and return emitted
# ---------------------------------------------------------------------------


def test_ts_abstract_method_param_and_return():
    """abstract class Foo { abstract transform(x: InputType): OutputType; } — both emitted."""
    source = "abstract class Foo { abstract transform(x: InputType): OutputType; }\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "InputType" in type_refs, "type_ref to InputType (abstract method param) missing"
    assert "OutputType" in type_refs, "type_ref to OutputType (abstract method return) missing"


# ---------------------------------------------------------------------------
# TS-W11-7  template_literal_type substitution type (regression guard)
# ---------------------------------------------------------------------------


def test_ts_template_literal_type_substitution():
    """type EventName = `on${EventKind}` — EventKind must be a type_ref."""
    source = "type EventName = `on${EventKind}`;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "EventKind" in type_refs, "type_ref to EventKind (template_literal_type) missing"


# ---------------------------------------------------------------------------
# TS-W11-8  conditional_type branches (regression guard from wave 10)
# ---------------------------------------------------------------------------


def test_ts_regression_conditional_type():
    """Regression: conditional_type branches still emit type_refs after Wave 11."""
    source = "type C<T> = T extends BaseClass ? TrueResult : FalseResult;\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "BaseClass" in type_refs, "type_ref to BaseClass (conditional extends) missing"
    assert "TrueResult" in type_refs, "type_ref to TrueResult missing"
    assert "FalseResult" in type_refs, "type_ref to FalseResult missing"


# ---------------------------------------------------------------------------
# TS-W11-9  type_predicate (regression guard from wave 8)
# ---------------------------------------------------------------------------


def test_ts_regression_type_predicate():
    """Regression: type_predicate (x is MyType) still emits type_ref after Wave 11."""
    source = "function isModel(x: unknown): x is UserModel { return true; }\n"
    _, refs = _parse_ts(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "UserModel" in type_refs, "type_ref to UserModel (type_predicate) missing"


# ---------------------------------------------------------------------------
# PY-W11-1  Annotated — first arg (type) emits type_ref
# ---------------------------------------------------------------------------


def test_py_annotated_first_arg_emitted():
    """x: Annotated[MyType, some_metadata] — MyType must produce a type_ref."""
    source = "x: Annotated[MyType, some_metadata]\n"
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyType" in type_refs, "type_ref to MyType (Annotated first arg) missing"


# ---------------------------------------------------------------------------
# PY-W11-2  Annotated — metadata identifier NOT emitted as type_ref
# ---------------------------------------------------------------------------


def test_py_annotated_metadata_not_type_ref():
    """x: Annotated[MyType, MetadataMarker] — MetadataMarker must NOT be a type_ref."""
    source = "x: Annotated[MyType, MetadataMarker]\n"
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyType" in type_refs, "type_ref to MyType (Annotated first arg) missing"
    assert "MetadataMarker" not in type_refs, "'MetadataMarker' (Annotated metadata) must NOT appear as type_ref"


# ---------------------------------------------------------------------------
# PY-W11-3  Annotated — multiple metadata args, only type emitted
# ---------------------------------------------------------------------------


def test_py_annotated_multiple_metadata():
    """x: Annotated[MyModel, Field(...), Validator] — only MyModel emitted as type_ref."""
    source = "x: Annotated[MyModel, some_field, AnotherMeta]\n"
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyModel" in type_refs, "type_ref to MyModel (Annotated first arg) missing"
    assert "some_field" not in type_refs, "'some_field' metadata must NOT appear as type_ref"
    assert "AnotherMeta" not in type_refs, "'AnotherMeta' metadata must NOT appear as type_ref"


# ---------------------------------------------------------------------------
# PY-W11-4  Python 3.12 type alias (type_alias_statement) emits type_ref
# ---------------------------------------------------------------------------


def test_py_type_alias_statement():
    """type MyAlias = list[MyModel] — MyModel must produce a type_ref."""
    source = "type MyAlias = list[MyModel]\n"
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "MyModel" in type_refs, "type_ref to MyModel (type_alias_statement) missing"


# ---------------------------------------------------------------------------
# PY-W11-5  TypeVarTuple emits no spurious type_refs
# ---------------------------------------------------------------------------


def test_py_typevartuple_no_spurious_refs():
    """Ts = TypeVarTuple('Ts') — no spurious type_refs emitted."""
    source = 'Ts = TypeVarTuple("Ts")\n'
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "Ts" not in type_refs, "'Ts' (TypeVarTuple name) must not appear as type_ref"
    # TypeVarTuple itself is a call so it may appear as a call ref, not type_ref
    assert not type_refs, f"No type_refs expected for TypeVarTuple, got {type_refs}"


# ---------------------------------------------------------------------------
# PY-W11-6  ParamSpec with no extras (regression guard)
# ---------------------------------------------------------------------------


def test_py_paramspec_no_spurious_refs():
    """P = ParamSpec('P') — no spurious type_refs emitted (regression from wave 10)."""
    source = 'P = ParamSpec("P")\n'
    _, refs = _parse_py(source)
    type_refs = _refs_of_kind(refs, "type_ref")
    assert not type_refs, f"No type_refs expected for ParamSpec, got {type_refs}"
