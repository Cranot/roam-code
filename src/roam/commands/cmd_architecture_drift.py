"""`roam architecture-drift` — time-series view of structural change.

R23 companion to ``roam graph-diff``. Where ``graph-diff`` answers
"what changed between baseline X and head Y?", ``architecture-drift``
answers "what direction is the system trending over the last N days?"

Inputs
------
Reads every persisted snapshot in ``.roam/snapshots/*.json`` (created by
``roam graph-diff --save-snapshot <label>``). With fewer than 2 snapshots in
the window we emit ``state: insufficient_snapshots`` + ``partial_success: true``
rather than fabricating a trend from a single point.

Metrics
-------
For each adjacent pair of snapshots (oldest -> newest) we compute a
``diff_graphs`` delta, then convert raw counts into per-week rates over the
window. The summary also classifies overall direction:

* ``improving`` -- cycles decreasing OR cohesion improving
* ``degrading`` -- cycles increasing OR significant new-edge / new-cycle growth
* ``stable``    -- neither

"Cohesion" is approximated by the inverse of edge churn rate (more new edges
per symbol = falling cohesion). It's a coarse proxy but it matches what the
trends command already calls "coupling".

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because architecture-drift outputs are invocation-scoped
architecture trend rankings — not per-location violations. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root
from roam.graph.versioning import (
    diff_graphs,
    list_snapshot_files,
    read_snapshot,
)
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Window parsing
# ---------------------------------------------------------------------------

_WINDOW_RE = re.compile(r"^(\d+)\s*([dwmy])?$", re.IGNORECASE)
_WINDOW_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _parse_window(spec: str | None) -> int:
    """Convert ``"30d"`` / ``"4w"`` / ``"6m"`` / ``"1y"`` into a day count.

    Bare integers are interpreted as days. Returns ``30`` (default) for
    anything malformed -- callers should never blow up on a bad flag.
    """
    if spec is None:
        return 30
    m = _WINDOW_RE.match(str(spec).strip())
    if not m:
        return 30
    n = int(m.group(1))
    unit = (m.group(2) or "d").lower()
    return n * _WINDOW_DAYS.get(unit, 1)


# ---------------------------------------------------------------------------
# Series math
# ---------------------------------------------------------------------------


def _per_week(value: float, days: float) -> float:
    """Convert a per-window count to a per-week rate."""
    if days <= 0:
        return 0.0
    return round(value * (7.0 / days), 3)


def _classify_direction(metrics: dict) -> str:
    """Single-word verdict direction (``improving`` / ``degrading`` / ``stable``).

    Cycles_growth_rate dominates: any positive cycle growth -> degrading.
    Cycle decrease + low edge growth -> improving.
    Anything else -> stable.
    """
    cyc = metrics.get("cycles_growth_rate", 0.0)
    edge_growth = metrics.get("edges_growth_rate", 0.0)
    if cyc > 0.05:
        return "degrading"
    if cyc < -0.05:
        return "improving"
    if edge_growth > 5.0:
        return "degrading"
    if edge_growth < -2.0:
        return "improving"
    return "stable"


def _aggregate_pair_diffs(pair_diffs: list[dict], window_days: int) -> dict:
    """Roll up per-pair diffs into a single window-level metric block."""
    sym_added = sum(p["symbols_added"] for p in pair_diffs)
    sym_removed = sum(p["symbols_removed"] for p in pair_diffs)
    edges_added = sum(p["edges_added"] for p in pair_diffs)
    edges_removed = sum(p["edges_removed"] for p in pair_diffs)
    cycles_added = sum(p["new_cycles"] for p in pair_diffs)
    cycles_removed = sum(p["removed_cycles"] for p in pair_diffs)
    moves = sum(p["likely_moves"] for p in pair_diffs)
    in_shifts = sum(p["in_degree_shifts"] for p in pair_diffs)

    symbol_net = sym_added - sym_removed
    cycle_net = cycles_added - cycles_removed
    edge_net = edges_added - edges_removed

    return {
        "symbols_growth_rate": _per_week(symbol_net, window_days),
        "edges_growth_rate": _per_week(edge_net, window_days),
        "cycles_growth_rate": _per_week(cycle_net, window_days),
        "in_degree_shifts_per_week": _per_week(in_shifts, window_days),
        "likely_moves_per_week": _per_week(moves, window_days),
        "totals": {
            "symbols_added": sym_added,
            "symbols_removed": sym_removed,
            "edges_added": edges_added,
            "edges_removed": edges_removed,
            "cycles_added": cycles_added,
            "cycles_removed": cycles_removed,
            "likely_moves": moves,
        },
    }


def _verdict_for(metrics: dict, direction: str, window_days: int, n_snaps: int) -> str:
    """One-line verdict that works without any other field (LAW 6)."""
    cyc = metrics.get("cycles_growth_rate", 0.0)
    edge = metrics.get("edges_growth_rate", 0.0)
    sym = metrics.get("symbols_growth_rate", 0.0)
    return (
        f"Architecture {direction}: cycles {cyc:+.2f}/wk, "
        f"edges {edge:+.1f}/wk, symbols {sym:+.1f}/wk over "
        f"{window_days}d window ({n_snaps} snapshots)"
    )


def _biggest_movers(pair_diffs: list[dict], top: int) -> list[dict]:
    """Collect the largest in-degree shifts seen across the window."""
    movers: dict[str, dict] = {}
    for p in pair_diffs:
        for s in p.get("top_in_degree_shifts", []) or []:
            key = s["symbol"]
            cur = movers.get(key)
            if cur is None or abs(s["delta"]) > abs(cur["delta"]):
                movers[key] = dict(s)
    items = sorted(movers.values(), key=lambda d: -abs(d["delta"]))
    if top > 0:
        items = items[:top]
    return items


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="architecture-drift",
    category="architecture",
    summary="Architectural-trend report over a sliding window of snapshots",
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
@click.command(name="architecture-drift")
@click.option(
    "--window",
    "window_spec",
    default="30d",
    help="Time window: 30d / 4w / 6m / 1y. Bare integer = days. Default 30d.",
)
@click.option(
    "--top",
    default=10,
    type=int,
    help="Cap biggest_movers list to N rows in the envelope.",
)
@click.pass_context
def architecture_drift_cmd(ctx, window_spec, top):
    """Architectural-trend report over a sliding window of snapshots.

    Loads every snapshot in ``.roam/snapshots/`` (newest mtime within the
    window), then chains ``graph-diff`` across adjacent pairs to compute
    per-week growth rates for symbols, edges, cycles, in-degree shifts,
    and likely moves. The summary classifies overall direction.

    \b
    Examples:
      roam architecture-drift
      roam architecture-drift --window 90d
      roam architecture-drift --json --top 25

    Pairs with ``roam trends`` (metric-level series) and ``roam graph-diff``
    (point-in-time deltas).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cmd_name = ctx.info_name or "architecture-drift"
    ensure_index()
    root = find_project_root()

    window_days = _parse_window(window_spec)

    snapshots = list_snapshot_files(root)
    if len(snapshots) < 2:
        # Pattern 1 + Pattern 2: explicit state, partial success, NEVER empty.
        envelope = json_envelope(
            cmd_name,
            summary={
                "verdict": (f"Need at least 2 snapshots within window — found {len(snapshots)}"),
                "state": "insufficient_snapshots",
                "partial_success": True,
                "window_days": window_days,
                "snapshots_analyzed": len(snapshots),
                "directional": "unknown",
            },
            window_days=window_days,
            snapshots_analyzed=len(snapshots),
            metrics={},
            biggest_movers=[],
            pair_diffs=[],
            # LAW 4: anchor on the concrete subject (the snapshots
            # directory) with a clear verb, not bare counts. W17.3:
            # promoted from payload-level ``facts=`` to the proper
            # ``agent_contract.facts`` slot so they actually win over the
            # auto-derive.
            agent_contract={
                "facts": [
                    f"architecture drift needs >= 2 snapshots; found {len(snapshots)} in {root}/.roam/snapshots/",
                    "architecture drift skipped: insufficient snapshots to compute trajectory",
                ],
            },
            next_steps=[
                "roam graph-diff --save-snapshot baseline",
            ],
        )
        auto_log(envelope, action="architecture-drift", repo_root=root)
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo(f"VERDICT: Need at least 2 snapshots within window -- found {len(snapshots)}")
        click.echo()
        click.echo("Capture more with: roam graph-diff --save-snapshot <label>")
        return

    # Filter to snapshots whose mtime falls inside the window (newest stays).
    import time

    cutoff = time.time() - window_days * 86400
    eligible = [p for p in snapshots if p.stat().st_mtime >= cutoff]
    # Always include the newest two even if older than cutoff, so a long-quiet
    # repo still gets a verdict instead of a misleading "insufficient" reply.
    if len(eligible) < 2:
        eligible = snapshots[-2:]

    # Hydrate snapshots.
    loaded: list[tuple[str, dict]] = []
    for p in eligible:
        snap = read_snapshot(p)
        if snap is None:
            continue
        loaded.append((p.stem, snap))

    if len(loaded) < 2:
        envelope = json_envelope(
            cmd_name,
            summary={
                "verdict": "Snapshot files unreadable",
                "state": "insufficient_snapshots",
                "partial_success": True,
                "window_days": window_days,
                "snapshots_analyzed": len(loaded),
                "directional": "unknown",
            },
            window_days=window_days,
            snapshots_analyzed=len(loaded),
            metrics={},
            biggest_movers=[],
            pair_diffs=[],
            # LAW 4 (W17.3): anchor the no-data branch on the concrete
            # subject + the actionable next step, not the auto-derived
            # "N window days findings" noise.
            agent_contract={
                "facts": [
                    f"architecture drift over {window_days}d window: only "
                    f"{len(loaded)} readable snapshot(s) under "
                    f"{root}/.roam/snapshots/",
                    "architecture drift skipped: re-capture snapshots with `roam graph-diff --save-snapshot <label>`",
                ],
            },
        )
        auto_log(envelope, action="architecture-drift", repo_root=root)
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo("VERDICT: Snapshot files unreadable")
        return

    # Chain pairwise diffs across the (chronologically-sorted) loaded list.
    pair_diffs: list[dict] = []
    for (lbl_a, snap_a), (lbl_b, snap_b) in zip(loaded, loaded[1:]):
        d = diff_graphs(snap_a, snap_b)
        pair_diffs.append(
            {
                "from": lbl_a,
                "to": lbl_b,
                "symbols_added": len(d.symbols_added),
                "symbols_removed": len(d.symbols_removed),
                "edges_added": len(d.edges_added),
                "edges_removed": len(d.edges_removed),
                "new_cycles": len(d.new_cycles),
                "removed_cycles": len(d.removed_cycles),
                "likely_moves": len(d.likely_moves),
                "in_degree_shifts": len(d.in_degree_shifts),
                "top_in_degree_shifts": d.in_degree_shifts[:5],
                "total_signals": d.total_signal_count,
            }
        )

    aggregate = _aggregate_pair_diffs(pair_diffs, window_days)
    direction = _classify_direction(aggregate)
    biggest_movers = _biggest_movers(pair_diffs, top)

    verdict = _verdict_for(aggregate, direction, window_days, len(loaded))

    envelope = json_envelope(
        cmd_name,
        summary={
            "verdict": verdict,
            "state": "ok",
            "partial_success": False,
            "window_days": window_days,
            "snapshots_analyzed": len(loaded),
            "directional": direction,
            "cycles_growth_rate": aggregate["cycles_growth_rate"],
            "edges_growth_rate": aggregate["edges_growth_rate"],
            "symbols_growth_rate": aggregate["symbols_growth_rate"],
        },
        window_days=window_days,
        snapshots_analyzed=len(loaded),
        directional=direction,
        metrics=aggregate,
        biggest_movers=biggest_movers,
        pair_diffs=pair_diffs,
        # LAW 4 (CLAUDE.md): anchor each fact on the analytical subject
        # ("architecture drift") with an explicit verb, never bare
        # "key: value". W17.3 promoted these into the proper
        # ``agent_contract.facts`` slot — the payload-level ``facts=`` was
        # ignored by tight-context agents that only read the contract.
        agent_contract={
            "facts": [
                f"architecture drift over {window_days}d window: trajectory is {direction}",
                f"architecture drift over {window_days}d window: {len(loaded)} snapshots analysed",
                f"architecture drift over {window_days}d window: cycles trending "
                f"{aggregate['cycles_growth_rate']:+.2f}/wk",
                f"architecture drift over {window_days}d window: edges trending "
                f"{aggregate['edges_growth_rate']:+.1f}/wk",
            ],
        },
        next_steps=_next_commands_for(direction, aggregate),
    )

    auto_log(envelope, action="architecture-drift", repo_root=root)

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Window: {window_days}d   Snapshots analysed: {len(loaded)}   Direction: {direction}")
    click.echo()
    click.echo("Per-week rates:")
    click.echo(f"  symbols   {aggregate['symbols_growth_rate']:+.2f}/wk")
    click.echo(f"  edges     {aggregate['edges_growth_rate']:+.2f}/wk")
    click.echo(f"  cycles    {aggregate['cycles_growth_rate']:+.2f}/wk")
    click.echo(f"  in-degree shifts {aggregate['in_degree_shifts_per_week']:+.2f}/wk")
    click.echo(f"  likely moves     {aggregate['likely_moves_per_week']:+.2f}/wk")

    if biggest_movers:
        click.echo()
        click.echo("Biggest in-degree movers across window:")
        for m in biggest_movers:
            click.echo(f"  {m['symbol']}  {m['before']} -> {m['after']} (delta {m['delta']:+d})")

    if pair_diffs:
        click.echo()
        click.echo("Pair-by-pair signal counts:")
        for p in pair_diffs:
            click.echo(
                f"  {p['from']} -> {p['to']}  "
                f"signals={p['total_signals']}  "
                f"sym=+{p['symbols_added']}/-{p['symbols_removed']}  "
                f"edges=+{p['edges_added']}/-{p['edges_removed']}  "
                f"cycles=+{p['new_cycles']}/-{p['removed_cycles']}"
            )


def _next_commands_for(direction: str, aggregate: dict) -> list[str]:
    """Imperative follow-ups keyed on direction (LAW 2)."""
    if direction == "degrading":
        cmds = ["roam clusters", "roam dark-matter"]
        if aggregate.get("cycles_growth_rate", 0.0) > 0:
            cmds.insert(0, "roam health")
        return cmds
    if direction == "improving":
        return ["roam graph-diff --save-snapshot improving"]
    return ["roam graph-diff"]
