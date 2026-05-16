"""``roam disambiguate <name>`` — list every symbol matching this name.

agents calling ``roam search`` then picking the first result
sometimes pick the wrong one when several functions share a name.
This command shows all matches with the disambiguators (file, line,
kind, signature, first docstring line) plus PageRank as a tiebreaker.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because disambiguate outputs are invocation-scoped symbol-match
enumerations — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="disambiguate",
    category="workflow",
    summary="List every symbol matching <name> with disambiguators",
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
@click.command()
@click.argument("name", metavar="SYMBOL")
@click.option("--limit", type=int, default=20, show_default=True, help="Max matches to display.")
@click.pass_context
def disambiguate(ctx, name, limit) -> None:
    """List every symbol matching SYMBOL with disambiguators.

    SYMBOL is a symbol identifier (bare name or qualified name). Match
    tiers, in order:
      1. ``s.name = SYMBOL`` (exact name match)
      2. ``s.qualified_name = SYMBOL`` (exact qname)
      3. ``s.qualified_name LIKE '%.SYMBOL'`` (suffix qname)
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.qualified_name, s.kind, s.signature,
                   s.docstring, s.line_start, f.path,
                   COALESCE(gm.pagerank, 0) AS pagerank
              FROM symbols s
              JOIN files f ON f.id = s.file_id
              LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
             WHERE s.name = ?
                OR s.qualified_name = ?
                OR s.qualified_name LIKE ?
             ORDER BY pagerank DESC, s.name
             LIMIT ?
            """,
            (name, name, f"%.{name}", int(limit)),
        ).fetchall()

    matches = []
    for r in rows:
        doc_first = ""
        if r["docstring"]:
            stripped = (r["docstring"] or "").strip().splitlines()
            doc_first = stripped[0] if stripped else ""
        matches.append(
            {
                "name": r["name"],
                "qualified_name": r["qualified_name"],
                "kind": r["kind"],
                "signature": (r["signature"] or "").strip(),
                "docstring_summary": doc_first,
                "file": r["path"],
                "line": r["line_start"],
                "pagerank": round(float(r["pagerank"]), 6),
            }
        )

    verdict = (
        f"no symbol matching '{name}'"
        if not matches
        else f"{len(matches)} symbol(s) matching '{name}' (top: {matches[0]['qualified_name'] or matches[0]['name']})"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "disambiguate",
                    summary={"verdict": verdict, "count": len(matches)},
                    matches=matches,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not matches:
        return
    click.echo()
    for m in matches:
        ident = m["qualified_name"] or m["name"]
        click.echo(f"  {m['kind']:<10}  {ident}  ({m['file']}:{m['line']})  PR={m['pagerank']:.5f}")
        if m["signature"]:
            click.echo(f"    sig: {m['signature'][:100]}")
        if m["docstring_summary"]:
            click.echo(f"    doc: {m['docstring_summary'][:100]}")
