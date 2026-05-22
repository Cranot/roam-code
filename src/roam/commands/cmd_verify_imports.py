"""Verify import statements against the indexed symbol table (hallucination firewall).

W1229: SARIF is deliberately surfaced via the global ``--sarif`` flag.
cmd_verify_imports emits per-import rows (each carrying ``file`` / ``line``
/ ``name`` / ``status`` / ``suggestions``) which the
:func:`roam.output.sarif.verify_imports_to_sarif` projection maps onto two
closed-enum rule ids — ``invalid-import`` (unresolved with fuzzy-match
candidates; warning band) and ``hallucination-import`` (unresolved with no
candidates; error band — the canonical hallucination-firewall signal for
LLM-generated code). See W1229 audit (Wave 15) + the SHIP path in
:mod:`tests.test_sarif_disclosure_coverage` (cmd_verify_imports removed
from ``_KNOWN_MISSING``).
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.db.edge_kinds import import_in_clause
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Python stdlib module names (for filtering false positives)
# ---------------------------------------------------------------------------

# sys.stdlib_module_names ships in Python 3.10+; pyproject.toml pins
# requires-python = ">=3.10", so this attribute is always present.
_PYTHON_STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)


def _is_stdlib_module(name: str) -> bool:
    """Return True if *name* is a Python stdlib module (top-level check)."""
    top = name.split(".")[0]
    return top in _PYTHON_STDLIB


def _is_python_file(language: str | None, file_path: str) -> bool:
    """Return True if the file is a Python source file."""
    if language and language.lower() in ("python", "py"):
        return True
    return file_path.endswith(".py") or file_path.endswith(".pyi")


# ---------------------------------------------------------------------------
# Import pattern regexes
# ---------------------------------------------------------------------------

# Python: import X, from X import Y
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+)")
_PY_FROM_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+([\w*][\w,\s*]*)")

# JavaScript / TypeScript: import { X } from 'Y', import X from 'Y', require('X')
_JS_IMPORT_FROM = re.compile(r"""^\s*import\s+(?:\{([^}]+)\}\s+from|(\w+)\s+from)\s+['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)""")

# Go: import "pkg" or import ( "pkg" )
_GO_IMPORT = re.compile(r"""^\s*(?:import\s+)?["']([^"']+)["']""")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _extract_import_names_from_line(line: str, language: str | None) -> list[str]:
    """Extract imported symbol/module names from a single source line.

    Returns a list of name strings that should be validated against the index.

    Language is checked first so JS-style imports (``import Bar from 'x'``)
    in ``.js``/``.ts``/``.vue``/``.svelte`` files don't accidentally hit
    the Python ``import Bar`` regex — that misattribution made every Vue
    SFC import look like a 1-name Python import and dropped the module
    path entirely.
    """
    names: list[str] = []
    lang = (language or "").lower()
    is_js_like = lang in (
        "javascript",
        "typescript",
        "tsx",
        "jsx",
        "vue",
        "svelte",
    )

    # JavaScript / TypeScript / Vue SFC / Svelte: try JS shapes first.
    if is_js_like:
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
        # JS-like files don't fall through to the Python regex.
        return names

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

    # JavaScript / TypeScript (fallback for files of unknown language)
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
    if lang in ("go",):
        m = _GO_IMPORT.match(line)
        if m:
            pkg = m.group(1)
            names.append(pkg.split("/")[-1])
            return names

    return names


def _get_file_language(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    lang_by_path: dict[str, str | None] | None = None,
) -> str | None:
    """Look up language for a file path from the index.

    When ``lang_by_path`` (a ``{path: language}`` map built once per run) is
    supplied, the lookup is an O(1) dict hit instead of a per-call SELECT —
    same result, including ``None`` for an unknown path. Callers without the
    map fall back to the single-row query.
    """
    if lang_by_path is not None:
        return lang_by_path.get(file_path)
    row = conn.execute("SELECT language FROM files WHERE path = ?", (file_path,)).fetchone()
    return row["language"] if row else None


class _FilePathIndex:
    """In-memory index over ``files.path`` mirroring the ``path LIKE`` fallbacks.

    Built ONCE per run from the same ``files`` rows the per-miss ``path LIKE``
    queries scanned. Every miss in :func:`_check_name_exists` otherwise fired a
    LEADING-wildcard ``LIKE`` (``%/{name}.%`` etc.) — un-indexable, so a full
    scan of ``files`` per unresolved import (~9.6k scans on roam-code). These
    structures make each check an O(1) set hit (O(bucket) for the rare
    underscore case).

    SQLite ``LIKE`` semantics replicated exactly:
      * ASCII-case-insensitive — keys are lowercased; the matcher lowercases
        the probe name.
      * ``_`` in the probe name is a single-char wildcard — handled via
        length-bucketed char-by-char compare (``_`` matches any one char).
      * ``%`` cannot appear in an import name (the extractor regexes restrict
        names to ``[\\w.]`` / path last-segments), and ``/`` likewise — names
        carrying either route to the caller's SQL fallback so correctness is
        never traded for the fast path.
    """

    def __init__(self, paths: list[str]) -> None:
        # path LIKE '%/{name}.%' : a '/'-preceded segment whose text up to its
        #   first '.' equals {name} (as a LIKE pattern).
        # path LIKE '{name}.%'   : the whole path's text up to its first '.'
        #   equals {name} (root-relative; '%' prefix absent).
        # Both reduce to: {name} == <stem before first '.'> of some segment,
        # so one stem set serves the OR of the two patterns.
        seg_stems: set[str] = set()
        # path LIKE '%/{name}' : basename (segment after last '/') equals {name}.
        basenames: set[str] = set()
        # path = {name} : exact, case-SENSITIVE equality (no LIKE, no wildcard).
        exact: set[str] = set()
        for p in paths:
            exact.add(p)
            pl = p.lower()
            sl = pl.rfind("/")
            basenames.add(pl[sl + 1 :] if sl >= 0 else pl)
            # whole-path stem (pattern '{name}.%')
            dot = pl.find(".")
            if dot > 0:
                seg_stems.add(pl[:dot])
            # per-'/'-segment stems (pattern '%/{name}.%')
            start = 0
            while True:
                j = pl.find("/", start)
                if j < 0:
                    break
                seg = pl[j + 1 :]
                k = seg.find(".")
                if k > 0:
                    seg_stems.add(seg[:k])
                start = j + 1
        self._seg_stems = seg_stems
        self._basenames = basenames
        self._exact = exact
        self._seg_stems_by_len: dict[int, list[str]] = defaultdict(list)
        for s in seg_stems:
            self._seg_stems_by_len[len(s)].append(s)
        self._basenames_by_len: dict[int, list[str]] = defaultdict(list)
        for b in basenames:
            self._basenames_by_len[len(b)].append(b)

    @staticmethod
    def _like_set_match(name_lower: str, exact_set: set[str], by_len: dict[int, list[str]]) -> bool:
        """True iff *name_lower* matches an entry treating ``_`` as a wildcard."""
        if "_" not in name_lower:
            return name_lower in exact_set
        bucket = by_len.get(len(name_lower))
        if not bucket:
            return False
        for cand in bucket:
            if all(a == "_" or a == c for a, c in zip(name_lower, cand)):
                return True
        return False

    def module_file_match(self, name: str) -> bool:
        """Mirror ``path LIKE '%/{name}.%' OR path LIKE '{name}.%'``."""
        return self._like_set_match(name.lower(), self._seg_stems, self._seg_stems_by_len)

    def sfc_file_match(self, name: str) -> bool:
        """Mirror ``path LIKE '%/{name}' OR path = {name}`` (Vue/Svelte SFC)."""
        if self._like_set_match(name.lower(), self._basenames, self._basenames_by_len):
            return True
        # path = ? is exact case-sensitive equality.
        return name in self._exact


def _build_file_path_index(conn: sqlite3.Connection) -> _FilePathIndex:
    """Load every ``files.path`` once into a :class:`_FilePathIndex`."""
    paths = [r["path"] for r in conn.execute("SELECT path FROM files")]
    return _FilePathIndex(paths)


def _check_name_exists(
    conn: sqlite3.Connection,
    name: str,
    *,
    symbol_names: set[str] | None = None,
    symbol_qnames: set[str] | None = None,
    file_index: _FilePathIndex | None = None,
) -> bool:
    """Check if a name exists as a symbol name, qualified_name, or file path.

    When both pre-loaded ``symbol_names`` / ``symbol_qnames`` sets are supplied
    (built once per run), the dominant ``name = ? OR qualified_name = ?`` probe
    — which runs for every import name — becomes an O(1) set membership instead
    of a per-name SELECT. This is exact: membership in either set is true iff
    the query would return a row.

    The file-path fallbacks fire on a symbol-table miss. When ``file_index``
    (a :class:`_FilePathIndex` built once per run) is supplied, those
    leading-wildcard ``path LIKE`` queries — a full scan of ``files`` per miss —
    become in-memory set lookups with semantics identical to the SQL. Callers
    without the index fall back to the direct queries. The dotted-module
    fallback stays a symbol-table query (cheap, indexable).
    """
    # Check symbols table (set fast-path when both preloaded; else query)
    if symbol_names is not None and symbol_qnames is not None:
        if name in symbol_names or name in symbol_qnames:
            return True
    else:
        row = conn.execute(
            "SELECT 1 FROM symbols WHERE name = ? OR qualified_name = ? LIMIT 1",
            (name, name),
        ).fetchone()
        if row:
            return True

    # Vue / Svelte SFC import: name retains the extension
    # (e.g. ``import Bar from '@/components/Bar.vue'`` extracts ``Bar.vue``).
    # Match the file by exact filename (``%/Bar.vue`` or ``Bar.vue``) and,
    # for the symbol form, by stem (``Bar`` is synthesised as a component
    # symbol by the TypeScript extractor for every .vue / .svelte file).
    lower = name.lower()
    if lower.endswith(".vue") or lower.endswith(".svelte"):
        # A '/' or '%' in the name escapes the in-memory index's segment model
        # (import names never carry either, so this fallback is effectively
        # dead — but it keeps the fast path provably exact).
        if file_index is not None and "/" not in name and "%" not in name:
            sfc_hit = file_index.sfc_file_match(name)
        else:
            sfc_hit = (
                conn.execute(
                    "SELECT 1 FROM files WHERE path LIKE ? OR path = ? LIMIT 1",
                    (f"%/{name}", name),
                ).fetchone()
                is not None
            )
        if sfc_hit:
            return True
        # Fallback: synthesised component symbol uses the stem
        stem = name.rsplit(".", 1)[0]
        row = conn.execute(
            "SELECT 1 FROM symbols WHERE name = ? LIMIT 1",
            (stem,),
        ).fetchone()
        if row:
            return True

    # Check if it matches a file path (module name -> file)
    # e.g. "models" -> "models.py" or "src/models.py"
    if file_index is not None and "/" not in name and "%" not in name:
        if file_index.module_file_match(name):
            return True
    else:
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
    except Exception as _fts_exc:  # noqa: BLE001 — defensive
        # FTS5 not available, try LIKE fallback
        from roam.observability import log_swallowed

        log_swallowed("cmd_verify_imports:fts_suggestions", _fts_exc)
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
        except Exception as _like_exc:  # noqa: BLE001 — defensive
            log_swallowed("cmd_verify_imports:like_suggestions_fallback", _like_exc)

    return suggestions


def _get_edge_imports(conn: sqlite3.Connection, file_path: str | None) -> list[dict]:
    """Get import edges from the edges table, optionally filtered by file.

    W543-followup: source the IN-clause from
    :func:`roam.db.edge_kinds.import_in_clause` so the verifier matches
    plugin-emitted ``'imports'`` rows alongside the canonical singular
    ``'import'``.
    """
    kind_clause = import_in_clause("e.kind")
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
            f"WHERE {kind_clause} AND f.path = ?",
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
            f"WHERE {kind_clause}"
        ).fetchall()

    return [dict(r) for r in rows]


