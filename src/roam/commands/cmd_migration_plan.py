"""roam migration-plan — generate an ordered migration plan from current state to a target architecture.

Use case: you've decided to split a monolith, extract a service, or reorganise
the layer structure. You want a step-by-step plan with blast-radius + test-
coverage estimates per step, so you can sequence the work safely.

Input: a target-architecture spec in YAML (or inline via flags). Examples:

    target:
      moves:
        - symbol: UserService
          to: src/services/auth/user_service.py
        - symbol: PaymentProcessor
          to: src/services/billing/payment_processor.py
      extractions:
        - file: src/api/users.py
          symbols: [validate_user, hash_password]
          to: src/util/security.py
      layer_constraints:
        - domain  must not depend on  http
        - data    must not depend on  ui

Output: ordered list of operations, each with blast-radius (caller count) and
risk score (high if many callers / no test coverage / cross-layer breakage).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cmd_migration_plan is a recipe generator — it produces
an ordered plan (steps[] with risk + blast_radius metadata) for an
agent to execute, not per-location violations. SARIF is reserved for
scanning findings with file:line coordinates; planning recipes are
operational guidance, not detector output. See action.yml
_SUPPORTED_SARIF allowlist + W1198-audit memo.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json
from roam.output.risk import normalize_risk_level, risk_rank

# ---------------------------------------------------------------------------
# W641-followup-H — canonical risk-LEVEL projection from per-step risk
# ---------------------------------------------------------------------------
#
# cmd_migration_plan is the EIGHTH emitter joining the W641 risk-axis cluster
# after cmd_pr_risk (W641), cmd_impact (W641-followup-A), cmd_critique
# (W641-followup-B), cmd_pr_bundle (W641-followup-C), cmd_attest
# (W641-followup-D), cmd_diff (W641-followup-E), and cmd_dark_matter
# (W641-followup-G). The roam/output/risk.py module docstring explicitly
# cites cmd_migration_plan as the canonical pre-W631 risk-rank polarity
# emitter — this follow-up closes the loop by adding the EMIT side
# (the W631 sort-polarity consumer-side at lines 125 / 138 / 142 already
# landed under W631 task #733).
#
# cmd_migration_plan's per-step risk vocabulary is the 3-tier ``low`` /
# ``medium`` / ``high`` set (lines 248-253 of ``_evaluate_move`` — derived
# from blast-radius + cross-layer signal). It does NOT emit ``critical``
# natively; the PLAN-level rollup picks the worst step's risk via
# max-tier aggregation and floors at ``high`` to stay consistent with the
# rest of the W641 cluster's conservative-on-critical discipline.
#
# Aggregation: **max-tier wins**. The PLAN inherits the worst step's risk
# (mirrors W641-followup-B cmd_critique severity aggregation). Skipped
# steps (above ``--max-risk`` threshold) are EXCLUDED from the aggregation
# because they're not part of the plan; if a user gates at
# ``--max-risk medium``, the plan-level canonical risk SHOULD reflect what
# the plan actually executes, not what was rejected.
#
# Conservative-on-critical: cmd_migration_plan saturates at ``high``
# because its per-step vocabulary tops out at ``high`` and the underlying
# signal is single-axis (blast-radius + cross-layer). ``critical`` is
# reserved for the multi-factor composite-score commands (cmd_attest's
# ``_collect_risk``). The W531 CI-safety lesson: a threshold wobble MUST
# NOT promote a finding into a CI-gating rank.


def _migration_plan_risk_level(
    step_risks: list[str],
    *,
    warnings_out: list[str] | None = None,
) -> str:
    """Project per-step migration risks onto the canonical W631 risk-LEVEL set.

    Aggregation: max-tier wins (the worst step's risk drives the plan's
    risk). Empty plan / no steps safe-floors to ``low`` (W531 CI-safety:
    a no-op migration MUST NOT promote into a gating rank).

    Returns a string in :data:`roam.output.risk.RISK_LEVELS`
    (``critical``/``high``/``medium``/``low``). cmd_migration_plan
    saturates at ``high`` (W641-followup-A/B/E/G discipline — single-axis
    blast-radius + cross-layer signal does not justify escalating to
    ``critical``).

    Unknown / non-list inputs accumulate a marker on *warnings_out* (when
    provided) under ``migration_plan_unknown_severity:<value>`` so
    Pattern-2 silent-fallback stays loud — mirrors the W918 alerts /
    W989 pr-risk / W641-followup-B critique / W641-followup-D attest /
    W641-followup-E diff / W641-followup-G dark-matter discipline.
    """
    if not isinstance(step_risks, list):
        if warnings_out is not None:
            warnings_out.append(f"migration_plan_unknown_severity:non_list({step_risks!r})")
        return "low"
    if not step_risks:
        return "low"

    max_rank = 0
    saw_unknown = False
    for risk in step_risks:
        canonical = normalize_risk_level(risk)
        if canonical is None:
            saw_unknown = True
            continue
        r = risk_rank(canonical)
        if r > max_rank:
            max_rank = r

    if saw_unknown and warnings_out is not None:
        warnings_out.append(f"migration_plan_unknown_severity:unknown_token({step_risks!r})")

    # Conservative-on-critical: cmd_migration_plan's per-step vocabulary
    # tops out at ``high``; saturate the rollup at ``high`` even if a
    # downstream rename introduces ``critical`` upstream of this helper.
    if max_rank >= 3:
        return "high"
    if max_rank >= 2:
        return "medium"
    if max_rank >= 1:
        return "low"
    # All-unknown path lands here (max_rank stayed 0). Safe-floor to low.
    return "low"


@roam_capability(
    name="migration-plan",
    category="workflow",
    summary="Generate an ordered migration plan with risk + blast-radius per step",
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
@click.command(name="migration-plan")
@click.option(
    "--target",
    "target_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a target-architecture spec (YAML). See module docstring for shape.",
)
@click.option(
    "--move",
    "moves_inline",
    multiple=True,
    help="Inline move directive: SYMBOL=path/to/new/file.py. Repeatable. Use instead of or in addition to --target.",
)
@click.option(
    "--max-risk",
    type=click.Choice(["low", "medium", "high"]),
    default="high",
    show_default=True,
    help="Stop the plan once the next step exceeds this risk threshold.",
)
@click.pass_context
def migration_plan_cmd(ctx, target_path: str | None, moves_inline: tuple[str, ...], max_risk: str) -> None:
    """Generate an ordered migration plan with risk + blast-radius per step.

    Reads the current symbol/file/edge state from the index, applies the
    target spec to compute a delta, and orders the operations so low-risk
    steps go first. Each operation is annotated with caller count and a
    derived risk score so you can decide where to stop or insert tests.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    moves = _parse_target_spec(target_path, moves_inline)
    if not moves:
        # JSON mode emits only the envelope (no text noise contaminating
        # stdout for Cloud Lite / MCP / agent consumers). Text mode emits
        # the human-readable line.
        if json_mode:
            # W641-followup-H — empty plan safe-floors to canonical ``low``
            # so consumers downstream can call ``risk_rank(...)``
            # unconditionally (parity with W641-followup-D/E/G).
            _empty_canonical = "low"
            _empty_rank = risk_rank(_empty_canonical)
            click.echo(
                to_json(
                    json_envelope(
                        "migration-plan",
                        summary={
                            "verdict": f"NO PLAN (risk_level {_empty_canonical})",
                            "reason": "no target moves provided",
                            "step_count": 0,
                            # W641-followup-H — canonical W631 risk-LEVEL +
                            # integer rank. Empty plan floors to ``low``.
                            "risk_level_canonical": _empty_canonical,
                            "risk_rank": _empty_rank,
                        },
                        steps=[],
                        # W641-followup-H — top-level mirror of summary
                        # canonical fields so consumers reading the envelope
                        # head without descending into ``summary`` see the
                        # canonical bucket too (parity with cmd_impact /
                        # cmd_critique / cmd_attest / cmd_diff / cmd_dark_matter).
                        risk_level_canonical=_empty_canonical,
                        risk_rank=_empty_rank,
                    )
                )
            )
        else:
            click.echo("VERDICT: NO PLAN  (no target moves provided)")
        return

    with open_db(readonly=True) as conn:
        steps = [_evaluate_move(conn, m) for m in moves]

    # Order: low risk first, then medium, then high; unknown last.
    # W631: risk_rank polarity is "higher = worse" (critical=4, high=3,
    # medium=2, low=1, unknown=-1). Pre-W631 unknown sorted LAST (the
    # local table defaulted unknowns to 3, one past high=2); preserve
    # that by treating rank<0 as a large sentinel for ordering.
    def _order_key(risk: str) -> int:
        r = risk_rank(risk)
        return r if r >= 0 else 999

    steps.sort(key=lambda s: (_order_key(s["risk"]), -s["blast_radius"]))

    # Apply the max-risk gate. ``max_risk`` is one of {low, medium, high}
    # (validated by Click); risk_rank(max_risk) is in {1, 2, 3}. A step
    # passes the gate when its (known) rank is at-or-below the threshold;
    # unknown risk (rank -1) is excluded by the ``0 < s_rank`` clause,
    # matching the pre-W631 ``risk_order.get(..., 3) <= threshold`` polarity
    # which placed unknown above every valid max-risk and therefore
    # skipped it for low/medium gates and, for high gate, ``3<=2`` was
    # False so unknown skipped there too.
    threshold_rank = risk_rank(max_risk)
    plan: list[dict] = []
    skipped: list[dict] = []
    for s in steps:
        s_rank = risk_rank(s["risk"])
        if 0 < s_rank <= threshold_rank:
            plan.append(s)
        else:
            skipped.append(s)

    verdict = _verdict(plan, skipped)

    if json_mode:
        # W641-followup-H — canonical W631 risk-LEVEL projection from the
        # plan's per-step risks. Aggregation is max-tier wins (the worst
        # step's risk drives the plan's risk). Skipped steps are EXCLUDED
        # from the aggregation: the canonical bucket reflects what the
        # plan actually executes, not what was rejected by --max-risk.
        # Cross-command consumers can compare e.g.
        # ``risk_rank(summary.risk_level_canonical) >= 3`` to gate on
        # high-or-worse plans without re-deriving the threshold table.
        _mp_warnings_out: list[str] = []
        _mp_step_risks = [s["risk"] for s in plan]
        _mp_domain_level = _migration_plan_risk_level(
            _mp_step_risks,
            warnings_out=_mp_warnings_out,
        )
        risk_level_canonical = normalize_risk_level(_mp_domain_level) or "low"
        risk_rank_int = risk_rank(risk_level_canonical)

        # Verdict augmentation: append the canonical bucket so LAW 6
        # standalone-parse holds — an agent reading just the verdict line
        # can call ``risk_rank`` on the parenthesised token without
        # consulting any other envelope field. Mirrors the W641-followup-
        # A/B/C/D/E/G verdict-augmentation contract.
        verdict_augmented = f"{verdict} (risk_level {risk_level_canonical})"

        # Stamp canonical risk_level_canonical onto each step too so a
        # downstream consumer iterating ``summary.steps[]`` can call
        # ``risk_rank(step["risk_level_canonical"])`` without re-normalising
        # the per-step ``risk`` token. The pre-existing per-step ``risk``
        # field is preserved verbatim so the regression contract (text
        # render, sort polarity, --max-risk gate) stays intact.
        plan_with_canonical = [
            {
                **s,
                "risk_level_canonical": normalize_risk_level(s["risk"]) or "low",
                "risk_rank": risk_rank(normalize_risk_level(s["risk"]) or "low"),
            }
            for s in plan
        ]
        skipped_with_canonical = [
            {
                **s,
                "risk_level_canonical": normalize_risk_level(s["risk"]) or "low",
                "risk_rank": risk_rank(normalize_risk_level(s["risk"]) or "low"),
            }
            for s in skipped
        ]

        _summary: dict = {
            "verdict": verdict_augmented,
            "step_count": len(plan),
            "skipped_count": len(skipped),
            "max_risk": max_risk,
            "high_risk_steps": sum(1 for s in plan if s["risk"] == "high"),
            "medium_risk_steps": sum(1 for s in plan if s["risk"] == "medium"),
            "low_risk_steps": sum(1 for s in plan if s["risk"] == "low"),
            # Pattern 2: when the gate skipped moves, the user's
            # intent was only partially honoured. Disclose it
            # explicitly so agents don't read step_count=N as
            # "all N moves planned".
            "partial_success": bool(skipped),
            # Pattern 1D: closed-enum risk-level summary of what
            # actually emerges, so the verdict doesn't have to
            # carry every detail.
            "risk_definition": "max(callers,cross_layer) -> low|medium|high",
            # W641-followup-H — canonical W631 risk-LEVEL + integer rank.
            # Projected from per-step risks via ``_migration_plan_risk_level``
            # (Pattern-3a structural close-out, eighth axis after W641 +
            # followup-A/B/C/D/E/G).
            "risk_level_canonical": risk_level_canonical,
            "risk_rank": risk_rank_int,
        }
        # Surface Pattern-2 silent-fallback markers (unknown / non-list
        # inputs). Empty list omitted to keep the envelope tight.
        if _mp_warnings_out:
            _summary["warnings_out"] = list(_mp_warnings_out)

        click.echo(
            to_json(
                json_envelope(
                    "migration-plan",
                    summary=_summary,
                    steps=plan_with_canonical,
                    skipped=skipped_with_canonical,
                    # W641-followup-H — top-level mirror of summary
                    # canonical fields so consumers reading the envelope
                    # head without descending into ``summary`` see the
                    # canonical bucket too (parity with cmd_impact /
                    # cmd_critique / cmd_attest / cmd_diff / cmd_dark_matter).
                    risk_level_canonical=risk_level_canonical,
                    risk_rank=risk_rank_int,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(f"  {len(plan)} steps in plan, {len(skipped)} skipped (above max-risk={max_risk})")
    click.echo("")
    for i, s in enumerate(plan, start=1):
        click.echo(f"  {i:2}. [{s['risk'].upper():6}] {s['op']:8} {s['symbol']:32} → {s['target']}")
        if s.get("blast_radius", 0) > 0:
            click.echo(f"        ↳ {s['blast_radius']} caller(s); {s['notes']}")
        else:
            click.echo(f"        ↳ {s['notes']}")
    if skipped:
        click.echo("")
        click.echo("  Skipped (above max-risk threshold):")
        for s in skipped:
            click.echo(f"    [{s['risk'].upper():6}] {s['op']:8} {s['symbol']}  ({s['blast_radius']} callers)")


def _parse_target_spec(path: str | None, inline_moves: tuple[str, ...]) -> list[dict]:
    """Parse the target spec from a file + inline --move directives.

    Returns a flat list of move operations. Each: {op, symbol, target, kind}.
    """
    moves: list[dict] = []

    if path:
        text = Path(path).read_text(encoding="utf-8")
        # Minimal YAML reader: looks for `- symbol: NAME` + `to: path` pairs
        # under a top-level `moves:` key. Avoids importing PyYAML.
        in_moves = False
        current: dict[str, str] = {}
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped.startswith("moves:"):
                in_moves = True
                continue
            if in_moves and stripped.startswith("- symbol:"):
                if current:
                    moves.append({"op": "move", "kind": "symbol", **current})
                current = {"symbol": stripped.split(":", 1)[1].strip()}
            elif in_moves and stripped.startswith("to:"):
                current["target"] = stripped.split(":", 1)[1].strip()
            elif stripped.endswith(":") and not stripped.startswith("- "):
                if current:
                    moves.append({"op": "move", "kind": "symbol", **current})
                    current = {}
                in_moves = False
        if current:
            moves.append({"op": "move", "kind": "symbol", **current})

    for raw in inline_moves:
        if "=" not in raw:
            continue
        sym, dst = raw.split("=", 1)
        moves.append({"op": "move", "kind": "symbol", "symbol": sym.strip(), "target": dst.strip()})

    return moves


def _evaluate_move(conn: sqlite3.Connection, move: dict) -> dict:
    """Compute blast-radius + risk for a single move operation."""
    sym = move.get("symbol", "")
    target = move.get("target", "")
    blast = _caller_count(conn, sym)
    has_tests = _has_test_coverage(conn, sym)
    cross_layer = _is_cross_layer(conn, sym, target)

    if blast >= 50 or (cross_layer and blast >= 10):
        risk = "high"
    elif blast >= 10 or cross_layer:
        risk = "medium"
    else:
        risk = "low"

    notes = []
    if blast > 0:
        notes.append(f"{blast} call site(s) need updating")
    if not has_tests:
        notes.append("no direct test coverage detected")
    if cross_layer:
        notes.append("crosses architectural layer")
    if not notes:
        notes.append("low-impact move")

    return {
        "op": move.get("op", "move"),
        "symbol": sym,
        "target": target,
        "blast_radius": blast,
        "has_tests": has_tests,
        "cross_layer": cross_layer,
        "risk": risk,
        "notes": "; ".join(notes),
    }


def _caller_count(conn: sqlite3.Connection, symbol_name: str) -> int:
    """Count edges where this symbol is the destination."""
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM edges e "
            "JOIN symbols s ON s.id = e.dst_symbol_id "
            "WHERE s.qualified_name = ? OR s.name = ?",
            (symbol_name, symbol_name),
        )
        return int(cur.fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _has_test_coverage(conn: sqlite3.Connection, symbol_name: str) -> bool:
    """True if any test-file caller exists for this symbol."""
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM edges e "
            "JOIN symbols s ON s.id = e.dst_symbol_id "
            "JOIN symbols src ON src.id = e.src_symbol_id "
            "JOIN files f ON f.id = src.file_id "
            "WHERE (s.qualified_name = ? OR s.name = ?) "
            "AND (f.path LIKE 'tests/%' OR f.path LIKE '%test\\_%' ESCAPE '\\' OR f.path LIKE '%\\_test.%' ESCAPE '\\')",
            (symbol_name, symbol_name),
        )
        return int(cur.fetchone()[0]) > 0
    except sqlite3.OperationalError:
        return False


_LAYER_PATTERNS = {
    "ui": re.compile(r"(?:^|/)(?:ui|views?|templates?)(?:/|$)", re.IGNORECASE),
    "http": re.compile(r"(?:^|/)(?:api|controllers?|routes?|handlers?)(?:/|$)", re.IGNORECASE),
    "domain": re.compile(r"(?:^|/)(?:domain|services?|core)(?:/|$)", re.IGNORECASE),
    "data": re.compile(r"(?:^|/)(?:db|data|repository|repositories|models?)(?:/|$)", re.IGNORECASE),
}


def _is_cross_layer(conn: sqlite3.Connection, symbol_name: str, target_path: str) -> bool:
    """Check if the target path is in a different architectural layer than the source."""
    try:
        cur = conn.execute(
            "SELECT f.path FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.qualified_name = ? OR s.name = ? LIMIT 1",
            (symbol_name, symbol_name),
        )
        row = cur.fetchone()
        if row is None:
            return False
        src_path = row[0] or ""
    except sqlite3.OperationalError:
        return False

    src_layer = _classify_layer(src_path)
    tgt_layer = _classify_layer(target_path)
    return src_layer is not None and tgt_layer is not None and src_layer != tgt_layer


def _classify_layer(path: str) -> str | None:
    for layer, pat in _LAYER_PATTERNS.items():
        if pat.search(path):
            return layer
    return None


def _verdict(plan: list[dict], skipped: list[dict]) -> str:
    """One-line verdict from the plan."""
    if not plan and not skipped:
        return "NO PLAN"
    if not plan:
        return "ALL HIGH RISK"
    high = sum(1 for s in plan if s["risk"] == "high")
    if high > 0:
        return f"PROCEED WITH CARE  ({high} high-risk step(s) included)"
    medium = sum(1 for s in plan if s["risk"] == "medium")
    if medium > 0:
        return f"PROCEED  ({medium} medium-risk step(s); rest low)"
    return "PROCEED  (all low-risk)"
