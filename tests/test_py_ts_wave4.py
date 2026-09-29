"""Regression tests for Wave 4: JS/TS export re-export edges.

Covers:
  TS-W4-1  export { X } emits a reference edge to X
  TS-W4-2  export { X as Y } emits a reference edge to X (local name)
  TS-W4-3  export { X } from './module' emits an import edge to X
  TS-W4-4  export { X as Y } from './module' emits an import edge to X
  TS-W4-5  export * from './module' emits a wildcard import edge
  TS-W4-6  export function foo() {} still emits call-graph edges inside body
  TS-W4-7  export const x = bar() still emits a call edge to bar
  TS-W4-8  Same behaviour for .js files
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
# TS-W4-1  export { X }
# ---------------------------------------------------------------------------


def test_ts_local_named_export_emits_reference():
    """export { UserProfile } must emit a reference edge to UserProfile."""
    source = """\
interface UserProfile { name: string; }
export { UserProfile };
"""
    _, refs = _parse(source, "types.ts")
    ref_targets = _refs_of_kind(refs, "reference")
    assert "UserProfile" in ref_targets, f"reference to UserProfile missing; got {ref_targets}"


# ---------------------------------------------------------------------------
# TS-W4-2  export { X as Y }
# ---------------------------------------------------------------------------


def test_ts_aliased_local_export_emits_reference_to_local_name():
    """export { Widget as PublicWidget } must emit a reference to Widget (local name)."""
    source = """\
class Widget {}
export { Widget as PublicWidget };
"""
    _, refs = _parse(source, "index.ts")
    ref_targets = _refs_of_kind(refs, "reference")
    assert "Widget" in ref_targets, f"reference to Widget (local name) missing; got {ref_targets}"


# ---------------------------------------------------------------------------
# TS-W4-3  export { X } from './module'
# ---------------------------------------------------------------------------


def test_ts_reexport_named_emits_import_edge():
    """export { UserProfile } from './types' must emit an import edge."""
    source = """\
export { UserProfile } from './types';
"""
    _, refs = _parse(source, "index.ts")
    import_targets = _refs_of_kind(refs, "import")
    assert "UserProfile" in import_targets, f"import edge to UserProfile missing; got {import_targets}"


# ---------------------------------------------------------------------------
# TS-W4-4  export { X as Y } from './module'
# ---------------------------------------------------------------------------


def test_ts_reexport_aliased_emits_import_edge_for_local_name():
    """export { Widget as PublicWidget } from './widget' emits import edge for Widget."""
    source = """\
export { Widget as PublicWidget } from './widget';
"""
    _, refs = _parse(source, "index.ts")
    import_targets = _refs_of_kind(refs, "import")
    assert "Widget" in import_targets, f"import edge to Widget missing; got {import_targets}"


# ---------------------------------------------------------------------------
# TS-W4-5  export * from './module'
# ---------------------------------------------------------------------------


def test_ts_reexport_wildcard_emits_import_edge():
    """export * from './utils' must emit an import edge."""
    source = """\
export * from './utils';
"""
    _, refs = _parse(source, "index.ts")
    # Any import edge referencing the utils module
    import_refs = [r for r in refs if r.get("kind") == "import"]
    assert any("utils" in r.get("import_path", "") for r in import_refs), (
        f"wildcard re-export import edge missing; refs={import_refs}"
    )


# ---------------------------------------------------------------------------
# TS-W4-6  export function — call edges inside body still work
# ---------------------------------------------------------------------------


def test_ts_export_function_call_edges_not_broken():
    """export function foo() { bar() } must still emit a call edge to bar."""
    source = """\
export function process(data: UserData): void {
    validate(data);
    emit(data);
}
"""
    _, refs = _parse(source, "handler.ts")
    call_targets = _refs_of_kind(refs, "call")
    assert "validate" in call_targets, f"call edge to validate missing; got {call_targets}"
    assert "emit" in call_targets, f"call edge to emit missing; got {call_targets}"


# ---------------------------------------------------------------------------
# TS-W4-7  export const — call edges still work
# ---------------------------------------------------------------------------


def test_ts_export_const_call_edges_not_broken():
    """export const x = bar() must still emit a call edge to bar."""
    source = """\
export const handler = createHandler('events');
"""
    _, refs = _parse(source, "exports.ts")
    call_targets = _refs_of_kind(refs, "call")
    assert "createHandler" in call_targets, f"call edge to createHandler missing; got {call_targets}"


# ---------------------------------------------------------------------------
# TS-W4-8  Same for .js files
# ---------------------------------------------------------------------------


def test_js_reexport_named_emits_import_edge():
    """export { helper } from './utils' in a .js file must emit an import edge."""
    source = """\
export { helper } from './utils';
"""
    _, refs = _parse(source, "index.js")
    import_targets = _refs_of_kind(refs, "import")
    assert "helper" in import_targets, f"import edge to helper missing in .js; got {import_targets}"
