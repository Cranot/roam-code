"""Shared workflow metadata helpers for ask, MCP compounds, and reports."""

from __future__ import annotations

from roam.ask.recipes import Recipe, by_name
from roam.ask.runner import extract_recipe_file, extract_recipe_symbol, fill_followups


def recipe_workflow_metadata(
    recipe: Recipe,
    *,
    query: str = "",
    render_followups: bool = False,
) -> dict:
    """Return the public workflow metadata shape for a recipe."""
    if render_followups:
        followups = fill_followups(recipe.followups, query, extract_recipe_symbol(query), extract_recipe_file(query))
    else:
        followups = list(recipe.followups)
    return {
        "recipe": recipe.name,
        "intent": recipe.intent,
        "phase": recipe.phase,
        "perspectives": list(recipe.perspectives),
        "followups": followups,
        "gates": list(recipe.gates),
    }


def workflow_metadata_for_recipe(
    recipe_name: str,
    *,
    query: str = "",
    render_followups: bool = False,
) -> dict | None:
    """Look up a recipe and return its public workflow metadata."""
    recipe = by_name(recipe_name)
    if recipe is None:
        return None
    return recipe_workflow_metadata(recipe, query=query, render_followups=render_followups)
