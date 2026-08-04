"""Aggregated cross-repo analysis commands."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from roam.commands.metrics_history import (
    LEGACY_METRICS_VERSION,
    snapshots_have_metrics_version,
)
from roam.observability import log_swallowed
from roam.workspace.db import get_cross_edges


def aggregate_understand(ws_conn: sqlite3.Connection, repo_infos: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a unified workspace understand report.

    Queries each repo's own DB for stats and combines with
    cross-repo edge data from the workspace DB.
    """
    repos_data = []
    total_files = 0
    total_symbols = 0
    total_edges = 0

    for info in repo_infos:
        repo_data = _query_repo_stats(info)
        repos_data.append(repo_data)
        total_files += repo_data.get("files", 0)
        total_symbols += repo_data.get("symbols", 0)
        total_edges += repo_data.get("edges", 0)

    cross_edges = get_cross_edges(ws_conn)
    edge_groups = _group_cross_edges(cross_edges)

    return {
        "total_files": total_files,
        "total_symbols": total_symbols,
        "total_edges": total_edges,
        "repos": repos_data,
        "cross_repo_edges": len(cross_edges),
        "cross_repo_connections": edge_groups,
    }


def aggregate_health(ws_conn: sqlite3.Connection, repo_infos: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a unified workspace health report.

    W1460 — the workspace average is taken over ONE metrics definition. Repos
    are indexed independently, so an upgrade rolls through a workspace repo by
    repo; in between, some DBs hold scores under the old definition and some
    under the new. The pre-fix code averaged them with no version check at
    all, so the workspace number moved as reindexing progressed and nothing
    in the output attributed that to anything but the code.

    Rule: keep the NEWEST version present in the workspace and average only
    those repos. A workspace where nobody has reindexed yet is uniformly
    legacy and stays fully included — the average is internally consistent,
    which is the property that matters. Repos left out are counted and named,
    never silently dropped.
    """
    repos_health = []

    for info in repo_infos:
        repos_health.append(_query_repo_health(info))

    scored = [h for h in repos_health if h.get("health_score") is not None]
    versions = {h.get("metrics_version") or LEGACY_METRICS_VERSION for h in scored}
    newest_version = max(versions) if versions else None

    included = [h for h in scored if (h.get("metrics_version") or LEGACY_METRICS_VERSION) == newest_version]
    excluded = [h for h in scored if h not in included]

    scores = [h["health_score"] for h in included]

    cross_edges = get_cross_edges(ws_conn)
    avg_score = sum(scores) / len(scores) if scores else 0

    # Cross-repo coupling assessment
    coupling_verdict = "low"
    if len(cross_edges) > 50:
        coupling_verdict = "high"
    elif len(cross_edges) > 20:
        coupling_verdict = "moderate"

    out: dict[str, Any] = {
        "workspace_health": round(avg_score),
        "repos": repos_health,
        "cross_repo_edges": len(cross_edges),
        "coupling_verdict": coupling_verdict,
        "health_metrics_version": newest_version,
        "repos_scored": len(included),
    }
    if excluded:
        out["metrics_version_mixed"] = True
        out["partial_success"] = True
        out["repos_excluded_metrics_version"] = [h["name"] for h in excluded]
    return out


def cross_repo_context(
    ws_conn: sqlite3.Connection, symbol_name: str, repo_infos: list[dict[str, Any]]
) -> dict[str, Any]:
    """Find a symbol across repos and return cross-repo context.

    Searches each repo DB for the symbol, then augments with
    cross-repo edges from the workspace DB.
    """
    found_in = []
    cross_edges_for_symbol = []

    for info in repo_infos:
        db_path = Path(info["db_path"])
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            for row in _find_symbol_matches(conn, symbol_name):
                found_in.append(_symbol_context_entry(conn, info["name"], row))
                cross_edges_for_symbol.extend(_workspace_edges_for_symbol(ws_conn, info["name"], row["id"]))
        finally:
            conn.close()

    return {
        "symbol": symbol_name,
        "found_in": found_in,
        "cross_repo_edges": cross_edges_for_symbol,
    }


def _find_symbol_matches(conn: sqlite3.Connection, symbol_name: str) -> list[sqlite3.Row]:
    """Find repo-local symbols matching a user-supplied name."""
    return conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.signature, "
        "  s.line_start, s.line_end, f.path AS file_path "
        "FROM symbols s "
        "JOIN files f ON f.id = s.file_id "
        "WHERE s.name = ? OR s.qualified_name = ? "
        "OR s.name LIKE ?",
        (symbol_name, symbol_name, f"%{symbol_name}%"),
    ).fetchall()


def _symbol_context_entry(conn: sqlite3.Connection, repo_name: str, row: sqlite3.Row) -> dict[str, Any]:
    """Build the repo-local context payload for one symbol row."""
    callers = conn.execute(
        "SELECT s.name, s.kind, f.path, e.line "
        "FROM edges e "
        "JOIN symbols s ON s.id = e.source_id "
        "JOIN files f ON f.id = s.file_id "
        "WHERE e.target_id = ? LIMIT 10",
        (row["id"],),
    ).fetchall()
    callees = conn.execute(
        "SELECT s.name, s.kind, f.path, e.line "
        "FROM edges e "
        "JOIN symbols s ON s.id = e.target_id "
        "JOIN files f ON f.id = s.file_id "
        "WHERE e.source_id = ? LIMIT 10",
        (row["id"],),
    ).fetchall()

    return {
        "repo": repo_name,
        "symbol_id": row["id"],
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "kind": row["kind"],
        "signature": row["signature"],
        "file_path": row["file_path"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "callers": [_call_edge_context(c) for c in callers],
        "callees": [_call_edge_context(c) for c in callees],
    }


def _call_edge_context(row: sqlite3.Row) -> dict[str, Any]:
    """Format a local caller/callee edge for context output."""
    return {
        "name": row["name"],
        "kind": row["kind"],
        "file": row["path"],
        "line": row["line"],
    }


def _workspace_edges_for_symbol(ws_conn: sqlite3.Connection, repo_name: str, symbol_id: int) -> list[dict[str, Any]]:
    """Find workspace cross-repo edges touching one repo-local symbol."""
    ws_edges = ws_conn.execute(
        "SELECT e.*, "
        "  sr.name AS source_repo_name, "
        "  tr.name AS target_repo_name "
        "FROM ws_cross_edges e "
        "JOIN ws_repos sr ON sr.id = e.source_repo_id "
        "JOIN ws_repos tr ON tr.id = e.target_repo_id "
        "WHERE (sr.name=? AND e.source_symbol_id=?) "
        "   OR (tr.name=? AND e.target_symbol_id=?)",
        (repo_name, symbol_id, repo_name, symbol_id),
    ).fetchall()

    return [_workspace_edge_context(edge) for edge in ws_edges]


def _workspace_edge_context(edge: sqlite3.Row) -> dict[str, Any]:
    """Format a workspace edge for context output."""
    meta = json.loads(edge["metadata"]) if edge["metadata"] else {}
    return {
        "source_repo": edge["source_repo_name"],
        "target_repo": edge["target_repo_name"],
        "kind": edge["kind"],
        "url_pattern": meta.get("url_pattern", ""),
        "http_method": meta.get("http_method", ""),
    }


def cross_repo_trace(
    ws_conn: sqlite3.Connection,
    source_name: str,
    target_name: str,
    repo_infos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Trace a path between symbols that may be in different repos.

    First tries intra-repo traces, then looks for cross-repo edges
    that bridge the gap.
    """
    source_locations = []
    target_locations = []

    for info in repo_infos:
        db_path = Path(info["db_path"])
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            for row in conn.execute(
                "SELECT s.id, s.name, s.kind, f.path "
                "FROM symbols s JOIN files f ON f.id=s.file_id "
                "WHERE s.name=? OR s.qualified_name=?",
                (source_name, source_name),
            ).fetchall():
                source_locations.append(
                    {
                        "repo": info["name"],
                        "id": row["id"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "file": row["path"],
                    }
                )

            for row in conn.execute(
                "SELECT s.id, s.name, s.kind, f.path "
                "FROM symbols s JOIN files f ON f.id=s.file_id "
                "WHERE s.name=? OR s.qualified_name=?",
                (target_name, target_name),
            ).fetchall():
                target_locations.append(
                    {
                        "repo": info["name"],
                        "id": row["id"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "file": row["path"],
                    }
                )
        finally:
            conn.close()

    # Find cross-repo edges that connect source repo to target repo
    bridge_edges = []
    cross_edges = get_cross_edges(ws_conn)
    for edge in cross_edges:
        meta = json.loads(edge["metadata"]) if edge["metadata"] else {}

        # Check if this edge connects source -> target
        for src in source_locations:
            for tgt in target_locations:
                if edge["source_repo_name"] == src["repo"] and edge["target_repo_name"] == tgt["repo"]:
                    bridge_edges.append(
                        {
                            "source_repo": edge["source_repo_name"],
                            "source_symbol_id": edge["source_symbol_id"],
                            "target_repo": edge["target_repo_name"],
                            "target_symbol_id": edge["target_symbol_id"],
                            "kind": edge["kind"],
                            "url_pattern": meta.get("url_pattern", ""),
                            "http_method": meta.get("http_method", ""),
                        }
                    )

    # Same repo? Use the repo's own trace capabilities
    same_repo = source_locations and target_locations and source_locations[0]["repo"] == target_locations[0]["repo"]

    return {
        "source": {"name": source_name, "locations": source_locations},
        "target": {"name": target_name, "locations": target_locations},
        "same_repo": same_repo,
        "bridge_edges": bridge_edges,
        "verdict": _trace_verdict(source_locations, target_locations, bridge_edges),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _query_repo_stats(info: dict[str, Any]) -> dict[str, Any]:
    """Query basic stats from a repo's own DB."""
    db_path = Path(info["db_path"])
    result = {
        "name": info["name"],
        "role": info.get("role", ""),
        "path": str(info.get("path", "")),
        "files": 0,
        "symbols": 0,
        "edges": 0,
        "languages": [],
        "key_symbols": [],
        "indexed": False,
    }

    if not db_path.exists():
        return result

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result["files"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        result["symbols"] = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        result["edges"] = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # Language breakdown
        langs = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM files "
            "WHERE language IS NOT NULL "
            "GROUP BY language ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        result["languages"] = [{"language": r["language"], "files": r["cnt"]} for r in langs]

        # Key symbols (by PageRank)
        try:
            top = conn.execute(
                "SELECT s.name, s.kind, gm.pagerank "
                "FROM graph_metrics gm "
                "JOIN symbols s ON s.id = gm.symbol_id "
                "ORDER BY gm.pagerank DESC LIMIT 5"
            ).fetchall()
            result["key_symbols"] = [
                {"name": r["name"], "kind": r["kind"], "pagerank": round(r["pagerank"], 6)} for r in top
            ]
        except sqlite3.OperationalError as exc:
            log_swallowed("workspace.aggregator:query_repo_stats.key_symbols", exc)

        result["indexed"] = True
        result["index_age_s"] = int(time.time() - db_path.stat().st_mtime)
    finally:
        conn.close()

    return result


def _query_repo_health(info: dict[str, Any]) -> dict[str, Any]:
    """Query health metrics from a repo's own DB."""
    db_path = Path(info["db_path"])
    result = {
        "name": info["name"],
        "role": info.get("role", ""),
        "health_score": None,
        "files": 0,
        "symbols": 0,
        "cycles": 0,
        "metrics_version": None,
    }

    if not db_path.exists():
        return result

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result["files"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        result["symbols"] = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

        # Try to get the latest snapshot health score.
        #
        # W1460: carry the metrics-definition version alongside the score.
        # Repos in one workspace are indexed independently, so a partial
        # upgrade leaves some DBs on the old definition and some on the new —
        # and ``aggregate_health`` averages them into one workspace number.
        # A mean over two different definitions of "health" is not a health
        # score, and nothing about the output said so.
        #
        # The column is PROBED, not assumed. A workspace member's DB may not
        # have been reopened read-write since the upgrade, so it can still
        # hold the pre-W1460 table shape. Naming the column unconditionally
        # would raise OperationalError into the handler below and drop that
        # repo's health_score entirely — turning a version question into
        # missing data.
        version_col = "metrics_version" if snapshots_have_metrics_version(conn) else "NULL AS metrics_version"
        try:
            snap = conn.execute(
                f"SELECT health_score, cycles, {version_col} FROM snapshots ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if snap:
                result["health_score"] = snap["health_score"]
                result["cycles"] = snap["cycles"] or 0
                result["metrics_version"] = (
                    snap["metrics_version"] if snap["metrics_version"] is not None else LEGACY_METRICS_VERSION
                )
        except sqlite3.OperationalError as exc:
            # Snapshot metadata is optional for partial or older repo DBs; keep the basic counts.
            log_swallowed("workspace.aggregator:query_repo_health.snapshot", exc)
    finally:
        conn.close()

    return result


def _group_cross_edges(cross_edges: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Group cross-repo edges by repo pair and summarize."""
    groups: dict[tuple[str, str], list] = {}
    for edge in cross_edges:
        key = (edge["source_repo_name"], edge["target_repo_name"])
        groups.setdefault(key, []).append(edge)

    result = []
    for (src_repo, tgt_repo), edges in groups.items():
        # Group by kind
        by_kind: dict[str, int] = {}
        sample_edges = []
        for e in edges:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
            if len(sample_edges) < 5:
                meta = json.loads(e["metadata"]) if e["metadata"] else {}
                sample_edges.append(
                    {
                        "kind": e["kind"],
                        "url_pattern": meta.get("url_pattern", ""),
                        "http_method": meta.get("http_method", ""),
                    }
                )

        result.append(
            {
                "source_repo": src_repo,
                "target_repo": tgt_repo,
                "edge_count": len(edges),
                "by_kind": by_kind,
                "samples": sample_edges,
            }
        )

    return result


def _trace_verdict(source_locs: list, target_locs: list, bridges: list) -> str:
    """Generate a human-readable trace verdict."""
    if not source_locs:
        return "Source symbol not found in any repo"
    if not target_locs:
        return "Target symbol not found in any repo"

    src_repos = {s["repo"] for s in source_locs}
    tgt_repos = {t["repo"] for t in target_locs}

    if src_repos & tgt_repos:
        common = src_repos & tgt_repos
        return f"Both symbols in same repo ({', '.join(common)}); use `roam trace` within that repo"

    if bridges:
        return f"Cross-repo path: {source_locs[0]['repo']} -> {target_locs[0]['repo']} via {len(bridges)} API edge(s)"

    return f"No direct cross-repo path found between {', '.join(src_repos)} and {', '.join(tgt_repos)}"
