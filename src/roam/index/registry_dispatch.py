"""Post-indexing pass to synthesise edges from string-keyed dispatch
registries to the functions they reference.

Why this exists
---------------

Roam's call-graph is built from explicit ``import`` and call-site
references. That misses a common Python pattern: a module-level dict
that maps user-visible names to ``(module_path, function_name)``
strings, which downstream code resolves at runtime via ``importlib``::

    _COMMANDS = {
        "preflight": ("roam.commands.cmd_preflight", "preflight"),
        "context":   ("roam.commands.cmd_context", "context"),
        ...
    }

The strings carry no Python-level reference, so the call-graph shows
``preflight`` as having zero dependents. ``roam impact preflight`` then
reports "no dependents — safe to change", which is misleading: every
CLI invocation that types ``roam preflight`` resolves through this
table.

This pass parses any module-level dict assignment whose values are
``(string, string)`` tuples and the first string starts with the
project's package prefix (here, ``roam.``), then looks up the target
symbol whose ``qualified_name`` matches the second string and inserts a
``dispatch`` edge from the dict's enclosing module to that symbol.

It's intentionally conservative: only modules under the project's own
package, only ``(module_path, fn_name)`` tuple values, and only string
literals (no f-strings, no concatenation). Non-matching shapes are
skipped silently — the indexer keeps working even when the parse
doesn't succeed.
"""

from __future__ import annotations

import ast


def _build_symbol_lookups(conn) -> tuple[dict[str, list[int]], dict[tuple[str, str], list[int]]]:
    """Build the qualified-name and (module-path, name) lookup maps used
    to resolve dispatch tuples to symbol ids."""
    sym_rows = conn.execute(
        """
        SELECT s.id, s.name, s.qualified_name, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'class', 'method')
          AND f.language = 'python'
        """
    ).fetchall()
    by_qualified: dict[str, list[int]] = {}
    by_module_dotted: dict[tuple[str, str], list[int]] = {}
    for s in sym_rows:
        qn = s["qualified_name"] or s["name"]
        if qn:
            by_qualified.setdefault(qn, []).append(s["id"])
        path = (s["file_path"] or "").replace("\\", "/")
        if path.endswith(".py"):
            mod = path[:-3].replace("/", ".")
            if mod.startswith("src."):
                mod = mod[len("src.") :]
            by_module_dotted.setdefault((mod, s["name"]), []).append(s["id"])
    return by_qualified, by_module_dotted


def _build_file_first_symbol(conn) -> dict[int, int]:
    """For each file, the id of its first-by-id symbol — used as the
    synthetic source-of-edge for dispatch records."""
    out: dict[int, int] = {}
    for row in conn.execute("SELECT file_id, MIN(id) AS first_id FROM symbols GROUP BY file_id").fetchall():
        if row["first_id"] is not None:
            out[row["file_id"]] = row["first_id"]
    return out


def _process_assign_node(
    value,
    package_prefix: str,
    file_id: int,
    source_sym_id: int,
    by_module_dotted: dict[tuple[str, str], list[int]],
    by_qualified: dict[str, list[int]],
    conn,
    seen: set[tuple[int, int]],
    edges_to_insert: list[tuple[int, int]],
) -> None:
    """Inspect one assignment value for known dispatch-table shapes and
    record an edge per resolvable element."""
    if isinstance(value, ast.Dict):
        # Shape A: ``_NAME = {"key": ("module.path", "fn_name"), ...}``.
        for v in value.values:
            target_id = _resolve_string_pair_tuple(v, package_prefix, by_module_dotted, by_qualified)
            if target_id is not None:
                _record_edge(target_id, source_sym_id, seen, edges_to_insert)
    elif isinstance(value, (ast.List, ast.Tuple)):
        # Shape B: ``_NAME = [(..., fn_ref), ...]`` — same-file Name reference.
        for v in value.elts:
            target_id = _resolve_function_ref_in_tuple(v, file_id, by_qualified, conn)
            if target_id is not None:
                _record_edge(target_id, source_sym_id, seen, edges_to_insert)


