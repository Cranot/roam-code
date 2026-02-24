"""Per-agent execution context from multi-agent task decomposition."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import budget_truncate, json_envelope, to_json
from roam.commands.cmd_agent_plan import build_agent_plan
from roam.commands.resolve import ensure_index


@click.command("agent-context")
@click.option(
    "--agent-id", "agent_id", required=True, type=click.IntRange(1, None),
    help="Worker ID (1-based).",
)
@click.option(
    "--agents", "n_agents", type=click.IntRange(1, None), default=None,
    help="Total number of agents used for partitioning (default: max(agent-id, 2)).",
)
@click.pass_context
def agent_context(ctx, agent_id, n_agents):
    """Generate per-worker context: write scope, read-only deps, and contracts."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    effective_agents = n_agents if n_agents is not None else max(2, agent_id)
    if effective_agents < 1:
        effective_agents = 1

    with open_db(readonly=True) as conn:
        plan = build_agent_plan(conn, n_agents=effective_agents)

    tasks = plan["tasks"]
    selected = None
    for task in tasks:
        if task["agent_id"] == f"Worker-{agent_id}":
            selected = task
            break

    if selected is None:
        msg = (
            f"Agent {agent_id} not found in plan with {effective_agents} agents. "
            "Try a larger --agents value."
        )
        if json_mode:
            click.echo(to_json(json_envelope(
                "agent-context",
                summary={
                    "verdict": msg,
                    "agent_id": agent_id,
                    "n_agents": effective_agents,
                },
                error=msg,
            )))
        else:
            click.echo(msg)
        raise SystemExit(1)

    downstream = selected.get("downstream_partitions", [])
    instructions = [
        "Edit only files in write_files.",
        "Treat read_only_dependencies as immutable unless a coordinated follow-up task is created.",
        "Preserve all interface_contracts while changing behavior.",
        "Before handoff, run `roam guard <key-symbol>` on 1-2 key symbols in this partition.",
    ]
    if downstream:
        instructions.append(
            f"Prepare compatibility handoff notes for downstream partitions: {', '.join(f'P{p}' for p in downstream)}."
        )

    payload = {
        "agent": {
            "agent_id": selected["agent_id"],
            "partition_id": selected["partition_id"],
            "task_id": selected["task_id"],
            "phase": selected["phase"],
            "merge_rank": selected["merge_rank"],
            "objective": selected["objective"],
        },
        "write_files": selected["write_files"],
        "read_only_dependencies": selected["read_only_dependencies"],
        "interface_contracts": selected["interface_contracts"],
        "depends_on_partitions": selected["depends_on_partitions"],
        "downstream_partitions": selected["downstream_partitions"],
        "key_symbols": selected["key_symbols"],
        "instructions": instructions,
        "coordination": {
            "merge_sequence": plan["merge_sequence"],
            "handoffs": [
                h for h in plan["handoffs"]
                if int(h["from_partition"]) == int(selected["partition_id"])
                or int(h["to_partition"]) == int(selected["partition_id"])
            ],
            "conflict_probability": plan["conflict_probability"],
        },
    }

    if json_mode:
        click.echo(to_json(json_envelope(
            "agent-context",
            summary={
                "verdict": (
                    f"context for {selected['agent_id']} "
                    f"(partition {selected['partition_id']}, phase {selected['phase']})"
                ),
                "agent_id": agent_id,
                "n_agents": effective_agents,
                "write_files": len(selected["write_files"]),
                "read_only_dependencies": len(selected["read_only_dependencies"]),
                "contracts": len(selected["interface_contracts"]),
                "downstream_partitions": len(selected["downstream_partitions"]),
            },
            **payload,
        )))
        return

    lines = []
    lines.append(
        f"AGENT CONTEXT: {selected['agent_id']}  "
        f"(partition P{selected['partition_id']}, phase {selected['phase']})"
    )
    lines.append(f"Objective: {selected['objective']}")
    lines.append("")

    lines.append(f"Write files ({len(selected['write_files'])}):")
    for fpath in selected["write_files"][:25]:
        lines.append(f"  - {fpath}")
    if len(selected["write_files"]) > 25:
        lines.append(f"  (+{len(selected['write_files']) - 25} more)")
    lines.append("")

    lines.append(f"Read-only dependencies ({len(selected['read_only_dependencies'])}):")
    if selected["read_only_dependencies"]:
        for fpath in selected["read_only_dependencies"][:25]:
            lines.append(f"  - {fpath}")
        if len(selected["read_only_dependencies"]) > 25:
            lines.append(f"  (+{len(selected['read_only_dependencies']) - 25} more)")
    else:
        lines.append("  - (none)")
    lines.append("")

    lines.append(f"Interface contracts ({len(selected['interface_contracts'])}):")
    if selected["interface_contracts"]:
        for c in selected["interface_contracts"]:
            lines.append(f"  - {c}")
    else:
        lines.append("  - (none)")
    lines.append("")

    lines.append("Execution guidance:")
    for item in instructions:
        lines.append(f"  - {item}")

    click.echo(budget_truncate("\n".join(lines), token_budget))
