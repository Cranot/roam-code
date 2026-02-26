"""Show project skeleton with entry points and key symbols."""

from __future__ import annotations

from collections import Counter

import click

from roam.commands.resolve import detect_entry_points, ensure_index
from roam.db.connection import open_db
from roam.db.queries import (
    ALL_FILES,
    TOP_SYMBOLS_BY_PAGERANK,
)
from roam.output.formatter import (
    abbrev_kind,
    format_signature,
    format_table,
    json_envelope,
    loc,
    section,
    to_json,
)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 4 characters = 1 token."""
    return max(1, len(text) // 4)


def _build_symbol_entry_text(s, *, for_budget: bool = False) -> str:
    """Build a single symbol's display line for token-budget accounting."""
    sig = format_signature(s["signature"], max_len=50)
    kind = abbrev_kind(s["kind"])
    location = loc(s["file_path"], s["line_start"])
    pr = f"{(s['pagerank'] or 0):.4f}"
    # Approximate the formatted table row as a single string
    return f"{kind}  {s['name']}  {sig}  {location}  {pr}"


@click.command("map")
@click.option("-n", "count", default=20, help="Number of top symbols to show")
@click.option("--full", is_flag=True, help="Show all results without truncation")
@click.option("--budget", type=int, default=None, help="Approximate token limit for output")
@click.pass_context
def map_cmd(ctx, count, full, budget):
    """Show project skeleton with entry points and key symbols.

    Unlike ``describe`` (which generates a prose project overview), this
    command produces a structured skeleton with directories, entry points,
    and top symbols by PageRank.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # --- Project stats ---
        files = conn.execute(ALL_FILES).fetchall()
        total_files = len(files)
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        lang_counts = Counter(f["language"] for f in files if f["language"])

        # Edge kind distribution
        edge_kinds = conn.execute("SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC").fetchall()

        # --- Top directories ---
        dir_rows_raw = conn.execute("""
            SELECT CASE WHEN INSTR(REPLACE(path, '\\', '/'), '/') > 0
                   THEN SUBSTR(REPLACE(path, '\\', '/'), 1, INSTR(REPLACE(path, '\\', '/'), '/') - 1)
                   ELSE '.' END as dir,
                   COUNT(*) as cnt
            FROM files GROUP BY dir ORDER BY cnt DESC
        """).fetchall()
        dir_counts = {r["dir"]: r["cnt"] for r in dir_rows_raw}
        dir_items = sorted(dir_counts.items(), key=lambda x: x[1], reverse=True)

        # --- Entry points ---
        entries = [ep["path"] for ep in detect_entry_points(conn)]

        # --- Top symbols by PageRank ---
        # When budget is active, fetch all ranked symbols to allow greedy
        # filling up to the token limit.  Otherwise honour the -n count.
        fetch_limit = count if budget is None else 10_000
        all_ranked = conn.execute(TOP_SYMBOLS_BY_PAGERANK, (fetch_limit,)).fetchall()

        # ---- Budget-aware symbol selection ----
        if budget is not None:
            # Pre-compute the preamble (stats, dirs, entries) to account
            # for its token cost when filling the budget.
            lang_str = ", ".join(f"{lang}={n}" for lang, n in lang_counts.most_common(8))
            edge_str = ", ".join(f"{r['kind']}={r['cnt']}" for r in edge_kinds) if edge_kinds else "none"

            preamble_lines = [
                f"Files: {total_files}  Symbols: {sym_count}  Edges: {edge_count}",
                f"Languages: {lang_str}",
                f"Edge kinds: {edge_str}",
                "",
            ]
            # Directories section
            dir_rows_budget = [[d, str(c)] for d, c in (dir_items if full else dir_items[:15])]
            preamble_lines.append("Directories:")
            preamble_lines.append(
                format_table(
                    ["dir", "files"],
                    dir_rows_budget,
                    budget=0 if full else 15,
                )
            )
            preamble_lines.append("")

            # Entry points section
            if entries:
                preamble_lines.append("Entry points:")
                for e in entries if full else entries[:20]:
                    preamble_lines.append(f"  {e}")
                if not full and len(entries) > 20:
                    preamble_lines.append(f"  (+{len(entries) - 20} more)")
                preamble_lines.append("")

            preamble_text = "\n".join(preamble_lines)
            tokens_used = _estimate_tokens(preamble_text)

            # Greedily add symbols until budget is exhausted
            top = []
            for s in all_ranked:
                entry_text = _build_symbol_entry_text(s)
                entry_tokens = _estimate_tokens(entry_text)
                if tokens_used + entry_tokens > budget:
                    break
                tokens_used += entry_tokens
                top.append(s)
        else:
            top = all_ranked
            tokens_used = 0  # not tracked when budget is off

        # Build verdict
        n_dirs = len(dir_items)
        deepest = max(dir_items, key=lambda x: x[0].count("/"))[0] if dir_items else "."
        map_verdict = f"map: {n_dirs} directories, {total_files} files, {sym_count} symbols, deepest: {deepest}"

        if json_mode:
            data = {
                "files": total_files,
                "symbols": sym_count,
                "edges": edge_count,
                "languages": dict(lang_counts.most_common(8)),
                "edge_kinds": {r["kind"]: r["cnt"] for r in edge_kinds},
                "directories": [{"name": d, "files": c} for d, c in dir_items],
                "entry_points": entries,
                "top_symbols": [
                    {
                        "name": s["name"],
                        "kind": s["kind"],
                        "signature": s["signature"] or "",
                        "location": loc(s["file_path"], s["line_start"]),
                        "pagerank": round(s["pagerank"] or 0, 4),
                    }
                    for s in top
                ],
            }
            summary = {
                "verdict": map_verdict,
                "files": total_files,
                "symbols": sym_count,
                "edges": edge_count,
            }
            if budget is not None:
                summary["token_budget"] = budget
                summary["tokens_used"] = tokens_used
                data["token_budget"] = budget
                data["tokens_used"] = tokens_used
            click.echo(
                to_json(
                    json_envelope(
                        "map",
                        summary=summary,
                        **data,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {map_verdict}\n")
        if budget is not None:
            # Preamble already built above; emit it directly
            click.echo(preamble_text, nl=False)
        else:
            lang_str = ", ".join(f"{lang}={n}" for lang, n in lang_counts.most_common(8))
            edge_str = ", ".join(f"{r['kind']}={r['cnt']}" for r in edge_kinds) if edge_kinds else "none"

            click.echo(f"Files: {total_files}  Symbols: {sym_count}  Edges: {edge_count}")
            click.echo(f"Languages: {lang_str}")
            click.echo(f"Edge kinds: {edge_str}")
            click.echo()

            dir_rows = [[d, str(c)] for d, c in (dir_items if full else dir_items[:15])]
            click.echo(section("Directories:", []))
            click.echo(
                format_table(
                    ["dir", "files"],
                    dir_rows,
                    budget=0 if full else 15,
                )
            )
            click.echo()

            if entries:
                click.echo("Entry points:")
                for e in entries if full else entries[:20]:
                    click.echo(f"  {e}")
                if not full and len(entries) > 20:
                    click.echo(f"  (+{len(entries) - 20} more)")
                click.echo()

        if top:
            rows = []
            for s in top:
                sig = format_signature(s["signature"], max_len=50)
                rows.append(
                    [
                        abbrev_kind(s["kind"]),
                        s["name"],
                        sig,
                        loc(s["file_path"], s["line_start"]),
                        f"{(s['pagerank'] or 0):.4f}",
                    ]
                )
            click.echo("Top symbols (PageRank):")
            click.echo(
                format_table(
                    ["kind", "name", "signature", "location", "PR"],
                    rows,
                )
            )
        else:
            click.echo("No graph metrics available. Run `roam index` first.")

        if budget is not None:
            click.echo()
            click.echo(f"Token budget: {tokens_used}/{budget} used")
