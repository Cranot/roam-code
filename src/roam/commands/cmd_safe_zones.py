"""Identify safe refactoring boundaries for a symbol or file."""

from __future__ import annotations

from collections import deque

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


def _bfs(graph, start_ids, max_depth, direction="forward"):
    """BFS traversal returning visited node IDs with their depths.

    Parameters
    ----------
    graph : nx.DiGraph
        The symbol graph.
    start_ids : set[int]
        Seed node IDs.
    max_depth : int
        Maximum BFS depth.
    direction : str
        ``"forward"`` follows outgoing edges (callees),
        ``"backward"`` follows incoming edges (callers via reverse).

    Returns
    -------
    dict[int, int]
        Mapping of visited node ID to its BFS depth.
    """
    visited: dict[int, int] = {}
    queue: deque[tuple[int, int]] = deque()

    for sid in start_ids:
        if sid in graph:
            visited[sid] = 0
            queue.append((sid, 0))

    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        if direction == "forward":
            neighbors = graph.successors(node)
        else:
            neighbors = graph.predecessors(node)
        for nb in neighbors:
            if nb not in visited:
                visited[nb] = depth + 1
                queue.append((nb, depth + 1))

    return visited


def _resolve_file_symbols(conn, target):
    """If *target* looks like a file path, return all symbol IDs in that file."""
    # Normalize separators for matching
    normalized = target.replace("\\", "/")

    row = conn.execute(
        "SELECT id FROM files WHERE path = ? OR path LIKE ?",
        (normalized, f"%{normalized}"),
    ).fetchone()
    if row is None:
        return None, None

    file_id = row["id"]
    syms = conn.execute(
        "SELECT id FROM symbols WHERE file_id = ?", (file_id,)
    ).fetchall()
    return file_id, {s["id"] for s in syms}


def _classify_zone(boundary_count):
    """Return (zone_label, zone_description)."""
    if boundary_count == 0:
        return "ISOLATED", "no external connections -- safe to refactor freely"
    if boundary_count <= 5:
        return "CONTAINED", f"{boundary_count} boundary symbols -- refactor with minor API contract awareness"
    return "EXPOSED", f"{boundary_count} boundary symbols -- refactor carefully, many consumers"


@click.command("safe-zones")
@click.argument("target")
@click.option("--depth", default=5, type=int, show_default=True,
              help="Max BFS depth for propagation analysis.")
