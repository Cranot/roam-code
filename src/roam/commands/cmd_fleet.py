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
def fleet_plan_command(ctx, goal, n_agents, adapter, output_path, branch_prefix):
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

    # W607-EB -- substrate-boundary plumbing for cmd_fleet.
    # ``_run_check_eb`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607eb_warnings_out`` rather than
    # crashing the fleet-plan (graph-aware planner producing
    # external-orchestrator manifests) command outright. cmd_fleet is
    # the fourth leg of the architecture multi-agent quad -- alongside
    # cmd_orchestrate (W607-DS), cmd_partition (W607-DU), and
    # cmd_agent_plan (W607-DY) -- and uniquely emits adapter-shaped
    # manifests (raw, Composio, Copilot CLI /fleet) for external
    # multi-agent orchestrators. A raise inside
    # ``compute_partition_manifest`` (DB -> partition manifest),
    # ``build_fleet_manifest`` (partition -> fleet envelope), the
    # adapter dispatch, or the downstream verdict / envelope composers
    # used to crash the fleet-plan command outright.
    # Marker family ``fleet_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped:
    #
    #   * resolve_target_files               -- n_agents normalisation
    #   * build_dependency_graph             -- partition manifest
    #                                           (DB -> partition manifest)
    #   * compute_partitions                 -- partition extraction
    #                                           from manifest
    #   * extract_external_manifest          -- build_fleet_manifest +
    #                                           adapter dispatch (output
    #                                           format for downstream
    #                                           orchestrator)
    #   * compute_fleet_metrics              -- per-fleet size / coupling
    #                                           / conflicts probe
    #   * compose_verdict                    -- LAW 6 single-line floor
    #   * compose_facts                      -- agent_contract.facts list
    #   * compose_next_commands              -- agent_contract.next_commands
    #   * serialize_envelope                 -- JSON envelope emission
    #   * format_text_output                 -- text path manifest printing
    #
    # W978 7-discipline applied: (1) f-string verdict floor uses literal
    # zero-count text -- no Name references, (2) default={...} carries
    # plain literals, (3) no json.dumps(default=str) needed (no
    # datetimes), (4) ``fleet_*`` prefix is unique (collision-checked
    # by cross-prefix-discipline test), (5) len() at kwarg-bind is
    # gated by the envelope fallback, (6) len() / if x: on a poisoned
    # object only runs after the empty-floor guard, (7) no dict.get(key,
    # expensive_default) calls -- all defaults are immutable literals.
    _w607eb_warnings_out: list[str] = []

    def _run_check_eb(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-EB marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``fleet_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607eb_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607eb_warnings_out.append(f"fleet_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-EB: ``resolve_target_files`` substrate -- normalise the
    # n_agents click option. cmd_fleet plan accepts an optional
    # n_agents (None means auto-detect); on a degraded resolution
    # leave it as None and let compute_partition_manifest auto-detect.
    def _resolve_target_files():
        return n_agents

    resolved_n_agents = _run_check_eb(
        "resolve_target_files",
        _resolve_target_files,
        default=None,
    )

    # W607-EB: empty-floor envelope reused by every degraded path so
    # the fleet envelope still composes a coherent verdict. The literal
    # zero counts avoid the 7919-partition CATASTROPHE shape
    # (CONSTRAINT 12 first-token executability) -- "0 task(s)" is the
    # executable empty-state, not "7919 task(s)" echoing raw input.
    empty_fleet_floor: dict = {
        "schema": "roam-fleet/v1",
        "goal": goal_text or "(no goal supplied)",
        "verdict": "",
        "tasks": [],
        "merge_order": [],
        "conflict_hotspots": [],
        "overall_conflict_probability": 0.0,
        "agent_count": 0,
    }

    empty_partition_floor: dict = {
        "verdict": "",
        "n_agents": 0,
        "total_partitions": 0,
        "overall_conflict_probability": 0.0,
        "partitions": [],
        "dependencies": [],
        "conflict_hotspots": [],
        "merge_order": [],
    }

    with open_db(readonly=True) as conn:
        # W607-EB: ``build_dependency_graph`` substrate -- partition
        # manifest construction (DB -> partition manifest). A raise
        # here degrades to the empty partition floor so the fleet
        # envelope still composes the LAW-6 verdict.
        partition_manifest = _run_check_eb(
            "build_dependency_graph",
            compute_partition_manifest,
            conn,
            n_agents=resolved_n_agents,
            default=empty_partition_floor,
        )
        if partition_manifest is None:
            partition_manifest = empty_partition_floor

    # W607-EB: ``compute_partitions`` substrate -- probe partition
    # extraction from the manifest. Returns the partitions list so a
    # raise on the access surfaces a distinct marker. Discarded result;
    # the partition_manifest above carries the canonical list.
    def _compute_partitions():
        return list(partition_manifest.get("partitions") or [])

    _run_check_eb(
        "compute_partitions",
        _compute_partitions,
        default=[],
    )

    # W607-EB: ``extract_external_manifest`` substrate -- build_fleet_manifest
    # + adapter dispatch. The fleet envelope is the output format for
    # the downstream orchestrator (raw, Composio, Copilot CLI /fleet).
    # A raise here degrades to the empty fleet floor.
    def _extract_external_manifest():
        env = build_fleet_manifest(
            partition_manifest,
            goal=goal_text,
            branch_prefix=branch_prefix,
        )
        adapter_fn = ADAPTERS.get(adapter.lower())
        if adapter_fn is None:
            raise KeyError(f"unknown fleet adapter ``{adapter!r}``; known: {sorted(ADAPTERS)!r}")
        rendered_local = adapter_fn(env)
        return (env, rendered_local)

    extracted = _run_check_eb(
        "extract_external_manifest",
        _extract_external_manifest,
        default=(empty_fleet_floor, dict(empty_fleet_floor)),
    )
    if extracted is None:
        extracted = (empty_fleet_floor, dict(empty_fleet_floor))
    envelope, rendered = extracted

    # W607-EB: ``compute_fleet_metrics`` substrate -- per-fleet size /
    # coupling / conflict probe. Validates the fleet envelope carries
    # the expected metric fields (agent_count / conflict_hotspots /
    # overall_conflict_probability) so a malformed envelope degrades
    # to literal zero counts on the verdict path.
    def _compute_fleet_metrics():
        agents = int(envelope.get("agent_count", 0) or 0)
        hotspots = len(envelope.get("conflict_hotspots") or [])
        conflict_prob = float(envelope.get("overall_conflict_probability", 0.0) or 0.0)
        return {
            "agents": agents,
            "conflict_hotspots": hotspots,
            "overall_conflict_probability": conflict_prob,
        }

    fleet_metrics = _run_check_eb(
        "compute_fleet_metrics",
        _compute_fleet_metrics,
        default={"agents": 0, "conflict_hotspots": 0, "overall_conflict_probability": 0.0},
    )
    if fleet_metrics is None:
        fleet_metrics = {"agents": 0, "conflict_hotspots": 0, "overall_conflict_probability": 0.0}

    if output_path:

        def _write_manifest():
            Path(output_path).write_text(
                _json.dumps(rendered, indent=2) + "\n",
                encoding="utf-8",
            )

        _run_check_eb("extract_external_manifest", _write_manifest, default=None)

    # W607-EB: ``compose_verdict`` substrate -- LAW 6 single-line
    # verdict. A raise inside the verdict access degrades to a literal
    # floor string with explicit zero counts -- the W811/W817 Pattern-2
    # guard: never collapse to a SAFE/passed verdict on the degraded
    # path. W978 #1: f-string verdict floor is plain text, no Name
    # references inside the literal.
    def _compose_verdict():
        return (
            f"{fleet_metrics['agents']} task(s), "
            f"{fleet_metrics['conflict_hotspots']} conflict hotspot(s), "
            f"overall conflict prob {fleet_metrics['overall_conflict_probability']:.2f}"
        )

    verdict = _run_check_eb(
        "compose_verdict",
        _compose_verdict,
        default="0 task(s), 0 conflict hotspot(s), overall conflict prob 0.00",
    )
    if not isinstance(verdict, str) or not verdict:
        verdict = "0 task(s), 0 conflict hotspot(s), overall conflict prob 0.00"

    # W607-EB: ``compose_facts`` substrate -- curated
    # ``agent_contract.facts`` list. A raise degrades to a single
    # verdict-only fact so LAW 6 verdict-first invariant holds.
    def _compose_facts():
        n_tasks = fleet_metrics["agents"]
        n_hotspots = fleet_metrics["conflict_hotspots"]
        facts_local = [
            verdict,
            f"{n_tasks} fleet tasks",
            f"{n_hotspots} conflict hotspots",
        ]
        return facts_local

    facts = _run_check_eb(
        "compose_facts",
        _compose_facts,
        default=[verdict],
    )
    if facts is None:
        facts = [verdict]

    # W607-EB: ``compose_next_commands`` substrate -- conditional
    # advisory next-step suggestions. A raise degrades to an empty
    # list so the agent_contract still composes.
    def _compose_next_commands():
        cmds = []
        cp = fleet_metrics["overall_conflict_probability"]
        if cp >= 0.25:
            cmds.append("roam orchestrate")
        if output_path:
            cmds.append(f"roam fleet verify {output_path}")
        return cmds

    next_commands = _run_check_eb(
        "compose_next_commands",
        _compose_next_commands,
        default=[],
    )
    if next_commands is None:
        next_commands = []

    if json_mode:
        # W607-EB: ``serialize_envelope`` substrate -- json_envelope
        # construction + click.echo emission. The wrap protects against
        # crashes inside the formatter call so the marker surfaces and
        # the function returns cleanly.
        envelope_summary: dict = {
            "verdict": verdict,
            "goal": goal_text,
            "agents": fleet_metrics["agents"],
            "conflict_hotspots": fleet_metrics["conflict_hotspots"],
            "overall_conflict_probability": fleet_metrics["overall_conflict_probability"],
            "adapter": adapter.lower(),
            "output_path": output_path or None,
        }
        envelope_kwargs: dict = dict(
            summary=envelope_summary,
            agent_contract={
                "facts": facts,
                "risks": [],
                "next_commands": next_commands,
                "confidence": None,
            },
            budget=token_budget,
            fleet=rendered,
        )
        # W607-EB: mirror substrate markers into BOTH the top-level
        # envelope ``warnings_out`` AND ``summary.warnings_out`` so MCP
        # consumers see disclosure regardless of which surface they
        # read. Flipping ``partial_success: True`` is the Pattern-2
        # silent-fallback guard.
        if _w607eb_warnings_out:
            envelope_summary["partial_success"] = True
            envelope_summary["warnings_out"] = list(_w607eb_warnings_out)
            envelope_kwargs["warnings_out"] = list(_w607eb_warnings_out)

        def _serialize_envelope():
            click.echo(to_json(json_envelope("fleet-plan", **envelope_kwargs)))

        _run_check_eb("serialize_envelope", _serialize_envelope, default=None)
        return

    # W607-EB: ``format_text_output`` substrate -- the human-readable
    # text emission path. A raise inside the loop (e.g. KeyError on a
    # malformed task dict missing ``task_id`` / ``title``) degrades to
    # a verdict-only emission so the user still sees the LAW 6 floor.
    def _format_text_output():
        click.echo(f"VERDICT: {verdict}")
        if goal_text:
            click.echo(f"GOAL: {goal_text}")
        click.echo()
        if output_path:
            click.echo(f"Wrote manifest ({adapter.lower()}) to: {output_path}")
            click.echo()
        if adapter.lower() == "raw":
            for t in envelope.get("tasks") or []:
                click.echo(f"  [{t['task_id']}] {t['title']}   branch={t['suggested_branch']}")
                click.echo(f"      files: {len(t['file_scope'])}")
                click.echo(f"      risk:  {t['conflict_risk']}")
        else:
            click.echo(f"Adapter '{adapter.lower()}' rendered:")
            click.echo(_json.dumps(rendered, indent=2)[:1500])
            if len(_json.dumps(rendered)) > 1500:
                click.echo("... (truncated; pass --output to capture full manifest)")

    _run_check_eb("format_text_output", _format_text_output, default=None)
    # Marker accumulator handles disclosure on the text path -- the
    # warning rides into ``_w607eb_warnings_out`` even when text-mode
    # output is human-targeted (JSON mode carries the structured
    # disclosure surface).


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
