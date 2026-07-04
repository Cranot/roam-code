"""Show direct + transitive side effects of symbols.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because effects outputs are invocation-scoped side-effect
classification rollups (per-symbol io_read / io_write / mutation /
process / none labels) — not per-location code violations. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, json_envelope, to_json


def _count_symbol_effects(conn) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM symbol_effects").fetchone()[0]
    except sqlite3.OperationalError as exc:
        if "no such table: symbol_effects" not in str(exc).lower():
            raise
        return 0


def _symbol_payload_preserving_effect_source(row, effect_rows, file_path):
    payload = {
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "kind": row["kind"],
        "file": file_path,
        "line": row["line_start"],
        "direct_effects": [],
        "transitive_effects": [],
    }
    for effect_row in effect_rows:
        bucket = "direct_effects" if effect_row["source"] == "direct" else "transitive_effects"
        payload[bucket].append(effect_row["effect_type"])
    return payload


@roam_capability(
    name="effects",
    category="architecture",
    summary="Show what functions DO — side-effect classification",
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
@click.command("effects")
@click.argument("target", required=False, default=None)
@click.option("--path", "file_path", default=None, help="Show effects per function in a file path.")
@click.option(
    "--file",
    "file_path",
    default=None,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.option("--type", "effect_type", default=None, help="Filter by effect type (e.g. writes_db, network).")
@click.option("--transitive/--direct-only", default=True, help="Include transitive effects (default: yes).")
@click.pass_context
def effects(ctx, target, file_path, effect_type, transitive):
    """Show what functions DO — side-effect classification.

    Classifies functions by their effects (database reads/writes, network
    I/O, filesystem access, global mutation, etc.) and shows both direct
    and transitive effects through the call graph.

    Unlike ``impact`` (which shows graph-level dependents that break when a
    symbol changes), this command classifies the runtime behaviour of each
    function.

    \b
    Examples:
      roam effects create_user       # effects of a specific function
      roam effects --path src/api.py  # effects per function in a file
      roam effects --type writes_db   # all functions that write to DB
      roam effects --transitive login_user

    See also ``impact`` (graph-level break-radius), ``trace`` (call-path
    walk through the graph), and ``taint`` (source-to-sink reachability
    for security).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Check if effects table has data
        count = _count_symbol_effects(conn)

        if count == 0:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "effects",
                            summary={
                                "verdict": "no effects classified",
                                "symbols_with_effects": 0,
                                "total_effects": 0,
                            },
                            symbols=[],
                        )
                    )
                )
            else:
                click.echo("No effects classified. Re-index to populate: roam index --force")
            return

        if target:
            _show_symbol_effects(ctx, conn, target, transitive, json_mode)
        elif file_path:
            _show_file_effects(ctx, conn, file_path, transitive, json_mode)
        elif effect_type:
            _show_by_type(ctx, conn, effect_type, transitive, json_mode)
        else:
            _show_summary(ctx, conn, json_mode)


def _show_symbol_effects(ctx, conn, target, transitive, json_mode):
    """Show effects for a specific symbol."""
    # Find symbol
    row = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path, s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? OR s.qualified_name = ? "
        "ORDER BY s.name LIMIT 1",
        (target, target),
    ).fetchone()

    if not row:
        # Try LIKE match
        row = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.name LIKE ? OR s.qualified_name LIKE ? "
            "ORDER BY s.name LIMIT 1",
            (f"%{target}%", f"%{target}%"),
        ).fetchone()

    if not row:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "effects",
                        summary={
                            "verdict": f"symbol '{target}' not found",
                            "symbols_with_effects": 0,
                            "total_effects": 0,
                        },
                        symbols=[],
                    )
                )
            )
        else:
            click.echo(f"Symbol '{target}' not found.")
        return

    sym_id = row["id"]
    source_filter = "" if transitive else " AND source = 'direct'"
    effects = conn.execute(
        f"SELECT effect_type, source FROM symbol_effects WHERE symbol_id = ?{source_filter} ORDER BY effect_type",
        (sym_id,),
    ).fetchall()

    symbol_payload = _symbol_payload_preserving_effect_source(row, effects, row["path"])
    direct = symbol_payload["direct_effects"]
    inherited = symbol_payload["transitive_effects"]

    loc = f"{row['path']}:{row['line_start']}" if row["line_start"] else row["path"]
    kind = abbrev_kind(row["kind"])

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "effects",
                    summary={
                        "verdict": f"{len(effects)} effects for {row['name']}",
                        "symbols_with_effects": 1,
                        "total_effects": len(effects),
                    },
                    symbols=[symbol_payload],
                )
            )
        )
        return

    # Text output
    click.echo(f"VERDICT: {len(effects)} effects for {kind} {row['name']}  {loc}")
    click.echo()

    if direct:
        click.echo("DIRECT EFFECTS:")
        for effect_type in direct:
            click.echo(f"  {effect_type}")
        click.echo()

    if inherited:
        click.echo("TRANSITIVE EFFECTS (inherited from callees):")
        for effect_type in inherited:
            click.echo(f"  {effect_type}")
        click.echo()

    if not effects:
        click.echo("  (pure — no side effects detected)")


