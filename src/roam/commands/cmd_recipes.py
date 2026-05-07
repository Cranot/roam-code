"""``roam recipes`` — list every ask recipe with intent + examples.

sugar over ``roam ask --list`` for discoverability. Useful as
the first thing an agent runs to see what natural-language tasks
``roam ask`` handles.
"""

from __future__ import annotations

import click

from roam.ask.recipes import RECIPES
from roam.output.formatter import json_envelope, to_json


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
    verdict = f"{len(items)} ask recipe(s) in registry"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "recipes",
                    summary={"verdict": verdict, "count": len(items)},
                    recipes=items,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"{'Name':<26}  {'Phase':<14}  Intent")
    click.echo(f"{'-' * 26}  {'-' * 14}  {'-' * 50}")
    for it in items:
        click.echo(f"{it['name']:<26}  {it['phase'][:14]:<14}  {it['intent'][:60]}")
    click.echo()
    click.echo('Run `roam ask "<query>"` to dispatch by intent.')
