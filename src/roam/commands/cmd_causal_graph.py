"""``roam causal-graph`` — per-symbol input→sink data dependencies.

R28 sub-feature 3 of 4 (shipped in W15.3).

Distinct from the call graph: causal edges record which **inputs / state
sources** flow into which **side-effects** (or return / raise) within a
single function body.

Examples
--------
    roam causal-graph                              # top-N by edge count
    roam causal-graph handleSave                   # one symbol
    roam causal-graph --kind param_to_effect       # filter by edge kind
    roam causal-graph --top 50
    roam causal-graph --json

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because causal-graph outputs are invocation-scoped causal
dependency rankings — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.helpers import auto_log
from roam.world_model.causal_graph import (
    CAUSAL_KINDS,
    MAX_EDGES_PER_SYMBOL,
    classify_causal_graph,
)


@roam_capability(
    name="causal-graph",
    category="architecture",
    summary="Build per-symbol input→sink causal graphs (param/global/env → effect/return/raise)",
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("causal-graph")
@click.argument("symbol", required=False, default=None)
@click.option(
    "--kind",
    type=click.Choice(CAUSAL_KINDS, case_sensitive=False),
    default=None,
    help="Filter edges by causal kind.",
)
@click.option(
    "--top",
    type=int,
    default=20,
    help="Limit the number of graphs surfaced (default: 20).",
)
@click.pass_context
def causal_graph_cmd(ctx, symbol, kind, top):
    """Build per-symbol causal graphs (input → sink data dependencies).

    Causal kinds:

    \b
      param_to_effect    — a parameter flows into a side-effecting call
      param_to_return    — a parameter flows into the return expression
      global_to_effect   — a global flows into a side-effecting call
      global_to_mutation — a global is written to inside the body
      env_to_effect      — an env read flows into a side-effecting call
      param_to_raise     — a parameter flows into a raise statement

    Heuristic detector — false negatives expected.  For each function we
    text-scan the body and emit an edge when a known input token appears
    on the same line as a known sink (side-effect call, return, raise,
    mutation).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    try:
        repo_root = find_project_root()
    except (OSError, RuntimeError):
        repo_root = None

    with open_db(readonly=True) as conn:
        all_graphs = classify_causal_graph(conn, symbol_name=symbol)

    # Optional kind filter — keep graphs that include at least one edge
    # of the requested kind, AND project the per-graph edges down to the
    # filtered subset for display.
    filtered = all_graphs
    if kind:
        target = kind.lower()
        filtered = []
        for g in all_graphs:
            if any(e.kind == target for e in g.edges):
                # Shallow-clone the graph with only matching edges so the
                # surfaced view stays focused.
                g_clone = type(g)(
                    symbol=g.symbol,
                    file=g.file,
                    edges=[e for e in g.edges if e.kind == target],
                    inputs=list(g.inputs),
                    sinks=list(g.sinks),
                    truncated=g.truncated,
                    confidence=g.confidence,
                    symbol_id=g.symbol_id,
                    line_start=g.line_start,
                    line_end=g.line_end,
                )
                filtered.append(g_clone)

    # Aggregate edge counts (over UN-filtered graphs).
    by_kind: Counter = Counter()
    total_edges = 0
    truncated_count = 0
    for g in all_graphs:
        for e in g.edges:
            by_kind[e.kind] += 1
            total_edges += 1
        if g.truncated:
            truncated_count += 1

    # Build verdict.
    if symbol and not filtered:
        if not all_graphs:
            verdict = f"No function/method/constructor named '{symbol}' classified."
        else:
            # Symbol exists but has no edges (pure / opaque).
            g0 = all_graphs[0]
            verdict = f"{g0.symbol} has 0 causal edges (pure or opaque)"
        partial_success = bool(symbol and not all_graphs)
    elif not all_graphs:
        verdict = "No symbols available to classify (run `roam index`)."
        partial_success = True
    elif symbol and len(filtered) == 1:
        g0 = filtered[0]
        parts: list[str] = []
        per_kind: Counter = Counter(e.kind for e in g0.edges)
        for k in CAUSAL_KINDS:
            n = per_kind.get(k, 0)
            if n:
                parts.append(f"{n} {k}")
        verdict = f"{g0.symbol} has {len(g0.edges)} causal edges ({', '.join(parts) if parts else 'no edges'})"
        partial_success = False
    else:
        # Aggregate verdict across the whole codebase.
        parts2: list[str] = []
        for k in CAUSAL_KINDS:
            n = by_kind.get(k, 0)
            if n:
                parts2.append(f"{n} {k}")
        verdict = f"Causal scan: {len(all_graphs)} symbols, {total_edges} edges" + (
            " (" + ", ".join(parts2) + ")" if parts2 else ""
        )
        partial_success = False

    # Rank graphs by interest (W596: canonical confidence-LEVEL rank):
    #   1. confidence high > medium > low (higher = more confident)
    #   2. higher edge count
    #   3. shorter file path
    def _interest(g):
        return (
            confidence_level_rank(g.confidence, fallback=-1),
            len(g.edges),
            -len(g.file or ""),
        )

    sorted_filtered = sorted(filtered, key=_interest, reverse=True)
    if top and top > 0:
        surfaced = sorted_filtered[:top]
    else:
        surfaced = sorted_filtered

    # -- agent_contract.facts (LAW 4: concrete-noun anchored) ------------
    facts: list[str] = []
    worst = sorted_filtered[0] if sorted_filtered else None
    if worst is not None and worst.edges:
        # Find the most-impacted input on the worst symbol and the kind of
        # sinks it drives — concrete-noun anchored, single-claim per fact.
        per_input: Counter = Counter()
        per_input_sinks: dict[str, Counter] = {}
        for e in worst.edges:
            per_input[e.source] += 1
            sk = e.sink.split(":", 1)[0] if ":" in e.sink else e.sink
            per_input_sinks.setdefault(e.source, Counter())[sk] += 1
        top_input, top_n = per_input.most_common(1)[0]
        # Strip the "param:" / "global:" / "env:" prefix from display
        # source for readability ("`path`" beats "`param:path`").
        display_src = top_input.split(":", 1)[1] if ":" in top_input else top_input
        sink_breakdown = per_input_sinks.get(top_input, Counter())
        sink_summary = ", ".join(f"{n} {sk}" for sk, n in sink_breakdown.most_common(3)) or "edges"
        facts.append(f"{worst.symbol} input `{display_src}` causes {top_n} sink edges ({sink_summary})")
    if by_kind.get("param_to_effect", 0):
        facts.append(
            f"causal scan found {by_kind['param_to_effect']} param_to_effect edges "
            f"across {len(all_graphs)} symbols (data flow from parameter to side-effect)"
        )
    if by_kind.get("global_to_effect", 0):
        facts.append(
            f"causal scan found {by_kind['global_to_effect']} global_to_effect edges "
            f"(module-level state flowing into side-effects)"
        )
    if by_kind.get("env_to_effect", 0):
        facts.append(
            f"causal scan found {by_kind['env_to_effect']} env_to_effect edges "
            f"(environment variables flowing into side-effects)"
        )
    if by_kind.get("param_to_return", 0):
        facts.append(
            f"causal scan found {by_kind['param_to_return']} param_to_return edges "
            f"(parameters appearing directly in return expressions)"
        )
    if truncated_count:
        facts.append(
            f"causal scan truncated {truncated_count} symbols at {MAX_EDGES_PER_SYMBOL} edges (noise cap reached)"
        )
    if not facts:
        facts.append("causal scan found no inputs flowing into side-effects")

    # -- next_commands (LAW 2: imperative) --------------------------------
    next_commands: list[str] = []
    if worst is not None and worst.edges:
        sym_arg = worst.symbol.rsplit(".", 1)[-1]
        next_commands.append(f"roam side-effects {sym_arg}")
        next_commands.append(f"roam idempotency {sym_arg}")
    if by_kind.get("param_to_effect", 0) and (not kind or kind != "param_to_effect"):
        next_commands.append("roam causal-graph --kind param_to_effect --top 20")
    if by_kind.get("env_to_effect", 0) and (not kind or kind != "env_to_effect"):
        next_commands.append("roam causal-graph --kind env_to_effect --top 20")
    if not next_commands:
        next_commands.append("roam side-effects")

    envelope = json_envelope(
        "causal-graph",
        summary={
            "verdict": verdict,
            "state": "ok" if not partial_success else "no_data",
            "partial_success": partial_success,
            "total_edges": total_edges,
            "by_kind": dict(by_kind),
            "total_classified": len(all_graphs),
            "surfaced": len(surfaced),
            "filter_kind": kind,
            "edges_truncated": truncated_count > 0,
            "truncated_symbols": truncated_count,
            "causal_kind_definition": (
                "param_to_effect | param_to_return | global_to_effect | "
                "global_to_mutation | env_to_effect | param_to_raise — "
                "emitted when a source token (param/global/env) appears on "
                "the same body line as a known sink (side-effect call / "
                "return / raise / mutation)."
            ),
            "detector": "world_model.causal_graph (heuristic)",
            "edge_cap_per_symbol": MAX_EDGES_PER_SYMBOL,
        },
        graphs=[g.to_dict() for g in surfaced],
        agent_contract={
            "facts": facts,
            "next_commands": next_commands,
        },
    )

    auto_log(envelope, action="causal-graph", target=symbol or "", repo_root=repo_root)

    if json_mode:
        click.echo(to_json(envelope))
        return

    # Text output.
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if not surfaced:
        return
    rows = []
    for g in surfaced:
        # Compact summary string for top edges.
        if g.edges:
            top_edges = ", ".join(f"{e.source}→{e.sink}" for e in g.edges[:3])
            if len(g.edges) > 3:
                top_edges += f", +{len(g.edges) - 3}"
        else:
            top_edges = "-"
        rows.append(
            [
                g.symbol[:38],
                str(len(g.edges)),
                g.confidence,
                top_edges[:60],
                (g.file or "")[-32:],
            ]
        )
    click.echo(
        format_table(
            ["Symbol", "Edges", "Conf", "Top edges", "File"],
            rows,
        )
    )
    if len(filtered) > len(surfaced):
        click.echo(f"\n(+{len(filtered) - len(surfaced)} more; --top {len(filtered)} to surface all)")


__all__ = ["causal_graph_cmd"]