def _scan_file_imports(
    conn: sqlite3.Connection,
    file_path: str,
    project_root: str,
    *,
    symbol_names: set[str] | None = None,
    symbol_qnames: set[str] | None = None,
    lang_by_path: dict[str, str | None] | None = None,
    file_index: _FilePathIndex | None = None,
) -> list[dict]:
    """Scan a source file for import statements and validate each one.

    Returns a list of import dicts with keys:
        file, line, name, status (resolved/unresolved), suggestions
    """
    full_path = os.path.join(project_root, file_path)
    if not os.path.isfile(full_path):
        return []

    language = _get_file_language(conn, file_path, lang_by_path=lang_by_path)
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

                    # Skip Python stdlib modules — they are never in the index
                    if _is_python_file(language, file_path) and _is_stdlib_module(name):
                        results.append(
                            {
                                "file": file_path,
                                "line": line_num,
                                "name": name,
                                "status": "resolved",
                                "suggestions": [],
                            }
                        )
                        continue

                    resolved = _check_name_exists(
                        conn,
                        name,
                        symbol_names=symbol_names,
                        symbol_qnames=symbol_qnames,
                        file_index=file_index,
                    )
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
    except (OSError, UnicodeDecodeError) as _exc:
        from roam.observability import log_swallowed

        log_swallowed("cmd_verify_imports:source_scan", _exc)

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

    # Pre-load symbol names / qualified names + a file->language map ONCE so the
    # per-import resolution probe and per-file language lookup are in-memory
    # set/dict hits instead of N+1 SELECTs (the file-scan phase otherwise fired
    # up to ~5 queries per import name -> tens of thousands of round-trips).
    # Output-preserving: set membership is exact for the symbols name/qname
    # probe, and dict.get matches the single-row language SELECT (None for an
    # unknown path).
    symbol_names: set[str] = set()
    symbol_qnames: set[str] = set()
    for r in conn.execute("SELECT name, qualified_name FROM symbols"):
        if r["name"]:
            symbol_names.add(r["name"])
        if r["qualified_name"]:
            symbol_qnames.add(r["qualified_name"])
    lang_by_path: dict[str, str | None] = {
        r["path"]: r["language"] for r in conn.execute("SELECT path, language FROM files")
    }
    # Pre-load the file-path index ONCE so the per-miss ``path LIKE`` fallbacks
    # in ``_check_name_exists`` (leading-wildcard -> un-indexable full scan of
    # ``files`` per unresolved import) become in-memory set lookups. Semantics
    # are identical to the SQL (see _FilePathIndex docstring).
    file_index = _build_file_path_index(conn)

    # 2. Scan each file
    all_imports: list[dict] = []
    files_checked: set[str] = set()

    for fp in file_paths:
        file_imports = _scan_file_imports(
            conn,
            fp,
            project_root,
            symbol_names=symbol_names,
            symbol_qnames=symbol_qnames,
            lang_by_path=lang_by_path,
            file_index=file_index,
        )
        if file_imports:
            files_checked.add(fp)
            all_imports.extend(file_imports)

    # 3. Also check edge-based imports from the DB.
    # (file, name) index for O(1) edge-dedup against already-found imports —
    # replaces an O(n) linear scan per edge, kept in sync as new edge imports
    # are appended (matches the original growing-scan semantics exactly).
    seen_file_name: set[tuple[str, str]] = {(i["file"], i["name"]) for i in all_imports}
    edge_imports = _get_edge_imports(conn, file_filter)
    for edge in edge_imports:
        target_name = edge.get("target_name") or ""
        if not target_name:
            continue
        # Check if we already found this import from file scanning
        fp = edge["file_path"]
        line = edge.get("line") or 0
        if (fp, target_name) in seen_file_name:
            continue

        # Skip Python stdlib modules in edge-based imports too
        edge_lang = _get_file_language(conn, fp, lang_by_path=lang_by_path)
        if _is_python_file(edge_lang, fp) and _is_stdlib_module(target_name):
            all_imports.append(
                {
                    "file": fp,
                    "line": line,
                    "name": target_name,
                    "status": "resolved",
                    "suggestions": [],
                }
            )
            files_checked.add(fp)
            seen_file_name.add((fp, target_name))
            continue

        resolved = edge["target_id"] is not None and _check_name_exists(
            conn,
            target_name,
            symbol_names=symbol_names,
            symbol_qnames=symbol_qnames,
            file_index=file_index,
        )
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
        seen_file_name.add((fp, target_name))

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


