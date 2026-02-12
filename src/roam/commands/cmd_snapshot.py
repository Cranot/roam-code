"""Persist a health metrics snapshot."""

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.metrics_history import append_snapshot


@click.command()
@click.option("--tag", default=None, help="Label for this snapshot (e.g. 'v2.1', 'pre-refactor')")
@click.pass_context
def snapshot(ctx, tag):
    """Save a snapshot of current health metrics.

    Snapshots are stored in the index DB and can be viewed with `roam trend`.
    Use --tag to label important milestones.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db() as conn:
        result = append_snapshot(conn, tag=tag, source="snapshot")

    if json_mode:
        click.echo(to_json(json_envelope("snapshot",
            summary={
                "health_score": result["health_score"],
                "tag": tag,
            },
            **result,
        )))
    else:
        tag_str = f" [{tag}]" if tag else ""
        click.echo(f"Snapshot saved{tag_str}")
        click.echo(f"  Health: {result['health_score']}/100")
        click.echo(f"  Files: {result['files']}  Symbols: {result['symbols']}  Edges: {result['edges']}")
        click.echo(f"  Cycles: {result['cycles']}  God: {result['god_components']}  "
                    f"Bottlenecks: {result['bottlenecks']}  Dead: {result['dead_exports']}  "
                    f"Violations: {result['layer_violations']}")
        if result.get("git_branch"):
            click.echo(f"  Branch: {result['git_branch']}  Commit: {result.get('git_commit', '?')}")
