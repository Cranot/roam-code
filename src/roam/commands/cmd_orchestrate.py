"""Swarm orchestration: partition codebase for parallel multi-agent work."""

from __future__ import annotations

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


def _resolve_target_files(conn, file_args, staged, root):
    """Resolve --files and --staged into a list of file paths.

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
        row = conn.execute(
            "SELECT path FROM files WHERE path = ?", (arg_norm,)
        ).fetchone()
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


@click.command("orchestrate")
@click.option(
    "--agents", "n_agents", required=True, type=int,
    help="Number of agents to partition work for",
)
@click.option(
    "--files", "file_args", multiple=True,
    help="Restrict to specific files or directories",
)
@click.option(
    "--staged", is_flag=True,
    help="Restrict to files in the git staging area",
)
@click.pass_context
def orchestrate(ctx, n_agents, file_args, staged):
    """Partition the codebase for parallel multi-agent work.

    Assigns exclusive write zones, read-only dependencies, interface
    contracts, merge order, and conflict probability for N agents.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        target_files = _resolve_target_files(conn, file_args, staged, root)

        if target_files is not None and len(target_files) == 0:
            msg = "No matching files found"
            if json_mode:
                click.echo(to_json(json_envelope("orchestrate",
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
                )))
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

        verdict = (
            f"{len(agents)} agents, {write_conflicts} write conflicts, "
            f"{len(shared_interfaces)} shared interfaces"
        )

        if json_mode:
            click.echo(to_json(json_envelope("orchestrate",
                summary={
                    "verdict": verdict,
                    "n_agents": len(agents),
                    "write_conflicts": write_conflicts,
                    "shared_interfaces_count": len(shared_interfaces),
                    "conflict_probability": conflict_prob,
                },
                agents=agents,
                merge_order=merge_order,
                shared_interfaces=shared_interfaces,
            )))
            return

        # ── Text output (verdict first) ───────────────────────────
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        for agent in agents:
            click.echo(
                f"Agent {agent['id']}: {agent['cluster_label']} "
                f"(cluster: {agent['cluster_label']})"
            )
            if agent["write_files"]:
                files_str = ", ".join(agent["write_files"][:8])
                if len(agent["write_files"]) > 8:
                    files_str += f" (+{len(agent['write_files']) - 8} more)"
                click.echo(
                    f"  Writes: {files_str} "
                    f"({agent['symbols_owned']} symbols)"
                )
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
        boundary_count = int(
            conflict_prob * len(list(G.edges)) if G.edges else 0
        )
        click.echo(
            f"Conflict probability: {conflict_prob:.2f} "
            f"({boundary_count} symbol(s) in conductance boundary)"
        )
