"""Data-gathering helpers for the context command.

Extracted from cmd_context.py to reduce file size.  These functions
query the index DB and return plain dicts — no rendering or CLI I/O.
"""
from __future__ import annotations

from collections import deque

from roam.db.connection import batched_in
from roam.output.formatter import loc
from roam.commands.changed_files import is_test_file
from roam.commands.graph_helpers import build_forward_adj


# ---------------------------------------------------------------------------
# Per-symbol metric fetchers
# ---------------------------------------------------------------------------

def get_symbol_metrics(conn, sym_id):
    """Fetch symbol_metrics row for a symbol, or None."""
    row = conn.execute(
        "SELECT * FROM symbol_metrics WHERE symbol_id = ?", (sym_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "cognitive_complexity": row["cognitive_complexity"],
        "nesting_depth": row["nesting_depth"],
        "param_count": row["param_count"],
        "line_count": row["line_count"],
        "return_count": row["return_count"],
        "bool_op_count": row["bool_op_count"],
        "callback_depth": row["callback_depth"],
    }


def get_graph_metrics(conn, sym_id):
    """Fetch graph_metrics row for a symbol, or None."""
    row = conn.execute(
        "SELECT * FROM graph_metrics WHERE symbol_id = ?", (sym_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "pagerank": round(row["pagerank"] or 0, 6),
        "in_degree": row["in_degree"] or 0,
        "out_degree": row["out_degree"] or 0,
        "betweenness": round(row["betweenness"] or 0, 6),
    }


def get_file_churn(conn, file_path):
    """Fetch git churn stats for the file containing the symbol."""
    frow = conn.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    if frow is None:
        return None
    stats = conn.execute(
        "SELECT * FROM file_stats WHERE file_id = ?", (frow["id"],)
    ).fetchone()
    if stats is None:
        return None
    return {
        "commit_count": stats["commit_count"] or 0,
        "total_churn": stats["total_churn"] or 0,
        "distinct_authors": stats["distinct_authors"] or 0,
    }


def get_coupling(conn, file_path, limit=10):
    """Fetch temporal coupling partners for the symbol's file."""
    frow = conn.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    if frow is None:
        return []
    fid = frow["id"]

    fstats = conn.execute(
        "SELECT commit_count FROM file_stats WHERE file_id = ?", (fid,)
    ).fetchone()
    file_commits = (fstats["commit_count"] or 1) if fstats else 1

    partners = conn.execute(
        "SELECT f.path, gc.cochange_count, "
        "CASE WHEN gc.file_id_a = ? THEN gc.file_id_b "
        "     ELSE gc.file_id_a END as partner_fid "
        "FROM git_cochange gc "
        "JOIN files f ON ("
        "  CASE WHEN gc.file_id_a = ? THEN gc.file_id_b "
        "       ELSE gc.file_id_a END"
        ") = f.id "
        "WHERE gc.file_id_a = ? OR gc.file_id_b = ? "
        "ORDER BY gc.cochange_count DESC LIMIT ?",
        (fid, fid, fid, fid, limit),
    ).fetchall()

    results = []
    for p in partners:
        pstats = conn.execute(
            "SELECT commit_count FROM file_stats WHERE file_id = ?",
            (p["partner_fid"],)
        ).fetchone()
        partner_commits = (pstats["commit_count"] or 1) if pstats else 1
        avg = (file_commits + partner_commits) / 2
        strength = round(p["cochange_count"] / avg, 2) if avg > 0 else 0
        results.append({
            "path": p["path"],
            "cochange_count": p["cochange_count"],
            "strength": strength,
        })
    return results


def get_affected_tests_bfs(conn, sym_id, max_hops=8):
    """BFS reverse-edge walk to find test symbols that transitively depend
    on the target symbol."""
    visited = {sym_id: (0, None)}
    queue = deque([(sym_id, 0, None)])

    while queue:
        current_id, hops, via = queue.popleft()
        if hops >= max_hops:
            continue
        callers = conn.execute(
            "SELECT e.source_id, s.name "
            "FROM edges e JOIN symbols s ON e.source_id = s.id "
            "WHERE e.target_id = ?",
            (current_id,),
        ).fetchall()
        for row in callers:
            cid = row["source_id"]
            new_hops = hops + 1
            new_via = via if via else row["name"]
            if cid not in visited or visited[cid][0] > new_hops:
                visited[cid] = (new_hops, new_via)
                queue.append((cid, new_hops, new_via))

    caller_ids = [sid for sid in visited if sid != sym_id]
    if not caller_ids:
        return []

    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        caller_ids,
    )

    tests = []
    seen = set()
    for r in rows:
        if not is_test_file(r["file_path"]):
            continue
        key = (r["file_path"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        hops, via = visited[r["id"]]
        tests.append({
            "file": r["file_path"],
            "symbol": r["name"],
            "kind": "DIRECT" if hops == 1 else "TRANSITIVE",
            "hops": hops,
            "via": via if hops > 1 else None,
        })

    tests.sort(key=lambda t: (
        0 if t["kind"] == "DIRECT" else 1, t["hops"], t["file"],
    ))
    return tests


def get_blast_radius(conn, sym_id):
    """Compute downstream dependents count via BFS on reverse edges."""
    visited = {sym_id}
    queue = deque([sym_id])
    while queue:
        current = queue.popleft()
        callers = conn.execute(
            "SELECT source_id FROM edges WHERE target_id = ?", (current,)
        ).fetchall()
        for row in callers:
            cid = row["source_id"]
            if cid not in visited:
                visited.add(cid)
                queue.append(cid)

    if len(visited) <= 1:
        return {"dependent_symbols": 0, "dependent_files": 0}

    dep_ids = [sid for sid in visited if sid != sym_id]
    file_rows = batched_in(
        conn,
        "SELECT DISTINCT f.path FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        dep_ids,
    )

    return {
        "dependent_symbols": len(dep_ids),
        "dependent_files": len(file_rows),
    }


def get_cluster_info(conn, sym_id):
    """Fetch cluster membership for a symbol."""
    row = conn.execute(
        "SELECT cluster_id, cluster_label FROM clusters WHERE symbol_id = ?",
        (sym_id,),
    ).fetchone()
    if row is None:
        return None
    members = conn.execute(
        "SELECT s.name, s.kind FROM clusters c "
        "JOIN symbols s ON c.symbol_id = s.id "
        "WHERE c.cluster_id = ? ORDER BY s.name LIMIT 8",
        (row["cluster_id"],),
    ).fetchall()
    size = conn.execute(
        "SELECT COUNT(*) FROM clusters WHERE cluster_id = ?",
        (row["cluster_id"],),
    ).fetchone()[0]
    return {
        "cluster_id": row["cluster_id"],
        "cluster_label": row["cluster_label"] or f"cluster-{row['cluster_id']}",
        "cluster_size": size,
        "top_members": [
            {"name": m["name"], "kind": m["kind"]} for m in members
        ],
    }


def get_similar_symbols(conn, sym, limit=10):
    """Find symbols of the same kind in the same parent module."""
    file_path = sym["file_path"].replace("\\", "/")
    dir_path = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
    if not dir_path:
        return []

    pattern = dir_path + "/%"
    rows = conn.execute(
        "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, s.signature "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = ? AND s.is_exported = 1 AND s.id != ? "
        "AND f.path LIKE ? "
        "ORDER BY f.path, s.line_start LIMIT ?",
        (sym["kind"], sym["id"], pattern, limit),
    ).fetchall()

    return [
        {
            "name": r["qualified_name"] or r["name"],
            "kind": r["kind"],
            "location": loc(r["file_path"], r["line_start"]),
            "signature": r["signature"] or "",
        }
        for r in rows
    ]


def get_entry_points_reaching(conn, sym_id, limit=5):
    """Find entry points (in_degree=0) that can reach this symbol via
    forward BFS."""
    entry_rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, "
        "f.path as file_path, s.line_start, gm.out_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE gm.in_degree = 0 "
        "AND s.kind IN ('function', 'method', 'class') "
        "ORDER BY gm.out_degree DESC LIMIT 50"
    ).fetchall()

    if not entry_rows:
        return []

    adj = build_forward_adj(conn)

    results = []
    for ep in entry_rows:
        visited = {ep["id"]}
        queue = deque([ep["id"]])
        found = False
        while queue:
            current = queue.popleft()
            for neighbor in adj.get(current, ()):
                if neighbor == sym_id:
                    found = True
                    break
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
            if found:
                break
        if found:
            results.append({
                "name": ep["qualified_name"] or ep["name"],
                "kind": ep["kind"],
                "location": loc(ep["file_path"], ep["line_start"]),
            })
            if len(results) >= limit:
                break

    return results


def get_file_context(conn, file_id, sym_id):
    """Get all exported symbols in the same file."""
    rows = conn.execute(
        "SELECT name, kind, line_start, signature, docstring "
        "FROM symbols WHERE file_id = ? AND is_exported = 1 AND id != ? "
        "ORDER BY line_start",
        (file_id, sym_id),
    ).fetchall()
    return [
        {
            "name": r["name"],
            "kind": r["kind"],
            "line": r["line_start"],
            "has_docstring": bool(r["docstring"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Task-mode: gather extra context based on task intent
# ---------------------------------------------------------------------------

def gather_task_extras(conn, sym, ctx_data, task):
    """Gather task-specific extra context data.

    Returns a dict of extra sections keyed by section name.  Keys
    prefixed with ``_`` are rendering hints (e.g. ``_hide_callees``).
    """
    sym_id = sym["id"]
    file_path = sym["file_path"]
    extras = {}

    if task == "refactor":
        extras["complexity"] = get_symbol_metrics(conn, sym_id)
        extras["graph_centrality"] = get_graph_metrics(conn, sym_id)
        extras["coupling"] = get_coupling(conn, file_path, limit=10)
        extras["_hide_callees"] = True

    elif task == "debug":
        extras["complexity"] = get_symbol_metrics(conn, sym_id)
        extras["affected_tests"] = get_affected_tests_bfs(conn, sym_id)

    elif task == "extend":
        extras["similar_symbols"] = get_similar_symbols(conn, sym, limit=10)
        extras["entry_points_reaching"] = get_entry_points_reaching(
            conn, sym_id, limit=5,
        )
        extras["graph_centrality"] = get_graph_metrics(conn, sym_id)

    elif task == "review":
        extras["complexity"] = get_symbol_metrics(conn, sym_id)
        extras["git_churn"] = get_file_churn(conn, file_path)
        extras["affected_tests"] = get_affected_tests_bfs(conn, sym_id)
        extras["coupling"] = get_coupling(conn, file_path, limit=10)
        extras["blast_radius"] = get_blast_radius(conn, sym_id)
        extras["graph_centrality"] = get_graph_metrics(conn, sym_id)

    elif task == "understand":
        # sqlite3.Row lacks .get() — use try/except for optional fields
        try:
            extras["docstring"] = sym["docstring"] or None
        except (KeyError, IndexError):
            extras["docstring"] = None
        extras["cluster"] = get_cluster_info(conn, sym_id)
        extras["graph_centrality"] = get_graph_metrics(conn, sym_id)
        try:
            fid = sym["file_id"]
        except (KeyError, IndexError):
            fid = conn.execute(
                "SELECT file_id FROM symbols WHERE id = ?", (sym_id,)
            ).fetchone()[0]
        extras["file_context"] = get_file_context(conn, fid, sym_id)
        extras["_limit_callers"] = 5
        extras["_limit_callees"] = 5

    return extras


# ---------------------------------------------------------------------------
# Single-symbol context gathering (reusable for batch mode)
# ---------------------------------------------------------------------------

def gather_symbol_context(conn, sym):
    """Gather callers, callees, tests, siblings, and files_to_read for a symbol.

    Returns a dict with all context fields.
    """
    sym_id = sym["id"]
    line_start = sym["line_start"]
    line_end = sym["line_end"] or line_start

    # --- Callers ---
    callers = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "f.path as file_path, e.kind as edge_kind, e.line as edge_line "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id = ? "
        "ORDER BY f.path, s.line_start",
        (sym_id,),
    ).fetchall()

    # --- Callees ---
    callees = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "f.path as file_path, e.kind as edge_kind, e.line as edge_line "
        "FROM edges e "
        "JOIN symbols s ON e.target_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.source_id = ? "
        "ORDER BY f.path, s.line_start",
        (sym_id,),
    ).fetchall()

    # --- Split callers into tests vs non-tests ---
    test_callers = [c for c in callers if is_test_file(c["file_path"])]
    non_test_callers = [c for c in callers if not is_test_file(c["file_path"])]

    # Rank callers by PageRank for high-fan symbols
    if len(non_test_callers) > 10:
        caller_ids = [c["id"] for c in non_test_callers]
        pr_rows = batched_in(
            conn,
            "SELECT symbol_id, pagerank FROM graph_metrics "
            "WHERE symbol_id IN ({ph})",
            caller_ids,
        )
        pr_map = {r["symbol_id"]: r["pagerank"] or 0 for r in pr_rows}
        non_test_callers = sorted(
            non_test_callers,
            key=lambda c: -pr_map.get(c["id"], 0),
        )

    # --- Test files that import the symbol's file ---
    sym_file_row = conn.execute(
        "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
    ).fetchone()
    test_importers = []
    if sym_file_row:
        importers = conn.execute(
            "SELECT f.path, fe.symbol_count "
            "FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id = ?",
            (sym_file_row["id"],),
        ).fetchall()
        test_importers = [r for r in importers if is_test_file(r["path"])]

    # --- Siblings (other exports in same file) ---
    siblings = conn.execute(
        "SELECT name, kind, line_start FROM symbols "
        "WHERE file_id = ? AND is_exported = 1 AND id != ? "
        "ORDER BY line_start",
        (sym["file_id"], sym_id),
    ).fetchall()

    # --- Build "files to read" list (capped for high-fan symbols) ---
    _MAX_CALLER_FILES = 10
    _MAX_CALLEE_FILES = 5
    _MAX_TEST_FILES = 5
    skipped_callers = 0
    skipped_callees = 0

    files_to_read = [{
        "path": sym["file_path"],
        "start": line_start,
        "end": line_end,
        "reason": "definition",
    }]
    seen = {sym["file_path"]}
    caller_files = 0
    for c in non_test_callers:
        if c["file_path"] not in seen:
            if caller_files >= _MAX_CALLER_FILES:
                skipped_callers += 1
                continue
            seen.add(c["file_path"])
            files_to_read.append({
                "path": c["file_path"],
                "start": c["line_start"],
                "end": c["line_end"] or c["line_start"],
                "reason": "caller",
            })
            caller_files += 1
    callee_files = 0
    for c in callees:
        if c["file_path"] not in seen:
            if callee_files >= _MAX_CALLEE_FILES:
                skipped_callees += 1
                continue
            seen.add(c["file_path"])
            files_to_read.append({
                "path": c["file_path"],
                "start": c["line_start"],
                "end": c["line_end"] or c["line_start"],
                "reason": "callee",
            })
            callee_files += 1
    test_files = 0
    for t in test_callers:
        if t["file_path"] not in seen and test_files < _MAX_TEST_FILES:
            seen.add(t["file_path"])
            files_to_read.append({
                "path": t["file_path"],
                "start": t["line_start"],
                "end": t["line_end"] or t["line_start"],
                "reason": "test",
            })
            test_files += 1
    for ti in test_importers:
        if ti["path"] not in seen and test_files < _MAX_TEST_FILES:
            seen.add(ti["path"])
            files_to_read.append({
                "path": ti["path"], "start": 1, "end": None,
                "reason": "test",
            })
            test_files += 1

    return {
        "sym": sym,
        "line_start": line_start,
        "line_end": line_end,
        "callers": callers,
        "callees": callees,
        "non_test_callers": non_test_callers,
        "test_callers": test_callers,
        "test_importers": test_importers,
        "siblings": siblings,
        "files_to_read": files_to_read,
        "skipped_callers": skipped_callers,
        "skipped_callees": skipped_callees,
    }


# ---------------------------------------------------------------------------
# Batch mode: shared callers + information density scoring
# ---------------------------------------------------------------------------

def batch_context(conn, contexts):
    """Compute batch-mode context for multiple symbols.

    Returns shared_callers, shared_callees, and density-scored files_to_read.
    """
    caller_id_sets = []
    callee_id_sets = []
    query_sym_ids = set()
    for ctx_data in contexts:
        query_sym_ids.add(ctx_data["sym"]["id"])
        caller_id_sets.append({c["id"] for c in ctx_data["non_test_callers"]})
        callee_id_sets.append({c["id"] for c in ctx_data["callees"]})

    shared_caller_ids = set.intersection(*caller_id_sets) if caller_id_sets else set()
    shared_callee_ids = set.intersection(*callee_id_sets) if callee_id_sets else set()

    def _resolve_ids(ids):
        if not ids:
            return []
        return batched_in(
            conn,
            "SELECT s.name, s.kind, f.path as file_path, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph}) "
            "ORDER BY f.path, s.line_start",
            list(ids),
        )

    shared_callers = _resolve_ids(shared_caller_ids)
    shared_callees = _resolve_ids(shared_callee_ids)

    file_reasons = {}
    file_edges_to_query = {}
    for ctx_data in contexts:
        for f in ctx_data["files_to_read"]:
            path = f["path"]
            file_reasons.setdefault(path, set()).add(f["reason"])
            if f["reason"] in ("caller", "callee"):
                file_edges_to_query[path] = file_edges_to_query.get(path, 0) + 1

    file_total_edges = {}
    all_paths = list(file_reasons.keys())
    for path in all_paths:
        frow = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if frow:
            total = conn.execute(
                "SELECT COUNT(*) FROM edges e JOIN symbols s ON e.source_id = s.id "
                "WHERE s.file_id = ?",
                (frow["id"],),
            ).fetchone()[0]
            file_total_edges[path] = max(total, 1)

    scored_files = []
    for path in all_paths:
        edges_to_query = file_edges_to_query.get(path, 0)
        total_edges = file_total_edges.get(path, 1)
        relevance = round(edges_to_query / total_edges, 3) if total_edges > 0 else 0.0

        reasons = file_reasons[path]
        if "definition" in reasons:
            relevance = 1.0

        scored_files.append({
            "path": path,
            "reasons": sorted(reasons),
            "relevance": relevance,
        })

    scored_files.sort(key=lambda x: -x["relevance"])
    return shared_callers, shared_callees, scored_files
