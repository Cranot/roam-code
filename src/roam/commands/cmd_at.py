"""``roam at <file:line>`` — show the code AT a location with graph context.

SARIF is deliberately NOT emitted: returns a code slice + enclosing symbol (a location view), not a line-level findings stream mappable to SARIF result objects.

The inverse of ``roam search`` (symbol → location). Agents constantly do
"I have a file:line, show me the code there" via ``Read`` (whole file) or a
grep. ``roam at`` is the targeted version and adds what Read cannot: the
ENCLOSING SYMBOL (which function/class contains this line) and, with
``--callers``, who calls it.

Motivated by 2026-06-02 production telemetry: roam_file_info had a 35%
fallback rate, 76% of which were Read-whole-file. A location-scoped reader
with structural context serves that need directly.

    roam at src/roam/cli.py:42
    roam at src/roam/plan/compiler.py:6730 --context 8 --callers

Output (``--json``) carries ``location``, ``enclosing_symbol``,
``code`` (the ±context slice with a ``>>`` marker on the target line), and
optionally ``callers``. LAW-4-anchored facts.
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, loc, to_json


def _read_slice(rel_path: str, line_no: int, context: int) -> tuple[str, int, int]:
    """Return (rendered_slice, start_line, total_lines) with a >> marker on
    the target line. Empty slice on any IO error."""
    full = rel_path if os.path.isabs(rel_path) else os.path.join(os.getcwd(), rel_path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return "", 0, 0
    total = len(lines)
    start = max(1, line_no - context)
    end = min(total, line_no + context)
    rendered = []
    for i in range(start, end + 1):
        marker = ">>" if i == line_no else "  "
        rendered.append(f"{marker} {i:>5}  {lines[i - 1].rstrip()}")
    return "\n".join(rendered), start, total


def _enclosing_symbol(conn, rel_path: str, line_no: int) -> dict | None:
    """Smallest symbol whose [line_start, line_end] spans line_no."""
    row = conn.execute(
        """SELECT s.name, s.qualified_name, s.kind, s.line_start, s.line_end,
                  s.signature, s.id
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE f.path = ? AND s.line_start <= ? AND s.line_end >= ?
           ORDER BY (s.line_end - s.line_start) ASC LIMIT 1""",
        (rel_path, line_no, line_no),
    ).fetchone()
    if not row:
        # Path may be stored without a leading dir component; try a suffix match.
        row = conn.execute(
            """SELECT s.name, s.qualified_name, s.kind, s.line_start, s.line_end,
                      s.signature, s.id
               FROM symbols s JOIN files f ON s.file_id = f.id
               WHERE f.path LIKE ? AND s.line_start <= ? AND s.line_end >= ?
               ORDER BY (s.line_end - s.line_start) ASC LIMIT 1""",
            ("%" + rel_path.lstrip("./"), line_no, line_no),
        ).fetchone()
    return dict(row) if row else None


def _callers_of(conn, symbol_id: int, cap: int = 8) -> list[str]:
    rows = conn.execute(
        """SELECT f.path AS path, e.line AS edge_line
           FROM edges e
           JOIN symbols s ON e.source_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE e.target_id = ?
           ORDER BY f.path LIMIT ?""",
        (symbol_id, cap),
    ).fetchall()
    return [f"{r['path']}:{r['edge_line']}" for r in rows]


@roam_capability(
    name="at",
    category="exploration",
    summary="Show the code AT a file:line with its enclosing symbol + callers",
    inputs=("location", "--context", "--callers"),
    outputs=("location_context_envelope",),
    examples=("roam at src/roam/cli.py:42", "roam at src/roam/plan/compiler.py:6730 --context 8 --callers"),
    tags=("exploration", "location", "context"),
)
@click.command(name="at")
@click.argument("location")
@click.option("--context", "-C", default=5, show_default=True, help="Lines of context above/below the target line.")
@click.option("--callers", is_flag=True, default=False, help="Also list who calls the enclosing symbol.")
@click.pass_context
def at(ctx, location, context, callers):
    """Show the code at FILE:LINE plus the enclosing symbol (and callers)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    if ":" not in location:
        msg = "location must be FILE:LINE (e.g. src/roam/cli.py:42)"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope("at", summary={"verdict": "bad_location", "partial_success": False, "error": msg})
                )
            )
        else:
            click.echo(f"VERDICT: bad_location\n  {msg}", err=True)
        ctx.exit(2)
        return
    rel_path, _, line_s = location.rpartition(":")
    try:
        line_no = int(line_s)
    except ValueError:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "at",
                        summary={
                            "verdict": "bad_location",
                            "partial_success": False,
                            "error": f"line '{line_s}' is not an integer",
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: bad_location ('{line_s}')", err=True)
        ctx.exit(2)
        return

    ensure_index()
    code, start, total = _read_slice(rel_path, line_no, context)
    with open_db(readonly=True) as conn:
        enclosing = _enclosing_symbol(conn, rel_path, line_no)
        caller_locs = []
        if callers and enclosing and enclosing.get("id"):
            caller_locs = _callers_of(conn, enclosing["id"])

    enc_name = enclosing["name"] if enclosing else None
    enc_kind = enclosing["kind"] if enclosing else None
    if not code:
        verdict = f"no readable source at {rel_path}:{line_no}"
    elif enc_name:
        verdict = (
            f"{rel_path}:{line_no} is inside {enc_kind} `{enc_name}` ({len(caller_locs)} callers shown)"
            if callers
            else f"{rel_path}:{line_no} is inside {enc_kind} `{enc_name}`"
        )
    else:
        verdict = f"{rel_path}:{line_no} — no enclosing symbol indexed"

    if json_mode:
        facts = [
            f"{rel_path}:{line_no} read with {context} context lines",
        ]
        if enc_name:
            facts.append(f"enclosing symbol: {enc_name} ({enc_kind})")
        if caller_locs:
            facts.append(f"{len(caller_locs)} caller locations")
        payload = {
            "location": loc(rel_path, line_no),
            "enclosing_symbol": (
                {
                    "name": enc_name,
                    "kind": enc_kind,
                    "qualified_name": enclosing.get("qualified_name"),
                    "signature": enclosing.get("signature"),
                    "span": loc(rel_path, enclosing["line_start"]) + f"-{enclosing['line_end']}",
                }
                if enclosing
                else None
            ),
            "code": code,
            "file_total_lines": total,
        }
        if callers:
            payload["callers"] = caller_locs
        click.echo(
            to_json(
                json_envelope(
                    "at",
                    summary={"verdict": verdict, "partial_success": not bool(code)},
                    agent_contract={"facts": facts, "next_commands": [], "risks": [], "confidence": None},
                    **payload,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if enclosing:
        click.echo(
            f"  enclosing: {enc_kind} {enc_name}  ({rel_path}:{enclosing['line_start']}-{enclosing['line_end']})"
        )
    if code:
        click.echo()
        click.echo(code)
    if caller_locs:
        click.echo()
        click.echo(f"  callers ({len(caller_locs)}):")
        for c in caller_locs:
            click.echo(f"    {c}")
