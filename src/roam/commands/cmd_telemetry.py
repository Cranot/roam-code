"""``roam telemetry`` — surface the opt-in local telemetry ring buffer.

list slowest and most recent commands recorded under
``ROAM_TELEMETRY_LOCAL=1``. Strictly local; no network egress.
"""

from __future__ import annotations

from datetime import datetime

import click

from roam.output.formatter import json_envelope, to_json
from roam.telemetry import _enabled, fetch_recent, fetch_top_slow


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "?"


@click.command()
@click.option("--top", "top_n", type=int, default=10, show_default=True, help="Number of slowest calls to show.")
@click.option("--recent", "recent_n", type=int, default=20, show_default=True, help="Number of recent calls to show.")
@click.pass_context
def telemetry(ctx, top_n, recent_n) -> None:
    """Show local telemetry: slowest commands + recent runs."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    enabled = _enabled()
    slow = fetch_top_slow(limit=top_n) if enabled else []
    recent = fetch_recent(limit=recent_n) if enabled else []
    verdict = (
        "telemetry disabled — set ROAM_TELEMETRY_LOCAL=1 to opt in"
        if not enabled
        else f"{len(slow)} slow / {len(recent)} recent calls in ring buffer"
    )
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "telemetry",
                    summary={"verdict": verdict, "enabled": enabled},
                    slow=slow,
                    recent=recent,
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
    if not enabled:
        click.echo()
        click.echo("Set ROAM_TELEMETRY_LOCAL=1 in your shell to start recording.")
        click.echo("Telemetry data lives in `.roam/telemetry.db` and never leaves your machine.")
        return
    if slow:
        click.echo()
        click.echo(f"=== Slowest {len(slow)} calls ===")
        click.echo(f"{'Duration (ms)':>13}  {'Exit':>4}  {'When':<20}  Command")
        click.echo(f"{'-' * 13}  {'-' * 4}  {'-' * 20}  {'-' * 30}")
        for s in slow:
            click.echo(f"{s['duration_ms']:>13}  {s['exit_code']:>4}  {_fmt_ts(s['ts']):<20}  {s['command']}")
    if recent:
        click.echo()
        click.echo(f"=== Recent {len(recent)} calls ===")
        click.echo(f"{'When':<20}  {'Duration (ms)':>13}  {'Exit':>4}  Command")
        click.echo(f"{'-' * 20}  {'-' * 13}  {'-' * 4}  {'-' * 30}")
        for r in recent:
            click.echo(f"{_fmt_ts(r['ts']):<20}  {r['duration_ms']:>13}  {r['exit_code']:>4}  {r['command']}")
