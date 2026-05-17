"""Swarm orchestration: partition codebase for parallel multi-agent work.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because orchestrate outputs are invocation-scoped agent-partition
advice (agents[], merge_order[], shared_interfaces[]) — not per-location
violations. SARIF requires ``locations[]`` with file:line coordinates.
See action.yml _SUPPORTED_SARIF allowlist and W1154 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json


def _resolve_target_files(conn, file_args, staged, root):
    """Resolve --file and --staged into a list of file paths.

    Returns a list of file path strings (forward-slash normalized) or None
    if no filtering was requested (whole codebase).
    """
    if staged:
        from roam.commands.changed_files import get_changed_files, resolve_changed_to_db

        changed = get_changed_files(root, staged=True)
        if not changed:
            return []
        file_map = resolve_changed_to_db(conn, changed)
        return sorted(file_map.keys()) if file_map else []

    if not file_args:
        return None  # whole codebase

    # Expand directory paths: collect all indexed files matching the prefix
    target_files = []
    for arg in file_args:
        arg_norm = arg.replace("\\", "/").rstrip("/")
        # Check if it is an exact file
        row = conn.execute("SELECT path FROM files WHERE path = ?", (arg_norm,)).fetchone()
        if row:
            target_files.append(row["path"])
            continue
        # Try prefix (directory)
        rows = conn.execute(
            "SELECT path FROM files WHERE path LIKE ?",
            (arg_norm + "/%",),
        ).fetchall()
        if rows:
            target_files.extend(r["path"] for r in rows)
            continue
        # Try suffix match
        rows = conn.execute(
            "SELECT path FROM files WHERE path LIKE ?",
            ("%" + arg_norm + "%",),
        ).fetchall()
        target_files.extend(r["path"] for r in rows)

    return sorted(set(target_files)) if target_files else []


@roam_capability(
    name="orchestrate",
    category="architecture",
    summary="Partition the codebase for parallel multi-agent work",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("orchestrate")
@click.option(
    "--agents",
    "n_agents",
    required=True,
    type=int,
    help="Number of agents to partition work for",
)
@click.option(
    "--file",
    "file_args",
    multiple=True,
    help="Restrict to specific files or directories. Repeatable.",
)
@click.option(
    "--files",
    "file_args",
    multiple=True,
    hidden=True,
    help="Deprecated alias for --file. Retained for backward compatibility.",
)
@click.option(
    "--staged",
    is_flag=True,
    help="Restrict to files in the git staging area",
)
@click.pass_context
def orchestrate(ctx, n_agents, file_args, staged):
    """Partition the codebase for parallel multi-agent work.

    Assigns exclusive write zones, read-only dependencies, interface
    contracts, merge order, and conflict probability for N agents.
    Supports ``--file`` and ``--staged`` to restrict to a subgraph.

    Unlike ``partition`` (which provides deeper analytical metrics like
    difficulty scores, churn, and co-change coupling), this command
    focuses on operational dispatch: give it N agents and get back
    ready-to-use work assignments with interface contracts.

    \b
    Examples:
      roam orchestrate --n-agents 3
      roam orchestrate --n-agents 4 --staged
      roam orchestrate --n-agents 5 --file src/api.py --file src/auth.py
      roam --json orchestrate --n-agents 4

    See also ``partition`` (deeper analytical metrics + claude-teams
    output), ``agent-plan`` (dependency-ordered phases), and
    ``fleet`` (graph-aware planner for external orchestrators).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        target_files = _resolve_target_files(conn, file_args, staged, root)

        if target_files is not None and len(target_files) == 0:
            msg = "No matching files found"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "orchestrate",
                            summary={
                                "verdict": msg,
                                "n_agents": n_agents,
                                "write_conflicts": 0,
                                "shared_interfaces_count": 0,
                                "conflict_probability": 0.0,
                            },
                            agents=[],
                            merge_order=[],
                            shared_interfaces=[],
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {msg}")
            return

        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        G = build_symbol_graph(conn)
        result = partition_for_agents(G, conn, n_agents, target_files)

        agents = result["agents"]
        merge_order = result["merge_order"]
        conflict_prob = result["conflict_probability"]
        shared_interfaces = result["shared_interfaces"]
        write_conflicts = result["write_conflicts"]

        # LAW 4 anchor + LAW 6 verdict-first: terminate verdict on a
        # concrete-noun token (``agents``) so agents reading just the
        # verdict get an analytical sentence rather than ending on
        # ``interfaces`` (not in the concrete-noun anchor set).
        verdict = (
            f"orchestrated {len(agents)} agents with {write_conflicts} write conflicts "
            f"across {len(shared_interfaces)} shared interfaces"
        )

        if json_mode:
            # Curated agent_contract overrides the auto-derive's ugly
            # facts (``"4 n agents"`` / ``"0.0767 conflict probability
            # findings"``) with LAW 4-anchored alternatives. See
            # cmd_partition._partition_agent_contract for the canonical
            # pattern.
            facts = [
                verdict,
                f"orchestrated {len(agents)} agents",
                f"flagged {write_conflicts} write conflicts",
                f"conflict score {conflict_prob:.4f}",
            ]
            n_shared = len(shared_interfaces)
            if n_shared:
                facts.append(f"flagged {n_shared} shared interfaces")
            next_commands = []
            if conflict_prob >= 0.25 or write_conflicts >= 5:
                next_commands.append("roam clusters")
            click.echo(
                to_json(
                    json_envelope(
                        "orchestrate",
                        summary={
                            "verdict": verdict,
                            "n_agents": len(agents),
                            "write_conflicts": write_conflicts,
                            "shared_interfaces_count": len(shared_interfaces),
                            "conflict_probability": conflict_prob,
                        },
                        agent_contract={
                            "facts": facts,
                            "risks": [],
                            "next_commands": next_commands,
                            "confidence": None,
                        },
                        agents=agents,
                        merge_order=merge_order,
                        shared_interfaces=shared_interfaces,
                    )
                )
            )
            return

        # ── Text output (verdict first) ───────────────────────────
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        for agent in agents:
            click.echo(f"Agent {agent['id']}: {agent['cluster_label']} (cluster: {agent['cluster_label']})")
            if agent["write_files"]:
                files_str = ", ".join(agent["write_files"][:8])
                if len(agent["write_files"]) > 8:
                    files_str += f" (+{len(agent['write_files']) - 8} more)"
                click.echo(f"  Writes: {files_str} ({agent['symbols_owned']} symbols)")
            else:
                click.echo("  Writes: (none)")

            if agent["read_only_files"]:
                ro_str = ", ".join(agent["read_only_files"][:8])
                if len(agent["read_only_files"]) > 8:
                    ro_str += f" (+{len(agent['read_only_files']) - 8} more)"
                click.echo(f"  Reads:  {ro_str}")

            if agent["contracts"]:
                for c in agent["contracts"][:3]:
                    click.echo(f"  Contract: {c}")

            click.echo()

        # Merge order
        order_str = " -> ".join(f"Agent {aid}" for aid in merge_order)
        click.echo(f"Merge order: {order_str}")

        # Conflict probability
        boundary_count = int(conflict_prob * len(list(G.edges)) if G.edges else 0)
        click.echo(f"Conflict probability: {conflict_prob:.2f} ({boundary_count} symbol(s) in conductance boundary)")
