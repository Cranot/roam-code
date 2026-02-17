"""Explain why a symbol matters — role, reach, criticality, verdict."""

from __future__ import annotations

import re

import click
import networkx as nx

from roam.db.connection import open_db, batched_in
from roam.graph.builder import build_symbol_graph
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


def _label_cluster(conn, sym_ids):
    """Generate a label for a cluster from its symbol names."""
    if not sym_ids:
        return "misc"
    sample = list(sym_ids)[:50]
    ph = ",".join("?" for _ in sample)
    rows = conn.execute(
        f"SELECT name FROM symbols WHERE id IN ({ph})", sample
    ).fetchall()

    split_re = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')
    stop = {
        "get", "set", "use", "handle", "on", "is", "has", "can", "do",
        "the", "for", "with", "from", "init", "new", "run",
    }
    word_counts: dict[str, int] = {}
    for r in rows:
        parts = split_re.findall(r["name"])
        for p in parts:
            w = p.lower()
            if len(w) >= 3 and w not in stop:
                word_counts[w] = word_counts.get(w, 0) + 1
    if not word_counts:
        return "misc"
    top = sorted(word_counts.items(), key=lambda x: -x[1])
    return top[0][0]


def _classify_role(in_deg, out_deg, is_bridge):
    """Classify symbol role based on fan-in/fan-out pattern."""
    if in_deg >= 5 and out_deg >= 5:
        return "Hub"
    if in_deg >= 5 and out_deg < 3:
        return "Core utility"
    if in_deg < 3 and out_deg >= 5:
        return "Orchestrator"
    if is_bridge:
        return "Bridge"
    if in_deg >= 3:
        return "Utility"
    if in_deg < 2 and out_deg < 2:
        return "Leaf"
    return "Internal"


def _verdict(role, reach, in_deg, out_deg, critical, affected_files):
    """Generate one-line verdict from role + metrics."""
    if role == "Leaf" and reach == 0 and in_deg == 0:
        return "Dead code. Safe to remove."
    if role == "Leaf" and reach == 0:
        return f"Low-traffic leaf ({in_deg} callers). Safe to modify."
    if role == "Leaf":
        return f"Minor symbol ({in_deg} callers, {reach} transitive). Low risk."
    if role == "Hub":
        return f"God symbol ({in_deg} in, {out_deg} out). Consider splitting responsibilities."
    if role == "Core utility" and critical:
        return f"Load-bearing. Signature changes require updating {in_deg} direct callers."
    if role == "Core utility":
        return f"Widely used utility ({in_deg} callers). Stable interface recommended."
    if role == "Orchestrator" and critical:
        return f"Critical orchestrator. {out_deg} downstream ops, {reach} dependents cascade."
    if role == "Orchestrator":
        return f"Coordinates {out_deg} downstream operations. Changes cascade to {reach} dependents."
    if role == "Bridge":
        return "Coupling point between clusters. Changes affect multiple domains."
    if role == "Utility":
        return f"Moderate usage ({in_deg} callers, {reach} transitive). Standard caution."
    if role == "Internal" and reach == 0:
        return f"Internal helper ({in_deg} callers). Low blast radius."
    return f"Internal helper. {in_deg} callers, {reach} transitive reach."


def _cluster_cohesion(conn, comm_set):
    """Compute cohesion % for a cluster community set."""
    id_list = list(comm_set)
    src_rows = batched_in(
        conn,
        "SELECT source_id, target_id FROM edges WHERE source_id IN ({ph})",
        id_list,
    )
    tgt_rows = batched_in(
        conn,
        "SELECT source_id, target_id FROM edges WHERE target_id IN ({ph})",
        id_list,
    )
    edges = list({
        (r["source_id"], r["target_id"]): r
        for r in src_rows + tgt_rows
    }.values())
    internal = sum(
        1 for e in edges
        if e["source_id"] in comm_set and e["target_id"] in comm_set
    )
    return round(internal * 100 / len(edges)) if edges else 0