def _show_file_effects(ctx, conn, file_path, transitive, json_mode):
    """Show effects per function in a file."""
    # Find file
    file_row = conn.execute("SELECT id, path FROM files WHERE path = ?", (file_path,)).fetchone()
    if not file_row:
        file_row = conn.execute(
            "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{file_path}%",),
        ).fetchone()

    if not file_row:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "effects",
                        summary={
                            "verdict": f"file '{file_path}' not found",
                            "symbols_with_effects": 0,
                            "total_effects": 0,
                        },
                        symbols=[],
                    )
                )
            )
        else:
            click.echo(f"File '{file_path}' not found in index.")
        return

    file_id = file_row["id"]
    actual_path = file_row["path"]

    # Get symbols with effects
    source_filter = "" if transitive else " AND se.source = 'direct'"
    rows = conn.execute(
        f"SELECT s.id, s.name, s.qualified_name, s.kind, s.line_start, "
        f"se.effect_type, se.source "
        f"FROM symbols s JOIN symbol_effects se ON s.id = se.symbol_id "
        f"WHERE s.file_id = ?{source_filter} "
        f"ORDER BY s.line_start, se.effect_type",
        (file_id,),
    ).fetchall()

    rows_by_symbol: dict[int, list] = {}
    for r in rows:
        rows_by_symbol.setdefault(r["id"], []).append(r)

    total_effects = len(rows)
    sym_list = [
        _symbol_payload_preserving_effect_source(effect_rows[0], effect_rows, actual_path)
        for effect_rows in rows_by_symbol.values()
    ]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "effects",
                    summary={
                        "verdict": f"{len(sym_list)} functions with effects in {actual_path}",
                        "symbols_with_effects": len(sym_list),
                        "total_effects": total_effects,
                    },
                    file=actual_path,
                    symbols=sym_list,
                )
            )
        )
        return

    # Text output
    click.echo(f"VERDICT: {len(sym_list)} functions with effects in {actual_path}")
    click.echo()

    for s in sym_list:
        kind = abbrev_kind(s["kind"])
        loc = f":{s['line']}" if s["line"] else ""
        all_effects = s["direct_effects"] + [f"{e} (transitive)" for e in s["transitive_effects"]]
        effects_str = ", ".join(all_effects) if all_effects else "pure"
        click.echo(f"  {kind} {s['name']}{loc}  [{effects_str}]")


def _show_by_type(ctx, conn, effect_type, transitive, json_mode):
    """Show all symbols with a specific effect type."""
    source_filter = "" if transitive else " AND se.source = 'direct'"
    rows = conn.execute(
        f"SELECT s.name, s.qualified_name, s.kind, f.path, s.line_start, se.source "
        f"FROM symbol_effects se "
        f"JOIN symbols s ON se.symbol_id = s.id "
        f"JOIN files f ON s.file_id = f.id "
        f"WHERE se.effect_type = ?{source_filter} "
        f"ORDER BY f.path, s.line_start",
        (effect_type,),
    ).fetchall()

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "effects",
                    summary={
                        "verdict": f"{len(rows)} symbols with {effect_type}",
                        "symbols_with_effects": len(rows),
                        "total_effects": len(rows),
                    },
                    effect_type=effect_type,
                    symbols=[
                        {
                            "name": r["name"],
                            "qualified_name": r["qualified_name"],
                            "kind": r["kind"],
                            "file": r["path"],
                            "line": r["line_start"],
                            "source": r["source"],
                        }
                        for r in rows
                    ],
                )
            )
        )
        return

    click.echo(f"VERDICT: {len(rows)} symbols with {effect_type}")
    click.echo()
    for r in rows:
        kind = abbrev_kind(r["kind"])
        loc = f"{r['path']}:{r['line_start']}" if r["line_start"] else r["path"]
        source = " (transitive)" if r["source"] == "transitive" else ""
        click.echo(f"  {kind} {r['name']}{source}    {loc}")


def _show_summary(ctx, conn, json_mode):
    """Show effect summary across the codebase."""
    # Count effects by type
    type_counts = conn.execute(
        "SELECT effect_type, source, COUNT(*) as cnt FROM symbol_effects GROUP BY effect_type, source ORDER BY cnt DESC"
    ).fetchall()

    # Count unique symbols
    sym_count = conn.execute("SELECT COUNT(DISTINCT symbol_id) FROM symbol_effects").fetchone()[0]

    total = conn.execute("SELECT COUNT(*) FROM symbol_effects").fetchone()[0]

    # Aggregate by type
    by_type: dict[str, dict[str, int]] = {}
    for r in type_counts:
        etype = r["effect_type"]
        source = r["source"]
        if etype not in by_type:
            by_type[etype] = {"direct": 0, "transitive": 0}
        by_type[etype][source] = r["cnt"]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "effects",
                    summary={
                        "verdict": f"{sym_count} symbols with effects ({total} total)",
                        "symbols_with_effects": sym_count,
                        "total_effects": total,
                    },
                    by_type={
                        k: v
                        for k, v in sorted(
                            by_type.items(),
                            key=lambda x: x[1]["direct"] + x[1]["transitive"],
                            reverse=True,
                        )
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {sym_count} symbols with effects ({total} total)")
    click.echo()
    click.echo(f"  {'Effect Type':20s} {'Direct':>8s} {'Transitive':>12s} {'Total':>8s}")
    click.echo(f"  {'-' * 20} {'-' * 8} {'-' * 12} {'-' * 8}")
    for etype in sorted(by_type, key=lambda k: by_type[k]["direct"] + by_type[k]["transitive"], reverse=True):
        d = by_type[etype]["direct"]
        t = by_type[etype]["transitive"]
        click.echo(f"  {etype:20s} {d:>8d} {t:>12d} {d + t:>8d}")
