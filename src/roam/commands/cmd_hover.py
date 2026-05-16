"""``roam hover`` — single-line architectural summary suitable for an
IDE hover panel or chat-inline reference.

Designed to fit in ~200 tokens regardless of the symbol. Where ``roam
context`` returns a full briefing (signature, callers, related files,
tests, model fields, etc.), ``roam hover`` returns the minimum useful
gloss: kind, qualified name, location, blast-radius bucket, top
caller, top callee. Pairs with a hover-on-symbol IDE plugin.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because hover outputs are invocation-scoped hover summaries —
not per-location violations. Editor consumers should use the JSON
envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)


def _blast_bucket(in_degree: int) -> str:
    """Coarse classifier: how nervous should an editor be about
    changing this symbol? Buckets match ``roam impact`` thresholds."""
    if in_degree >= 50:
        return "large"
    if in_degree >= 10:
        return "moderate"
    if in_degree >= 1:
        return "small"
    return "none"


def _top_neighbour(conn, sym_id: int, *, direction: str) -> dict | None:
    """Highest-PageRank caller (direction='in') or callee (direction='out')."""
    if direction == "in":
        edge_clause = "e.target_id = ? AND s.id = e.source_id"
    else:
        edge_clause = "e.source_id = ? AND s.id = e.target_id"
    rows = conn.execute(
        f"""
        SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path,
               s.line_start, COALESCE(gm.pagerank, 0) AS pr
        FROM edges e
        JOIN symbols s ON {edge_clause}
        JOIN files f ON s.file_id = f.id
        LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
        -- W524-fix: 'imports' is a phantom for the symbol-level `edges` table
        -- (canonical writer is 'import' singular; 'imports' is the file_edges value).
        -- Union both forms + plural 'calls' for plugin variants (W79 / W499 pattern).
        WHERE e.kind IN ('call', 'calls', 'inherits', 'import', 'imports')
        ORDER BY pr DESC
        LIMIT 1
        """,
        (sym_id,),
    ).fetchall()
    if not rows:
        return None
    r = rows[0]
    return {
        "name": r["qualified_name"] or r["name"],
        "kind": r["kind"],
        "file_path": r["file_path"],
        "line_start": r["line_start"],
    }


@roam_capability(
    name="hover",
    category="workflow",
    summary="Show a one-line architectural summary for SYMBOL",
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
@click.argument("symbol")
@click.pass_context
def hover(ctx, symbol: str):
    """Show a one-line architectural summary for SYMBOL.

    Output is bounded at ~200 tokens regardless of the symbol — kind,
    qualified name, location, blast-radius bucket, top caller, top
    callee. Designed for IDE hover panels and chat-inline references
    where ``roam context`` is too verbose.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, symbol)
        if sym is None:
            # W1272 — Pattern-2c Convention (c): unresolved exits 0 with a
            # resolution=unresolved + partial_success disclosure. A
            # hover summary on a missing symbol is "I tried and there's
            # nothing to summarise" (a valid no-op success), not a tool
            # failure. Keep the FTS suggestion list in text mode.
            unresolved_block = resolution_disclosure("unresolved", target=symbol or "")
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "hover",
                            summary={
                                "verdict": f"Symbol '{symbol}' not found",
                                "partial_success": True,
                                "state": "not_found",
                                **unresolved_block,
                            },
                            symbol=symbol or "",
                            **unresolved_block,
                        )
                    )
                )
            else:
                click.echo(symbol_not_found(conn, symbol, json_mode=False))
            return
        sym_id = sym["id"]

        metrics = conn.execute(
            "SELECT in_degree, out_degree, pagerank FROM graph_metrics WHERE symbol_id = ?",
            (sym_id,),
        ).fetchone()
        in_d = metrics["in_degree"] if metrics else 0
        out_d = metrics["out_degree"] if metrics else 0
        pr = float(metrics["pagerank"] or 0) if metrics else 0.0

        bucket = _blast_bucket(in_d)
        top_caller = _top_neighbour(conn, sym_id, direction="in")
        top_callee = _top_neighbour(conn, sym_id, direction="out")

    qn = sym["qualified_name"] or sym["name"]
    file_loc = loc(sym["file_path"], sym["line_start"])
    kind_short = abbrev_kind(sym["kind"])

    # W1245 Pattern-2 variant-D: disclose which resolver tier matched so
    # IDE consumers reading the hover envelope can distinguish an exact
    # match from a fuzzy-LIKE-fallback that landed on a different symbol.
    resolution_tier = sym.get("_resolution_tier", "symbol")
    resolution_block = resolution_disclosure(resolution_tier, target=qn)
    fuzzy_suffix = " [fuzzy resolution]" if resolution_tier != "symbol" else ""

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "hover",
                    summary={
                        "verdict": (
                            f"{kind_short} {qn} — {bucket} blast radius ({in_d} in, {out_d} out){fuzzy_suffix}"
                        ),
                        "kind": sym["kind"],
                        "qualified_name": qn,
                        "file_path": sym["file_path"],
                        "line_start": sym["line_start"],
                        "in_degree": in_d,
                        "out_degree": out_d,
                        "pagerank": round(pr, 6),
                        "blast_bucket": bucket,
                        # W1245 Pattern-2 variant-D resolution disclosure.
                        **resolution_block,
                    },
                    top_caller=top_caller,
                    top_callee=top_callee,
                    **resolution_block,
                )
            )
        )
        return

    click.echo(f"{kind_short}  {qn}  {file_loc}")
    click.echo(f"  blast radius: {bucket} ({in_d} callers, {out_d} callees, pr={pr:.4f})")
    if top_caller:
        c_loc = loc(top_caller["file_path"], top_caller["line_start"])
        click.echo(f"  top caller:   {top_caller['name']}  {c_loc}")
    if top_callee:
        c_loc = loc(top_callee["file_path"], top_callee["line_start"])
        click.echo(f"  top callee:   {top_callee['name']}  {c_loc}")
