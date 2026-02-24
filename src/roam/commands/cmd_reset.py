"""Delete the index DB and force a fresh reindex (recovery for AI agents)."""

from __future__ import annotations

import click

from roam.db.connection import get_db_path, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.exit_codes import EXIT_USAGE, EXIT_ERROR


@click.command("reset")
@click.option("--force", is_flag=True, default=False,
              help="Required to confirm destructive reset")
@click.option("--root", default=".", help="Project root")
@click.pass_context
def reset(ctx, force, root):
    """Delete the index DB and rebuild from scratch.

    Equivalent to: rm .roam/index.db && roam init

    Requires --force to confirm the destructive operation.
    Useful when the index is corrupted or out of sync.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False

    if not force:
        if json_mode:
            click.echo(to_json(json_envelope("reset", summary={
                "verdict": "aborted -- use --force to confirm destructive reset",
                "removed": False,
                "force_required": True,
            })))
        else:
            click.echo("VERDICT: aborted -- use --force to confirm destructive reset")
            click.echo("  Run `roam reset --force` to delete and rebuild the index.")
        ctx.exit(EXIT_USAGE)
        return

    project_root = find_project_root(root)
    db_path = get_db_path(project_root)

    removed = False

    if db_path.exists():
        try:
            db_path.unlink()
            removed = True
        except OSError as exc:
            if json_mode:
                click.echo(to_json(json_envelope("reset", summary={
                    "verdict": f"error -- failed to delete index: {exc}",
                    "removed": False,
                    "error": str(exc),
                })))
            else:
                click.echo(f"VERDICT: error -- failed to delete index: {exc}")
            ctx.exit(EXIT_ERROR)
            return

    if not json_mode:
        action = "deleted" if removed else "no index found"
        click.echo(f"VERDICT: reset -- index {action}, rebuilding...")

    # Rebuild via ensure_index (same as roam init does)
    from roam.commands.resolve import ensure_index
    try:
        ensure_index(quiet=json_mode)
    except Exception as exc:
        if json_mode:
            click.echo(to_json(json_envelope("reset", summary={
                "verdict": f"error -- index deleted but rebuild failed: {exc}",
                "removed": removed,
                "error": str(exc),
            })))
        else:
            click.echo(f"  Rebuild failed: {exc}")
        ctx.exit(EXIT_ERROR)
        return

    if json_mode:
        click.echo(to_json(json_envelope("reset", summary={
            "verdict": "reset complete -- index deleted and rebuilt",
            "removed": removed,
            "db_path": str(db_path),
        })))
    else:
        click.echo("  Done. Index rebuilt successfully.")
        click.echo("  Run `roam health` to verify the new index.")
