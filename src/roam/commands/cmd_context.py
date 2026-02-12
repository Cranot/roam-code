"""Get the minimal context needed to safely modify a symbol."""

from collections import defaultdict, deque

import click

from roam.db.connection import open_db
from roam.db.queries import FILE_BY_PATH
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol
from roam.commands.changed_files import is_test_file


# ---------------------------------------------------------------------------
# Task-mode data gatherers
# ---------------------------------------------------------------------------

_TASK_CHOICES = ["refactor", "debug", "extend", "review", "understand"]


def _get_symbol_metrics(conn, sym_id):
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


def _get_graph_metrics(conn, sym_id):
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


def _get_file_churn(conn, file_path):
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


def _get_coupling(conn, file_path, limit=10):
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


def _get_affected_tests_bfs(conn, sym_id, max_hops=8):
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

    ph = ",".join("?" for _ in caller_ids)
    rows = conn.execute(
        f"SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
        f"FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE s.id IN ({ph})",
        caller_ids,
    ).fetchall()

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


def _get_blast_radius(conn, sym_id):
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
    ph = ",".join("?" for _ in dep_ids)
    file_rows = conn.execute(
        f"SELECT DISTINCT f.path FROM symbols s "
        f"JOIN files f ON s.file_id = f.id "
        f"WHERE s.id IN ({ph})",
        dep_ids,
    ).fetchall()

    return {
        "dependent_symbols": len(dep_ids),
        "dependent_files": len(file_rows),
    }


def _get_cluster_info(conn, sym_id):
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


def _get_similar_symbols(conn, sym, limit=10):
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


def _get_entry_points_reaching(conn, sym_id, limit=5):
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

    # Build forward adjacency list
    adj = {}
    for row in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj.setdefault(row["source_id"], set()).add(row["target_id"])

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


def _get_file_context(conn, file_id, sym_id):
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

def _gather_task_extras(conn, sym, ctx_data, task):
    """Gather task-specific extra context data.

    Returns a dict of extra sections keyed by section name.  Keys
    prefixed with ``_`` are rendering hints (e.g. ``_hide_callees``).
    """
    sym_id = sym["id"]
    file_path = sym["file_path"]
    extras = {}

    if task == "refactor":
        extras["complexity"] = _get_symbol_metrics(conn, sym_id)
        extras["graph_centrality"] = _get_graph_metrics(conn, sym_id)
        extras["coupling"] = _get_coupling(conn, file_path, limit=10)
        extras["_hide_callees"] = True

    elif task == "debug":
        extras["complexity"] = _get_symbol_metrics(conn, sym_id)
        extras["affected_tests"] = _get_affected_tests_bfs(conn, sym_id)

    elif task == "extend":
        extras["similar_symbols"] = _get_similar_symbols(conn, sym, limit=10)
        extras["entry_points_reaching"] = _get_entry_points_reaching(
            conn, sym_id, limit=5,
        )
        extras["graph_centrality"] = _get_graph_metrics(conn, sym_id)

    elif task == "review":
        extras["complexity"] = _get_symbol_metrics(conn, sym_id)
        extras["git_churn"] = _get_file_churn(conn, file_path)
        extras["affected_tests"] = _get_affected_tests_bfs(conn, sym_id)
        extras["coupling"] = _get_coupling(conn, file_path, limit=10)
        extras["blast_radius"] = _get_blast_radius(conn, sym_id)
        extras["graph_centrality"] = _get_graph_metrics(conn, sym_id)

    elif task == "understand":
        # sqlite3.Row lacks .get() — use try/except for optional fields
        try:
            extras["docstring"] = sym["docstring"] or None
        except (KeyError, IndexError):
            extras["docstring"] = None
        extras["cluster"] = _get_cluster_info(conn, sym_id)
        extras["graph_centrality"] = _get_graph_metrics(conn, sym_id)
        try:
            fid = sym["file_id"]
        except (KeyError, IndexError):
            fid = conn.execute(
                "SELECT file_id FROM symbols WHERE id = ?", (sym_id,)
            ).fetchone()[0]
        extras["file_context"] = _get_file_context(conn, fid, sym_id)
        extras["_limit_callers"] = 5
        extras["_limit_callees"] = 5

    return extras


# ---------------------------------------------------------------------------
# Single-symbol context gathering (reusable for batch mode)
# ---------------------------------------------------------------------------

def _gather_symbol_context(conn, sym):
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
        ph = ",".join("?" for _ in caller_ids)
        pr_rows = conn.execute(
            f"SELECT symbol_id, pagerank FROM graph_metrics "
            f"WHERE symbol_id IN ({ph})",
            caller_ids,
        ).fetchall()
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

