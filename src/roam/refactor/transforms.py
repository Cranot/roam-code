"""File-level refactoring transforms: move, rename, add-call, extract."""

from __future__ import annotations

import os

from roam.commands.resolve import find_symbol
from roam.refactor.codegen import detect_language, generate_import


def _read_file(path: str) -> list[str]:
    """Read a file and return its lines (with newlines stripped)."""
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n\r") for line in f.readlines()]


def _write_file(path: str, lines: list[str]) -> None:
    """Write lines to a file, creating parent directories if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _find_callers(conn, symbol_id: int) -> list[dict]:
    """Find all symbols that call or import the given symbol."""
    rows = conn.execute(
        "SELECT DISTINCT s.*, f.path as file_path, e.kind as edge_kind "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id = ?",
        (symbol_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _find_files_referencing(conn, symbol_id: int) -> list[str]:
    """Find all unique file paths that reference a symbol."""
    rows = conn.execute(
        "SELECT DISTINCT f.path "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id = ?",
        (symbol_id,),
    ).fetchall()
    return [r["path"] for r in rows]


def _find_import_line(lines: list[str], symbol_name: str) -> int | None:
    """Find the line index of an import that references symbol_name.

    Returns the 0-based line index, or None if not found.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Python: from X import Y  or  import X
        if ("import " in stripped) and (symbol_name in stripped):
            return i
    return None


def _rewrite_import(line: str, old_module: str, new_module: str) -> str:
    """Rewrite an import line to point to a new module."""
    return line.replace(old_module, new_module)


def _module_name_from_path(path: str) -> str:
    """Extract a simple module name from a file path (no extension)."""
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name


def move_symbol(conn, symbol_name: str, target_file: str,
                dry_run: bool = True) -> dict:
    """Move a symbol to a different file.

    1. Resolve symbol via find_symbol
    2. Read source file, extract symbol text (line_start to line_end)
    3. Find all callers/importers from edges table
    4. For each caller file: rewrite import to point to new location
    5. Generate import statements for the target file
    6. If dry_run: return the planned changes without writing
    7. Return change plan

    Parameters
    ----------
    conn:
        SQLite connection to the roam index.
    symbol_name:
        Name of the symbol to move.
    target_file:
        Destination file path.
    dry_run:
        If True, return planned changes without writing files.
    """
    sym = find_symbol(conn, symbol_name)
    if not sym:
        return {
            "operation": "move",
            "symbol": symbol_name,
            "error": f"symbol not found: {symbol_name}",
            "files_modified": [],
            "warnings": [f"symbol not found: {symbol_name}"],
        }

    source_file = sym["file_path"]
    line_start = sym["line_start"] or 1
    line_end = sym["line_end"] or line_start
    sym_actual_name = sym["name"]

    source_lines = _read_file(source_file)
    if not source_lines:
        return {
            "operation": "move",
            "symbol": sym_actual_name,
            "from_file": source_file,
            "to_file": target_file,
            "error": f"could not read source file: {source_file}",
            "files_modified": [],
            "warnings": [f"could not read source file: {source_file}"],
        }

    # Extract symbol lines (1-based to 0-based)
    start_idx = max(0, line_start - 1)
    end_idx = min(len(source_lines), line_end)
    symbol_lines = source_lines[start_idx:end_idx]
    symbol_line_count = len(symbol_lines)

    language = detect_language(source_file)
    old_module = _module_name_from_path(source_file)
    new_module = _module_name_from_path(target_file)

    files_modified = []
    warnings = []

    # 1. Target file: create/append with the symbol
    target_lines = _read_file(target_file)
    target_changes = []

    # Generate import for the symbol's own dependencies (simplified:
    # just add the symbol text to the target)
    insert_at = len(target_lines) + 1
    if target_lines:
        # Add blank line separator
        target_changes.append({
            "type": "insert",
            "line": insert_at,
            "text": "",
        })
        insert_at += 1

    for i, sl in enumerate(symbol_lines):
        target_changes.append({
            "type": "insert",
            "line": insert_at + i,
            "text": sl,
        })

    file_action = "CREATE" if not target_lines else "MODIFY"
    files_modified.append({
        "path": target_file,
        "action": file_action,
        "changes": target_changes,
    })

    # 2. Source file: remove the symbol
    source_changes = [{
        "type": "delete",
        "line_start": line_start,
        "line_end": line_end,
        "old_text": "\n".join(symbol_lines),
    }]
    files_modified.append({
        "path": source_file,
        "action": "MODIFY",
        "changes": source_changes,
    })

    # 3. Caller files: rewrite imports
    referencing_files = _find_files_referencing(conn, sym["id"])
    for ref_file in referencing_files:
        if ref_file == source_file:
            continue
        ref_lines = _read_file(ref_file)
        import_idx = _find_import_line(ref_lines, sym_actual_name)
        if import_idx is not None:
            old_line = ref_lines[import_idx]
            new_line = _rewrite_import(old_line, old_module, new_module)
            if old_line != new_line:
                files_modified.append({
                    "path": ref_file,
                    "action": "MODIFY",
                    "changes": [{
                        "type": "replace",
                        "line_start": import_idx + 1,
                        "line_end": import_idx + 1,
                        "old_text": old_line,
                        "new_text": new_line,
                    }],
                })

    result = {
        "operation": "move",
        "symbol": sym_actual_name,
        "from_file": source_file,
        "to_file": target_file,
        "lines_moved": symbol_line_count,
        "files_modified": files_modified,
        "warnings": warnings,
    }

    if not dry_run:
        _apply_move(source_file, source_lines, start_idx, end_idx,
                     target_file, target_lines, symbol_lines,
                     conn, sym, old_module, new_module, referencing_files)

    return result


