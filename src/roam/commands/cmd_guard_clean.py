"""`roam guard-clean` — prune the verdict log to its last N entries.

SARIF is deliberately NOT emitted: this command rewrites the verdict
log file in-place — it has no per-file findings to report and no source
locations to populate.

The verdict log at `.roam/verdict-log.jsonl` is append-only by default
and grows unbounded. Over months of CI runs it can hit MB-scale, which
slows `roam guard-history` reads and bloats clones. This command:

  * Truncates the log to the last `--keep N` entries (default 500)
  * Atomic write via temp-file + rename so a concurrent appender never
    sees a half-rewritten log
  * `--dry-run` reports what would be removed without writing

Idempotent: re-running on an already-trimmed log is a no-op.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.guard_errors import guard_error_envelope
from roam.guard_log import log_path_for
from roam.output.formatter import json_envelope, to_json


@click.command(name="guard-clean")
@click.option(
    "--keep", "keep", type=int, default=500, show_default=True, help="Number of most-recent log entries to retain."
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, default=False, help="Report what would be removed without modifying the log."
)
@click.pass_context
@roam_capability(
    name="guard-clean",
    category="planning",
    summary="Prune the verdict log to last N entries",
    inputs=("verdict_log",),
    outputs=("clean_report",),
    side_effect=True,  # atomic rewrite of .roam/verdict-log.jsonl
    examples=(
        "roam guard-clean",
        "roam guard-clean --keep 100",
        "roam guard-clean --dry-run",
    ),
    tags=("planning", "roam-guard", "log", "cleanup"),
)
def guard_clean(ctx: click.Context, keep: int, dry_run: bool) -> None:
    """Prune `.roam/verdict-log.jsonl` to its last N entries."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = Path(find_project_root() or Path.cwd())
    log = log_path_for(root)

    if keep < 0:
        msg = f"--keep must be >= 0, got {keep}"
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "guard-clean",
                        "invalid_argument",
                        msg,
                        context={"keep": keep},
                    )
                )
            )
        else:
            click.echo(msg, err=True)
        ctx.exit(2)
        return

    if not log.is_file():
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-clean",
                        summary={
                            "verdict": "no verdict log present",
                            "partial_success": False,
                            "kept": 0,
                            "removed": 0,
                            "dry_run": dry_run,
                        },
                        agent_contract={
                            "facts": ["0 log entries"],
                            "next_commands": ["roam guard-pr --dry-run"],
                            "risks": [],
                        },
                        log_path=str(log),
                    )
                )
            )
        else:
            click.echo(f"No verdict log at {log}. Nothing to clean.")
        return

    # Read every line as a raw record; preserve order for tail-trim.
    raw_lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    total = len(raw_lines)
    to_keep = raw_lines[-keep:] if keep > 0 else []
    removed = total - len(to_keep)

    summary = {
        "verdict": (f"would remove {removed} entries (dry-run)" if dry_run else f"removed {removed} entries"),
        "kept": len(to_keep),
        "removed": removed,
        "total_before": total,
        "dry_run": dry_run,
        "partial_success": False,
    }
    facts = [
        f"{total} entries scanned",
        f"{len(to_keep)} entries kept",
        f"{removed} entries removed",
    ]

    if removed == 0:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-clean",
                        summary=summary,
                        agent_contract={"facts": facts, "next_commands": [], "risks": []},
                        log_path=str(log),
                    )
                )
            )
        else:
            click.echo(f"Log already <= {keep} entries ({total}). Nothing to clean.")
        return

    if not dry_run:
        # Delegate to the public rotate_log helper (W26) — atomic rewrite,
        # never raises, same atomicity guarantees as before.
        from roam.guard_log import rotate_log

        rotate_log(root, keep)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-clean",
                    summary=summary,
                    agent_contract={
                        "facts": facts,
                        "next_commands": ["roam guard-history --limit 10"],
                        "risks": [],
                    },
                    log_path=str(log),
                )
            )
        )
        return

    prefix = "[dry-run] " if dry_run else ""
    click.echo(f"{prefix}{summary['verdict']}")
    click.echo(f"  log: {log}")
    click.echo(f"  before: {total} entries, kept: {len(to_keep)}, removed: {removed}")
