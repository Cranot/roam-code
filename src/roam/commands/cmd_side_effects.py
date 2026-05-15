"""``roam side-effects`` — coarse, agent-friendly side-effects classifier.

Builds on the world-model detector at :mod:`roam.world_model.side_effects`.
Complements ``roam effects`` (finer 11-kind taxonomy + transitive
propagation): this command is the **agent decision surface** — five
buckets, one verdict, ready to drop into a PR-bundle risks block.

Heuristic detector — false negatives expected, false positives should
be rare.

Examples
--------
    roam side-effects                        # scan all, top 50 by interest
    roam side-effects handleSave             # one symbol
    roam side-effects --kind io_write        # filter by kind
    roam side-effects --kind io_write --top 20
    roam side-effects --json
"""

from __future__ import annotations

from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.helpers import auto_log
from roam.world_model.side_effects import SIDE_EFFECT_KINDS, classify_side_effects


@roam_capability(
    name="side-effects",
    category="architecture",
    summary="Classify symbols by their side effects (none / io_read / io_write / mutation / process / unknown)",
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
@click.command("side-effects")
@click.argument("symbol", required=False, default=None)
@click.option(
    "--kind",
    type=click.Choice(SIDE_EFFECT_KINDS, case_sensitive=False),
    default=None,
    help="Filter classifications by side-effect kind.",
)
@click.option(
    "--top",
    type=int,
    default=50,
    help="Limit the number of classifications surfaced (default: 50).",
)
@click.pass_context
def side_effects_cmd(ctx, symbol, kind, top):
    """Classify symbols by their side effects (none / io_read / io_write / mutation / process / unknown).

    Coarse classification designed for agent decisions:

    \b
      none      — pure function
      io_read   — reads from disk / network / DB
      io_write  — writes to disk / network / DB
      mutation  — mutates global / module state
      process   — spawns subprocess / threads / async
      unknown   — couldn't analyze (signal exists, evidence does not)

    For finer per-effect transitive propagation, use ``roam effects``.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    try:
        repo_root = find_project_root()
    except Exception:
        repo_root = None

    with open_db(readonly=True) as conn:
        all_results = classify_side_effects(conn, symbol_name=symbol)

    # Optional kind filter — keep classifications that include that kind.
    filtered = all_results
    if kind:
        target = kind.lower()
        filtered = [c for c in all_results if target in c.kinds]

    # Aggregate counters (over UN-filtered list, since this is the
    # snapshot of the whole codebase; the verdict reports the real
    # global distribution even when the user filtered the table).
    by_kind: Counter = Counter()
    for c in all_results:
        for k in c.kinds:
            by_kind[k] += 1

    # Build verdict.
    if symbol and not filtered:
        verdict = f"No function/method/constructor named '{symbol}' classified."
        partial_success = True
    elif not all_results:
        verdict = "No symbols available to classify (run `roam index`)."
        partial_success = True
    else:
        parts = []
        for k in SIDE_EFFECT_KINDS:
            n = by_kind.get(k, 0)
            if n:
                parts.append(f"{n} {k}")
        verdict = f"Classified {len(all_results)} symbols: " + ", ".join(parts)
        partial_success = False

    # Rank classifications:
    #  1. confidence high > medium > low
    #  2. kinds containing io_write / process > io_read / mutation > none / unknown
    #  3. shorter file path (favours canonical src/ paths)
    _KIND_INTEREST = {
        "process": 5,
        "io_write": 4,
        "mutation": 3,
        "io_read": 2,
        "unknown": 1,
        "none": 0,
    }
    # W596: canonical confidence-LEVEL rank — higher = more confident.
    def _interest(c):
        return (
            confidence_level_rank(c.confidence, fallback=-1),
            max((_KIND_INTEREST.get(k, 0) for k in c.kinds), default=0),
            -len(c.file or ""),
        )

    sorted_filtered = sorted(filtered, key=_interest, reverse=True)
    if top and top > 0:
        surfaced = sorted_filtered[:top]
    else:
        surfaced = sorted_filtered

    # Build agent_contract facts.
    # LAW 4 (CLAUDE.md): anchor on a concrete subject ("side-effects scan")
    # with an analytical verb, not bare "{N} {kind} symbols" counts. Surface
    # the highest-confidence individual symbol first when available.
    facts: list[str] = []
    worst = sorted_filtered[0] if sorted_filtered else None
    if worst is not None and worst.kinds and "none" not in worst.kinds:
        kind_str = "+".join(sorted(worst.kinds))
        facts.append(
            f"{worst.symbol} classified {kind_str} "
            f"(confidence={worst.confidence})"
        )
    for k in ("io_write", "process", "mutation", "io_read"):
        n = by_kind.get(k, 0)
        if n:
            facts.append(
                f"side-effects scan classified {n} symbols as {k} "
                f"out of {len(all_results)} analysed"
            )
    pure_n = by_kind.get("none", 0)
    if pure_n:
        facts.append(
            f"side-effects scan confirmed {pure_n} symbols are pure "
            "(no detected side effects)"
        )
    if not facts:
        facts.append("side-effects scan found no symbols to classify")

    next_commands: list[str] = []
    if by_kind.get("io_write", 0) and (not kind or kind != "io_write"):
        next_commands.append("roam side-effects --kind io_write --top 20")
    if by_kind.get("process", 0) and (not kind or kind != "process"):
        next_commands.append("roam side-effects --kind process --top 20")
    next_commands.append("roam idempotency --kind non_idempotent --top 20")

    envelope = json_envelope(
        "side-effects",
        summary={
            "verdict": verdict,
            "state": "ok" if not partial_success else "no_data",
            "partial_success": partial_success,
            "by_kind": dict(by_kind),
            "total_classified": len(all_results),
            "surfaced": len(surfaced),
            "filter_kind": kind,
            "kind_definition": (
                "coarse-grained agent-facing taxonomy: "
                "none|io_read|io_write|mutation|process|unknown"
            ),
            "detector": "world_model.side_effects (heuristic)",
        },
        classifications=[c.to_dict() for c in surfaced],
        agent_contract={
            "facts": facts,
            "next_commands": next_commands,
        },
    )

    # Auto-log into the active R20 run (silent no-op when no run is active).
    auto_log(envelope, action="side-effects", target=symbol or "", repo_root=repo_root)

    if json_mode:
        click.echo(to_json(envelope))
        return

    # Text output.
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if not surfaced:
        return
    rows = []
    for c in surfaced:
        kinds_str = ",".join(c.kinds) if c.kinds else "-"
        rows.append([
            c.symbol[:42],
            kinds_str,
            c.confidence,
            (c.file or "")[-46:],
        ])
    click.echo(
        format_table(
            ["Symbol", "Kinds", "Conf", "File"],
            rows,
        )
    )
    if len(filtered) > len(surfaced):
        click.echo(f"\n(+{len(filtered) - len(surfaced)} more; --top {len(filtered)} to surface all)")


__all__ = ["side_effects_cmd"]
