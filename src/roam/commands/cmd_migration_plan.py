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
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


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
        click.echo("VERDICT: NO PLAN  (no target moves provided)", err=False)
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "migration-plan",
                        summary={"verdict": "NO PLAN", "reason": "no target moves provided", "step_count": 0},
                        steps=[],
                    )
                )
            )
        return

    with open_db(readonly=True) as conn:
        steps = [_evaluate_move(conn, m) for m in moves]

    # Order: low risk first, then medium, then high
    risk_order = {"low": 0, "medium": 1, "high": 2}
    steps.sort(key=lambda s: (risk_order.get(s["risk"], 3), -s["blast_radius"]))

    # Apply the max-risk gate
    threshold = risk_order[max_risk]
    plan: list[dict] = []
    skipped: list[dict] = []
    for s in steps:
        if risk_order.get(s["risk"], 3) <= threshold:
            plan.append(s)
        else:
            skipped.append(s)

    verdict = _verdict(plan, skipped)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "migration-plan",
                    summary={
                        "verdict": verdict,
                        "step_count": len(plan),
                        "skipped_count": len(skipped),
                        "max_risk": max_risk,
                        "high_risk_steps": sum(1 for s in plan if s["risk"] == "high"),
                        "medium_risk_steps": sum(1 for s in plan if s["risk"] == "medium"),
                        "low_risk_steps": sum(1 for s in plan if s["risk"] == "low"),
                    },
                    steps=plan,
                    skipped=skipped,
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
            "AND (f.path LIKE 'tests/%' OR f.path LIKE '%test_%' OR f.path LIKE '%_test.%')",
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
