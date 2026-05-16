"""roam fleet — graph-aware planner for multi-agent code work.

``roam fleet plan "<goal>"`` ingests a fleet goal, runs the existing
multi-agent partition (Louvain + co-change + blast-radius), and emits
``.roam-fleet.json`` shaped for external orchestrators (Composio,
GitHub Copilot CLI ``/fleet``, raw JSON for custom runtimes).

``roam fleet verify <manifest>`` is a v12.1 stub that re-runs the
blast-radius check against the live index and reports residual
cross-task overlap.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because fleet outputs are invocation-scoped agent-orchestration
metadata (agent task assignments, conflict_hotspots, conflict probability)
designed for external multi-agent orchestrators — not per-location code
findings. Output is manifest-shaped, not defect-shaped. See action.yml
_SUPPORTED_SARIF allowlist and W1155 audit memo.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.fleet.adapters import ADAPTERS
from roam.fleet.manifest import build_fleet_manifest
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="fleet",
    category="workflow",
    summary="Graph-aware planner for multi-agent code work",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.group()
def fleet():
    """Graph-aware planner for multi-agent code work."""


@fleet.command("plan")
@click.argument("goal", nargs=-1)
@click.option(
    "--n-agents",
    "n_agents",
    type=int,
    default=None,
    help="Number of agents (default: auto-detect from cluster count).",
)
@click.option(
    "--adapter",
    type=click.Choice(list(ADAPTERS), case_sensitive=False),
    default="raw",
    show_default=True,
    help="Output format adapter for the fleet manifest.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default=None,
    help=(
        "Write the manifest to this file (default: print to stdout). "
        "Use ``.roam-fleet.json`` for the canonical filename."
    ),
)
@click.option(
    "--branch-prefix",
    default="fleet",
    show_default=True,
    help="Prefix for suggested per-task branch names (e.g. 'fleet/3-billing').",
)
@click.pass_context
def fleet_plan(ctx, goal, n_agents, adapter, output_path, branch_prefix):
    """Plan a multi-agent fleet for a given goal.

    Returns a `.roam-fleet.json` envelope consumable by Composio Agent
    Orchestrator, GitHub Copilot CLI ``/fleet``, or any raw fleet runtime.
    The planner uses graph signals competitors can't compute without
    re-indexing: Louvain partitioning, dark-matter co-change, and
    personalised PageRank anchors per partition.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    goal_text = " ".join(goal).strip()

    ensure_index()

    from roam.commands.cmd_partition import compute_partition_manifest

    with open_db(readonly=True) as conn:
        partition_manifest = compute_partition_manifest(conn, n_agents=n_agents)

    envelope = build_fleet_manifest(
        partition_manifest,
        goal=goal_text,
        branch_prefix=branch_prefix,
    )
    rendered = ADAPTERS[adapter.lower()](envelope)

    if output_path:
        Path(output_path).write_text(
            _json.dumps(rendered, indent=2) + "\n",
            encoding="utf-8",
        )

    verdict = (
        f"{envelope['agent_count']} task(s), "
        f"{len(envelope['conflict_hotspots'])} conflict hotspot(s), "
        f"overall conflict prob {envelope['overall_conflict_probability']:.2f}"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "fleet-plan",
                    summary={
                        "verdict": verdict,
                        "goal": goal_text,
                        "agents": envelope["agent_count"],
                        "conflict_hotspots": len(envelope["conflict_hotspots"]),
                        "overall_conflict_probability": envelope["overall_conflict_probability"],
                        "adapter": adapter.lower(),
                        "output_path": output_path or None,
                    },
                    budget=token_budget,
                    fleet=rendered,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if goal_text:
        click.echo(f"GOAL: {goal_text}")
    click.echo()
    if output_path:
        click.echo(f"Wrote manifest ({adapter.lower()}) to: {output_path}")
        click.echo()
    if adapter.lower() == "raw":
        for t in envelope["tasks"]:
            click.echo(f"  [{t['task_id']}] {t['title']}   branch={t['suggested_branch']}")
            click.echo(f"      files: {len(t['file_scope'])}")
            click.echo(f"      risk:  {t['conflict_risk']}")
    else:
        click.echo(f"Adapter '{adapter.lower()}' rendered:")
        click.echo(_json.dumps(rendered, indent=2)[:1500])
        if len(_json.dumps(rendered)) > 1500:
            click.echo("... (truncated; pass --output to capture full manifest)")


@fleet.command("verify")
@click.argument(
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False),
)
@click.pass_context
def fleet_verify(ctx, manifest_path):
    """Re-check a fleet manifest against the current index.

    Reports residual cross-task file overlap (i.e. tasks that nominally
    shouldn't conflict but share a hot file). v12.0 ships an overlap
    audit; v12.1 will add structural-blast-radius diff per task.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    raw = Path(manifest_path).read_text(encoding="utf-8")
    try:
        manifest = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        from roam.output.errors import INVALID_FORMAT, structured_usage_error

        raise structured_usage_error(INVALID_FORMAT, f"manifest is not valid JSON: {exc}") from exc

    tasks = manifest.get("tasks") or manifest.get("agents") or manifest.get("worktrees") or []
    if not tasks:
        click.echo("VERDICT: no tasks in manifest")
        return

    # Normalise to (task_id, files) pairs across the three known shapes.
    pairs: list[tuple[str, list[str]]] = []
    for t in tasks:
        if "file_scope" in t:
            pairs.append((t.get("task_id", "?"), list(t["file_scope"])))
        elif "allowed_paths" in t:
            pairs.append((t.get("name", "?"), list(t["allowed_paths"])))
        elif "files" in t:
            pairs.append((t.get("description", "?")[:30], list(t["files"])))

    overlap_count = 0
    overlaps: list[dict] = []
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            a_id, a_files = pairs[i]
            b_id, b_files = pairs[j]
            shared = set(a_files) & set(b_files)
            if shared:
                overlap_count += 1
                overlaps.append({"a": a_id, "b": b_id, "files": sorted(shared)})

    verdict = f"{overlap_count} cross-task overlap(s) across {len(tasks)} task(s)"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "fleet-verify",
                    summary={
                        "verdict": verdict,
                        "task_count": len(tasks),
                        "overlap_count": overlap_count,
                    },
                    overlaps=overlaps,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    for o in overlaps:
        click.echo(f"  {o['a']} ↔ {o['b']}: {len(o['files'])} shared file(s)")
        for f in o["files"][:3]:
            click.echo(f"    {f}")
        if len(o["files"]) > 3:
            click.echo(f"    ... and {len(o['files']) - 3} more")
