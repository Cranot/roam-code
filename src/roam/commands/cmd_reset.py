"""Delete the index DB and force a fresh reindex (recovery for AI agents).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam reset`` is a setup/bootstrap command — its
output is human-facing setup status (index DB deleted and rebuilt from
scratch), not analysis findings with file:line coordinates.
SARIF is reserved for scanning results. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root, get_db_path
from roam.exit_codes import EXIT_ERROR, EXIT_USAGE
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="reset",
    category="getting-started",
    summary="Delete the index DB and rebuild from scratch",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    # W365-followup: deleting the index DB is a side effect (writes to disk /
    # mutates state). destructive=True and side_effect=False were
    # self-contradictory under capability.py:64's canonical semantic.
    side_effect=True,
    task_required=False,
    destructive=True,
    stale_sensitive=False,
    ai_safe=False,
    requires_index=False,
)
@click.command("reset")
@click.option("--force", is_flag=True, default=False, help="Required to confirm destructive reset")
@click.option("--root", default=".", help="Project root")
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="preview the reset (db path + size) without deleting.",
)
@click.pass_context
def reset_command(ctx, force, root, dry_run):
    """Delete the index DB and rebuild from scratch.

    Requires --force to confirm the destructive operation. Unlike ``clean``
    (which surgically removes orphaned records while preserving valid data),
    this command deletes the entire index and rebuilds from scratch. Use
    ``doctor`` to verify environment health after a reset.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    project_root = find_project_root(root)
    db_path_preview = get_db_path(project_root)
    if dry_run:
        # preview shouldn't require --force.
        size_bytes = 0
        if db_path_preview.exists():
            try:
                size_bytes = db_path_preview.stat().st_size
            except OSError:
                size_bytes = 0
        verdict = (
            f"would delete {db_path_preview} ({size_bytes} bytes)"
            if size_bytes
            else f"no index at {db_path_preview} — nothing to delete"
        )
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "reset",
                        summary={"verdict": verdict, "dry_run": True, "would_remove_bytes": size_bytes},
                        db_path=str(db_path_preview),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    if not force:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "reset",
                        summary={
                            "verdict": "aborted -- use --force to confirm destructive reset",
                            "removed": False,
                            "force_required": True,
                        },
                        status="usage_error",
                        isError=True,
                        error_code="USAGE_ERROR",
                        error="aborted -- use --force to confirm destructive reset",
                    )
                )
            )
        else:
            click.echo("VERDICT: aborted -- use --force to confirm destructive reset")
            click.echo("  Run `roam reset --force` to delete and rebuild the index.")
        ctx.exit(EXIT_USAGE)
        return

    db_path = get_db_path(project_root)

    removed = False

    if db_path.exists():
        try:
            db_path.unlink()
            removed = True
        except OSError as exc:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "reset",
                            summary={
                                "verdict": f"error -- failed to delete index: {exc}",
                                "removed": False,
                                "error": str(exc),
                            },
                        )
                    )
                )
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
            click.echo(
                to_json(
                    json_envelope(
                        "reset",
                        summary={
                            "verdict": f"error -- index deleted but rebuild failed: {exc}",
                            "removed": removed,
                            "error": str(exc),
                        },
                    )
                )
            )
        else:
            click.echo(f"  Rebuild failed: {exc}")
        ctx.exit(EXIT_ERROR)
        return

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "reset",
                    summary={
                        "verdict": "reset complete -- index deleted and rebuilt",
                        "removed": removed,
                        "db_path": str(db_path),
                    },
                )
            )
        )
    else:
        click.echo("  Done. Index rebuilt successfully.")
        click.echo("  Run `roam health` to verify the new index.")


reset = reset_command
