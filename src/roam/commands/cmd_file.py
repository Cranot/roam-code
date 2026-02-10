from collections import Counter

import click

from roam.db.connection import open_db
from roam.db.queries import FILE_BY_PATH, SYMBOLS_IN_FILE
from roam.output.formatter import abbrev_kind, loc, format_signature, to_json
from roam.commands.resolve import ensure_index


@click.command("file")
@click.argument('path')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.pass_context
def file_cmd(ctx, path, full):
    """Show file skeleton: all definitions with signatures."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # Normalise separators
    path = path.replace("\\", "/")

    with open_db(readonly=True) as conn:
        frow = conn.execute(FILE_BY_PATH, (path,)).fetchone()
        if frow is None:
            frow = conn.execute(
                "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{path}",),
            ).fetchone()
        if frow is None:
            click.echo(f"File not found in index: {path}")
            raise SystemExit(1)

        symbols = conn.execute(SYMBOLS_IN_FILE, (frow["id"],)).fetchall()
        kind_counts = Counter(abbrev_kind(s["kind"]) for s in symbols)
        parent_ids = {s["id"]: s["parent_id"] for s in symbols}

        if json_mode:
            def _depth(s):
                level = 0
                pid = s["parent_id"]
                while pid is not None and pid in parent_ids:
                    level += 1
                    pid = parent_ids[pid]
                return level

            click.echo(to_json({
                "path": frow["path"],
                "language": frow["language"],
                "line_count": frow["line_count"],
                "kind_summary": dict(kind_counts.most_common()),
                "symbols": [
                    {
                        "name": s["name"],
                        "kind": s["kind"],
                        "signature": s["signature"] or "",
                        "line_start": s["line_start"],
                        "line_end": s["line_end"],
                        "depth": _depth(s),
                    }
                    for s in symbols
                ],
            }))
            return

        # --- Text output ---
        click.echo(f"{frow['path']}  ({frow['language'] or '?'}, {frow['line_count']} lines)")
        click.echo()

        if not symbols:
            click.echo("  (no symbols)")
            return

        summary_parts = [f"{k}:{v}" for k, v in kind_counts.most_common()]
        click.echo("  ".join(summary_parts))
        click.echo()

        for s in symbols:
            level = 0
            if s["parent_id"] is not None:
                level = 1
                pid = s["parent_id"]
                while pid in parent_ids and parent_ids[pid] is not None:
                    level += 1
                    pid = parent_ids[pid]

            prefix = "  " * level
            kind = abbrev_kind(s["kind"])
            sig = format_signature(s["signature"])
            line_info = f"L{s['line_start']}"
            if s["line_end"] and s["line_end"] != s["line_start"]:
                line_info += f"-{s['line_end']}"

            parts = [kind, s["name"]]
            if sig:
                parts.append(sig)
            parts.append(line_info)
            click.echo(f"{prefix}{'  '.join(parts)}")
