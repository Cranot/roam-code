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

# A single pathological SCC can carry hundreds of files / thousands of
# symbols (observed live on roam-code's own graph: a 1531-symbol / 390-file
# component). ``cycles`` is a JSON envelope's ONLY payload field, so when its
# serialized size alone exceeds the default token budget, the generic
# budget_truncate_json drop path deletes the WHOLE field rather than
# partially trimming it -- an agent asking "what are the biggest cycles"
# on a large repo got summary.cycle_count > 0 but an empty items list, with
# no way to tell "no cycles" from "cycles found but discarded" (the same
# failure shape 92a18361 fixed for the disclosure flag, but that fix did not
# stop the drop itself). Cap each cycle's member lists here, at the source,
# so a giant SCC can never by itself blow the field out of the envelope --
# the truncation is disclosed per-cycle (``symbols_truncated`` /
# ``files_truncated``) rather than silent, and ``size`` / ``file_count``
# keep reporting the true, uncapped counts.
_MAX_MEMBERS_PER_CYCLE_JSON = 40


def _capped_for_json(cyc: dict, max_members: int = _MAX_MEMBERS_PER_CYCLE_JSON) -> dict:
    """Return *cyc* with over-long ``symbols``/``files`` lists capped + disclosed.

    ``size`` / ``file_count`` are left untouched (they already carry the true,
    uncapped counts) -- only the enumerated member lists are bounded.
    """
    symbols = cyc.get("symbols") or []
    files = cyc.get("files") or []
    if len(symbols) <= max_members and len(files) <= max_members:
        return cyc
    out = dict(cyc)
    if len(symbols) > max_members:
        out["symbols"] = symbols[:max_members]
        out["symbols_truncated"] = True
    if len(files) > max_members:
        out["files"] = files[:max_members]
        out["files_truncated"] = True
    return out


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
            shown_json = [_capped_for_json(c) for c in shown]
            members_capped = any(c.get("symbols_truncated") or c.get("files_truncated") for c in shown_json)
            summary: dict = {
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
            }
            if members_capped:
                # A pathologically large SCC had its symbols/files list capped
                # at the source (see _capped_for_json) so it cannot blow the
                # envelope's only payload field out of the JSON token budget.
                # size/file_count still report the true, uncapped counts —
                # this is a disclosed partial view of THOSE cycles' members,
                # not a dropped result.
                summary["partial_success"] = True
                summary["cycle_members_capped"] = _MAX_MEMBERS_PER_CYCLE_JSON
            click.echo(
                to_json(
                    json_envelope(
                        "cycles",
                        summary=summary,
                        cycles=shown_json,
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
