"""``roam recipes`` — list every ask recipe with intent + examples.

sugar over ``roam ask --list`` for discoverability. Useful as
the first thing an agent runs to see what natural-language tasks
``roam ask`` handles.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because recipes outputs are invocation-scoped ask-recipe
enumerations (metadata registry) — not per-location violations. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.ask.recipes import RECIPES
from roam.capability import roam_capability
from roam.output.formatter import render_catalog


@roam_capability(
    name="recipes",
    category="getting-started",
    summary="List every ``roam ask`` recipe with intent + example queries",
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
@click.pass_context
def recipes(ctx) -> None:
    """List every ``roam ask`` recipe with intent + example queries."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    items = [
        {
            "name": r.name,
            "intent": r.intent,
            "phase": r.phase,
            "examples": list(r.examples),
            "commands": [{"cmd": c[0], "args": list(c[1])} for c in r.commands],
        }
        for r in RECIPES
    ]
    items.sort(key=lambda x: x["name"])

    def _format_recipe_row(it: dict) -> str:
        return f"{it['name']:<26}  {it['phase'][:14]:<14}  {it['intent'][:60]}"

    click.echo(
        render_catalog(
            json_mode,
            "recipes",
            items,
            "recipes",
            f"{len(items)} ask recipe(s) in registry",
            [("Name", 26), ("Phase", 14), ("Intent", 50)],
            _format_recipe_row,
            footer='Run `roam ask "<query>"` to dispatch by intent.',
        )
    )
