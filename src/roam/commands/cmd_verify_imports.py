"""Verify import statements against the indexed symbol table (hallucination firewall)."""

from __future__ import annotations

import os
import re
import sqlite3

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Import pattern regexes
# ---------------------------------------------------------------------------

# Python: import X, from X import Y
_PY_IMPORT = re.compile(
    r"^\s*import\s+([\w.]+)"
)
_PY_FROM_IMPORT = re.compile(
    r"^\s*from\s+([\w.]+)\s+import\s+([\w*][\w,\s*]*)"
)

# JavaScript / TypeScript: import { X } from 'Y', import X from 'Y', require('X')
_JS_IMPORT_FROM = re.compile(
    r"""^\s*import\s+(?:\{([^}]+)\}\s+from|(\w+)\s+from)\s+['"]([^'"]+)['"]"""
)
_JS_REQUIRE = re.compile(
    r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)"""
)

# Go: import "pkg" or import ( "pkg" )
_GO_IMPORT = re.compile(
    r"""^\s*(?:import\s+)?["']([^"']+)["']"""
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _extract_import_names_from_line(line: str, language: str | None) -> list[str]:
    """Extract imported symbol/module names from a single source line.

    Returns a list of name strings that should be validated against the index.
    """
    names: list[str] = []

    # Python
    m = _PY_FROM_IMPORT.match(line)
    if m:
        module = m.group(1)
        imports_str = m.group(2)
        names.append(module)
        for part in imports_str.split(","):
            part = part.strip()
            if part and part != "*":
                # Handle "as" aliases: "foo as bar" -> "foo"
                name = part.split(" as ")[0].strip() if " as " in part else part
                names.append(name)
        return names

    m = _PY_IMPORT.match(line)
    if m:
        names.append(m.group(1))
        return names

    # JavaScript / TypeScript
    m = _JS_IMPORT_FROM.match(line)
    if m:
        braced = m.group(1)
        default = m.group(2)
        module_path = m.group(3)
        names.append(module_path.split("/")[-1])  # last segment
        if braced:
            for part in braced.split(","):
                part = part.strip()
                if part:
                    name = part.split(" as ")[0].strip() if " as " in part else part
                    names.append(name)
        if default:
            names.append(default)
        return names

    m = _JS_REQUIRE.search(line)
    if m:
        module_path = m.group(1)
        names.append(module_path.split("/")[-1])
        return names

    # Go
    if language in ("go", "Go"):
        m = _GO_IMPORT.match(line)
        if m:
            pkg = m.group(1)
            names.append(pkg.split("/")[-1])
            return names

    return names


def _get_file_language(conn: sqlite3.Connection, file_path: str) -> str | None:
    """Look up language for a file path from the index."""
    row = conn.execute(
        "SELECT language FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    return row["language"] if row else None


def _check_name_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Check if a name exists as a symbol name, qualified_name, or file path."""
    # Check symbols table
    row = conn.execute(
        "SELECT 1 FROM symbols WHERE name = ? OR qualified_name = ? LIMIT 1",
        (name, name),
    ).fetchone()
    if row:
        return True

    # Check if it matches a file path (module name -> file)
    # e.g. "models" -> "models.py" or "src/models.py"
    row = conn.execute(
        "SELECT 1 FROM files WHERE path LIKE ? OR path LIKE ? LIMIT 1",
        (f"%/{name}.%", f"{name}.%"),
    ).fetchone()
    if row:
        return True

    # Check for dotted module path (e.g. "os.path" -> look for "path" in symbols)
    if "." in name:
        last_part = name.rsplit(".", 1)[-1]
        row = conn.execute(
            "SELECT 1 FROM symbols WHERE name = ? LIMIT 1",
            (last_part,),
        ).fetchone()
        if row:
            return True

    return False


