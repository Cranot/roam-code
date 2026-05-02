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
        if package_prefix not in source:
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue

        source_sym_id = file_first_symbol.get(file_id)
        if source_sym_id is None:
            continue

        for node in ast.walk(tree):
            # Top-level assignment whose value is a Dict literal.
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for value in node.value.values:
                # Each value must be a tuple of two string constants.
                if not isinstance(value, ast.Tuple):
                    continue
                if len(value.elts) != 2:
                    continue
                mod_node, name_node = value.elts
                if not (
                    isinstance(mod_node, ast.Constant)
                    and isinstance(mod_node.value, str)
                    and isinstance(name_node, ast.Constant)
                    and isinstance(name_node.value, str)
                ):
                    continue
                mod_str = mod_node.value
                name_str = name_node.value
                if not mod_str.startswith(package_prefix):
                    continue
                # Look up the target symbol
                target_ids = by_module_dotted.get((mod_str, name_str)) or by_qualified.get(name_str, [])
                if not target_ids:
                    continue
                # Pick the first match (most-specific module path
                # already filtered to one entry typically).
                target_id = target_ids[0]
                if target_id == source_sym_id:
                    continue
                key = (source_sym_id, target_id)
                if key in seen:
                    continue
                seen.add(key)
                edges_to_insert.append(key)

    with conn:
        conn.execute("DELETE FROM edges WHERE kind = 'dispatch'")
        if edges_to_insert:
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'dispatch')",
                edges_to_insert,
            )
    return len(edges_to_insert)