def _apply_move(source_file, source_lines, start_idx, end_idx,
                target_file, target_lines, symbol_lines,
                conn, sym, old_module, new_module, referencing_files):
    """Actually write the move changes to disk."""
    # Write symbol to target
    new_target = list(target_lines)
    if new_target:
        new_target.append("")
    new_target.extend(symbol_lines)
    _write_file(target_file, new_target)

    # Remove from source
    new_source = source_lines[:start_idx] + source_lines[end_idx:]
    _write_file(source_file, new_source)

    # Rewrite caller imports
    sym_name = sym["name"]
    for ref_file in referencing_files:
        if ref_file == source_file:
            continue
        ref_lines = _read_file(ref_file)
        import_idx = _find_import_line(ref_lines, sym_name)
        if import_idx is not None:
            old_line = ref_lines[import_idx]
            new_line = _rewrite_import(old_line, old_module, new_module)
            if old_line != new_line:
                ref_lines[import_idx] = new_line
                _write_file(ref_file, ref_lines)


def rename_symbol(conn, symbol_name: str, new_name: str,
                  dry_run: bool = True) -> dict:
    """Rename a symbol across the codebase.

    1. Resolve symbol
    2. Find all references (callers + importers)
    3. For each file containing a reference: replace old name with new name
    4. Replace in the definition file
    5. Return planned changes

    Parameters
    ----------
    conn:
        SQLite connection to the roam index.
    symbol_name:
        Current name of the symbol.
    new_name:
        New name for the symbol.
    dry_run:
        If True, return planned changes without writing files.
    """
    sym = find_symbol(conn, symbol_name)
    if not sym:
        return {
            "operation": "rename",
            "symbol": symbol_name,
            "new_name": new_name,
            "error": f"symbol not found: {symbol_name}",
            "files_modified": [],
            "warnings": [f"symbol not found: {symbol_name}"],
        }

    sym_actual_name = sym["name"]
    source_file = sym["file_path"]
    line_start = sym["line_start"] or 1
    line_end = sym["line_end"] or line_start

    files_modified = []
    warnings = []

    # 1. Definition file: rename in the definition line(s)
    source_lines = _read_file(source_file)
    def_changes = []
    for i in range(max(0, line_start - 1), min(len(source_lines), line_end)):
        line = source_lines[i]
        if sym_actual_name in line:
            new_line = line.replace(sym_actual_name, new_name)
            def_changes.append({
                "type": "replace",
                "line_start": i + 1,
                "line_end": i + 1,
                "old_text": line,
                "new_text": new_line,
            })

    if def_changes:
        files_modified.append({
            "path": source_file,
            "action": "MODIFY",
            "changes": def_changes,
        })

    # 2. Reference files: rename in each referencing file
    referencing_files = _find_files_referencing(conn, sym["id"])
    for ref_file in referencing_files:
        ref_lines = _read_file(ref_file)
        ref_changes = []
        for i, line in enumerate(ref_lines):
            if sym_actual_name in line:
                new_line = line.replace(sym_actual_name, new_name)
                ref_changes.append({
                    "type": "replace",
                    "line_start": i + 1,
                    "line_end": i + 1,
                    "old_text": line,
                    "new_text": new_line,
                })

        if ref_changes:
            # Avoid duplicating source file
            if ref_file == source_file:
                # Merge changes with existing definition changes
                existing = next((f for f in files_modified
                                 if f["path"] == source_file), None)
                if existing:
                    existing_lines = {c["line_start"] for c in existing["changes"]}
                    for c in ref_changes:
                        if c["line_start"] not in existing_lines:
                            existing["changes"].append(c)
                continue
            files_modified.append({
                "path": ref_file,
                "action": "MODIFY",
                "changes": ref_changes,
            })

    result = {
        "operation": "rename",
        "symbol": sym_actual_name,
        "new_name": new_name,
        "file": source_file,
        "files_modified": files_modified,
        "warnings": warnings,
    }

    if not dry_run:
        _apply_rename(files_modified, sym_actual_name, new_name)

    return result


