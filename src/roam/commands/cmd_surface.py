"""roam surface — canonical capability registry as JSON or text.

Single source of truth for: commands, aliases, MCP tools, presets,
categories, maturity, deprecation. Used by docs generation, contract
tests, release notes, and the marketing/landscape surfaces.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# Command maturity overlay. Default: "stable". Override only the exceptions.
# stable: production, supported, contract tests pass.
# experimental: usable but interface may change; not in core MCP preset.
# internal: not meant for end users (debug, plumbing).
# deprecated: still works, replaced — see _DEPRECATED_COMMANDS in cli.py.
_MATURITY: dict[str, str] = {
    "adversarial": "experimental",
    "fingerprint": "experimental",
    "simulate": "experimental",
    "mutate": "experimental",
    "intent": "experimental",
    "closure": "experimental",
    "dark-matter": "experimental",
    "spectral": "experimental",
    "forecast": "experimental",
    "bisect": "experimental",
    "orchestrate": "experimental",
    "partition": "experimental",
    "fleet": "experimental",
    "vibe-check": "experimental",
    "weather": "experimental",
    "doctor": "stable",
    "dogfood": "internal",
    "telemetry": "internal",
    "schema": "internal",
    "ws": "internal",
    "stats": "internal",
    "index-stats": "internal",
}


def _build_surface() -> dict:
    """Build the canonical surface manifest from cli.py + mcp_server.py."""
    from roam.cli import _CATEGORIES, _COMMANDS

    # Reverse-map: command name -> category
    name_to_category: dict[str, str] = {}
    for category, names in _CATEGORIES.items():
        for n in names:
            name_to_category.setdefault(n, category)

    # Collect aliases: same (module, function) tuple under different names.
    target_to_names: dict[tuple, list[str]] = {}
    for name, target in _COMMANDS.items():
        target_to_names.setdefault(target, []).append(name)

    # MCP-exposed tools (read from mcp_server registry).
    # ``_TOOL_METADATA`` is populated unconditionally by every ``@_tool(...)``
    # decorator (the decorator writes metadata BEFORE the fastmcp-presence
    # check), so the count of *defined* tools is env-independent and equals
    # the AST-derived ground truth in ``roam.surface_counts``. Distinguish
    # that from ``mcp_introspection_available``, which now means "the
    # FastMCP transport is importable and could actually serve these tools
    # at runtime" — two different signals on two different axes.
    mcp_tools: list[str] = []
    mcp_introspection_available = False
    preset_counts: dict[str, int] = {}
    try:
        from roam.mcp_server import FastMCP as _FastMCP
        from roam.mcp_server import _PRESETS, _TOOL_METADATA

        mcp_introspection_available = _FastMCP is not None
        mcp_tools = list(_TOOL_METADATA.keys())
        total = len(_TOOL_METADATA)
        # "full" preset is the empty-set sentinel meaning "no filter / all tools".
        # Resolve it to the actual total so consumers don't see a misleading 0.
        for preset_name, members in _PRESETS.items():
            preset_counts[preset_name] = len(members) if members else total
    except Exception:
        # MCP server module failed to import entirely — leave both signals as the
        # "unavailable / empty" baseline set above.
        pass

    from roam.cli import _deprecation_record

    commands = []
    for name in sorted(_COMMANDS):
        target = _COMMANDS[name]
        aliases = sorted(n for n in target_to_names[target] if n != name)
        deprecation = _deprecation_record(name)
        commands.append(
            {
                "name": name,
                "module": target[0],
                "function": target[1],
                "category": name_to_category.get(name, "Uncategorized"),
                "maturity": "deprecated" if deprecation else _MATURITY.get(name, "stable"),
                "aliases": aliases,
                "deprecated_replacement": deprecation.get("replacement") if deprecation else None,
                "deprecation_reason": deprecation.get("reason") if deprecation else None,
                "deprecation_removal_version": deprecation.get("removal_version") if deprecation else None,
                "mcp_exposed": name.replace("-", "_") in mcp_tools,
            }
        )

    by_maturity: dict[str, int] = {}
    for c in commands:
        by_maturity[c["maturity"]] = by_maturity.get(c["maturity"], 0) + 1

    result = {
        "command_count": len(_COMMANDS),
        "canonical_count": len({tuple(t) for t in _COMMANDS.values()}),
        "category_count": len(_CATEGORIES),
        "mcp_tool_count": len(mcp_tools),
        "mcp_tool_count_by_preset": preset_counts,
        "mcp_introspection_available": mcp_introspection_available,
        "by_maturity": by_maturity,
        "categories": list(_CATEGORIES.keys()),
        "commands": commands,
        "mcp_tools": sorted(mcp_tools),
    }
    # Distinct from the count itself: the count is the number of *defined*
    # tools (env-independent); the note flags that the transport layer is
    # absent and these tools can't actually be served until ``fastmcp`` is
    # installed.
    if not mcp_introspection_available:
        result["mcp_tools_note"] = (
            "fastmcp not installed; install with: pip install 'roam-code[mcp]'"
        )
    return result


@roam_capability(
    name="surface",
    category="getting-started",
    summary="Print the canonical capability surface (commands, aliases, MCP tools, maturity)",
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
@click.command("surface")
@click.option(
    "--filter",
    "filter_by",
    type=click.Choice(["all", "stable", "experimental", "internal", "deprecated"]),
    default="all",
    help="Filter commands by maturity level.",
)
@click.option(
    "--category",
    default=None,
    help="Filter to a specific category (e.g. 'Daily Workflow').",
)
@click.pass_context
def surface(ctx, filter_by: str, category: str | None):
    """Print the canonical capability surface (commands, aliases, MCP tools, maturity).

    \b
    Examples:
      roam surface
      roam surface --filter stable
      roam surface --category "Daily Workflow"
      roam --json surface > surface.json

    See also ``recipes`` (curated multi-command workflows),
    ``help-search`` (search command help text), and
    ``explain-command`` (per-command summary card).
    """
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    data = _build_surface()

    if filter_by != "all":
        data["commands"] = [c for c in data["commands"] if c["maturity"] == filter_by]
    if category:
        data["commands"] = [c for c in data["commands"] if c["category"] == category]

    if json_mode:
        summary = {
            "verdict": "OK",
            "command_count": data["command_count"],
            "canonical_count": data["canonical_count"],
            "category_count": data["category_count"],
            "mcp_tool_count": data["mcp_tool_count"],
            "mcp_tool_count_by_preset": data["mcp_tool_count_by_preset"],
            "mcp_introspection_available": data["mcp_introspection_available"],
            "by_maturity": data["by_maturity"],
        }
        if "mcp_tools_note" in data:
            summary["mcp_tools_note"] = data["mcp_tools_note"]
        click.echo(
            to_json(
                json_envelope(
                    "surface",
                    summary=summary,
                    categories=data["categories"],
                    commands=data["commands"],
                    mcp_tools=data["mcp_tools"],
                )
            )
        )
        return

    click.echo(f"VERDICT: OK  ({data['command_count']} commands, {data['mcp_tool_count']} MCP tools)")
    click.echo("")
    click.echo(f"  canonical commands: {data['canonical_count']}")
    click.echo(f"  aliases:            {data['command_count'] - data['canonical_count']}")
    click.echo(f"  categories:         {data['category_count']}")
    if data["mcp_introspection_available"]:
        click.echo(f"  mcp tools:          {data['mcp_tool_count']}")
    else:
        click.echo(f"  mcp tools:          {data['mcp_tool_count']} (introspection unavailable; install 'roam-code[mcp]')")
    click.echo("")
    click.echo("  by maturity:")
    for level in ("stable", "experimental", "internal", "deprecated"):
        n = data["by_maturity"].get(level, 0)
        if n:
            click.echo(f"    {level:14s} {n}")
    if filter_by != "all" or category:
        click.echo("")
        click.echo(f"matching commands ({len(data['commands'])}):")
        for c in data["commands"]:
            extra = []
            if c["aliases"]:
                extra.append("aliases: " + ", ".join(c["aliases"]))
            if c["deprecated_replacement"]:
                extra.append(f"-> {c['deprecated_replacement']}")
            tail = " (" + " · ".join(extra) + ")" if extra else ""
            click.echo(f"  {c['name']:30s} [{c['maturity']:13s}] {c['category']}{tail}")
