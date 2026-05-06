"""``roam suppress`` — record an audit-trail-friendly finding suppression.

Use after manually verifying a finding is a false positive. Records the
suppression in ``.roam/suppressions.json`` keyed by the finding ID
(stable hash of task_id + location + symbol_name). Future detector
runs flag the matched finding with ``suppressed: {source: …, reason: …}``
instead of dropping it silently — so a future code change that
invalidates the suppression still surfaces in JSON output.

Companion paths to per-file inline annotations + ``.roamignore-findings``
(see :mod:`roam.commands.finding_suppress`).
"""

from __future__ import annotations

import datetime as _dt
import json as _json
from pathlib import Path

import click

from roam.commands.finding_suppress import DEFAULT_SUPPRESSIONS_PATH
from roam.output.formatter import json_envelope, to_json


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@click.command(name="suppress")
@click.argument("finding_id", required=True)
@click.option(
    "--reason",
    default=None,
    help="Why this is a false positive (kept for audit). Required for `add`.",
)
@click.option(
    "--remove",
    is_flag=True,
    help="Remove an existing suppression instead of adding one.",
)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    help="List all current suppressions (ignores FINDING_ID; pass `_` as a placeholder).",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(),
    default=None,
    help=f"Suppressions file path (default: {DEFAULT_SUPPRESSIONS_PATH}).",
)
@click.pass_context
def suppress(
    ctx,
    finding_id: str,
    reason: str | None,
    remove: bool,
    list_only: bool,
    input_path: str | None,
) -> None:
    """Suppress a math / over-fetch / missing-index / auth-gaps finding.

    \b
    Examples:
      roam suppress a1b2c3d4e5f60718 --reason "depth-limited; verified"
      roam suppress a1b2c3d4e5f60718 --remove
      roam suppress _ --list
      roam --json suppress _ --list   # JSON envelope for scripts

    Get the finding ID from `roam --json math` (each finding carries a
    `finding_id` field added by the suppression layer).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    path = Path(input_path) if input_path else DEFAULT_SUPPRESSIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing
    if path.exists():
        try:
            current: dict = _json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (OSError, _json.JSONDecodeError):
            current = {}
    else:
        current = {}

    if list_only:
        summary = {
            "verdict": f"{len(current)} suppression(s)",
            "count": len(current),
            "path": str(path),
        }
        if json_mode:
            click.echo(to_json(json_envelope("suppress", summary=summary, suppressions=current)))
        else:
            click.echo(f"VERDICT: {summary['verdict']}")
            click.echo(f"  path: {path}")
            for fid, entry in sorted(current.items()):
                click.echo(f"  {fid}  {entry.get('reason', '<no reason>')[:80]}")
        return

    if remove:
        existed = current.pop(finding_id, None)
        path.write_text(_json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        verdict = f"removed {finding_id}" if existed else f"no-op: {finding_id} not found"
        if json_mode:
            click.echo(to_json(json_envelope("suppress", summary={"verdict": verdict, "removed": bool(existed)})))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    # Add path
    if not reason:
        ctx.fail("--reason is required when adding a suppression (use `roam suppress _ --list` to view existing)")
    current[finding_id] = {
        "reason": reason,
        "added_at": _utc_now_iso(),
    }
    path.write_text(_json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    verdict = f"suppressed {finding_id}"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "suppress",
                    summary={"verdict": verdict, "finding_id": finding_id},
                    entry=current[finding_id],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  reason: {reason}")
        click.echo(f"  saved:  {path}")
