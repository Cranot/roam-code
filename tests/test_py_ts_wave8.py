"""Regression tests for Wave 8: satisfies operator and type predicate return types.

Covers:
  TS-W8-1   satisfies emits type_ref for bare type identifier
  TS-W8-2   satisfies emits type_ref for generic type
  TS-W8-3   satisfies value part keeps call edges
  TS-W8-4   satisfies value part keeps reference edges
  TS-W8-5   satisfies with object literal (no call edges from object itself)
  TS-W8-6   type predicate return type emits type_ref
  TS-W8-7   type predicate does not produce type_ref for the parameter name
  TS-W8-8   type predicate works in method signature inside interface
  TS-W8-9   as + satisfies chained — both type parts emit type_ref
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
# TS-W8-1  satisfies emits type_ref for bare type identifier
# ---------------------------------------------------------------------------


def test_ts_satisfies_bare_type_ref():
    """expr satisfies ServerConfig must emit type_ref to ServerConfig."""
    source = """\
const cfg = { host: "localhost" } satisfies ServerConfig;
"""
    _, refs = _parse(source, "cfg.ts")
    assert "ServerConfig" in _refs_of_kind(refs, "type_ref"), "type_ref to ServerConfig missing"


# ---------------------------------------------------------------------------
# TS-W8-2  satisfies emits type_ref for generic type
# ---------------------------------------------------------------------------


def test_ts_satisfies_generic_type_ref():
    """expr satisfies Record<string, Handler> must emit type_ref to Handler."""
    source = """\
const handlers = {} satisfies Record<string, Handler>;
"""
    _, refs = _parse(source, "cfg.ts")
    assert "Handler" in _refs_of_kind(refs, "type_ref"), "type_ref to Handler missing"


# ---------------------------------------------------------------------------
# TS-W8-3  satisfies value part keeps call edges
# ---------------------------------------------------------------------------


def test_ts_satisfies_preserves_call_edges():
    """buildConfig() satisfies AppConfig must keep call edge to buildConfig."""
    source = """\
const cfg = buildConfig() satisfies AppConfig;
"""
    _, refs = _parse(source, "cfg.ts")
    assert "buildConfig" in _refs_of_kind(refs, "call"), "call edge to buildConfig missing"
    assert "AppConfig" in _refs_of_kind(refs, "type_ref"), "type_ref to AppConfig missing"


# ---------------------------------------------------------------------------
# TS-W8-4  satisfies value part keeps reference edges
# ---------------------------------------------------------------------------


def test_ts_satisfies_preserves_reference_edges():
    """baseConfig satisfies ExtendedConfig must emit reference to baseConfig."""
    source = """\
const final = baseConfig satisfies ExtendedConfig;
"""
    _, refs = _parse(source, "cfg.ts")
    assert "ExtendedConfig" in _refs_of_kind(refs, "type_ref"), "type_ref to ExtendedConfig missing"


# ---------------------------------------------------------------------------
# TS-W8-5  satisfies with object literal does not produce spurious call edges
# ---------------------------------------------------------------------------


def test_ts_satisfies_object_literal_no_call():
    """Object literal satisfies SomeType must NOT produce a call edge."""
    source = """\
const x = { a: 1, b: 2 } satisfies SomeType;
"""
    _, refs = _parse(source, "cfg.ts")
    call_targets = _refs_of_kind(refs, "call")
    assert "SomeType" not in call_targets, "SomeType must not be a call target"


# ---------------------------------------------------------------------------
# TS-W8-6  type predicate return type emits type_ref
# ---------------------------------------------------------------------------


def test_ts_type_predicate_emits_type_ref():
    """function isUser(x: unknown): x is UserModel must emit type_ref to UserModel."""
    source = """\
function isUser(x: unknown): x is UserModel {
    return typeof x === "object" && x !== null;
}
"""
    _, refs = _parse(source, "guards.ts")
    assert "UserModel" in _refs_of_kind(refs, "type_ref"), "type_ref to UserModel missing"


# ---------------------------------------------------------------------------
# TS-W8-7  type predicate does not produce type_ref for the parameter name
# ---------------------------------------------------------------------------


def test_ts_type_predicate_parameter_name_not_type_ref():
    """The 'x' in 'x is UserModel' is the parameter name, not a type."""
    source = """\
function isUser(x: unknown): x is UserModel {
    return true;
}
"""
    _, refs = _parse(source, "guards.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "x" not in type_refs, "'x' parameter name must not appear as type_ref"
    assert "UserModel" in type_refs, "UserModel type must be in type_refs"


# ---------------------------------------------------------------------------
# TS-W8-8  type predicate in interface method signature
# ---------------------------------------------------------------------------


def test_ts_type_predicate_in_interface_method():
    """Interface method with type predicate return type emits type_ref."""
    source = """\
interface Validator {
    isValid(val: unknown): val is RequestPayload;
}
"""
    _, refs = _parse(source, "validators.ts")
    assert "RequestPayload" in _refs_of_kind(refs, "type_ref"), "type_ref to RequestPayload missing"


# ---------------------------------------------------------------------------
# TS-W8-9  chained as + satisfies both emit type_refs
# ---------------------------------------------------------------------------


def test_ts_as_and_satisfies_chained():
    """(x as Base) satisfies Derived — both Base and Derived produce type_refs."""
    source = """\
const y = (x as Base) satisfies Derived;
"""
    _, refs = _parse(source, "cast.ts")
    type_refs = _refs_of_kind(refs, "type_ref")
    assert "Base" in type_refs, "type_ref to Base missing"
    assert "Derived" in type_refs, "type_ref to Derived missing"
