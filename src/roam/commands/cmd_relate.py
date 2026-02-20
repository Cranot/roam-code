"""Show how a set of symbols relate: shared deps, call chains, conflicts."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


def _resolve_symbols_from_files(conn, file_paths):
    """Resolve all symbol IDs from file paths or directory prefixes."""
    symbol_ids = []
    for fp in file_paths:
        # Normalize path separators
        fp_norm = fp.replace("\\", "/")
        rows = conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE f.path LIKE ?",
            (f"%{fp_norm}%",),
        ).fetchall()
        for r in rows:
            symbol_ids.append(r[0])
    return symbol_ids


def _get_symbol_info(conn, node_id):
    """Get name, kind, file_path for a symbol node ID."""
    row = conn.execute(
        "SELECT s.name, s.kind, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id = ?",
        (node_id,),
    ).fetchone()
    if row:
        return {"name": row["name"], "kind": row["kind"], "file_path": row["file_path"]}
    return None


def _find_shared_dependencies(G, input_ids):
    """Find symbols that multiple input symbols depend on (common targets)."""
    # For each input symbol, find its successors (what it calls/depends on)
    dep_map = {}  # target_id -> set of input_ids that depend on it
    for nid in input_ids:
        if nid not in G:
            continue
        for succ in G.successors(nid):
            if succ in input_ids:
                continue  # Skip input symbols themselves
            dep_map.setdefault(succ, set()).add(nid)

    # Keep only those depended on by 2+ input symbols
    shared = {tid: callers for tid, callers in dep_map.items() if len(callers) >= 2}
    return shared


def _find_shared_callers(G, input_ids):
    """Find symbols that call multiple input symbols (common predecessors)."""
    caller_map = {}  # caller_id -> set of input_ids it calls
    for nid in input_ids:
        if nid not in G:
            continue
        for pred in G.predecessors(nid):
            if pred in input_ids:
                continue
            caller_map.setdefault(pred, set()).add(nid)

    shared = {cid: callees for cid, callees in caller_map.items() if len(callees) >= 2}
    return shared


def _compute_distance_matrix(G, input_ids, depth):
    """Compute shortest-path distances between all pairs of input symbols."""
    import networkx as nx

    undirected = G.to_undirected()
    matrix = {}
    input_set = set(input_ids)
    for i, src in enumerate(input_ids):
        for j, tgt in enumerate(input_ids):
            if i >= j:
                continue
            if src not in G or tgt not in G:
                matrix[(src, tgt)] = None
                continue
            try:
                dist = nx.shortest_path_length(undirected, src, tgt)
                if dist > depth:
                    matrix[(src, tgt)] = None
                else:
                    matrix[(src, tgt)] = dist
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                matrix[(src, tgt)] = None
    return matrix


def _find_direct_edges(G, input_ids):
    """Find direct edges between input symbols."""
    edges = []
    input_set = set(input_ids)
    for src in input_ids:
        if src not in G:
            continue
        for tgt in G.successors(src):
            if tgt in input_set and tgt != src:
                kind = G.edges[src, tgt].get("kind", "unknown")
                edges.append((src, tgt, kind))
    return edges


def _find_connecting_path(G, src, tgt, depth):
    """Find shortest path between two nodes, respecting depth limit."""
    import networkx as nx

    if src not in G or tgt not in G:
        return None
    # Try directed first
    try:
        path = nx.shortest_path(G, src, tgt)
        if len(path) - 1 <= depth:
            return path
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass
    # Try undirected
    try:
        undirected = G.to_undirected()
        path = nx.shortest_path(undirected, src, tgt)
        if len(path) - 1 <= depth:
            return path
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass
    return None


def _detect_conflicts(G, input_ids, shared_deps, conn):
    """Detect conflict risks: input symbols that both have outgoing edges to the same dependency."""
    conflicts = []
    for dep_id, callers in shared_deps.items():
        # Check if multiple inputs have edges that indicate modification
        # (calls, uses — things that could conflict)
        modifiers = set()
        for caller_id in callers:
            if G.has_edge(caller_id, dep_id):
                edge_kind = G.edges[caller_id, dep_id].get("kind", "")
                if edge_kind in ("call", "uses", "uses_trait"):
                    modifiers.add(caller_id)
        if len(modifiers) >= 2:
            dep_info = _get_symbol_info(conn, dep_id)
            if dep_info:
                conflicts.append({
                    "symbol": dep_info["name"],
                    "symbol_id": dep_id,
                    "modified_by": list(modifiers),
                    "recommendation": "coordinate changes to avoid race conditions",
                })
    return conflicts


def _compute_cohesion(distance_matrix, input_count):
    """Compute cohesion score: average inverse distance, normalized 0-1."""
    if input_count < 2:
        return 1.0
    distances = [d for d in distance_matrix.values() if d is not None]
    if not distances:
        return 0.0
    # Inverse distance: 1/d, averaged
    inv_sum = sum(1.0 / d for d in distances if d > 0)
    # Number of pairs
    n_pairs = input_count * (input_count - 1) // 2
    if n_pairs == 0:
        return 0.0
    cohesion = inv_sum / n_pairs
    # Clamp to 0-1
    return min(1.0, max(0.0, cohesion))


@click.command()
@click.argument("symbols", nargs=-1)
@click.option("--file", "files", multiple=True, help="Include symbols from file/dir path")
@click.option("--depth", default=3, help="Max hops for connecting paths (default 3)")
@click.pass_context
def relate(ctx, symbols, files, depth):
    """Show how a set of symbols relate to each other."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph

    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)

        # Resolve input symbols to node IDs
        input_ids = []
        input_names = {}  # id -> name

        for name in symbols:
            sym = find_symbol(conn, name)
            if sym:
                sid = sym["id"]
                if sid not in input_names:
                    input_ids.append(sid)
                    input_names[sid] = sym["name"]
            else:
                click.echo(f"Symbol not found: {name}")
                if not json_mode:
                    raise SystemExit(1)

        # Resolve symbols from --file paths
        if files:
            file_ids = _resolve_symbols_from_files(conn, files)
            for sid in file_ids:
                if sid not in input_names:
                    info = _get_symbol_info(conn, sid)
                    if info:
                        input_ids.append(sid)
                        input_names[sid] = info["name"]

        if not input_ids:
            click.echo("No symbols to analyze.")
            raise SystemExit(1)

        # Analysis
        direct_edges = _find_direct_edges(G, input_ids)
        shared_deps = _find_shared_dependencies(G, input_ids)
        shared_callers = _find_shared_callers(G, input_ids)
        distance_matrix = _compute_distance_matrix(G, input_ids, depth)
        conflicts = _detect_conflicts(G, input_ids, shared_deps, conn)
        cohesion = _compute_cohesion(distance_matrix, len(input_ids))

        # Build relationship list for each pair
        relationships = []
        input_set = set(input_ids)
        for i, src in enumerate(input_ids):
            for j, tgt in enumerate(input_ids):
                if i >= j:
                    continue
                src_name = input_names[src]
                tgt_name = input_names[tgt]
                dist = distance_matrix.get((src, tgt))
                if dist is None:
                    dist = distance_matrix.get((tgt, src))

                # Check for direct edge
                has_direct = False
                edge_kind = None
                for s, t, k in direct_edges:
                    if (s == src and t == tgt) or (s == tgt and t == src):
                        has_direct = True
                        edge_kind = k
                        break

                # Find intermediate node if distance is 2
                via = None
                if dist is not None and dist == 2 and not has_direct:
                    path = _find_connecting_path(G, src, tgt, depth)
                    if path and len(path) == 3:
                        mid_info = _get_symbol_info(conn, path[1])
                        if mid_info:
                            via = mid_info["name"]

                if has_direct:
                    kind = f"DIRECT {edge_kind.upper()}" if edge_kind else "DIRECT"
                elif dist is not None:
                    kind = "INDIRECT"
                else:
                    kind = "NO PATH"

                relationships.append({
                    "source": src_name,
                    "target": tgt_name,
                    "kind": kind,
                    "distance": dist,
                    "via": via,
                })

        # Build shared_deps output
        shared_deps_out = []
        for dep_id, callers in shared_deps.items():
            dep_info = _get_symbol_info(conn, dep_id)
            if dep_info:
                shared_deps_out.append({
                    "name": dep_info["name"],
                    "used_by": [input_names[c] for c in callers if c in input_names],
                })

        # Build shared_callers output
        shared_callers_out = []
        for caller_id, callees in shared_callers.items():
            caller_info = _get_symbol_info(conn, caller_id)
            if caller_info:
                shared_callers_out.append({
                    "name": caller_info["name"],
                    "calls": [input_names[c] for c in callees if c in input_names],
                })

        # Build conflicts output
        conflicts_out = []
        for c in conflicts:
            conflicts_out.append({
                "symbol": c["symbol"],
                "modified_by": [input_names[m] for m in c["modified_by"] if m in input_names],
                "recommendation": c["recommendation"],
            })

        # Build distance matrix output
        dist_matrix_out = {}
        for sid in input_ids:
            name = input_names[sid]
            dist_matrix_out[name] = {}
            for sid2 in input_ids:
                name2 = input_names[sid2]
                if sid == sid2:
                    dist_matrix_out[name][name2] = 0
                else:
                    key = (min(sid, sid2), max(sid, sid2))
                    d = distance_matrix.get(key)
                    dist_matrix_out[name][name2] = d

        verdict = (
            f"{len(input_ids)} symbols analyzed, "
            f"cohesion {cohesion:.2f}, "
            f"{len(direct_edges)} direct edges, "
            f"{len(conflicts_out)} conflict risks"
        )

        if json_mode:
            click.echo(to_json(json_envelope("relate",
                summary={
                    "verdict": verdict,
                    "symbol_count": len(input_ids),
                    "cohesion": round(cohesion, 2),
                    "direct_edges": len(direct_edges),
                    "conflict_risks": len(conflicts_out),
                },
                relationships=relationships,
                shared_deps=shared_deps_out,
                shared_callers=shared_callers_out,
                conflicts=conflicts_out,
                distance_matrix=dist_matrix_out,
            )))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")

        if relationships:
            click.echo("\nRELATIONSHIPS:")
            for rel in relationships:
                dist_str = f"distance {rel['distance']}" if rel["distance"] is not None else "no path"
                via_str = f" via {rel['via']}" if rel.get("via") else ""
                click.echo(f"  {rel['source']} -> {rel['target']}    {rel['kind']} ({dist_str}){via_str}")

        if shared_deps_out:
            click.echo("\nSHARED DEPENDENCIES:")
            for dep in shared_deps_out:
                click.echo(f"  {dep['name']}    used by: {', '.join(dep['used_by'])}")

        if shared_callers_out:
            click.echo("\nSHARED CALLERS:")
            for caller in shared_callers_out:
                click.echo(f"  {caller['name']}    calls: {', '.join(caller['calls'])}")

        if conflicts_out:
            click.echo("\nCONFLICT RISKS:")
            for c in conflicts_out:
                click.echo(f"  {c['symbol']} -- modified by {' AND '.join(c['modified_by'])}")
                click.echo(f"  Recommendation: {c['recommendation']}")

        # Distance matrix
        if len(input_ids) >= 2:
            click.echo("\nDISTANCE MATRIX:")
            names = [input_names[sid] for sid in input_ids]
            # Header
            max_name_len = max(len(n) for n in names)
            header = " " * (max_name_len + 2)
            for n in names:
                header += f"{n:>{max_name_len + 2}}"
            click.echo(header)
            for sid in input_ids:
                name = input_names[sid]
                row = f"  {name:<{max_name_len}}"
                for sid2 in input_ids:
                    name2 = input_names[sid2]
                    if sid == sid2:
                        row += f"{'-':>{max_name_len + 2}}"
                    else:
                        d = dist_matrix_out[name][name2]
                        val = str(d) if d is not None else "-"
                        row += f"{val:>{max_name_len + 2}}"
                click.echo(row)