def _scan_file_for_dispatch(
    path: str,
    file_id: int,
    package_prefix: str,
    file_first_symbol: dict[int, int],
    by_module_dotted: dict[tuple[str, str], list[int]],
    by_qualified: dict[str, list[int]],
    conn,
    seen: set[tuple[int, int]],
    edges_to_insert: list[tuple[int, int]],
) -> None:
    """Read, parse, and walk one Python file for dispatch-table assignments.

    Best-effort — silently skips unreadable files and parse errors.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fp:
            source = fp.read()
    except OSError:
        return
    # Cheap prefilter: skip files with neither the package prefix nor the
    # ``[(`` / ``[<newline>`` patterns that hint at list-of-tuples shape.
    if package_prefix not in source and "[(" not in source and "[\n" not in source:
        return
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return
    source_sym_id = file_first_symbol.get(file_id)
    if source_sym_id is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            _process_assign_node(
                node.value,
                package_prefix,
                file_id,
                source_sym_id,
                by_module_dotted,
                by_qualified,
                conn,
                seen,
                edges_to_insert,
            )


def resolve_registry_dispatch(conn, package_prefix: str = "roam.") -> int:
    """Insert ``dispatch`` edges for ``(module, fn)`` tuples in
    module-level dict registries.

    Returns the number of edges inserted. Idempotent: existing edges
    with ``kind='dispatch'`` are dropped and re-derived each run.
    """
    rows = conn.execute(
        """
        SELECT f.id AS file_id, f.path AS file_path
        FROM files f
        WHERE f.language = 'python'
          AND f.file_role = 'source'
        """
    ).fetchall()
    if not rows:
        return 0

    by_qualified, by_module_dotted = _build_symbol_lookups(conn)
    file_first_symbol = _build_file_first_symbol(conn)

    edges_to_insert: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for r in rows:
        _scan_file_for_dispatch(
            r["file_path"],
            r["file_id"],
            package_prefix,
            file_first_symbol,
            by_module_dotted,
            by_qualified,
            conn,
            seen,
            edges_to_insert,
        )

    with conn:
        conn.execute("DELETE FROM edges WHERE kind = 'dispatch'")
        if edges_to_insert:
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'dispatch')",
                edges_to_insert,
            )
    return len(edges_to_insert)


def _resolve_string_pair_tuple(
    value,
    package_prefix: str,
    by_module_dotted: dict[tuple[str, str], list[int]],
    by_qualified: dict[str, list[int]],
) -> int | None:
    """For a dict-value of shape ``("module.path", "fn_name")`` return
    the matching symbol id, or None if the shape doesn't match or the
    target can't be resolved.
    """
    if not isinstance(value, ast.Tuple) or len(value.elts) != 2:
        return None
    mod_node, name_node = value.elts
    if not (
        isinstance(mod_node, ast.Constant)
        and isinstance(mod_node.value, str)
        and isinstance(name_node, ast.Constant)
        and isinstance(name_node.value, str)
    ):
        return None
    mod_str = mod_node.value
    name_str = name_node.value
    if not mod_str.startswith(package_prefix):
        return None
    target_ids = by_module_dotted.get((mod_str, name_str)) or by_qualified.get(name_str, [])
    if not target_ids:
        return None
    return target_ids[0]


def _resolve_function_ref_in_tuple(
    value,
    file_id: int,
    by_qualified: dict[str, list[int]],
    conn,
) -> int | None:
    """For a list/tuple element of shape ``(..., fn_name_reference)``
    return the matching symbol id when ``fn_name_reference`` is an
    ``ast.Name`` defined in the same file. Returns None for any other
    shape.

    Restricting same-file is intentional: cross-module function
    references typically come in via ``import``, which the regular
    extractor already records. This pass only fills the gap for inline
    references inside literal containers that the extractor misses.
    """
    if not isinstance(value, ast.Tuple):
        return None
    if not value.elts:
        return None
    last = value.elts[-1]
    if not isinstance(last, ast.Name):
        return None
    name = last.id
    candidates = by_qualified.get(name, [])
    if not candidates:
        return None
    # Prefer the candidate in the same file
    rows = conn.execute(
        f"SELECT id FROM symbols WHERE id IN ({','.join('?' * len(candidates))}) AND file_id = ?",
        (*candidates, file_id),
    ).fetchall()
    if rows:
        return int(rows[0][0])
    return None


def _record_edge(
    target_id: int,
    source_sym_id: int,
    seen: set[tuple[int, int]],
    edges_to_insert: list[tuple[int, int]],
) -> None:
    if target_id == source_sym_id:
        return
    key = (source_sym_id, target_id)
    if key in seen:
        return
    seen.add(key)
    edges_to_insert.append(key)
