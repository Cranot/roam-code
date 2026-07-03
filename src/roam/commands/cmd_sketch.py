"""Compact structural skeleton of a directory — API surface without implementation.

Prefer ``roam understand --skeleton <DIR>`` for the unified single-call
alternative, which produces identical output within the broader understand
context.

This command is kept as a standalone entry point because it accepts a
positional ``directory`` argument and supports a ``--full`` flag to include
non-exported symbols, neither of which is exposed by ``understand
--skeleton``.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because sketch outputs are invocation-scoped directory-skeleton
+ API-surface summaries — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    format_signature,
    json_envelope,
    resolution_disclosure,
    to_json,
)


def _normalized_directory_for_index_lookup(directory):
    return directory.replace("\\", "/").rstrip("/")


def _directory_symbols_query(full):
    exported_filter = "" if full else "AND s.is_exported = 1 "
    return (
        "SELECT s.*, f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE REPLACE(f.path, '\\', '/') LIKE ? "
        f"{exported_filter}"
        "ORDER BY f.path, s.line_start"
    )


def _symbols_preserving_resolution_tier(conn, directory, full):
    query = _directory_symbols_query(full)
    symbols = conn.execute(query, (f"{directory}/%",)).fetchall()
    if symbols:
        return symbols, "file"

    symbols = conn.execute(query, (f"%{directory}/%",)).fetchall()
    if symbols:
        return symbols, "file_substring"

    return symbols, "unresolved"


def _group_symbols_by_file(symbols):
    by_file = {}
    for symbol in symbols:
        by_file.setdefault(symbol["file_path"], []).append(symbol)
    return by_file


def _symbol_json_entry(symbol):
    docstring = symbol["docstring"] or ""
    return {
        "name": symbol["name"],
        "kind": symbol["kind"],
        "signature": symbol["signature"] or "",
        "line_start": symbol["line_start"],
        "line_end": symbol["line_end"],
        "docstring": docstring.strip().split("\n")[0][:80] if docstring else "",
    }


def _files_json_payload(by_file):
    return {
        file_path: [_symbol_json_entry(symbol) for symbol in by_file[file_path]] for file_path in sorted(by_file.keys())
    }


def _depth_within_returned_skeleton(symbol, parent_ids, parent_set):
    if symbol["parent_id"] is None or symbol["parent_id"] not in parent_set:
        return 0

    level = 1
    parent_id = symbol["parent_id"]
    while parent_id in parent_ids and parent_ids[parent_id] is not None and parent_ids[parent_id] in parent_set:
        level += 1
        parent_id = parent_ids[parent_id]
    return level


def _line_span(symbol):
    line_info = f"L{symbol['line_start']}"
    if symbol["line_end"] and symbol["line_end"] != symbol["line_start"]:
        line_info += f"-{symbol['line_end']}"
    return line_info


def _first_docstring_line(symbol):
    if not symbol["docstring"]:
        return ""

    first_line = symbol["docstring"].strip().split("\n")[0].strip()
    if len(first_line) > 50:
        first_line = first_line[:47] + "..."
    return f"  {first_line}"


def _skeleton_symbol_text(symbol, level):
    parts = [f"{abbrev_kind(symbol['kind']):<6s}", symbol["name"]]
    signature = format_signature(symbol["signature"], max_len=40)
    if signature:
        parts.append(signature)
    parts.append(_line_span(symbol))

    prefix = "    " + "  " * level
    return f"{prefix}{'  '.join(parts)}{_first_docstring_line(symbol)}"


def _render_skeleton_symbol_lines(by_file, symbols):
    parent_ids = {symbol["id"]: symbol["parent_id"] for symbol in symbols}
    parent_set = {symbol["id"] for symbol in symbols}
    lines = []

    for file_path in sorted(by_file.keys()):
        lines.append(f"  {file_path}")
        for symbol in by_file[file_path]:
            level = _depth_within_returned_skeleton(symbol, parent_ids, parent_set)
            lines.append(_skeleton_symbol_text(symbol, level))
        lines.append("")

    return lines


@roam_capability(
    name="sketch",
    category="refactoring",
    summary="Show compact structural skeleton of a directory",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.argument("directory")
@click.option("--full", is_flag=True, help="Show all symbols, not just exported")
@click.pass_context
def sketch(ctx, directory, full):
    """Show compact structural skeleton of a directory.

    Unlike ``understand --skeleton`` (which shows exported symbols as part
    of a broader overview), this command provides a standalone structural
    skeleton with optional ``--full`` mode to include private symbols.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    directory = _normalized_directory_for_index_lookup(directory)

    with open_db(readonly=True) as conn:
        symbols, resolution_tier = _symbols_preserving_resolution_tier(conn, directory, full)

        if not symbols:
            disclosure = resolution_disclosure("unresolved", target=directory)
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "sketch",
                            summary={
                                "verdict": f"no symbols found in {directory}/",
                                "file_count": 0,
                                "symbol_count": 0,
                                "resolution": disclosure["resolution"],
                                "partial_success": disclosure["partial_success"],
                            },
                            directory=directory,
                            files={},
                            symbol_count=0,
                            resolution=disclosure,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: no symbols found in {directory}/\n")
                click.echo(f"No {'symbols' if full else 'exported symbols'} found in: {directory}/")
                click.echo("Hint: use a path relative to the project root.")
            return

        by_file = _group_symbols_by_file(symbols)
        disclosure = resolution_disclosure(resolution_tier, target=directory)
        verdict_suffix = " [file substring match]" if resolution_tier == "file_substring" else ""

        if json_mode:
            _verdict = f"{directory}/: {len(by_file)} files, {len(symbols)} symbols{verdict_suffix}"
            click.echo(
                to_json(
                    json_envelope(
                        "sketch",
                        summary={
                            "verdict": _verdict,
                            "file_count": len(by_file),
                            "symbol_count": len(symbols),
                            "resolution": disclosure["resolution"],
                            "partial_success": disclosure["partial_success"],
                        },
                        directory=directory,
                        file_count=len(by_file),
                        symbol_count=len(symbols),
                        files=_files_json_payload(by_file),
                        resolution=disclosure,
                    )
                )
            )
            return

        file_count = len(by_file)
        sym_count = len(symbols)
        label = "symbols" if full else "exported symbols"
        _verdict = f"{directory}/: {file_count} files, {sym_count} {label}{verdict_suffix}"
        click.echo(f"VERDICT: {_verdict}\n")
        click.echo(f"{directory}/ ({file_count} files, {sym_count} {label})")
        if resolution_tier == "file_substring":
            click.echo("  Note: substring match on directory path; input was not an exact directory prefix.")
        click.echo()

        for line in _render_skeleton_symbol_lines(by_file, symbols):
            click.echo(line)
