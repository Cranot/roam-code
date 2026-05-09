"""roam surface — canonical capability registry as JSON or text.

Single source of truth for: commands, aliases, MCP tools, presets,
categories, maturity, deprecation. Used by docs generation, contract
tests, release notes, and the marketing/landscape surfaces.
"""

from __future__ import annotations

import click

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
    mcp_tools: list[str] = []
    try:
        from roam.mcp_server import _REGISTERED_TOOLS

        mcp_tools = list(_REGISTERED_TOOLS)
    except Exception:
        # FastMCP not installed — surface still works without MCP introspection.
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

    return {
        "command_count": len(_COMMANDS),
        "canonical_count": len({tuple(t) for t in _COMMANDS.values()}),
        "category_count": len(_CATEGORIES),
        "mcp_tool_count": len(mcp_tools),
        "by_maturity": by_maturity,
        "categories": list(_CATEGORIES.keys()),
        "commands": commands,
        "mcp_tools": sorted(mcp_tools),
    }


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
    """Print the canonical capability surface (commands, aliases, MCP tools, maturity)."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    data = _build_surface()

    if filter_by != "all":
        data["commands"] = [c for c in data["commands"] if c["maturity"] == filter_by]
    if category:
        data["commands"] = [c for c in data["commands"] if c["category"] == category]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "surface",
                    summary={
                        "verdict": "OK",
                        "command_count": data["command_count"],
                        "canonical_count": data["canonical_count"],
                        "category_count": data["category_count"],
                        "mcp_tool_count": data["mcp_tool_count"],
                        "by_maturity": data["by_maturity"],
                    },
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
    click.echo(f"  mcp tools:          {data['mcp_tool_count']}")
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
