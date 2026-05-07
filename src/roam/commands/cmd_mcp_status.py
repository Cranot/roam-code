"""``roam mcp-status`` — health check for the MCP server.

companion to ``roam doctor``. Reports:

  * preset (core / review / refactor / debug / architecture / full)
  * registered tool count + core tool count
  * backpressure limits (max_concurrent, in_flight, per-tool overrides)
  * MCP-level result cache size + hit-rate (if anything cached this session)
  * recent ``busy_responses_total`` count
  * watcher status (if ROAM_MCP_WATCH=1)
"""

from __future__ import annotations

import os

import click

from roam.output.formatter import json_envelope, to_json


@click.command(name="mcp-status")
@click.pass_context
def mcp_status(ctx) -> None:
    """Report MCP server status: preset, tools, backpressure, cache, watcher."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    preset = os.environ.get("ROAM_MCP_PRESET", "core")
    watch_enabled = os.environ.get("ROAM_MCP_WATCH", "").strip() in {"1", "true", "yes"}

    try:
        from roam.mcp_server import _CORE_TOOLS, _REGISTERED_TOOLS, _ROAM_RESULT_CACHE
    except Exception as exc:
        msg = f"MCP server module unavailable: {type(exc).__name__}: {exc}"
        if json_mode:
            click.echo(to_json(json_envelope("mcp-status", summary={"verdict": msg, "ready": False})))
        else:
            click.echo(f"VERDICT: {msg}")
        return

    try:
        from roam.mcp_extras.concurrency import metrics as concurrency_metrics

        bp = concurrency_metrics()
    except Exception:
        bp = {"max_concurrent": None, "in_flight": None, "busy_responses_total": None, "per_tool_limits": {}}

    cache_size = len(_ROAM_RESULT_CACHE)
    registered = len(_REGISTERED_TOOLS)
    core_count = sum(1 for n in _REGISTERED_TOOLS if n in _CORE_TOOLS)

    verdict = (
        f"MCP ready — preset={preset}, {registered} tools registered "
        f"({core_count} core), max_concurrent={bp.get('max_concurrent')}"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "mcp-status",
                    summary={
                        "verdict": verdict,
                        "preset": preset,
                        "tools_registered": registered,
                        "core_tool_count": core_count,
                        "max_concurrent": bp.get("max_concurrent"),
                        "in_flight": bp.get("in_flight"),
                        "busy_responses_total": bp.get("busy_responses_total"),
                        "cache_entries": cache_size,
                        "watcher_enabled": watch_enabled,
                    },
                    backpressure=bp,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Preset:                    {preset}")
    click.echo(f"Tools registered:          {registered}")
    click.echo(f"  of which core preset:    {core_count}")
    click.echo(f"Max concurrent:            {bp.get('max_concurrent')}")
    click.echo(f"In flight (now):           {bp.get('in_flight')}")
    click.echo(f"Busy responses total:      {bp.get('busy_responses_total')}")
    click.echo(f"Per-tool override count:   {len(bp.get('per_tool_limits') or {})}")
    click.echo(f"Result-cache entries:      {cache_size}")
    click.echo(f"Watcher enabled:           {'yes' if watch_enabled else 'no'}")