def _batch_context(conn, contexts):
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
        ph = ",".join("?" for _ in ids)
        return conn.execute(
            f"SELECT s.name, s.kind, f.path as file_path, s.line_start "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({ph}) "
            f"ORDER BY f.path, s.line_start",
            list(ids),
        ).fetchall()

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


# ---------------------------------------------------------------------------
# Task-mode text output helpers
# ---------------------------------------------------------------------------

def _render_complexity_text(metrics):
    if not metrics:
        return
    click.echo("Complexity:")
    click.echo(
        f"  cognitive={metrics['cognitive_complexity']:.0f}  "
        f"nesting={metrics['nesting_depth']}  "
        f"params={metrics['param_count']}  "
        f"lines={metrics['line_count']}  "
        f"returns={metrics['return_count']}  "
        f"bool_ops={metrics['bool_op_count']}  "
        f"callbacks={metrics['callback_depth']}"
    )
    click.echo()


def _render_graph_centrality_text(metrics):
    if not metrics:
        return
    click.echo("Graph centrality:")
    click.echo(
        f"  pagerank={metrics['pagerank']:.6f}  "
        f"in_degree={metrics['in_degree']}  "
        f"out_degree={metrics['out_degree']}  "
        f"betweenness={metrics['betweenness']:.6f}"
    )
    click.echo()


def _render_churn_text(churn):
    if not churn:
        return
    click.echo("Git churn (file):")
    click.echo(
        f"  commits={churn['commit_count']}  "
        f"total_churn={churn['total_churn']}  "
        f"authors={churn['distinct_authors']}"
    )
    click.echo()


def _render_coupling_text(coupling):
    if not coupling:
        return
    click.echo(f"Temporal coupling ({len(coupling)} partners):")
    rows = [
        [c["path"], f"{c['strength']:.0%}", str(c["cochange_count"])]
        for c in coupling[:10]
    ]
    click.echo(format_table(["file", "strength", "co-changes"], rows))
    click.echo()


def _render_affected_tests_text(tests):
    if not tests:
        click.echo("Affected tests: (none found via BFS)")
        click.echo()
        return
    direct = sum(1 for t in tests if t["kind"] == "DIRECT")
    transitive = sum(1 for t in tests if t["kind"] == "TRANSITIVE")
    click.echo(f"Affected tests ({direct} direct, {transitive} transitive):")
    for t in tests[:15]:
        via_str = f" via {t['via']}" if t.get("via") else ""
        hops = t["hops"]
        click.echo(
            f"  {t['kind']:<12s} {t['file']}::{t['symbol']}  "
            f"({hops} hop{'s' if hops != 1 else ''}{via_str})"
        )
    if len(tests) > 15:
        click.echo(f"  (+{len(tests) - 15} more)")
    click.echo()


def _render_blast_radius_text(blast):
    if not blast:
        return
    click.echo("Blast radius:")
    click.echo(
        f"  {blast['dependent_symbols']} dependent symbols in "
        f"{blast['dependent_files']} files"
    )
    click.echo()


def _render_cluster_text(cluster):
    if not cluster:
        return
    click.echo(
        f"Cluster: {cluster['cluster_label']} "
        f"({cluster['cluster_size']} symbols)"
    )
    names = ", ".join(m["name"] for m in cluster["top_members"][:6])
    if cluster["cluster_size"] > 6:
        names += f" +{cluster['cluster_size'] - 6} more"
    click.echo(f"  members: {names}")
    click.echo()


def _render_similar_symbols_text(similar):
    if not similar:
        return
    click.echo(f"Similar symbols ({len(similar)}):")
    rows = [
        [abbrev_kind(s["kind"]), s["name"], s["location"]]
        for s in similar[:10]
    ]
    click.echo(format_table(["kind", "name", "location"], rows))
    click.echo()


def _render_entry_points_text(entries):
    if not entries:
        return
    click.echo(f"Entry points reaching this ({len(entries)}):")
    rows = [
        [abbrev_kind(e["kind"]), e["name"], e["location"]]
        for e in entries
    ]
    click.echo(format_table(["kind", "name", "location"], rows))
    click.echo()


def _render_file_context_text(file_context):
    if not file_context:
        return
    click.echo(f"File context ({len(file_context)} other exports):")
    for fc in file_context[:15]:
        doc = " [documented]" if fc["has_docstring"] else ""
        click.echo(
            f"  {abbrev_kind(fc['kind'])}  {fc['name']}  L{fc['line']}{doc}"
        )
    if len(file_context) > 15:
        click.echo(f"  (+{len(file_context) - 15} more)")
    click.echo()


