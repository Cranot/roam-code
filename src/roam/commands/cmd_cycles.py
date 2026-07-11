"""Show import/call cycles (strongly-connected components of the symbol graph).

The focused view of the cycle analysis that ``roam health`` bundles — parallels
``roam clusters`` (community detection) and ``roam layers`` (dependency-layer
violations), all three exposing a ``roam.graph.*`` analysis as its own command.

Output formats: text (default), ``--json``. SARIF is deliberately NOT emitted
because cycles outputs are invocation-scoped SCC rankings — not per-location
violations; multi-file expansion would distort SARIF semantics. Same basis on
which ``clusters`` / ``layers`` skip SARIF. See W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import empty_corpus_state, ensure_index
from roam.db.connection import open_db
from roam.graph.builder import build_symbol_graph
from roam.graph.cycles import (
    find_cycles,
    format_cycles,
    mark_actionable_cycles,
    mark_shadow_artifacts,
)
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="cycles",
    category="architecture",
    summary="Show import/call cycles (Tarjan SCCs) in the symbol graph",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("architecture",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--min-size", type=int, default=2, help="Minimum SCC size to report (default 2).")
@click.option("--limit", type=int, default=20, help="Max cycles to list (default 20).")
@click.option(
    "--actionable-only",
    "actionable_only",
    is_flag=True,
    default=False,
    help="Show only actionable cycles (span >=2 distinct non-test files).",
)
@click.pass_context
def cycles(ctx, min_size, limit, actionable_only):
    """List strongly-connected components (import/call cycles) of the symbol graph.

    A cycle is ``actionable`` when it spans >=2 distinct non-test files; intra-file
    and test-only SCCs are excluded from architectural scoring. The focused
    counterpart to the cycle section of ``roam health``.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    with open_db(readonly=True) as conn:
        # B3 (Pattern-2): a 0-symbol corpus must NOT report "clean dependency
        # graph" — there is no graph to analyze. Disclose empty_corpus instead
        # of a vacuous clean verdict.
        _empty = empty_corpus_state(conn)
        if _empty is not None:
            empty_verdict = "no symbols indexed — no dependency graph to analyze (run `roam index --force`)"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "cycles",
                            summary={
                                "verdict": empty_verdict,
                                "cycle_count": 0,
                                "actionable_count": 0,
                                **_empty,
                            },
                            cycles=[],
                            budget=token_budget,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {empty_verdict}")
            return

        graph = build_symbol_graph(conn)
        raw = find_cycles(graph, min_size=min_size)
        formatted = format_cycles(raw, conn) if raw else []
        mark_actionable_cycles(formatted)
        # Label-only classification: phantom shadow-cycle artifacts (resolver
        # mislink into a destructured consumer binding). Never suppresses —
        # genuine cycles report unchanged; renderers just annotate.
        mark_shadow_artifacts(formatted, graph, conn)
        shadow_count = sum(1 for c in formatted if c.get("shadow_artifact"))
        actionable = [c for c in formatted if c.get("actionable")]
        pool = actionable if actionable_only else formatted
        shown = sorted(pool, key=lambda c: -c.get("size", 0))[: max(0, limit)]

        verdict = (
            f"{len(formatted)} import cycles, {len(actionable)} actionable"
            if formatted
            else "No import cycles — clean dependency graph"
        )

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "cycles",
                        summary={
                            "verdict": verdict,
                            "cycle_count": len(formatted),
                            "actionable_count": len(actionable),
                            "cycle_count_definition": (
                                "strongly-connected components (Tarjan SCC) of the symbol "
                                "import/call graph with >= min_size members; actionable = "
                                "spans >=2 distinct non-test files"
                            ),
                            "shadow_artifact_count": shadow_count,
                            "shadow_artifact_definition": (
                                "cycles whose closing edge is a likely name-resolution "
                                "mislink into a non-exported destructured binding that "
                                "shadows a distinct cross-file export; label-only, never "
                                "excluded from counts"
                            ),
                        },
                        cycles=shown,
                        budget=token_budget,
                    )
                )
            )
            return

        click.echo(f"VERDICT: {verdict}")
        if not shown:
            return
        click.echo("")
        for i, cyc in enumerate(shown, 1):
            mark = "!" if cyc.get("actionable") else " "
            names = ", ".join(s.get("name", "?") for s in cyc.get("symbols", [])[:6])
            file_count = cyc.get("file_count", len(cyc.get("files", [])))
            shadow_note = " [shadow-artifact? likely resolver mislink]" if cyc.get("shadow_artifact") else ""
            click.echo(f"  {mark} cycle {i}: {cyc.get('size')} symbols, {file_count} file(s){shadow_note}")
            click.echo(f"      files:   {', '.join(cyc.get('files', [])[:5])}")
            click.echo(f"      symbols: {names}")
        if len(pool) > len(shown):
            click.echo(f"\n  ... +{len(pool) - len(shown)} more (use --limit / --json)")
