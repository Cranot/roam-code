"""roam ask — collapse the 143-command surface to one phrase.

Examples
--------

    roam ask "is it safe to delete UserSession"
    roam ask "where does login validate sessions"
    roam ask "split the auth refactor across 4 agents"
    roam ask --list
"""

from __future__ import annotations

import click

from roam.ask.classifier import classify
from roam.ask.recipes import RECIPES, by_name
from roam.ask.runner import run_recipe
from roam.output.formatter import json_envelope, to_json

_CONFIDENCE_THRESHOLD = 0.15


@click.command()
@click.argument("query", nargs=-1)
@click.option(
    "--list",
    "list_recipes",
    is_flag=True,
    help="List all recipes and exit. The lazygit-`?` moment.",
)
@click.option(
    "--explain",
    is_flag=True,
    help="Show which recipe matched and why before running it.",
)
@click.option(
    "--recipe",
    "recipe_override",
    type=str,
    default=None,
    help="Skip classification and run a specific recipe by name.",
)
@click.pass_context
def ask(ctx, query, list_recipes, explain, recipe_override):
    """Run the recipe that matches a free-form query.

    The recipe registry covers the most common workflows by composing
    existing commands (preflight, retrieve, critique, fleet, diagnose,
    trace, trends, hotspots, debt, taint, dead, coupling, etc.). Twelve
    recipes ship in v12.0; the full 22-recipe surface lands in v12.1.
    When the classifier is confident, ``roam ask`` just runs the matched
    recipe; when it's not, it shows the top-3 candidates so you can refine.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if list_recipes:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "ask",
                        summary={
                            "verdict": f"{len(RECIPES)} recipe(s) registered",
                            "recipe_count": len(RECIPES),
                        },
                        recipes=[
                            {
                                "name": r.name,
                                "intent": r.intent,
                                "examples": list(r.examples),
                                "commands": [{"cmd": c, "args": list(a)} for c, a in r.commands],
                            }
                            for r in RECIPES
                        ],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {len(RECIPES)} recipe(s) registered")
        click.echo()
        for r in RECIPES:
            click.echo(f"  {r.name}")
            click.echo(f"     intent: {r.intent}")
            if r.examples:
                click.echo(f"     example: {r.examples[0]}")
        click.echo()
        click.echo('Run `roam ask "<question>"` to dispatch.')
        return

    query_text = " ".join(query).strip()
    if not query_text and not recipe_override:
        # Lazygit `?` moment — no query, no recipe → show shortlist.
        click.echo("VERDICT: type a question or use --list")
        click.echo()
        for r in RECIPES[:3]:
            click.echo(f'  example: roam ask "{r.examples[0]}"')
        return

    if recipe_override:
        recipe = by_name(recipe_override)
        if recipe is None:
            raise click.UsageError(f"unknown recipe: {recipe_override!r}. See `roam ask --list` for available recipes.")
        ranked = [(recipe, 1.0)]
    else:
        ranked = classify(query_text)

    if not ranked or ranked[0][1] < _CONFIDENCE_THRESHOLD:
        # Low confidence — show top-3 and bail out.
        msg = "VERDICT: no confident recipe match"
        top = ranked[:3] if ranked else []
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "ask",
                        summary={
                            "verdict": msg,
                            "query": query_text,
                            "low_confidence": True,
                        },
                        candidates=[{"name": r.name, "score": round(s, 3), "intent": r.intent} for r, s in top],
                    )
                )
            )
            return
        click.echo(msg)
        if top:
            click.echo()
            click.echo("Closest matches (try `--recipe <name>` to force one):")
            for r, s in top:
                click.echo(f"  [{s:.2f}] {r.name} — {r.intent}")
        return

    chosen, score = ranked[0]
    if explain or not json_mode:
        click.echo(f"RECIPE: {chosen.name} (score {score:.2f})")
        click.echo(f"INTENT: {chosen.intent}")
        for c_name, c_args in chosen.commands:
            click.echo(f"  → roam {c_name} {' '.join(c_args)}")
        click.echo()

    results = run_recipe(chosen, query_text)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ask",
                    summary={
                        "verdict": (f"ran recipe '{chosen.name}' ({len(results)} step(s))"),
                        "recipe": chosen.name,
                        "confidence": round(score, 4),
                        "query": query_text,
                        "step_count": len(results),
                    },
                    intent=chosen.intent,
                    summary_hint=chosen.summary,
                    steps=results,
                )
            )
        )
        return

    for i, env in enumerate(results, 1):
        sub_summary = env.get("summary", {})
        verdict = sub_summary.get("verdict") if isinstance(sub_summary, dict) else None
        cmd_name = env.get("command", chosen.commands[i - 1][0] if i <= len(chosen.commands) else "?")
        if verdict:
            click.echo(f"[{i}/{len(results)}] {cmd_name}: {verdict}")
        elif "error" in env:
            click.echo(f"[{i}/{len(results)}] {cmd_name}: ERROR — {env['error']}")
        else:
            click.echo(f"[{i}/{len(results)}] {cmd_name}: ok")

    click.echo()
    click.echo(f"SUMMARY: {chosen.summary}")
