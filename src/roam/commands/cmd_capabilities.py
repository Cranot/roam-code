"""roam capabilities — emit the capability registry as YAML / JSON.

redacted. Reads the in-process
``roam.capability.REGISTRY`` (populated by ``@roam_capability``
decorators on commands as they're imported) and writes a manifest
suitable for downstream consumers (Roam Review GitHub App, MCP server,
documentation generators).
"""

from __future__ import annotations

import json

import click

from roam.capability import REGISTRY, emit_yaml
from roam.output.formatter import json_envelope, to_json


@click.command()
@click.option(
    "--emit",
    type=click.Choice(["yaml", "json", "text"]),
    default="text",
    show_default=True,
    help="Output format. yaml/json for machine consumption, text for human reading.",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Filter to one category (e.g. review, refactor, architecture).",
)
@click.option(
    "--ai-safe-only",
    is_flag=True,
    help="Show only capabilities marked ai_safe=True (safe for an AI agent to call without human approval).",
)
@click.pass_context
def capabilities_cmd(ctx, emit: str, category: str | None, ai_safe_only: bool) -> None:
    """Emit the capability registry — every command's machine-readable shape.

    The manifest is what the Roam Review GitHub App reads to decide which
    commands an AI agent can call without human approval, and what tools
    the MCP server should expose by default.

    Imports trigger registration. Run ``roam capabilities --emit yaml``
    to produce a stable manifest after every release.
    """
    # Force-load known command modules so the registry is populated.
    _populate_registry()

    items = REGISTRY.all()
    if category:
        items = [c for c in items if c.category == category]
    if ai_safe_only:
        items = [c for c in items if c.ai_safe]

    json_mode = ctx.obj.get("json") if ctx.obj else False

    if emit == "yaml":
        click.echo(emit_yaml())
        return
    if emit == "json" or json_mode:
        envelope = json_envelope(
            "capabilities",
            summary={
                "count": len(items),
                "category_filter": category,
                "ai_safe_only": ai_safe_only,
            },
            capabilities=[
                {
                    "name": c.name,
                    "category": c.category,
                    "summary": c.summary,
                    "inputs": list(c.inputs),
                    "outputs": list(c.outputs),
                    "ai_safe": c.ai_safe,
                    "since": c.since,
                }
                for c in items
            ],
        )
        click.echo(to_json(envelope))
        return

    # Text output
    if not items:
        click.echo("No registered capabilities (registry empty).")
        return
    click.echo(f"{len(items)} registered capabilities:")
    last_cat = None
    for c in items:
        if c.category != last_cat:
            click.echo(f"\n  [{c.category}]")
            last_cat = c.category
        flags = []
        if c.ai_safe:
            flags.append("ai-safe")
        if c.deprecated:
            flags.append("deprecated")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        click.echo(f"    {c.name:30}  {c.summary}{flag_str}")


def _populate_registry() -> None:
    """Import known capability-decorated command modules to populate the registry.

    Today this lists commands explicitly. Once the decorator has been
    applied to all 190 commands, this becomes a sweep of
    ``roam.commands.cmd_*``.
    """
    import importlib

    decorated_modules = [
        "roam.commands.cmd_critique",
        "roam.commands.cmd_preflight",
        "roam.commands.cmd_understand",
        "roam.commands.cmd_permit",
        "roam.commands.cmd_postmortem",
        "roam.commands.cmd_article_12_check",
    ]
    for mod in decorated_modules:
        try:
            importlib.import_module(mod)
        except Exception:
            # Best-effort; if a command module fails to import, skip it
            pass
