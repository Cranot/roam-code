"""Import and call resolution into graph edges."""

import os
import re


def resolve_references(
    references: list[dict],
    symbols_by_name: dict[str, list[dict]],
    files_by_path: dict[str, int],
) -> list[dict]:
    """Resolve references to concrete symbol edges.

    Args:
        references: List of reference dicts with source_name, target_name, kind, line.
        symbols_by_name: Mapping from symbol name -> list of symbol dicts
            (each with at least 'id', 'file_id', 'file_path', 'qualified_name').
        files_by_path: Mapping from file path -> file_id.

    Returns:
        List of edge dicts with source_id, target_id, kind, line.
    """
    # Build a lookup: qualified_name -> list of symbols (multiple files may define same qn)
    symbols_by_qualified: dict[str, list[dict]] = {}
    for name, sym_list in symbols_by_name.items():
        for sym in sym_list:
            qn = sym.get("qualified_name")
            if qn:
                symbols_by_qualified.setdefault(qn, []).append(sym)

    # Build import map: (source_file, imported_name) -> import_path
    import_map: dict[tuple[str, str], str] = {}
    for ref in references:
        if ref.get("kind") == "import" and ref.get("import_path"):
            key = (ref.get("source_file", ""), ref.get("target_name", ""))
            if key[0] and key[1]:
                import_map[key] = ref["import_path"]

    # Build fallback map: file_path -> sorted list of symbols for line-based lookup
    # Used when source_name is None/empty (top-level code, e.g. Vue <script setup>)
    _file_symbols: dict[str, list[dict]] = {}
    for sym_list in symbols_by_name.values():
        for sym in sym_list:
            fp = sym.get("file_path", "")
            if fp:
                _file_symbols.setdefault(fp, []).append(sym)
    # Sort each file's symbols by line_start for binary-search-style lookup
    for fp in _file_symbols:
        _file_symbols[fp].sort(key=lambda s: s.get("line_start") or 0)

    # Also index source symbols by name for finding the caller
    edges = []
    seen = set()

    # Pre-compute Salesforce canonical file preferences
    sf_file_priority = _build_sf_file_priority(symbols_by_name)

    for ref in references:
        source_name = ref.get("source_name", "")
        target_name = ref.get("target_name", "")
        kind = ref.get("kind", "call")
        line = ref.get("line")
        source_file = ref.get("source_file", "")

        if not target_name:
            continue

        # Find source symbol (the caller)
        source_sym = _best_match(source_name, source_file, symbols_by_name)
        if source_sym is None:
            # Fallback for top-level code (e.g. Vue <script setup>, Python module scope):
            # pick the closest symbol at or before the reference line
            source_sym = _closest_symbol(source_file, line, _file_symbols)
        if source_sym is None:
            continue

        # Extract parent context from source for same-file disambiguation
        # e.g. MyStruct::some_method -> parent = "MyStruct"
        source_parent = ""
        src_qn = source_sym.get("qualified_name", "")
        if "::" in src_qn:
            source_parent = src_qn.rsplit("::", 1)[0]
        elif "." in src_qn:
            source_parent = src_qn.rsplit(".", 1)[0]

        # Salesforce resolution: handle @salesforce/ imports and controller refs
        import_path = ref.get("import_path", "")
        target_sym = None
        if import_path and import_path.startswith("@salesforce/"):
            target_sym = _resolve_salesforce_import(
                import_path, symbols_by_name, symbols_by_qualified,
            )
        elif kind in ("controller", "soql", "metadata_ref", "component_ref"):
            target_sym = _resolve_salesforce_name(
                target_name, kind, symbols_by_name, sf_file_priority,
            )

        # Standard resolution (skip if Salesforce already resolved)
        if target_sym is None:
            # 1. Try qualified name exact match
            qn_matches = symbols_by_qualified.get(target_name, [])
            target_sym = qn_matches[0] if len(qn_matches) == 1 else None
            if len(qn_matches) > 1:
                # Multiple symbols share this qualified name — disambiguate
                target_sym = _best_match(
                    target_name, source_file, symbols_by_name,
                    ref_kind=kind, source_parent=source_parent, import_map=import_map,
                )
            # If the qualified match is in a different file, prefer a local
            # symbol (same-file, then same-directory for Go packages).
            if target_sym is not None and target_sym.get("file_path") != source_file:
                candidates = symbols_by_name.get(target_name, [])
                # Prefer same-file
                for cand in candidates:
                    if cand.get("file_path") == source_file:
                        target_sym = cand
                        break
                else:
                    # Prefer same-directory (Go packages, co-located modules)
                    source_dir = os.path.dirname(source_file) if source_file else ""
                    if source_dir and os.path.dirname(target_sym.get("file_path", "")) != source_dir:
                        for cand in candidates:
                            if os.path.dirname(cand.get("file_path", "")) == source_dir:
                                target_sym = cand
                                break
            # 2. Try by simple name with disambiguation
            if target_sym is None:
                target_sym = _best_match(
                    target_name, source_file, symbols_by_name,
                    ref_kind=kind, source_parent=source_parent, import_map=import_map,
                )

        if target_sym is None:
            continue

        source_id = source_sym["id"]
        target_id = target_sym["id"]

        if source_id == target_id:
            continue

        edge_key = (source_id, target_id, kind)
        if edge_key in seen:
            continue
        seen.add(edge_key)

        edges.append({
            "source_id": source_id,
            "target_id": target_id,
            "kind": kind,
            "line": line,
        })

    return edges


