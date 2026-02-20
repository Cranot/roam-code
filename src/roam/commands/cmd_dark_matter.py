"""Detect dark matter: co-changing files with no structural dependency."""

from __future__ import annotations

from collections import Counter

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.option('-n', 'limit', default=30, help='Max pairs to show')
@click.option('--min-npmi', default=0.3, type=float, show_default=True,
              help='Minimum NPMI threshold')
@click.option('--min-cochanges', default=3, type=int, show_default=True,
              help='Minimum co-change count')
@click.option('--explain', is_flag=True, help='Add hypothesis for each pair')
@click.option('--category', is_flag=True, help='Group output by hypothesis category')
@click.pass_context
def dark_matter(ctx, limit, min_npmi, min_cochanges, explain, category):
    """Detect dark matter: file pairs that co-change but have no structural link.

    Dark matter couplings indicate hidden dependencies -- shared databases,
    event buses, config keys, or copy-paste patterns. Use --explain to see
    hypothesized reasons for each coupling.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.graph.dark_matter import dark_matter_edges, HypothesisEngine

    with open_db(readonly=True) as conn:
        pairs = dark_matter_edges(conn, min_cochanges=min_cochanges, min_npmi=min_npmi)
        pairs = pairs[:limit]

        # Run hypothesis engine when needed
        need_hypotheses = explain or category or json_mode
        if need_hypotheses and pairs:
            root = find_project_root()
            engine = HypothesisEngine(root)
            engine.classify_all(pairs)

        if json_mode:
            by_cat: dict[str, int] = Counter()
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                by_cat[cat] += 1

            total = len(pairs)
            parts = [f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
            verdict = f"{total} dark-matter coupling{'s' if total != 1 else ''} found"
            if parts:
                verdict += f" ({', '.join(parts)})"

            click.echo(to_json(json_envelope("dark-matter",
                summary={"verdict": verdict, "total_dark_matter_edges": total, "by_category": dict(by_cat)},
                dark_matter_pairs=[
                    {
                        "file_a": p["path_a"],
                        "file_b": p["path_b"],
                        "npmi": p["npmi"],
                        "lift": p["lift"],
                        "strength": p["strength"],
                        "cochange_count": p["cochange_count"],
                        "hypothesis": p.get("hypothesis"),
                    }
                    for p in pairs
                ],
            )))
            return

        total = len(pairs)

        if not pairs:
            click.echo("VERDICT: 0 dark-matter couplings found")
            return

        # Build verdict with category breakdown if hypotheses available
        if need_hypotheses:
            by_cat = Counter()
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                by_cat[cat] += 1
            parts = [f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
            click.echo(f"VERDICT: {total} dark-matter coupling{'s' if total != 1 else ''} found ({', '.join(parts)})")
        else:
            click.echo(f"VERDICT: {total} dark-matter coupling{'s' if total != 1 else ''} found")

        click.echo()

        if category:
            # Group by hypothesis category
            groups: dict[str, list[dict]] = {}
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                groups.setdefault(cat, []).append(p)
            for cat in sorted(groups.keys()):
                click.echo(f"  [{cat}]")
                for p in groups[cat]:
                    detail = p.get("hypothesis", {}).get("detail", "")
                    click.echo(f"    {p['path_a']} <-> {p['path_b']}")
                    click.echo(f"      NPMI: {p['npmi']:.2f} | Lift: {p['lift']:.1f} | Co-changes: {p['cochange_count']}")
                    if detail:
                        click.echo(f"      Hypothesis: {cat} ({detail})")
                click.echo()
        else:
            for p in pairs:
                click.echo(f"  {p['path_a']} <-> {p['path_b']}")
                click.echo(f"    NPMI: {p['npmi']:.2f} | Lift: {p['lift']:.1f} | Co-changes: {p['cochange_count']}")
                if explain:
                    hyp = p.get("hypothesis", {})
                    cat = hyp.get("category", "UNKNOWN")
                    detail = hyp.get("detail", "")
                    click.echo(f"    Hypothesis: {cat} ({detail})")
                click.echo()
