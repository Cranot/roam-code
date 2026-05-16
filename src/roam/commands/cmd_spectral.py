"""Spectral bisection command -- alternative module decomposition via Fiedler vector.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because spectral outputs are invocation-scoped spectral
decomposition partitions — not per-location violations. Multi-file
expansion would distort SARIF semantics. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.graph.builder import build_symbol_graph
from roam.graph.clusters import detect_clusters
from roam.graph.spectral import (
    adjusted_rand_index,
    fiedler_partition,
    spectral_communities,
    spectral_gap,
    verdict_from_gap,
)
from roam.output.formatter import format_table, json_envelope, to_json

_MAX_GRAPH_SYMBOLS = 5000


def _partition_tree(partition_map, G):
    """Build a tree description of the spectral partition.

    Returns a list of dicts with partition metadata.
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for node, pid in partition_map.items():
        groups[pid].append(node)
    result = []
    for pid in sorted(groups):
        members = groups[pid]
        names = []
        for n in members[:5]:
            attr = G.nodes.get(n, {})
            names.append(attr.get("name", str(n)))
        result.append(
            {
                "partition_id": pid,
                "size": len(members),
                "sample_members": names,
            }
        )
    return result


def _compare_with_louvain(G, spectral_map):
    """Compare spectral partition against Louvain.

    Returns ARI score and comparison metadata.
    """
    louvain_map = detect_clusters(G)
    # Align nodes present in both maps
    common_nodes = sorted(set(spectral_map) & set(louvain_map))
    if not common_nodes:
        return {"ari": 0.0, "spectral_partitions": 0, "louvain_partitions": 0}
    s_labels = [spectral_map[n] for n in common_nodes]
    l_labels = [louvain_map[n] for n in common_nodes]
    ari = adjusted_rand_index(s_labels, l_labels)
    return {
        "ari": ari,
        "spectral_partitions": len(set(s_labels)),
        "louvain_partitions": len(set(l_labels)),
        "nodes_compared": len(common_nodes),
    }


@roam_capability(
    name="spectral",
    category="architecture",
    summary="Spectral bisection: Fiedler vector partition tree",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--depth", default=3, show_default=True, help="Max recursion depth for bisection")
@click.option("--compare", is_flag=True, help="Compare spectral vs Louvain (Adjusted Rand Index)")
@click.option("--gap-only", is_flag=True, help="Only show spectral gap metric")
@click.option("--k", default=0, help="Number of communities (0=auto-detect)")
@click.pass_context
def spectral(ctx, depth, compare, gap_only, k):
    """Spectral bisection: Fiedler vector partition tree.

    Unlike ``clusters`` (which uses Louvain community detection to find logical
    groupings), this command uses Fiedler-vector bisection to measure how
    cleanly the dependency graph partitions, reporting spectral gap and
    partition quality.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    with open_db(readonly=True) as conn:
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if sym_count > _MAX_GRAPH_SYMBOLS:
            # Dense Fiedler bisection is O(N^3) — at this size we point the
            # caller at the sparse alternatives that already work:
            # ``roam clusters`` (Louvain) and ``roam partition`` (graph-aware).
            msg = (
                f"Run roam clusters or roam partition (graph has {sym_count} symbols, "
                f"dense spectral threshold {_MAX_GRAPH_SYMBOLS}); spectral bisection "
                "is O(N^3) without sparse Lanczos"
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "spectral",
                            summary={
                                "verdict": msg,
                                "state": "graph_too_large_for_spectral_dense",
                                "partial_success": True,
                                "symbol_count": sym_count,
                                "max_dense_symbols": _MAX_GRAPH_SYMBOLS,
                                "suggested_commands": ["clusters", "partition"],
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {msg}")
            return

        G = build_symbol_graph(conn)

        # Spectral gap
        gap = spectral_gap(G)
        verdict = verdict_from_gap(gap)

        if gap_only:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "spectral",
                            summary={
                                "verdict": verdict,
                                "spectral_gap": round(gap, 6),
                            },
                            budget=token_budget,
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"Spectral gap: {gap:.6f}")
            return

        # Partition
        k_val = k if k > 0 else None
        if k_val is not None:
            partition_map = spectral_communities(G, k=k_val)
        else:
            partition_map = fiedler_partition(G, max_depth=depth)

        tree = _partition_tree(partition_map, G)
        n_partitions = len(tree)

        # Compare with Louvain if requested
        comparison = None
        if compare:
            comparison = _compare_with_louvain(G, partition_map)

        if json_mode:
            extra = {}
            if comparison is not None:
                extra["comparison"] = comparison
            click.echo(
                to_json(
                    json_envelope(
                        "spectral",
                        summary={
                            "verdict": verdict,
                            "spectral_gap": round(gap, 6),
                            "partitions": n_partitions,
                            "depth": depth,
                        },
                        budget=token_budget,
                        partitions=tree,
                        **extra,
                    )
                )
            )
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"Spectral gap: {gap:.6f}  Partitions: {n_partitions}  Depth: {depth}")
        click.echo("")
        click.echo("=== Spectral Partition Tree ===")
        if not tree:
            click.echo("  (no partitions detected -- empty graph)")
            return

        table_rows = []
        for pt in tree:
            members_str = ", ".join(pt["sample_members"])
            if pt["size"] > 5:
                extra_count = pt["size"] - 5
                members_str += f" (+{extra_count} more)"
            table_rows.append(
                [
                    str(pt["partition_id"]),
                    str(pt["size"]),
                    members_str,
                ]
            )
        click.echo(
            format_table(
                ["Partition", "Size", "Sample Members"],
                table_rows,
                budget=token_budget or 30,
            )
        )

        if comparison is not None:
            click.echo("")
            click.echo("=== Comparison: Spectral vs Louvain ===")
            ari = comparison["ari"]
            sp = comparison.get("spectral_partitions", 0)
            lp = comparison.get("louvain_partitions", 0)
            nc = comparison.get("nodes_compared", 0)
            agree_label = "high" if ari > 0.7 else ("moderate" if ari > 0.3 else "low")
            click.echo(f"  Adjusted Rand Index: {ari:.4f} ({agree_label} agreement)")
            click.echo(f"  Spectral partitions: {sp}  Louvain partitions: {lp}")
            click.echo(f"  Nodes compared: {nc}")