def _match_import_path(import_path: str, candidates: list[dict]) -> list[dict]:
    """Filter candidates whose file_path matches an import path string.

    Handles:
    - @/ alias → src/ (Vue convention)
    - ./ and ../ relative prefixes (stripped for suffix matching)
    - Barrel exports: import from '@/composables/kiniseis' matches
      'src/composables/kiniseis/types.ts'
    - File extension stripping on candidates
    """
    if not import_path:
        return []

    # Normalize import path: strip prefix, normalize separators
    normalized = import_path.replace("\\", "/")
    if normalized.startswith("@/"):
        normalized = "src/" + normalized[2:]
    elif normalized.startswith("./"):
        normalized = normalized[2:]
    elif normalized.startswith("../"):
        # Keep relative — will use endswith matching
        pass

    # Strip trailing extension from normalized path if present
    for ext in (".ts", ".js", ".vue", ".tsx", ".jsx", ".py"):
        if normalized.endswith(ext):
            normalized = normalized[: -len(ext)]
            break

    matched = []
    for cand in candidates:
        fp = cand.get("file_path", "").replace("\\", "/")
        # Strip file extension from candidate
        fp_no_ext = fp
        for ext in (".ts", ".js", ".vue", ".tsx", ".jsx", ".py"):
            if fp_no_ext.endswith(ext):
                fp_no_ext = fp_no_ext[: -len(ext)]
                break

        # Direct match: candidate path ends with normalized import path
        if fp_no_ext.endswith("/" + normalized) or fp_no_ext == normalized:
            matched.append(cand)
        # Barrel export: import path is a directory prefix of the candidate
        elif fp.startswith(normalized + "/") or ("/" + normalized + "/") in fp:
            matched.append(cand)

    return matched


