"""Show structural consequences of code changes (graph delta, not text diff)."""

from __future__ import annotations

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db


@click.command("pr-diff")
@click.option("--staged", is_flag=True, help="Analyse staged changes only.")
@click.option("--range", "commit_range", default=None,
              help="Git range, e.g. main..HEAD.")
@click.option("--format", "fmt", type=click.Choice(["text", "markdown"]),
              default="text", help="Output format.")
@click.option("--fail-on-degradation", is_flag=True,
              help="Exit 1 if health score degraded.")
@click.pass_context
def pr_diff(ctx, staged, commit_range, fmt, fail_on_degradation):
    """Show structural impact of pending changes.

    Compares current metrics against the latest snapshot to show metric
    deltas, cross-cluster edges, layer violations, symbol changes, and
    overall graph footprint.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # Determine changed files
    changed = get_changed_files(root, staged=staged, commit_range=commit_range)
    if not changed:
        if json_mode:
            click.echo(to_json(json_envelope(
                "pr-diff",
                summary={"verdict": "no changes detected",
                         "footprint_pct": 0.0,
                         "metric_deltas_available": False,
                         "health_delta": None,
                         "new_issues": 0},
                changed_files=[],
                metric_deltas={},
                edge_analysis={"total_from_changed": 0,
                               "cross_cluster": [],
                               "layer_violations": []},
                symbol_changes={"added": [], "removed": [], "modified": []},
                footprint={"files_changed": 0, "files_total": 0,
                           "files_pct": 0.0, "symbols_changed": 0,
                           "symbols_total": 0, "symbols_pct": 0.0},
            )))
        else:
            click.echo("No changed files detected.")
        return

    # Determine base ref for snapshot matching
    base_ref = "HEAD"
    if commit_range and ".." in commit_range:
        base_ref = commit_range.split("..")[0]

    from roam.graph.diff import (
        find_before_snapshot,
        metric_delta,
        edge_analysis,
        symbol_changes,
        compute_footprint,
    )
    from roam.commands.metrics_history import collect_metrics

    with open_db(readonly=True) as conn:
        file_map = resolve_changed_to_db(conn, changed)
        changed_file_ids = list(file_map.values())

        # Current metrics
        current = collect_metrics(conn)

        # Before snapshot
        before = find_before_snapshot(conn, root, base_ref)
        deltas = {}
        deltas_available = False
        health_delta = None
        if before:
            deltas = metric_delta(before, current)
            deltas_available = True
            if "health_score" in deltas:
                health_delta = deltas["health_score"]["delta"]

        # Edge analysis
        edges = edge_analysis(conn, changed_file_ids)

        # Symbol changes
        sym_changes = symbol_changes(conn, root, base_ref, changed)

        # Footprint
        footprint = compute_footprint(conn, changed_file_ids)

    # Count new issues
    new_issues = 0
    for m in ["cycles", "god_components", "layer_violations", "brain_methods"]:
        if m in deltas and deltas[m]["direction"] == "degraded":
            new_issues += int(deltas[m]["delta"])

    # Verdict
    health_degraded = (health_delta is not None and health_delta < 0)
    has_layer_violations = len(edges.get("layer_violations", [])) > 0
    has_cross_cluster = len(edges.get("cross_cluster", [])) > 0
    fp_pct = footprint["files_pct"]

    if health_degraded or fp_pct > 10 or has_layer_violations:
        verdict = f"significant structural impact (footprint: {fp_pct}% of graph)"
    elif has_cross_cluster or fp_pct > 2:
        verdict = f"moderate structural impact (footprint: {fp_pct}% of graph)"
    else:
        verdict = f"minimal structural impact (footprint: {fp_pct}% of graph)"

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "pr-diff",
            summary={
                "verdict": verdict,
                "footprint_pct": fp_pct,
                "metric_deltas_available": deltas_available,
                "health_delta": health_delta,
                "new_issues": new_issues,
            },
            changed_files=changed,
            metric_deltas=deltas,
            edge_analysis=edges,
            symbol_changes=sym_changes,
            footprint=footprint,
        )))
        if fail_on_degradation and health_degraded:
            ctx.exit(1)
        return

    # --- Markdown output ---
    if fmt == "markdown":
        _emit_markdown(verdict, deltas, deltas_available, edges,
                       sym_changes, footprint, changed)
        if fail_on_degradation and health_degraded:
            ctx.exit(1)
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    # Metric deltas
    if deltas_available and deltas:
        click.echo("METRIC DELTAS:")
        for metric, d in deltas.items():
            label = metric.replace("_", " ").title()
            arrow = "<<"
            flag = ""
            if d["direction"] == "degraded":
                flag = f"  {arrow} DEGRADED"
            elif d["direction"] == "improved":
                flag = f"  {arrow} IMPROVED"

            if d["delta"] == 0:
                delta_str = "(no change)"
            elif isinstance(d["delta"], float) and d["delta"] != int(d["delta"]):
                delta_str = f"({d['delta']:+.1f}, {d['pct_change']:+.1f}%)"
            else:
                delta_str = f"({d['delta']:+d}, {d['pct_change']:+.1f}%)"

            click.echo(f"  {label:20s} {d['before']} -> {d['after']}  {delta_str}{flag}")
        click.echo()
    else:
        click.echo("METRIC DELTAS: No snapshot found. Run 'roam snapshot' to enable delta tracking.")
        click.echo()

    # Edge analysis
    total_edges = edges["total_from_changed"]
    click.echo(f"EDGE ANALYSIS: {total_edges} dependency edges from {len(changed)} changed files")
    for cc in edges.get("cross_cluster", []):
        click.echo(f"  cross-cluster: {cc['source']} -> {cc['target']}  << WARNING")
    click.echo()

    # Layer violations
    lvs = edges.get("layer_violations", [])
    if lvs:
        click.echo("LAYER VIOLATIONS:")
        for lv in lvs:
            click.echo(
                f"  {lv['source']} (L{lv['source_layer']}) -> "
                f"{lv['target']} (L{lv['target_layer']})"
            )
        click.echo()

    # Symbol changes
    n_added = len(sym_changes["added"])
    n_removed = len(sym_changes["removed"])
    n_modified = len(sym_changes["modified"])
    click.echo(f"SYMBOL CHANGES: +{n_added} added, -{n_removed} removed, {n_modified} modified")
    click.echo()

    # Footprint
    click.echo(
        f"FOOTPRINT: {footprint['files_changed']} / {footprint['files_total']} files "
        f"({footprint['files_pct']}%), "
        f"{footprint['symbols_changed']} / {footprint['symbols_total']} symbols "
        f"({footprint['symbols_pct']}%)"
    )

    if fail_on_degradation and health_degraded:
        ctx.exit(1)


def _emit_markdown(verdict, deltas, deltas_available, edges,
                   sym_changes, footprint, changed):
    """Emit GitHub/GitLab compatible markdown output."""
    click.echo(f"## PR Structural Diff")
    click.echo()
    click.echo(f"**Verdict:** {verdict}")
    click.echo()

    # Metric deltas table
    if deltas_available and deltas:
        click.echo("### Metric Deltas")
        click.echo()
        click.echo("| Metric | Before | After | Delta | Direction |")
        click.echo("|--------|--------|-------|-------|-----------|")
        for metric, d in deltas.items():
            label = metric.replace("_", " ").title()
            direction = d["direction"].upper()
            click.echo(
                f"| {label} | {d['before']} | {d['after']} | "
                f"{d['delta']:+g} ({d['pct_change']:+.1f}%) | {direction} |"
            )
        click.echo()
    else:
        click.echo("_No snapshot found. Run `roam snapshot` to enable delta tracking._")
        click.echo()

    # Edge analysis
    click.echo("### Edge Analysis")
    click.echo()
    click.echo(f"- **{edges['total_from_changed']}** dependency edges from **{len(changed)}** changed files")
    for cc in edges.get("cross_cluster", []):
        click.echo(f"- Cross-cluster: `{cc['source']}` -> `{cc['target']}`")
    click.echo()

    # Layer violations
    lvs = edges.get("layer_violations", [])
    if lvs:
        click.echo("### Layer Violations")
        click.echo()
        for lv in lvs:
            click.echo(
                f"- `{lv['source']}` (L{lv['source_layer']}) -> "
                f"`{lv['target']}` (L{lv['target_layer']})"
            )
        click.echo()

    # Symbol changes
    click.echo("### Symbol Changes")
    click.echo()
    click.echo(
        f"- **+{len(sym_changes['added'])}** added, "
        f"**-{len(sym_changes['removed'])}** removed, "
        f"**{len(sym_changes['modified'])}** modified"
    )
    click.echo()

    # Footprint
    click.echo("### Footprint")
    click.echo()
    click.echo(
        f"- Files: {footprint['files_changed']} / {footprint['files_total']} "
        f"({footprint['files_pct']}%)"
    )
    click.echo(
        f"- Symbols: {footprint['symbols_changed']} / {footprint['symbols_total']} "
        f"({footprint['symbols_pct']}%)"
    )
