"""Aggregated cross-repo analysis commands."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from roam.workspace.db import get_repos, get_cross_edges


def aggregate_understand(ws_conn: sqlite3.Connection,
                         repo_infos: list[dict[str, Any]]) -> dict[str, Any]:
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


def aggregate_health(ws_conn: sqlite3.Connection,
                     repo_infos: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a unified workspace health report."""
    repos_health = []
    scores = []

    for info in repo_infos:
        health = _query_repo_health(info)
        repos_health.append(health)
        if health.get("health_score") is not None:
            scores.append(health["health_score"])

    cross_edges = get_cross_edges(ws_conn)
    avg_score = sum(scores) / len(scores) if scores else 0

    # Cross-repo coupling assessment
    coupling_verdict = "low"
    if len(cross_edges) > 50:
        coupling_verdict = "high"
    elif len(cross_edges) > 20:
        coupling_verdict = "moderate"

    return {
        "workspace_health": round(avg_score),
        "repos": repos_health,
        "cross_repo_edges": len(cross_edges),
        "coupling_verdict": coupling_verdict,
    }


def cross_repo_context(ws_conn: sqlite3.Connection,
                       symbol_name: str,
                       repo_infos: list[dict[str, Any]]) -> dict[str, Any]:
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
            rows = conn.execute(
                "SELECT s.id, s.name, s.qualified_name, s.kind, s.signature, "
                "  s.line_start, s.line_end, f.path AS file_path "
                "FROM symbols s "
                "JOIN files f ON f.id = s.file_id "
                "WHERE s.name = ? OR s.qualified_name = ? "
                "OR s.name LIKE ?",
                (symbol_name, symbol_name, f"%{symbol_name}%"),
            ).fetchall()

            for row in rows:
                # Get callers and callees
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

                found_in.append({
                    "repo": info["name"],
                    "symbol_id": row["id"],
                    "name": row["name"],
                    "qualified_name": row["qualified_name"],
                    "kind": row["kind"],
                    "signature": row["signature"],
                    "file_path": row["file_path"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "callers": [
                        {"name": c["name"], "kind": c["kind"],
                         "file": c["path"], "line": c["line"]}
                        for c in callers
                    ],
                    "callees": [
                        {"name": c["name"], "kind": c["kind"],
                         "file": c["path"], "line": c["line"]}
                        for c in callees
                    ],
                })

                # Cross-repo edges for this symbol
                ws_edges = ws_conn.execute(
                    "SELECT e.*, "
                    "  sr.name AS source_repo_name, "
                    "  tr.name AS target_repo_name "
                    "FROM ws_cross_edges e "
                    "JOIN ws_repos sr ON sr.id = e.source_repo_id "
                    "JOIN ws_repos tr ON tr.id = e.target_repo_id "
                    "WHERE (sr.name=? AND e.source_symbol_id=?) "
                    "   OR (tr.name=? AND e.target_symbol_id=?)",
                    (info["name"], row["id"], info["name"], row["id"]),
                ).fetchall()

                for edge in ws_edges:
                    meta = json.loads(edge["metadata"]) if edge["metadata"] else {}
                    cross_edges_for_symbol.append({
                        "source_repo": edge["source_repo_name"],
                        "target_repo": edge["target_repo_name"],
                        "kind": edge["kind"],
                        "url_pattern": meta.get("url_pattern", ""),
                        "http_method": meta.get("http_method", ""),
                    })
        finally:
            conn.close()

    return {
        "symbol": symbol_name,
        "found_in": found_in,
        "cross_repo_edges": cross_edges_for_symbol,
    }


def cross_repo_trace(ws_conn: sqlite3.Connection,
                     source_name: str,
                     target_name: str,
                     repo_infos: list[dict[str, Any]]) -> dict[str, Any]:
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
                source_locations.append({
                    "repo": info["name"], "id": row["id"],
                    "name": row["name"], "kind": row["kind"],
                    "file": row["path"],
                })

            for row in conn.execute(
                "SELECT s.id, s.name, s.kind, f.path "
                "FROM symbols s JOIN files f ON f.id=s.file_id "
                "WHERE s.name=? OR s.qualified_name=?",
                (target_name, target_name),
            ).fetchall():
                target_locations.append({
                    "repo": info["name"], "id": row["id"],
                    "name": row["name"], "kind": row["kind"],
                    "file": row["path"],
                })
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
                if (edge["source_repo_name"] == src["repo"]
                        and edge["target_repo_name"] == tgt["repo"]):
                    bridge_edges.append({
                        "source_repo": edge["source_repo_name"],
                        "source_symbol_id": edge["source_symbol_id"],
                        "target_repo": edge["target_repo_name"],
                        "target_symbol_id": edge["target_symbol_id"],
                        "kind": edge["kind"],
                        "url_pattern": meta.get("url_pattern", ""),
                        "http_method": meta.get("http_method", ""),
                    })

    # Same repo? Use the repo's own trace capabilities
    same_repo = (
        source_locations and target_locations
        and source_locations[0]["repo"] == target_locations[0]["repo"]
    )

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
        result["languages"] = [
            {"language": r["language"], "files": r["cnt"]} for r in langs
        ]

        # Key symbols (by PageRank)
        try:
            top = conn.execute(
                "SELECT s.name, s.kind, gm.pagerank "
                "FROM graph_metrics gm "
                "JOIN symbols s ON s.id = gm.symbol_id "
                "ORDER BY gm.pagerank DESC LIMIT 5"
            ).fetchall()
            result["key_symbols"] = [
                {"name": r["name"], "kind": r["kind"],
                 "pagerank": round(r["pagerank"], 6)}
                for r in top
            ]
        except sqlite3.OperationalError:
            pass

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
    }

    if not db_path.exists():
        return result

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result["files"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        result["symbols"] = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

        # Try to get the latest snapshot health score
        try:
            snap = conn.execute(
                "SELECT health_score, cycles FROM snapshots "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if snap:
                result["health_score"] = snap["health_score"]
                result["cycles"] = snap["cycles"] or 0
        except sqlite3.OperationalError:
            pass
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
                sample_edges.append({
                    "kind": e["kind"],
                    "url_pattern": meta.get("url_pattern", ""),
                    "http_method": meta.get("http_method", ""),
                })

        result.append({
            "source_repo": src_repo,
            "target_repo": tgt_repo,
            "edge_count": len(edges),
            "by_kind": by_kind,
            "samples": sample_edges,
        })

    return result


def _trace_verdict(source_locs: list, target_locs: list,
                   bridges: list) -> str:
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
        return (
            f"Cross-repo path: {source_locs[0]['repo']} -> "
            f"{target_locs[0]['repo']} via {len(bridges)} API edge(s)"
        )

    return (
        f"No direct cross-repo path found between "
        f"{', '.join(src_repos)} and {', '.join(tgt_repos)}"
    )