def _best_match(
    name: str,
    source_file: str,
    symbols_by_name: dict,
    ref_kind: str = "",
    source_parent: str = "",
    import_map: dict[tuple[str, str], str] | None = None,
) -> dict | None:
    """Find the best matching symbol for a name, preferring locality."""
    candidates = symbols_by_name.get(name, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # For call references with an uppercase name, prefer class (constructor call pattern)
    if ref_kind == "call" and name and name[0].isupper():
        class_candidates = [c for c in candidates if c.get("kind") == "class"]
        if class_candidates:
            for sym in class_candidates:
                if sym.get("file_path") == source_file:
                    return sym
            source_dir = os.path.dirname(source_file) if source_file else ""
            for sym in class_candidates:
                if os.path.dirname(sym.get("file_path", "")) == source_dir:
                    return sym
            return class_candidates[0]

    # Prefer same file — with parent-aware tie-breaking for Rust/Go impl blocks
    same_file = [s for s in candidates if s.get("file_path") == source_file]
    if len(same_file) == 1:
        return same_file[0]
    if len(same_file) > 1:
        # If source has a parent (e.g. MyStruct::some_method calling new()),
        # prefer the candidate whose qualified_name starts with the same parent
        if source_parent:
            for s in same_file:
                qn = s.get("qualified_name", "")
                if qn.startswith(source_parent + "::") or qn.startswith(source_parent + "."):
                    return s
        return same_file[0]

    # Prefer same directory — with exported definitions over local bindings
    source_dir = os.path.dirname(source_file) if source_file else ""
    same_dir = [s for s in candidates if os.path.dirname(s.get("file_path", "")) == source_dir]
    if same_dir:
        # Prefer exported symbols (canonical definitions, not destructured imports)
        exported = [s for s in same_dir if s.get("is_exported")]
        if exported:
            return exported[0]
        return same_dir[0]

    # Import-aware resolution: use import path data to narrow candidates
    if import_map:
        imp_path = import_map.get((source_file, name))
        if imp_path:
            import_matched = _match_import_path(imp_path, candidates)
            if import_matched:
                # Prefer exported among import-matched candidates
                exported = [s for s in import_matched if s.get("is_exported")]
                if exported:
                    return exported[0]
                return import_matched[0]

    # Fall back: prefer exported symbols globally
    exported = [s for s in candidates if s.get("is_exported")]
    if exported:
        return exported[0]
    return candidates[0]


def _closest_symbol(
    source_file: str,
    ref_line: int | None,
    file_symbols: dict[str, list[dict]],
) -> dict | None:
    """Find the symbol that contains ref_line, or fall back to file-level source.

    Prefers the most-nested symbol whose line_start <= ref_line <= line_end.
    When no symbol contains the reference (module-scope code like watch callbacks),
    returns the first symbol in the file as a file-level source to avoid
    self-references from "closest before" matching a completed function.
    """
    syms = file_symbols.get(source_file)
    if not syms:
        return None
    if ref_line is None:
        return syms[0]

    # Prefer symbol that CONTAINS the reference line (most nested wins)
    containing = None
    for sym in syms:
        ls = sym.get("line_start") or 0
        le = sym.get("line_end") or 0
        if ls <= ref_line and le >= ref_line and le > 0:
            containing = sym  # last containing wins (most nested)
    if containing:
        return containing

    # No containing symbol — reference is at module scope.
    # Return first symbol in file as a "file-level" source.
    return syms[0]


def build_file_edges(
    symbol_edges: list[dict],
    symbols: dict[int, dict],
) -> list[dict]:
    """Aggregate symbol-level edges into file-level edges.

    Args:
        symbol_edges: List of edge dicts with source_id, target_id.
        symbols: Mapping from symbol_id -> symbol dict (with 'file_id').

    Returns:
        List of file edge dicts with source_file_id, target_file_id, kind, symbol_count.
    """
    file_edge_counts: dict[tuple[int, int], int] = {}

    for edge in symbol_edges:
        src_sym = symbols.get(edge["source_id"])
        tgt_sym = symbols.get(edge["target_id"])
        if src_sym is None or tgt_sym is None:
            continue

        src_fid = src_sym["file_id"]
        tgt_fid = tgt_sym["file_id"]
        if src_fid == tgt_fid:
            continue

        key = (src_fid, tgt_fid)
        file_edge_counts[key] = file_edge_counts.get(key, 0) + 1

    return [
        {
            "source_file_id": src,
            "target_file_id": tgt,
            "kind": "imports",
            "symbol_count": count,
        }
        for (src, tgt), count in file_edge_counts.items()
    ]


# ---------------------------------------------------------------------------
# Salesforce cross-language resolution
# ---------------------------------------------------------------------------

# File extension priority for Salesforce disambiguation
_SF_EXT_PRIORITY = {
    ".cls": 0,
    ".trigger": 1,
    ".cmp": 2,
    ".app": 2,
    ".page": 3,
    ".component": 3,
}


def _build_sf_file_priority(symbols_by_name: dict) -> dict[str, int]:
    """Pre-compute file priority scores for Salesforce disambiguation."""
    priority = {}
    for sym_list in symbols_by_name.values():
        for sym in sym_list:
            fp = sym.get("file_path", "")
            if fp not in priority:
                _, ext = os.path.splitext(fp)
                priority[fp] = _SF_EXT_PRIORITY.get(ext, 10)
    return priority


def _resolve_salesforce_import(
    import_path: str,
    symbols_by_name: dict,
    symbols_by_qualified: dict,
) -> dict | None:
    """Resolve @salesforce/* import paths to symbols.

    Handles:
    - @salesforce/apex/ClassName.methodName → find method in .cls file
    - @salesforce/schema/ObjectName.FieldName → match by qualified name
    - @salesforce/label/c.LabelName → match CustomLabels
    """
    parts = import_path.split("/")
    if len(parts) < 3:
        return None

    category = parts[1]  # apex, schema, label, messageChannel, etc.

    if category == "apex" and len(parts) >= 3:
        # @salesforce/apex/MyController.myMethod
        apex_ref = parts[2]
        if "." in apex_ref:
            class_name, method_name = apex_ref.rsplit(".", 1)
            # Try qualified name first: ClassName.methodName
            qn = f"{class_name}.{method_name}"
            candidates = symbols_by_qualified.get(qn, [])
            if candidates:
                # Prefer candidates from .cls files
                cls_cands = [c for c in candidates
                             if c.get("file_path", "").endswith(".cls")]
                return cls_cands[0] if cls_cands else candidates[0]
            # Try just the method name
            method_cands = symbols_by_name.get(method_name, [])
            for c in method_cands:
                if c.get("file_path", "").endswith(".cls"):
                    # Check if it belongs to the right class
                    cqn = c.get("qualified_name", "")
                    if cqn.startswith(class_name + "."):
                        return c
        else:
            # Just class name: @salesforce/apex/MyController
            candidates = symbols_by_name.get(apex_ref, [])
            cls_cands = [c for c in candidates
                         if c.get("file_path", "").endswith(".cls")
                         and c.get("kind") == "class"]
            if cls_cands:
                return cls_cands[0]

    elif category == "schema" and len(parts) >= 3:
        # @salesforce/schema/Account.Name
        schema_ref = parts[2]
        candidates = symbols_by_qualified.get(schema_ref, [])
        if candidates:
            return candidates[0]
        # Try simple name
        name = schema_ref.rsplit(".", 1)[-1] if "." in schema_ref else schema_ref
        candidates = symbols_by_name.get(name, [])
        if candidates:
            return candidates[0]

    elif category == "label" and len(parts) >= 3:
        # @salesforce/label/c.MyLabel
        label_ref = parts[2]
        # Strip namespace prefix (e.g. "c.MyLabel" → "MyLabel")
        label_name = label_ref.split(".")[-1] if "." in label_ref else label_ref
        candidates = symbols_by_name.get(label_name, [])
        if candidates:
            return candidates[0]

    return None


def _resolve_salesforce_name(
    target_name: str,
    kind: str,
    symbols_by_name: dict,
    sf_file_priority: dict,
) -> dict | None:
    """Resolve Salesforce controller/component/SOQL references by name.

    Prefers .cls files for controller refs, applies SF file priority ordering.
    """
    candidates = symbols_by_name.get(target_name, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # For controller refs, prefer class symbols in .cls files
    if kind == "controller":
        cls_classes = [c for c in candidates
                       if c.get("file_path", "").endswith(".cls")
                       and c.get("kind") == "class"]
        if cls_classes:
            return cls_classes[0]

    # Sort by file priority
    def priority_key(sym):
        fp = sym.get("file_path", "")
        return sf_file_priority.get(fp, 10)

    sorted_cands = sorted(candidates, key=priority_key)
    return sorted_cands[0]
