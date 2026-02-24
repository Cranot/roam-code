"""Agent task graph decomposition for multi-agent execution."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.cmd_partition import compute_partition_manifest
from roam.commands.resolve import ensure_index


def _dependency_maps(dependencies: list[dict]) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Build prerequisite and downstream maps from manifest dependency edges."""
    prereqs: dict[int, set[int]] = defaultdict(set)
    downstream: dict[int, set[int]] = defaultdict(set)

    for dep in dependencies:
        src = int(dep["from"])   # source partition depends on target partition
        tgt = int(dep["to"])
        prereqs[src].add(tgt)
        downstream[tgt].add(src)

    return prereqs, downstream


def _phase_map(partition_ids: list[int], prereqs: dict[int, set[int]]) -> dict[int, int]:
    """Assign execution phases based on dependency prerequisites."""
    remaining = {pid: set(prereqs.get(pid, set())) for pid in partition_ids}
    unscheduled = set(partition_ids)
    phase = 1
    phases: dict[int, int] = {}

    while unscheduled:
        ready = sorted(pid for pid in unscheduled if not remaining[pid])
        if not ready:
            # Cycle fallback: choose deterministic single partition and continue.
            ready = [min(unscheduled)]

        ready_set = set(ready)
        for pid in ready:
            phases[pid] = phase
        unscheduled -= ready_set
        for pid in unscheduled:
            remaining[pid] -= ready_set
        phase += 1

    return phases


def _task_id(partition_id: int) -> str:
    return f"T{partition_id:02d}"


def _build_contracts(
    partition_id: int,
    dependencies: list[dict],
) -> list[str]:
    """Derive interface contracts from cross-partition dependency edges."""
    contracts: list[str] = []

    outgoing = [d for d in dependencies if int(d["from"]) == partition_id]
    incoming = [d for d in dependencies if int(d["to"]) == partition_id]

    for dep in outgoing:
        contracts.append(
            f"Consumes partition {dep['to']} interfaces ({dep['edge_count']} edges)"
        )
    for dep in incoming:
        contracts.append(
            f"Publishes interfaces to partition {dep['from']} ({dep['edge_count']} edges)"
        )

    # Keep compact and deterministic.
    uniq = []
    seen = set()
    for c in contracts:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    return uniq[:8]


def _build_handoffs(dependencies: list[dict], merge_rank: dict[int, int]) -> list[dict]:
    """Convert dependency edges into merge-aware handoff instructions."""
    handoffs = []
    for dep in sorted(
        dependencies,
        key=lambda d: (
            merge_rank.get(int(d["to"]), 999),
            merge_rank.get(int(d["from"]), 999),
            int(d["from"]),
            int(d["to"]),
        ),
    ):
        handoffs.append({
            "from_partition": int(dep["to"]),
            "to_partition": int(dep["from"]),
            "reason": f"{dep['edge_count']} cross-partition edges",
            "sample_edges": list(dep.get("sample_edges", []))[:3],
        })
    return handoffs


