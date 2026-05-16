"""`roam graph-diff` — structural delta between two graph snapshots.

R23: surfaces what changed in the SYSTEM STRUCTURE, not just what files
changed (that's ``roam diff``). New / removed symbols, edge churn, in/out-
degree shifts, freshly-introduced cycles, layer migrations, and "likely
move" rename heuristics.

v1 scope (per BACKLOG)
----------------------
Option B only: read previously-persisted snapshots from ``.roam/snapshots/``.
If no baseline snapshot exists we emit a clean ``state: no_baseline_snapshot``
envelope (NEVER an empty stdout / crash). Git-worktree-based re-indexing of
the base ref is a documented follow-up.

Usage::

    roam graph-diff                        # latest snapshot vs current
    roam graph-diff --base <label>         # named-snapshot vs current
    roam graph-diff --base A --head B      # two persisted snapshots
    roam graph-diff --json
    roam graph-diff --top 20
    roam graph-diff --save-snapshot <name> # persist current as named snapshot

Pairs with ``roam trends`` (time-series of *metrics*) and
``roam architecture-drift`` (time-series of *graph deltas*).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because graph-diff outputs are invocation-scoped structural
delta digests — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.graph.versioning import (
    diff_graphs,
    list_snapshot_files,
    read_snapshot,
    snapshot_graph,
    write_snapshot,
)
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


def _verdict_for(diff) -> str:
    """One-line, executable-first verdict for ``summary.verdict``.

    Keeps the LAW-6 compression-survives test: works without any other field.
    """
    if diff.total_signal_count == 0:
        return "No structural changes vs baseline snapshot"
    bits: list[str] = []
    if diff.symbols_added:
        bits.append(f"{len(diff.symbols_added)} new symbols")
    if diff.symbols_removed:
        bits.append(f"{len(diff.symbols_removed)} symbols removed")
    if diff.edges_added:
        bits.append(f"{len(diff.edges_added)} new edges")
    if diff.edges_removed:
        bits.append(f"{len(diff.edges_removed)} edges removed")
    if diff.new_cycles:
        bits.append(f"{len(diff.new_cycles)} cycles introduced")
    if diff.likely_moves:
        bits.append(f"{len(diff.likely_moves)} likely moves")
    if not bits:
        bits.append(f"{diff.total_signal_count} signals")
    return f"{diff.total_signal_count} structural changes ({', '.join(bits)})"


def _next_commands(diff) -> list[str]:
    """Imperative next commands (LAW 2). Empty when nothing actionable."""
    cmds: list[str] = []
    if diff.in_degree_shifts:
        biggest = diff.in_degree_shifts[0]
        sym_name = biggest["symbol"].split("::")[0]
        cmds.append(f"roam impact {sym_name}")
    if diff.new_cycles:
        cmds.append("roam health")
    if diff.likely_moves:
        cmds.append("roam fingerprint")
    if not cmds:
        cmds.append("roam diff")
    return cmds


def _facts(diff, baseline_label: str | None) -> list[str]:
    """Flat, positive, concrete-noun-anchored facts (LAWs 4 + 10).

    Each fact is anchored on the analytical subject ("graph delta") with an
    explicit verb ("added", "lost", "introduced"), so a tight-context agent
    reading only ``agent_contract.facts`` activates analytical mode instead
    of summary mode.
    """
    label = baseline_label or "baseline"
    facts: list[str] = []
    facts.append(
        f"graph delta vs {label}: added {len(diff.symbols_added)} symbols, removed {len(diff.symbols_removed)} symbols"
    )
    facts.append(
        f"graph delta vs {label}: added {len(diff.edges_added)} call edges, "
        f"removed {len(diff.edges_removed)} call edges"
    )
    facts.append(
        f"graph delta vs {label}: {len(diff.in_degree_shifts)} symbols changed "
        f"in-degree, {len(diff.out_degree_shifts)} changed out-degree"
    )
    if diff.new_cycles:
        facts.append(f"graph delta vs {label}: introduced {len(diff.new_cycles)} new cycles")
    if diff.likely_moves:
        facts.append(
            f"graph delta vs {label}: detected {len(diff.likely_moves)} "
            "likely symbol moves (rename / refactor candidates)"
        )
    return facts


def _trim(items: list, top: int) -> list:
    if top <= 0:
        return items
    return items[:top]


def _resolve_snapshot(root, label: str | None) -> tuple[dict | None, str | None]:
    """Resolve *label* to a (snapshot_dict, label_string).

    ``label`` can be:
      * ``None``  -> newest persisted snapshot
      * a file name (with or without ``.json``)
      * a path to a JSON file

    Returns ``(None, None)`` if no snapshot can be located.
    """
    from pathlib import Path as _Path

    files = list_snapshot_files(root)

    if label is None:
        if not files:
            return None, None
        latest = files[-1]
        return read_snapshot(latest), latest.stem

    # Explicit file path?
    candidate = _Path(label)
    if candidate.is_file():
        return read_snapshot(candidate), candidate.stem

    # Match by name/stem.
    for f in files:
        if label in (f.name, f.stem):
            return read_snapshot(f), f.stem

    return None, None


@roam_capability(
    name="graph-diff",
    category="architecture",
    summary="Structural diff between two graph snapshots",
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
@click.command(name="graph-diff")
@click.option(
    "--base",
    default=None,
    help="Baseline snapshot label (default: newest .roam/snapshots/*.json).",
)
@click.option(
    "--head",
    default=None,
    help="Head snapshot label. Default: current DB graph (live).",
)
@click.option(
    "--top",
    default=20,
    type=int,
    help="Cap list outputs to N rows in the envelope (0 = unlimited).",
)
@click.option(
    "--save-snapshot",
    default=None,
    help="Persist the current DB graph to .roam/snapshots/<label>.json and exit.",
)
@click.pass_context
def graph_diff_cmd(ctx, base, head, top, save_snapshot):
    """Structural diff between two graph snapshots.

    Surfaces new/removed symbols, edge churn, degree shifts, new cycles,
    layer migrations, and likely renames. v1 reads persisted snapshots from
    ``.roam/snapshots/`` only; run with ``--save-snapshot LABEL`` to capture
    one. Emits a clean ``state: no_baseline_snapshot`` envelope if nothing
    is on disk.

    \b
    Examples:
      roam graph-diff --save-snapshot pre-refactor
      # ... make changes ...
      roam graph-diff --base pre-refactor
      roam graph-diff --json --top 50
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cmd_name = ctx.info_name or "graph-diff"
    ensure_index()
    root = find_project_root()

    # --- Save-snapshot path (one-shot) ---
    if save_snapshot:
        with open_db(readonly=True) as conn:
            snap = snapshot_graph(conn)
        path = write_snapshot(root, snap, label=save_snapshot)
        envelope = json_envelope(
            cmd_name,
            summary={
                "verdict": f"Snapshot saved: {save_snapshot}",
                "state": "ok",
                "partial_success": False,
                "snapshot_path": str(path),
                "symbol_count": snap["metrics"]["symbol_count"],
                "edge_count": snap["metrics"]["edge_count"],
            },
            mode="save",
            snapshot_path=str(path),
            metrics=snap["metrics"],
            next_steps=["roam graph-diff"],
        )
        auto_log(envelope, action="graph-diff", target=save_snapshot, repo_root=root)
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo(f"VERDICT: Snapshot saved: {save_snapshot}")
        click.echo(f"Path: {path}")
        click.echo(
            f"Symbols: {snap['metrics']['symbol_count']}  "
            f"Edges: {snap['metrics']['edge_count']}  "
            f"Cycles: {snap['metrics']['cycle_count']}"
        )
        return

    # --- Resolve baseline ---
    before, before_label = _resolve_snapshot(root, base)
    if before is None:
        # Clean "no baseline" envelope — NEVER empty stdout (Pattern 1).
        # LAW 4 (W17.3): anchor the no-data facts on the literal subject
        # ("graph-diff baseline") with concrete paths + the executable
        # command that creates one, not the abstract "no data" string the
        # auto-derive would emit.
        snapshots_dir = root / ".roam" / "snapshots"
        envelope = json_envelope(
            cmd_name,
            summary={
                "verdict": "No baseline snapshot found",
                "state": "no_baseline_snapshot",
                "partial_success": True,
                "total_signals": 0,
                "hint": "Run `roam graph-diff --save-snapshot <label>` to capture a baseline.",
            },
            symbols_added=[],
            symbols_removed=[],
            edges_added_count=0,
            edges_removed_count=0,
            in_degree_shifts=[],
            out_degree_shifts=[],
            new_cycles=[],
            removed_cycles=[],
            layer_changes=[],
            likely_moves=[],
            agent_contract={
                "facts": [
                    f"graph-diff baseline: no snapshot found under {snapshots_dir}",
                    "graph-diff baseline: run `roam graph-diff --save-snapshot <label>` to capture one",
                ],
            },
            next_steps=[
                "roam graph-diff --save-snapshot baseline",
            ],
        )
        auto_log(envelope, action="graph-diff", target="(no_baseline)", repo_root=root)
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo("VERDICT: No baseline snapshot found")
        click.echo()
        click.echo(
            f"Capture one with: roam graph-diff --save-snapshot <label>\nSnapshot directory: {root}/.roam/snapshots/"
        )
        return

    # --- Resolve head ---
    if head is None:
        with open_db(readonly=True) as conn:
            after = snapshot_graph(conn)
        head_label = "(current)"
    else:
        after, head_label = _resolve_snapshot(root, head)
        if after is None:
            envelope = json_envelope(
                cmd_name,
                summary={
                    "verdict": f"Head snapshot not found: {head}",
                    "state": "no_head_snapshot",
                    "partial_success": True,
                    "total_signals": 0,
                },
                agent_contract={
                    "facts": [
                        f"graph-diff head: snapshot label `{head}` not found under {root}/.roam/snapshots/",
                        "graph-diff head: list available labels with `ls .roam/snapshots/`",
                    ],
                },
                next_steps=["roam graph-diff --save-snapshot <label>"],
            )
            auto_log(envelope, action="graph-diff", target=head, repo_root=root)
            if json_mode:
                click.echo(to_json(envelope))
                return
            click.echo(f"VERDICT: Head snapshot not found: {head}")
            return

    # --- Diff ---
    diff = diff_graphs(before, after)

    # Build envelope payload (trim list fields to ``--top`` for token budget).
    payload = {
        "baseline_label": before_label,
        "head_label": head_label,
        "symbols_added": _trim(diff.symbols_added, top),
        "symbols_removed": _trim(diff.symbols_removed, top),
        "edges_added_count": len(diff.edges_added),
        "edges_removed_count": len(diff.edges_removed),
        "edges_added": _trim([list(e) for e in diff.edges_added], top),
        "edges_removed": _trim([list(e) for e in diff.edges_removed], top),
        "in_degree_shifts": _trim(diff.in_degree_shifts, top),
        "out_degree_shifts": _trim(diff.out_degree_shifts, top),
        "new_cycles": _trim(diff.new_cycles, top),
        "removed_cycles": _trim(diff.removed_cycles, top),
        "layer_changes": _trim(diff.layer_changes, top),
        "likely_moves": _trim(diff.likely_moves, top),
        "facts": _facts(diff, before_label),
        "next_steps": _next_commands(diff),
    }

    envelope = json_envelope(
        cmd_name,
        summary={
            "verdict": _verdict_for(diff),
            "state": "ok",
            "partial_success": False,
            "total_signals": diff.total_signal_count,
            "baseline_label": before_label,
            "head_label": head_label,
            "symbols_added": len(diff.symbols_added),
            "symbols_removed": len(diff.symbols_removed),
            "new_cycles": len(diff.new_cycles),
            "likely_moves": len(diff.likely_moves),
        },
        # W17.3: promote ``_facts()`` strings into ``agent_contract`` so
        # tight-context agents that only read the contract still see the
        # concrete-noun anchored facts. The payload-level ``facts`` field
        # stays in place for full-envelope consumers.
        agent_contract={"facts": _facts(diff, before_label)},
        **payload,
    )

    auto_log(envelope, action="graph-diff", target=before_label or "", repo_root=root)

    if json_mode:
        click.echo(to_json(envelope))
        return

    # Plain-ASCII text rendering.
    click.echo(f"VERDICT: {_verdict_for(diff)}")
    click.echo()
    click.echo(f"Baseline: {before_label}    Head: {head_label}")
    click.echo()
    click.echo(
        f"Symbols  +{len(diff.symbols_added):<5d} -{len(diff.symbols_removed):<5d}  "
        f"Edges  +{len(diff.edges_added):<5d} -{len(diff.edges_removed):<5d}  "
        f"Cycles  +{len(diff.new_cycles):<3d} -{len(diff.removed_cycles):<3d}"
    )
    click.echo(
        f"In-degree shifts: {len(diff.in_degree_shifts)}  "
        f"Out-degree shifts: {len(diff.out_degree_shifts)}  "
        f"Layer changes: {len(diff.layer_changes)}  "
        f"Likely moves: {len(diff.likely_moves)}"
    )

    if diff.likely_moves:
        click.echo()
        click.echo("Likely moves:")
        for m in _trim(diff.likely_moves, top):
            click.echo(
                f"  {m['symbol']} ({m.get('kind') or '?'})  {m.get('from_file')} -> {m.get('to_file')}  "
                f"[{m['confidence']}]"
            )

    if diff.in_degree_shifts:
        click.echo()
        click.echo("Top in-degree shifts:")
        for s in _trim(diff.in_degree_shifts, min(10, top or 10)):
            click.echo(f"  {s['symbol']}  {s['before']} -> {s['after']} (delta {s['delta']:+d})")

    if diff.new_cycles:
        click.echo()
        click.echo(f"New cycles ({len(diff.new_cycles)}):")
        for c in _trim(diff.new_cycles, min(5, top or 5)):
            click.echo(f"  size={len(c)}  members={', '.join(c[:5])}{' ...' if len(c) > 5 else ''}")
