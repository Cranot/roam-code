"""Show detected clusters and directory mismatches."""

import os
from collections import Counter

import click

from roam.db.connection import open_db
from roam.db.queries import ALL_CLUSTERS
from roam.graph.clusters import compare_with_directories
from roam.output.formatter import abbrev_kind, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.option('--min-size', default=3, show_default=True, help='Hide clusters smaller than this')
@click.pass_context
def clusters(ctx, min_size):
    """Show code clusters and directory mismatches."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute(ALL_CLUSTERS).fetchall()

        if json_mode:
            visible = [r for r in rows if r["size"] >= min_size]
            mismatches = compare_with_directories(conn)
            visible_ids = {r["cluster_id"] for r in visible}

            # Per-cluster cohesion for JSON
            cluster_rows_j = conn.execute(
                "SELECT symbol_id, cluster_id FROM clusters"
            ).fetchall()
            sym_to_cluster_j = {r["symbol_id"]: r["cluster_id"] for r in cluster_rows_j}
            edges_j = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
            j_intra: dict[int, int] = {}
            j_total: dict[int, int] = {}
            for e in edges_j:
                cs = sym_to_cluster_j.get(e["source_id"])
                ct = sym_to_cluster_j.get(e["target_id"])
                if cs is None or ct is None:
                    continue
                if cs == ct:
                    j_intra[cs] = j_intra.get(cs, 0) + 1
                    j_total[cs] = j_total.get(cs, 0) + 1
                else:
                    j_total[cs] = j_total.get(cs, 0) + 1
                    j_total[ct] = j_total.get(ct, 0) + 1

            click.echo(to_json(json_envelope("clusters",
                summary={
                    "clusters": len(visible),
                    "mismatches": sum(1 for m in mismatches if m["cluster_id"] in visible_ids),
                },
                clusters=[
                    {
                        "id": r["cluster_id"],
                        "label": r["cluster_label"],
                        "size": r["size"],
                        "cohesion_pct": round(j_intra.get(r["cluster_id"], 0) * 100 / j_total[r["cluster_id"]]) if j_total.get(r["cluster_id"]) else 0,
                        "members": r["members"] or "",
                    }
                    for r in visible
                ],
                mismatches=[
                    {
                        "cluster_id": m["cluster_id"],
                        "label": m["cluster_label"],
                        "mismatch_count": m["mismatch_count"],
                        "directories": m["directories"],
                    }
                    for m in mismatches if m["cluster_id"] in visible_ids
                ],
            )))
            return

        click.echo("=== Clusters ===")
        if rows:
            # Filter clusters by min-size
            visible = [r for r in rows if r["size"] >= min_size]
            hidden_count = len(rows) - len(visible)
            visible_ids = {r["cluster_id"] for r in visible}

            total_symbols = sum(r["size"] for r in rows)

            # --- Build per-cluster cohesion metrics ---
            cluster_rows_all = conn.execute(
                "SELECT symbol_id, cluster_id FROM clusters"
            ).fetchall()
            sym_to_cluster = {r["symbol_id"]: r["cluster_id"] for r in cluster_rows_all}

            edges = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
            intra_count: dict[int, int] = {}
            total_count: dict[int, int] = {}
            inter_pairs: dict[tuple, int] = {}
            for e in edges:
                c_src = sym_to_cluster.get(e["source_id"])
                c_tgt = sym_to_cluster.get(e["target_id"])
                if c_src is None or c_tgt is None:
                    continue
                if c_src == c_tgt:
                    intra_count[c_src] = intra_count.get(c_src, 0) + 1
                    total_count[c_src] = total_count.get(c_src, 0) + 1
                else:
                    pair = (min(c_src, c_tgt), max(c_src, c_tgt))
                    inter_pairs[pair] = inter_pairs.get(pair, 0) + 1
                    total_count[c_src] = total_count.get(c_src, 0) + 1
                    total_count[c_tgt] = total_count.get(c_tgt, 0) + 1

            # --- Main table with cohesion column ---
            mega_ids = set()
            cohesion_values = []
            if visible:
                table_rows = []
                for r in visible:
                    cid = r["cluster_id"]
                    members_str = r["members"] or ""
                    pct = r["size"] * 100 / total_symbols if total_symbols else 0
                    is_mega = r["size"] > 100 or pct > 40

                    # Per-cluster cohesion (context added after median is computed)
                    c_intra = intra_count.get(cid, 0)
                    c_total = total_count.get(cid, 0)
                    cohesion = c_intra * 100 / c_total if c_total else 0
                    coh_str = f"{cohesion:.0f}%"
                    cohesion_values.append(cohesion)

                    if is_mega:
                        mega_ids.add(cid)
                        preview = f"MEGA ({pct:.0f}%) — see detail below"
                    else:
                        preview = members_str[:80] + "..." if len(members_str) > 80 else members_str
                    table_rows.append([
                        str(cid),
                        r["cluster_label"],
                        str(r["size"]),
                        coh_str,
                        preview,
                    ])
                click.echo(format_table(
                    ["ID", "Label", "Size", "Cohsn", "Members"],
                    table_rows,
                    budget=30,
                ))
            else:
                click.echo("  (no clusters with size >= {})".format(min_size))

            if hidden_count:
                click.echo(f"  ({hidden_count} clusters with fewer than {min_size} members hidden)")

            # --- Cohesion context: compute median for "above/below median" ---
            median_cohesion = sorted(cohesion_values)[len(cohesion_values) // 2] if cohesion_values else 50

            # --- Mega-cluster detail: sub-directory breakdown ---
            if visible and mega_ids:
                click.echo(f"\n=== Mega-Cluster Detail ===")
                for r in visible:
                    cid = r["cluster_id"]
                    if cid not in mega_ids:
                        continue
                    pct = r["size"] * 100 / total_symbols if total_symbols else 0
                    c_coh = intra_count.get(cid, 0) * 100 / total_count[cid] if total_count.get(cid) else 0
                    coh_ctx = "above" if c_coh >= median_cohesion else "below"
                    click.echo(f"\n  Cluster {cid}: {r['cluster_label']} "
                                f"({r['size']} symbols, {pct:.0f}% of graph, "
                                f"cohesion {c_coh:.0f}% — {coh_ctx} median {median_cohesion:.0f}%)")

                    # Get all symbols with file paths in this cluster
                    c_syms = conn.execute(
                        "SELECT s.id, s.name, s.kind, f.path, "
                        "COALESCE(gm.pagerank, 0) as pagerank "
                        "FROM clusters c "
                        "JOIN symbols s ON c.symbol_id = s.id "
                        "JOIN files f ON s.file_id = f.id "
                        "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
                        "WHERE c.cluster_id = ?",
                        (cid,),
                    ).fetchall()

                    # Group by second-level directory for deeper breakdown
                    dir_syms: dict[str, list] = {}
                    for s in c_syms:
                        p = s["path"].replace("\\", "/")
                        parts = p.split("/")
                        if len(parts) >= 3:
                            d = "/".join(parts[:2])
                        elif len(parts) >= 2:
                            d = parts[0]
                        else:
                            d = "."
                        dir_syms.setdefault(d, []).append(s)

                    sorted_dirs = sorted(dir_syms.items(), key=lambda x: -len(x[1]))

                    # Named sub-groups with letter labels
                    group_labels = "ABCDEFGH"
                    big_groups = []
                    click.echo("    Sub-groups:")
                    for idx, (d, syms) in enumerate(sorted_dirs[:8]):
                        dpct = len(syms) * 100 / r["size"] if r["size"] else 0
                        top3 = sorted(syms, key=lambda s: -s["pagerank"])[:3]
                        names = ", ".join(f"{s['name']}" for s in top3)
                        label = group_labels[idx] if idx < len(group_labels) else str(idx)
                        click.echo(f"      {label}: {d + '/':<36s} {len(syms):>4d} ({dpct:>2.0f}%)  {names}")
                        if dpct >= 10:
                            big_groups.append((label, d, {s["id"] for s in syms}))
                    if len(sorted_dirs) > 8:
                        click.echo(f"      (+{len(sorted_dirs) - 8} more directories)")

                    # Cross-coupling matrix between big sub-groups
                    if len(big_groups) >= 2:
                        # Build sym→group mapping
                        sym_to_grp = {}
                        for lbl, d, sids in big_groups:
                            for sid in sids:
                                sym_to_grp[sid] = lbl

                        # Count edges between groups
                        pair_edges: dict[tuple, int] = {}
                        grp_internal: dict[str, int] = {}
                        for e in edges:
                            g_src = sym_to_grp.get(e["source_id"])
                            g_tgt = sym_to_grp.get(e["target_id"])
                            if g_src and g_tgt:
                                if g_src == g_tgt:
                                    grp_internal[g_src] = grp_internal.get(g_src, 0) + 1
                                else:
                                    pair = (min(g_src, g_tgt), max(g_src, g_tgt))
                                    pair_edges[pair] = pair_edges.get(pair, 0) + 1

                        click.echo("    Coupling matrix:")
                        total_internal = sum(grp_internal.values())
                        total_cross = sum(pair_edges.values())
                        total_grp = total_internal + total_cross

                        for i, (lbl_a, d_a, _) in enumerate(big_groups):
                            for lbl_b, d_b, _ in big_groups[i + 1:]:
                                pair = (min(lbl_a, lbl_b), max(lbl_a, lbl_b))
                                cnt = pair_edges.get(pair, 0)
                                cpct = cnt * 100 / total_grp if total_grp else 0
                                d_a_short = d_a.rsplit("/", 1)[-1] if "/" in d_a else d_a
                                d_b_short = d_b.rsplit("/", 1)[-1] if "/" in d_b else d_b
                                if cnt > 0:
                                    click.echo(f"      {lbl_a}({d_a_short}) <-> {lbl_b}({d_b_short}): "
                                                f"{cnt} edges ({cpct:.0f}%)")

                        if total_grp > 0:
                            overall_cross = total_cross * 100 / total_grp
                            if overall_cross < 20:
                                click.echo(f"    ** Consider splitting: {len(big_groups)} sub-groups, "
                                            f"only {overall_cross:.0f}% cross-group coupling — clear seams")
                            elif overall_cross < 40:
                                click.echo(f"    Cross-group coupling: {overall_cross:.0f}% — moderate, "
                                            f"some seams visible")

            # --- Global cohesion summary ---
            total_intra = sum(intra_count.values())
            total_inter = sum(inter_pairs.values())
            total_all = total_intra + total_inter
            cohesion_pct = total_intra * 100 / total_all if total_all else 0
            click.echo(f"\n  Cluster cohesion: {cohesion_pct:.0f}% edges are intra-cluster ({total_intra} internal, {total_inter} cross-cluster)")

            # Top inter-cluster coupling pairs
            if inter_pairs:
                visible_pairs = {k: v for k, v in inter_pairs.items()
                                 if k[0] in visible_ids and k[1] in visible_ids}
                top_inter = sorted(visible_pairs.items(), key=lambda x: -x[1])[:10]
                if top_inter:
                    cl_labels = {r["cluster_id"]: r["cluster_label"] for r in rows}
                    click.echo(f"\n=== Inter-Cluster Coupling (top pairs) ===")
                    ic_rows = []
                    for (ca, cb), cnt in top_inter:
                        ic_rows.append([
                            cl_labels.get(ca, f"c{ca}"),
                            cl_labels.get(cb, f"c{cb}"),
                            str(cnt),
                        ])
                    click.echo(format_table(["Cluster A", "Cluster B", "Edges"], ic_rows))

        # --- Mismatches ---
        click.echo("\n=== Directory Mismatches (hidden coupling) ===")
        mismatches = compare_with_directories(conn)
        if mismatches:
            # Only show mismatches for displayed clusters
            mismatches = [m for m in mismatches if m["cluster_id"] in visible_ids]
        if mismatches:
            m_rows = []
            for m in mismatches:
                dirs = ", ".join(m["directories"][:5])
                if len(m["directories"]) > 5:
                    dirs += f" (+{len(m['directories']) - 5})"
                m_rows.append([
                    str(m["cluster_id"]),
                    m["cluster_label"],
                    str(m["mismatch_count"]),
                    dirs,
                ])
            click.echo(format_table(
                ["Cluster", "Label", "Mismatches", "Directories"],
                m_rows,
                budget=20,
            ))
        else:
            click.echo("  (none -- clusters align with directories)")