def _analyze_symbol(conn, G, RG, name, communities, sym_to_cluster,
                    cluster_label_cache, cluster_cohesion_cache):
    """Analyze a single symbol and return its result dict."""
    sym = find_symbol(conn, name)
    if sym is None:
        return {"name": name, "error": f"Symbol not found: {name}"}

    sym_id = sym["id"]

    gm = conn.execute(
        "SELECT in_degree, out_degree, betweenness, pagerank "
        "FROM graph_metrics WHERE symbol_id = ?",
        (sym_id,),
    ).fetchone()

    in_deg = gm["in_degree"] if gm else 0
    out_deg = gm["out_degree"] if gm else 0
    betweenness = (gm["betweenness"] or 0) if gm else 0
    pagerank = (gm["pagerank"] or 0) if gm else 0

    dependents: set = set()
    affected_files: set = set()
    if sym_id in RG:
        dependents = nx.descendants(RG, sym_id)
        for d in dependents:
            fp = G.nodes.get(d, {}).get("file_path")
            if fp:
                affected_files.add(fp)

    # Bridge detection
    is_bridge = False
    sym_cluster_id = sym_to_cluster.get(sym_id)
    if sym_cluster_id is not None and sym_id in G:
        other_clusters = {
            sym_to_cluster.get(n)
            for n in list(G.predecessors(sym_id)) + list(G.successors(sym_id))
        }
        other_clusters.discard(None)
        other_clusters.discard(sym_cluster_id)
        is_bridge = len(other_clusters) >= 1

    role = _classify_role(in_deg, out_deg, is_bridge)
    critical = betweenness > 0.5
    reach = len(dependents)

    cluster_label = None
    cluster_size = 0
    cluster_cohesion = None
    if sym_cluster_id is not None:
        comm = communities[sym_cluster_id]
        cluster_size = len(comm)

        if sym_cluster_id not in cluster_label_cache:
            cluster_label_cache[sym_cluster_id] = _label_cluster(conn, comm)
        cluster_label = cluster_label_cache[sym_cluster_id]

        if sym_cluster_id not in cluster_cohesion_cache:
            coh = None
            if cluster_size <= 500:
                coh = _cluster_cohesion(conn, set(comm))
            cluster_cohesion_cache[sym_cluster_id] = coh
        cluster_cohesion = cluster_cohesion_cache[sym_cluster_id]

    verdict_text = _verdict(role, reach, in_deg, out_deg, critical, len(affected_files))

    return {
        "name": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "location": loc(sym["file_path"], sym["line_start"]),
        "role": role,
        "fan_in": in_deg,
        "fan_out": out_deg,
        "reach": reach,
        "affected_files": len(affected_files),
        "critical": critical,
        "betweenness": round(betweenness, 2),
        "pagerank": round(pagerank, 4),
        "cluster": cluster_label,
        "cluster_size": cluster_size,
        "cluster_cohesion": cluster_cohesion,
        "verdict": verdict_text,
    }


@click.command()
@click.argument('names', nargs=-1, required=True)
@click.pass_context
def why(ctx, names):
    """Explain why a symbol matters — role, reach, criticality, verdict.

    Shows role classification, transitive reach, critical path membership,
    cluster affiliation, and a one-line verdict for decision-making.

    Accepts multiple symbols for batch triage:

        roam why parseAmount formatNumber clearGrid
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)
        RG = G.reverse()

        # Pre-compute clusters for bridge detection + cluster membership
        UG = G.to_undirected()
        communities: list[set] = []
        if UG.number_of_nodes() >= 2 and UG.number_of_edges() > 0:
            try:
                communities = list(nx.community.louvain_communities(UG, seed=42))
            except (AttributeError, TypeError):
                try:
                    communities = list(
                        nx.community.greedy_modularity_communities(UG)
                    )
                except Exception:
                    pass

        sym_to_cluster: dict[int, int] = {}
        for i, comm in enumerate(communities):
            for sid in comm:
                sym_to_cluster[sid] = i

        cluster_label_cache: dict[int, str] = {}
        cluster_cohesion_cache: dict[int, int | None] = {}

        results = []
        for name in names:
            result = _analyze_symbol(
                conn, G, RG, name, communities, sym_to_cluster,
                cluster_label_cache, cluster_cohesion_cache,
            )
            results.append(result)

        if json_mode:
            click.echo(to_json(json_envelope("why",
                summary={
                    "symbols": len(results),
                    "critical": sum(1 for r in results if r.get("critical")),
                },
                symbols=results,
            )))
            return

        # --- Batch mode: compact table ---
        if len(results) > 1:
            table_rows = []
            for r in results:
                if "error" in r:
                    table_rows.append([
                        r["name"], "?", "", "", "", r["error"],
                    ])
                    continue
                risk = "CRITICAL" if r["critical"] else (
                    "moderate" if r["reach"] > 5 else "low"
                )
                table_rows.append([
                    r["name"],
                    r["role"],
                    f"fan-in:{r['fan_in']}",
                    f"reach:{r['reach']}",
                    risk,
                    r["verdict"],
                ])
            click.echo(format_table(
                ["Symbol", "Role", "Fan", "Reach", "Risk", "Verdict"],
                table_rows,
            ))
            return

        # --- Single symbol: detailed output ---
        r = results[0]
        if "error" in r:
            click.echo(r["error"])
            raise SystemExit(1)

        click.echo(f"\n{r['name']}  {r['location']}")
        click.echo(
            f"  ROLE:      {r['role']}  "
            f"(fan-in: {r['fan_in']}, fan-out: {r['fan_out']})"
        )
        click.echo(
            f"  REACH:     {r['reach']} transitive dependents "
            f"across {r['affected_files']} files"
        )

        crit_str = "Yes" if r["critical"] else "No"
        if r["critical"]:
            crit_str += f" — betweenness {r['betweenness']:.1f}"
        elif r["reach"] > 5:
            crit_str += f" — moderate (reach: {r['reach']})"
        click.echo(f"  CRITICAL:  {crit_str}")

        if r["cluster"] is not None:
            coh = (
                f", cohesion: {r['cluster_cohesion']}%"
                if r["cluster_cohesion"] is not None
                else ""
            )
            click.echo(
                f"  CLUSTER:   {r['cluster']} "
                f"({r['cluster_size']} symbols{coh})"
            )
        else:
            click.echo("  CLUSTER:   (none — disconnected)")

        click.echo(f"  VERDICT:   {r['verdict']}")
        click.echo()