@click.pass_context
def safe_zones(ctx, target, depth):
    """Identify safe refactoring boundaries for a symbol or file.

    Answers: if I refactor TARGET, what is the containment boundary?
    How far can changes propagate?

    TARGET is a symbol name (or file:symbol) or a file path.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # --- Resolve target to seed symbol IDs ---
        file_target = False
        seed_ids: set[int] = set()
        target_label = target

        # Try as file first
        file_id, file_syms = _resolve_file_symbols(conn, target)
        if file_syms:
            seed_ids = file_syms
            file_target = True
            # Fetch the canonical path for display
            frow = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
            if frow:
                target_label = frow["path"]
        else:
            # Try as symbol
            sym = find_symbol(conn, target)
            if sym is None:
                click.echo(f"Symbol or file not found: {target}")
                raise SystemExit(1)
            seed_ids = {sym["id"]}
            target_label = sym["qualified_name"] or sym["name"]

        # --- Build graph ---
        try:
            from roam.graph.builder import build_symbol_graph
        except ImportError:
            click.echo("Graph module not available. Run `roam index` first.")
            return

        G = build_symbol_graph(conn)

        # Filter seed_ids to nodes present in the graph
        seed_ids = {s for s in seed_ids if s in G}
        if not seed_ids:
            click.echo("Target symbol(s) not found in the dependency graph.")
            return

        # --- BFS forward (callees / downstream) and backward (callers / upstream) ---
        forward = _bfs(G, seed_ids, depth, direction="forward")
        backward = _bfs(G, seed_ids, depth, direction="backward")

        # Internal zone = union of forward and backward, including seeds
        internal_ids = set(forward.keys()) | set(backward.keys())

        # --- Identify boundary symbols ---
        # A boundary symbol is an internal node that has at least one
        # neighbor outside the internal zone.
        boundary_ids: set[int] = set()
        external_caller_count: dict[int, int] = {}  # boundary_id -> count of external callers
        external_callee_count: dict[int, int] = {}   # boundary_id -> count of external callees

        for nid in internal_ids:
            if nid not in G:
                continue
            # Check outgoing edges to external nodes
            for succ in G.successors(nid):
                if succ not in internal_ids:
                    boundary_ids.add(nid)
                    external_callee_count[nid] = external_callee_count.get(nid, 0) + 1
            # Check incoming edges from external nodes
            for pred in G.predecessors(nid):
                if pred not in internal_ids:
                    boundary_ids.add(nid)
                    external_caller_count[nid] = external_caller_count.get(nid, 0) + 1

        # Strictly internal = internal minus boundary
        strictly_internal_ids = internal_ids - boundary_ids

        # --- Classify ---
        zone_label, zone_desc = _classify_zone(len(boundary_ids))

        # --- Gather node details from DB for display ---
        all_display_ids = internal_ids | boundary_ids
        if not all_display_ids:
            click.echo("No reachable symbols found.")
            return

        ph = ",".join("?" for _ in all_display_ids)
        detail_rows = conn.execute(
            f"SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, "
            f"f.path as file_path "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({ph})",
            list(all_display_ids),
        ).fetchall()

        detail_map: dict[int, dict] = {}
        for r in detail_rows:
            detail_map[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "kind": r["kind"],
                "qualified_name": r["qualified_name"],
                "line_start": r["line_start"],
                "file_path": r["file_path"],
            }

        # --- Collect affected files ---
        affected_files: set[str] = set()
        for nid in internal_ids:
            info = detail_map.get(nid)
            if info:
                affected_files.add(info["file_path"])

        # --- External ref counts for boundary symbols ---
        # Total external references (callers from outside the zone)
        boundary_external_refs: dict[int, int] = {}
        for bid in boundary_ids:
            ext_callers = external_caller_count.get(bid, 0)
            ext_callees = external_callee_count.get(bid, 0)
            boundary_external_refs[bid] = ext_callers + ext_callees

        # --- JSON output ---
        if json_mode:
            internal_list = []
            for nid in sorted(strictly_internal_ids):
                info = detail_map.get(nid)
                if not info:
                    continue
                internal_list.append({
                    "name": info["name"],
                    "kind": abbrev_kind(info["kind"]),
                    "file": info["file_path"],
                    "line": info["line_start"],
                })

            boundary_list = []
            for nid in sorted(boundary_ids, key=lambda b: boundary_external_refs.get(b, 0), reverse=True):
                info = detail_map.get(nid)
                if not info:
                    continue
                boundary_list.append({
                    "name": info["name"],
                    "kind": abbrev_kind(info["kind"]),
                    "file": info["file_path"],
                    "line": info["line_start"],
                    "external_callers": external_caller_count.get(nid, 0),
                    "external_callees": external_callee_count.get(nid, 0),
                    "external_refs": boundary_external_refs.get(nid, 0),
                })

            click.echo(to_json(json_envelope("safe-zones",
                summary={
                    "zone": zone_label,
                    "internal_symbols": len(strictly_internal_ids),
                    "boundary_symbols": len(boundary_ids),
                    "total_symbols": len(internal_ids),
                    "affected_files": len(affected_files),
                },
                target=target_label,
                depth=depth,
                zone=zone_label,
                zone_description=zone_desc,
                internal_symbols=len(strictly_internal_ids),
                boundary_symbols=len(boundary_ids),
                total_symbols=len(internal_ids),
                affected_files=sorted(affected_files),
                internal=internal_list,
                boundary=boundary_list,
            )))
            return

        # --- Text output ---
        click.echo(f"Safe zone analysis for `{target_label}`:\n")
        click.echo(f"Zone: {zone_label} ({zone_desc})\n")

        # Internal symbols
        if strictly_internal_ids:
            click.echo("Internal (safe to change freely):")
            int_rows = []
            for nid in sorted(strictly_internal_ids):
                info = detail_map.get(nid)
                if not info:
                    continue
                int_rows.append([
                    info["name"],
                    abbrev_kind(info["kind"]),
                    loc(info["file_path"], info["line_start"]),
                ])
            click.echo(format_table(["name", "kind", "location"], int_rows, budget=20))
            click.echo()

        # Boundary symbols
        if boundary_ids:
            click.echo("Boundary (maintain contracts):")
            bnd_rows = []
            for nid in sorted(boundary_ids, key=lambda b: boundary_external_refs.get(b, 0), reverse=True):
                info = detail_map.get(nid)
                if not info:
                    continue
                ext_c = external_caller_count.get(nid, 0)
                ext_e = external_callee_count.get(nid, 0)
                refs_parts = []
                if ext_c:
                    refs_parts.append(f"{ext_c} caller{'s' if ext_c != 1 else ''}")
                if ext_e:
                    refs_parts.append(f"{ext_e} callee{'s' if ext_e != 1 else ''}")
                refs_label = ", ".join(refs_parts) if refs_parts else "0 external refs"
                bnd_rows.append([
                    info["name"],
                    abbrev_kind(info["kind"]),
                    loc(info["file_path"], info["line_start"]),
                    f"({refs_label})",
                ])
            click.echo(format_table(
                ["name", "kind", "location", "external refs"],
                bnd_rows,
                budget=20,
            ))
            click.echo()

        # Blast radius summary
        click.echo(
            f"Blast radius: {len(internal_ids)} symbols in "
            f"{len(affected_files)} file{'s' if len(affected_files) != 1 else ''}"
            f" (contained to {', '.join(sorted(affected_files)[:5])}"
            f"{'...' if len(affected_files) > 5 else ''})"
        )
