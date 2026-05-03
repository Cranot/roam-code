"""roam ask — the only command anyone needs to remember (B.1).

Brainstorm 02_dx_design.md framing: the ~143-command surface is intimidating
to humans and bloats the agent context. ``roam ask "<sentence>"`` collapses
that into a deterministic intent classifier over a small recipe book.

The registry ships 13 recipes that cover the most common intents using v12
primitives (retrieve, critique, fleet, taint, fixture impact) plus the classic
preflight/health/context/diff stack. Recipes also carry workflow metadata so
agents can reason about phase, review lenses, gates, and follow-up actions.

Public API:

* :data:`recipes.RECIPES` — the recipe registry.
* :func:`classifier.classify` — pick the best recipe for a free-form query.
* :func:`runner.run_recipe` — execute a recipe's command DAG against the
  current project.

Recipes are pure data — adding one is editing :data:`recipes.RECIPES`, no
Click registration required.
"""

from __future__ import annotations
