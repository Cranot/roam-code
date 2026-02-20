"""Syntax-less agentic editing -- move, rename, add-call, extract symbols."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


def _format_changes_text(result: dict) -> list[str]:
    """Format change plan as human-readable text lines."""
    lines = []
    for fmod in result.get("files_modified", []):
        action = fmod.get("action", "MODIFY")
        lines.append(f"  {fmod['path']} ({action})")
        for change in fmod.get("changes", []):
            ctype = change.get("type", "?")
            if ctype == "replace":
                ls = change.get("line_start", "?")
                le = change.get("line_end", "?")
                old = change.get("old_text", "")
                new = change.get("new_text", "")
                lines.append(f"    -{ls}  {_truncate(old, 60)}")
                lines.append(f"    +{ls}  {_truncate(new, 60)}")
            elif ctype == "insert":
                ln = change.get("line", "?")
                text = change.get("text", "")
                lines.append(f"    +{ln}  {_truncate(text, 60)}")
            elif ctype == "delete":
                ls = change.get("line_start", "?")
                le = change.get("line_end", "?")
                old = change.get("old_text", "")
                first_line = old.split("\n")[0] if old else ""
                total = le - ls + 1 if isinstance(ls, int) and isinstance(le, int) else "?"
                lines.append(f"    -{ls}..{le}  {_truncate(first_line, 50)}  ({total} lines)")
        lines.append("")
    return lines


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if needed."""
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group("mutate")
@click.pass_context
def mutate(ctx):
    """Syntax-less agentic editing.

    Move, rename, add calls, and extract symbols with automatic import
    rewriting and reference updates. Default is dry-run (preview).
    Use --apply to write changes to disk.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

@mutate.command("move")
@click.argument("symbol")
@click.argument("target_file")
@click.option("--apply", "apply_changes", is_flag=True, default=False,
              help="Write changes to disk (default: dry-run preview).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Preview changes without writing (this is the default).")
@click.pass_context
def mutate_move(ctx, symbol, target_file, apply_changes, dry_run):
    """Move a symbol to a different file."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.refactor.transforms import move_symbol

    with open_db(readonly=True) as conn:
        result = move_symbol(conn, symbol, target_file,
                             dry_run=(not apply_changes))

    if result.get("error"):
        if json_mode:
            click.echo(to_json(json_envelope("mutate",
                summary={"verdict": result["error"], "operation": "move",
                         "files_modified": 0, "conflicts": 0},
                changes=[], warnings=result.get("warnings", []),
            )))
            return
        click.echo(f"VERDICT: {result['error']}")
        return

    n_files = len(result.get("files_modified", []))
    verdict = (f"move {result.get('symbol', symbol)} -- "
               f"{n_files} files modified, 0 conflicts")

    if json_mode:
        click.echo(to_json(json_envelope("mutate",
            summary={"verdict": verdict, "operation": "move",
                     "files_modified": n_files, "conflicts": 0},
            changes=result.get("files_modified", []),
            warnings=result.get("warnings", []),
        )))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        from_f = result.get("from_file", "?")
        click.echo(f"Run `roam mutate move {symbol} {target_file} --apply` to execute.")


@mutate.command("rename")
@click.argument("symbol")
@click.argument("new_name")
@click.option("--apply", "apply_changes", is_flag=True, default=False,
              help="Write changes to disk (default: dry-run preview).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Preview changes without writing (this is the default).")
