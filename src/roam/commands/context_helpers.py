"""Data-gathering helpers for the context command.

Extracted from cmd_context.py to reduce file size.  These functions
query the index DB and return plain dicts — no rendering or CLI I/O.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict, deque

from roam.commands.changed_files import is_test_file
from roam.commands.graph_helpers import build_forward_adj
from roam.db.connection import batched_in
from roam.output.formatter import loc

_DEFAULT_REASON_WEIGHTS = {
    "definition": 1.0,
    "caller": 0.78,
    "callee": 0.72,
    "test": 0.52,
}

_TASK_REASON_WEIGHTS = {
    "refactor": {
        **_DEFAULT_REASON_WEIGHTS,
        "caller": 0.95,
        "callee": 0.62,
    },
    "debug": {
        **_DEFAULT_REASON_WEIGHTS,
        "callee": 0.96,
        "test": 0.82,
    },
    "extend": {
        **_DEFAULT_REASON_WEIGHTS,
        "callee": 0.86,
    },
    "review": {
        **_DEFAULT_REASON_WEIGHTS,
        "caller": 0.88,
        "test": 0.76,
    },
    "understand": {
        **_DEFAULT_REASON_WEIGHTS,
        "caller": 0.82,
        "callee": 0.82,
    },
}


def _normalize_task(task):
    if not task:
        return ""
    return str(task).strip().lower()


def _reason_weight(task, reason):
    task_key = _normalize_task(task)
    weights = _TASK_REASON_WEIGHTS.get(task_key, _DEFAULT_REASON_WEIGHTS)
    return weights.get(reason, 0.5)


def _tokenize_hint(text):
    if not text:
        return set()
    return {tok for tok in re.split(r"[^a-zA-Z0-9_]+", str(text).lower()) if len(tok) >= 3}


def _session_overlap(path, tokens):
    if not tokens:
        return 0.0
    p = (path or "").replace("\\", "/").lower()
    matches = sum(1 for tok in tokens if tok in p)
    if matches <= 0:
        return 0.0
    return min(matches / 3.0, 1.0)


def _resolve_recent_symbol_paths(conn, recent_symbols):
    if not recent_symbols:
        return set()
    paths = set()
    for sym_name in recent_symbols:
        name = (sym_name or "").strip()
        if not name:
            continue
        row = conn.execute(
            "SELECT f.path FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE lower(s.name) = lower(?) "
            "   OR lower(COALESCE(s.qualified_name, '')) = lower(?) "
            "ORDER BY CASE WHEN lower(s.name) = lower(?) THEN 0 ELSE 1 END, s.id "
            "LIMIT 1",
            (name, name, name),
        ).fetchone()
        if row:
            paths.add(row["path"].replace("\\", "/"))
    return paths


def _recent_path_boost(path, recent_paths):
    if not recent_paths:
        return 0.0
    p = (path or "").replace("\\", "/")
    p_dir = p.rsplit("/", 1)[0] if "/" in p else ""
    for recent in recent_paths:
        if p == recent:
            return 1.0
        if p_dir and p_dir == recent.rsplit("/", 1)[0]:
            return 0.35
    return 0.0


def _file_pagerank_map(conn, paths):
    path_list = sorted({(p or "").replace("\\", "/") for p in paths if p})
    if not path_list:
        return {}

    file_rows = batched_in(
        conn,
        "SELECT id, path FROM files WHERE path IN ({ph})",
        path_list,
    )
    if not file_rows:
        return {}

    file_ids = [r["id"] for r in file_rows]
    id_to_path = {r["id"]: r["path"].replace("\\", "/") for r in file_rows}

    pr_rows = batched_in(
        conn,
        "SELECT s.file_id, MAX(COALESCE(gm.pagerank, 0)) AS pagerank "
        "FROM symbols s "
        "LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
        "WHERE s.file_id IN ({ph}) "
        "GROUP BY s.file_id",
        file_ids,
    )
    file_pr = {id_to_path[r["file_id"]]: float(r["pagerank"] or 0.0) for r in pr_rows}
    for path in path_list:
        file_pr.setdefault(path, 0.0)
    return file_pr


def _attach_rank_scores(items):
    total = len(items)
    for idx, item in enumerate(items):
        # Higher rank = more important (aligns with budget truncation semantics).
        item["rank"] = total - idx
    return items


def _rank_files_preserving_context_signal_parity(
    conn,
    files,
    task,
    session_hint,
    recent_symbols,
    propagation_scores,
    score_file,
    sort_key,
):
    """Apply shared context biases while each mode keeps its own relevance rule."""
    if not files:
        return []

    hint_tokens = _tokenize_hint(session_hint)
    recent_paths = _resolve_recent_symbol_paths(conn, recent_symbols)
    file_pr = _file_pagerank_map(conn, [f["path"] for f in files])
    max_pr = max(file_pr.values(), default=0.0)

    prop_scores = propagation_scores or {}
    has_prop = bool(prop_scores)
    max_prop = max(prop_scores.values(), default=0.0) if has_prop else 0.0

    ranked = []
    for item in files:
        path = item["path"].replace("\\", "/")
        features = {
            "has_prop": has_prop,
            "prop_norm": (prop_scores.get(path, 0.0) / max_prop) if max_prop > 0 else 0.0,
            "pr_norm": (file_pr.get(path, 0.0) / max_pr) if max_pr > 0 else 0.0,
            "session_score": _session_overlap(path, hint_tokens),
            "recent_score": _recent_path_boost(path, recent_paths),
        }

        ranked.append(
            {
                **item,
                "path": path,
                "score": round(score_file(item, task, features), 3),
            }
        )

    ranked.sort(key=sort_key)
    return _attach_rank_scores(ranked)


def _score_single_file_for_symbol_context_budget(item, task, features):
    reason = item.get("reason", "")
    reason_score = _reason_weight(task, reason)

    if features["has_prop"]:
        score = (
            (0.40 * reason_score)
            + (0.25 * features["prop_norm"])
            + (0.20 * features["pr_norm"])
            + (0.10 * features["session_score"])
            + (0.05 * features["recent_score"])
        )
    else:
        score = (
            (0.50 * reason_score)
            + (0.30 * features["pr_norm"])
            + (0.12 * features["session_score"])
            + (0.08 * features["recent_score"])
        )
    if reason == "definition":
        score = max(score, 1.25)
    return score


def _score_batch_file_for_density_context_budget(item, task, features):
    reasons = item.get("reasons", [])
    relevance = float(item.get("relevance") or 0.0)
    reason_score = max(
        (_reason_weight(task, r) for r in reasons),
        default=_DEFAULT_REASON_WEIGHTS["callee"],
    )

    if features["has_prop"]:
        score = (
            (0.35 * relevance)
            + (0.25 * features["prop_norm"])
            + (0.22 * reason_score)
            + (0.12 * features["pr_norm"])
            + (0.04 * features["session_score"])
            + (0.02 * features["recent_score"])
        )
    else:
        score = (
            (0.45 * relevance)
            + (0.30 * reason_score)
            + (0.15 * features["pr_norm"])
            + (0.07 * features["session_score"])
            + (0.03 * features["recent_score"])
        )
    if "definition" in reasons:
        score = max(score, 1.2)
    return score


def _rank_single_files(
    conn,
    files_to_read,
    task=None,
    session_hint="",
    recent_symbols=(),
    propagation_scores=None,
):
    """Score files-to-read using task, conversation, and propagation signals.

    When *propagation_scores* is provided (a ``{file_path: float}`` dict from
    ``_get_propagation_scores_for_paths``), the score formula is extended to
    include a propagation component that rewards transitive callees/callers.
    """
    return _rank_files_preserving_context_signal_parity(
        conn,
        files_to_read,
        task,
        session_hint,
        recent_symbols,
        propagation_scores,
        _score_single_file_for_symbol_context_budget,
        lambda x: (-x["score"], x["path"], x.get("start") or 0),
    )


def _rank_batch_files(
    conn,
    files,
    task=None,
    session_hint="",
    recent_symbols=(),
    propagation_scores=None,
):
    """Score batch-mode files from density + task/session personalization.

    When *propagation_scores* is provided, the score formula is extended to
    include a propagation component for transitive callee/caller weighting.
    """
    return _rank_files_preserving_context_signal_parity(
        conn,
        files,
        task,
        session_hint,
        recent_symbols,
        propagation_scores,
        _score_batch_file_for_density_context_budget,
        lambda x: (-x["score"], -x["relevance"], x["path"]),
    )


# ---------------------------------------------------------------------------
# Propagation-aware ranking helpers
# ---------------------------------------------------------------------------


def _get_propagation_scores_for_paths(conn, sym_ids, use_propagation, max_depth=3, decay=0.5):
    """Compute per-file propagation scores for a set of seed symbol IDs.

    Builds a lightweight adjacency map (no networkx) by loading edges directly
    from the DB, runs BFS propagation, then aggregates per-file scores as the
    maximum propagation score of any symbol in that file.

    Returns a dict mapping ``{file_path: propagation_score}`` for all files
    reachable within max_depth from the seeds.  Returns an empty dict when
    use_propagation is False or sym_ids is empty.
    """
    if not use_propagation or not sym_ids:
        return {}

    # Build a lightweight networkx-free graph using adjacency dicts
    # to avoid importing networkx (it is a heavy dependency only loaded on demand).
    # We implement a small BFS directly rather than constructing a DiGraph.

    seed_set = set(sym_ids)

    # Load edges in both directions
    forward: dict = {}  # caller -> {callee, ...}
    backward: dict = {}  # callee -> {caller, ...}

    for batch_ids in _batch_list(list(seed_set), 400):
        _load_neighborhood_edges(conn, batch_ids, forward, backward, max_depth)

    # BFS callee direction (forward)
    callee_scores: dict[int, float] = {s: 1.0 for s in seed_set}
    visited_callee: dict[int, int] = {s: 0 for s in seed_set}
    queue = deque([(s, 0) for s in seed_set])
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor in forward.get(node, ()):
            if neighbor in seed_set:
                continue
            nd = depth + 1
            if neighbor not in visited_callee or visited_callee[neighbor] > nd:
                visited_callee[neighbor] = nd
                score = decay**nd
                prev = callee_scores.get(neighbor, 0.0)
                callee_scores[neighbor] = max(prev, score)
                queue.append((neighbor, nd))

    # BFS caller direction (backward), lower weight
    caller_decay = decay * 0.5
    visited_caller: dict[int, int] = {s: 0 for s in seed_set}
    queue = deque([(s, 0) for s in seed_set])
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor in backward.get(node, ()):
            if neighbor in seed_set:
                continue
            nd = depth + 1
            if neighbor not in visited_caller or visited_caller[neighbor] > nd:
                visited_caller[neighbor] = nd
                score = caller_decay**nd
                prev = callee_scores.get(neighbor, 0.0)
                callee_scores[neighbor] = max(prev, score)
                queue.append((neighbor, nd))

    if not callee_scores:
        return {}

    # Map symbol_id -> file_path and aggregate per file
    all_sym_ids = list(callee_scores.keys())
    path_max_score: dict[str, float] = {}

    for chunk in _batch_list(all_sym_ids, 400):
        ph = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT s.id, f.path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
            chunk,
        ).fetchall()
        for row in rows:
            # Handle both sqlite3.Row and tuple
            try:
                sym_id_val = row["id"]
                path_val = row["path"]
            except (KeyError, TypeError):
                sym_id_val = row[0]
                path_val = row[1]
            path_val = (path_val or "").replace("\\", "/")
            sc = callee_scores.get(sym_id_val, 0.0)
            if sc > path_max_score.get(path_val, 0.0):
                path_max_score[path_val] = sc

    return path_max_score


def _batch_list(lst, size):
    """Yield successive chunks of *size* from *lst*."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _load_neighborhood_edges(conn, seed_ids, forward, backward, max_depth):
    """BFS-expand edges up to max_depth from seed_ids, populating forward/backward."""
    frontier = set(seed_ids)
    visited_nodes = set(seed_ids)
    for _depth in range(max_depth):
        if not frontier:
            break
        ph = ",".join("?" * len(frontier))
        frontier_list = list(frontier)
        # Forward edges (callees)
        rows = conn.execute(
            f"SELECT source_id, target_id FROM edges WHERE source_id IN ({ph})",
            frontier_list,
        ).fetchall()
        next_frontier = set()
        for row in rows:
            try:
                src, tgt = row["source_id"], row["target_id"]
            except (KeyError, TypeError):
                # Expected branch: positional tuple rows (no key access).
                src, tgt = row[0], row[1]
            forward.setdefault(src, set()).add(tgt)
            backward.setdefault(tgt, set()).add(src)
            if tgt not in visited_nodes:
                next_frontier.add(tgt)
                visited_nodes.add(tgt)
        # Backward edges (callers)
        rows = conn.execute(
            f"SELECT source_id, target_id FROM edges WHERE target_id IN ({ph})",
            frontier_list,
        ).fetchall()
        for row in rows:
            try:
                src, tgt = row["source_id"], row["target_id"]
            except (KeyError, TypeError):
                # Expected branch: positional tuple rows (no key access).
                src, tgt = row[0], row[1]
            forward.setdefault(src, set()).add(tgt)
            backward.setdefault(tgt, set()).add(src)
            if src not in visited_nodes:
                next_frontier.add(src)
                visited_nodes.add(src)
        frontier = next_frontier


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------


