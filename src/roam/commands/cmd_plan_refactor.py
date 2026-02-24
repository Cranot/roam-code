"""Compound refactoring plan for one symbol."""

from __future__ import annotations

import click

from roam.commands.cmd_guard import _layer_analysis, _risk_score, _trim_signature
from roam.commands.context_helpers import (
    gather_symbol_context,
    get_affected_tests_bfs,
    get_blast_radius,
    get_graph_metrics,
    get_symbol_metrics,
)
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db
from roam.output.formatter import budget_truncate, json_envelope, loc, to_json


def _summarize_tests(test_hits: list[dict], cap: int) -> tuple[list[dict], int, int]:
    by_file: dict[str, dict] = {}

    for hit in test_hits:
        path = hit["file"]
        kind = hit["kind"]
        hops = int(hit["hops"])
        priority = (0 if kind == "DIRECT" else 1, hops)

        existing = by_file.get(path)
        if existing is None or priority < existing["_priority"]:
            by_file[path] = {
                "file": path,
                "kind": kind,
                "hops": hops,
                "via": hit.get("via"),
                "_priority": priority,
            }

    rows = sorted(
        by_file.values(),
        key=lambda r: (0 if r["kind"] == "DIRECT" else 1, r["hops"], r["file"]),
    )
    for row in rows:
        row.pop("_priority", None)

    direct_files = sum(1 for row in rows if row["kind"] == "DIRECT")
    return rows[:cap], direct_files, len(rows)


def _default_target_file(source_file: str, operation: str) -> str:
    norm = (source_file or "").replace("\\", "/")
    suffix = "_refactor" if operation == "extract" else "_moved"

    if "/" in norm:
        directory, filename = norm.rsplit("/", 1)
    else:
        directory, filename = "", norm

    stem, dot, ext = filename.rpartition(".")
    if dot:
        name = f"{stem}{suffix}.{ext}"
    else:
        name = f"{filename}{suffix}.py"
    return f"{directory}/{name}" if directory else name


def _metric_summary(deltas: dict) -> dict:
    focus = (
        "health_score",
        "cycles",
        "layer_violations",
        "modularity",
        "tangle_ratio",
        "propagation_cost",
    )
    out: dict = {}
    for key in focus:
        delta = deltas.get(key)
        if not delta:
            continue
        out[key] = {
            "before": delta.get("before"),
            "after": delta.get("after"),
            "delta": delta.get("delta"),
            "direction": delta.get("direction"),
        }
    return out


def _simulation_previews(
    conn,
    sym: dict,
    operation: str,
    target_file: str,
) -> list[dict]:
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.simulate import (
            apply_extract,
            apply_move,
            clone_graph,
            compute_graph_metrics,
            metric_delta,
        )
    except Exception:
        return []

    try:
        G = build_symbol_graph(conn)
    except Exception:
        return []

    sym_id = int(sym["id"])
    if sym_id not in G:
        return []

    before = compute_graph_metrics(G)
    ops = ["extract", "move"] if operation == "auto" else [operation]
    previews: list[dict] = []

    for op in ops:
        tgt = target_file.strip() or _default_target_file(sym["file_path"], op)
        G_sim = clone_graph(G)
        if op == "extract":
            op_result = apply_extract(G_sim, sym_id, tgt)
        else:
            op_result = apply_move(G_sim, sym_id, tgt)

        after = compute_graph_metrics(G_sim)
        deltas = metric_delta(before, after)
        health_delta = int(after["health_score"] - before["health_score"])
        cycles_delta = int(after["cycles"] - before["cycles"])
        layer_delta = int(after["layer_violations"] - before["layer_violations"])
        improved = sum(1 for d in deltas.values() if d.get("direction") == "improved")
        degraded = sum(1 for d in deltas.values() if d.get("direction") == "degraded")

        score = (
            (health_delta * 4)
            + (improved - degraded)
            - (max(0, cycles_delta) * 3)
            - (max(0, layer_delta) * 3)
        )

        previews.append(
            {
                "operation": op,
                "target_file": tgt,
                "result": op_result,
                "score": int(score),
                "health_delta": health_delta,
                "cycles_delta": cycles_delta,
                "layer_violations_delta": layer_delta,
                "improved_metrics": improved,
                "degraded_metrics": degraded,
                "metrics": _metric_summary(deltas),
            },
        )

    previews.sort(
        key=lambda p: (
            -p["score"],
            -p["health_delta"],
            p["cycles_delta"],
            p["layer_violations_delta"],
            p["operation"],
        ),
    )
    return previews


