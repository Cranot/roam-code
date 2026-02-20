"""Counterfactual architecture simulator -- test structural changes before making them."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


def _run_simulation(ctx, op_name, apply_fn, op_args_fn):
    """Shared flow for all simulate subcommands.

    Parameters
    ----------
    ctx : click.Context
    op_name : str
        Operation name for output.
    apply_fn : callable
        Transform function from simulate module.
    op_args_fn : callable(G_sim, conn) -> (dict, str | None)
        Returns (op_result, error_message).  error_message is None on success.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph
    from roam.graph.simulate import (
        compute_graph_metrics, clone_graph, metric_delta,
    )

    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)
        before = compute_graph_metrics(G)

        G_sim = clone_graph(G)
        op_result, error = op_args_fn(G_sim, conn)
        if error:
            if json_mode:
                click.echo(to_json(json_envelope("simulate",
                    summary={"verdict": error, "operation": op_name,
                             "health_delta": 0, "health_before": before["health_score"],
                             "health_after": before["health_score"],
                             "improved_metrics": 0, "degraded_metrics": 0},
                    operation={"operation": op_name, "error": error},
                    metrics={}, warnings=[error],
                )))
                return
            click.echo(f"VERDICT: {error}")
            return

        after = compute_graph_metrics(G_sim)
        deltas = metric_delta(before, after)

        health_delta = after["health_score"] - before["health_score"]
        improved = sum(1 for d in deltas.values() if d["direction"] == "improved")
        degraded = sum(1 for d in deltas.values() if d["direction"] == "degraded")

        # Warnings
        warnings = []
        if after["cycles"] > before["cycles"]:
            warnings.append(f"new cycles introduced ({before['cycles']} -> {after['cycles']})")
        if after["layer_violations"] > before["layer_violations"]:
            warnings.append(f"new layer violations ({before['layer_violations']} -> {after['layer_violations']})")
        if after["modularity"] < before["modularity"]:
            warnings.append(f"modularity decreased ({before['modularity']} -> {after['modularity']})")

        # Verdict
        mod_delta = deltas.get("modularity", {})
        mod_str = ""
        if mod_delta:
            md = mod_delta["delta"]
            mod_str = f", modularity {md:+.2f}" if md != 0 else ", modularity unchanged"

        cycle_delta = after["cycles"] - before["cycles"]
        cycle_str = f", {cycle_delta} new cycles" if cycle_delta > 0 else ", 0 new cycles"

        if health_delta > 0:
            verdict = f"health {health_delta:+d} ({before['health_score']} -> {after['health_score']}){mod_str}{cycle_str}"
        elif health_delta == 0:
            verdict = f"health unchanged at {before['health_score']}{mod_str}{cycle_str}"
        else:
            verdict = f"health {health_delta:+d} ({before['health_score']} -> {after['health_score']}){mod_str}{cycle_str}"

        if json_mode:
            click.echo(to_json(json_envelope("simulate",
                summary={
                    "verdict": verdict,
                    "operation": op_name,
                    "health_delta": health_delta,
                    "health_before": before["health_score"],
                    "health_after": after["health_score"],
                    "improved_metrics": improved,
                    "degraded_metrics": degraded,
                },
                operation=op_result,
                metrics=deltas,
                warnings=warnings,
            )))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo("")

        # Operation summary
        op_line = op_result.get("operation", op_name).upper()
        sym = op_result.get("symbol", "")
        from_f = op_result.get("from_file", "")
        to_f = op_result.get("to_file", op_result.get("target_file", ""))
        if sym and from_f and to_f:
            click.echo(f"OPERATION: {op_line} {sym} from {from_f} to {to_f}")
        elif op_result.get("removed"):
            click.echo(f"OPERATION: {op_line} {', '.join(op_result['removed'])}")
        elif op_result.get("merged_file"):
            click.echo(f"OPERATION: {op_line} {op_result['merged_file']} into {to_f}")
        else:
            click.echo(f"OPERATION: {op_line}")
        click.echo("")

        # Metric deltas table
        click.echo("METRIC DELTAS:")
        label_map = {
            "health_score": "Health score",
            "cycles": "Cycles",
            "tangle_ratio": "Tangle ratio",
            "layer_violations": "Layer violations",
            "modularity": "Modularity",
            "fiedler": "Fiedler",
            "propagation_cost": "Propagation cost",
            "god_components": "God components",
            "bottlenecks": "Bottlenecks",
            "nodes": "Nodes",
            "edges": "Edges",
        }
        display_order = [
            "health_score", "cycles", "tangle_ratio", "layer_violations",
            "modularity", "fiedler", "propagation_cost",
            "god_components", "bottlenecks",
        ]
        for key in display_order:
            d = deltas.get(key)
            if d is None:
                continue
            label = label_map.get(key, key)
            b_val = d["before"]
            a_val = d["after"]
            pct = d["pct_change"]
            direction = d["direction"]

            if isinstance(b_val, float):
                val_str = f"{b_val:.4f} -> {a_val:.4f}"
            else:
                val_str = f"{b_val} -> {a_val}"

            if direction == "unchanged":
                pct_str = "(no change)"
            else:
                pct_str = f"({pct:+.1f}%)"

            dir_str = ""
            if direction == "improved":
                dir_str = "  IMPROVED"
            elif direction == "degraded":
                dir_str = "  DEGRADED"

            click.echo(f"  {label:20s} {val_str:24s} {pct_str:14s}{dir_str}")

        if warnings:
            click.echo("")
            click.echo("WARNINGS:")
            for w in warnings:
                click.echo(f"  - {w}")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group("simulate")