def _is_old_index_without_annotations_table(exc: sqlite3.OperationalError) -> bool:
    """Return True when annotations are absent because the index predates them."""
    return "no such table: annotations" in str(exc).lower()


def gather_annotations(conn: sqlite3.Connection, sym: dict | None = None, file_path: str | None = None):
    """Fetch active annotations for a symbol or file.

    Returns a list of annotation dicts (empty if none found).
    """
    conditions = ["(expires_at IS NULL OR expires_at > datetime('now'))"]
    params = []

    if sym is not None:
        sym_id = sym["id"]
        # sqlite3.Row lacks .get() — use try/except
        try:
            qname = sym["qualified_name"] or sym["name"]
        except (KeyError, IndexError):
            qname = sym["name"]
        conditions.append("(symbol_id = ? OR qualified_name = ?)")
        params.extend([sym_id, qname])
    elif file_path is not None:
        conditions.append("file_path = ?")
        params.append(file_path)
    else:
        return []

    where = " AND ".join(conditions)
    try:
        rows = conn.execute(
            f"SELECT * FROM annotations WHERE {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
    except sqlite3.OperationalError as _exc:
        if not _is_old_index_without_annotations_table(_exc):
            raise
        # Expected absence: the annotations table may not exist on an
        # older index.
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_annotations:annotations_query", _exc)
        return []

    return [
        {
            "tag": r["tag"],
            "content": r["content"],
            "author": r["author"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-symbol metric fetchers
# ---------------------------------------------------------------------------


def get_symbol_metrics(conn: sqlite3.Connection, sym_id: int):
    """Fetch symbol_metrics row for a symbol, or None."""
    row = conn.execute("SELECT * FROM symbol_metrics WHERE symbol_id = ?", (sym_id,)).fetchone()
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


def get_graph_metrics(conn: sqlite3.Connection, sym_id: int):
    """Fetch graph_metrics row for a symbol, or None."""
    row = conn.execute("SELECT * FROM graph_metrics WHERE symbol_id = ?", (sym_id,)).fetchone()
    if row is None:
        return None
    out = {
        "pagerank": round(row["pagerank"] or 0, 6),
        "in_degree": row["in_degree"] or 0,
        "out_degree": row["out_degree"] or 0,
        "betweenness": round(row["betweenness"] or 0, 6),
    }
    row_keys = set(row.keys())
    if "closeness" in row_keys:
        out["closeness"] = round(row["closeness"] or 0, 6)
    if "eigenvector" in row_keys:
        out["eigenvector"] = round(row["eigenvector"] or 0, 6)
    if "clustering_coefficient" in row_keys:
        out["clustering_coefficient"] = round(row["clustering_coefficient"] or 0, 6)
    if "debt_score" in row_keys:
        out["debt_score"] = round(row["debt_score"] or 0, 3)
    return out


def get_file_churn(conn: sqlite3.Connection, file_path: str):
    """Fetch git churn stats for the file containing the symbol."""
    frow = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
    if frow is None:
        return None
    stats = conn.execute("SELECT * FROM file_stats WHERE file_id = ?", (frow["id"],)).fetchone()
    if stats is None:
        return None
    return {
        "commit_count": stats["commit_count"] or 0,
        "total_churn": stats["total_churn"] or 0,
        "distinct_authors": stats["distinct_authors"] or 0,
    }


def get_coupling(conn: sqlite3.Connection, file_path: str, limit: int = 10):
    """Fetch temporal coupling partners for the symbol's file.

    Bulk-fetches all partner ``commit_count`` values in one batched
    ``IN (...)`` query so we never issue per-partner lookups inside the
    loop (was N+1 — one query per partner).
    """
    frow = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
    if frow is None:
        return []
    fid = frow["id"]

    fstats = conn.execute("SELECT commit_count FROM file_stats WHERE file_id = ?", (fid,)).fetchone()
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

    if not partners:
        return []

    partner_fids = [p["partner_fid"] for p in partners]
    pstats_rows = batched_in(
        conn,
        "SELECT file_id, commit_count FROM file_stats WHERE file_id IN ({ph})",
        partner_fids,
    )
    pcommits_by_fid = {r["file_id"]: (r["commit_count"] or 1) for r in pstats_rows}

    results = []
    for p in partners:
        partner_commits = pcommits_by_fid.get(p["partner_fid"], 1)
        avg = (file_commits + partner_commits) / 2
        strength = round(p["cochange_count"] / avg, 2) if avg > 0 else 0
        results.append(
            {
                "path": p["path"],
                "cochange_count": p["cochange_count"],
                "strength": strength,
            }
        )
    return results


def get_affected_tests_bfs(conn: sqlite3.Connection, sym_id: int, max_hops: int = 8):
    """BFS reverse-edge walk to find test symbols that transitively depend
    on the target symbol.

    Loads the reverse adjacency map once (single bulk query) and then
    BFS-walks it in memory. The previous version issued one ``SELECT
    source_id ... WHERE target_id = ?`` per pop — on a 17K-edge graph
    that meant ~970 queries for a moderate-fan symbol.
    """
    # Load reverse adjacency + symbol names in one query. Pulling the
    # name here saves a follow-up batched_in on caller_ids for the
    # ``via`` chain (we still need a separate batched_in for kind/path).
    rev_adj: dict[int, list[tuple[int, str]]] = {}
    try:
        for row in conn.execute(
            "SELECT e.target_id, e.source_id, s.name FROM edges e JOIN symbols s ON e.source_id = s.id"
        ).fetchall():
            try:
                tgt = row["target_id"]
                src = row["source_id"]
                name = row["name"]
            except (KeyError, TypeError):
                tgt, src, name = row[0], row[1], row[2]
            rev_adj.setdefault(tgt, []).append((src, name))
    except sqlite3.Error as _exc:
        # A database-level failure (schema drift, missing table/column,
        # locked DB) is the only thing this bulk load legitimately raises
        # — row-shape errors are handled inside. Degrade gracefully to an
        # empty adjacency (return no affected tests rather than
        # re-introducing per-hop queries) and surface the cause so an
        # empty result isn't mistaken for "no tests depend on this".
        # Non-DB errors (a refactor typo, an API misuse) are real bugs and
        # deliberately propagate instead of being silently swallowed.
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:get_affected_tests_bfs:rev_adj_load", _exc)
        rev_adj = {}

    visited: dict[int, tuple[int, str | None]] = {sym_id: (0, None)}
    queue: deque[tuple[int, int, str | None]] = deque([(sym_id, 0, None)])

    while queue:
        current_id, hops, via = queue.popleft()
        if hops >= max_hops:
            continue
        for src, src_name in rev_adj.get(current_id, ()):
            new_hops = hops + 1
            new_via = via if via else src_name
            if src not in visited or visited[src][0] > new_hops:
                visited[src] = (new_hops, new_via)
                queue.append((src, new_hops, new_via))

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
        tests.append(
            {
                "file": r["file_path"],
                "symbol": r["name"],
                "kind": "DIRECT" if hops == 1 else "TRANSITIVE",
                "hops": hops,
                "via": via if hops > 1 else None,
            }
        )

    tests.sort(
        key=lambda t: (
            0 if t["kind"] == "DIRECT" else 1,
            t["hops"],
            t["file"],
        )
    )
    return tests


def get_blast_radius(conn: sqlite3.Connection, sym_id: int):
    """Compute downstream dependents count via BFS on reverse edges.

    Loads the reverse adjacency map in a single bulk query and walks it
    in memory. The previous version issued one ``SELECT source_id
    ... WHERE target_id = ?`` per pop — on a 17K-edge graph that meant
    ~970 queries for a moderate-fan symbol.
    """
    rev_adj: dict[int, list[int]] = {}
    try:
        for row in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
            try:
                src = row["source_id"]
                tgt = row["target_id"]
            except (KeyError, TypeError):
                src, tgt = row[0], row[1]
            rev_adj.setdefault(tgt, []).append(src)
    except sqlite3.Error as _exc:
        # Bulk edge load failed -> empty adjacency -> blast radius of 0.
        # Surface the cause so a DB fault isn't mistaken for "no
        # downstream dependents". Any non-DB failure is allowed to propagate.
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:get_blast_radius:rev_adj_load", _exc)
        rev_adj = {}

    visited = {sym_id}
    queue: deque[int] = deque([sym_id])
    while queue:
        current = queue.popleft()
        for src in rev_adj.get(current, ()):
            if src not in visited:
                visited.add(src)
                queue.append(src)

    if len(visited) <= 1:
        return {"dependent_symbols": 0, "dependent_files": 0}

    dep_ids = [sid for sid in visited if sid != sym_id]
    file_rows = batched_in(
        conn,
        "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
        dep_ids,
    )

    return {
        "dependent_symbols": len(dep_ids),
        "dependent_files": len(file_rows),
    }


def get_cluster_info(conn: sqlite3.Connection, sym_id: int):
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
        "top_members": [{"name": m["name"], "kind": m["kind"]} for m in members],
    }


def get_similar_symbols(conn: sqlite3.Connection, sym: dict, limit: int = 10):
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


def get_entry_points_reaching(conn: sqlite3.Connection, sym_id: int, limit: int = 5):
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
            results.append(
                {
                    "name": ep["qualified_name"] or ep["name"],
                    "kind": ep["kind"],
                    "location": loc(ep["file_path"], ep["line_start"]),
                }
            )
            if len(results) >= limit:
                break

    return results


def get_file_context(conn: sqlite3.Connection, file_id: int, sym_id: int):
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
# Single-symbol context gathering (reusable for batch mode)
# ---------------------------------------------------------------------------


def gather_symbol_context(conn, sym, task=None, session_hint="", recent_symbols=(), use_propagation=True):
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
            "SELECT symbol_id, pagerank FROM graph_metrics WHERE symbol_id IN ({ph})",
            caller_ids,
        )
        pr_map = {r["symbol_id"]: r["pagerank"] or 0 for r in pr_rows}
        non_test_callers = sorted(
            non_test_callers,
            key=lambda c: -pr_map.get(c["id"], 0),
        )

    # --- Test files that import the symbol's file ---
    sym_file_row = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
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

    files_to_read = [
        {
            "path": sym["file_path"],
            "start": line_start,
            "end": line_end,
            "reason": "definition",
        }
    ]
    seen = {sym["file_path"]}
    caller_files = 0
    for c in non_test_callers:
        if c["file_path"] not in seen:
            if caller_files >= _MAX_CALLER_FILES:
                skipped_callers += 1
                continue
            seen.add(c["file_path"])
            files_to_read.append(
                {
                    "path": c["file_path"],
                    "start": c["line_start"],
                    "end": c["line_end"] or c["line_start"],
                    "reason": "caller",
                }
            )
            caller_files += 1
    callee_files = 0
    for c in callees:
        if c["file_path"] not in seen:
            if callee_files >= _MAX_CALLEE_FILES:
                skipped_callees += 1
                continue
            seen.add(c["file_path"])
            files_to_read.append(
                {
                    "path": c["file_path"],
                    "start": c["line_start"],
                    "end": c["line_end"] or c["line_start"],
                    "reason": "callee",
                }
            )
            callee_files += 1
    test_files = 0
    for t in test_callers:
        if t["file_path"] not in seen and test_files < _MAX_TEST_FILES:
            seen.add(t["file_path"])
            files_to_read.append(
                {
                    "path": t["file_path"],
                    "start": t["line_start"],
                    "end": t["line_end"] or t["line_start"],
                    "reason": "test",
                }
            )
            test_files += 1
    for ti in test_importers:
        if ti["path"] not in seen and test_files < _MAX_TEST_FILES:
            seen.add(ti["path"])
            files_to_read.append(
                {
                    "path": ti["path"],
                    "start": 1,
                    "end": None,
                    "reason": "test",
                }
            )
            test_files += 1

    # Compute propagation scores for context-aware ranking
    prop_scores = _get_propagation_scores_for_paths(
        conn,
        [sym_id],
        use_propagation=use_propagation,
    )

    files_to_read = _rank_single_files(
        conn,
        files_to_read,
        task=task,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
        propagation_scores=prop_scores,
    )

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


def _collect_file_signals_for_batch_relevance(contexts):
    """Collect file reasons and edge hits used to compute batch relevance."""
    file_reasons = {}
    file_edges_to_query: defaultdict[str, int] = defaultdict(int)
    for ctx_data in contexts:
        for f in ctx_data["files_to_read"]:
            path = f["path"]
            file_reasons.setdefault(path, set()).add(f["reason"])
            if f["reason"] in ("caller", "callee"):
                file_edges_to_query[path] += 1
    return file_reasons, file_edges_to_query


def _load_edge_totals_for_batch_relevance(conn, paths):
    """Load per-file edge denominators needed by batch relevance scoring."""
    path_list = sorted({p for p in paths if p})
    if not path_list:
        return {}

    rows = batched_in(
        conn,
        "SELECT f.path AS path, COUNT(e.id) AS total_edges "
        "FROM files f "
        "LEFT JOIN symbols s ON s.file_id = f.id "
        "LEFT JOIN edges e ON e.source_id = s.id "
        "WHERE f.path IN ({ph}) "
        "GROUP BY f.id, f.path",
        path_list,
    )

    totals = {}
    for row in rows:
        try:
            path = row["path"]
            total_edges = row["total_edges"]
        except (KeyError, TypeError):
            path = row[0]
            total_edges = row[1]
        totals[path] = max(int(total_edges or 0), 1)
    return totals


def batch_context(conn, contexts, task=None, session_hint="", recent_symbols=(), use_propagation=True):
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

    file_reasons, file_edges_to_query = _collect_file_signals_for_batch_relevance(contexts)
    all_paths = list(file_reasons.keys())
    file_total_edges = _load_edge_totals_for_batch_relevance(conn, all_paths)

    scored_files = []
    for path in all_paths:
        edges_to_query = file_edges_to_query.get(path, 0)
        total_edges = file_total_edges.get(path, 1)
        relevance = round(edges_to_query / total_edges, 3) if total_edges > 0 else 0.0

        reasons = file_reasons[path]
        if "definition" in reasons:
            relevance = 1.0

        scored_files.append(
            {
                "path": path,
                "reasons": sorted(reasons),
                "relevance": relevance,
            }
        )

    # Compute propagation scores for context-aware ranking
    batch_sym_ids = [ctx_data["sym"]["id"] for ctx_data in contexts]
    batch_prop_scores = _get_propagation_scores_for_paths(
        conn,
        batch_sym_ids,
        use_propagation=use_propagation,
    )

    scored_files = _rank_batch_files(
        conn,
        scored_files,
        task=task,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
        propagation_scores=batch_prop_scores,
    )
    return shared_callers, shared_callees, scored_files


def summarize_tests(test_hits: list[dict], cap: int) -> tuple[list[dict], int, int]:
    """Collapse per-symbol test hits to file-level coverage hints.

    Each ``test_hits`` entry is a dict with ``file``, ``kind``
    (``"DIRECT"`` or otherwise), ``hops``, and optional ``via``. Returns
    ``(rows[:cap], direct_files, total_files)`` where ``rows`` is sorted
    by (direct-first, hops asc, file asc) and each row drops the
    internal ``_priority`` sort key.

    Hoisted from ``cmd_guard`` + ``cmd_plan_refactor`` (W856 detector
    flagged sim=0.947 between the two copies).
    """
    by_file: dict[str, dict] = {}

    for hit in test_hits:
        path = hit["file"]
        kind = hit["kind"]
        hops = int(hit["hops"])
        priority = (0 if kind == "DIRECT" else 1, hops)

        existing = by_file.get(path)
        if existing is None or priority < existing["_priority"]:
            by_file[path] = {
                "file": path,
                "kind": kind,
                "hops": hops,
                "via": hit.get("via"),
                "_priority": priority,
            }

    rows = sorted(
        by_file.values(),
        key=lambda r: (0 if r["kind"] == "DIRECT" else 1, r["hops"], r["file"]),
    )
    for r in rows:
        r.pop("_priority", None)

    direct_files = sum(1 for r in rows if r["kind"] == "DIRECT")
    return rows[:cap], direct_files, len(rows)


# ---------------------------------------------------------------------------
# Pre-edit uncertainty bundle (single-symbol composer)
# ---------------------------------------------------------------------------


def gather_task_bundle(
    conn,
    sym,
    task=None,
    session_hint="",
    recent_symbols=(),
    use_propagation=True,
    *,
    test_cap=10,
    coupling_limit=8,
):
    """Compose a single-symbol pre-edit uncertainty bundle into one JSON-first dict.

    Reduces recurrent pre-edit navigation into a single read-only envelope by
    composing the per-symbol helpers already defined in this module:

    * ``gather_symbol_context`` -> ranked ``files_to_read`` plus caller/callee
      file counts (the existing context helpers).
    * ``get_affected_tests_bfs`` + ``summarize_tests`` -> file-level test
      coverage (direct vs transitive) for the symbols an edit would break.
    * ``get_file_churn`` + ``get_coupling`` -> recent churn on the owning file
      and its temporal co-change partners.
    * ``get_blast_radius`` -> downstream dependent symbols/files.
    * ``gather_annotations`` -> human ``policy_hints`` (caveats attached to the
      symbol, e.g. deprecated / owns-transaction).

    Read-only: issues no writes and adds no DB queries beyond what those
    helpers already run (no index-schema change). Each section is best-effort
    and degrades to a neutral default so the bundle always emits a non-empty
    envelope even when an optional table is absent or a helper fails. Returns a
    plain dict of JSON-serializable scalars/lists/dicts (no ``sqlite3.Row``
    leakage) ready to hand to ``json_envelope(...)``.
    """
    sym_id = sym["id"]
    file_path = sym["file_path"]

    try:
        qualified_name = sym["qualified_name"] or sym["name"]
    except (KeyError, IndexError):
        qualified_name = sym["name"]

    # --- context (existing context helpers) ---
    try:
        ctx = gather_symbol_context(
            conn,
            sym,
            task=task,
            session_hint=session_hint,
            recent_symbols=recent_symbols,
            use_propagation=use_propagation,
        )
        context = {
            "files_to_read": ctx["files_to_read"],
            "caller_files": len(ctx["non_test_callers"]),
            "callee_files": len(ctx["callees"]),
            "sibling_files": len(ctx["siblings"]),
            "skipped_callers": ctx["skipped_callers"],
            "skipped_callees": ctx["skipped_callees"],
        }
    except Exception as _exc:  # noqa: BLE001 -- best-effort; degrade to empty
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_task_bundle:context", _exc)
        context = {}

    # --- affected tests (file-level collapse) ---
    try:
        raw_tests = get_affected_tests_bfs(conn, sym_id)
        test_rows, direct_files, total_files = summarize_tests(raw_tests, cap=test_cap)
        affected_tests = {
            "rows": test_rows,
            "direct_files": direct_files,
            "total_files": total_files,
        }
    except Exception as _exc:  # noqa: BLE001 -- best-effort; degrade to empty
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_task_bundle:affected_tests", _exc)
        affected_tests = {"rows": [], "direct_files": 0, "total_files": 0}

    # --- recent churn ---
    try:
        git_churn = get_file_churn(conn, file_path)
    except Exception as _exc:  # noqa: BLE001 -- best-effort; degrade to None
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_task_bundle:git_churn", _exc)
        git_churn = None

    try:
        coupling = get_coupling(conn, file_path, limit=coupling_limit)
    except Exception as _exc:  # noqa: BLE001 -- best-effort; degrade to empty
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_task_bundle:coupling", _exc)
        coupling = []

    # --- blast radius (downstream dependents) ---
    try:
        blast_radius = get_blast_radius(conn, sym_id)
    except Exception as _exc:  # noqa: BLE001 -- best-effort; degrade to None
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_task_bundle:blast_radius", _exc)
        blast_radius = None

    # --- policy hints (human annotations on the symbol) ---
    try:
        policy_hints = gather_annotations(conn, sym=sym)
    except Exception as _exc:  # noqa: BLE001 -- best-effort; degrade to empty
        from roam.observability import log_swallowed

        log_swallowed("context_helpers:gather_task_bundle:policy_hints", _exc)
        policy_hints = []

    return {
        "symbol": {
            "id": sym_id,
            "name": sym["name"],
            "kind": sym["kind"],
            "qualified_name": qualified_name,
            "file_path": file_path,
            "line_start": sym["line_start"],
            "line_end": sym["line_end"] or sym["line_start"],
        },
        "context": context,
        "affected_tests": affected_tests,
        "git_churn": git_churn,
        "coupling": coupling,
        "blast_radius": blast_radius,
        "policy_hints": policy_hints,
    }
