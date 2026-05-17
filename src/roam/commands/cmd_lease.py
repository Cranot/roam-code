"""``roam lease`` — multi-agent lease CLI (R21 substrate).

Five subcommands (W20.6 docstring count corrected; ``claim`` takes either
``--file`` or ``--partition`` as the subject kind):

  - ``roam lease claim --agent NAME (--file PATH... | --partition ID)`` -- claim a lease
  - ``roam lease release <lease_id>``                  -- release a claim
  - ``roam lease list [--agent N] [--include-expired]``-- enumerate leases
  - ``roam lease show <lease_id>``                     -- dump one lease
  - ``roam lease gc``                                  -- force expired cleanup

A lease lives on disk at ``.roam/leases/<lease_id>.json``. This is the
SUBSTRATE for R21: ``roam orchestrate --claim`` / ``roam preflight``
warning-on-conflict are FUTURE integrations, NOT wired here. Keeping the
substrate decoupled means the CLI surface can evolve without touching
the storage format or the lock semantics.

Integration seams documented for future work
============================================

  * ``roam preflight <symbol>`` could call :func:`find_conflict` with the
    symbol's file path before reporting the verdict, and append a
    ``conflicting_lease`` field to its envelope.
  * ``roam orchestrate --claim`` could auto-invoke :func:`claim_lease`
    on each partition's file set after computing the partitioning.
  * ``roam pr-bundle`` could attach the agent's active leases as bundle
    metadata so reviewers see exactly what was held during the run.

None of these are implemented in this PR — substrate first, enforcement
later (matches the W13.2 mode substrate's release strategy).

Auto-log integration
====================

Every claim / release / gc event is auto-logged via
:func:`roam.runs.helpers.auto_log` so when ``ROAM_RUN_ID`` is set these
appear in the active run's ``events.jsonl`` timeline alongside
preflight / diff / critique. Auto-logging is silent if no run is
active.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam lease`` operates on substrate state in ``.roam/``
(lease records) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.exit_codes import EXIT_GATE_FAILURE, EXIT_USAGE
from roam.leases.store import (
    DEFAULT_TTL_SECONDS,
    Lease,
    claim_lease,
    gc_expired_leases,
    leases_root,
    list_leases,
    read_lease,
    release_lease,
)
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="lease",
    category="agent-os",
    summary="Multi-agent lease system: claim, release, list, show, gc.",
    inputs=["agent", "files", "partition"],
    outputs=["lease_id", "state", "conflicting_lease"],
    examples=[
        "roam lease claim --agent claude-code --file src/foo.py",
        "roam lease list",
        "roam lease release lease_20260513_abc123",
    ],
    tags=["lease", "agent-os", "multi-agent"],
    ai_safe=True,
    requires_index=False,
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.group("lease")
@click.pass_context
def lease_group(ctx):
    """Multi-agent lease system.

    A lease is an agent's advisory claim on a set of files OR a graph
    partition, stored under ``.roam/leases/<lease_id>.json``. Use
    ``roam lease claim`` to open a claim, ``roam lease release`` to
    drop it, ``roam lease list`` / ``roam lease show`` to inspect.

    Substrate for ``roam orchestrate --claim`` and future preflight
    conflict-warnings. NO auto-enforcement at command-dispatch level.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lease_table_row(lease: Lease) -> list[str]:
    subject = ", ".join(lease.subject[:3])
    if len(lease.subject) > 3:
        subject += f" (+{len(lease.subject) - 3} more)"
    return [
        lease.lease_id,
        lease.agent,
        lease.subject_kind,
        subject or "-",
        lease.state,
        lease.expires_at,
    ]


def _format_claim_verdict(lease: Lease) -> str:
    """Build the single-line verdict for a successful claim (LAW 6)."""
    n = len(lease.subject)
    noun = "file" if lease.subject_kind == "files" else "partition"
    plural = "" if n == 1 else "s"
    return f"claimed lease {lease.lease_id} (agent={lease.agent}, {n} {noun}{plural}, expires {lease.expires_at})"


