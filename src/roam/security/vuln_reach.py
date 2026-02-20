"""Reachability analysis for vulnerabilities in the call graph."""

from __future__ import annotations

import json
import sqlite3
from collections import deque

import networkx as nx


def _entry_points(G: nx.DiGraph) -> list[int]:
    """Find entry point nodes: in-degree 0 in the symbol graph."""
    return [n for n in G.nodes() if G.in_degree(n) == 0]


def _blast_radius(G: nx.DiGraph, node: int) -> int:
    """Count transitive dependents (ancestors in reverse graph)."""
    try:
        ancestors = nx.ancestors(G, node)
        return len(ancestors)
    except nx.NetworkXError:
        return 0


def _shortest_path_to(G: nx.DiGraph, entries: list[int], target: int) -> list[int] | None:
    """Find shortest path from any entry point to the target node.

    Returns the path as a list of node IDs, or None if unreachable.
    """
    best: list[int] | None = None
    for entry in entries:
        try:
            path = nx.shortest_path(G, entry, target)
            if best is None or len(path) < len(best):
                best = path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
    return best


def _node_name(G: nx.DiGraph, node_id: int) -> str:
    """Get the display name for a graph node."""
    data = G.nodes.get(node_id, {})
    return data.get("qualified_name") or data.get("name", str(node_id))


def analyze_reachability(conn: sqlite3.Connection, G: nx.DiGraph) -> list[dict]:
    """Analyze reachability for all vulnerabilities in the DB.

    For each vulnerability with a matched symbol:
    1. Find the matched symbol node in the graph
    2. Find entry points (in-degree 0)
    3. Check if vuln symbol is reachable from any entry point
    4. Compute shortest path and blast radius
    5. Update vulnerability record with results

    Returns list of analyzed vulns with reachability info.
    """
    rows = conn.execute(
        "SELECT id, cve_id, package_name, severity, title, "
        "matched_symbol_id, matched_file "
        "FROM vulnerabilities"
    ).fetchall()

    if not rows:
        return []

    entries = _entry_points(G)
    results: list[dict] = []

    for row in rows:
        vuln_id = row["id"]
        symbol_id = row["matched_symbol_id"]
        result = {
            "vuln_id": vuln_id,
            "cve_id": row["cve_id"],
            "package_name": row["package_name"],
            "severity": row["severity"],
            "title": row["title"],
            "matched_symbol_id": symbol_id,
            "matched_file": row["matched_file"],
            "reachable": 0,
            "path": [],
            "path_names": [],
            "hop_count": 0,
            "blast_radius": 0,
        }

        if symbol_id is not None and symbol_id in G:
            path = _shortest_path_to(G, entries, symbol_id)
            if path is not None:
                result["reachable"] = 1
                result["path"] = path
                result["path_names"] = [_node_name(G, n) for n in path]
                result["hop_count"] = len(path) - 1
            else:
                result["reachable"] = -1  # unreachable

            result["blast_radius"] = _blast_radius(G, symbol_id)

            # Update the DB record
            conn.execute(
                "UPDATE vulnerabilities SET reachable=?, shortest_path=?, hop_count=? "
                "WHERE id=?",
                (
                    result["reachable"],
                    json.dumps(result["path_names"]),
                    result["hop_count"],
                    vuln_id,
                ),
            )
        else:
            # No matched symbol -- cannot determine reachability
            result["reachable"] = 0

        results.append(result)

    return results


def reach_from_entry(conn: sqlite3.Connection, G: nx.DiGraph, entry_point: str) -> list[dict]:
    """Check which vulnerabilities are reachable from a specific entry point.

    Parameters
    ----------
    entry_point:
        Symbol name to use as the starting node.

    Returns list of reachable vulns from this entry point.
    """
    # Find the entry point node
    entry_ids: list[int] = []
    for nid, data in G.nodes(data=True):
        name = data.get("name", "")
        qname = data.get("qualified_name", "")
        if name == entry_point or qname == entry_point or entry_point in (qname or ""):
            entry_ids.append(nid)

    if not entry_ids:
        return []

    # Find all nodes reachable from entry
    reachable_nodes: set[int] = set()
    for eid in entry_ids:
        reachable_nodes.update(nx.descendants(G, eid))
        reachable_nodes.add(eid)

    # Check each vulnerability
    rows = conn.execute(
        "SELECT id, cve_id, package_name, severity, title, "
        "matched_symbol_id, matched_file "
        "FROM vulnerabilities"
    ).fetchall()

    results: list[dict] = []
    for row in rows:
        symbol_id = row["matched_symbol_id"]
        if symbol_id is None or symbol_id not in G:
            continue
        if symbol_id not in reachable_nodes:
            continue

        # Find path from the closest entry to the vuln symbol
        best_path: list[int] | None = None
        for eid in entry_ids:
            try:
                path = nx.shortest_path(G, eid, symbol_id)
                if best_path is None or len(path) < len(best_path):
                    best_path = path
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

        path_names = [_node_name(G, n) for n in best_path] if best_path else []
        results.append({
            "vuln_id": row["id"],
            "cve_id": row["cve_id"],
            "package_name": row["package_name"],
            "severity": row["severity"],
            "title": row["title"],
            "matched_file": row["matched_file"],
            "reachable": True,
            "path": best_path or [],
            "path_names": path_names,
            "hop_count": len(best_path) - 1 if best_path else 0,
            "blast_radius": _blast_radius(G, symbol_id),
        })

    return results


def reach_for_cve(conn: sqlite3.Connection, G: nx.DiGraph, cve_id: str) -> dict:
    """Detailed reachability analysis for a specific CVE.

    Returns a dict with full reachability information for the given CVE.
    """
    row = conn.execute(
        "SELECT id, cve_id, package_name, severity, title, "
        "matched_symbol_id, matched_file "
        "FROM vulnerabilities WHERE cve_id = ?",
        (cve_id,),
    ).fetchone()

    if row is None:
        return {"error": f"CVE {cve_id} not found", "cve_id": cve_id}

    symbol_id = row["matched_symbol_id"]
    result: dict = {
        "vuln_id": row["id"],
        "cve_id": row["cve_id"],
        "package_name": row["package_name"],
        "severity": row["severity"],
        "title": row["title"],
        "matched_symbol_id": symbol_id,
        "matched_file": row["matched_file"],
        "reachable": False,
        "path": [],
        "path_names": [],
        "hop_count": 0,
        "blast_radius": 0,
        "entry_points_reaching": [],
    }

    if symbol_id is None or symbol_id not in G:
        return result

    entries = _entry_points(G)
    result["blast_radius"] = _blast_radius(G, symbol_id)

    # Check which entry points can reach the vuln
    best_path: list[int] | None = None
    reaching_entries: list[str] = []

    for eid in entries:
        try:
            path = nx.shortest_path(G, eid, symbol_id)
            reaching_entries.append(_node_name(G, eid))
            if best_path is None or len(path) < len(best_path):
                best_path = path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    if best_path is not None:
        result["reachable"] = True
        result["path"] = best_path
        result["path_names"] = [_node_name(G, n) for n in best_path]
        result["hop_count"] = len(best_path) - 1

    result["entry_points_reaching"] = reaching_entries
    return result
