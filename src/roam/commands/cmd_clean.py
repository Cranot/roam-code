"""Remove stale/orphaned data from the index without a full rebuild."""

from __future__ import annotations

import os

import click

from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command("clean")
@click.pass_context
def clean(ctx):
    """Remove orphaned entries from the index (files no longer on disk).

    Deletes file records whose paths no longer exist on disk, along with
    their associated symbols, edges, and metrics — without a full rebuild.

    Faster than `roam reset --force` for incremental cleanup after
    files are deleted or moved outside of git tracking.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    with open_db() as conn:
        # Find all files in the DB
        all_rows = conn.execute("SELECT id, path FROM files").fetchall()

        # Identify orphaned files: paths that no longer exist on disk.
        # Paths in the DB are relative to project root.
        orphaned_ids = []
        orphaned_paths = []
        for row in all_rows:
            db_path = row["path"]
            # Paths may be relative (typical) or absolute
            if os.path.isabs(db_path):
                full_path = db_path
            else:
                full_path = os.path.join(str(project_root), db_path)
            if not os.path.exists(full_path):
                orphaned_ids.append(row["id"])
                orphaned_paths.append(db_path)

        symbols_removed = 0
        edges_removed = 0
        files_removed = len(orphaned_ids)

        if orphaned_ids:
            # Count what will be removed for reporting.
            # batched_in returns a flat list of rows; each row is a COUNT(*) result.
            sym_rows = batched_in(
                conn,
                "SELECT COUNT(*) FROM symbols WHERE file_id IN ({ph})",
                orphaned_ids,
            )
            symbols_removed = sum(r[0] for r in sym_rows)

            edge_rows = batched_in(
                conn,
                "SELECT COUNT(*) FROM edges WHERE source_file_id IN ({ph})",
                orphaned_ids,
            )
            edges_removed_source = sum(r[0] for r in edge_rows)

            # Delete orphaned file records.
            # ON DELETE CASCADE removes: symbols, file_edges, file_stats,
            # graph_metrics, clusters, git_cochange, and edges via source_file_id.
            # We delete in batches to avoid SQLITE_MAX_VARIABLE_NUMBER.
            batch_size = 400
            for i in range(0, len(orphaned_ids), batch_size):
                batch = orphaned_ids[i:i + batch_size]
                ph = ",".join("?" for _ in batch)
                conn.execute(f"DELETE FROM files WHERE id IN ({ph})", batch)

            edges_removed = edges_removed_source

        # Also clean up any dangling edges where the symbol no longer exists
        # (can happen when FK enforcement was off or edge was written before
        #  symbol was cleaned up)
        dangling_source = conn.execute(
            "SELECT COUNT(*) FROM edges e "
            "WHERE NOT EXISTS (SELECT 1 FROM symbols WHERE id = e.source_id)"
        ).fetchone()[0]
        dangling_target = conn.execute(
            "SELECT COUNT(*) FROM edges e "
            "WHERE NOT EXISTS (SELECT 1 FROM symbols WHERE id = e.target_id)"
        ).fetchone()[0]
        dangling_edges = dangling_source + dangling_target

        if dangling_source > 0:
            conn.execute(
                "DELETE FROM edges WHERE NOT EXISTS "
                "(SELECT 1 FROM symbols WHERE id = edges.source_id)"
            )
        if dangling_target > 0:
            conn.execute(
                "DELETE FROM edges WHERE NOT EXISTS "
                "(SELECT 1 FROM symbols WHERE id = edges.target_id)"
            )

        # Run VACUUM only if we removed a significant amount of data
        total_removed = files_removed + symbols_removed + edges_removed + dangling_edges
        vacuumed = False
        if total_removed > 100:
            try:
                conn.execute("VACUUM")
                vacuumed = True
            except Exception:
                pass

        verdict = (
            f"clean -- {files_removed} orphaned file(s) removed, "
            f"{symbols_removed} symbol(s), {edges_removed + dangling_edges} edge(s)"
            if total_removed > 0
            else "clean -- index is already clean, nothing to remove"
        )

        if json_mode:
            click.echo(to_json(json_envelope("clean", summary={
                "verdict": verdict,
                "files_removed": files_removed,
                "symbols_removed": symbols_removed,
                "edges_removed": edges_removed + dangling_edges,
                "dangling_edges_removed": dangling_edges,
                "vacuumed": vacuumed,
            },
                orphaned_paths=orphaned_paths,
            )))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        if orphaned_paths:
            click.echo(f"\nOrphaned files removed ({len(orphaned_paths)}):")
            for p in orphaned_paths[:20]:
                click.echo(f"  {p}")
            if len(orphaned_paths) > 20:
                click.echo(f"  (+{len(orphaned_paths) - 20} more)")
        if dangling_edges > 0:
            click.echo(f"\nDangling edges removed: {dangling_edges}")
        if vacuumed:
            click.echo("\nIndex compacted (VACUUM).")
        if total_removed == 0:
            click.echo("  Nothing to remove.")
