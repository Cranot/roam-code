"""``roam commands`` — the repo's OWN runnable commands, classified + evidence-backed (G2).

Lists the build/test/lint/typecheck/run commands a repo actually exposes
(`package.json` scripts, `Makefile`, `justfile`, `pyproject`/`tox`, ecosystem
fallbacks), each classified by kind/scope/cost with the *evidence* that proves
the classification. This is the command graph that powers the G3 minimal-
verification contract and the Agent Change Proof Bundle's "checks available"
section — so an agent never has to guess `pnpm test` vs `npm run test` vs
`pytest`.

Engine: ``roam.command_graph`` (pure, local, deterministic, zero-token).

SARIF is deliberately NOT emitted: the command graph describes runnable
commands, not source-code-coordinate findings — there are no ``locations[]`` to
project into a SARIF result. Output formats are text (default) and ``--json``.
"""

from __future__ import annotations

from collections import Counter

import click

from roam.capability import roam_capability
from roam.command_graph import build_command_graph
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="commands",
    category="exploration",
    summary="List the repo's own runnable build/test/lint commands, classified by kind/scope/cost with evidence",
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command(name="commands")
@click.option(
    "--kind",
    "kind_filter",
    default=None,
    type=click.Choice(["test", "typecheck", "lint", "build", "run", "other"], case_sensitive=False),
    help="Only show commands of this kind.",
)
@click.option(
    "--scope",
    "scope_filter",
    default=None,
    type=click.Choice(["repo", "package", "file"], case_sensitive=False),
    help="Only show commands at this scope. (--scope affected is a G3/verification follow-up.)",
)
@click.option("--safe-only", is_flag=True, default=False, help="Only commands marked safe_to_auto_run.")
@click.option("--top", "--limit", "-n", "limit", default=100, type=int, help="Max commands to show (0 = all).")
@click.pass_context
def commands_cmd(ctx, kind_filter, scope_filter, safe_only, limit):
    """List the repo's runnable commands, classified + evidence-backed."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    graph = build_command_graph(None)
    cmds = graph["commands"]
    if kind_filter:
        cmds = [c for c in cmds if c["kind"] == kind_filter.lower()]
    if scope_filter:
        cmds = [c for c in cmds if c["scope"] == scope_filter.lower()]
    if safe_only:
        cmds = [c for c in cmds if c.get("safe_to_auto_run")]

    total = len(cmds)
    by_kind = dict(Counter(c["kind"] for c in cmds))
    sources = graph["sources_scanned"]
    n_sources = len(sources)
    truncated = False
    shown = cmds
    if limit and limit > 0 and total > limit:
        shown = cmds[:limit]
        truncated = True

    # Verdict (LAW 6: works standalone).
    if total == 0:
        if n_sources == 0:
            verdict = "0 runnable commands found — no package.json / Makefile / justfile / pyproject detected"
        else:
            verdict = f"0 runnable commands matched the filter — {n_sources} manifest sources scanned"
    else:
        kbits = ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items(), key=lambda kv: -kv[1])[:3])
        verdict = f"{total} runnable commands ({kbits}) across {n_sources} manifest sources"

    # agent_contract (LAW 4 anchored, imperative; CONSTRAINT 12 next_commands).
    facts = [
        f"{total} runnable commands",
        f"{by_kind.get('test', 0)} test commands",
        f"{n_sources} manifest sources scanned",
        "Run roam commands --kind test to list the test commands",
    ]
    next_commands = ["roam commands --kind test", "roam commands --json", "roam verify --changed"]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "commands",
                    summary={
                        "verdict": verdict,
                        "command_count": total,
                        "by_kind": by_kind,
                        "sources_scanned": sources,
                        "package_manager": graph["package_manager"],
                        "truncated": truncated,
                        "partial_success": False,
                    },
                    commands=shown,
                    command_graph_schema_version=graph["schema_version"],
                    agent_contract={"facts": facts, "next_commands": next_commands},
                )
            )
        )
        return

    # Text output.
    click.echo(f"VERDICT: {verdict}")
    if graph["package_manager"]:
        click.echo(f"Package manager: {graph['package_manager']}")
    for c in shown:
        flags = []
        if c.get("safe_to_auto_run"):
            flags.append("safe")
        if c.get("targetable"):
            flags.append("targetable")
        if c.get("mutates_state"):
            flags.append("mutates")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        click.echo(f"  {c['kind']:<10} {c['command']:<28} (cost {c['cost']}, conf {c['confidence']}){flag_str}")
        click.echo(f"             evidence: {', '.join(c['evidence'])}")
    if truncated:
        click.echo(f"  ... {total - len(shown)} more (raise --limit to see all)")
    if total == 0:
        click.echo("Run `roam commands --json` to integrate, or add scripts to package.json / Makefile.")
