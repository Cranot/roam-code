"""``roam mcp-status`` — health check for the MCP server.

companion to ``roam doctor``. Reports:

  * preset (core / review / refactor / debug / architecture / full)
  * registered tool count + core tool count
  * backpressure limits (max_concurrent, in_flight, per-tool overrides)
  * MCP-level result cache size + hit-rate (if anything cached this session)
  * recent ``busy_responses_total`` count
  * watcher status (if ROAM_MCP_WATCH=1)

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam mcp-status`` is a setup/bootstrap command — its
output is human-facing setup status (MCP server preset, tool counts,
backpressure limits, cache stats), not analysis findings with
file:line coordinates. SARIF is reserved for scanning results. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1148 audit memo.
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="mcp-status",
    category="getting-started",
    summary="Report MCP server status: preset, tools, backpressure, cache, watcher",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
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
        # W1289 — canonical Pattern-1A failure envelope. The previous handler
        # produced a one-line "MCP server module unavailable: <ExcType>: <msg>"
        # that surfaced raw Python exception text (e.g. ``KeyError: 'symbol'``)
        # to agents without status/error_code/next_command fields. Replaced
        # with the documented index_not_built envelope shape (CLAUDE.md
        # §"Pattern-1 family — (A) Hang on missing prerequisite") so consumers
        # reading only ``summary.verdict`` get an imperative, executable next
        # action and JSON consumers get the full canonical structure.
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        verdict = "MCP server requires a built index — run `roam init`"
        envelope = json_envelope(
            "mcp-status",
            summary={
                "verdict": verdict,
                "level": "warning",
                "partial_success": False,
                "state": "not_initialized",
            },
            status="index_not_built",
            isError=True,
            error_code="INDEX_NOT_BUILT",
            error=f"MCP server module unavailable: {exc_type}: {exc_msg}",
            hint="Run `roam init` to bootstrap the index",
            next_command="roam init",
            agent_contract={
                "facts": [
                    "MCP server cannot start without .roam/index.db",
                    f"underlying error: {exc_type}",
                ],
                "next_commands": [
                    "roam init",
                    "# then retry roam mcp-status",
                ],
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo()
            click.echo(f"Underlying error: {exc_type}: {exc_msg}")
            click.echo("Hint:             Run `roam init` to bootstrap the index")
            click.echo("Next command:     roam init")
        return

    try:
        from roam.mcp_extras.concurrency import metrics as concurrency_metrics

        bp = concurrency_metrics()
    except (ImportError, ValueError):
        bp = {"max_concurrent": None, "in_flight": None, "busy_responses_total": None, "per_tool_limits": {}}

    cache_size = len(_ROAM_RESULT_CACHE)
    registered = len(_REGISTERED_TOOLS)
    core_count = sum(1 for n in _REGISTERED_TOOLS if n in _CORE_TOOLS)

    # W420 dual-source: the runtime ``_REGISTERED_TOOLS`` count above is
    # SEMANTICALLY CORRECT for "what's active under the current preset",
    # which is the whole point of ``roam mcp-status``. But surfacing only
    # the active number hides the ceiling — an operator on the ``core``
    # preset sees "57 tools registered" with no signal that widening to
    # ``full`` would unlock 227. Mirror the cmd_doctor:430 Option B pattern:
    # keep the live runtime numbers AND emit the AST-sourced ceiling
    # side-by-side so plugin-loading / preset-state drift is visible at a
    # glance. Additive only — existing ``tools_registered`` /
    # ``core_tool_count`` fields and the human text layout stay unchanged.
    tools_shipped_ceiling: int | None = None
    core_tools_shipped_ceiling: int | None = None
    try:
        from roam.surface_counts import mcp_surface_counts  # noqa: PLC0415

        _mcp_counts = mcp_surface_counts()
        tools_shipped_ceiling = int(_mcp_counts.get("registered_tools") or 0) or None
        core_tools_shipped_ceiling = int(_mcp_counts.get("core_tools") or 0) or None
    except Exception:  # noqa: BLE001 — AST helper unavailable, see fallback below
        # AST helper unavailable (minimal install / sdist edge case);
        # silently fall back to runtime-only — old behaviour preserved.
        pass

    if tools_shipped_ceiling and tools_shipped_ceiling != registered:
        verdict = (
            f"MCP ready — preset={preset}, {registered} active / "
            f"{tools_shipped_ceiling} shipped tools registered "
            f"({core_count} core), max_concurrent={bp.get('max_concurrent')}"
        )
    else:
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
                        # W420 dual-source: AST-sourced ceiling fields are
                        # additive — consumers pinning the older field
                        # names keep working unchanged.
                        "tools_shipped_ceiling": tools_shipped_ceiling,
                        "core_tools_shipped_ceiling": core_tools_shipped_ceiling,
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
    if tools_shipped_ceiling and tools_shipped_ceiling != registered:
        click.echo(f"Tools registered:          {registered} (of {tools_shipped_ceiling} shipped)")
    else:
        click.echo(f"Tools registered:          {registered}")
    if core_tools_shipped_ceiling and core_tools_shipped_ceiling != core_count:
        click.echo(f"  of which core preset:    {core_count} (of {core_tools_shipped_ceiling} shipped)")
    else:
        click.echo(f"  of which core preset:    {core_count}")
    click.echo(f"Max concurrent:            {bp.get('max_concurrent')}")
    click.echo(f"In flight (now):           {bp.get('in_flight')}")
    click.echo(f"Busy responses total:      {bp.get('busy_responses_total')}")
    click.echo(f"Per-tool override count:   {len(bp.get('per_tool_limits') or {})}")
    click.echo(f"Result-cache entries:      {cache_size}")
    click.echo(f"Watcher enabled:           {'yes' if watch_enabled else 'no'}")
