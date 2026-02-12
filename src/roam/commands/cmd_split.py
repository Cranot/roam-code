"""Analyze a file's internal structure and suggest decomposition."""

from __future__ import annotations

import re
from collections import Counter

import click
import networkx as nx

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


def _label_group(symbols):
    """Generate a descriptive label for a group of symbols."""
    _split_re = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')
    _stop = {
        "get", "set", "use", "handle", "on", "is", "has", "can", "do",
        "the", "for", "with", "from", "init", "new", "run",
    }
    word_counts: dict[str, int] = {}
    for s in symbols:
        parts = _split_re.findall(s["name"])
        for p in parts:
            w = p.lower()
            if len(w) >= 3 and w not in _stop:
                word_counts[w] = word_counts.get(w, 0) + 1

    if not word_counts:
        return "misc"
    # Pick the most common meaningful word
    top = sorted(word_counts.items(), key=lambda x: -x[1])
    return top[0][0]


def _label_suffix(symbols, exclude_word):
    """Find next most descriptive word for disambiguation."""
    _split_re = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')
    _stop = {
        "get", "set", "use", "handle", "on", "is", "has", "can", "do",
        "the", "for", "with", "from", "init", "new", "run",
    }
    word_counts: dict[str, int] = {}
    for s in symbols:
        parts = _split_re.findall(s["name"])
        for p in parts:
            w = p.lower()
            if len(w) >= 3 and w not in _stop and w != exclude_word:
                word_counts[w] = word_counts.get(w, 0) + 1
    if not word_counts:
        return None
    top = sorted(word_counts.items(), key=lambda x: -x[1])
    return top[0][0]


@click.command()
@click.argument('path')
@click.option('--min-group', default=2, show_default=True,
              help='Minimum symbols per group')