def build_agent_plan(
    conn,
    n_agents: int,
) -> dict:
    """Build dependency-ordered multi-agent task plan from partition manifest."""
    manifest = compute_partition_manifest(conn, n_agents=n_agents)
    partitions = manifest["partitions"]
    dependencies = manifest["dependencies"]

    if not partitions:
        return {
            "verdict": "No partitions available",
            "n_agents": n_agents,
            "tasks": [],
            "merge_sequence": [],
            "handoffs": [],
            "claude_teams": {"agents": [], "coordination": {"merge_order": []}},
            "conflict_probability": 0.0,
            "manifest": manifest,
        }

    part_by_id = {int(p["id"]): p for p in partitions}
    partition_ids = sorted(part_by_id.keys())
    prereqs, downstream = _dependency_maps(dependencies)

    merge_sequence = [int(pid) for pid in manifest.get("merge_order", [])]
    if not merge_sequence:
        merge_sequence = partition_ids
    merge_rank = {pid: idx + 1 for idx, pid in enumerate(merge_sequence)}
    phases = _phase_map(partition_ids, prereqs)

    tasks = []
    for pid in sorted(
        partition_ids,
        key=lambda x: (phases.get(x, 999), merge_rank.get(x, 999), x),
    ):
        p = part_by_id[pid]
        dep_partitions = sorted(prereqs.get(pid, set()))
        downstream_partitions = sorted(downstream.get(pid, set()))

        read_only_files = []
        for dep_pid in dep_partitions:
            read_only_files.extend(part_by_id.get(dep_pid, {}).get("files", []))
        # Unique + deterministic
        read_only_files = sorted({
            fp for fp in read_only_files
            if fp not in set(p["files"])
        })

        tasks.append({
            "task_id": _task_id(pid),
            "partition_id": pid,
            "agent_id": p.get("agent", f"Worker-{pid}"),
            "phase": phases.get(pid, 1),
            "merge_rank": merge_rank.get(pid, 999),
            "objective": (
                f"Deliver partition {pid} ({p['role']}) with isolated writes "
                f"and stable cross-partition interfaces."
            ),
            "write_files": list(p["files"]),
            "read_only_dependencies": read_only_files,
            "depends_on_partitions": dep_partitions,
            "downstream_partitions": downstream_partitions,
            "interface_contracts": _build_contracts(pid, dependencies),
            "key_symbols": list(p.get("key_symbols", []))[:5],
            "difficulty_score": p.get("difficulty_score"),
            "difficulty_label": p.get("difficulty_label"),
            "conflict_risk": p.get("conflict_risk"),
        })

    # Claude Agent Teams-compatible projection.
    claude_agents = []
    for task in tasks:
        claude_agents.append({
            "agent_id": task["agent_id"],
            "role": part_by_id[task["partition_id"]]["role"],
            "scope": {
                "write_files": task["write_files"],
                "read_only_deps": task["read_only_dependencies"],
            },
            "depends_on": [
                part_by_id[pid].get("agent", f"Worker-{pid}")
                for pid in task["depends_on_partitions"]
                if pid in part_by_id
            ],
            "constraints": {
                "conflict_risk": task["conflict_risk"],
                "difficulty_label": task["difficulty_label"],
                "difficulty_score": task["difficulty_score"],
                "test_coverage": part_by_id[task["partition_id"]].get("test_coverage"),
            },
        })

    handoffs = _build_handoffs(dependencies, merge_rank)
    claude_teams = {
        "agents": claude_agents,
        "coordination": {
            "merge_order": [part_by_id[pid].get("agent", f"Worker-{pid}") for pid in merge_sequence if pid in part_by_id],
            "merge_partitions": merge_sequence,
            "handoffs": handoffs,
            "overall_conflict_probability": manifest["overall_conflict_probability"],
        },
    }

    return {
        "verdict": (
            f"{len(tasks)} tasks for {manifest['n_agents']} agents, "
            f"{len(handoffs)} handoffs, "
            f"{int(manifest['overall_conflict_probability'] * 100)}% conflict probability"
        ),
        "n_agents": manifest["n_agents"],
        "tasks": tasks,
        "merge_sequence": merge_sequence,
        "handoffs": handoffs,
        "claude_teams": claude_teams,
        "conflict_probability": manifest["overall_conflict_probability"],
        "manifest": manifest,
    }


@click.command("agent-plan")
@click.option(
    "--agents", "n_agents", required=True, type=click.IntRange(1, None),
    help="Number of agents/tasks to generate.",
)
@click.option(
    "--format", "output_format", type=click.Choice(["plain", "json", "claude-teams"]),
    default="plain",
    help="Output format.",
)
@click.pass_context
def agent_plan(ctx, n_agents, output_format):
    """Decompose partitions into dependency-ordered multi-agent tasks."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        plan = build_agent_plan(conn, n_agents=n_agents)

    if output_format == "claude-teams":
        if json_mode:
            click.echo(to_json(json_envelope(
                "agent-plan",
                summary={
                    "verdict": plan["verdict"],
                    "n_agents": plan["n_agents"],
                    "tasks": len(plan["tasks"]),
                    "handoffs": len(plan["handoffs"]),
                    "conflict_probability": plan["conflict_probability"],
                },
                format="claude-teams",
                **plan["claude_teams"],
            )))
        else:
            click.echo(to_json(plan["claude_teams"]))
        return

    if json_mode or output_format == "json":
        click.echo(to_json(json_envelope(
            "agent-plan",
            summary={
                "verdict": plan["verdict"],
                "n_agents": plan["n_agents"],
                "tasks": len(plan["tasks"]),
                "handoffs": len(plan["handoffs"]),
                "conflict_probability": plan["conflict_probability"],
            },
            tasks=plan["tasks"],
            merge_sequence=plan["merge_sequence"],
            handoffs=plan["handoffs"],
            claude_teams=plan["claude_teams"],
        )))
        return

    click.echo(f"VERDICT: {plan['verdict']}")
    click.echo()

    by_phase: dict[int, list[dict]] = defaultdict(list)
    for task in plan["tasks"]:
        by_phase[int(task["phase"])].append(task)

    for phase in sorted(by_phase):
        click.echo(f"Phase {phase}:")
        for task in sorted(by_phase[phase], key=lambda t: (t["merge_rank"], t["partition_id"])):
            deps = ", ".join(str(p) for p in task["depends_on_partitions"]) or "none"
            click.echo(
                f"  {task['task_id']} ({task['agent_id']}): "
                f"P{task['partition_id']}  deps=[{deps}]  "
                f"files={len(task['write_files'])}  "
                f"difficulty={task.get('difficulty_label', '?')}"
            )
        click.echo()

    merge_str = " -> ".join(f"P{pid}" for pid in plan["merge_sequence"])
    click.echo(f"Merge sequence: {merge_str}")
