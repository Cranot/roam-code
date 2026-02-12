"""Symbol and reference extraction from tree-sitter ASTs."""

from __future__ import annotations


def extract_symbols(tree, source: bytes, file_path: str, extractor) -> list[dict]:
    """Extract symbol definitions from a parsed AST.

    Uses a language-specific extractor that implements:
        extractor.extract_symbols(tree, source, file_path) -> list[dict]

    Each returned dict has:
        name, qualified_name, kind, signature, line_start, line_end,
        docstring, visibility, is_exported, parent_name
    """
    if extractor is None or tree is None:
        return []
    try:
        symbols = extractor.extract_symbols(tree, source, file_path)
    except Exception:
        return []

    # Ensure every symbol dict has all required keys with defaults
    normalised = []
    for sym in symbols:
        normalised.append({
            "name": sym.get("name", ""),
            "qualified_name": sym.get("qualified_name", sym.get("name", "")),
            "kind": sym.get("kind", "unknown"),
            "signature": sym.get("signature"),
            "line_start": sym.get("line_start"),
            "line_end": sym.get("line_end"),
            "docstring": sym.get("docstring"),
            "visibility": sym.get("visibility", "public"),
            "is_exported": sym.get("is_exported", True),
            "parent_name": sym.get("parent_name"),
            "default_value": sym.get("default_value"),
        })
    return normalised


def extract_references(tree, source: bytes, file_path: str, extractor) -> list[dict]:
    """Extract references (calls, imports) from a parsed AST.

    Uses a language-specific extractor that implements:
        extractor.extract_references(tree, source, file_path) -> list[dict]

    Each returned dict has:
        source_name, target_name, kind, line, import_path
    """
    if extractor is None or tree is None:
        return []
    try:
        refs = extractor.extract_references(tree, source, file_path)
    except Exception:
        return []

    normalised = []
    for ref in refs:
        normalised.append({
            "source_name": ref.get("source_name", ""),
            "target_name": ref.get("target_name", ""),
            "kind": ref.get("kind", "call"),
            "line": ref.get("line"),
            "import_path": ref.get("import_path"),
        })
    return normalised
