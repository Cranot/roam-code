"""Detect near-duplicate code via AST structural hashing.

Unlike ``duplicates`` (which uses metric-based similarity from the DB),
this command re-parses source files and compares actual AST subtree
structures.  Detects Type-2 clones: identical control flow with different
identifiers or literals.

Related commands: ``duplicates`` (metric-based), ``suggest-refactoring``,
``split`` (extract responsibilities).
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import (
    json_envelope,
    loc,
    to_json,
)


# R22 — confidence-derivation rule for clone clusters and pairs:
#   similarity >= 0.90 → "high"  (near-identical, almost certainly a clone)
#   similarity in [0.70, 0.90) → "medium"
#   similarity < 0.70 → "low"  (structural skeleton match only; high FP)
def _classify_similarity(sim: float) -> tuple[str, str]:
    """Map a similarity score to a (confidence, reason) tuple."""
    if sim >= 0.90:
        return "high", f"similarity {sim:.2f} ≥ 0.90 — near-identical clone"
    if sim >= 0.70:
        return "medium", f"similarity {sim:.2f} in [0.70, 0.90) — likely clone"
    return "low", f"similarity {sim:.2f} < 0.70 — structural skeleton only"


def _cluster_classify(cluster: dict) -> tuple[str, str]:
    sim = float(cluster.get("avg_similarity", 0.0) or 0.0)
    return _classify_similarity(sim)


def _pair_classify(pair: dict) -> tuple[str, str]:
    sim = float(pair.get("similarity", 0.0) or 0.0)
    return _classify_similarity(sim)


@roam_capability(
    name="clones",
    category="health",
    summary="Detect near-duplicate code via AST structural hashing (Type-2 clones).",
    inputs=["repo_path"],
    outputs=["clusters", "verdict"],
    examples=[
        "roam clones",
        "roam clones --threshold 0.85 --min-lines 8",
        "roam clones --persist",
    ],
    tags=["health", "duplication"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.option(
    "--threshold",
    default=0.70,
    show_default=True,
    type=click.FloatRange(0.0, 1.0),
    help="Minimum Jaccard similarity (0.0-1.0)",
)
@click.option(
    "--min-lines",
    default=5,
    show_default=True,
    type=int,
    help="Skip functions shorter than N lines",
)
@click.option("--scope", default=None, type=str, help="Limit to files under this path prefix")
@click.option("--top", default=0, type=int, help="Show only top N clusters (0=all)")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help="Write results to clone_pairs and clone_clusters tables for downstream consumers (roam critique, roam retrieve).",
)
@click.option(
    "--by-file",
    "by_file",
    is_flag=True,
    default=False,
    help="aggregate clone pairs into file-pair coupling, surface the top-coupled file pairs.",
)
@click.pass_context
def clones(ctx, threshold, min_lines, scope, top, persist, by_file):
    """Detect near-duplicate code via AST structural hashing.

    Re-parses source files and compares function AST structures via subtree
    hashing.  Finds Type-2 clones: identical control flow with different
    identifiers or literals.

    Unlike ``duplicates`` (metric-based), this uses actual tree-sitter AST
    comparison for higher precision.

    \b
    Examples:
      roam clones
      roam clones --threshold 0.85 --min-lines 8
      roam clones --persist
      roam clones --by-file --top 30

    See also ``duplicates`` (metric-based dup detection), ``critique``
    (clones-not-edited check on a diff), and ``debt`` (refactoring
    backlog).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    from roam.graph.clone_detect import detect_clones, store_clones

    with open_db(readonly=not persist) as conn:
        pairs, clusters = detect_clones(
            conn,
            min_similarity=threshold,
            min_lines=min_lines,
            scope=scope,
        )

        if persist:
            store_clones(conn, pairs, clusters)

        if top > 0:
            clusters = clusters[:top]

        # Summary stats
        total_functions = sum(len(c.members) for c in clusters)
        total_pairs = len(pairs)
        avg_sim = sum(c.avg_similarity for c in clusters) / len(clusters) if clusters else 0.0

        # Estimate reducible lines
        reducible_lines = 0
        for c in clusters:
            lines = sorted(m["line_end"] - m["line_start"] + 1 for m in c.members)
            if len(lines) > 1:
                reducible_lines += sum(lines[:-1])

        verdict = (
            f"{len(clusters)} clone cluster{'s' if len(clusters) != 1 else ''} "
            f"found ({total_functions} functions, {round(avg_sim * 100)}% avg similarity)"
            if clusters
            else "No structural clones detected"
        )

        # aggregate clone pairs into (file_a, file_b) coupling.
        if by_file:
            file_pair_counts: dict[tuple[str, str], int] = {}
            for p in pairs:
                key = tuple(sorted((p.file_a, p.file_b)))
                file_pair_counts[key] = file_pair_counts.get(key, 0) + 1
            file_pairs = [
                {"file_a": a, "file_b": b, "clone_pairs": n}
                for (a, b), n in sorted(file_pair_counts.items(), key=lambda x: -x[1])
            ]
            file_pairs_top = file_pairs[: max(1, top or 25)]
            verdict = f"{len(file_pairs)} clone-coupled file pair(s) (top {len(file_pairs_top)} shown)"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "clones",
                            summary={"verdict": verdict, "file_pairs_total": len(file_pairs)},
                            file_pairs=file_pairs_top,
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            if not file_pairs:
                return
            click.echo()
            click.echo(f"{'Pairs':>5}  File A  ↔  File B")
            click.echo(f"{'-' * 5}  {'-' * 60}")
            for fp in file_pairs_top:
                click.echo(f"{fp['clone_pairs']:>5}  {fp['file_a']}  ↔  {fp['file_b']}")
            return

        if json_mode:
            # R22: wrap each cluster and pair in {value, confidence,
            # reason} so consumers can weight signals. Consumers that
            # previously read `clusters[i]["avg_similarity"]` must now
            # read `clusters[i]["value"]["avg_similarity"]` plus
            # `clusters[i]["confidence"]` / `clusters[i]["reason"]`.
            cluster_values = [
                {
                    "cluster_id": c.cluster_id,
                    "avg_similarity": c.avg_similarity,
                    "size": len(c.members),
                    "members": c.members,
                    "pattern": c.pattern,
                    "suggestion": c.suggestion,
                }
                for c in clusters
            ]
            cluster_triples = wrap_findings(cluster_values, classifier=_cluster_classify)

            pair_values = [
                {
                    "file_a": p.file_a,
                    "func_a": p.func_a,
                    "line_a": p.line_a,
                    "file_b": p.file_b,
                    "func_b": p.func_b,
                    "line_b": p.line_b,
                    "similarity": p.similarity,
                }
                for p in pairs[:50]  # Cap pair output
            ]
            pair_triples = wrap_findings(pair_values, classifier=_pair_classify)

            # Combined distribution for the summary field (clusters +
            # pairs counted together — both are "findings").
            combined = cluster_triples + pair_triples
            distribution = confidence_distribution(combined)
            verdict_with_conf = verdict_with_high_count(verdict, distribution)

            click.echo(
                to_json(
                    json_envelope(
                        "clones",
                        summary={
                            "verdict": verdict_with_conf,
                            "clusters": len(clusters),
                            "clone_pairs": total_pairs,
                            "total_functions": total_functions,
                            "avg_similarity": round(avg_sim, 3),
                            "estimated_reducible_lines": reducible_lines,
                            "findings_confidence_distribution": distribution,
                        },
                        budget=token_budget,
                        clusters=cluster_triples,
                        pairs=pair_triples,
                    )
                )
            )
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")

        if not clusters:
            return

        click.echo()
        for c in clusters:
            sim_pct = round(c.avg_similarity * 100)
            click.echo(f"CLUSTER {c.cluster_id} -- {sim_pct}% similarity, {len(c.members)} functions:")
            for m in c.members:
                lines = m["line_end"] - m["line_start"] + 1
                click.echo(
                    f"  {m['function']:<40s} "
                    f"{loc(m['file'], m['line_start'])}"
                    f"  ({lines} lines, {m['ast_nodes']} AST nodes)"
                )
            click.echo(f"  Pattern: {c.pattern}")
            click.echo(f"  Suggestion: {c.suggestion}")
            click.echo()

        click.echo(
            f"SUMMARY: {len(clusters)} clusters, "
            f"{total_functions} functions, "
            f"~{reducible_lines} lines of reducible duplication"
        )