def _build_steps(
    *,
    symbol_name: str,
    selected_preview: dict | None,
    risk_score: int,
    blast: dict,
    direct_test_files: int,
    total_test_files: int,
    layers: dict,
) -> list[dict]:
    steps: list[dict] = [
        {
            "title": "Capture baseline safety snapshot",
            "details": (
                "Record pre-change risk, architecture, and metrics so post-change "
                "regressions are measurable."
            ),
            "command": f"roam guard {symbol_name}",
        },
    ]

    if direct_test_files <= 0:
        steps.append(
            {
                "title": "Add characterization tests before changing behavior",
                "details": "No direct covering tests were found for this symbol.",
                "command": f"roam test-map {symbol_name}",
            },
        )
    elif total_test_files < 2:
        steps.append(
            {
                "title": "Strengthen regression test breadth",
                "details": "Coverage exists but is thin for a structural refactor.",
                "command": f"roam affected-tests {symbol_name}",
            },
        )

    if int(layers.get("violation_count") or 0) > 0:
        steps.append(
            {
                "title": "Resolve existing layer violations first",
                "details": (
                    "Refactoring on top of active layer violations increases "
                    "the chance of architectural drift."
                ),
                "command": f"roam layers --focus {symbol_name}",
            },
        )

    if selected_preview:
        op = selected_preview["operation"]
        target = selected_preview["target_file"]
        steps.append(
            {
                "title": f"Apply {op} refactor in small commit slices",
                "details": (
                    f"Target file: {target}. Use staged slices to keep "
                    "rollback and review bounded."
                ),
                "command": f"roam simulate {op} {symbol_name} {target}",
            },
        )
    else:
        steps.append(
            {
                "title": "Apply incremental extract/simplify changes",
                "details": "Graph simulation is unavailable; proceed with conservative slices.",
                "command": f"roam context {symbol_name}",
            },
        )

    dependent_symbols = int(blast.get("dependent_symbols") or 0)
    dependent_files = int(blast.get("dependent_files") or 0)
    if dependent_symbols > 0:
        steps.append(
            {
                "title": "Migrate dependents and imports in bounded batches",
                "details": (
                    f"{dependent_symbols} downstream symbols in {dependent_files} files "
                    "depend on this symbol."
                ),
                "command": f"roam impact {symbol_name}",
            },
        )

    if risk_score >= 60:
        steps.append(
            {
                "title": "Use compatibility shim + deprecation window",
                "details": (
                    "High risk score warrants a temporary compatibility layer "
                    "to avoid abrupt downstream breakage."
                ),
                "command": f"roam breaking HEAD~1..HEAD",
            },
        )

    steps.append(
        {
            "title": "Run verification gates and close-out checks",
            "details": "Validate architecture, tests, and policy gates before merge.",
            "command": "roam verify --threshold 80",
        },
    )

    for idx, step in enumerate(steps, start=1):
        step["step"] = idx
    return steps


