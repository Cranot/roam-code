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
    roam at src/roam/cli.py:40-90 --context 0
    roam at src/roam/plan/compiler.py:6730 --whole-symbol --callers

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


def _read_range(
    rel_path: str,
    line_start: int,
    line_end: int,
    context: int,
    max_lines: int = 200,
) -> tuple[str, int, int, int, bool]:
    """Return a bounded rendered range and disclose any truncation."""
    full = rel_path if os.path.isabs(rel_path) else os.path.join(os.getcwd(), rel_path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return "", 0, 0, 0, False
    total = len(lines)
    start = max(1, line_start - context)
    requested_end = min(total, line_end + context)
    end = min(requested_end, start + max_lines - 1)
    truncated = end < requested_end
    rendered = []
    for i in range(start, end + 1):
        marker = ">>" if line_start <= i <= line_end else "  "
        rendered.append(f"{marker} {i:>5}  {lines[i - 1].rstrip()}")
    return "\n".join(rendered), start, end, total, truncated


def _read_slice(rel_path: str, line_no: int, context: int) -> tuple[str, int, int]:
    """Return (rendered_slice, start_line, total_lines) with a >> marker on
    the target line. Empty slice on any IO error."""
    rendered, start, _end, total, _truncated = _read_range(
        rel_path,
        line_no,
        line_no,
        context,
    )
    return rendered, start, total


def _parse_location(location: str) -> tuple[str, int, int]:
    if ":" not in location:
        raise ValueError("location must be FILE:LINE or FILE:START-END")
    rel_path, _, line_spec = location.rpartition(":")
    if "-" in line_spec:
        start_s, _, end_s = line_spec.partition("-")
    else:
        start_s = end_s = line_spec
    try:
        line_start = int(start_s)
        line_end = int(end_s)
    except ValueError as exc:
        raise ValueError(f"line range '{line_spec}' must contain integers") from exc
    if line_start < 1 or line_end < line_start:
        raise ValueError(f"line range '{line_spec}' must satisfy 1 <= START <= END")
    return rel_path, line_start, line_end


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
    inputs=("location", "--context", "--whole-symbol", "--max-lines", "--callers"),
    outputs=("location_context_envelope",),
    examples=(
        "roam at src/roam/cli.py:42",
        "roam at src/roam/cli.py:40-90 --context 0",
        "roam at src/roam/plan/compiler.py:6730 --whole-symbol --callers",
    ),
    tags=("exploration", "location", "context"),
    displaces=("repeated_code_slicing",),
)
@click.command(name="at")
@click.argument("location")
@click.option("--context", "-C", default=5, show_default=True, help="Lines of context above/below the target line.")
@click.option(
    "--whole-symbol",
    is_flag=True,
    default=False,
    help="Expand a single target line to the complete enclosing symbol.",
)
@click.option(
    "--max-lines",
    type=click.IntRange(1, 1000),
    default=200,
    show_default=True,
    help="Maximum rendered source lines; truncation is disclosed.",
)
@click.option("--callers", is_flag=True, default=False, help="Also list who calls the enclosing symbol.")
@click.pass_context
def at(ctx, location, context, whole_symbol, max_lines, callers):
    """Show the code at FILE:LINE plus the enclosing symbol (and callers)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    try:
        rel_path, line_start, line_end = _parse_location(location)
    except ValueError as exc:
        msg = f"{exc} (e.g. src/roam/cli.py:42 or src/roam/cli.py:40-90)"
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
    if whole_symbol and line_start != line_end:
        msg = "--whole-symbol accepts one target line, not an explicit range"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "at",
                        summary={
                            "verdict": "bad_location",
                            "partial_success": False,
                            "error": msg,
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: bad_location\n  {msg}", err=True)
        ctx.exit(2)
        return

    ensure_index()
    with open_db(readonly=True) as conn:
        enclosing = _enclosing_symbol(conn, rel_path, line_start)
        caller_locs = []
        if callers and enclosing and enclosing.get("id"):
            caller_locs = _callers_of(conn, enclosing["id"])
    requested_start = line_start
    requested_end = line_end
    if whole_symbol and enclosing:
        line_start = int(enclosing["line_start"])
        line_end = int(enclosing["line_end"])
    code, returned_start, returned_end, total, truncated = _read_range(
        rel_path,
        line_start,
        line_end,
        context,
        max_lines=max_lines,
    )

    enc_name = enclosing["name"] if enclosing else None
    enc_kind = enclosing["kind"] if enclosing else None
    if not code:
        verdict = f"no readable source at {rel_path}:{requested_start}"
    elif enc_name:
        target = f"{rel_path}:{line_start}-{line_end}" if line_start != line_end else f"{rel_path}:{line_start}"
        verdict = f"{target} covers {enc_kind} `{enc_name}`"
        if callers:
            verdict += f" with {len(caller_locs)} callers shown"
        if truncated:
            verdict += f"; rendered first {max_lines} lines"
    else:
        target = f"{rel_path}:{line_start}-{line_end}" if line_start != line_end else f"{rel_path}:{line_start}"
        verdict = f"{target} — no enclosing symbol indexed"
        if truncated:
            verdict += f"; rendered first {max_lines} lines"

    if json_mode:
        facts = [
            f"Rendered {returned_end - returned_start + 1 if code else 0} source lines",
        ]
        if enc_name:
            facts.append(f"{enc_name} anchors 1 enclosing {enc_kind} symbols")
        if caller_locs:
            facts.append(f"{len(caller_locs)} callers")
        payload = {
            "location": loc(rel_path, requested_start),
            "requested_range": {
                "start": requested_start,
                "end": requested_end,
            },
            "rendered_range": {
                "start": returned_start,
                "end": returned_end,
                "target_start": line_start,
                "target_end": line_end,
                "context": context,
                "max_lines": max_lines,
                "truncated": truncated,
            },
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
                    summary={
                        "verdict": verdict,
                        "partial_success": not bool(code) or truncated,
                    },
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
        if truncated:
            click.echo()
            click.echo(f"  ... truncated at {max_lines} lines; increase --max-lines to read more")
    if caller_locs:
        click.echo()
        click.echo(f"  callers ({len(caller_locs)}):")
        for c in caller_locs:
            click.echo(f"    {c}")