def _apply_rename(files_modified, old_name, new_name):
    """Actually write rename changes to disk."""
    for fmod in files_modified:
        lines = _read_file(fmod["path"])
        for change in sorted(fmod["changes"],
                             key=lambda c: c["line_start"], reverse=True):
            idx = change["line_start"] - 1
            if 0 <= idx < len(lines):
                lines[idx] = lines[idx].replace(old_name, new_name)
        _write_file(fmod["path"], lines)


def add_call(conn, from_symbol: str, to_symbol: str, args: str = "",
             dry_run: bool = True) -> dict:
    """Add a call from one symbol to another.

    1. Resolve both symbols
    2. Check if import exists in source file; if not, generate one
    3. Generate call statement: to_symbol_name(args)
    4. Find insertion point (end of from_symbol's body)
    5. Return planned changes

    Parameters
    ----------
    conn:
        SQLite connection to the roam index.
    from_symbol:
        The calling symbol name.
    to_symbol:
        The callee symbol name.
    args:
        Arguments string for the call (e.g. "data, config").
    dry_run:
        If True, return planned changes without writing files.
    """
    from_sym = find_symbol(conn, from_symbol)
    if not from_sym:
        return {
            "operation": "add-call",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "error": f"symbol not found: {from_symbol}",
            "files_modified": [],
            "warnings": [f"symbol not found: {from_symbol}"],
        }

    to_sym = find_symbol(conn, to_symbol)
    if not to_sym:
        return {
            "operation": "add-call",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "error": f"symbol not found: {to_symbol}",
            "files_modified": [],
            "warnings": [f"symbol not found: {to_symbol}"],
        }

    from_file = from_sym["file_path"]
    to_file = to_sym["file_path"]
    from_name = from_sym["name"]
    to_name = to_sym["name"]

    language = detect_language(from_file)
    lines = _read_file(from_file)
    changes = []
    warnings = []

    # Check if import already exists
    needs_import = (from_file != to_file)
    if needs_import:
        import_idx = _find_import_line(lines, to_name)
        if import_idx is not None:
            needs_import = False

    if needs_import:
        import_stmt = generate_import(language, to_file, to_name, from_file)
        # Insert import at line 1 (before everything else) or after existing imports
        insert_line = 1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                insert_line = i + 2  # After this import line (1-based)
        changes.append({
            "type": "insert",
            "line": insert_line,
            "text": import_stmt,
        })

    # Generate call statement
    call_stmt = f"    {to_name}({args})"
    from_end = from_sym["line_end"] or (from_sym["line_start"] or 1)
    # Insert before the last line of from_symbol's body
    insert_line = from_end
    changes.append({
        "type": "insert",
        "line": insert_line,
        "text": call_stmt,
    })

    files_modified = []
    if changes:
        files_modified.append({
            "path": from_file,
            "action": "MODIFY",
            "changes": changes,
        })

    result = {
        "operation": "add-call",
        "from_symbol": from_name,
        "to_symbol": to_name,
        "from_file": from_file,
        "to_file": to_file,
        "files_modified": files_modified,
        "warnings": warnings,
    }

    if not dry_run:
        _apply_add_call(from_file, lines, changes)

    return result


def _apply_add_call(file_path, lines, changes):
    """Actually write the add-call changes to disk."""
    new_lines = list(lines)
    # Sort inserts by line in reverse so indices don't shift
    sorted_changes = sorted(changes, key=lambda c: c.get("line", 0),
                            reverse=True)
    for change in sorted_changes:
        if change["type"] == "insert":
            idx = change["line"] - 1
            new_lines.insert(idx, change["text"])
    _write_file(file_path, new_lines)