@click.command("plan-refactor")
@click.argument("symbol")
@click.option(
    "--operation",
    default="auto",
    type=click.Choice(["auto", "extract", "move"], case_sensitive=False),
    show_default=True,
    help="Preferred structural operation. 'auto' compares extract vs move previews.",
)
@click.option(
    "--target-file",
    default="",
    help="Explicit target file path for move/extract simulation preview.",
)
@click.option(
    "--max-steps",
    default=7,
    show_default=True,
    type=int,
    help="Maximum number of plan steps to return.",
)
@click.pass_context
def plan_refactor(ctx, symbol, operation, target_file, max_steps):
    """Build an ordered refactoring plan with risk, test, and impact context."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = bool(ctx.obj.get("detail", False)) if ctx.obj else False
    ensure_index()

    operation = str(operation or "auto").lower()
    test_cap = 20 if detail else 8
    layer_cap = 20 if detail else 8

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, symbol)
        if sym is None:
            click.echo(symbol_not_found(conn, symbol, json_mode=json_mode))
            raise SystemExit(1)

        context = gather_symbol_context(conn, sym, task="refactor", use_propagation=False)
        test_hits = get_affected_tests_bfs(conn, sym["id"], max_hops=8)
        tests, direct_test_files, total_test_files = _summarize_tests(test_hits, cap=test_cap)

        blast = get_blast_radius(conn, sym["id"])
        symbol_metrics = get_symbol_metrics(conn, sym["id"])
        graph_metrics = get_graph_metrics(conn, sym["id"])
        layers = _layer_analysis(conn, sym["id"], cap=layer_cap)
        risk_score, risk_level, risk_factors = _risk_score(
            blast=blast,
            symbol_metrics=symbol_metrics,
            graph_metrics=graph_metrics,
            direct_test_files=direct_test_files,
            total_test_files=total_test_files,
            layer_violation_count=layers["violation_count"],
            move_sensitive_count=layers["move_sensitive_count"],
        )

        previews = _simulation_previews(conn, sym, operation, target_file)

    selected_preview = previews[0] if previews else None
    plan_steps = _build_steps(
        symbol_name=sym["qualified_name"] or sym["name"],
        selected_preview=selected_preview,
        risk_score=risk_score,
        blast=blast,
        direct_test_files=direct_test_files,
        total_test_files=total_test_files,
        layers=layers,
    )
    plan_steps = plan_steps[: max(1, int(max_steps))]
    for idx, step in enumerate(plan_steps, start=1):
        step["step"] = idx

    if selected_preview:
        strategy = f"{selected_preview['operation']} -> {selected_preview['target_file']}"
    else:
        strategy = "manual incremental refactor (simulation unavailable)"

    verdict = (
        f"{risk_level} risk ({risk_score}/100), {len(plan_steps)}-step plan, strategy: {strategy}"
    )

    definition = {
        "name": sym["name"],
        "qualified_name": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "signature": _trim_signature(sym["signature"] if "signature" in sym.keys() else None),
        "location": loc(sym["file_path"], sym["line_start"]),
    }

    if json_mode:
        payload = json_envelope(
            "plan-refactor",
            summary={
                "verdict": verdict,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "steps": len(plan_steps),
                "selected_strategy": strategy,
                "callers": len(context["non_test_callers"]),
                "callees": len(context["callees"]),
                "test_files": total_test_files,
                "dependent_symbols": int(blast.get("dependent_symbols") or 0),
                "dependent_files": int(blast.get("dependent_files") or 0),
            },
            definition=definition,
            context_counts={
                "callers": len(context["non_test_callers"]),
                "callees": len(context["callees"]),
                "test_files": total_test_files,
                "direct_test_files": direct_test_files,
            },
            blast_radius=blast,
            risk={
                "score": risk_score,
                "level": risk_level,
                "factors": risk_factors,
            },
            layer_analysis={
                "current_layer": layers["current_layer"],
                "violation_count": layers["violation_count"],
                "move_sensitive_count": layers["move_sensitive_count"],
                "violations": layers["violations"],
                "move_sensitive_edges": layers["move_sensitive_edges"],
            },
            simulation_previews=previews if detail else previews[:1],
            tests=tests if detail else tests[: min(5, len(tests))],
            plan=plan_steps,
        )
        click.echo(to_json(payload))
        return

    lines = [
        f"Refactor plan: {definition['qualified_name']} {definition['location']}",
        f"VERDICT: {verdict}",
        (
            "Context: "
            f"{len(context['non_test_callers'])} callers, "
            f"{len(context['callees'])} callees, "
            f"{direct_test_files}/{total_test_files} direct/total test files"
        ),
        (
            "Blast radius: "
            f"{int(blast.get('dependent_symbols') or 0)} symbols across "
            f"{int(blast.get('dependent_files') or 0)} files"
        ),
    ]
    if selected_preview:
        lines.append(
            "Simulation preview: "
            f"{selected_preview['operation']} -> {selected_preview['target_file']} "
            f"(health {selected_preview['health_delta']:+d}, "
            f"cycles {selected_preview['cycles_delta']:+d}, "
            f"layers {selected_preview['layer_violations_delta']:+d})",
        )

    lines.append("")
    lines.append("Steps:")
    for step in plan_steps:
        lines.append(f"{step['step']}. {step['title']}")
        if detail:
            lines.append(f"   {step['details']}")
            lines.append(f"   run: {step['command']}")
        else:
            lines.append(f"   run: {step['command']}")

    click.echo(budget_truncate("\n".join(lines), token_budget))
