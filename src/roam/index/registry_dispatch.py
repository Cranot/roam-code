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

    # Pre-load symbol lookup keyed by qualified_name AND short name.
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
        # Map (module_dotted_from_path, name) for the
        # ``("roam.commands.cmd_x", "fn")`` shape — Python module path
        # derived from the file path.
        path = (s["file_path"] or "").replace("\\", "/")
        if path.endswith(".py"):
            mod = path[:-3].replace("/", ".")
            # Trim ``src.`` prefix (project-relative paths)
            if mod.startswith("src."):
                mod = mod[len("src.") :]
            by_module_dotted.setdefault((mod, s["name"]), []).append(s["id"])

    # File path → module symbol id (for the edge source). The dispatch
    # edge originates from the file's module-level "container" — there
    # is no module-level symbol per se in roam's index, so use the
    # first symbol in the file as a synthetic source. If no symbol
    # exists in the file, skip.
    file_first_symbol: dict[int, int] = {}
    for row in conn.execute("SELECT file_id, MIN(id) AS first_id FROM symbols GROUP BY file_id").fetchall():
        if row["first_id"] is not None:
            file_first_symbol[row["file_id"]] = row["first_id"]

    edges_to_insert: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    for r in rows:
        file_id = r["file_id"]
        path = r["file_path"]
        try:
            with open(path, encoding="utf-8", errors="replace") as fp:
                source = fp.read()
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        # Cheap prefilter: skip files that contain neither the package
        # prefix (Shape A signal) nor any list/tuple literal of tuples
        # (Shape B signal). The latter is hard to grep for, so just
        # check ``[(`` which is a strong hint.
        if package_prefix not in source and "[(" not in source and "[\n" not in source:
            continue

        source_sym_id = file_first_symbol.get(file_id)
        if source_sym_id is None:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value

            # Shape A: ``_NAME = {"key": ("module.path", "fn_name"), ...}``
            # Lazy-import dispatch (cli._COMMANDS pattern).
            if isinstance(value, ast.Dict):
                for v in value.values:
                    target_id = _resolve_string_pair_tuple(v, package_prefix, by_module_dotted, by_qualified)
                    if target_id is None:
                        continue
                    _record_edge(target_id, source_sym_id, seen, edges_to_insert)

            # Shape B: ``_NAME = [(..., fn_ref), ...]`` — list/tuple of
            # tuples whose last element is a ``Name`` (a Python
            # function reference). Catches the
            # ``_PYTHON_IDIOM_DETECTORS = [("py-django-n1", "...",
            # detect_django_n1), ...]`` pattern. Function references
            # are looked up in the same file's symbol table.
            elif isinstance(value, (ast.List, ast.Tuple)):
                for v in value.elts:
                    target_id = _resolve_function_ref_in_tuple(v, file_id, by_qualified, conn)
                    if target_id is None:
                        continue
                    _record_edge(target_id, source_sym_id, seen, edges_to_insert)

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