def extract_symbol(conn, source_symbol: str, line_start: int, line_end: int,
                   new_name: str, dry_run: bool = True) -> dict:
    """Extract lines from a symbol into a new function.

    1. Read the source file
    2. Extract lines line_start to line_end
    3. Create new function definition with new_name
    4. Replace extracted lines with a call to new_name
    5. Return planned changes

    Parameters
    ----------
    conn:
        SQLite connection to the roam index.
    source_symbol:
        Name of the containing symbol.
    line_start:
        Start line (1-based) to extract.
    line_end:
        End line (1-based, inclusive) to extract.
    new_name:
        Name for the new extracted function.
    dry_run:
        If True, return planned changes without writing files.
    """
    sym = find_symbol(conn, source_symbol)
    if not sym:
        return {
            "operation": "extract",
            "symbol": source_symbol,
            "new_name": new_name,
            "error": f"symbol not found: {source_symbol}",
            "files_modified": [],
            "warnings": [f"symbol not found: {source_symbol}"],
        }

    source_file = sym["file_path"]
    sym_name = sym["name"]
    language = detect_language(source_file)

    lines = _read_file(source_file)
    if not lines:
        return {
            "operation": "extract",
            "symbol": sym_name,
            "new_name": new_name,
            "error": f"could not read source file: {source_file}",
            "files_modified": [],
            "warnings": [f"could not read source file: {source_file}"],
        }

    # Validate line range
    start_idx = max(0, line_start - 1)
    end_idx = min(len(lines), line_end)
    extracted_lines = lines[start_idx:end_idx]

    if not extracted_lines:
        return {
            "operation": "extract",
            "symbol": sym_name,
            "new_name": new_name,
            "error": "no lines in specified range",
            "files_modified": [],
            "warnings": ["no lines in specified range"],
        }

    # Detect indentation of extracted lines
    base_indent = ""
    for el in extracted_lines:
        if el.strip():
            base_indent = el[:len(el) - len(el.lstrip())]
            break

    # Build new function definition
    if language == "python":
        func_def = f"def {new_name}():"
    elif language in ("javascript", "typescript", "tsx"):
        func_def = f"function {new_name}() {{"
    elif language == "go":
        func_def = f"func {new_name}() {{"
    else:
        func_def = f"def {new_name}():"

    # Build call statement (same indent as extracted lines)
    if language in ("javascript", "typescript", "tsx"):
        call_stmt = f"{base_indent}{new_name}();"
    else:
        call_stmt = f"{base_indent}{new_name}()"

    changes = []

    # Replace extracted lines with call
    changes.append({
        "type": "replace",
        "line_start": line_start,
        "line_end": line_end,
        "old_text": "\n".join(extracted_lines),
        "new_text": call_stmt,
    })

    # Insert new function definition after the containing symbol
    sym_end = sym["line_end"] or len(lines)
    insert_at = sym_end + 1

    new_func_lines = ["", "", func_def]
    for el in extracted_lines:
        new_func_lines.append("    " + el.lstrip() if el.strip() else el)
    if language in ("javascript", "typescript", "tsx", "go"):
        new_func_lines.append("}")

    changes.append({
        "type": "insert",
        "line": insert_at,
        "text": "\n".join(new_func_lines),
    })

    files_modified = [{
        "path": source_file,
        "action": "MODIFY",
        "changes": changes,
    }]

    warnings = []

    result = {
        "operation": "extract",
        "symbol": sym_name,
        "new_name": new_name,
        "file": source_file,
        "line_range": f"{line_start}-{line_end}",
        "lines_extracted": len(extracted_lines),
        "files_modified": files_modified,
        "warnings": warnings,
    }

    if not dry_run:
        _apply_extract(source_file, lines, start_idx, end_idx,
                       call_stmt, new_func_lines, sym_end)

    return result


def _apply_extract(source_file, lines, start_idx, end_idx,
                   call_stmt, new_func_lines, sym_end):
    """Actually write the extract changes to disk."""
    new_lines = list(lines)

    # Replace extracted lines with call
    new_lines[start_idx:end_idx] = [call_stmt]

    # Adjust insertion point (we removed lines, so shift)
    removed_count = (end_idx - start_idx) - 1
    insert_idx = sym_end - removed_count
    if insert_idx < 0:
        insert_idx = len(new_lines)

    for i, fl in enumerate(new_func_lines):
        new_lines.insert(insert_idx + i, fl)

    _write_file(source_file, new_lines)
