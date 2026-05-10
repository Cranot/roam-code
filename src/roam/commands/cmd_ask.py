"""roam ask — collapse the command surface to one phrase.

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
from roam.ask.recipes import RECIPES, Recipe, by_name
from roam.ask.runner import extract_symbol, fill_followups, run_recipe
from roam.output.confidence import DEFAULT_CONFIDENCE_THRESHOLD, is_low_confidence
from roam.output.formatter import json_envelope, to_json

# Single source of truth for the low-confidence threshold across
# ranked-output commands. Imported from output.confidence so cmd_ask
# and any future ranker stay in lockstep.
_CONFIDENCE_THRESHOLD = DEFAULT_CONFIDENCE_THRESHOLD


def _recipe_listing_payload(recipe: Recipe) -> dict:
    return {
        "name": recipe.name,
        "intent": recipe.intent,
        "phase": recipe.phase,
        "perspectives": list(recipe.perspectives),
        "followups": list(recipe.followups),
        "gates": list(recipe.gates),
        "examples": list(recipe.examples),
        "commands": [{"cmd": c, "args": list(a)} for c, a in recipe.commands],
    }


def _emit_recipe_list(json_mode: bool) -> None:
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ask",
                    summary={
                        "verdict": f"{len(RECIPES)} recipe(s) registered",
                        "recipe_count": len(RECIPES),
                    },
                    recipes=[_recipe_listing_payload(r) for r in RECIPES],
                )
            )
        )
        return

    click.echo(f"VERDICT: {len(RECIPES)} recipe(s) registered")
    click.echo()
    for recipe in RECIPES:
        click.echo(f"  {recipe.name}")
        click.echo(f"     intent: {recipe.intent}")
        if recipe.phase:
            click.echo(f"     phase: {recipe.phase}")
        if recipe.perspectives:
            click.echo(f"     perspectives: {', '.join(recipe.perspectives)}")
        if recipe.gates:
            click.echo(f"     gate: {recipe.gates[0]}")
        if recipe.examples:
            click.echo(f"     example: {recipe.examples[0]}")
    click.echo()
    click.echo('Run `roam ask "<question>"` to dispatch.')


def _emit_no_query() -> None:
    click.echo("VERDICT: type a question or use --list")
    click.echo()
    for recipe in RECIPES[:3]:
        click.echo(f'  example: roam ask "{recipe.examples[0]}"')


def _rank_recipes(query_text: str, recipe_override: str | None) -> list[tuple[Recipe, float]]:
    if not recipe_override:
        return classify(query_text)
    recipe = by_name(recipe_override)
    if recipe is None:
        from roam.output.errors import UNKNOWN_RECIPE, structured_usage_error

        raise structured_usage_error(
            UNKNOWN_RECIPE,
            f"unknown recipe: {recipe_override!r}. See `roam ask --list` for available recipes.",
        )
    return [(recipe, 1.0)]


def _emit_low_confidence(query_text: str, ranked: list[tuple[Recipe, float]], json_mode: bool) -> None:
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
                    candidates=[
                        {
                            "name": recipe.name,
                            "score": round(score, 3),
                            "intent": recipe.intent,
                            "phase": recipe.phase,
                            "perspectives": list(recipe.perspectives),
                            "gates": list(recipe.gates),
                        }
                        for recipe, score in top
                    ],
                )
            )
        )
        return

    click.echo(msg)
    if top:
        click.echo()
        click.echo("Closest matches (try `--recipe <name>` to force one):")
        for recipe, score in top:
            click.echo(f"  [{score:.2f}] {recipe.name} — {recipe.intent}")
            if recipe.perspectives:
                click.echo(f"       lenses: {', '.join(recipe.perspectives)}")


def _emit_explain(recipe: Recipe, score: float) -> None:
    click.echo(f"RECIPE: {recipe.name} (score {score:.2f})")
    click.echo(f"INTENT: {recipe.intent}")
    if recipe.phase:
        click.echo(f"PHASE: {recipe.phase}")
    if recipe.perspectives:
        click.echo(f"PERSPECTIVES: {', '.join(recipe.perspectives)}")
    if recipe.gates:
        click.echo(f"GATES: {'; '.join(recipe.gates)}")
    for c_name, c_args in recipe.commands:
        click.echo(f"  → roam {c_name} {' '.join(c_args)}")
    click.echo()


def _emit_json_result(
    recipe: Recipe,
    score: float,
    query_text: str,
    results: list[dict],
    rendered_followups: list[str],
) -> None:
    click.echo(
        to_json(
            json_envelope(
                "ask",
                summary={
                    "verdict": (f"ran recipe '{recipe.name}' ({len(results)} step(s))"),
                    "recipe": recipe.name,
                    "confidence": round(score, 4),
                    "query": query_text,
                    "step_count": len(results),
                },
                intent=recipe.intent,
                phase=recipe.phase,
                perspectives=list(recipe.perspectives),
                gates=list(recipe.gates),
                followups=rendered_followups,
                summary_hint=recipe.summary,
                steps=results,
            )
        )
    )


def _emit_text_result(recipe: Recipe, results: list[dict], rendered_followups: list[str]) -> None:
    for i, env in enumerate(results, 1):
        sub_summary = env.get("summary", {})
        verdict = sub_summary.get("verdict") if isinstance(sub_summary, dict) else None
        cmd_name = env.get("command", recipe.commands[i - 1][0] if i <= len(recipe.commands) else "?")
        if verdict:
            click.echo(f"[{i}/{len(results)}] {cmd_name}: {verdict}")
        elif "error" in env:
            click.echo(f"[{i}/{len(results)}] {cmd_name}: ERROR — {env['error']}")
        else:
            click.echo(f"[{i}/{len(results)}] {cmd_name}: ok")

    click.echo()
    click.echo(f"SUMMARY: {recipe.summary}")
    if rendered_followups:
        click.echo("NEXT:")
        for item in rendered_followups:
            click.echo(f"  {item}")


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
    trace, trends, hotspots, debt, taint, dead, coupling, etc.). Each recipe
    also carries workflow metadata: phase, review perspectives, and next
    actions. When the classifier is confident, ``roam ask`` runs the matched
    recipe; when it's not, it shows the top-3 candidates so you can refine.

    \b
    Examples:
      roam ask "what is the most coupled module?"
      roam ask --list                       # show recipe registry
      roam ask "trace login flow" --explain # show classifier reasoning
      roam ask "n+1" --recipe diagnose-n1   # force a specific recipe

    See also ``retrieve`` (graph-aware code-span retrieval), ``understand``
    (broad orientation), and ``diagnose`` (root-cause for a known symbol).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if list_recipes:
        _emit_recipe_list(json_mode)
        return

    query_text = " ".join(query).strip()
    if not query_text and not recipe_override:
        # Lazygit `?` moment — no query, no recipe → show shortlist.
        _emit_no_query()
        return

    ranked = _rank_recipes(query_text, recipe_override)

    top_score = ranked[0][1] if ranked else 0.0
    if not ranked or is_low_confidence(top_score, _CONFIDENCE_THRESHOLD):
        # Low confidence — show top-3 and bail out.
        _emit_low_confidence(query_text, ranked, json_mode)
        return

    chosen, score = ranked[0]
    if explain or not json_mode:
        _emit_explain(chosen, score)

    results = run_recipe(chosen, query_text)
    rendered_followups = fill_followups(chosen.followups, query_text, extract_symbol(query_text))

    if json_mode:
        _emit_json_result(chosen, score, query_text, results, rendered_followups)
        return

    _emit_text_result(chosen, results, rendered_followups)
