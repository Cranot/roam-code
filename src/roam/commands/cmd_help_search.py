"""``roam help-search <query>`` — fuzzy match across every command's help.

replaces having to grep ``--help-all`` output of 200+ commands.
The default ranking weights name matches above docstring matches so
``roam help-search docs`` surfaces ``docs-coverage`` first, then
commands that mention "documentation" in their docstring.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because help-search outputs are invocation-scoped search-result
rankings — not per-location violations. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re

import click

from roam.capability import roam_capability
from roam.cli import _COMMANDS, _ensure_plugin_commands_loaded, _short_help_via_ast
from roam.output.formatter import json_envelope, to_json


def _score(query: str, name: str, help_text: str) -> int:
    """Higher = better. Pure heuristic — no fancy ranking required.

    * Exact name hit: +100
    * Substring of name: +60 (boost for shorter names)
    * Word-boundary match in help: +20 per term
    * Substring in help: +5 per term
    """
    q = query.lower().strip()
    if not q:
        return 0
    n = name.lower()
    h = help_text.lower()
    score = 0
    if q == n:
        score += 100
    elif q in n:
        # Shorter names with the query inside rank higher (closer match).
        score += 60 + max(0, 30 - len(n))
    terms = [t for t in re.split(r"\W+", q) if t]
    for term in terms:
        # Whole-word match in help
        if re.search(rf"\b{re.escape(term)}\b", h):
            score += 20
        elif term in h:
            score += 5
        if term in n:
            score += 10
    return score


@roam_capability(
    name="help-search",
    category="getting-started",
    summary="Fuzzy search across every command's help text",
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
@click.command(name="help-search")
@click.argument("query", nargs=-1, required=True)
@click.option(
    "--limit",
    type=int,
    default=15,
    show_default=True,
    help="Maximum number of matches to display.",
)
@click.pass_context
def help_search(ctx, query, limit) -> None:
    """Fuzzy search across every command's help text.

    \b
    Examples:
      roam help-search docs       # commands related to documentation
      roam help-search blast      # commands that mention "blast" in help
      roam help-search debt       # debt-related commands
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    q = " ".join(query).strip()
    if not q:
        from roam.output.errors import EMPTY_INPUT, structured_usage_error

        raise structured_usage_error(EMPTY_INPUT, "query cannot be empty")

    _ensure_plugin_commands_loaded()
    matches: list[dict] = []
    for cmd_name in sorted(_COMMANDS):
        help_text = _short_help_via_ast(cmd_name) or ""
        s = _score(q, cmd_name, help_text)
        if s <= 0:
            continue
        matches.append({"name": cmd_name, "help": help_text, "score": s})
    matches.sort(key=lambda m: (-m["score"], m["name"]))
    matches = matches[: max(1, limit)]

    verdict = f"{len(matches)} command(s) matching '{q}'" if matches else f"no commands matched '{q}'"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "help-search",
                    summary={"verdict": verdict, "count": len(matches)},
                    query=q,
                    matches=matches,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not matches:
        return
    click.echo()
    for m in matches:
        click.echo(f"  roam {m['name']:<28}  {m['help']}")
