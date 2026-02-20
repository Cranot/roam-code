"""Agentic memory: persistent annotations on symbols and files.

Provides two CLI commands:
  - ``roam annotate <target> <content>`` — write an annotation
  - ``roam annotations <target>``        — read annotations
"""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


# ---------------------------------------------------------------------------
# Write command
# ---------------------------------------------------------------------------

@click.command()
@click.argument("target")
@click.argument("content")
@click.option("--tag", default=None, help="Category tag: security, performance, gotcha, review, wip")
@click.option("--author", default=None, help="Who is annotating (agent name or user)")
@click.option("--expires", default=None, help="Expiry datetime (ISO 8601, e.g. 2025-12-31)")
@click.pass_context
def annotate(ctx, target, content, tag, author, expires):
    """Annotate a symbol or file with a persistent note.

    Annotations survive reindexing and are auto-injected into ``roam context``
    output so every agent session inherits institutional knowledge.

    TARGET is a symbol name (resolved via find_symbol) or a file path.

    \b
    Examples:
        roam annotate User "Auth bypass possible via mass assignment" --tag security
        roam annotate src/auth.py "Needs refactor before v2 launch" --tag wip --author claude
        roam annotate handle_request "O(n^2) loop -- see PR #42" --tag performance
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db() as conn:
        symbol_id = None
        qualified_name = None
        file_path = None

        # Try symbol resolution first
        sym = find_symbol(conn, target)
        if sym is not None:
            symbol_id = sym["id"]
            qualified_name = sym["qualified_name"] or sym["name"]
        else:
            # Try as file path
            frow = conn.execute(
                "SELECT path FROM files WHERE path = ? OR path LIKE ?",
                (target, f"%{target}"),
            ).fetchone()
            if frow:
                file_path = frow["path"]
            else:
                # Store as unresolved qualified_name for future linking
                qualified_name = target

        conn.execute(
            "INSERT INTO annotations "
            "(symbol_id, qualified_name, file_path, tag, content, author, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol_id, qualified_name, file_path, tag, content, author, expires),
        )

        if json_mode:
            click.echo(to_json(json_envelope("annotate",
                summary={
                    "verdict": "Annotation saved",
                    "target": qualified_name or file_path or target,
                    "tag": tag,
                },
                target=qualified_name or file_path or target,
                symbol_id=symbol_id,
                tag=tag,
                author=author,
                expires_at=expires,
            )))
            return

        resolved = qualified_name or file_path or target
        tag_str = f" [{tag}]" if tag else ""
        click.echo(f"Annotation saved for {resolved}{tag_str}")


# ---------------------------------------------------------------------------
# Read command
# ---------------------------------------------------------------------------

@click.command()
@click.argument("target", required=False, default=None)
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--since", default=None, help="Only annotations created after this datetime (ISO 8601)")
@click.pass_context
def annotations(ctx, target, tag, since):
    """List annotations for a symbol, file, or the whole project.

    If TARGET is omitted, shows all active annotations.

    \b
    Examples:
        roam annotations User                    # Annotations for a symbol
        roam annotations src/auth.py             # Annotations for a file
        roam annotations --tag security          # All security annotations
        roam annotations --since 2025-01-01      # Recent annotations
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        conditions = ["(expires_at IS NULL OR expires_at > datetime('now'))"]
        params = []

        if target:
            # Try symbol resolution
            sym = find_symbol(conn, target)
            if sym is not None:
                sym_id = sym["id"]
                qname = sym["qualified_name"] or sym["name"]
                conditions.append(
                    "(symbol_id = ? OR qualified_name = ?)"
                )
                params.extend([sym_id, qname])
            else:
                # Try file path
                frow = conn.execute(
                    "SELECT path FROM files WHERE path = ? OR path LIKE ?",
                    (target, f"%{target}"),
                ).fetchone()
                if frow:
                    conditions.append("file_path = ?")
                    params.append(frow["path"])
                else:
                    # Try as qualified_name
                    conditions.append("qualified_name = ?")
                    params.append(target)

        if tag:
            conditions.append("tag = ?")
            params.append(tag)

        if since:
            conditions.append("created_at >= ?")
            params.append(since)

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT * FROM annotations WHERE {where} "
            "ORDER BY created_at DESC",
            params,
        ).fetchall()

        ann_list = [
            {
                "id": r["id"],
                "symbol_id": r["symbol_id"],
                "qualified_name": r["qualified_name"],
                "file_path": r["file_path"],
                "tag": r["tag"],
                "content": r["content"],
                "author": r["author"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ]

        if json_mode:
            click.echo(to_json(json_envelope("annotations",
                summary={
                    "verdict": f"{len(ann_list)} annotation{'s' if len(ann_list) != 1 else ''}",
                    "count": len(ann_list),
                    "target": target,
                    "tag_filter": tag,
                },
                annotations=ann_list,
            )))
            return

        if not ann_list:
            click.echo("No annotations found.")
            return

        click.echo(f"{len(ann_list)} annotation{'s' if len(ann_list) != 1 else ''}:")
        click.echo()
        for a in ann_list:
            target_str = a["qualified_name"] or a["file_path"] or "(unlinked)"
            tag_str = f"[{a['tag']}] " if a["tag"] else ""
            author_str = f" (by {a['author']})" if a["author"] else ""
            expires_str = f" [expires {a['expires_at']}]" if a["expires_at"] else ""
            click.echo(f"  {tag_str}{target_str}{author_str}")
            click.echo(f"    {a['content']}")
            click.echo(f"    created: {a['created_at']}{expires_str}")
            click.echo()
