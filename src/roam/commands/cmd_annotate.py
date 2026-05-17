"""Agentic memory: persistent annotations on symbols and files.

Provides two CLI commands:
  - ``roam annotate <target> <content>`` — write an annotation
  - ``roam annotations <target>``        — read annotations

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam annotate`` operates on substrate state in ``.roam/``
(annotation records) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Write command
# ---------------------------------------------------------------------------


@roam_capability(
    name="annotate",
    category="workflow",
    summary="Annotate a symbol or file with a persistent note",
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
        resolution = "unresolved"  # W324: explicit resolution-state disclosure

        # Try symbol resolution first
        sym = find_symbol(conn, target)
        if sym is not None:
            symbol_id = sym["id"]
            qualified_name = sym["qualified_name"] or sym["name"]
            resolution = "symbol"
        else:
            # Try as file path
            frow = conn.execute(
                "SELECT path FROM files WHERE path = ? OR path LIKE ?",
                (target, f"%{target}"),
            ).fetchone()
            if frow:
                file_path = frow["path"]
                resolution = "file"
            else:
                # Store as unresolved qualified_name for future linking
                qualified_name = target
                resolution = "unresolved"

        conn.execute(
            "INSERT INTO annotations "
            "(symbol_id, qualified_name, file_path, tag, content, author, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol_id, qualified_name, file_path, tag, content, author, expires),
        )

        # W324 — Pattern-2 fix: disclose resolution state in the verdict so
        # agents can tell whether the target was actually linked (symbol/file)
        # or stored as a dangling qualified_name awaiting future reindex.
        # The dangling-name path is intentional design (annotations relink on
        # reindex, see test_annotation_relinked_after_force), but silent-
        # success vocabulary masks that distinction. Keep the resolved verdict
        # as "Annotation saved" so existing consumers / tests still match.
        if resolution == "unresolved":
            verdict = "Annotation saved as unresolved name (relinks on reindex)"
        else:
            verdict = "Annotation saved"

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "annotate",
                        summary={
                            "verdict": verdict,
                            "resolution": resolution,
                            "target": qualified_name or file_path or target,
                            "tag": tag,
                            "partial_success": resolution == "unresolved",
                        },
                        target=qualified_name or file_path or target,
                        symbol_id=symbol_id,
                        resolution=resolution,
                        tag=tag,
                        author=author,
                        expires_at=expires,
                    )
                )
            )
            return

        resolved = qualified_name or file_path or target
        tag_str = f" [{tag}]" if tag else ""
        suffix = " (unresolved -- relinks on reindex)" if resolution == "unresolved" else ""
        click.echo(f"Annotation saved for {resolved}{tag_str}{suffix}")


# ---------------------------------------------------------------------------
# Read command
# ---------------------------------------------------------------------------


@roam_capability(
    name="annotations",
    category="workflow",
    summary="List annotations for a symbol, file, or the whole project",
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

    Annotations persist across reindexing and are auto-injected into
    ``roam context`` output.  Use ``annotate`` to create new annotations.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        conditions = ["(expires_at IS NULL OR expires_at > datetime('now'))"]
        params = []

        # W324 + dogfood — Pattern 1D: track resolution state for the read
        # path so the envelope can distinguish a legitimate "0 annotations
        # for a resolved target" from a degraded "target did not resolve, we
        # are querying a dangling qualified_name" silent-success. Mirrors the
        # write-path disclosure on ``annotate``.
        resolution: str | None = None  # None when no target was supplied
        if target:
            # Try symbol resolution
            sym = find_symbol(conn, target)
            if sym is not None:
                sym_id = sym["id"]
                qname = sym["qualified_name"] or sym["name"]
                conditions.append("(symbol_id = ? OR qualified_name = ?)")
                params.extend([sym_id, qname])
                resolution = "symbol"
            else:
                # Try file path
                frow = conn.execute(
                    "SELECT path FROM files WHERE path = ? OR path LIKE ?",
                    (target, f"%{target}"),
                ).fetchone()
                if frow:
                    conditions.append("file_path = ?")
                    params.append(frow["path"])
                    resolution = "file"
                else:
                    # Try as qualified_name. The target did NOT resolve as a
                    # live symbol or file row; matching by literal qname can
                    # still hit annotations stored on dangling names from
                    # prior unresolved writes (relinks on reindex), but the
                    # caller must be told this is degraded resolution.
                    conditions.append("qualified_name = ?")
                    params.append(target)
                    resolution = "unresolved"

        if tag:
            conditions.append("tag = ?")
            params.append(tag)

        if since:
            conditions.append("created_at >= ?")
            params.append(since)

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT * FROM annotations WHERE {where} ORDER BY created_at DESC",
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

        # Pattern 1D — silent-success guard. If the caller asked about a
        # specific target and we fell through to the dangling-qualified_name
        # path, the count we just returned is from the dangling-name shard
        # of the table, not from a resolved subject. Mark the envelope
        # partial_success and degrade the verdict so agents can tell.
        partial = resolution == "unresolved"
        if resolution == "unresolved":
            # LAW 4 / Pattern 1D: lead with a non-digit subject so the long-
            # sentence anchor rule fires, and surface that the count reflects
            # only literal qualified_name matches on a target that did NOT
            # resolve to a live symbol or file row.
            plural = "s" if len(ann_list) != 1 else ""
            verdict = f"target did not resolve to any symbol or file: {len(ann_list)} dangling-name annotation{plural}"
        else:
            verdict = f"{len(ann_list)} annotation{'s' if len(ann_list) != 1 else ''}"

        summary: dict = {
            "verdict": verdict,
            "count": len(ann_list),
            "target": target,
            "tag_filter": tag,
            "partial_success": partial,
        }
        if resolution is not None:
            summary["resolution"] = resolution

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "annotations",
                        summary=summary,
                        resolution=resolution,
                        annotations=ann_list,
                    )
                )
            )
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