@click.pass_context
def split(ctx, path, min_group):
    """Analyze a file's internal structure and suggest how to split it.

    Shows natural symbol groups within a file based on call/reference
    patterns, with coupling metrics and extraction suggestions.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    path = path.replace("\\", "/")

    with open_db(readonly=True) as conn:
        frow = conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        if frow is None:
            frow = conn.execute(
                "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{path}",),
            ).fetchone()
        if frow is None:
            click.echo(f"File not found in index: {path}")
            raise SystemExit(1)

        file_id = frow["id"]

        # Get all symbols in this file
        symbols = conn.execute(
            "SELECT s.*, COALESCE(gm.pagerank, 0) as pagerank "
            "FROM symbols s "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "WHERE s.file_id = ? ORDER BY s.line_start",
            (file_id,),
        ).fetchall()

        if len(symbols) < 3:
            if json_mode:
                click.echo(to_json(json_envelope("split",
                    summary={"groups": 0, "total_symbols": len(symbols)},
                    path=frow["path"], groups=[],
                    message="Too few symbols to analyze",
                )))
            else:
                click.echo(f"File has only {len(symbols)} symbols — too few to analyze.")
            return

        sym_ids = {s["id"] for s in symbols}
        sym_by_id = {s["id"]: s for s in symbols}

        # Get all edges involving symbols in this file
        ph = ",".join("?" for _ in sym_ids)
        id_list = list(sym_ids)
        all_edges = conn.execute(
            f"SELECT source_id, target_id, kind FROM edges "
            f"WHERE source_id IN ({ph}) OR target_id IN ({ph})",
            id_list + id_list,
        ).fetchall()

        # Classify edges
        intra_edges = []   # both endpoints in this file
        external_out = []  # source in file, target outside
        external_in = []   # source outside, target in file
        for e in all_edges:
            s_in = e["source_id"] in sym_ids
            t_in = e["target_id"] in sym_ids
            if s_in and t_in:
                intra_edges.append(e)
            elif s_in:
                external_out.append(e)
            elif t_in:
                external_in.append(e)

        # Build intra-file graph
        G = nx.Graph()
        for s in symbols:
            G.add_node(s["id"])
        for e in intra_edges:
            if G.has_edge(e["source_id"], e["target_id"]):
                G[e["source_id"]][e["target_id"]]["weight"] += 1
            else:
                G.add_edge(e["source_id"], e["target_id"], weight=1)

        # Run community detection
        if len(G) < 2 or G.number_of_edges() == 0:
            # No internal edges — every symbol is isolated
            communities = [{s["id"]} for s in symbols]
        else:
            try:
                communities = list(nx.community.louvain_communities(G, seed=42))
            except (AttributeError, TypeError):
                communities = list(nx.community.greedy_modularity_communities(G))

        # Filter by min-group and sort by size
        groups = []
        ungrouped = []
        for comm in communities:
            if len(comm) >= min_group:
                groups.append(comm)
            else:
                ungrouped.extend(comm)

        groups.sort(key=lambda c: -len(c))

        # Compute metrics for each group
        group_data = []
        for i, comm in enumerate(groups):
            comm_syms = [sym_by_id[sid] for sid in comm if sid in sym_by_id]
            comm_syms.sort(key=lambda s: -(s["pagerank"] or 0))

            # Count edges within this community
            internal = sum(
                1 for e in intra_edges
                if e["source_id"] in comm and e["target_id"] in comm
            )
            # Count edges to other communities (within file)
            cross_community = sum(
                1 for e in intra_edges
                if (e["source_id"] in comm) != (e["target_id"] in comm)
                and (e["source_id"] in comm or e["target_id"] in comm)
            )
            # Count edges to symbols outside the file
            ext_out = sum(1 for e in external_out if e["source_id"] in comm)
            ext_in = sum(1 for e in external_in if e["target_id"] in comm)
            external = ext_out + ext_in

            label = _label_group(comm_syms)
            total_edges = internal + cross_community
            isolation = internal * 100 / total_edges if total_edges else 100

            group_data.append({
                "id": i,
                "label": label,
                "symbols": comm_syms,
                "symbol_ids": comm,
                "internal_edges": internal,
                "cross_edges": cross_community,
                "external_edges": external,
                "isolation_pct": isolation,
            })

        # Disambiguate duplicate labels
        label_counts = Counter(g["label"] for g in group_data)
        dupes = {l for l, c in label_counts.items() if c > 1}
        if dupes:
            for g in group_data:
                if g["label"] in dupes:
                    suffix = _label_suffix(g["symbols"], g["label"])
                    if suffix:
                        g["label"] = f"{g['label']}-{suffix}"
            # If still duplicated, append numeric suffix
            seen: dict[str, int] = {}
            for g in group_data:
                if g["label"] in seen:
                    seen[g["label"]] += 1
                    g["label"] = f"{g['label']}-{seen[g['label']]}"
                else:
                    seen[g["label"]] = 1

        # Identify extraction candidates
        # Good candidate: high isolation (>60%), multiple symbols, low cross-community edges
        extractable = []
        for g in group_data:
            if (len(g["symbols"]) >= 3
                    and g["isolation_pct"] >= 50
                    and g["cross_edges"] <= g["internal_edges"]):
                extractable.append(g)
        extractable.sort(key=lambda g: -g["isolation_pct"])

        # Overall cross-group coupling
        total_intra_all = sum(g["internal_edges"] for g in group_data)
        total_cross_all = sum(g["cross_edges"] for g in group_data) // 2  # counted from both sides
        total_all = total_intra_all + total_cross_all
        cross_pct = total_cross_all * 100 / total_all if total_all else 0

        # Ungrouped breakdown by kind
        ungrouped_syms = [sym_by_id[sid] for sid in ungrouped if sid in sym_by_id]
        ungrouped_kinds = Counter(s["kind"] for s in ungrouped_syms)

        # Cross-group edge detail
        sym_to_group: dict[int, dict] = {}
        for g in group_data:
            for sid in g["symbol_ids"]:
                sym_to_group[sid] = g
        cross_detail: dict[tuple, list] = {}
        for e in intra_edges:
            sg = sym_to_group.get(e["source_id"])
            tg = sym_to_group.get(e["target_id"])
            if sg and tg and sg["id"] != tg["id"]:
                pair = (min(sg["id"], tg["id"]), max(sg["id"], tg["id"]))
                src_name = sym_by_id[e["source_id"]]["name"]
                tgt_name = sym_by_id[e["target_id"]]["name"]
                cross_detail.setdefault(pair, []).append(
                    {"source": src_name, "target": tgt_name, "kind": e["kind"]}
                )

        if json_mode:
            click.echo(to_json(json_envelope("split",
                summary={
                    "groups": len(group_data),
                    "total_symbols": len(symbols),
                    "extractable": len(extractable),
                },
                path=frow["path"],
                total_symbols=len(symbols),
                total_intra_edges=len(intra_edges),
                total_external_edges=len(external_out) + len(external_in),
                cross_group_coupling_pct=round(cross_pct),
                groups=[
                    {
                        "label": g["label"],
                        "size": len(g["symbols"]),
                        "symbols": [
                            {"name": s["name"], "kind": s["kind"],
                             "line": s["line_start"],
                             "pagerank": round(s["pagerank"], 4)}
                            for s in g["symbols"]
                        ],
                        "internal_edges": g["internal_edges"],
                        "cross_edges": g["cross_edges"],
                        "external_edges": g["external_edges"],
                        "isolation_pct": round(g["isolation_pct"]),
                        "extractable": g in extractable,
                    }
                    for g in group_data
                ],
                ungrouped_count=len(ungrouped),
                ungrouped_breakdown={k: c for k, c in ungrouped_kinds.most_common()},
                cross_group_edges=[
                    {
                        "groups": [
                            next(g["label"] for g in group_data if g["id"] == a),
                            next(g["label"] for g in group_data if g["id"] == b),
                        ],
                        "count": len(edges),
                        "edges": [
                            {"source": e["source"], "target": e["target"], "kind": e["kind"]}
                            for e in edges[:10]
                        ],
                    }
                    for (a, b), edges in sorted(cross_detail.items())
                ],
                suggestions=[
                    {
                        "group": g["label"],
                        "symbols": [s["name"] for s in g["symbols"][:10]],
                        "isolation_pct": round(g["isolation_pct"]),
                    }
                    for g in extractable
                ],
            )))
            return

        # --- Text output ---
        click.echo(f"=== Split analysis: {frow['path']} ===")
        click.echo(f"  {len(symbols)} symbols, {len(intra_edges)} internal edges, "
                    f"{len(external_out) + len(external_in)} external edges")
        click.echo(f"  Cross-group coupling: {cross_pct:.0f}%")
        click.echo()

        for g in group_data:
            iso_flag = ""
            if g["isolation_pct"] >= 70:
                iso_flag = " [extractable]"
            elif g["isolation_pct"] >= 50:
                iso_flag = " [candidate]"

            click.echo(f"  Group {g['id'] + 1} ({g['label']}) — "
                        f"{len(g['symbols'])} symbols, "
                        f"{g['internal_edges']} internal / "
                        f"{g['cross_edges']} cross / "
                        f"{g['external_edges']} external edges "
                        f"(isolation: {g['isolation_pct']:.0f}%){iso_flag}")

            # Show symbols sorted by PageRank, capped at 10
            for s in g["symbols"][:10]:
                pr = s["pagerank"] or 0
                pr_str = f"  PR={pr:.4f}" if pr > 0 else ""
                click.echo(f"    {abbrev_kind(s['kind'])}  {s['name']:<35s} "
                            f"L{s['line_start']}{pr_str}")
            if len(g["symbols"]) > 10:
                click.echo(f"    (+{len(g['symbols']) - 10} more)")
            click.echo()

        if ungrouped:
            kind_str = ", ".join(
                f"~{c} {k}" for k, c in ungrouped_kinds.most_common()
            )
            click.echo(f"  Ungrouped: {len(ungrouped)} symbols ({kind_str})")
            click.echo()

        # --- Extraction suggestions ---
        if extractable:
            click.echo("=== Extraction Suggestions ===")
            for rank, g in enumerate(extractable):
                names = ", ".join(s["name"] for s in g["symbols"][:5])
                if len(g["symbols"]) > 5:
                    names += f" (+{len(g['symbols']) - 5} more)"
                marker = ""
                if g["isolation_pct"] == 100 and len(g["symbols"]) <= 10:
                    marker = " ** Start here"
                elif rank == 0:
                    marker = " <- best candidate"
                click.echo(f"  {rank + 1}. Extract '{g['label']}' group: {names}")
                click.echo(f"     {g['isolation_pct']:.0f}% isolated, "
                            f"only {g['cross_edges']} edges to other groups{marker}")
            click.echo()
            click.echo(f"  Overall cross-group coupling: {cross_pct:.0f}% — "
                        f"{'high (tightly woven file)' if cross_pct > 40 else 'moderate (some natural seams)' if cross_pct > 20 else 'low (clear separation)'}")
        elif group_data:
            if cross_pct > 50:
                click.echo("No clear extraction candidates — file is tightly woven "
                            f"({cross_pct:.0f}% cross-group coupling).")
            else:
                click.echo("Groups identified but none meet extraction threshold "
                            "(need ≥3 symbols, ≥50% isolation).")

        # Cross-group edge detail
        if cross_detail:
            click.echo()
            click.echo("=== Cross-group Edges ===")
            for (a, b), edges in sorted(cross_detail.items()):
                ga = next(g for g in group_data if g["id"] == a)
                gb = next(g for g in group_data if g["id"] == b)
                click.echo(f"  {ga['label']} <-> {gb['label']}: {len(edges)} edges")
                for edge in edges[:5]:
                    click.echo(f"    {edge['source']} -> {edge['target']} ({edge['kind']})")
                if len(edges) > 5:
                    click.echo(f"    (+{len(edges) - 5} more)")
