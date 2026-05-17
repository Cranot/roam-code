"""Graph-aware policy clause primitives.

Pure functions used by :mod:`roam.rules.engine` to evaluate the new R18
clause types (``reachable_from``, ``imports_from``, ``clones_with``,
``tested_by``) against an indexed roam DB.

Each public ``check_*`` function:

1. Resolves the target (symbol qualified-name / file path) against the
   index. If the target cannot be resolved, returns
   ``(False, {"status": "target_not_indexed", ...})`` so the caller can
   mark the rule as ``partial_success: true`` instead of silently passing
   (Pattern 2 in CLAUDE.md — no silent fallback).
2. Performs the graph / SQL query, applying BFS guardrails
   (``max_depth``, ``max_nodes``) modelled on cmd_impact._bounded_bfs.
3. Returns ``(matches, evidence)``. ``matches=True`` means the clause is
   SATISFIED (NOT necessarily a violation — the rule wrapper applies
   ``must`` / ``must_not`` polarity).

The evidence dict always carries enough information to localise the
finding: file path + line / qname pair / hop count, plus a ``status``
field for ``target_not_indexed`` cases. Field names match
``cmd_check_rules`` violation conventions.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from roam._glob_match import matches_glob as _matches_glob

# ---------------------------------------------------------------------------
# Defaults — mirror W3.4 guardrails on cmd_impact._bounded_bfs.
# ---------------------------------------------------------------------------

DEFAULT_DEPTH = 3
DEFAULT_MAX_NODES = 100


# ``_matches_glob`` is imported from ``roam._glob_match`` at the top of
# this module (W856 hoist — was duplicated in ``rules/engine.py``;
# leaf-module home keeps both packages independent and breaks the
# ``policy → rules.engine`` cycle that the previous local copy hedged
# against).


# ---------------------------------------------------------------------------
# Target resolution helpers
# ---------------------------------------------------------------------------


def _resolve_symbol_id(conn, qname_or_name: str) -> tuple[int | None, dict | None]:
    """Resolve a symbol identifier to a single ``symbols.id``.

    Tries (in order): qualified_name exact, name exact, single-name disambig.
    Returns ``(symbol_id, row_dict_or_None)``. Returns ``(None, None)`` when
    no symbol matches.
    """
    if not qname_or_name:
        return None, None
    # 1. exact qualified_name
    row = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
        "s.line_start FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.qualified_name = ? LIMIT 1",
        (qname_or_name,),
    ).fetchone()
    if row is not None:
        return row["id"], dict(row)
    # 2. exact name
    row = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
        "s.line_start FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? LIMIT 1",
        (qname_or_name,),
    ).fetchone()
    if row is not None:
        return row["id"], dict(row)
    return None, None


def _resolve_file_id(conn, file_path: str) -> tuple[int | None, str | None]:
    """Resolve a file path (or glob fragment) to a single ``files.id``.

    Tries exact path match first, then a normalised separator match.
    Returns ``(file_id, normalised_path)`` or ``(None, None)``.
    """
    if not file_path:
        return None, None
    norm = file_path.replace("\\", "/")
    row = conn.execute(
        "SELECT id, path FROM files WHERE path = ? OR path = ? LIMIT 1",
        (norm, file_path),
    ).fetchone()
    if row is not None:
        return row["id"], row["path"]
    # Fall back to suffix match for convenience (e.g. ``db.py``).
    row = conn.execute(
        "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
        (f"%{norm}",),
    ).fetchone()
    if row is not None:
        return row["id"], row["path"]
    return None, None


# ---------------------------------------------------------------------------
# Bounded BFS — symbol-level
# ---------------------------------------------------------------------------


def _bfs_symbol_reachable(
    conn,
    start_ids: set[int],
    *,
    max_depth: int,
    max_nodes: int,
    direction: str = "forward",
) -> tuple[set[int], bool, bool]:
    """Bounded BFS over the ``edges`` table.

    Parameters
    ----------
    start_ids:
        Seed symbol IDs.
    max_depth:
        Maximum hop count from any seed. Same guardrail W3.4 applies to
        ``cmd_impact`` (default 3).
    max_nodes:
        Maximum number of distinct nodes added to the visited set.
        Default 100 — beyond this we surface a ``hit_node_cap`` flag.
    direction:
        ``forward`` follows ``source_id -> target_id`` (callees /
        imports). ``backward`` follows the reverse (callers).

    Returns
    -------
    ``(visited, hit_depth_cap, hit_node_cap)``.
    """
    visited: set[int] = set(start_ids)
    if not visited:
        return visited, False, False
    if max_depth <= 0:
        return visited, True, False

    sql_forward = "SELECT target_id FROM edges WHERE source_id = ?"
    sql_backward = "SELECT source_id FROM edges WHERE target_id = ?"
    sql = sql_forward if direction == "forward" else sql_backward

    frontier = deque((sid, 0) for sid in start_ids)
    hit_depth_cap = False
    hit_node_cap = False
    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_depth:
            # We don't expand past max_depth, but we may have queued
            # leaves at the boundary. Mark and continue draining.
            hit_depth_cap = True
            continue
        for row in conn.execute(sql, (node,)).fetchall():
            nb = row[0]
            if nb in visited:
                continue
            if len(visited) >= max_nodes:
                hit_node_cap = True
                break
            visited.add(nb)
            frontier.append((nb, depth + 1))
        if hit_node_cap:
            break
    return visited, hit_depth_cap, hit_node_cap


# ---------------------------------------------------------------------------
# Public clause checkers
# ---------------------------------------------------------------------------


def check_reachable_from(
    conn,
    entry: str,
    target_symbol: str,
    *,
    max_depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> tuple[bool, dict[str, Any]]:
    """Return True when ``target_symbol`` is reachable from ``entry``.

    Both ``entry`` and ``target_symbol`` can be a qualified name or a
    plain symbol name. Uses BFS over the ``edges`` table in the forward
    direction (callers reach callees).

    ``entry`` may also be a FILE path — in that case every top-level
    symbol defined in that file is treated as a seed (common pattern:
    ``reachable_from: "src/db/__init__.py"`` should mean "from any
    top-level symbol in db/__init__.py").
    """
    target_id, target_row = _resolve_symbol_id(conn, target_symbol)
    if target_id is None:
        return False, {
            "status": "target_not_indexed",
            "target": target_symbol,
            "reason": f"symbol '{target_symbol}' not found in index",
        }

    # Resolve seeds. Try entry as a symbol first; fall back to "all
    # symbols in this file" when the entry looks like a path.
    seed_ids: set[int] = set()
    entry_kind = "symbol"
    entry_resolved: str | None = None
    entry_id, entry_row = _resolve_symbol_id(conn, entry)
    if entry_id is not None:
        seed_ids.add(entry_id)
        entry_resolved = (entry_row or {}).get("qualified_name") or (entry_row or {}).get("name")
    else:
        file_id, norm_path = _resolve_file_id(conn, entry)
        if file_id is not None:
            entry_kind = "file"
            entry_resolved = norm_path
            rows = conn.execute(
                "SELECT id FROM symbols WHERE file_id = ? AND parent_id IS NULL",
                (file_id,),
            ).fetchall()
            seed_ids.update(r["id"] for r in rows)

    if not seed_ids:
        return False, {
            "status": "entry_not_indexed",
            "entry": entry,
            "target": target_symbol,
            "reason": f"entry '{entry}' not found in index",
        }

    visited, hit_depth_cap, hit_node_cap = _bfs_symbol_reachable(
        conn,
        seed_ids,
        max_depth=max_depth,
        max_nodes=max_nodes,
        direction="forward",
    )
    matches = target_id in visited
    evidence: dict[str, Any] = {
        "status": "ok",
        "entry": entry_resolved or entry,
        "entry_kind": entry_kind,
        "target": (target_row or {}).get("qualified_name") or (target_row or {}).get("name") or target_symbol,
        "target_file": (target_row or {}).get("file_path"),
        "target_line": (target_row or {}).get("line_start"),
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "visited_count": len(visited),
        "reachable": matches,
    }
    if hit_depth_cap:
        evidence["truncated_depth"] = True
    if hit_node_cap:
        evidence["truncated_nodes"] = True
    return matches, evidence


def check_imports_from(
    conn,
    module: str,
    target_file: str,
) -> tuple[bool, dict[str, Any]]:
    """Return True when ``target_file`` imports from ``module``.

    ``module`` may be a directory prefix (``src/legacy``), a single file
    (``src/legacy/old.py``), or a glob (``src/legacy/**``). Uses the
    indexed ``file_edges`` (kind=``imports``) table — falls back to the
    symbol-edges table when ``file_edges`` is empty for this file.
    """
    file_id, norm_path = _resolve_file_id(conn, target_file)
    if file_id is None:
        return False, {
            "status": "target_not_indexed",
            "target": target_file,
            "reason": f"file '{target_file}' not found in index",
        }

    module_norm = (module or "").replace("\\", "/")
    glob_mode = ("*" in module_norm) or module_norm.endswith("/")
    # Treat bare directory prefix (``src/legacy``) the same as ``src/legacy/**``.
    is_directory_prefix = (
        not glob_mode
        and not module_norm.endswith((".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".php"))
        and "/" in module_norm
        and module_norm
    )

    def _imp_match(other_path: str) -> bool:
        op = (other_path or "").replace("\\", "/")
        if glob_mode:
            return _matches_glob(op, module_norm)
        if is_directory_prefix:
            return op == module_norm or op.startswith(module_norm + "/")
        # Exact / suffix match for module-as-file or short module name.
        return op == module_norm or op.endswith("/" + module_norm) or op.endswith(module_norm)

    # 1. file_edges table
    rows = conn.execute(
        "SELECT f.path FROM file_edges fe JOIN files f ON fe.target_file_id = f.id WHERE fe.source_file_id = ?",
        (file_id,),
    ).fetchall()
    matches: list[str] = []
    for r in rows:
        if _imp_match(r["path"]):
            matches.append(r["path"])

    # 2. Fallback to symbol-edges (kind='import') when file_edges has no rows
    #    for this file. This makes the clause resilient to indexers that
    #    only emit symbol-level imports.
    if not matches and not rows:
        sym_rows = conn.execute(
            "SELECT DISTINCT f.path FROM edges e "
            "JOIN symbols s ON e.target_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE e.source_file_id = ? AND e.kind = 'import'",
            (file_id,),
        ).fetchall()
        for r in sym_rows:
            if _imp_match(r["path"]):
                matches.append(r["path"])

    found = len(matches) > 0
    evidence: dict[str, Any] = {
        "status": "ok",
        "target_file": norm_path,
        "module": module,
        "imports_matched": matches[:10],
        "imports_matched_count": len(matches),
        "imports_from": found,
    }
    return found, evidence


def check_clones_with(
    conn,
    symbol_a: str,
    symbol_b: str,
) -> tuple[bool, dict[str, Any]]:
    """Return True when ``symbol_a`` and ``symbol_b`` appear in the
    same ``clone_pairs`` row (or the same ``clone_clusters`` cluster).

    Match is by qualified name first, then by short ``func`` name as a
    fallback. Run ``roam clones --persist`` beforehand to populate
    ``clone_pairs``; without it this clause cannot evaluate and returns
    ``status: "not_indexed"``.
    """
    # 1. Detect whether clone_pairs has any rows — distinguishes "not run"
    #    from "ran but no match".
    try:
        any_row = conn.execute("SELECT 1 FROM clone_pairs LIMIT 1").fetchone()
    except Exception:
        any_row = None
    if any_row is None:
        return False, {
            "status": "not_indexed",
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "reason": "no clone_pairs rows — run `roam clones --persist` first",
        }

    # 2. Look for an explicit pair in either direction.
    row = conn.execute(
        "SELECT qname_a, qname_b, file_a, file_b, func_a, func_b, line_a, line_b, "
        "similarity, cluster_id FROM clone_pairs "
        "WHERE (qname_a = ? AND qname_b = ?) OR (qname_a = ? AND qname_b = ?) "
        "   OR (func_a = ? AND func_b = ?) OR (func_a = ? AND func_b = ?) "
        "LIMIT 1",
        (symbol_a, symbol_b, symbol_b, symbol_a, symbol_a, symbol_b, symbol_b, symbol_a),
    ).fetchone()
    if row is not None:
        return True, {
            "status": "ok",
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "file_a": row["file_a"],
            "file_b": row["file_b"],
            "line_a": row["line_a"],
            "line_b": row["line_b"],
            "similarity": row["similarity"],
            "cluster_id": row["cluster_id"],
            "clones_with": True,
        }

    # 3. Same cluster? (a and b might not be a direct pair but share a cluster).
    rows = conn.execute(
        "SELECT DISTINCT cluster_id FROM clone_pairs "
        "WHERE (qname_a = ? OR qname_b = ? OR func_a = ? OR func_b = ?) "
        "  AND cluster_id IS NOT NULL",
        (symbol_a, symbol_a, symbol_a, symbol_a),
    ).fetchall()
    a_clusters = {r["cluster_id"] for r in rows}
    rows = conn.execute(
        "SELECT DISTINCT cluster_id FROM clone_pairs "
        "WHERE (qname_a = ? OR qname_b = ? OR func_a = ? OR func_b = ?) "
        "  AND cluster_id IS NOT NULL",
        (symbol_b, symbol_b, symbol_b, symbol_b),
    ).fetchall()
    b_clusters = {r["cluster_id"] for r in rows}
    shared = a_clusters & b_clusters
    if shared:
        return True, {
            "status": "ok",
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "shared_cluster_id": min(shared),
            "clones_with": True,
        }

    return False, {
        "status": "ok",
        "symbol_a": symbol_a,
        "symbol_b": symbol_b,
        "clones_with": False,
    }


def check_tested_by(
    conn,
    test_pattern: str,
    target_symbol: str,
    *,
    max_depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> tuple[bool, dict[str, Any]]:
    """Return True when ``target_symbol`` is reachable from any test file
    whose path matches ``test_pattern``.

    Uses the ``files.file_role`` column to identify test files. Each
    top-level symbol in a matching test file becomes a BFS seed; the
    clause succeeds when the target symbol id is in the reachable set.
    """
    target_id, target_row = _resolve_symbol_id(conn, target_symbol)
    if target_id is None:
        return False, {
            "status": "target_not_indexed",
            "target": target_symbol,
            "reason": f"symbol '{target_symbol}' not found in index",
        }

    # Find test files. Pattern may match either role or path glob.
    rows = conn.execute(
        "SELECT id, path, file_role FROM files WHERE file_role = 'test' OR path LIKE '%test%'",
    ).fetchall()
    test_files: list[tuple[int, str]] = []
    for r in rows:
        path = r["path"]
        if _matches_glob(path, test_pattern):
            test_files.append((r["id"], path))

    if not test_files:
        return False, {
            "status": "no_tests_indexed",
            "target": target_symbol,
            "target_file": (target_row or {}).get("file_path"),
            "test_pattern": test_pattern,
            "reason": f"no test files match '{test_pattern}'",
        }

    # Seed BFS with top-level symbols (parent_id IS NULL) from each test file.
    file_ids = [fid for fid, _ in test_files]
    ph = ",".join("?" for _ in file_ids)
    seeds = conn.execute(
        f"SELECT id FROM symbols WHERE file_id IN ({ph}) AND parent_id IS NULL",
        file_ids,
    ).fetchall()
    seed_ids = {r["id"] for r in seeds}
    if not seed_ids:
        return False, {
            "status": "no_test_symbols",
            "target": target_symbol,
            "test_pattern": test_pattern,
            "test_files": [p for _, p in test_files[:5]],
            "reason": "matching test files contain no indexed symbols",
        }

    visited, hit_depth_cap, hit_node_cap = _bfs_symbol_reachable(
        conn,
        seed_ids,
        max_depth=max_depth,
        max_nodes=max_nodes,
        direction="forward",
    )
    matches = target_id in visited
    evidence: dict[str, Any] = {
        "status": "ok",
        "target": (target_row or {}).get("qualified_name") or (target_row or {}).get("name") or target_symbol,
        "target_file": (target_row or {}).get("file_path"),
        "target_line": (target_row or {}).get("line_start"),
        "test_pattern": test_pattern,
        "test_files_matched": len(test_files),
        "test_files": [p for _, p in test_files[:5]],
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "tested_by": matches,
    }
    if hit_depth_cap:
        evidence["truncated_depth"] = True
    if hit_node_cap:
        evidence["truncated_nodes"] = True
    return matches, evidence


# ---------------------------------------------------------------------------
# Clause dispatcher
# ---------------------------------------------------------------------------

#: Mapping of clause-name → callable. Each callable signature is
#: ``(conn, clause_arg, target, **kwargs) -> (matches, evidence)``.
#: This is the closed enumeration referenced from
#: :func:`roam.rules.engine._evaluate_graph_clause` — adding a new clause
#: type means adding one entry here AND one in cmd_rules-validate.
SUPPORTED_CLAUSES: tuple[str, ...] = (
    "reachable_from",
    "imports_from",
    "clones_with",
    "tested_by",
)


def evaluate_clause(
    clause_name: str,
    clause_arg: str,
    *,
    conn,
    target_symbol: str | None = None,
    target_file: str | None = None,
    max_depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch a single named clause to the right ``check_*`` function.

    Returns ``(matches, evidence)``. ``matches=True`` means the clause is
    SATISFIED — the caller decides whether that maps to a violation
    (``must_not``) or a pass (``must``).
    """
    if clause_name not in SUPPORTED_CLAUSES:
        return False, {
            "status": "unsupported_clause",
            "clause": clause_name,
            "supported": list(SUPPORTED_CLAUSES),
        }

    if clause_name == "reachable_from":
        return check_reachable_from(
            conn,
            entry=clause_arg,
            target_symbol=target_symbol or "",
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    if clause_name == "imports_from":
        return check_imports_from(
            conn,
            module=clause_arg,
            target_file=target_file or "",
        )
    if clause_name == "clones_with":
        return check_clones_with(
            conn,
            symbol_a=target_symbol or "",
            symbol_b=clause_arg,
        )
    if clause_name == "tested_by":
        return check_tested_by(
            conn,
            test_pattern=clause_arg,
            target_symbol=target_symbol or "",
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    return False, {"status": "unsupported_clause", "clause": clause_name}