@roam_capability(
    name="verify-imports",
    category="workflow",
    summary="Validate import/require statements against the indexed symbol table",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("verify-imports")
@click.option("--path", "file_path", default=None, help="Restrict verification to a single file path.")
@click.option(
    "--file",
    "file_path",
    default=None,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.pass_context
def verify_imports_cmd(ctx, file_path):
    """Validate import/require statements against the indexed symbol table.

    Flags unresolvable imports and suggests corrections via fuzzy matching.
    Acts as a hallucination firewall for AI-generated code.

    Unlike ``search`` (which finds symbols by name) and ``relate`` (which shows
    symbol relationships), this command validates that import statements in source
    files resolve to indexed symbols -- a hallucination firewall for AI-generated
    imports.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = str(find_project_root())

    with open_db(readonly=True) as conn:
        result = verify_imports(conn, project_root, file_filter=file_path)

        # --- SARIF output (W1229) ------------------------------------------
        # SARIF surfaces the closed-enum classification rule catalogue
        # (invalid-import / hallucination-import) even on a clean scan so
        # CI consumers see the rule vocabulary regardless of whether any
        # import fired. ``resolved`` rows are filtered upstream by
        # ``verify_imports_to_sarif`` (not actionable). Language is
        # stamped onto each row inside this branch so the SARIF message
        # body can prefix the imported name with the producer's
        # ``language`` column (the JSON envelope keeps the per-import
        # record compact and elides the language field — only the SARIF
        # branch consumes it).
        if sarif_mode:
            from roam.output.sarif import verify_imports_to_sarif, write_sarif

            sarif_findings: list[dict] = []
            for i in result["imports"]:
                if i.get("status") != "unresolved":
                    continue
                lang = _get_file_language(conn, i["file"]) or ""
                sarif_findings.append(
                    {
                        "file": i["file"],
                        "line": i["line"],
                        "name": i["name"],
                        "status": i["status"],
                        "language": lang,
                        "suggestions": i.get("suggestions", []),
                    }
                )
            click.echo(write_sarif(verify_imports_to_sarif(sarif_findings)))
            return

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
        verdict = f"{unresolved} unresolved imports out of {total} in {files_checked} files"

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

        click.echo(
            format_table(
                ["Location", "Import", "Suggestions"],
                rows,
            )
        )
        click.echo()
        click.echo(f"  {unresolved} unresolved, {resolved} resolved, {files_checked} files checked")
        click.echo()
        click.echo("  Tip: Run `roam search <name>` for more details on a symbol.")
        click.echo("       If recently added, run `roam index` to refresh.")
    else:
        if total > 0:
            click.echo(f"  All {total} imports verified successfully.")
        else:
            click.echo("  No import statements found in indexed files.")
