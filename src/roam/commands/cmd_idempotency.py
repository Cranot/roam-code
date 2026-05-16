"""``roam idempotency`` — is this symbol safe to call twice?

Builds on :func:`roam.world_model.idempotency.classify_idempotency`,
which composes on top of the side-effects detector.

Heuristic detector — false negatives expected, false positives should
be rare.

Examples
--------
    roam idempotency                              # scan all, top 50 by interest
    roam idempotency handleSave                   # one symbol
    roam idempotency --kind non_idempotent         # filter
    roam idempotency --kind non_idempotent --top 20
    roam idempotency --json

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because idempotency outputs are invocation-scoped per-symbol
classification rollups (idempotent / non_idempotent / unknown labels) —
not per-location code violations. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH propagation plan + W1224-audit memo.
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
from roam.world_model.idempotency import IDEMPOTENCY_KINDS, classify_idempotency


@roam_capability(
    name="idempotency",
    category="architecture",
    summary="Classify symbols by idempotency (idempotent / non_idempotent / unknown)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("idempotency")
@click.argument("symbol", required=False, default=None)
@click.option(
    "--kind",
    type=click.Choice(IDEMPOTENCY_KINDS, case_sensitive=False),
    default=None,
    help="Filter classifications by idempotency kind.",
)
@click.option(
    "--top",
    type=int,
    default=50,
    help="Limit the number of classifications surfaced (default: 50).",
)
@click.pass_context
def idempotency_cmd(ctx, symbol, kind, top):
    """Classify symbols by idempotency (idempotent / non_idempotent / unknown).

    Composes on top of ``roam side-effects``:

    \b
      idempotent     — pure functions, read-only I/O, write-with-check
                        patterns (mkdir(exist_ok=True), INSERT OR IGNORE,
                        UPSERT, if not exists: create).
      non_idempotent — naive writes, mutations, appends.
      unknown        — process spawn or anything we can't reason about.

    Useful for retry decisions and replay safety (R20).  Cross-reference
    ``roam side-effects`` for the underlying classification.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    try:
        repo_root = find_project_root()
    except Exception:
        repo_root = None

    with open_db(readonly=True) as conn:
        all_results = classify_idempotency(conn, symbol_name=symbol)

    filtered = all_results
    if kind:
        target = kind.lower()
        filtered = [c for c in all_results if c.kind == target]

    by_kind: Counter = Counter(c.kind for c in all_results)

    if symbol and not filtered:
        verdict = f"No function/method/constructor named '{symbol}' classified."
        partial_success = True
    elif not all_results:
        verdict = "No symbols available to classify (run `roam index`)."
        partial_success = True
    else:
        parts = []
        for k in IDEMPOTENCY_KINDS:
            n = by_kind.get(k, 0)
            if n:
                parts.append(f"{n} {k}")
        verdict = f"Classified {len(all_results)} symbols: " + ", ".join(parts)
        partial_success = False

    # Interest ranking — surface non_idempotent first (highest retry risk).
    # W596: confidence-LEVEL rank uses the canonical helper (higher = more confident).
    _INTEREST = {"non_idempotent": 3, "unknown": 2, "idempotent": 1}

    def _key(c):
        return (
            _INTEREST.get(c.kind, 0),
            confidence_level_rank(c.confidence, fallback=-1),
            -len(c.file or ""),
        )

    sorted_filtered = sorted(filtered, key=_key, reverse=True)
    if top and top > 0:
        surfaced = sorted_filtered[:top]
    else:
        surfaced = sorted_filtered

    # LAW 4 (CLAUDE.md): anchor on a concrete subject ("idempotency scan")
    # with an analytical verb, not a bare numeric prefix. Surface the worst
    # individual symbol first if we have one — concrete-noun anchoring beats
    # category counts when both are available.
    facts: list[str] = []
    worst = sorted_filtered[0] if sorted_filtered else None
    if worst is not None and worst.kind in ("non_idempotent", "unknown"):
        facts.append(f"{worst.symbol} classified {worst.kind} (confidence={worst.confidence})")
    if by_kind.get("non_idempotent", 0):
        facts.append(
            f"idempotency scan flagged {by_kind['non_idempotent']} non_idempotent "
            f"symbols out of {len(all_results)} analysed"
        )
    if by_kind.get("unknown", 0):
        facts.append(f"idempotency scan classified {by_kind['unknown']} symbols as unknown (retry-risk indeterminate)")
    if by_kind.get("idempotent", 0):
        facts.append(f"idempotency scan confirmed {by_kind['idempotent']} idempotent symbols")
    if not facts:
        facts.append("idempotency scan found no symbols to classify")

    next_commands: list[str] = []
    if by_kind.get("non_idempotent", 0) and (not kind or kind != "non_idempotent"):
        next_commands.append("roam idempotency --kind non_idempotent --top 20")
    next_commands.append("roam side-effects")

    envelope = json_envelope(
        "idempotency",
        summary={
            "verdict": verdict,
            "state": "ok" if not partial_success else "no_data",
            "partial_success": partial_success,
            "by_kind": dict(by_kind),
            "total_classified": len(all_results),
            "surfaced": len(surfaced),
            "filter_kind": kind,
            "kind_definition": (
                "idempotent | non_idempotent | unknown — composes on world_model.side_effects classification"
            ),
            "detector": "world_model.idempotency (heuristic)",
        },
        classifications=[c.to_dict() for c in surfaced],
        agent_contract={
            "facts": facts,
            "next_commands": next_commands,
        },
    )

    auto_log(envelope, action="idempotency", target=symbol or "", repo_root=repo_root)

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if not surfaced:
        return
    rows = []
    for c in surfaced:
        rows.append(
            [
                c.symbol[:42],
                c.kind,
                c.confidence,
                (c.file or "")[-46:],
            ]
        )
    click.echo(
        format_table(
            ["Symbol", "Idempotency", "Conf", "File"],
            rows,
        )
    )
    if len(filtered) > len(surfaced):
        click.echo(f"\n(+{len(filtered) - len(surfaced)} more; --top {len(filtered)} to surface all)")


__all__ = ["idempotency_cmd"]
