"""``roam telemetry`` — surface the opt-in local telemetry ring buffer.

list slowest and most recent commands recorded under
``ROAM_TELEMETRY_LOCAL=1``. Strictly local; no network egress.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because telemetry outputs surface a state-mutating ring
buffer (opt-in local command-timing log) — its rows describe
roam's own command-invocation performance, not per-location code
violations in the indexed workspace. SARIF audiences scan for code
findings rather than tool-performance metrics. See ``cmd_mutate``
for the parallel state-mutating disclosure pattern (W1180) +
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

from datetime import datetime

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json
from roam.telemetry import _enabled, fetch_recent, fetch_top_slow


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


@roam_capability(
    name="telemetry",
    category="getting-started",
    summary="Show local telemetry: slowest commands + recent runs",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
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
