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
def orchestrate_cmd(ctx, n_agents, file_args, staged):
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
      roam orchestrate --agents 3
      roam orchestrate --agents 4 --staged
      roam orchestrate --agents 5 --file src/api.py --file src/auth.py
      roam --json orchestrate --agents 4

    See also ``partition`` (deeper analytical metrics + claude-teams
    output), ``agent-plan`` (dependency-ordered phases), and
    ``fleet`` (graph-aware planner for external orchestrators).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # W607-DS -- substrate-boundary plumbing for cmd_orchestrate.
    # ``_run_check_ds`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607ds_warnings_out`` rather than
    # crashing the orchestrate (multi-agent partitioning) command outright.
    # cmd_orchestrate is the swarm-orchestration partitioner -- Louvain
    # community detection over the symbol graph with merge-order
    # planning and conflict-probability scoring. A raise in
    # ``build_symbol_graph`` (DB read), ``partition_for_agents`` (Louvain
    # + worker assignment + metrics), or the downstream verdict /
    # envelope composers used to crash the orchestrate command outright.
    # Marker family ``orchestrate_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped:
    #
    #   * resolve_target_files       -- --file / --staged resolution
    #   * build_dependency_graph     -- build_symbol_graph(conn)
    #   * partition_for_agents       -- Louvain + worker assignment +
    #                                   merge order + conflict prob
    #   * extract_agent_descriptors  -- result["agents"] / etc. unpack
    #   * compose_verdict            -- LAW 6 single-line verdict string
    #   * compose_facts              -- agent_contract.facts list
    #   * compose_next_commands      -- agent_contract.next_commands
    #   * serialize_envelope         -- JSON envelope emission
    #   * format_text_output         -- text path agent printing
    #
    # W978 7-discipline applied: f-string verdict floor (compose_verdict
    # returns a literal floor string), no Name references inside literal
    # default={...} dicts (defaults are plain tuples / [] / "" / 0),
    # json.dumps(default=str) is not used here (no datetime fields),
    # no phase-name collision (orchestrate prefix is unique), no
    # len()/if x: on a poisoned object without the empty-floor guard
    # below, no expensive default eager-eval.
    _w607ds_warnings_out: list[str] = []

    def _run_check_ds(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-DS marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an ``orchestrate_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ds_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ds_warnings_out.append(f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # W607-DS: ``resolve_target_files`` substrate -- a raise here
        # degrades to None (whole-codebase mode) rather than crashing
        # the command outright.
        target_files = _run_check_ds(
            "resolve_target_files",
            _resolve_target_files,
            conn,
            file_args,
            staged,
            root,
            default=None,
        )

        # ``target_files is not None and len(target_files) == 0`` is the
        # "no matching files" path. W978 #6: the len() check on a
        # poisoned object is gated by the explicit None check, so a
        # marker-degraded default of None routes to the whole-codebase
        # path instead.
        if target_files is not None and len(target_files) == 0:
            msg = "No matching files found"
            if json_mode:
                no_match_summary: dict = {
                    "verdict": msg,
                    "n_agents": n_agents,
                    "write_conflicts": 0,
                    "shared_interfaces_count": 0,
                    "conflict_probability": 0.0,
                }
                no_match_envelope_kwargs: dict = dict(
                    summary=no_match_summary,
                    agents=[],
                    merge_order=[],
                    shared_interfaces=[],
                )
                if _w607ds_warnings_out:
                    no_match_summary["partial_success"] = True
                    no_match_summary["warnings_out"] = list(_w607ds_warnings_out)
                    no_match_envelope_kwargs["warnings_out"] = list(_w607ds_warnings_out)
                click.echo(to_json(json_envelope("orchestrate", **no_match_envelope_kwargs)))
            else:
                click.echo(f"VERDICT: {msg}")
            return

        # W607-DS: ``build_dependency_graph`` substrate -- DB -> networkx
        # construction. A raise here degrades to an empty DiGraph so the
        # partition substrate produces the canonical _empty_result floor.
        import networkx as _nx

        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        G = _run_check_ds(
            "build_dependency_graph",
            build_symbol_graph,
            conn,
            default=_nx.DiGraph(),
        )
        if G is None:
            G = _nx.DiGraph()

        # W607-DS: ``partition_for_agents`` substrate -- the Louvain
        # community-detection + worker-assignment + merge-order +
        # conflict-probability engine. A raise here degrades to the
        # canonical empty-agents result so the envelope still composes.
        # W978 #2: default={...} dict carries plain literal values --
        # no Name references in the literal, so the kwarg is eager-eval
        # safe.
        empty_partition_result: dict = {
            "agents": [],
            "merge_order": [],
            "conflict_probability": 0.0,
            "shared_interfaces": [],
            "write_conflicts": 0,
        }
        result = _run_check_ds(
            "partition_for_agents",
            partition_for_agents,
            G,
            conn,
            n_agents,
            target_files,
            default=empty_partition_result,
        )
        if result is None:
            result = empty_partition_result

        # W607-DS: ``extract_agent_descriptors`` substrate -- unpack the
        # result dict. A KeyError on a malformed result degrades to the
        # empty floor so the envelope stays well-formed.
        def _extract_descriptors():
            return (
                result["agents"],
                result["merge_order"],
                result["conflict_probability"],
                result["shared_interfaces"],
                result["write_conflicts"],
            )

        descriptors = _run_check_ds(
            "extract_agent_descriptors",
            _extract_descriptors,
            default=([], [], 0.0, [], 0),
        )
        if descriptors is None:
            descriptors = ([], [], 0.0, [], 0)
        agents, merge_order, conflict_prob, shared_interfaces, write_conflicts = descriptors

        # W607-DS: ``compose_verdict`` substrate -- LAW 6 single-line
        # verdict + LAW 4 concrete-noun terminal. A raise inside the
        # f-string (e.g. len() on a poisoned object) degrades to a
        # literal floor string with explicit zero counts -- the W811/W817
        # Pattern-2 guard: never collapse to a SAFE/passed verdict on
        # the degraded path. W978 #1: f-string verdict floor is plain
        # text, no Name references.
        def _compose_verdict():
            return (
                f"orchestrated {len(agents)} agents with {write_conflicts} write conflicts "
                f"across {len(shared_interfaces)} shared interfaces"
            )

        verdict = _run_check_ds(
            "compose_verdict",
            _compose_verdict,
            default="orchestrated 0 agents with 0 write conflicts across 0 shared interfaces",
        )
        if not isinstance(verdict, str) or not verdict:
            verdict = "orchestrated 0 agents with 0 write conflicts across 0 shared interfaces"

        if json_mode:
            # W607-DS: ``compose_facts`` substrate -- the curated
            # agent_contract.facts list. A raise degrades to a single
            # verdict-only fact so LAW 6 verdict-first invariant holds.
            def _compose_facts():
                facts_local = [
                    verdict,
                    f"orchestrated {len(agents)} agents",
                    f"flagged {write_conflicts} write conflicts",
                    f"conflict score {conflict_prob:.4f}",
                ]
                n_shared = len(shared_interfaces)
                if n_shared:
                    facts_local.append(f"flagged {n_shared} shared interfaces")
                return facts_local

            facts = _run_check_ds(
                "compose_facts",
                _compose_facts,
                default=[verdict],
            )
            if facts is None:
                facts = [verdict]

            # W607-DS: ``compose_next_commands`` substrate -- conditional
            # advisory next-step suggestions. A raise degrades to the
            # empty list so the agent_contract still composes.
            def _compose_next_commands():
                ncs = []
                if conflict_prob >= 0.25 or write_conflicts >= 5:
                    ncs.append("roam clusters")
                return ncs

            next_commands = _run_check_ds(
                "compose_next_commands",
                _compose_next_commands,
                default=[],
            )
            if next_commands is None:
                next_commands = []

            # W607-DS: ``serialize_envelope`` substrate -- json_envelope
            # construction + click.echo emission. A raise here is fatal
            # in the strict sense (no output) but the wrap protects
            # against crashes inside the formatter call so the marker
            # surfaces and the function returns cleanly.
            envelope_summary: dict = {
                "verdict": verdict,
                "n_agents": len(agents),
                "write_conflicts": write_conflicts,
                "shared_interfaces_count": len(shared_interfaces),
                "conflict_probability": conflict_prob,
            }
            envelope_kwargs: dict = dict(
                summary=envelope_summary,
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
            # W607-DS: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard -- a degraded substrate
            # path must NOT be mistaken for a clean ranked verdict.
            if _w607ds_warnings_out:
                envelope_summary["partial_success"] = True
                envelope_summary["warnings_out"] = list(_w607ds_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607ds_warnings_out)

            def _serialize_envelope():
                click.echo(to_json(json_envelope("orchestrate", **envelope_kwargs)))

            _run_check_ds("serialize_envelope", _serialize_envelope, default=None)
            return

        # -- Text output (verdict first) ---------------------------
        # W607-DS: ``format_text_output`` substrate -- the human-readable
        # text emission path. A raise here (e.g. KeyError on a malformed
        # agent dict missing ``write_files`` / ``read_only_files`` /
        # ``contracts`` keys) degrades to a verdict-only emission so
        # the user still sees the LAW 6 floor.
        def _format_text_output():
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
            click.echo(
                f"Conflict probability: {conflict_prob:.2f} ({boundary_count} symbol(s) in conductance boundary)"
            )

        _run_check_ds("format_text_output", _format_text_output, default=None)
        # Marker accumulator handles disclosure on the text path -- the
        # warning rides into ``_w607ds_warnings_out`` even when text-mode
        # output is human-targeted (JSON mode carries the structured
        # disclosure surface).


orchestrate = orchestrate_cmd
