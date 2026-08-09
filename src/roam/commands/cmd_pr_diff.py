"""Show structural consequences of code changes (graph delta, not text diff).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because pr-diff outputs are invocation-scoped graph-delta
summaries (symbols added / removed / modified, edges gained / lost,
blast-radius shifts between two refs) — not per-location code
violations. The downstream ``pr-risk`` / ``pr-analyze`` commands roll
this delta into risk findings; pr-diff itself returns the structural
delta envelope without source coordinates suitable for SARIF
``locations[]``. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH propagation plan + W1224-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files_status, resolve_changed_to_db
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.exit_codes import EXIT_GATE_FAILURE, gate_should_fail
from roam.output.formatter import echo_text_warnings, json_envelope, to_json


@roam_capability(
    name="pr-diff",
    category="workflow",
    summary="Show structural impact of pending changes",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "review"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("pr-diff")
@click.option("--staged", is_flag=True, help="Analyse staged changes only.")
@click.option("--range", "commit_range", default=None, help="Git range, e.g. main..HEAD.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "markdown"]),
    default="text",
    help="Output format.",
)
@click.option("--fail-on-degradation", is_flag=True, help="Exit 1 if health score degraded.")
@click.pass_context
def pr_diff_cmd(ctx, staged, commit_range, fmt, fail_on_degradation):
    """Show structural impact of pending changes.

    Compares current metrics against the latest snapshot to show metric
    deltas, cross-cluster edges, layer violations, symbol changes, and
    overall graph footprint.

    Unlike ``diff`` (which shows the developer-facing blast radius of
    changed symbols), this command compares aggregate CI-level metrics
    before and after a change.

    \b
    Examples:
      roam pr-diff
      roam pr-diff --staged
      roam pr-diff --commit-range main..HEAD
      roam pr-diff --fail-on-degradation

    See also ``diff`` (developer-facing blast radius), ``pr-risk``
    (PR risk score), and ``critique`` (clones-not-edited check on a
    diff).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # Determine changed files
    # W1462: read the ``(paths, error_kind)`` form — an empty list from a
    # FAILED ``git diff`` must not be published as "no changes detected"
    # and must not authorise under --fail-on-degradation.
    changed, git_error = get_changed_files_status(root, staged=staged, commit_range=commit_range)
    if git_error is not None:
        verdict = f"diff unavailable: {git_error} — cannot gate"
        # W1331: the SAME bucket reaches both channels. Building it once and
        # emitting it in both branches is what keeps the text reader from
        # seeing a quieter story than the --json reader.
        warnings_out = [f"pr_diff_changed_files_failed:{git_error}:cannot read git diff"]
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "pr-diff",
                        summary={
                            "verdict": verdict,
                            "footprint_pct": 0.0,
                            "metric_deltas_available": False,
                            "health_delta": None,
                            "new_issues": 0,
                            "partial_success": True,
                            "git_error": git_error,
                        },
                        changed_files=[],
                        metric_deltas={},
                        edge_analysis={
                            "total_from_changed": 0,
                            "cross_cluster": [],
                            "layer_violations": [],
                        },
                        symbol_changes={"added": [], "removed": [], "modified": []},
                        footprint={
                            "files_changed": 0,
                            "files_total": 0,
                            "files_pct": 0.0,
                            "symbols_changed": 0,
                            "symbols_total": 0,
                            "symbols_pct": 0.0,
                        },
                        warnings_out=list(warnings_out),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"Could not read the git diff ({git_error}); no change set was measured.")
            echo_text_warnings(warnings_out)
        if fail_on_degradation:
            ctx.exit(EXIT_GATE_FAILURE)
        return
    if not changed:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "pr-diff",
                        summary={
                            "verdict": "no changes detected",
                            "footprint_pct": 0.0,
                            "metric_deltas_available": False,
                            "health_delta": None,
                            "new_issues": 0,
                        },
                        changed_files=[],
                        metric_deltas={},
                        edge_analysis={
                            "total_from_changed": 0,
                            "cross_cluster": [],
                            "layer_violations": [],
                        },
                        symbol_changes={"added": [], "removed": [], "modified": []},
                        footprint={
                            "files_changed": 0,
                            "files_total": 0,
                            "files_pct": 0.0,
                            "symbols_changed": 0,
                            "symbols_total": 0,
                            "symbols_pct": 0.0,
                        },
                    )
                )
            )
        else:
            click.echo("No changed files detected.")
        return

    # Determine base ref for snapshot matching
    base_ref = "HEAD"
    if commit_range and ".." in commit_range:
        base_ref = commit_range.split("..")[0]

    from roam.commands.metrics_history import collect_metrics
    from roam.graph.diff import (
        compute_footprint,
        edge_analysis,
        find_before_snapshot,
        metric_delta,
        symbol_changes,
    )

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
        baseline_commit = None
        if before:
            deltas = metric_delta(before, current)
            deltas_available = True
            # `find_before_snapshot` falls back to the LATEST snapshot by
            # timestamp when ``base_ref`` has no snapshot of its own, so
            # `metric_deltas_available: true` does NOT mean "compared against
            # the base". Name the commit the baseline actually came from, so
            # the claim carries its own comparand instead of implying one.
            try:
                baseline_commit = before["git_commit"]
            except (KeyError, IndexError, TypeError):
                baseline_commit = None
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
    health_degraded = health_delta is not None and health_delta < 0
    has_layer_violations = len(edges.get("layer_violations", [])) > 0
    has_cross_cluster = len(edges.get("cross_cluster", [])) > 0
    fp_pct = footprint["files_pct"]

    if health_degraded or fp_pct > 10 or has_layer_violations:
        verdict = f"significant structural impact (footprint: {fp_pct}% of graph)"
    elif has_cross_cluster or fp_pct > 2:
        verdict = f"moderate structural impact (footprint: {fp_pct}% of graph)"
    else:
        verdict = f"minimal structural impact (footprint: {fp_pct}% of graph)"

    # W1526 -- the gate cannot fire without a baseline, so say so rather than
    # publishing "0 new issues". `roam init` leaves 0 snapshots (measured), so
    # this is the DEFAULT state of a freshly onboarded repo, not a corner case.
    # The shape and wording mirror the git_error branch ~90 lines above, which
    # already refuses on the same UNANALYZABLE class with `partial_success:
    # true` and a "cannot gate" verdict.
    no_baseline_for_gate = fail_on_degradation and not deltas_available
    if no_baseline_for_gate:
        verdict = (
            f"no baseline snapshot for {base_ref} - cannot gate on degradation "
            "(run `roam index`, or `roam trends --save` on the base ref, to record one)"
        )
    # ONE decision, read by all three output channels below. This file carried
    # THREE hand-written copies of `fail_on_degradation and health_degraded`
    # -- the highest count in the batch, and exactly the duplication
    # `gate_should_fail` exists to eliminate.
    gate_failed = gate_should_fail(
        fail_on_degradation,
        findings=health_degraded,
        scan_incomplete=not deltas_available,
    )

    # --- JSON output ---
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "pr-diff",
                    summary={
                        "verdict": verdict,
                        "footprint_pct": fp_pct,
                        "metric_deltas_available": deltas_available,
                        "health_delta": health_delta,
                        "new_issues": new_issues,
                        "baseline_commit": baseline_commit,
                        "partial_success": no_baseline_for_gate,
                    },
                    changed_files=changed,
                    metric_deltas=deltas,
                    edge_analysis=edges,
                    symbol_changes=sym_changes,
                    footprint=footprint,
                )
            )
        )
        if gate_failed:
            ctx.exit(EXIT_GATE_FAILURE)
        return

    # --- Markdown output ---
    if fmt == "markdown":
        _emit_markdown(verdict, deltas, deltas_available, edges, sym_changes, footprint, changed)
        if gate_failed:
            ctx.exit(EXIT_GATE_FAILURE)
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
        click.echo("METRIC DELTAS: No snapshot found. Run 'roam trends --save' to enable delta tracking.")
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
            click.echo(f"  {lv['source']} (L{lv['source_layer']}) -> {lv['target']} (L{lv['target_layer']})")
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

    if no_baseline_for_gate:
        click.echo()
        click.echo(
            f"Could not gate: no baseline snapshot for {base_ref}, so no metric delta "
            "was computed. Record one with `roam index`, or `roam trends --save` on "
            "the base ref."
        )

    if gate_failed:
        ctx.exit(EXIT_GATE_FAILURE)


def _emit_markdown(verdict, deltas, deltas_available, edges, sym_changes, footprint, changed):
    """Emit GitHub/GitLab compatible markdown output."""
    click.echo("## PR Structural Diff")
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
                f"| {label} | {d['before']} | {d['after']} | {d['delta']:+g} ({d['pct_change']:+.1f}%) | {direction} |"
            )
        click.echo()
    else:
        click.echo("_No snapshot found. Run `roam trends --save` to enable delta tracking._")
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
            click.echo(f"- `{lv['source']}` (L{lv['source_layer']}) -> `{lv['target']}` (L{lv['target_layer']})")
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
    click.echo(f"- Files: {footprint['files_changed']} / {footprint['files_total']} ({footprint['files_pct']}%)")
    click.echo(
        f"- Symbols: {footprint['symbols_changed']} / {footprint['symbols_total']} ({footprint['symbols_pct']}%)"
    )