# ---------------------------------------------------------------------------
# Task-mode output: text
# ---------------------------------------------------------------------------

def _output_task_single_text(c, task, extras):
    """Render task-mode text output for a single symbol."""
    sym = c["sym"]
    line_start = c["line_start"]
    non_test_callers = c["non_test_callers"]
    callees = c["callees"]
    test_callers = c["test_callers"]
    test_importers = c["test_importers"]
    siblings = c["siblings"]
    files_to_read = c["files_to_read"]
    skipped_callers = c["skipped_callers"]
    skipped_callees = c["skipped_callees"]

    hide_callees = extras.get("_hide_callees", False)
    limit_callers = extras.get("_limit_callers")
    limit_callees = extras.get("_limit_callees")

    sig = sym["signature"] or ""
    click.echo(f"=== Context for: {sym['name']} (task={task}) ===")
    click.echo(
        f"{abbrev_kind(sym['kind'])}  "
        f"{sym['qualified_name'] or sym['name']}"
        f"{'  ' + sig if sig else ''}  "
        f"{loc(sym['file_path'], line_start)}"
    )
    click.echo()

    # understand: show docstring first
    if task == "understand" and extras.get("docstring"):
        click.echo("Docstring:")
        for line in extras["docstring"].strip().splitlines()[:10]:
            click.echo(f"  {line}")
        click.echo()

    # Callers
    caller_cap = limit_callers or 20
    if non_test_callers:
        click.echo(f"Callers ({len(non_test_callers)}):")
        rows = []
        for cr in non_test_callers[:caller_cap]:
            rows.append([
                abbrev_kind(cr["kind"]), cr["name"],
                loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                cr["edge_kind"] or "",
            ])
        click.echo(format_table(["kind", "name", "location", "edge"], rows))
        if len(non_test_callers) > caller_cap:
            click.echo(f"  (+{len(non_test_callers) - caller_cap} more)")
        click.echo()
    else:
        click.echo("Callers: (none)")
        click.echo()

    # Callees (hidden for refactor, limited for understand)
    if not hide_callees:
        callee_cap = limit_callees or 15
        if callees:
            click.echo(f"Callees ({len(callees)}):")
            rows = []
            for ce in callees[:callee_cap]:
                rows.append([
                    abbrev_kind(ce["kind"]), ce["name"],
                    loc(ce["file_path"], ce["line_start"]),
                    ce["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(callees) > callee_cap:
                click.echo(f"  (+{len(callees) - callee_cap} more)")
            click.echo()
        else:
            click.echo("Callees: (none)")
            click.echo()

    # Tests (default for non-review/debug; those use BFS affected_tests)
    if task not in ("review", "debug"):
        if test_callers or test_importers:
            click.echo(
                f"Tests ({len(test_callers)} direct, "
                f"{len(test_importers)} file-level):"
            )
            for t in test_callers:
                click.echo(
                    f"  {abbrev_kind(t['kind'])}  {t['name']}  "
                    f"{loc(t['file_path'], t['line_start'])}"
                )
            for ti in test_importers:
                click.echo(f"  file  {ti['path']}")
        else:
            click.echo("Tests: (none)")
        click.echo()

    # Siblings (shown for refactor, understand)
    if task in ("refactor", "understand") and siblings:
        click.echo(f"Siblings ({len(siblings)} exports in same file):")
        for s in siblings[:10]:
            click.echo(f"  {abbrev_kind(s['kind'])}  {s['name']}")
        if len(siblings) > 10:
            click.echo(f"  (+{len(siblings) - 10} more)")
        click.echo()

    # Task-specific extra sections
    if task == "refactor":
        _render_complexity_text(extras.get("complexity"))
        _render_graph_centrality_text(extras.get("graph_centrality"))
        _render_coupling_text(extras.get("coupling"))

    elif task == "debug":
        _render_complexity_text(extras.get("complexity"))
        _render_affected_tests_text(extras.get("affected_tests", []))

    elif task == "extend":
        _render_graph_centrality_text(extras.get("graph_centrality"))
        _render_similar_symbols_text(extras.get("similar_symbols", []))
        _render_entry_points_text(extras.get("entry_points_reaching", []))

    elif task == "review":
        _render_complexity_text(extras.get("complexity"))
        _render_churn_text(extras.get("git_churn"))
        _render_affected_tests_text(extras.get("affected_tests", []))
        _render_coupling_text(extras.get("coupling"))
        _render_blast_radius_text(extras.get("blast_radius"))
        _render_graph_centrality_text(extras.get("graph_centrality"))

    elif task == "understand":
        _render_cluster_text(extras.get("cluster"))
        _render_graph_centrality_text(extras.get("graph_centrality"))
        _render_file_context_text(extras.get("file_context", []))

    # Files to read
    skipped_total = skipped_callers + skipped_callees
    extra_label = f", +{skipped_total} more" if skipped_total else ""
    click.echo(f"Files to read ({len(files_to_read)}{extra_label}):")
    for f in files_to_read:
        end_str = f"-{f['end']}" if f["end"] and f["end"] != f["start"] else ""
        lr = f":{f['start']}{end_str}" if f["start"] else ""
        click.echo(f"  {f['path']:<50s} {lr:<12s} ({f['reason']})")


# ---------------------------------------------------------------------------
# Task-mode output: JSON
# ---------------------------------------------------------------------------

def _output_task_single_json(c, task, extras):
    """Build and emit JSON output for task-mode single symbol."""
    sym = c["sym"]
    line_start = c["line_start"]
    line_end = c["line_end"]
    non_test_callers = c["non_test_callers"]
    callees = c["callees"]
    test_callers = c["test_callers"]
    test_importers = c["test_importers"]
    siblings = c["siblings"]
    files_to_read = c["files_to_read"]

    hide_callees = extras.get("_hide_callees", False)
    limit_callers = extras.get("_limit_callers")
    limit_callees = extras.get("_limit_callees")

    caller_cap = limit_callers or len(non_test_callers)
    callee_cap = limit_callees or len(callees)

    payload = {
        "task": task,
        "symbol": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "signature": sym["signature"] or "",
        "location": loc(sym["file_path"], line_start),
        "definition": {
            "file": sym["file_path"],
            "start": line_start, "end": line_end,
        },
        "callers": [
            {"name": cr["name"], "kind": cr["kind"],
             "location": loc(
                 cr["file_path"], cr["edge_line"] or cr["line_start"],
             ),
             "edge_kind": cr["edge_kind"] or ""}
            for cr in non_test_callers[:caller_cap]
        ],
    }

    if not hide_callees:
        payload["callees"] = [
            {"name": ce["name"], "kind": ce["kind"],
             "location": loc(ce["file_path"], ce["line_start"]),
             "edge_kind": ce["edge_kind"] or ""}
            for ce in callees[:callee_cap]
        ]

    if task not in ("review", "debug"):
        payload["tests"] = [
            {"name": t["name"], "kind": t["kind"],
             "location": loc(t["file_path"], t["line_start"]),
             "edge_kind": t["edge_kind"] or ""}
            for t in test_callers
        ]
        payload["test_files"] = [r["path"] for r in test_importers]

    if task in ("refactor", "understand"):
        payload["siblings"] = [
            {"name": s["name"], "kind": s["kind"]}
            for s in siblings[:10]
        ]

    # Task-specific extras
    for key in ("docstring", "complexity", "graph_centrality", "git_churn",
                "blast_radius", "cluster"):
        val = extras.get(key)
        if val is not None:
            payload[key] = val

    for key in ("coupling", "affected_tests", "similar_symbols",
                "entry_points_reaching", "file_context"):
        val = extras.get(key)
        if val:
            payload[key] = val

    payload["files_to_read"] = [
        {"path": f["path"], "start": f["start"],
         "end": f["end"], "reason": f["reason"]}
        for f in files_to_read
    ]

    # Summary
    summary = {"task": task, "callers": len(non_test_callers)}
    if not hide_callees:
        summary["callees"] = len(callees)
    summary["tests"] = len(test_callers)
    summary["files_to_read"] = len(files_to_read)

    if extras.get("blast_radius"):
        summary["blast_radius_symbols"] = extras["blast_radius"]["dependent_symbols"]
        summary["blast_radius_files"] = extras["blast_radius"]["dependent_files"]
    if extras.get("affected_tests") is not None:
        summary["affected_tests_total"] = len(extras["affected_tests"])
    if extras.get("coupling"):
        summary["coupling_partners"] = len(extras["coupling"])

    click.echo(to_json(json_envelope("context", summary=summary, **payload)))


# ---------------------------------------------------------------------------
# File-level context: --for-file
# ---------------------------------------------------------------------------

def _resolve_file(conn, path):
    """Resolve a file path to its DB row, or None."""
    path = path.replace("\\", "/")
    frow = conn.execute(FILE_BY_PATH, (path,)).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{path}",),
        ).fetchone()
    return frow


def _gather_file_level_context(conn, frow):
    """Gather comprehensive file-level context.

    Returns a dict with callers, callees, tests, coupling, and complexity
    aggregated across all symbols in the file.
    """
    file_id = frow["id"]
    file_path = frow["path"]

    # Get all symbols in the file
    symbols = conn.execute(
        "SELECT s.*, f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.file_id = ? ORDER BY s.line_start",
        (file_id,),
    ).fetchall()

    sym_ids = [s["id"] for s in symbols]
    if not sym_ids:
        return {
            "file_path": file_path,
            "symbol_count": 0,
            "callers": [],
            "callees": [],
            "tests": [],
            "coupling": [],
            "complexity": None,
        }

    ph = ",".join("?" for _ in sym_ids)

    # --- Callers: symbols in OTHER files that reference symbols in this file ---
    caller_rows = conn.execute(
        f"SELECT e.target_id, s.name as caller_name, s.kind as caller_kind, "
        f"f.path as caller_file, s.line_start as caller_line, "
        f"ts.name as target_name "
        f"FROM edges e "
        f"JOIN symbols s ON e.source_id = s.id "
        f"JOIN files f ON s.file_id = f.id "
        f"JOIN symbols ts ON e.target_id = ts.id "
        f"WHERE e.target_id IN ({ph}) AND s.file_id != ?",
        sym_ids + [file_id],
    ).fetchall()

    # Group callers by source file
    callers_by_file = defaultdict(list)
    for r in caller_rows:
        if not is_test_file(r["caller_file"]):
            callers_by_file[r["caller_file"]].append(r["target_name"])

    callers = []
    for cfile, targets in sorted(callers_by_file.items()):
        unique_targets = sorted(set(targets))
        callers.append({
            "file": cfile,
            "symbols": unique_targets,
            "count": len(unique_targets),
        })

    # --- Callees: symbols in OTHER files that this file's symbols reference ---
    callee_rows = conn.execute(
        f"SELECT e.source_id, s.name as callee_name, s.kind as callee_kind, "
        f"f.path as callee_file, s.line_start as callee_line "
        f"FROM edges e "
        f"JOIN symbols s ON e.target_id = s.id "
        f"JOIN files f ON s.file_id = f.id "
        f"WHERE e.source_id IN ({ph}) AND s.file_id != ?",
        sym_ids + [file_id],
    ).fetchall()

    # Group callees by target file
    callees_by_file = defaultdict(list)
    for r in callee_rows:
        callees_by_file[r["callee_file"]].append(r["callee_name"])

    callees = []
    for cfile, names in sorted(callees_by_file.items()):
        unique_names = sorted(set(names))
        callees.append({
            "file": cfile,
            "symbols": unique_names,
            "count": len(unique_names),
        })

    # --- Tests: test files that reference any symbol in this file ---
    test_caller_rows = conn.execute(
        f"SELECT DISTINCT f.path "
        f"FROM edges e "
        f"JOIN symbols s ON e.source_id = s.id "
        f"JOIN files f ON s.file_id = f.id "
        f"WHERE e.target_id IN ({ph}) AND s.file_id != ?",
        sym_ids + [file_id],
    ).fetchall()

    direct_tests = sorted(set(
        r["path"] for r in test_caller_rows if is_test_file(r["path"])
    ))

    # Also check file_edges for file-level test importers
    test_importers = conn.execute(
        "SELECT f.path FROM file_edges fe "
        "JOIN files f ON fe.source_file_id = f.id "
        "WHERE fe.target_file_id = ?",
        (file_id,),
    ).fetchall()
    file_level_tests = sorted(set(
        r["path"] for r in test_importers if is_test_file(r["path"])
    ))

    # Merge direct + file-level, mark kind
    test_set = set()
    tests = []
    for t in direct_tests:
        test_set.add(t)
        tests.append({"file": t, "kind": "direct"})
    for t in file_level_tests:
        if t not in test_set:
            test_set.add(t)
            tests.append({"file": t, "kind": "file-level"})

    # --- Coupling ---
    coupling = _get_coupling(conn, file_path, limit=10)

    # --- Complexity summary ---
    metrics_rows = conn.execute(
        f"SELECT sm.* FROM symbol_metrics sm "
        f"WHERE sm.symbol_id IN ({ph})",
        sym_ids,
    ).fetchall()

    complexity = None
    if metrics_rows:
        cc_values = [r["cognitive_complexity"] for r in metrics_rows]
        threshold = 15
        complexity = {
            "avg": round(sum(cc_values) / len(cc_values), 1),
            "max": max(cc_values),
            "count_above_threshold": sum(1 for v in cc_values if v > threshold),
            "threshold": threshold,
            "measured_symbols": len(cc_values),
        }

    return {
        "file_path": file_path,
        "language": frow["language"],
        "line_count": frow["line_count"],
        "symbol_count": len(symbols),
        "callers": callers,
        "callees": callees,
        "tests": tests,
        "coupling": coupling,
        "complexity": complexity,
    }


def _output_file_context_text(data):
    """Render --for-file context as text."""
    click.echo(
        f"Context for {data['file_path']} "
        f"({data['symbol_count']} symbols):"
    )
    click.echo()

    # Callers
    callers = data["callers"]
    if callers:
        click.echo(f"Callers ({len(callers)} unique files):")
        for c in callers[:20]:
            syms = ", ".join(c["symbols"][:3])
            if len(c["symbols"]) > 3:
                syms += f" +{len(c['symbols']) - 3} more"
            click.echo(f"  {c['file']:<50s} \u2192 {syms}")
        if len(callers) > 20:
            click.echo(f"  (+{len(callers) - 20} more)")
        click.echo()
    else:
        click.echo("Callers: (none)")
        click.echo()

    # Callees
    callees = data["callees"]
    if callees:
        click.echo(f"Callees ({len(callees)} unique files):")
        for c in callees[:20]:
            syms = ", ".join(c["symbols"][:3])
            if len(c["symbols"]) > 3:
                syms += f" +{len(c['symbols']) - 3} more"
            click.echo(f"  {c['file']:<50s} \u2190 {syms}")
        if len(callees) > 20:
            click.echo(f"  (+{len(callees) - 20} more)")
        click.echo()
    else:
        click.echo("Callees: (none)")
        click.echo()

    # Tests
    tests = data["tests"]
    if tests:
        direct = sum(1 for t in tests if t["kind"] == "direct")
        file_lvl = sum(1 for t in tests if t["kind"] == "file-level")
        click.echo(f"Tests ({direct} direct, {file_lvl} file-level):")
        for t in tests:
            click.echo(f"  {t['file']} ({t['kind']})")
        click.echo()
    else:
        click.echo("Tests: (none)")
        click.echo()

    # Coupling
    coupling = data["coupling"]
    if coupling:
        click.echo(f"Coupling ({len(coupling)} partners):")
        rows = [
            [c["path"], str(c["cochange_count"]), f"{c['strength']:.0%}"]
            for c in coupling[:10]
        ]
        click.echo(format_table(["file", "co-changes", "strength"], rows))
        click.echo()

    # Complexity
    cx = data["complexity"]
    if cx:
        click.echo(
            f"Complexity: avg={cx['avg']}, max={cx['max']}, "
            f"{cx['count_above_threshold']} above threshold "
            f"(>{cx['threshold']})"
        )
        click.echo()


def _output_file_context_json(data):
    """Render --for-file context as JSON."""
    summary = {
        "symbol_count": data["symbol_count"],
        "caller_files": len(data["callers"]),
        "callee_files": len(data["callees"]),
        "test_files": len(data["tests"]),
        "coupling_partners": len(data["coupling"]),
    }
    if data["complexity"]:
        summary["complexity_avg"] = data["complexity"]["avg"]
        summary["complexity_max"] = data["complexity"]["max"]

    click.echo(to_json(json_envelope("context",
        summary=summary,
        mode="file",
        file=data["file_path"],
        language=data.get("language"),
        line_count=data.get("line_count"),
        symbol_count=data["symbol_count"],
        callers=data["callers"],
        callees=data["callees"],
        tests=data["tests"],
        coupling=data["coupling"],
        complexity=data["complexity"],
    )))


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.argument('names', nargs=-1)
@click.option(
    '--task', 'task',
    type=click.Choice(_TASK_CHOICES, case_sensitive=False),
    default=None,
    help='Tailor context to a specific task intent: '
         'refactor, debug, extend, review, understand.',
)
@click.option(
    '--for-file', 'for_file', type=str, default=None,
    help='Get aggregated context for an entire file instead of a symbol.',
)
@click.pass_context
def context(ctx, names, task, for_file):
    """Get the minimal context needed to safely modify a symbol.

    Returns definition, callers, callees, tests, and the exact files
    to read -- everything an AI agent needs in one shot.

    Pass multiple symbol names for batch mode with shared callers analysis.

    Use --for-file PATH to get file-level context: callers grouped by
    source file, callees grouped by target file, tests, coupling partners,
    and a complexity summary across all symbols in the file.

    Use --task to tailor the context to a specific agent intent:

    \b
      refactor   - callers, siblings, complexity, coupling (safe modification)
      debug      - callees, callers, affected tests (execution tracing)
      extend     - full graph, similar symbols, conventions (integration)
      review     - complexity, churn, blast radius, coupling (risk assessment)
      understand - docstring, cluster, architecture role (comprehension)
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # --- File-level context mode ---
    if for_file:
        with open_db(readonly=True) as conn:
            frow = _resolve_file(conn, for_file)
            if frow is None:
                click.echo(f"File not found in index: {for_file}")
                raise SystemExit(1)
            data = _gather_file_level_context(conn, frow)
            if json_mode:
                _output_file_context_json(data)
            else:
                _output_file_context_text(data)
        return

    # Require at least one symbol name if --for-file is not used
    if not names:
        click.echo(ctx.get_help())
        return

    with open_db(readonly=True) as conn:
        # Resolve all symbols
        resolved = []
        for name in names:
            sym = find_symbol(conn, name)
            if sym is None:
                click.echo(f"Symbol not found: {name}")
                raise SystemExit(1)
            resolved.append(sym)

        # Gather context for each
        contexts = [_gather_symbol_context(conn, sym) for sym in resolved]

        # --- Batch mode (--task is ignored) ---
        if len(contexts) > 1:
            if task:
                click.echo(
                    "Warning: --task is ignored in batch mode "
                    "(multiple symbols). Using default context."
                )
            shared_callers, shared_callees, scored_files = _batch_context(
                conn, contexts,
            )

            if json_mode:
                click.echo(to_json(json_envelope("context",
                    summary={
                        "symbols": len(contexts),
                        "shared_callers": len(shared_callers),
                        "shared_callees": len(shared_callees),
                        "files_to_read": len(scored_files),
                    },
                    mode="batch",
                    symbols=[
                        {
                            "name": c["sym"]["qualified_name"] or c["sym"]["name"],
                            "kind": c["sym"]["kind"],
                            "location": loc(c["sym"]["file_path"], c["line_start"]),
                            "callers": [
                                {"name": cr["name"], "kind": cr["kind"],
                                 "location": loc(cr["file_path"], cr["edge_line"] or cr["line_start"])}
                                for cr in c["non_test_callers"][:20]
                            ],
                            "callees": [
                                {"name": ce["name"], "kind": ce["kind"],
                                 "location": loc(ce["file_path"], ce["line_start"])}
                                for ce in c["callees"][:15]
                            ],
                            "tests": len(c["test_callers"]),
                        }
                        for c in contexts
                    ],
                    shared_callers=[
                        {"name": c["name"], "kind": c["kind"],
                         "location": loc(c["file_path"], c["line_start"])}
                        for c in shared_callers
                    ],
                    shared_callees=[
                        {"name": c["name"], "kind": c["kind"],
                         "location": loc(c["file_path"], c["line_start"])}
                        for c in shared_callees
                    ],
                    files_to_read=scored_files,
                )))
                return

            # Text batch output
            click.echo(f"=== Batch Context ({len(contexts)} symbols) ===\n")

            for c in contexts:
                s = c["sym"]
                sig = s["signature"] or ""
                click.echo(f"--- {s['name']} ---")
                click.echo(
                    f"  {abbrev_kind(s['kind'])}  "
                    f"{s['qualified_name'] or s['name']}"
                    f"{'  ' + sig if sig else ''}  "
                    f"{loc(s['file_path'], c['line_start'])}"
                )
                click.echo(
                    f"  Callers: {len(c['non_test_callers'])}  "
                    f"Callees: {len(c['callees'])}  "
                    f"Tests: {len(c['test_callers'])}"
                )
                click.echo()

            if shared_callers:
                click.echo(f"Shared callers ({len(shared_callers)}):")
                rows = [[abbrev_kind(c["kind"]), c["name"],
                         loc(c["file_path"], c["line_start"])]
                        for c in shared_callers[:15]]
                click.echo(format_table(["kind", "name", "location"], rows))
                click.echo()

            if shared_callees:
                click.echo(f"Shared callees ({len(shared_callees)}):")
                rows = [[abbrev_kind(c["kind"]), c["name"],
                         loc(c["file_path"], c["line_start"])]
                        for c in shared_callees[:15]]
                click.echo(format_table(["kind", "name", "location"], rows))
                click.echo()

            click.echo(f"Files to read ({len(scored_files)}):")
            for f in scored_files[:25]:
                reasons = ", ".join(f["reasons"])
                rel_str = f"{f['relevance']:.0%}" if f["relevance"] > 0 else ""
                click.echo(
                    f"  {f['path']:<50s} {rel_str:>5s}  ({reasons})"
                )
            if len(scored_files) > 25:
                click.echo(f"  (+{len(scored_files) - 25} more)")
            return

        # --- Single symbol mode ---
        c = contexts[0]
        sym = c["sym"]

        # Task mode: gather extras and render
        if task:
            extras = _gather_task_extras(conn, sym, c, task)
            if json_mode:
                _output_task_single_json(c, task, extras)
            else:
                _output_task_single_text(c, task, extras)
            return

        # --- Default single symbol mode (original behavior) ---
        line_start = c["line_start"]
        line_end = c["line_end"]
        non_test_callers = c["non_test_callers"]
        callees = c["callees"]
        test_callers = c["test_callers"]
        test_importers = c["test_importers"]
        siblings = c["siblings"]
        files_to_read = c["files_to_read"]
        skipped_callers = c["skipped_callers"]
        skipped_callees = c["skipped_callees"]

        if json_mode:
            click.echo(to_json(json_envelope("context",
                summary={
                    "callers": len(non_test_callers),
                    "callees": len(callees),
                    "tests": len(test_callers),
                    "files_to_read": len(files_to_read),
                },
                symbol=sym["qualified_name"] or sym["name"],
                kind=sym["kind"],
                signature=sym["signature"] or "",
                location=loc(sym["file_path"], line_start),
                definition={
                    "file": sym["file_path"],
                    "start": line_start, "end": line_end,
                },
                callers=[
                    {"name": cr["name"], "kind": cr["kind"],
                     "location": loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                     "edge_kind": cr["edge_kind"] or ""}
                    for cr in non_test_callers
                ],
                callees=[
                    {"name": ce["name"], "kind": ce["kind"],
                     "location": loc(ce["file_path"], ce["line_start"]),
                     "edge_kind": ce["edge_kind"] or ""}
                    for ce in callees
                ],
                tests=[
                    {"name": t["name"], "kind": t["kind"],
                     "location": loc(t["file_path"], t["line_start"]),
                     "edge_kind": t["edge_kind"] or ""}
                    for t in test_callers
                ],
                test_files=[r["path"] for r in test_importers],
                siblings=[
                    {"name": s["name"], "kind": s["kind"]}
                    for s in siblings[:10]
                ],
                files_to_read=[
                    {"path": f["path"], "start": f["start"],
                     "end": f["end"], "reason": f["reason"]}
                    for f in files_to_read
                ],
            )))
            return

        # --- Text output ---
        sig = sym["signature"] or ""
        click.echo(f"=== Context for: {sym['name']} ===")
        click.echo(
            f"{abbrev_kind(sym['kind'])}  "
            f"{sym['qualified_name'] or sym['name']}"
            f"{'  ' + sig if sig else ''}  "
            f"{loc(sym['file_path'], line_start)}"
        )
        click.echo()

        if non_test_callers:
            click.echo(f"Callers ({len(non_test_callers)}):")
            rows = []
            for cr in non_test_callers[:20]:
                rows.append([
                    abbrev_kind(cr["kind"]), cr["name"],
                    loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                    cr["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(non_test_callers) > 20:
                click.echo(f"  (+{len(non_test_callers) - 20} more)")
            click.echo()
        else:
            click.echo("Callers: (none)")
            click.echo()

        if callees:
            click.echo(f"Callees ({len(callees)}):")
            rows = []
            for ce in callees[:15]:
                rows.append([
                    abbrev_kind(ce["kind"]), ce["name"],
                    loc(ce["file_path"], ce["line_start"]),
                    ce["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(callees) > 15:
                click.echo(f"  (+{len(callees) - 15} more)")
            click.echo()
        else:
            click.echo("Callees: (none)")
            click.echo()

        if test_callers or test_importers:
            click.echo(
                f"Tests ({len(test_callers)} direct, "
                f"{len(test_importers)} file-level):"
            )
            for t in test_callers:
                click.echo(
                    f"  {abbrev_kind(t['kind'])}  {t['name']}  "
                    f"{loc(t['file_path'], t['line_start'])}"
                )
            for ti in test_importers:
                click.echo(f"  file  {ti['path']}")
        else:
            click.echo("Tests: (none)")
        click.echo()

        if siblings:
            click.echo(f"Siblings ({len(siblings)} exports in same file):")
            for s in siblings[:10]:
                click.echo(f"  {abbrev_kind(s['kind'])}  {s['name']}")
            if len(siblings) > 10:
                click.echo(f"  (+{len(siblings) - 10} more)")
            click.echo()

        skipped_total = skipped_callers + skipped_callees
        extra = f", +{skipped_total} more" if skipped_total else ""
        click.echo(f"Files to read ({len(files_to_read)}{extra}):")
        for f in files_to_read:
            end_str = (
                f"-{f['end']}"
                if f["end"] and f["end"] != f["start"]
                else ""
            )
            lr = f":{f['start']}{end_str}" if f["start"] else ""
            click.echo(
                f"  {f['path']:<50s} {lr:<12s} ({f['reason']})"
            )