@click.pass_context
def simulate(ctx):
    """Counterfactual architecture simulator.

    Test structural changes (move, extract, merge, delete) on the dependency
    graph and see predicted metric deltas before making actual code changes.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

@simulate.command("move")
@click.argument("symbol")
@click.argument("target_file")
@click.pass_context
def simulate_move(ctx, symbol, target_file):
    """Simulate moving a symbol to a different file."""
    from roam.graph.simulate import apply_move, resolve_target

    def do_op(G_sim, conn):
        node_ids, label = resolve_target(G_sim, conn, symbol)
        if not node_ids:
            return {}, f"symbol not found: {symbol}"
        result = apply_move(G_sim, node_ids[0], target_file)
        return result, None

    _run_simulation(ctx, "move", apply_move, do_op)


@simulate.command("extract")
@click.argument("symbol")
@click.argument("target_file")
@click.pass_context
def simulate_extract(ctx, symbol, target_file):
    """Simulate extracting a symbol and its private callees to a new file."""
    from roam.graph.simulate import apply_extract, resolve_target

    def do_op(G_sim, conn):
        node_ids, label = resolve_target(G_sim, conn, symbol)
        if not node_ids:
            return {}, f"symbol not found: {symbol}"
        result = apply_extract(G_sim, node_ids[0], target_file)
        return result, None

    _run_simulation(ctx, "extract", apply_extract, do_op)


@simulate.command("merge")
@click.argument("file_a")
@click.argument("file_b")
@click.pass_context
def simulate_merge(ctx, file_a, file_b):
    """Simulate merging file_b into file_a."""
    from roam.graph.simulate import apply_merge

    def do_op(G_sim, conn):
        # Check file_b has nodes
        norm_b = file_b.replace("\\", "/")
        has_nodes = any(
            norm_b in (G_sim.nodes[n].get("file_path") or "").replace("\\", "/")
            for n in G_sim.nodes
        )
        if not has_nodes:
            return {}, f"no symbols found in: {file_b}"
        result = apply_merge(G_sim, file_a, file_b)
        return result, None

    _run_simulation(ctx, "merge", apply_merge, do_op)


@simulate.command("delete")
@click.argument("target")
@click.pass_context
def simulate_delete(ctx, target):
    """Simulate deleting a symbol or all symbols in a file."""
    from roam.graph.simulate import apply_delete, resolve_target

    def do_op(G_sim, conn):
        node_ids, label = resolve_target(G_sim, conn, target)
        if not node_ids:
            return {}, f"target not found: {target}"
        result = apply_delete(G_sim, node_ids)
        return result, None

    _run_simulation(ctx, "delete", apply_delete, do_op)
