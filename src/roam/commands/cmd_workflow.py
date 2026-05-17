"""Inspect ask workflow recipes without running their command steps.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cmd_workflow is a recipe-composer / inspector (lists
the ask-workflow recipes and their command-step DAG without executing
them). When a workflow IS executed via ``roam ask``, the composed
sub-commands emit their own ``--sarif`` when applicable; cmd_workflow
itself returns an invocation-scoped recipe-metadata enumeration —
not per-location violations. See ``cmd_report`` for the parallel
composer disclosure pattern (W1221) + action.yml _SUPPORTED_SARIF
allowlist + W1145 / W1085 composer audit + W1224-audit memo.
"""

from __future__ import annotations

import click

from roam.ask.recipes import RECIPES, Recipe, by_name
from roam.ask.runner import extract_symbol, fill_args
from roam.ask.workflow import recipe_workflow_metadata
from roam.capability import roam_capability
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


_NEXT_HINTS: dict[str, list[str]] = {
    "preflight": ["roam context <symbol>", "roam impact <symbol>", "roam diff"],
    "context": ["roam preflight <symbol>", "roam diff"],
    "diff": ["git diff | roam critique", "roam pr-prep", "roam pr-risk"],
    "critique": ["roam pr-prep", "roam diff"],
    "pr-risk": ["roam pr-prep", "roam fitness"],
    "health": ["roam debt", "roam complexity", "roam hotspots --danger"],
    "search": ["roam context <symbol>", "roam impact <symbol>"],
    "retrieve": ["roam context <symbol>", "roam preflight <symbol>"],
    "understand": ["roam tour", "roam stats", "roam health"],
    "tour": ["roam health", "roam minimap"],
    "init": ["roam understand", "roam tour", "roam stats"],
    "index": ["roam health", "roam diagnose <symbol>"],
    "stats": ["roam health", "roam tour"],
    "impact": ["roam preflight <symbol>", "roam affected-tests <symbol>"],
    "diagnose": ["roam timeline <symbol>", "roam recommend <symbol>"],
}


@roam_capability(
    name="workflow",
    category="getting-started",
    summary="Inspect a workflow recipe DAG, review lenses, and next commands",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("workflow")
@click.argument("recipe_name", required=False)
@click.option("--list", "list_recipes", is_flag=True, help="List available workflow recipes.")
@click.option(
    "--query",
    default="",
    help="Render {symbol}/{task} placeholders using this query without running commands.",
)
@click.option(
    "--next",
    "next_after",
    type=str,
    default=None,
    help="given the previously-run command, suggest what to run next.",
)
@click.pass_context
def workflow(ctx, recipe_name, list_recipes, query, next_after):
    """Inspect a workflow recipe DAG, review lenses, and next commands."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if next_after:
        # suggest what to run next given the prior command.
        from roam.output.formatter import json_envelope, to_json

        suggestions = _NEXT_HINTS.get(next_after.lower(), [])
        verdict = (
            f"{len(suggestions)} suggestion(s) after `roam {next_after}`"
            if suggestions
            else f"no canned next-command for `roam {next_after}`"
        )
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "workflow",
                        summary={"verdict": verdict, "after": next_after},
                        suggestions=suggestions,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        for s in suggestions:
            click.echo(f"  {s}")
        return

    if list_recipes or not recipe_name:
        _emit_recipe_list(json_mode)
        return

    recipe = by_name(recipe_name)
    if recipe is None:
        # W1083-followup: delegate the difflib closest-match + suffix-build
        # to the shared ``structured_unknown_filter`` helper. Canonical
        # knobs (cutoff=0.6, n=2) were already in use here per W1074. In
        # json_mode the helper also closes the Pattern-1C gap (pre-W1083-
        # followup the path raised UsageError unconditionally — no
        # structured stdout). Text-mode UsageError prefix + phrasing stay
        # byte-identical so the W1074 tests continue to pin the contract.
        from roam.output.errors import UNKNOWN_RECIPE, structured_usage_error

        # Local re-import: an earlier branch (``next_after``) has its own
        # ``from roam.output.formatter import ...`` line, which makes
        # ``json_envelope`` / ``to_json`` function-locals for the whole
        # function. When that branch returned early they stay unbound at
        # this point — re-importing here keeps the binding clean.
        from roam.output.formatter import json_envelope as _json_envelope
        from roam.output.formatter import to_json as _to_json
        from roam.output.structured_unknowns import (
            structured_unknown_filter,
            to_summary_payload,
        )

        known_names = sorted(r.name for r in RECIPES)
        frag = structured_unknown_filter(
            requested=recipe_name,
            known=known_names,
            state="unknown_recipe",
            requested_field="requested_recipe",
            known_field="known_recipes",
            fact_anchor="recipes",
        )
        # ``frag`` is always non-None here (we already know ``by_name``
        # returned None, so ``recipe_name`` is not in the closed set).
        assert frag is not None
        base_msg = (
            f"unknown workflow recipe: {recipe_name!r}. "
            f"Run `roam workflow --list`.{frag['verdict_suffix']}"
        )
        if json_mode:
            verdict_unknown = f"unknown workflow recipe {recipe_name!r}"
            click.echo(
                _to_json(
                    _json_envelope(
                        "workflow",
                        summary={
                            "verdict": verdict_unknown + frag["verdict_suffix"],
                            **to_summary_payload(frag),
                            "error_code": UNKNOWN_RECIPE,
                        },
                        agent_contract={
                            "facts": frag["facts"],
                            "next_commands": ["roam workflow --list"],
                        },
                    )
                )
            )
            # Still raise so exit code remains non-zero — agents read the
            # structured stdout envelope, the prefix lands on stderr.
        raise structured_usage_error(UNKNOWN_RECIPE, base_msg)

    _emit_recipe_detail(recipe, query, json_mode)
