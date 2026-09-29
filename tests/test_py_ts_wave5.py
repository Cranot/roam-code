"""Regression tests for Wave 5: TypeScript constructor parameter properties.

Covers:
  TS-W5-1  private parameter property emits property symbol
  TS-W5-2  public parameter property emits property symbol
  TS-W5-3  protected parameter property emits property symbol
  TS-W5-4  readonly does not affect property extraction
  TS-W5-5  plain parameter (no modifier) is NOT extracted as property
  TS-W5-6  visibility matches the modifier
  TS-W5-7  optional parameter property emitted
  TS-W5-8  multiple params — all access-modified ones extracted
  TS-W5-9  constructor symbol itself still emitted
  TS-W5-10 type_ref edges emitted for parameter type annotations
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "service.ts"):
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


def _sym_by_name(symbols, name):
    return next((s for s in symbols if s["name"] == name), None)


# ---------------------------------------------------------------------------
# TS-W5-1  private parameter property
# ---------------------------------------------------------------------------


def test_ts_constructor_private_param_property():
    """private param in constructor must emit a property symbol."""
    source = """\
class UserService {
    constructor(private db: Database) {}
}
"""
    symbols, _ = _parse(source)
    sym = _sym_by_name(symbols, "db")
    assert sym is not None, "db property symbol missing"
    assert sym["kind"] == "property"
    assert sym["parent_name"] == "UserService"


# ---------------------------------------------------------------------------
# TS-W5-2  public parameter property
# ---------------------------------------------------------------------------


def test_ts_constructor_public_param_property():
    """public param in constructor must emit a property symbol."""
    source = """\
class Point {
    constructor(public x: number, public y: number) {}
}
"""
    symbols, _ = _parse(source)
    assert _sym_by_name(symbols, "x") is not None, "x property missing"
    assert _sym_by_name(symbols, "y") is not None, "y property missing"


# ---------------------------------------------------------------------------
# TS-W5-3  protected parameter property
# ---------------------------------------------------------------------------


def test_ts_constructor_protected_param_property():
    """protected param must emit a property symbol."""
    source = """\
class Base {
    constructor(protected config: Config) {}
}
"""
    symbols, _ = _parse(source)
    sym = _sym_by_name(symbols, "config")
    assert sym is not None, "config property missing"
    assert sym["kind"] == "property"


# ---------------------------------------------------------------------------
# TS-W5-4  readonly does not block extraction
# ---------------------------------------------------------------------------


def test_ts_constructor_readonly_param_property():
    """private readonly must still extract the property."""
    source = """\
class Logger {
    constructor(private readonly level: string) {}
}
"""
    symbols, _ = _parse(source)
    sym = _sym_by_name(symbols, "level")
    assert sym is not None, "level property (private readonly) missing"
    assert sym["kind"] == "property"


# ---------------------------------------------------------------------------
# TS-W5-5  plain parameter not extracted
# ---------------------------------------------------------------------------


def test_ts_constructor_plain_param_not_extracted():
    """A constructor param without an access modifier must NOT become a property."""
    source = """\
class Thing {
    constructor(private owned: Owned, plain: number) {}
}
"""
    symbols, _ = _parse(source)
    plain_sym = _sym_by_name(symbols, "plain")
    assert plain_sym is None, "plain (no modifier) should not be a property symbol"


# ---------------------------------------------------------------------------
# TS-W5-6  visibility matches the modifier
# ---------------------------------------------------------------------------


def test_ts_constructor_param_property_visibility():
    """Visibility on the emitted property must match the access modifier."""
    source = """\
class Svc {
    constructor(
        private a: string,
        public b: string,
        protected c: string
    ) {}
}
"""
    symbols, _ = _parse(source)
    assert _sym_by_name(symbols, "a")["visibility"] == "private"
    assert _sym_by_name(symbols, "b")["visibility"] == "public"
    assert _sym_by_name(symbols, "c")["visibility"] == "protected"


# ---------------------------------------------------------------------------
# TS-W5-7  optional parameter property
# ---------------------------------------------------------------------------


def test_ts_constructor_optional_param_property():
    """Optional parameter property (private name?: string) must be extracted."""
    source = """\
class Widget {
    constructor(private label?: string) {}
}
"""
    symbols, _ = _parse(source)
    sym = _sym_by_name(symbols, "label")
    assert sym is not None, "optional param property 'label' missing"
    assert sym["kind"] == "property"


# ---------------------------------------------------------------------------
# TS-W5-8  multiple mixed params
# ---------------------------------------------------------------------------


def test_ts_constructor_multiple_params_all_modifiers_extracted():
    """All access-modified params must be extracted; unmodified ones must not."""
    source = """\
class Repo {
    constructor(
        private readonly db: Db,
        public logger: Logger,
        rawConn: Connection
    ) {}
}
"""
    symbols, _ = _parse(source)
    assert _sym_by_name(symbols, "db") is not None, "db missing"
    assert _sym_by_name(symbols, "logger") is not None, "logger missing"
    assert _sym_by_name(symbols, "rawConn") is None, "rawConn should not be extracted"


# ---------------------------------------------------------------------------
# TS-W5-9  constructor symbol still emitted
# ---------------------------------------------------------------------------


def test_ts_constructor_symbol_still_present():
    """The constructor method symbol must still be emitted alongside the properties."""
    source = """\
class Svc {
    constructor(private db: Db) {}
}
"""
    symbols, _ = _parse(source)
    ctor = _sym_by_name(symbols, "constructor")
    assert ctor is not None, "constructor method symbol missing"
    assert ctor["kind"] == "constructor"


# ---------------------------------------------------------------------------
# TS-W5-10  type_ref edges for param type annotations
# ---------------------------------------------------------------------------


def test_ts_constructor_param_type_annotation_emits_type_ref():
    """Type annotations on constructor params must emit type_ref edges."""
    source = """\
class UserService {
    constructor(
        private db: UserRepository,
        private email: EmailService
    ) {}
}
"""
    _, refs = _parse(source)
    type_refs = {r["target_name"] for r in refs if r.get("kind") == "type_ref"}
    assert "UserRepository" in type_refs, f"type_ref to UserRepository missing; got {type_refs}"
    assert "EmailService" in type_refs, f"type_ref to EmailService missing; got {type_refs}"