def _format_conflict_verdict(conflict: Lease, subject_preview: str) -> str:
    """Build the single-line verdict for a blocked claim (LAW 6)."""
    return f"claim BLOCKED: conflict with lease {conflict.lease_id} (agent={conflict.agent}, owns: {subject_preview})"


# ---------------------------------------------------------------------------
# lease claim
# ---------------------------------------------------------------------------


@lease_group.command("claim")
@click.option("--agent", required=True, help="Agent identifier (e.g. claude-code, cursor, w17-task).")
@click.option(
    "--file",
    "files",
    multiple=True,
    help="File path(s) to claim. Repeatable. Mutually exclusive with --partition.",
)
@click.option(
    "--files",
    "files",
    multiple=True,
    hidden=True,
    help="Deprecated alias for --file. Retained for backward compatibility.",
)
@click.option(
    "--partition",
    "partition",
    multiple=True,
    help="Partition id(s) to claim. Repeatable. Mutually exclusive with --file.",
)
@click.option(
    "--ttl",
    "ttl_seconds",
    default=DEFAULT_TTL_SECONDS,
    show_default=True,
    type=int,
    help="Lease lifetime in seconds (default 1800 = 30min).",
)
@click.pass_context
def lease_claim(ctx, agent, files, partition, ttl_seconds):
    """Claim a lease on a set of files or partitions.

    \b
    Exactly one of ``--file`` / ``--partition`` must be supplied
    (repeatable). On success the lease_id is echoed; on conflict the
    command exits 5 (GATE_FAILURE) and the envelope's
    ``conflicting_lease`` field carries the blocking lease's record.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    # Validate --file / --partition are mutually exclusive and one was given.
    if bool(files) == bool(partition):
        verdict = (
            "exactly one of --file or --partition must be supplied"
            if not files and not partition
            else "--file and --partition are mutually exclusive"
        )
        if json_mode:
            envelope = json_envelope(
                "lease-claim",
                summary={
                    "verdict": verdict,
                    "state": "usage_error",
                    "partial_success": True,
                    "claimed": False,
                },
            )
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    if files:
        kind = "files"
        # Normalise file paths: forward slashes, no leading "./".
        subject = [str(f).replace("\\", "/").lstrip("./") for f in files]
    else:
        kind = "partition"
        subject = [f"partition:{p}" for p in partition]

    if ttl_seconds <= 0:
        verdict = "ttl_seconds must be positive"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "lease-claim",
                        summary={
                            "verdict": verdict,
                            "state": "usage_error",
                            "partial_success": True,
                            "claimed": False,
                        },
                    )
                )
            )
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    root = find_project_root()
    try:
        claimed, conflict = claim_lease(root, agent=agent, subject=subject, kind=kind, ttl_seconds=ttl_seconds)
    except ValueError as exc:
        verdict = f"error: {exc}"
        envelope = json_envelope(
            "lease-claim",
            summary={
                "verdict": verdict,
                "state": "usage_error",
                "partial_success": True,
                "claimed": False,
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    if conflict is not None:
        # CONFLICT path: exit 5, envelope describes the blocker.
        preview = ", ".join(conflict.subject[:3])
        if len(conflict.subject) > 3:
            preview += f" (+{len(conflict.subject) - 3} more)"
        verdict = _format_conflict_verdict(conflict, preview)
        envelope = json_envelope(
            "lease-claim",
            summary={
                "verdict": verdict,
                "state": "conflict",
                "partial_success": True,
                "claimed": False,
            },
            budget=token_budget,
            subject_requested=subject,
            subject_kind=kind,
            conflicting_lease=conflict.to_dict(),
            agent_contract={
                "facts": [
                    f"{conflict.agent} already holds lease {conflict.lease_id}",
                    f"blocking subject overlap: {preview}",
                    f"lease expires at {conflict.expires_at}",
                ],
                "next_commands": [
                    f"roam lease show {conflict.lease_id}",
                    f"roam lease release {conflict.lease_id}",
                    "roam lease list  # see all active leases",
                ],
            },
        )
        # Auto-log even on conflict — agents replay BOTH wins and losses.
        try:
            auto_log(envelope, action="lease-claim", target=conflict.lease_id, repo_root=root)
        except Exception:
            pass
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_GATE_FAILURE)
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  conflicting lease: {conflict.lease_id}")
        click.echo(f"  owned by:          {conflict.agent}")
        click.echo(f"  expires at:        {conflict.expires_at}")
        click.echo(f"  subject overlap:   {preview}")
        ctx.exit(EXIT_GATE_FAILURE)

    # SUCCESS path.
    assert claimed is not None  # for type-checkers; mutual-exclusion above
    verdict = _format_claim_verdict(claimed)
    envelope = json_envelope(
        "lease-claim",
        summary={
            "verdict": verdict,
            "state": "claimed",
            "partial_success": False,
            "claimed": True,
            "lease_id": claimed.lease_id,
            "expires_at": claimed.expires_at,
        },
        budget=token_budget,
        lease=claimed.to_dict(),
        path=str(leases_root(root) / f"{claimed.lease_id}.json"),
        agent_contract={
            "facts": [
                f"agent {claimed.agent} holds {len(claimed.subject)} {claimed.subject_kind}",
                f"lease expires at {claimed.expires_at}",
                f"lease_id: {claimed.lease_id}",
            ],
            "next_commands": [
                f"roam lease release {claimed.lease_id}",
                "roam lease list",
            ],
        },
    )
    try:
        # W294 - stamp ``lease_id`` on the event so the W292 collector
        # harvester corroborates the matching lease AuthorityRef and
        # promotes it to ``provenance="run_ledger"``. Only on the
        # SUCCESS path (the conflict branch above does NOT stamp the
        # field - the lease wasn't actually claimed).
        auto_log(
            envelope,
            action="lease-claim",
            target=claimed.lease_id,
            repo_root=root,
            extra_event_fields={"lease_id": claimed.lease_id},
        )
    except Exception:
        pass

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  lease_id:   {claimed.lease_id}")
    click.echo(f"  agent:      {claimed.agent}")
    click.echo(f"  kind:       {claimed.subject_kind}")
    click.echo(f"  subject:    {', '.join(claimed.subject)}")
    click.echo(f"  acquired:   {claimed.acquired_at}")
    click.echo(f"  expires:    {claimed.expires_at}")
    click.echo(f"  ttl_seconds:{claimed.ttl_seconds}")
    click.echo("")
    click.echo(f"Hint: roam lease release {claimed.lease_id}")


# ---------------------------------------------------------------------------
# lease release
# ---------------------------------------------------------------------------


@lease_group.command("release")
@click.argument("lease_id")
@click.pass_context
def lease_release(ctx, lease_id):
    """Release a lease (mark state=released). Idempotent."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    existing = read_lease(root, lease_id)
    if existing is None:
        verdict = f"lease {lease_id} does not exist -- run `roam lease list` to find a valid lease_id"
        envelope = json_envelope(
            "lease-release",
            summary={
                "verdict": verdict,
                "state": "unknown_lease",
                "partial_success": True,
                "released": False,
            },
            # W20.6 error-msg consistency
            agent_contract={
                "facts": [f"no lease named {lease_id} in this repo"],
                "next_commands": ["roam lease list"],
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    ok = release_lease(root, lease_id)
    if not ok:  # should be unreachable given the read_lease check above
        verdict = f"lease {lease_id} could not be released"
        envelope = json_envelope(
            "lease-release",
            summary={
                "verdict": verdict,
                "state": "error",
                "partial_success": True,
                "released": False,
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_GATE_FAILURE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_GATE_FAILURE)

    refreshed = read_lease(root, lease_id)
    state = refreshed.state if refreshed is not None else "released"
    verdict = f"released lease {lease_id} (agent={existing.agent})"
    envelope = json_envelope(
        "lease-release",
        summary={
            "verdict": verdict,
            "state": state,
            "partial_success": False,
            "released": True,
            "lease_id": lease_id,
        },
        budget=token_budget,
        lease=refreshed.to_dict() if refreshed is not None else None,
        agent_contract={
            "facts": [
                f"lease {lease_id} released by agent {existing.agent}",
                "subject is now free for another claim",
            ],
            "next_commands": [
                "roam lease list",
                f"roam lease claim --agent NAME --file {existing.subject[0] if existing.subject else '<path>'}",
            ],
        },
    )
    try:
        # W294 - stamp ``lease_id`` so a release event STILL corroborates
        # the matching lease AuthorityRef (the lease was held during the
        # change scope even if it's been released since). The W292
        # harvester does not distinguish acquire from release; both
        # emissions count as evidence the lease existed.
        auto_log(
            envelope,
            action="lease-release",
            target=lease_id,
            repo_root=root,
            extra_event_fields={"lease_id": lease_id},
        )
    except Exception:
        pass

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")


# ---------------------------------------------------------------------------
# lease list
# ---------------------------------------------------------------------------


@lease_group.command("list")
@click.option("--agent", default=None, help="Filter to leases held by this agent.")
@click.option(
    "--include-expired",
    is_flag=True,
    default=False,
    help="Include expired leases (default: hide expired).",
)
@click.option(
    "--gc",
    "do_gc",
    is_flag=True,
    default=False,
    help="Mark wall-clock-expired leases as state=expired before listing.",
)
@click.pass_context
def lease_list(ctx, agent, include_expired, do_gc):
    """List leases for this repo, newest first.

    Empty state (no leases yet) returns a clean envelope with
    ``state: no_leases`` -- never an error or empty stdout (Pattern 1).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    lroot = leases_root(root)

    freed: list[str] = []
    if do_gc:
        try:
            freed = gc_expired_leases(root)
        except Exception:
            freed = []

    if not lroot.exists():
        verdict = "no leases yet -- run `roam lease claim --agent NAME --file PATH` to open one"  # W20.6 error-msg consistency
        envelope = json_envelope(
            "lease-list",
            summary={
                "verdict": verdict,
                "state": "no_leases",
                "partial_success": False,
                "total": 0,
            },
            budget=token_budget,
            leases=[],
            path=str(lroot),
        )
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo(f"VERDICT: {verdict}")
        return

    leases = list_leases(root, agent=agent, include_expired=include_expired)
    total = len(leases)
    if total == 0:
        verdict = "no leases match the given filters"
        state = "no_matches"
    else:
        verdict = f"{total} lease{'s' if total != 1 else ''}"
        state = "ok"
    if do_gc and freed:
        verdict = f"{verdict} (gc freed {len(freed)})"

    # Explicit agent_contract: the auto-derive humanizer would render
    # ``gc_freed: 0`` as "0 gc freed findings" because ``freed`` is not in
    # the concrete-plural-terminal allowlist (LAW 4). Provide a hand-anchored
    # facts list with terminal nouns ("leases", "agents") instead.
    if total == 0:
        agent_facts = [f"{total} leases"]
    else:
        agent_owners = sorted({lease.agent for lease in leases})
        n_agents = len(agent_owners)
        agent_facts = [f"{total} active leases"]
        # Grammar: keep terminal "agents" plural for LAW-4 anchor; only emit
        # the line when the count is >1 to avoid awkward "1 distinct agents".
        if n_agents > 1:
            agent_facts.append(f"{n_agents} distinct agents")
        if do_gc and freed:
            agent_facts.append(f"{len(freed)} expired leases")
    envelope = json_envelope(
        "lease-list",
        summary={
            "verdict": verdict,
            "state": state,
            "partial_success": False,
            "total": total,
            "gc_freed": len(freed),
        },
        budget=token_budget,
        leases=[lease.to_dict() for lease in leases],
        gc_freed_ids=freed,
        path=str(lroot),
        agent_contract={
            "facts": agent_facts,
            "next_commands": (["roam lease claim --agent NAME --file PATH"] if total == 0 else []),
        },
    )

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    if total == 0:
        return
    rows = [_lease_table_row(lease) for lease in leases]
    click.echo(format_table(["Lease", "Agent", "Kind", "Subject", "State", "Expires"], rows))


# ---------------------------------------------------------------------------
# lease show
# ---------------------------------------------------------------------------


@lease_group.command("show")
@click.argument("lease_id")
@click.pass_context
def lease_show(ctx, lease_id):
    """Dump a single lease record."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    lease = read_lease(root, lease_id)
    if lease is None:
        verdict = f"lease {lease_id} does not exist -- run `roam lease list` to find a valid lease_id"
        envelope = json_envelope(
            "lease-show",
            summary={
                "verdict": verdict,
                "state": "unknown_lease",
                "partial_success": True,
                "total": 0,
            },
            budget=token_budget,
            lease=None,
            # W20.6 error-msg consistency
            agent_contract={
                "facts": [f"no lease named {lease_id} in this repo"],
                "next_commands": ["roam lease list"],
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    # Compute the effective wall-clock state so the show command tells the
    # same truth that find_conflict / list_leases see (Pattern 2).
    effective = lease.state
    if effective == "active" and lease.is_expired_at():
        effective = "expired"

    n = len(lease.subject)
    verdict = f"lease {lease.lease_id} state={effective} agent={lease.agent} ({n} {lease.subject_kind})"
    envelope = json_envelope(
        "lease-show",
        summary={
            "verdict": verdict,
            "state": effective,
            "partial_success": False,
            "total": 1,
            "lease_id": lease.lease_id,
        },
        budget=token_budget,
        lease={**lease.to_dict(), "effective_state": effective},
    )

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  lease_id:    {lease.lease_id}")
    click.echo(f"  agent:       {lease.agent}")
    click.echo(f"  kind:        {lease.subject_kind}")
    click.echo(f"  subject:     {', '.join(lease.subject)}")
    click.echo(f"  acquired_at: {lease.acquired_at}")
    click.echo(f"  expires_at:  {lease.expires_at}")
    click.echo(f"  ttl_seconds: {lease.ttl_seconds}")
    click.echo(f"  state:       {lease.state}")
    if effective != lease.state:
        click.echo(f"  effective:   {effective}  (wall-clock has advanced past expires_at)")


# ---------------------------------------------------------------------------
# lease gc
# ---------------------------------------------------------------------------


@lease_group.command("gc")
@click.pass_context
def lease_gc(ctx):
    """Mark wall-clock-expired leases as state=expired. Returns freed ids."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    try:
        freed = gc_expired_leases(root)
    except Exception as exc:
        verdict = f"gc error: {exc}"
        envelope = json_envelope(
            "lease-gc",
            summary={
                "verdict": verdict,
                "state": "error",
                "partial_success": True,
                "gc_freed": 0,
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_GATE_FAILURE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_GATE_FAILURE)

    n = len(freed)
    verdict = f"gc freed {n} expired lease{'s' if n != 1 else ''}"
    # Explicit agent_contract: the auto-derive humanizer renders
    # ``gc_freed: N`` as "N gc freed findings" because ``freed`` is not in
    # the concrete-plural-terminal allowlist (LAW 4). Hand-anchor on
    # "leases" terminal.
    envelope = json_envelope(
        "lease-gc",
        summary={
            "verdict": verdict,
            "state": "ok",
            "partial_success": False,
            "gc_freed": n,
        },
        budget=token_budget,
        freed_ids=freed,
        agent_contract={
            "facts": [f"{n} freed leases"],
            "next_commands": ["roam lease list"],
        },
    )
    try:
        auto_log(envelope, action="lease-gc", target="", repo_root=root)
    except Exception:
        pass

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    for lease_id in freed:
        click.echo(f"  freed: {lease_id}")
