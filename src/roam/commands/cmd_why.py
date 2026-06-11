"""Explain why a symbol matters — role, reach, criticality, verdict.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because why outputs are invocation-scoped importance rankings —
not per-location violations. Editor consumers should use the JSON
envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re
from collections import Counter

import click
import networkx as nx

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import batched_in, open_db
from roam.graph.builder import build_symbol_graph
from roam.output.formatter import (
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)


def _label_cluster(conn, sym_ids):
    """Generate a label for a cluster from its symbol names."""
    if not sym_ids:
        return "misc"
    sample = list(sym_ids)[:50]
    ph = ",".join("?" for _ in sample)
    rows = conn.execute(f"SELECT name FROM symbols WHERE id IN ({ph})", sample).fetchall()

    split_re = re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)")
    stop = {
        "get",
        "set",
        "use",
        "handle",
        "on",
        "is",
        "has",
        "can",
        "do",
        "the",
        "for",
        "with",
        "from",
        "init",
        "new",
        "run",
    }
    word_counts: Counter[str] = Counter()
    for r in rows:
        parts = split_re.findall(r["name"])
        for p in parts:
            w = p.lower()
            if len(w) >= 3 and w not in stop:
                word_counts[w] += 1
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
    edges = list({(r["source_id"], r["target_id"]): r for r in src_rows + tgt_rows}.values())
    internal = sum(1 for e in edges if e["source_id"] in comm_set and e["target_id"] in comm_set)
    return round(internal * 100 / len(edges)) if edges else 0


def _analyze_symbol(conn, G, RG, name, communities, sym_to_cluster, cluster_label_cache, cluster_cohesion_cache):
    """Analyze a single symbol and return its result dict.

    W1245 Pattern-2 variant-D: the returned dict carries ``resolution`` +
    ``partial_success`` so the per-entry payload discloses which resolver
    tier landed the match. Unresolved targets emit an explicit
    ``resolution="unresolved"`` entry; fuzzy LIKE-fallback matches flip
    ``partial_success`` so success payloads aren't indistinguishable from
    exact-match successes.
    """
    sym = find_symbol(conn, name)
    if sym is None:
        return {
            "name": name,
            "error": f"Symbol not found: {name}",
            **resolution_disclosure("unresolved", target=name),
        }

    sym_id = sym["id"]
    resolution_tier = sym.get("_resolution_tier", "symbol")

    gm = conn.execute(
        "SELECT in_degree, out_degree, betweenness, pagerank FROM graph_metrics WHERE symbol_id = ?",
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
        other_clusters = {sym_to_cluster.get(n) for n in list(G.predecessors(sym_id)) + list(G.successors(sym_id))}
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
    resolved_name = sym["qualified_name"] or sym["name"]
    if resolution_tier == "fuzzy":
        verdict_text = f"{verdict_text} [fuzzy resolution]"

    return {
        "name": resolved_name,
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
        # W1245 Pattern-2 variant-D per-entry resolver disclosure.
        **resolution_disclosure(resolution_tier, target=resolved_name),
    }


def _detect_communities(G) -> list[set]:
    """Best-effort Louvain (then greedy-modularity fallback) on the
    undirected graph. Empty list when neither is available or the graph
    has no edges."""
    UG = G.to_undirected()
    if UG.number_of_nodes() < 2 or UG.number_of_edges() == 0:
        return []
    try:
        return list(nx.community.louvain_communities(UG, seed=42))
    except (AttributeError, TypeError):
        try:
            return list(nx.community.greedy_modularity_communities(UG))
        except Exception:
            return []


def _emit_why_json(results: list[dict]) -> None:
    """JSON envelope for the why command — single OR multi-symbol.

    W1245 Pattern-2 variant-D: per-entry disclosure rides on each entry
    in ``symbols[]`` (set in ``_analyze_symbol``). Top-level
    ``partial_success`` flips when ANY entry resolved non-exactly so
    LAW-6 single-field consumers see the degradation without parsing
    the array. The single-target case mirrors that by suffixing the
    verdict with ``[fuzzy resolution]`` when the lone entry is fuzzy.
    """
    crit = sum(1 for r in results if r.get("critical"))
    any_degraded = any(r.get("resolution", "symbol") != "symbol" for r in results)
    base_verdict = (
        f"{crit} of {len(results)} symbol(s) critical" if crit else f"{len(results)} symbol(s) — none critical"
    )
    # Single-target case: surface fuzzy resolution on the LAW-6 verdict line
    # so editor consumers reading only the verdict see the disclosure.
    fuzzy_suffix = ""
    if len(results) == 1 and results[0].get("resolution") == "fuzzy":
        fuzzy_suffix = " [fuzzy resolution]"
    verdict = f"{base_verdict}{fuzzy_suffix}"

    summary: dict[str, object] = {
        "verdict": verdict,
        "symbols": len(results),
        "critical": crit,
        "partial_success": any_degraded,
    }
    click.echo(
        to_json(
            json_envelope(
                "why",
                summary=summary,
                symbols=results,
                partial_success=any_degraded,
            )
        )
    )


def _emit_why_batch_table(results: list[dict]) -> None:
    """Compact one-row-per-symbol table for batch invocations."""
    table_rows = []
    for r in results:
        if "error" in r:
            table_rows.append([r["name"], "?", "", "", "", r["error"]])
            continue
        risk = "CRITICAL" if r["critical"] else ("moderate" if r["reach"] > 5 else "low")
        table_rows.append(
            [
                r["name"],
                r["role"],
                f"fan-in:{r['fan_in']}",
                f"reach:{r['reach']}",
                risk,
                r["verdict"],
            ]
        )
    click.echo(format_table(["Symbol", "Role", "Fan", "Reach", "Risk", "Verdict"], table_rows))


def _emit_why_single(r: dict) -> None:
    """Detailed multi-line output for a single symbol."""
    if "error" in r:
        click.echo(r["error"])
        raise SystemExit(1)
    click.echo(f"\n{r['name']}  {r['location']}")
    click.echo(f"  ROLE:      {r['role']}  (fan-in: {r['fan_in']}, fan-out: {r['fan_out']})")
    click.echo(f"  REACH:     {r['reach']} transitive dependents across {r['affected_files']} files")

    crit_str = "Yes" if r["critical"] else "No"
    if r["critical"]:
        crit_str += f" — betweenness {r['betweenness']:.1f}"
    elif r["reach"] > 5:
        crit_str += f" — moderate (reach: {r['reach']})"
    click.echo(f"  CRITICAL:  {crit_str}")

    if r["cluster"] is not None:
        coh = f", cohesion: {r['cluster_cohesion']}%" if r["cluster_cohesion"] is not None else ""
        click.echo(f"  CLUSTER:   {r['cluster']} ({r['cluster_size']} symbols{coh})")
    else:
        click.echo("  CLUSTER:   (none — disconnected)")

    click.echo(f"  VERDICT:   {r['verdict']}")
    click.echo()


@roam_capability(
    name="why",
    category="refactoring",
    summary="Explain why a symbol matters — role, reach, criticality, verdict",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "debug"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def why(ctx, names):
    """Explain why a symbol matters — role, reach, criticality, verdict.

    Shows role classification, transitive reach, critical path membership,
    cluster affiliation, and a one-line verdict for decision-making.

    Unlike ``fan`` (which ranks symbols by raw connectivity) and ``preflight``
    (which checks blast radius before a change), this command explains a specific
    symbol's role (Hub/Bridge/Leaf), cluster cohesion, transitive reach, and
    generates a human-readable verdict.

    Accepts multiple symbols for batch triage.

    \b
    Examples:
      roam why parseAmount
      roam why parseAmount formatNumber clearGrid
      roam --json why login_user
      roam --detail why build_symbol_graph

    See also ``fan`` (raw connectivity ranking), ``preflight``
    (blast-radius gate), and ``diagnose`` (root-cause ranking).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)
        RG = G.reverse()

        communities = _detect_communities(G)
        sym_to_cluster: dict[int, int] = {}
        for i, comm in enumerate(communities):
            for sid in comm:
                sym_to_cluster[sid] = i
        cluster_label_cache: dict[int, str] = {}
        cluster_cohesion_cache: dict[int, int | None] = {}

        results = [
            _analyze_symbol(
                conn,
                G,
                RG,
                name,
                communities,
                sym_to_cluster,
                cluster_label_cache,
                cluster_cohesion_cache,
            )
            for name in names
        ]

        if json_mode:
            _emit_why_json(results)
            return
        if len(results) > 1:
            _emit_why_batch_table(results)
            return
        _emit_why_single(results[0])