@click.pass_context
def mutate_rename(ctx, symbol, new_name, apply_changes, dry_run):
    """Rename a symbol across the codebase."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.refactor.transforms import rename_symbol

    with open_db(readonly=True) as conn:
        result = rename_symbol(conn, symbol, new_name,
                               dry_run=(not apply_changes))

    if result.get("error"):
        if json_mode:
            click.echo(to_json(json_envelope("mutate",
                summary={"verdict": result["error"], "operation": "rename",
                         "files_modified": 0, "conflicts": 0},
                changes=[], warnings=result.get("warnings", []),
            )))
            return
        click.echo(f"VERDICT: {result['error']}")
        return

    n_files = len(result.get("files_modified", []))
    verdict = (f"rename {result.get('symbol', symbol)} -> {new_name} -- "
               f"{n_files} files modified, 0 conflicts")

    if json_mode:
        click.echo(to_json(json_envelope("mutate",
            summary={"verdict": verdict, "operation": "rename",
                     "files_modified": n_files, "conflicts": 0},
            changes=result.get("files_modified", []),
            warnings=result.get("warnings", []),
        )))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        click.echo(f"Run `roam mutate rename {symbol} {new_name} --apply` to execute.")


@mutate.command("add-call")
@click.option("--from", "from_symbol", required=True,
              help="The calling symbol.")
@click.option("--to", "to_symbol", required=True,
              help="The callee symbol.")
@click.option("--args", "call_args", default="",
              help="Arguments for the call (e.g. 'data, config').")
@click.option("--apply", "apply_changes", is_flag=True, default=False,
              help="Write changes to disk (default: dry-run preview).")
@click.pass_context
def mutate_add_call(ctx, from_symbol, to_symbol, call_args, apply_changes):
    """Add a call from one symbol to another."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.refactor.transforms import add_call

    with open_db(readonly=True) as conn:
        result = add_call(conn, from_symbol, to_symbol, call_args,
                          dry_run=(not apply_changes))

    if result.get("error"):
        if json_mode:
            click.echo(to_json(json_envelope("mutate",
                summary={"verdict": result["error"], "operation": "add-call",
                         "files_modified": 0, "conflicts": 0},
                changes=[], warnings=result.get("warnings", []),
            )))
            return
        click.echo(f"VERDICT: {result['error']}")
        return

    n_files = len(result.get("files_modified", []))
    from_name = result.get("from_symbol", from_symbol)
    to_name = result.get("to_symbol", to_symbol)
    verdict = (f"add-call {from_name} -> {to_name} -- "
               f"{n_files} files modified, 0 conflicts")

    if json_mode:
        click.echo(to_json(json_envelope("mutate",
            summary={"verdict": verdict, "operation": "add-call",
                     "files_modified": n_files, "conflicts": 0},
            changes=result.get("files_modified", []),
            warnings=result.get("warnings", []),
        )))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        click.echo(f"Run `roam mutate add-call --from {from_symbol} --to {to_symbol} --apply` to execute.")


@mutate.command("extract")
@click.argument("symbol")
@click.option("--lines", required=True,
              help="Line range to extract (e.g. '5-10').")
@click.option("--name", "new_name", required=True,
              help="Name for the new extracted function.")
@click.option("--apply", "apply_changes", is_flag=True, default=False,
              help="Write changes to disk (default: dry-run preview).")
@click.pass_context
def mutate_extract(ctx, symbol, lines, new_name, apply_changes):
    """Extract lines from a symbol into a new function."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # Parse line range
    try:
        parts = lines.split("-")
        line_start = int(parts[0])
        line_end = int(parts[1]) if len(parts) > 1 else line_start
    except (ValueError, IndexError):
        msg = f"invalid line range: {lines} (expected START-END)"
        if json_mode:
            click.echo(to_json(json_envelope("mutate",
                summary={"verdict": msg, "operation": "extract",
                         "files_modified": 0, "conflicts": 0},
                changes=[], warnings=[msg],
            )))
            return
        click.echo(f"VERDICT: {msg}")
        return

    from roam.refactor.transforms import extract_symbol

    with open_db(readonly=True) as conn:
        result = extract_symbol(conn, symbol, line_start, line_end, new_name,
                                dry_run=(not apply_changes))

    if result.get("error"):
        if json_mode:
            click.echo(to_json(json_envelope("mutate",
                summary={"verdict": result["error"], "operation": "extract",
                         "files_modified": 0, "conflicts": 0},
                changes=[], warnings=result.get("warnings", []),
            )))
            return
        click.echo(f"VERDICT: {result['error']}")
        return

    n_files = len(result.get("files_modified", []))
    sym_name = result.get("symbol", symbol)
    verdict = (f"extract {sym_name}:{lines} -> {new_name} -- "
               f"{n_files} files modified, 0 conflicts")

    if json_mode:
        click.echo(to_json(json_envelope("mutate",
            summary={"verdict": verdict, "operation": "extract",
                     "files_modified": n_files, "conflicts": 0},
            changes=result.get("files_modified", []),
            warnings=result.get("warnings", []),
        )))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        click.echo(f"Run `roam mutate extract {symbol} --lines {lines} --name {new_name} --apply` to execute.")
