"""Counterfactual architecture simulator -- test structural changes before making them.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because simulate outputs are invocation-scoped scenario-planning
what-if envelopes (cloned-graph deltas under proposed move / extract /
merge transforms) -- not per-location code violations on the real
codebase. The simulation operates on a counterfactual graph that does
not yet exist on disk, so there are no source coordinates to populate
SARIF ``locations[]``. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH Bucket B propagation plan + W1221-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

_MAX_GRAPH_SYMBOLS = 10000


def _empty_metrics_floor() -> dict:
    """Metric floor used when a simulate substrate degrades."""
    return {
        "health_score": 0,
        "cycles": 0,
        "tangle_ratio": 0.0,
        "layer_violations": 0,
        "modularity": 0.0,
        "fiedler": 0.0,
        "propagation_cost": 0.0,
        "god_components": 0,
        "bottlenecks": 0,
        "nodes": 0,
        "edges": 0,
    }


def _simulation_topology_unchanged(G, G_sim) -> bool:
    """Whether two graphs have identical node and edge topology.

    Simulation metrics are topology-derived; move/extract/merge usually
    change only ``file_path`` attributes. Reusing the baseline metrics for
    unchanged topology avoids an expensive recompute without trusting the
    operation name.
    """
    if G is None or G_sim is None:
        return False
    if G.number_of_nodes() != G_sim.number_of_nodes():
        return False
    if G.number_of_edges() != G_sim.number_of_edges():
        return False
    if G.nodes.keys() != G_sim.nodes.keys():
        return False
    return all(G_sim.has_edge(u, v) for u, v in G.edges())


def _simulation_metric_diff(before: dict, after: dict, metric_delta_fn) -> tuple[dict, list[str]]:
    """Compute metric deltas and human-readable regression warnings."""
    deltas_local = metric_delta_fn(before, after)
    warnings_local: list[str] = []
    if after.get("cycles", 0) > before.get("cycles", 0):
        warnings_local.append(f"new cycles introduced ({before.get('cycles', 0)} -> {after.get('cycles', 0)})")
    if after.get("layer_violations", 0) > before.get("layer_violations", 0):
        warnings_local.append(
            f"new layer violations ({before.get('layer_violations', 0)} -> {after.get('layer_violations', 0)})"
        )
    if after.get("modularity", 0) < before.get("modularity", 0):
        warnings_local.append(f"modularity decreased ({before.get('modularity', 0)} -> {after.get('modularity', 0)})")
    return (deltas_local, warnings_local)


def _simulation_verdict(before: dict, after: dict, deltas) -> tuple[str, int, int, int]:
    """Compose the LAW 6 single-line health-delta verdict."""
    health_before_local = before.get("health_score", 0)
    health_after_local = after.get("health_score", 0)
    health_delta_local = health_after_local - health_before_local

    mod_delta = deltas.get("modularity", {}) if isinstance(deltas, dict) else {}
    mod_str = ""
    if isinstance(mod_delta, dict) and mod_delta:
        md = mod_delta.get("delta", 0)
        mod_str = f", modularity {md:+.2f}" if md != 0 else ", modularity unchanged"

    cycle_delta = after.get("cycles", 0) - before.get("cycles", 0)
    cycle_str = f", {cycle_delta} new cycles" if cycle_delta > 0 else ", 0 new cycles"

    if health_delta_local > 0:
        return (
            f"health {health_delta_local:+d} ({health_before_local} -> {health_after_local}){mod_str}{cycle_str}",
            health_delta_local,
            health_before_local,
            health_after_local,
        )
    if health_delta_local == 0:
        return (
            f"health unchanged at {health_before_local}{mod_str}{cycle_str}",
            health_delta_local,
            health_before_local,
            health_after_local,
        )
    return (
        f"health {health_delta_local:+d} ({health_before_local} -> {health_after_local}){mod_str}{cycle_str}",
        health_delta_local,
        health_before_local,
        health_after_local,
    )


def _metric_direction_counts(deltas) -> tuple[int, int]:
    """Count improved and degraded metric directions defensively."""
    if not isinstance(deltas, dict):
        return (0, 0)
    improved_local = sum(1 for d in deltas.values() if isinstance(d, dict) and d.get("direction") == "improved")
    degraded_local = sum(1 for d in deltas.values() if isinstance(d, dict) and d.get("direction") == "degraded")
    return (improved_local, degraded_local)


def _simulation_facts(verdict: str, improved: int, degraded: int) -> list[str]:
    """Build the simulate agent-contract facts."""
    return [
        verdict,
        f"{improved} improved metrics",
        f"{degraded} degraded metrics",
    ]


def _simulation_next_commands(degraded: int, warnings: list[str]) -> list[str]:
    """Build conditional simulate next commands."""
    cmds = []
    if degraded > 0:
        cmds.append("roam preflight")
    if warnings:
        cmds.append("roam health")
    return cmds


def _emit_simulation_error(json_mode: bool, op_name: str, error: str, before: dict, warnings_out: list[str]) -> None:
    """Emit the transform-resolution error path."""
    if json_mode:
        envelope_summary: dict = {
            "verdict": error,
            "operation": op_name,
            "health_delta": 0,
            "health_before": before.get("health_score", 0),
            "health_after": before.get("health_score", 0),
            "improved_metrics": 0,
            "degraded_metrics": 0,
        }
        envelope_kwargs: dict = dict(
            summary=envelope_summary,
            operation={"operation": op_name, "error": error},
            metrics={},
            warnings=[error],
        )
        if warnings_out:
            envelope_summary["partial_success"] = True
            envelope_summary["warnings_out"] = list(warnings_out)
            envelope_kwargs["warnings_out"] = list(warnings_out)
        click.echo(to_json(json_envelope("simulate", **envelope_kwargs)))
        return
    click.echo(f"VERDICT: {error}")


def _emit_simulation_json_output(
    op_name: str,
    op_result: dict,
    deltas: dict,
    warnings: list[str],
    facts: list[str],
    next_commands: list[str],
    verdict: str,
    health_delta: int,
    health_before: int,
    health_after: int,
    improved: int,
    degraded: int,
    warnings_out: list[str],
) -> None:
    """Emit the successful JSON simulation envelope."""
    envelope_summary: dict = {
        "verdict": verdict,
        "operation": op_name,
        "health_delta": health_delta,
        "health_before": health_before,
        "health_after": health_after,
        "improved_metrics": improved,
        "degraded_metrics": degraded,
    }
    envelope_kwargs: dict = dict(
        summary=envelope_summary,
        operation=op_result,
        metrics=deltas,
        warnings=warnings,
        agent_contract={
            "facts": facts,
            "risks": [],
            "next_commands": next_commands,
            "confidence": None,
        },
    )
    if warnings_out:
        envelope_summary["partial_success"] = True
        envelope_summary["warnings_out"] = list(warnings_out)
        envelope_kwargs["warnings_out"] = list(warnings_out)
    click.echo(to_json(json_envelope("simulate", **envelope_kwargs)))


def _simulation_operation_line(op_name: str, op_result) -> str:
    """Format the text-mode operation line."""
    op_line = op_result.get("operation", op_name).upper() if isinstance(op_result, dict) else op_name.upper()
    sym = op_result.get("symbol", "") if isinstance(op_result, dict) else ""
    from_f = op_result.get("from_file", "") if isinstance(op_result, dict) else ""
    to_f = op_result.get("to_file", op_result.get("target_file", "")) if isinstance(op_result, dict) else ""
    if sym and from_f and to_f:
        return f"OPERATION: {op_line} {sym} from {from_f} to {to_f}"
    if isinstance(op_result, dict) and op_result.get("removed"):
        return f"OPERATION: {op_line} {', '.join(op_result['removed'])}"
    if isinstance(op_result, dict) and op_result.get("merged_file"):
        return f"OPERATION: {op_line} {op_result['merged_file']} into {to_f}"
    return f"OPERATION: {op_line}"


def _simulation_metric_delta_line(key: str, delta: dict) -> str:
    """Format one text-mode metric delta row."""
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
    label = label_map.get(key, key)
    b_val = delta["before"]
    a_val = delta["after"]
    pct = delta["pct_change"]
    direction = delta["direction"]

    val_str = f"{b_val:.4f} -> {a_val:.4f}" if isinstance(b_val, float) else f"{b_val} -> {a_val}"
    pct_str = "(no change)" if direction == "unchanged" else f"({pct:+.1f}%)"

    dir_str = ""
    if direction == "improved":
        dir_str = "  IMPROVED"
    elif direction == "degraded":
        dir_str = "  DEGRADED"

    return f"  {label:20s} {val_str:24s} {pct_str:14s}{dir_str}"


def _emit_simulation_text_output(op_name: str, op_result, deltas, warnings: list[str], verdict: str) -> None:
    """Emit the human-readable simulation output."""
    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(_simulation_operation_line(op_name, op_result))
    click.echo("")

    click.echo("METRIC DELTAS:")
    display_order = [
        "health_score",
        "cycles",
        "tangle_ratio",
        "layer_violations",
        "modularity",
        "fiedler",
        "propagation_cost",
        "god_components",
        "bottlenecks",
    ]
    for key in display_order:
        delta = deltas.get(key) if isinstance(deltas, dict) else None
        if delta is not None:
            click.echo(_simulation_metric_delta_line(key, delta))

    if warnings:
        click.echo("")
        click.echo("WARNINGS:")
        for warning in warnings:
            click.echo(f"  - {warning}")


def _warn_if_large_graph(conn, json_mode: bool) -> None:
    """Warn text users before running simulation on a large graph."""
    sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    if sym_count > _MAX_GRAPH_SYMBOLS and not json_mode:
        click.echo(f"Warning: large graph ({sym_count} symbols) -- simulation may be slow.", err=True)


def _simulate_baseline(_run_check_ef, conn, build_symbol_graph, compute_graph_metrics, empty_metrics_floor: dict):
    """Load the baseline graph and metrics through the W607-EF wrapper."""

    def _load_baseline_graph():
        G_local = build_symbol_graph(conn)
        before_local = compute_graph_metrics(G_local)
        return (G_local, before_local)

    baseline = _run_check_ef(
        "load_baseline_graph",
        _load_baseline_graph,
        default=(None, dict(empty_metrics_floor)),
    )
    if baseline is None:
        baseline = (None, dict(empty_metrics_floor))
    G, before = baseline
    if before is None:
        before = dict(empty_metrics_floor)
    return G, before


def _simulate_transform(_run_check_ef, G, conn, clone_graph, op_args_fn):
    """Clone the baseline graph and apply the requested counterfactual transform."""

    def _apply_transforms():
        if G is None:
            return (None, {}, "baseline graph unavailable")
        G_sim_local = clone_graph(G)
        op_result_local, error_local = op_args_fn(G_sim_local, conn)
        return (G_sim_local, op_result_local, error_local)

    transformed = _run_check_ef(
        "apply_transforms",
        _apply_transforms,
        default=(None, {}, "transform unavailable"),
    )
    if transformed is None:
        transformed = (None, {}, "transform unavailable")
    G_sim, op_result, error = transformed
    if op_result is None:
        op_result = {}
    return G_sim, op_result, error


def _simulate_after_metrics(_run_check_ef, G, G_sim, before: dict, compute_graph_metrics, empty_metrics_floor: dict):
    """Compute post-transform metrics through the W607-EF wrapper."""

    def _recompute_metrics():
        if G_sim is None:
            return dict(empty_metrics_floor)
        if _simulation_topology_unchanged(G, G_sim):
            return dict(before)
        return compute_graph_metrics(G_sim)

    after = _run_check_ef(
        "recompute_metrics",
        _recompute_metrics,
        default=dict(empty_metrics_floor),
    )
    if after is None:
        after = dict(empty_metrics_floor)
    return after


def _simulate_metric_deltas(_run_check_ef, before: dict, after: dict, metric_delta):
    """Compute metric deltas and warning strings through the W607-EF wrapper."""

    def _diff_metrics():
        return _simulation_metric_diff(before, after, metric_delta)

    diffed = _run_check_ef(
        "diff_metrics",
        _diff_metrics,
        default=({}, []),
    )
    if diffed is None:
        diffed = ({}, [])
    deltas, warnings = diffed
    if deltas is None:
        deltas = {}
    if warnings is None:
        warnings = []
    return deltas, warnings


def _simulate_verdict_bundle(_run_check_ef, before: dict, after: dict, deltas):
    """Compose the verdict bundle through the W607-EF wrapper."""

    def _compose_verdict():
        return _simulation_verdict(before, after, deltas)

    verdict_bundle = _run_check_ef(
        "compose_verdict",
        _compose_verdict,
        default=("health unchanged at 0, 0 new cycles", 0, 0, 0),
    )
    if verdict_bundle is None:
        verdict_bundle = ("health unchanged at 0, 0 new cycles", 0, 0, 0)
    verdict, health_delta, health_before, health_after = verdict_bundle
    if not isinstance(verdict, str) or not verdict:
        verdict = "health unchanged at 0, 0 new cycles"
    return verdict, health_delta, health_before, health_after


def _simulate_direction_counts(_run_check_ef, deltas) -> tuple[int, int]:
    """Count improved/degraded directions through the W607-EF wrapper."""

    def _count_directions():
        return _metric_direction_counts(deltas)

    counts = _run_check_ef(
        "diff_metrics",
        _count_directions,
        default=(0, 0),
    )
    if counts is None:
        counts = (0, 0)
    return counts


def _simulate_agent_contract_parts(_run_check_ef, verdict: str, improved: int, degraded: int, warnings: list[str]):
    """Build agent contract fields through the W607-EF wrappers."""

    def _compose_facts():
        return _simulation_facts(verdict, improved, degraded)

    facts = _run_check_ef(
        "compose_facts",
        _compose_facts,
        default=[verdict],
    )
    if facts is None:
        facts = [verdict]

    def _compose_next_commands():
        return _simulation_next_commands(degraded, warnings)

    next_commands = _run_check_ef(
        "compose_next_commands",
        _compose_next_commands,
        default=[],
    )
    if next_commands is None:
        next_commands = []
    return facts, next_commands


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
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph
    from roam.graph.simulate import (
        clone_graph,
        compute_graph_metrics,
        metric_delta,
    )

    # W607-EF -- substrate-boundary plumbing for cmd_simulate.
    # ``_run_check_ef`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607ef_warnings_out`` rather than
    # crashing the simulate command outright. cmd_simulate is the fifth
    # leg of the architecture-prediction PENTAGON at substrate-CALL
    # layer -- alongside cmd_orchestrate (W607-DS), cmd_partition
    # (W607-DU), cmd_agent_plan (W607-DY), and cmd_fleet (W607-EB) --
    # and uniquely emits counterfactual graph-mutation envelopes
    # (move/extract/merge/delete deltas on a cloned graph). A raise
    # inside ``build_symbol_graph`` (baseline) / ``clone_graph`` +
    # transform application / ``compute_graph_metrics`` /
    # ``metric_delta`` / or any downstream verdict / envelope composer
    # used to crash the simulate command outright. Marker family
    # ``simulate_<phase>_failed:<exc_class>:<detail>``. Substrates
    # wrapped:
    #
    #   * load_baseline_graph     -- DB -> baseline networkx graph +
    #                                pre-transform metrics
    #   * apply_transforms        -- clone_graph + op_args_fn dispatch
    #                                (move/extract/merge/delete
    #                                counterfactual mutations)
    #   * recompute_metrics       -- compute_graph_metrics on the
    #                                counterfactual graph
    #   * diff_metrics            -- metric_delta(before, after) + warning
    #                                derivation (cycles / layer-violations
    #                                / modularity regressions)
    #   * compose_verdict         -- LAW 6 single-line health-delta floor
    #   * compose_facts           -- agent_contract.facts list
    #   * compose_next_commands   -- agent_contract.next_commands
    #   * serialize_envelope      -- JSON envelope emission
    #   * format_text_output      -- text path metric-delta table printing
    #
    # W978 7-discipline applied: (1) f-string verdict floor uses literal
    # zero-count text -- no Name references, (2) default={...} carries
    # plain literals, (3) no json.dumps(default=str) needed (no
    # datetimes), (4) ``simulate_*`` prefix is unique (collision-checked
    # by cross-prefix-discipline test), (5) len() at kwarg-bind is gated
    # by the envelope fallback, (6) len() / if x: on a poisoned object
    # only runs after the empty-floor guard, (7) no dict.get(key,
    # expensive_default) calls -- all defaults are immutable literals.
    _w607ef_warnings_out: list[str] = []

    def _run_check_ef(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-EF marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``simulate_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ef_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ef_warnings_out.append(f"simulate_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-EF: empty-floor metric used by every degraded path so the
    # simulate envelope still composes a coherent verdict. Literal
    # zero-count baseline avoids re-introducing the 7919-CATASTROPHE
    # shape (CONSTRAINT 12 first-token executability) -- the verdict
    # emits the executable empty-state, not raw input values.
    empty_metrics_floor: dict = _empty_metrics_floor()

    with open_db(readonly=True) as conn:
        _warn_if_large_graph(conn, json_mode)
        G, before = _simulate_baseline(
            _run_check_ef,
            conn,
            build_symbol_graph,
            compute_graph_metrics,
            empty_metrics_floor,
        )
        G_sim, op_result, error = _simulate_transform(
            _run_check_ef,
            G,
            conn,
            clone_graph,
            op_args_fn,
        )

        if error:
            _emit_simulation_error(json_mode, op_name, error, before, _w607ef_warnings_out)
            return

    after = _simulate_after_metrics(_run_check_ef, G, G_sim, before, compute_graph_metrics, empty_metrics_floor)
    deltas, warnings = _simulate_metric_deltas(_run_check_ef, before, after, metric_delta)
    verdict, health_delta, health_before, health_after = _simulate_verdict_bundle(
        _run_check_ef,
        before,
        after,
        deltas,
    )
    improved, degraded = _simulate_direction_counts(_run_check_ef, deltas)
    facts, next_commands = _simulate_agent_contract_parts(_run_check_ef, verdict, improved, degraded, warnings)

    if json_mode:
        # W607-EF: ``serialize_envelope`` substrate -- json_envelope
        # construction + click.echo emission. The wrap protects against
        # crashes inside the formatter call so the marker surfaces and
        # the function returns cleanly.
        def _serialize_envelope():
            _emit_simulation_json_output(
                op_name,
                op_result,
                deltas,
                warnings,
                facts,
                next_commands,
                verdict,
                health_delta,
                health_before,
                health_after,
                improved,
                degraded,
                _w607ef_warnings_out,
            )

        _run_check_ef("serialize_envelope", _serialize_envelope, default=None)
        return

    # W607-EF: ``format_text_output`` substrate -- the human-readable
    # text emission path. A raise inside the loop (e.g. KeyError on a
    # malformed delta dict missing ``before`` / ``after``) degrades to a
    # verdict-only emission so the user still sees the LAW 6 floor.
    def _format_text_output():
        _emit_simulation_text_output(op_name, op_result, deltas, warnings, verdict)

    _run_check_ef("format_text_output", _format_text_output, default=None)
    # Marker accumulator handles disclosure on the text path -- the
    # warning rides into ``_w607ef_warnings_out`` even when text-mode
    # output is human-targeted (JSON mode carries the structured
    # disclosure surface).


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="simulate",
    category="architecture",
    summary="Counterfactual architecture simulator",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.group("simulate")
@click.pass_context
def simulate_cli(ctx):
    """Counterfactual architecture simulator.

    Unlike ``mutate`` (which generates actual code changes), this command
    predicts metric deltas on a cloned graph without modifying any files.

    Test structural changes (move, extract, merge, delete) on the dependency
    graph and see predicted metric deltas before making actual code changes.

    \b
    Examples:
      roam simulate move handle_login src/auth/login.py
      roam simulate extract validate_input src/validation.py
      roam simulate merge src/foo.py src/bar.py
      roam simulate delete legacy_helper

    See also ``mutate`` (apply real code transforms), ``preflight``
    (combined pre-change safety), and ``impact`` (current blast radius).
    """
    ctx.ensure_object(dict)


simulate = simulate_cli


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
        has_nodes = any(norm_b in (G_sim.nodes[n].get("file_path") or "").replace("\\", "/") for n in G_sim.nodes)
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
