"""roam capabilities — emit the capability registry as YAML / JSON.

Capability Registry — declarative manifest of public surface. Reads the in-process
``roam.capability.REGISTRY`` (populated by ``@roam_capability``
decorators on commands as they're imported) and writes a manifest
suitable for downstream consumers (Roam Review GitHub App, MCP server,
documentation generators).

Output formats: text (default), ``--json``, YAML.
SARIF is deliberately NOT emitted because cmd_capabilities is a
capability-registry manifest emitter — it dumps the catalog of
registered roam capabilities (name, category, MCP exposure, preset
membership) without per-location violations. The registry is
invocation-scoped metadata, not a detector result. See action.yml
_SUPPORTED_SARIF allowlist + W1214-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import REGISTRY, emit_yaml, roam_capability
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="capabilities",
    category="workflow",
    summary="Emit the capability registry — every command's machine-readable shape",
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

    \b
    Examples:
      roam capabilities --emit yaml
      roam capabilities --emit json
      roam capabilities --category review
      roam capabilities --ai-safe-only

    See also ``recipes`` (intent-classified workflow recipes),
    ``permit`` (allow-list a tool for an AI agent), and ``mcp-setup``
    (wire roam into MCP-aware clients).
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
        # LAW 4 (W17.3): give the bare "count" verdict a concrete-noun
        # anchor and add an actionable next step. Without this the
        # auto-derived facts read as just ``["count 10"]``.
        ai_safe_count = sum(1 for c in items if c.ai_safe)
        verdict = (
            f"{len(items)} registered capabilities"
            + (f" in category '{category}'" if category else "")
            + (f" ({ai_safe_count} AI-safe)" if not ai_safe_only else "")
        )
        # W21.7 LAW 4: the verdict already names both counts. Pin explicit
        # facts so the auto-derive doesn't bolt on a redundant
        # ``"count 10"`` fact alongside the concrete verdict. The
        # AI-safe-share fact terminates on the ``capabilities`` anchor
        # (in the LAW 4 noun set) so the runtime lint accepts it.
        explicit_facts = [verdict]
        if not ai_safe_only and len(items) > 0:
            explicit_facts.append(f"{ai_safe_count} of {len(items)} AI-safe capabilities")
        envelope = json_envelope(
            "capabilities",
            summary={
                "verdict": verdict,
                "count": len(items),
                "category_filter": category,
                "ai_safe_only": ai_safe_only,
            },
            agent_contract={"facts": explicit_facts},
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
    applied to every command, this becomes a sweep of
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
