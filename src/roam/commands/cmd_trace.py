"""Show shortest dependency path between two symbols."""

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


def _classify_coupling(hops):
    """Classify path coupling strength from edge kinds.

    Returns a label like "strong (direct call chain)" based on the
    ratio of call/uses edges vs import/template edges.
    """
    kinds = [h.get("edge_kind", "") for h in hops[1:] if h.get("edge_kind")]
    total = len(kinds)
    if total == 0:
        # No symbol-level edges. 2-hop paths are file-level import shortcuts
        # (the code adds these when source file imports target file).
        # Longer paths with no edges shouldn't happen, but handle gracefully.
        if len(hops) == 2:
            return "structural (file import)"
        return "unknown"
    call_count = sum(1 for k in kinds if k in ("call", "uses", "uses_trait"))
    ratio = call_count / total
    if ratio == 1.0:
        return "strong (direct call chain)"
    if ratio >= 0.5:
        return "moderate (mixed call + import)"
    return "weak (via imports/template)"


def _detect_hubs(path_ids, G, threshold=50):
    """Detect hub nodes (high-degree) in path intermediates.

    Returns list of (node_id, degree) for intermediate nodes exceeding threshold.
    Skips first and last node (source/target are intentional).
    """
    hubs = []
    for node_id in path_ids[1:-1]:  # Skip source and target
        degree = G.in_degree(node_id) + G.out_degree(node_id)
        if degree > threshold:
            hubs.append((node_id, degree))
    return hubs


def _path_quality(hops, hubs):
    """Score path quality (higher = better). Three factors:

    1. Coupling: call/uses edges = 1.0 (runtime dependency), file imports = 0.5
       (structural dependency), import-only = 0.0 (weak).
    2. Directness: shorter paths are inherently more meaningful. 2-hop = 1.0,
       each extra hop reduces by 0.15.
    3. Hub penalty: high-degree intermediates make connections coincidental.
       Scales with degree — a mega-hub (degree 500+) penalizes more than a
       borderline hub (degree 50).

    Combined: coupling * 0.7 + directness * 0.3 - hub_penalty.
    """
    if not hops:
        return 0.0

    n_hops = len(hops)

    # --- Coupling score ---
    kinds = [h.get("edge_kind", "") for h in hops[1:] if h.get("edge_kind")]
    if kinds:
        total = len(kinds)
        coupling = sum(1 for k in kinds if k in ("call", "uses", "uses_trait")) / total
    else:
        # No edge kind info — file-level import shortcut paths.
        # These represent intentional structural dependencies (someone wrote
        # the import), so they deserve moderate coupling, not zero.
        coupling = 0.5

    # --- Directness bonus ---
    # 2 hops = 1.0, 3 hops = 0.85, 4 hops = 0.7, 5 hops = 0.55, ...
    directness = max(0.0, min(1.0, 1.0 - (n_hops - 2) * 0.15))

    base = coupling * 0.7 + directness * 0.3

    # --- Hub penalty (scales with degree) ---
    hub_penalty = 0.0
    for _, degree in hubs:
        # degree 50 → 0.30, degree 100 → 0.40, degree 250+ → 0.50 (capped)
        hub_penalty += min(0.5, 0.2 + degree / 500)

    return base - hub_penalty


def _build_hops(path_ids, annotated, G):
    """Build hop annotations for a single path."""
    hops = []
    for i, node in enumerate(annotated):
        hop = {
            "name": node["name"],
            "kind": node["kind"],
            "location": loc(node["file_path"], node["line"]),
        }
        if i > 0:
            prev_id = path_ids[i - 1]
            curr_id = path_ids[i]
            edge_kind = G.edges.get((prev_id, curr_id), {}).get("kind", "")
            if not edge_kind:
                edge_kind = G.edges.get((curr_id, prev_id), {}).get("kind", "")
            hop["edge_kind"] = edge_kind
        hops.append(hop)
    return hops


