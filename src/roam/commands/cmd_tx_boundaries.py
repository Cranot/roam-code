"""``roam tx-boundaries`` — classify functions by transactional safety.

Builds on :func:`roam.world_model.tx_boundaries.classify_tx_boundaries`,
which composes on top of the side-effects detector.

R28 sub-feature 4 of 4 (shipped in W15.4 — final world-model feature).
Heuristic detector — false negatives expected (we don't cover every ORM
idiom), false positives possible (e.g. a function named
``commit_changes`` may match the ``commit()`` pattern without performing
a DB commit).

Examples
--------
    roam tx-boundaries                                    # scan all, top 30
    roam tx-boundaries handleSave                         # one symbol
    roam tx-boundaries --classification unsafe_mutation   # filter
    roam tx-boundaries --classification unmatched_begin --top 10
    roam tx-boundaries --json

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because tx-boundaries outputs are invocation-scoped per-
symbol transaction classification rollups (idempotent / non-
idempotent / unsafe_mutation / unmatched_begin / unmatched_commit) —
not per-location code violations. The classification is descriptive
metadata about transactional safety, paralleling
``cmd_side_effects`` + ``cmd_idempotency`` (the sibling world-model
classifiers). See ``cmd_idempotency`` for the parallel per-symbol
classification disclosure pattern (W1224) + action.yml
_SUPPORTED_SARIF allowlist + W1224-audit memo.
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
from roam.world_model.tx_boundaries import (
    TX_CLASSIFICATIONS,
    classify_tx_boundaries,
)

# Ranked by severity for sorting / verdict prioritisation.
# unmatched_* are bugs; unsafe_mutation is a latent bug; partial_* is a
# smell; transactional / non_transactional are clean; unknown is a gap.
_SEVERITY_RANK: dict[str, int] = {
    "unmatched_begin": 6,
    "unmatched_commit": 5,
    "unsafe_mutation": 4,
    "partial_transactional": 3,
    "unknown": 2,
    "transactional": 1,
    "non_transactional": 0,
}

# Subset that contributes to the "high_severity_count" summary scalar —
# anything that's an outright bug OR a latent bug worth surfacing.
_HIGH_SEVERITY_KINDS = frozenset({"unmatched_begin", "unmatched_commit", "unsafe_mutation"})


@roam_capability(
    name="tx-boundaries",
    category="architecture",
    summary=("Classify functions by transactional safety (transactional / unsafe_mutation / unmatched_begin / ...)"),
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("tx-boundaries")
@click.argument("symbol", required=False, default=None)
@click.option(
    "--classification",
    type=click.Choice(TX_CLASSIFICATIONS, case_sensitive=False),
    default=None,
    help="Filter to one classification (e.g. unsafe_mutation, unmatched_begin).",
)
@click.option(
    "--top",
    type=int,
    default=30,
    help="Limit the number of boundaries surfaced (default: 30).",
)
@click.pass_context
def tx_boundaries_cmd(ctx, symbol, classification, top):
    """Classify functions by transactional safety.

    Each function with side effects is bucketed:

    \b
      transactional         — begin matched by commit/rollback; all
                              mutations are inside the scope.
      partial_transactional — has a transaction but mutations occur
                              both inside AND outside the scope.
      unsafe_mutation       — performs mutations OUTSIDE any
                              transaction wrapper (latent bug).
      unmatched_begin       — begin without commit/rollback (leak).
      unmatched_commit      — commit/rollback without preceding begin.
      non_transactional     — no mutations, no transaction markers.
      unknown               — body unreadable / file missing.

    Composes on top of ``roam side-effects`` for the underlying
    mutation classification.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    try:
        repo_root = find_project_root()
    except Exception:
        repo_root = None

    with open_db(readonly=True) as conn:
        all_results = classify_tx_boundaries(conn, symbol_name=symbol)

    # Restrict the by-classification rollup to functions whose body is
    # NOT clean — i.e. "Classified N functions with mutations" should
    # not count thousands of pure helpers as "non_transactional".
    mutating_or_marked = [
        c
        for c in all_results
        if c.classification != "non_transactional" or c.begin_markers or c.commit_markers or c.rollback_markers
    ]

    filtered = mutating_or_marked
    if classification:
        target = classification.lower()
        filtered = [c for c in all_results if c.classification == target]

    by_classification: Counter = Counter(c.classification for c in all_results)

    # Verdict prioritises the high-severity bucket — pattern from
    # internal/dogfood/SYNTHESIS-2026-05-12.md (LAW 6: compression forces
    # domain neutrality — verdict must stand on its own).
    high_sev = sum(by_classification.get(k, 0) for k in _HIGH_SEVERITY_KINDS)

    if symbol and not filtered:
        verdict = f"No function/method/constructor named '{symbol}' classified."
        state = "no_data"
        partial_success = True
    elif not all_results:
        verdict = "No symbols available to classify (run `roam index`)."
        state = "no_data"
        partial_success = True
    else:
        # Compose the verdict from the interesting buckets only.
        parts = []
        for k in (
            "transactional",
            "unsafe_mutation",
            "unmatched_begin",
            "unmatched_commit",
            "partial_transactional",
        ):
            n = by_classification.get(k, 0)
            if n:
                parts.append(f"{n} {k}")
        n_mutating = len(mutating_or_marked)
        verdict = (
            f"Classified {n_mutating} functions with mutations: " + ", ".join(parts)
            if parts
            else f"Classified {n_mutating} functions; none transactional"
        )
        state = "ok"
        partial_success = False

    # Rank for surfacing: severity desc, then confidence, then file len.
    # W596: canonical confidence-LEVEL rank — higher = more confident.
    def _key(c):
        return (
            _SEVERITY_RANK.get(c.classification, 0),
            confidence_level_rank(c.confidence, fallback=-1),
            -len(c.file or ""),
        )

    sorted_filtered = sorted(filtered, key=_key, reverse=True)
    if top and top > 0:
        surfaced = sorted_filtered[:top]
    else:
        surfaced = sorted_filtered

    # LAW 4: concrete-noun-anchored facts. Anchor on the highest-severity
    # individual symbol when available.
    facts: list[str] = []
    worst = sorted_filtered[0] if sorted_filtered else None
    if worst is not None and worst.classification in _HIGH_SEVERITY_KINDS:
        facts.append(
            f"{worst.symbol} classified {worst.classification} "
            f"(mutations_outside={worst.mutations_outside}, "
            f"confidence={worst.confidence})"
        )
    if by_classification.get("unsafe_mutation"):
        facts.append(f"tx-boundaries flagged {by_classification['unsafe_mutation']} unsafe_mutation symbols")
    if by_classification.get("unmatched_begin"):
        facts.append(
            f"tx-boundaries flagged {by_classification['unmatched_begin']} unmatched_begin symbols (transaction leak)"
        )
    if by_classification.get("unmatched_commit"):
        facts.append(
            f"tx-boundaries flagged {by_classification['unmatched_commit']} unmatched_commit symbols (stray commit)"
        )
    if by_classification.get("partial_transactional"):
        facts.append(
            f"tx-boundaries flagged {by_classification['partial_transactional']} partial_transactional symbols"
        )
    if by_classification.get("transactional"):
        facts.append(f"tx-boundaries confirmed {by_classification['transactional']} transactional symbols")
    if not facts:
        facts.append("tx-boundaries scan found no functions to classify")

    next_commands: list[str] = []
    if by_classification.get("unsafe_mutation") and (not classification or classification != "unsafe_mutation"):
        next_commands.append("roam tx-boundaries --classification unsafe_mutation --top 10")
    if by_classification.get("unmatched_begin") and (not classification or classification != "unmatched_begin"):
        next_commands.append("roam tx-boundaries --classification unmatched_begin --top 10")
    if by_classification.get("unmatched_commit") and (not classification or classification != "unmatched_commit"):
        next_commands.append("roam tx-boundaries --classification unmatched_commit --top 10")
    next_commands.append("roam side-effects --kind io_write --top 20")

    envelope = json_envelope(
        "tx-boundaries",
        summary={
            "verdict": verdict,
            "state": state,
            "partial_success": partial_success,
            "by_classification": dict(by_classification),
            "total_classified": len(all_results),
            "surfaced": len(surfaced),
            "filter_classification": classification,
            "high_severity_count": high_sev,
            # Pattern 3: metric definition co-located with the field.
            "classification_definition": (
                "per-function transaction scope: "
                "transactional | partial_transactional | unsafe_mutation | "
                "unmatched_begin | unmatched_commit | non_transactional | unknown. "
                "Heuristic begin/commit/rollback markers + side-effects-derived "
                "mutation count; depth tracked by `with`-block indent."
            ),
            "detector": "world_model.tx_boundaries (heuristic)",
        },
        boundaries=[c.to_dict() for c in surfaced],
        agent_contract={
            "facts": facts,
            "next_commands": next_commands,
        },
    )

    auto_log(envelope, action="tx-boundaries", target=symbol or "", repo_root=repo_root)

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
                c.classification,
                f"{c.mutations_inside}/{c.mutations_outside}",
                c.confidence,
                (c.file or "")[-46:],
            ]
        )
    click.echo(
        format_table(
            ["Symbol", "Classification", "Mut(in/out)", "Conf", "File"],
            rows,
        )
    )
    if len(filtered) > len(surfaced):
        click.echo(f"\n(+{len(filtered) - len(surfaced)} more; --top {len(filtered)} to surface all)")


__all__ = ["tx_boundaries_cmd"]
