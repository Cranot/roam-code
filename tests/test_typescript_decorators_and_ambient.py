"""TypeScript decorator ref edges (#15) and ambient declaration refs (#16).

TS-D1  bare @Decorator class     → call ref to decorator name
TS-D2  @Decorator() class        → call ref to decorator name (call expression)
TS-D3  @ns.Decorator class       → call ref to namespace name
TS-D4  method @Decorator         → call ref to decorator name
TS-D5  multiple decorators       → call refs to all
TS-A1  declare module 'name'     → import ref to module name
TS-A2  declare global {}         → no spurious refs
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "foo.ts"):
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


def _call_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "call"}


def _import_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "import"}


# ---------------------------------------------------------------------------
# TS-D1  bare @Decorator on a class
# ---------------------------------------------------------------------------


def test_ts_bare_class_decorator_emits_call_ref():
    """@Injectable class Foo → call ref to Injectable."""
    source = """\
@Injectable
class Foo {}
"""
    _, refs = _parse(source)
    assert "Injectable" in _call_targets(refs), f"Injectable missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# TS-D2  @Decorator() call expression on a class
# ---------------------------------------------------------------------------


def test_ts_call_class_decorator_emits_call_ref():
    """@Component({}) class Bar → call ref to Component."""
    source = """\
@Component({
  selector: 'app-root',
  template: '<div></div>',
})
class Bar {}
"""
    _, refs = _parse(source)
    assert "Component" in _call_targets(refs), f"Component missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# TS-D3  @ns.Decorator on a class
# ---------------------------------------------------------------------------


def test_ts_member_decorator_emits_call_ref():
    """@Guards.Auth class Foo → call ref to Guards."""
    source = """\
@Guards.Auth
class Foo {}
"""
    _, refs = _parse(source)
    assert "Guards" in _call_targets(refs), f"Guards missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# TS-D4  decorator on a method
# ---------------------------------------------------------------------------


def test_ts_method_decorator_emits_call_ref():
    """@Get('/') method → call ref to Get."""
    source = """\
class Controller {
  @Get('/')
  index() {}
}
"""
    _, refs = _parse(source)
    assert "Get" in _call_targets(refs), f"Get missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# TS-D5  multiple decorators
# ---------------------------------------------------------------------------


def test_ts_multiple_decorators_all_emitted():
    """Multiple decorators must each produce a call ref."""
    source = """\
@Injectable
@Singleton
class Service {}
"""
    _, refs = _parse(source)
    calls = _call_targets(refs)
    assert "Injectable" in calls, f"Injectable missing from {calls}"
    assert "Singleton" in calls, f"Singleton missing from {calls}"


# ---------------------------------------------------------------------------
# TS-A1  declare module 'name'
# ---------------------------------------------------------------------------


def test_ts_declare_module_emits_import_ref():
    """declare module 'express' → import ref to express."""
    source = """\
declare module 'express' {
  interface Request {}
}
"""
    _, refs = _parse(source)
    imports = _import_targets(refs)
    assert "express" in imports, f"express missing from {imports}"


# ---------------------------------------------------------------------------
# TS-A2  declare global should not produce spurious refs
# ---------------------------------------------------------------------------


def test_ts_declare_global_no_spurious_refs():
    """declare global {} → no import ref to 'global'."""
    source = """\
declare global {
  interface Window {
    myProp: string;
  }
}
"""
    _, refs = _parse(source)
    imports = _import_targets(refs)
    assert "global" not in imports, f"spurious 'global' import ref: {imports}"


# ---------------------------------------------------------------------------
# Non-regression: existing call refs still work
# ---------------------------------------------------------------------------


def test_ts_decorator_does_not_break_call_refs():
    """Adding decorator handling must not break existing call ref extraction."""
    source = """\
@Injectable
class Service {
  run() {
    doWork();
  }
}
"""
    _, refs = _parse(source)
    calls = _call_targets(refs)
    assert "doWork" in calls, f"doWork missing from {calls}"
    assert "Injectable" in calls, f"Injectable missing from {calls}"
