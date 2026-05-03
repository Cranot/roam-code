"""Inspect ask workflow recipes without running their command steps."""

from __future__ import annotations

import click

from roam.ask.recipes import RECIPES, Recipe, by_name
from roam.ask.runner import extract_symbol, fill_args
from roam.ask.workflow import recipe_workflow_metadata
from roam.output.formatter import json_envelope, to_json


def _recipe_payload(recipe: Recipe, query: str = "") -> dict:
    symbol = extract_symbol(query)
    return {
        **recipe_workflow_metadata(recipe, query=query, render_followups=bool(query)),
        "examples": list(recipe.examples),
        "commands": [
            {
                "cmd": cmd,
                "args": fill_args(args, query, symbol) if query else list(args),
            }
            for cmd, args in recipe.commands
        ],
    }


def _emit_recipe_list(json_mode: bool) -> None:
    payloads = [_recipe_payload(recipe) for recipe in RECIPES]
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "workflow",
                    summary={
                        "verdict": f"{len(payloads)} workflow recipe(s) available",
                        "recipe_count": len(payloads),
                    },
                    recipes=payloads,
                )
            )
        )
        return

    click.echo(f"VERDICT: {len(payloads)} workflow recipe(s) available")
    click.echo()
    for item in payloads:
        click.echo(f"  {item['recipe']:<20s} {item['phase']}")
        click.echo(f"     lenses: {', '.join(item['perspectives'])}")
    click.echo()
    click.echo("Run `roam workflow <recipe>` to inspect a recipe.")


def _emit_recipe_detail(recipe: Recipe, query: str, json_mode: bool) -> None:
    payload = _recipe_payload(recipe, query)
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "workflow",
                    summary={
                        "verdict": f"workflow recipe '{recipe.name}'",
                        "recipe": recipe.name,
                        "phase": recipe.phase,
                    },
                    **payload,
                )
            )
        )
        return

    click.echo(f"VERDICT: workflow recipe '{recipe.name}'")
    click.echo(f"PHASE: {recipe.phase}")
    click.echo(f"INTENT: {recipe.intent}")
    click.echo(f"PERSPECTIVES: {', '.join(recipe.perspectives)}")
    if recipe.gates:
        click.echo(f"GATES: {'; '.join(recipe.gates)}")
    click.echo()
    click.echo("COMMANDS:")
    for item in payload["commands"]:
        click.echo(f"  roam {item['cmd']} {' '.join(item['args'])}".rstrip())
    if payload["followups"]:
        click.echo()
        click.echo("NEXT:")
        for item in payload["followups"]:
            click.echo(f"  {item}")


@click.command("workflow")
@click.argument("recipe_name", required=False)
@click.option("--list", "list_recipes", is_flag=True, help="List available workflow recipes.")
@click.option(
    "--query",
    default="",
    help="Render {symbol}/{task} placeholders using this query without running commands.",
)
@click.pass_context
def workflow(ctx, recipe_name, list_recipes, query):
    """Inspect a workflow recipe DAG, review lenses, and next commands."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if list_recipes or not recipe_name:
        _emit_recipe_list(json_mode)
        return

    recipe = by_name(recipe_name)
    if recipe is None:
        raise click.UsageError(f"unknown workflow recipe: {recipe_name!r}. Run `roam workflow --list`.")

    _emit_recipe_detail(recipe, query, json_mode)
