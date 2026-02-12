"""Find unprotected entry points — symbols with no path to a required gate."""

import re
from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


def _find_gates(conn, gate_names, gate_pattern):
    """Find gate symbol IDs by exact name or regex pattern."""
    gates = set()
    gate_info = {}

    if gate_names:
        names = [n.strip() for n in gate_names.split(",") if n.strip()]
        for name in names:
            rows = conn.execute(
                "SELECT s.id, s.name, f.path as file_path, s.line_start "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = ?",
                (name,),
            ).fetchall()
            for r in rows:
                gates.add(r["id"])
                gate_info[r["id"]] = r["name"]

    if gate_pattern:
        regex = re.compile(gate_pattern, re.IGNORECASE)
        rows = conn.execute(
            "SELECT s.id, s.name, f.path as file_path, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
        ).fetchall()
        for r in rows:
            if regex.search(r["name"]):
                gates.add(r["id"])
                gate_info[r["id"]] = r["name"]

    return gates, gate_info


def _find_entries(conn, scope, entry_pattern):
    """Find entry point symbols — exported top-level functions, optionally scoped."""
    sql = (
        "SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.is_exported = 1 AND s.kind IN ('function', 'method') "
        "AND s.parent_id IS NULL "
    )
    params = []

    if scope:
        # Convert glob to LIKE pattern
        like = scope.replace("*", "%").replace("?", "_")
        sql += "AND f.path LIKE ? "
        params.append(like)

    sql += "ORDER BY f.path, s.line_start"
    rows = conn.execute(sql, params).fetchall()

    if entry_pattern:
        regex = re.compile(entry_pattern, re.IGNORECASE)
        rows = [r for r in rows if regex.search(r["name"])]

    return rows


def _build_adj(conn):
    """Build adjacency list from edges table (source → [targets])."""
    adj = defaultdict(set)
    for e in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj[e["source_id"]].add(e["target_id"])
    return adj


def _bfs_to_gate(adj, start_id, gates, max_depth):
    """BFS from start_id to find shortest path to any gate symbol.

    Returns (gate_name, depth, chain) or (None, None, None) if not found.
    """
    if start_id in gates:
        return start_id, 0, [start_id]

    visited = {start_id}
    # Queue entries: (node_id, depth, path)
    queue = [(start_id, 0, [start_id])]

    while queue:
        current, depth, path = queue.pop(0)
        if depth >= max_depth:
            continue
        for neighbor in adj.get(current, set()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            new_path = path + [neighbor]
            if neighbor in gates:
                return neighbor, depth + 1, new_path
            queue.append((neighbor, depth + 1, new_path))

    return None, None, None


@click.command("coverage-gaps")
@click.option("--gate", "gate_names", default=None,
              help="Comma-separated gate symbol names (e.g. 'requireAuth,validateToken')")
@click.option("--gate-pattern", "gate_pattern", default=None,
              help="Regex to match gate symbols by name (e.g. 'auth|permission|guard')")
@click.option("--scope", default=None,
              help="File scope glob (e.g. 'app/routes/**')")
@click.option("--entry-pattern", "entry_pattern", default=None,
              help="Regex to filter entry points by name (e.g. 'handler|controller')")
@click.option("--max-depth", default=8, show_default=True, help="Max BFS depth")
@click.pass_context
def coverage_gaps(ctx, gate_names, gate_pattern, scope, entry_pattern, max_depth):
    """Find entry points with no path to a required gate symbol.

    Use --gate for exact names or --gate-pattern for regex matching.
    Searches the call graph to find which entry points can reach a gate
    and which are unprotected.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    if not gate_names and not gate_pattern:
        click.echo("Provide --gate <names> or --gate-pattern <regex>")
        raise SystemExit(1)

    with open_db(readonly=True) as conn:
        gates, gate_info = _find_gates(conn, gate_names, gate_pattern)

        if not gates:
            if json_mode:
                click.echo(to_json(json_envelope("coverage-gaps",
                    summary={"error": "No gate symbols found"},
                )))
            else:
                click.echo("No gate symbols found matching the criteria.")
            return

        entries = _find_entries(conn, scope, entry_pattern)
        if not entries:
            if json_mode:
                click.echo(to_json(json_envelope("coverage-gaps",
                    summary={"error": "No entry points found"},
                )))
            else:
                click.echo("No entry points found in scope.")
            return

        adj = _build_adj(conn)

        # Resolve symbol names for chain display
        id_to_name = {}
        all_ids = set()
        for e in entries:
            all_ids.add(e["id"])
        for g in gates:
            all_ids.add(g)
        # Batch fetch names
        if all_ids:
            ph = ",".join("?" for _ in all_ids)
            for r in conn.execute(
                f"SELECT id, name FROM symbols WHERE id IN ({ph})",
                list(all_ids),
            ).fetchall():
                id_to_name[r["id"]] = r["name"]

        covered = []
        uncovered = []

        for entry in entries:
            gate_id, depth, chain = _bfs_to_gate(adj, entry["id"], gates, max_depth)
            if gate_id is not None:
                # Resolve chain names (lazy — fetch as needed)
                chain_names = []
                for sid in chain:
                    if sid not in id_to_name:
                        r = conn.execute("SELECT name FROM symbols WHERE id = ?", (sid,)).fetchone()
                        id_to_name[sid] = r["name"] if r else "?"
                    chain_names.append(id_to_name[sid])

                covered.append({
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "file": entry["file_path"],
                    "line": entry["line_start"],
                    "gate": gate_info.get(gate_id, "?"),
                    "depth": depth,
                    "chain": chain_names,
                })
            else:
                uncovered.append({
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "file": entry["file_path"],
                    "line": entry["line_start"],
                    "reason": f"no gate in call chain (searched {max_depth} hops)",
                })

        total = len(entries)
        coverage_pct = round(len(covered) * 100 / total, 1) if total else 0

        if json_mode:
            click.echo(to_json(json_envelope("coverage-gaps",
                summary={
                    "total_entries": total,
                    "covered": len(covered),
                    "uncovered": len(uncovered),
                    "coverage_pct": coverage_pct,
                    "gates_found": sorted(set(gate_info.values())),
                },
                gates_found=sorted(set(gate_info.values())),
                uncovered=uncovered,
                covered=covered,
            )))
            return

        # --- Text output ---
        click.echo(f"=== Coverage Gaps ===\n")
        click.echo(f"Gates: {', '.join(sorted(set(gate_info.values())))}")
        click.echo(f"Entry points: {total}  Covered: {len(covered)}  "
                    f"Uncovered: {len(uncovered)}  Coverage: {coverage_pct}%")
        click.echo()

        if uncovered:
            click.echo(f"-- Uncovered ({len(uncovered)}) --")
            rows = []
            for u in uncovered[:30]:
                rows.append([
                    u["name"], abbrev_kind(u["kind"]),
                    loc(u["file"], u["line"]),
                    u["reason"],
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location", "Reason"],
                rows,
                budget=30,
            ))
            click.echo()

        if covered:
            click.echo(f"-- Covered ({len(covered)}) --")
            rows = []
            for c in covered[:20]:
                chain_str = " -> ".join(c["chain"][:5])
                if len(c["chain"]) > 5:
                    chain_str += f" (+{len(c['chain']) - 5})"
                rows.append([
                    c["name"], abbrev_kind(c["kind"]),
                    loc(c["file"], c["line"]),
                    c["gate"], str(c["depth"]),
                    chain_str,
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location", "Gate", "Depth", "Chain"],
                rows,
                budget=20,
            ))
