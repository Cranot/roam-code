"""Show cross-language symbol bridges detected in the project."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command("x-lang")
@click.pass_context
def xlang(ctx):
    """Show cross-language symbol bridges detected in the project.

    Detects and reports cross-language boundaries such as:
    - Protobuf .proto -> generated Go/Java/Python stubs
    - Salesforce Apex -> Aura/LWC/Visualforce templates
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Get all indexed file paths
        rows = conn.execute("SELECT path FROM files").fetchall()
        file_paths = [r["path"] for r in rows]

        if not file_paths:
            if json_mode:
                click.echo(to_json(json_envelope("x-lang",
                    summary={"bridges": 0, "links": 0},
                    bridges=[],
                )))
            else:
                click.echo("No files indexed.")
            return

        # Detect active bridges
        from roam.bridges.registry import detect_bridges
        active = detect_bridges(file_paths)

        if not active:
            if json_mode:
                click.echo(to_json(json_envelope("x-lang",
                    summary={"bridges": 0, "links": 0},
                    bridges=[],
                )))
            else:
                click.echo("No cross-language bridges detected.")
            return

        # For each bridge, resolve cross-language edges
        all_links = []
        bridge_summaries = []

        for bridge in active:
            # Find source files for this bridge
            source_files = [
                p for p in file_paths
                if any(p.endswith(ext) for ext in bridge.source_extensions)
            ]
            # Find target files for this bridge
            target_files_paths = [
                p for p in file_paths
                if any(p.endswith(ext) for ext in bridge.target_extensions)
            ]

            if not source_files or not target_files_paths:
                continue

            # Get symbols for source and target files
            links_for_bridge = []
            for src_path in source_files:
                src_syms = _get_file_symbols(conn, src_path)
                if not src_syms:
                    continue

                # Build target_files dict
                target_files = {}
                for tp in target_files_paths:
                    tgt_syms = _get_file_symbols(conn, tp)
                    if tgt_syms:
                        target_files[tp] = tgt_syms

                if not target_files:
                    continue

                edges = bridge.resolve(src_path, src_syms, target_files)
                links_for_bridge.extend(edges)

            all_links.extend(links_for_bridge)
            bridge_summaries.append({
                "name": bridge.name,
                "source_files": len(source_files),
                "target_files": len(target_files_paths),
                "links": len(links_for_bridge),
                "source_extensions": sorted(bridge.source_extensions),
                "target_extensions": sorted(bridge.target_extensions),
            })

        if json_mode:
            click.echo(to_json(json_envelope("x-lang",
                summary={
                    "bridges": len(bridge_summaries),
                    "links": len(all_links),
                },
                bridges=bridge_summaries,
                links=all_links[:200],
            )))
            return

        # Text output
        click.echo(f"=== Cross-Language Bridges ({len(bridge_summaries)}) ===\n")

        if bridge_summaries:
            table_rows = []
            for bs in bridge_summaries:
                src_ext = ", ".join(bs["source_extensions"])
                tgt_ext = ", ".join(bs["target_extensions"])
                table_rows.append([
                    bs["name"],
                    src_ext,
                    tgt_ext,
                    str(bs["source_files"]),
                    str(bs["target_files"]),
                    str(bs["links"]),
                ])
            click.echo(format_table(
                ["Bridge", "Source Ext", "Target Ext", "Src Files", "Tgt Files", "Links"],
                table_rows,
            ))
        else:
            click.echo("  (no bridges active)")

        if all_links:
            click.echo(f"\n=== Cross-Language Links ({len(all_links)}) ===\n")
            shown = min(20, len(all_links))
            for link in all_links[:shown]:
                click.echo(f"  {link.get('source', '?')} -> {link.get('target', '?')}  ({link.get('bridge', '?')})")
            if len(all_links) > shown:
                click.echo(f"\n  (+{len(all_links) - shown} more)")


def _get_file_symbols(conn, path):
    """Get symbols for a file path from the DB."""
    frow = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    if not frow:
        return []
    syms = conn.execute(
        "SELECT name, qualified_name, kind FROM symbols WHERE file_id = ?",
        (frow["id"],),
    ).fetchall()
    return [{"name": s["name"], "qualified_name": s["qualified_name"], "kind": s["kind"]} for s in syms]
