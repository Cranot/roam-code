"""Compact structural skeleton of a directory — API surface without implementation."""

import os
from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, format_signature, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.argument('directory')
@click.option('--full', is_flag=True, help='Show all symbols, not just exported')
@click.pass_context
def sketch(ctx, directory, full):
    """Show compact structural skeleton of a directory."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # Normalise path separators
    directory = directory.replace("\\", "/").rstrip("/")

    with open_db(readonly=True) as conn:
        # Find files under directory
        if full:
            symbols = conn.execute(
                "SELECT s.*, f.path as file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE REPLACE(f.path, '\\', '/') LIKE ? "
                "ORDER BY f.path, s.line_start",
                (f"{directory}/%",),
            ).fetchall()
        else:
            symbols = conn.execute(
                "SELECT s.*, f.path as file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE REPLACE(f.path, '\\', '/') LIKE ? AND s.is_exported = 1 "
                "ORDER BY f.path, s.line_start",
                (f"{directory}/%",),
            ).fetchall()

        if not symbols:
            # Try partial match
            if full:
                symbols = conn.execute(
                    "SELECT s.*, f.path as file_path "
                    "FROM symbols s JOIN files f ON s.file_id = f.id "
                    "WHERE REPLACE(f.path, '\\', '/') LIKE ? "
                    "ORDER BY f.path, s.line_start",
                    (f"%{directory}/%",),
                ).fetchall()
            else:
                symbols = conn.execute(
                    "SELECT s.*, f.path as file_path "
                    "FROM symbols s JOIN files f ON s.file_id = f.id "
                    "WHERE REPLACE(f.path, '\\', '/') LIKE ? AND s.is_exported = 1 "
                    "ORDER BY f.path, s.line_start",
                    (f"%{directory}/%",),
                ).fetchall()

        if not symbols:
            if json_mode:
                click.echo(to_json(json_envelope("sketch",
                    summary={"file_count": 0, "symbol_count": 0},
                    directory=directory, files={}, symbol_count=0,
                )))
            else:
                click.echo(f"No {'symbols' if full else 'exported symbols'} found in: {directory}/")
                click.echo("Hint: use a path relative to the project root.")
            return

        # Group by file
        by_file = defaultdict(list)
        for s in symbols:
            by_file[s["file_path"]].append(s)

        if json_mode:
            result = {}
            for fp in sorted(by_file.keys()):
                result[fp] = [
                    {
                        "name": s["name"], "kind": s["kind"],
                        "signature": s["signature"] or "",
                        "line_start": s["line_start"],
                        "line_end": s["line_end"],
                        "docstring": (s["docstring"] or "").strip().split("\n")[0][:80] if s["docstring"] else "",
                    }
                    for s in by_file[fp]
                ]
            click.echo(to_json(json_envelope("sketch",
                summary={"file_count": len(by_file), "symbol_count": len(symbols)},
                directory=directory, file_count=len(by_file),
                symbol_count=len(symbols), files=result,
            )))
            return

        # Count files and symbols
        file_count = len(by_file)
        sym_count = len(symbols)
        label = "symbols" if full else "exported symbols"
        click.echo(f"{directory}/ ({file_count} files, {sym_count} {label})")
        click.echo()

        # Build parent lookup for indentation
        parent_ids = {s["id"]: s["parent_id"] for s in symbols}
        parent_set = {s["id"] for s in symbols}

        for file_path in sorted(by_file.keys()):
            file_syms = by_file[file_path]
            click.echo(f"  {file_path}")

            for s in file_syms:
                # Compute indentation level
                level = 0
                if s["parent_id"] is not None and s["parent_id"] in parent_set:
                    level = 1
                    pid = s["parent_id"]
                    while pid in parent_ids and parent_ids[pid] is not None and parent_ids[pid] in parent_set:
                        level += 1
                        pid = parent_ids[pid]

                prefix = "    " + "  " * level
                kind = abbrev_kind(s["kind"])
                sig = format_signature(s["signature"], max_len=40)
                line_info = f"L{s['line_start']}"
                if s["line_end"] and s["line_end"] != s["line_start"]:
                    line_info += f"-{s['line_end']}"

                # First line of docstring
                doc_snippet = ""
                if s["docstring"]:
                    first_line = s["docstring"].strip().split("\n")[0].strip()
                    if len(first_line) > 50:
                        first_line = first_line[:47] + "..."
                    doc_snippet = f"  {first_line}"

                parts = [f"{kind:<6s}", s["name"]]
                if sig:
                    parts.append(sig)
                parts.append(line_info)

                click.echo(f"{prefix}{'  '.join(parts)}{doc_snippet}")

            click.echo()
