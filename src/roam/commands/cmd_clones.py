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

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import (
    json_envelope,
    loc,
    to_json,
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
    help="redactedaggregate clone pairs into file-pair coupling, surface the top-coupled file pairs.",
)
@click.pass_context
def clones(ctx, threshold, min_lines, scope, top, persist, by_file):
    """Detect near-duplicate code via AST structural hashing.

    Re-parses source files and compares function AST structures via subtree
    hashing.  Finds Type-2 clones: identical control flow with different
    identifiers or literals.

    Unlike ``duplicates`` (metric-based), this uses actual tree-sitter AST
    comparison for higher precision.
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

        # redactedaggregate clone pairs into (file_a, file_b) coupling.
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
            clusters_json = []
            for c in clusters:
                clusters_json.append(
                    {
                        "cluster_id": c.cluster_id,
                        "avg_similarity": c.avg_similarity,
                        "size": len(c.members),
                        "members": c.members,
                        "pattern": c.pattern,
                        "suggestion": c.suggestion,
                    }
                )

            pairs_json = [
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

            click.echo(
                to_json(
                    json_envelope(
                        "clones",
                        summary={
                            "verdict": verdict,
                            "clusters": len(clusters),
                            "clone_pairs": total_pairs,
                            "total_functions": total_functions,
                            "avg_similarity": round(avg_sim, 3),
                            "estimated_reducible_lines": reducible_lines,
                        },
                        budget=token_budget,
                        clusters=clusters_json,
                        pairs=pairs_json,
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