@click.command()
@click.argument('source')
@click.argument('target')
@click.option('-k', 'k_paths', default=3, help='Number of alternative paths to find')
@click.pass_context
def trace(ctx, source, target, k_paths):
    """Show shortest path between two symbols."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph
    from roam.graph.pathfinding import find_k_paths, find_symbol_id, format_path

    with open_db(readonly=True) as conn:
        src_ids = find_symbol_id(conn, source)
        if not src_ids:
            click.echo(f"Source symbol not found: {source}")
            raise SystemExit(1)

        tgt_ids = find_symbol_id(conn, target)
        if not tgt_ids:
            click.echo(f"Target symbol not found: {target}")
            raise SystemExit(1)

        G = build_symbol_graph(conn)

        # Pre-check: direct file-level imports beat graph pathfinding.
        _file_ids = {}
        for _sid in src_ids + tgt_ids:
            row = conn.execute("SELECT file_id FROM symbols WHERE id = ?", (_sid,)).fetchone()
            if row:
                _file_ids[_sid] = row["file_id"]

        # Collect all paths across all source/target combinations
        all_paths = []
        for sid in src_ids:
            for tid in tgt_ids:
                if sid == tid:
                    continue
                # Direct file import shortcut
                src_fid = _file_ids.get(sid)
                tgt_fid = _file_ids.get(tid)
                if src_fid and tgt_fid and src_fid != tgt_fid:
                    fe = conn.execute(
                        "SELECT 1 FROM file_edges WHERE source_file_id = ? AND target_file_id = ?",
                        (src_fid, tgt_fid),
                    ).fetchone()
                    if fe:
                        all_paths.append([sid, tid])

                paths = find_k_paths(G, sid, tid, k=k_paths)
                for p in paths:
                    all_paths.append(p)

        # Deduplicate and sort by length
        seen = set()
        unique_paths = []
        for p in all_paths:
            key = tuple(p)
            if key not in seen:
                seen.add(key)
                unique_paths.append(p)
        unique_paths.sort(key=len)
        unique_paths = unique_paths[:k_paths]

        if not unique_paths:
            if json_mode:
                click.echo(to_json(json_envelope("trace",
                    summary={"hops": 0, "paths": 0},
                    source=source,
                    target=target,
                    path=None,
                    paths=[],
                    coupling_summary="none — no dependency path exists",
                )))
            else:
                click.echo(f"No dependency path between '{source}' and '{target}'.")
                click.echo("These symbols are independent — changes to one cannot affect the other.")
            return

        # Annotate all paths with hub detection and quality scoring
        annotated_paths = []
        for path_ids in unique_paths:
            annotated = format_path(path_ids, conn)
            hops = _build_hops(path_ids, annotated, G)
            hubs = _detect_hubs(path_ids, G)
            coupling = _classify_coupling(hops)
            quality = _path_quality(hops, hubs)

            # Mark hub nodes in hops
            hub_ids = {h[0] for h in hubs}
            hub_degrees = {h[0]: h[1] for h in hubs}
            for i, node_id in enumerate(path_ids):
                if node_id in hub_ids:
                    hops[i]["is_hub"] = True
                    hops[i]["hub_degree"] = hub_degrees[node_id]

            annotated_paths.append({
                "path_ids": path_ids,
                "hops": hops,
                "coupling": coupling,
                "quality": round(quality, 2),
                "hub_count": len(hubs),
            })

        # Sort by quality (desc), then length (asc)
        annotated_paths.sort(key=lambda ap: (-ap["quality"], len(ap["hops"])))
        annotated_paths = annotated_paths[:k_paths]

        # Coupling summary: strongest coupling across ALL paths (not just best-quality).
        # A hub-mediated call chain may rank lower on quality but still reveals
        # that runtime coupling exists — the summary should reflect that.
        _COUPLING_RANK = {
            "strong (direct call chain)": 4,
            "moderate (mixed call + import)": 3,
            "weak (via imports/template)": 2,
            "structural (file import)": 1,
        }
        coupling_summary = max(
            (ap["coupling"] for ap in annotated_paths),
            key=lambda c: _COUPLING_RANK.get(c, 0),
            default="none",
        )

        if json_mode:
            # Backward-compatible: "path" = first path's hops, "hops" = first path hop count
            first = annotated_paths[0]
            click.echo(to_json(json_envelope("trace",
                summary={
                    "hops": len(first["hops"]),
                    "paths": len(annotated_paths),
                    "coupling": coupling_summary,
                },
                source=source,
                target=target,
                hops=len(first["hops"]),
                path=first["hops"],
                coupling_summary=coupling_summary,
                paths=[
                    {
                        "hops": len(ap["hops"]),
                        "coupling": ap["coupling"],
                        "quality": ap["quality"],
                        "hub_count": ap["hub_count"],
                        "path": ap["hops"],
                    }
                    for ap in annotated_paths
                ],
            )))
            return

        # --- Text output ---
        if len(annotated_paths) == 1:
            ap = annotated_paths[0]
            hub_note = f", {ap['hub_count']} hub{'s' if ap['hub_count'] != 1 else ''}" if ap["hub_count"] else ""
            click.echo(f"Path ({len(ap['hops'])} hops, quality={ap['quality']}, {ap['coupling']}{hub_note}):")
            _print_path(ap["hops"])
        else:
            for idx, ap in enumerate(annotated_paths, 1):
                hub_note = f", {ap['hub_count']} hub{'s' if ap['hub_count'] != 1 else ''}" if ap["hub_count"] else ""
                click.echo(f"\n=== Path {idx} of {len(annotated_paths)} "
                           f"({len(ap['hops'])} hops, quality={ap['quality']}, "
                           f"{ap['coupling']}{hub_note}) ===")
                _print_path(ap["hops"])

        click.echo(f"\nCoupling: {coupling_summary}")


def _print_path(hops):
    """Print a single path in the text format."""
    for i, hop in enumerate(hops):
        if i == 0:
            click.echo(f"    {abbrev_kind(hop['kind'])}  {hop['name']}  {hop['location']}")
        else:
            edge = hop.get("edge_kind", "")
            edge_label = f"--{edge}-->" if edge else "->"
            click.echo(f"  {edge_label}  {abbrev_kind(hop['kind'])}  {hop['name']}  {hop['location']}")
        if hop.get("is_hub"):
            click.echo(f"           ^ hub (degree {hop['hub_degree']})")