def _fts_suggestions(conn: sqlite3.Connection, name: str, limit: int = 3) -> list[str]:
    """Use FTS5 to find fuzzy matches for an unresolved import name."""
    suggestions: list[str] = []

    # Tokenize the query
    tokens = name.replace("_", " ").replace(".", " ").split()
    if not tokens:
        return suggestions

    try:
        fts_query = " OR ".join(f'"{t}"*' for t in tokens)
        rows = conn.execute(
            "SELECT s.name, s.qualified_name, f.path as file_path "
            "FROM symbol_fts sf "
            "JOIN symbols s ON sf.rowid = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE symbol_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        for r in rows:
            display = r["qualified_name"] or r["name"]
            if display not in suggestions:
                suggestions.append(display)
    except Exception:
        # FTS5 not available, try LIKE fallback
        try:
            rows = conn.execute(
                "SELECT s.name, s.qualified_name "
                "FROM symbols s "
                "WHERE s.name LIKE ? COLLATE NOCASE "
                "ORDER BY s.name LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
            for r in rows:
                display = r["qualified_name"] or r["name"]
                if display not in suggestions:
                    suggestions.append(display)
        except Exception:
            pass

    return suggestions


def _get_edge_imports(conn: sqlite3.Connection, file_path: str | None) -> list[dict]:
    """Get import edges from the edges table, optionally filtered by file."""
    if file_path:
        rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.line, "
            "s_src.name AS source_name, s_src.qualified_name AS source_qname, "
            "s_tgt.name AS target_name, s_tgt.qualified_name AS target_qname, "
            "f.path AS file_path "
            "FROM edges e "
            "JOIN symbols s_src ON e.source_id = s_src.id "
            "LEFT JOIN symbols s_tgt ON e.target_id = s_tgt.id "
            "JOIN files f ON s_src.file_id = f.id "
            "WHERE e.kind = 'import' AND f.path = ?",
            (file_path,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.line, "
            "s_src.name AS source_name, s_src.qualified_name AS source_qname, "
            "s_tgt.name AS target_name, s_tgt.qualified_name AS target_qname, "
            "f.path AS file_path "
            "FROM edges e "
            "JOIN symbols s_src ON e.source_id = s_src.id "
            "LEFT JOIN symbols s_tgt ON e.target_id = s_tgt.id "
            "JOIN files f ON s_src.file_id = f.id "
            "WHERE e.kind = 'import'"
        ).fetchall()

    return [dict(r) for r in rows]


def _scan_file_imports(
    conn: sqlite3.Connection,
    file_path: str,
    project_root: str,
) -> list[dict]:
    """Scan a source file for import statements and validate each one.

    Returns a list of import dicts with keys:
        file, line, name, status (resolved/unresolved), suggestions
    """
    full_path = os.path.join(project_root, file_path)
    if not os.path.isfile(full_path):
        return []

    language = _get_file_language(conn, file_path)
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, start=1):
                import_names = _extract_import_names_from_line(line, language)
                for name in import_names:
                    key = (name, line_num)
                    if key in seen:
                        continue
                    seen.add(key)

                    resolved = _check_name_exists(conn, name)
                    entry: dict = {
                        "file": file_path,
                        "line": line_num,
                        "name": name,
                        "status": "resolved" if resolved else "unresolved",
                        "suggestions": [],
                    }
                    if not resolved:
                        entry["suggestions"] = _fts_suggestions(conn, name)
                    results.append(entry)
    except (OSError, UnicodeDecodeError):
        pass

    return results


def verify_imports(
    conn: sqlite3.Connection,
    project_root: str,
    file_filter: str | None = None,
) -> dict:
    """Run the full import verification pipeline.

    Parameters
    ----------
    conn:
        Open DB connection (readonly is fine for reads; we don't write).
    project_root:
        Absolute path to the project root directory.
    file_filter:
        Optional file path to restrict scanning to a single file.

    Returns
    -------
    dict with keys: imports (list), total, resolved, unresolved, files_checked
    """
    # 1. Determine which files to check
    if file_filter:
        # Normalize the filter path
        norm = file_filter.replace("\\", "/")
        row = conn.execute(
            "SELECT path FROM files WHERE path = ? OR path LIKE ?",
            (norm, f"%{norm}"),
        ).fetchone()
        if row:
            file_paths = [row["path"]]
        else:
            file_paths = [norm]
    else:
        rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        file_paths = [r["path"] for r in rows]

    # 2. Scan each file
    all_imports: list[dict] = []
    files_checked: set[str] = set()

    for fp in file_paths:
        file_imports = _scan_file_imports(conn, fp, project_root)
        if file_imports:
            files_checked.add(fp)
            all_imports.extend(file_imports)

    # 3. Also check edge-based imports from the DB
    edge_imports = _get_edge_imports(conn, file_filter)
    for edge in edge_imports:
        target_name = edge.get("target_name") or ""
        if not target_name:
            continue
        # Check if we already found this import from file scanning
        fp = edge["file_path"]
        line = edge.get("line") or 0
        already_found = any(
            i["file"] == fp and i["name"] == target_name
            for i in all_imports
        )
        if already_found:
            continue

        resolved = edge["target_id"] is not None and _check_name_exists(conn, target_name)
        entry: dict = {
            "file": fp,
            "line": line,
            "name": target_name,
            "status": "resolved" if resolved else "unresolved",
            "suggestions": [],
        }
        if not resolved:
            entry["suggestions"] = _fts_suggestions(conn, target_name)
        all_imports.append(entry)
        files_checked.add(fp)

    total = len(all_imports)
    resolved = sum(1 for i in all_imports if i["status"] == "resolved")
    unresolved = total - resolved

    return {
        "imports": all_imports,
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "files_checked": len(files_checked),
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("verify-imports")
@click.option("--file", "file_path", default=None,
              help="Restrict verification to a single file path.")
@click.pass_context
def verify_imports_cmd(ctx, file_path):
    """Validate import/require statements against the indexed symbol table.

    Flags unresolvable imports and suggests corrections via fuzzy matching.
    Acts as a hallucination firewall for AI-generated code.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = str(find_project_root())

    with open_db(readonly=True) as conn:
        result = verify_imports(conn, project_root, file_filter=file_path)

    imports = result["imports"]
    total = result["total"]
    resolved = result["resolved"]
    unresolved = result["unresolved"]
    files_checked = result["files_checked"]

    # Build verdict
    if total == 0:
        verdict = "No imports found to verify"
    elif unresolved == 0:
        verdict = f"All {total} imports resolved across {files_checked} files"
    else:
        verdict = (
            f"{unresolved} unresolved imports out of {total} "
            f"in {files_checked} files"
        )

    # --- JSON output ---
    if json_mode:
        # Filter to unresolved for compact JSON; include all if few
        import_records = []
        for i in imports:
            rec: dict = {
                "file": i["file"],
                "line": i["line"],
                "name": i["name"],
                "status": i["status"],
            }
            if i["suggestions"]:
                rec["suggestions"] = i["suggestions"]
            import_records.append(rec)

        envelope = json_envelope(
            "verify-imports",
            summary={
                "verdict": verdict,
                "total_imports": total,
                "resolved": resolved,
                "unresolved": unresolved,
                "files_checked": files_checked,
            },
            budget=token_budget,
            imports=import_records,
        )
        click.echo(to_json(envelope))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if unresolved > 0:
        rows = []
        for i in imports:
            if i["status"] != "unresolved":
                continue
            loc_str = f"{i['file']}:{i['line']}"
            suggestions_str = ", ".join(i["suggestions"]) if i["suggestions"] else "-"
            rows.append([loc_str, i["name"], suggestions_str])

        click.echo(format_table(
            ["Location", "Import", "Suggestions"],
            rows,
        ))
        click.echo()
        click.echo(f"  {unresolved} unresolved, {resolved} resolved, "
                    f"{files_checked} files checked")
        click.echo()
        click.echo("  Tip: Run `roam search <name>` for more details on a symbol.")
        click.echo("       If recently added, run `roam index` to refresh.")
    else:
        if total > 0:
            click.echo(f"  All {total} imports verified successfully.")
        else:
            click.echo("  No import statements found in indexed files.")
