"""Compute the minimal set of changes needed when modifying a symbol."""

from __future__ import annotations

import os

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


_TEST_NAME_PATS = ["test_", "_test.", ".test.", ".spec."]
_TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/"]


def _is_test_file(path):
    """Check if a file path looks like a test file."""
    p = path.replace("\\", "/")
    bn = os.path.basename(p)
    return any(pat in bn for pat in _TEST_NAME_PATS) or any(d in p for d in _TEST_DIR_PATS)


def _collect_closure(conn, sym, rename=None, delete=False):
    """Compute the minimal change set for a symbol modification.

    Returns a list of change dicts, each with:
        change_type, file, line, name, kind, reason
    """
    sym_id = sym["id"]
    sym_name = sym["name"]
    changes = []
    seen_files = set()

    # 1. Definition — the symbol itself
    changes.append({
        "change_type": "update_definition" if not delete else "delete_definition",
        "file": sym["file_path"],
        "line": sym["line_start"],
        "name": sym_name,
        "kind": sym["kind"],
        "reason": "symbol definition",
    })
    seen_files.add(sym["file_path"])

    # 2. Direct callers — symbols that reference this one
    callers = conn.execute(
        "SELECT DISTINCT s.id, s.name, s.kind, f.path AS file_path, "
        "s.line_start, e.kind AS edge_kind "
        "FROM edges e "
        "JOIN symbols s ON s.id = e.source_id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id = ?",
        (sym_id,),
    ).fetchall()

    for caller in callers:
        fp = caller["file_path"]
        is_test = _is_test_file(fp)
        if is_test:
            change_type = "update_test"
            reason = "test exercises this symbol"
        else:
            edge_kind = caller["edge_kind"] or "calls"
            if edge_kind in ("imports", "import"):
                change_type = "update_import"
                reason = f"imports {sym_name}"
            else:
                change_type = "update_call"
                reason = f"{edge_kind} {sym_name}"
        changes.append({
            "change_type": change_type,
            "file": fp,
            "line": caller["line_start"],
            "name": caller["name"],
            "kind": caller["kind"],
            "reason": reason,
        })
        seen_files.add(fp)

    # 3. Test files via path pattern (may find tests not linked by edges)
    test_rows = conn.execute(
        "SELECT DISTINCT f.path "
        "FROM files f "
        "JOIN symbols s ON s.file_id = f.id "
        "JOIN edges e ON e.source_id = s.id "
        "WHERE e.target_id = ? AND ("
        "  f.path LIKE '%%test%%' OR f.path LIKE '%%spec%%'"
        ")",
        (sym_id,),
    ).fetchall()
    for row in test_rows:
        fp = row["path"]
        if fp not in seen_files:
            changes.append({
                "change_type": "update_test",
                "file": fp,
                "line": None,
                "name": "",
                "kind": "test_file",
                "reason": f"test file referencing {sym_name}",
            })
            seen_files.add(fp)

    # 4. Re-exports — files that import this symbol's file and re-export symbols
    file_row = conn.execute(
        "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
    ).fetchone()
    if file_row:
        importers = conn.execute(
            "SELECT DISTINCT f.path, f.id "
            "FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id = ?",
            (file_row["id"],),
        ).fetchall()
        for imp in importers:
            fp = imp["path"]
            if fp not in seen_files:
                # Check if this file re-exports symbols from the target file
                re_export = conn.execute(
                    "SELECT s.name FROM symbols s "
                    "WHERE s.file_id = ? AND s.name = ? AND s.is_exported = 1",
                    (imp["id"], sym_name),
                ).fetchone()
                if re_export:
                    changes.append({
                        "change_type": "update_import",
                        "file": fp,
                        "line": None,
                        "name": sym_name,
                        "kind": "re_export",
                        "reason": f"re-exports {sym_name}",
                    })
                    seen_files.add(fp)

    # 5. String references in doc/config files (for rename)
    if rename:
        doc_rows = conn.execute(
            "SELECT f.path FROM files f "
            "WHERE (f.language IS NULL OR f.language IN "
            "  ('markdown', 'yaml', 'json', 'toml', 'text', 'rst', 'xml')) "
            "AND f.path NOT LIKE '%%.roam%%'"
        ).fetchall()
        for row in doc_rows:
            fp = row["path"]
            if fp in seen_files:
                continue
            # Check file content for the symbol name
            try:
                full_path = fp
                if os.path.isfile(full_path):
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                    if sym_name in content:
                        changes.append({
                            "change_type": "update_doc",
                            "file": fp,
                            "line": None,
                            "name": sym_name,
                            "kind": "string_ref",
                            "reason": f"contains string reference to '{sym_name}'",
                        })
                        seen_files.add(fp)
            except (OSError, IOError):
                pass

    return changes


def _closure_verdict(changes, sym_name):
    """Generate a verdict line from change list."""
    file_set = set(c["file"] for c in changes)
    return (
        f"closure for {sym_name} requires {len(changes)} change(s) "
        f"in {len(file_set)} file(s)"
    )


@click.command()
@click.argument('name')
@click.option('--rename', default=None, help='New name for rename closure')
@click.option('--delete', 'delete_mode', is_flag=True, help='Deletion closure')
@click.pass_context
def closure(ctx, name, rename, delete_mode):
    """Compute the minimal set of changes needed when modifying a symbol.

    Unlike `impact` (blast radius -- what MIGHT break), closure tells you
    what MUST change. Returns the exact files and locations that need updating.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)

        changes = _collect_closure(conn, sym, rename=rename, delete=delete_mode)
        file_set = set(c["file"] for c in changes)

        # Group by change type
        by_type = {}
        for c in changes:
            by_type.setdefault(c["change_type"], []).append(c)

        verdict = _closure_verdict(changes, sym["name"])
        mode = "rename" if rename else ("delete" if delete_mode else "modify")

        if json_mode:
            click.echo(to_json(json_envelope("closure",
                summary={
                    "verdict": verdict,
                    "total_changes": len(changes),
                    "files_affected": len(file_set),
                    "mode": mode,
                },
                symbol=sym["qualified_name"] or sym["name"],
                kind=sym["kind"],
                location=loc(sym["file_path"], sym["line_start"]),
                mode=mode,
                rename_to=rename,
                total_changes=len(changes),
                files_affected=len(file_set),
                changes=[
                    {
                        "change_type": c["change_type"],
                        "file": c["file"],
                        "line": c["line"],
                        "name": c["name"],
                        "kind": c["kind"],
                        "reason": c["reason"],
                    }
                    for c in changes
                ],
                by_type={
                    ct: [
                        {"name": c["name"], "file": c["file"],
                         "line": c["line"], "reason": c["reason"]}
                        for c in items
                    ]
                    for ct, items in by_type.items()
                },
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()
        click.echo(f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  "
                    f"{loc(sym['file_path'], sym['line_start'])}")
        if rename:
            click.echo(f"Mode: rename -> {rename}")
        elif delete_mode:
            click.echo(f"Mode: delete")
        else:
            click.echo(f"Mode: modify")
        click.echo()

        for change_type in sorted(by_type.keys()):
            items = by_type[change_type]
            click.echo(f"{change_type} ({len(items)}):")
            rows = []
            for c in items[:20]:
                rows.append([
                    abbrev_kind(c["kind"]),
                    c["name"] or "(file)",
                    loc(c["file"], c["line"]),
                    c["reason"],
                ])
            click.echo(format_table(["kind", "name", "location", "reason"], rows))
            if len(items) > 20:
                click.echo(f"  (+{len(items) - 20} more)")
            click.echo()

        click.echo(f"Total: {len(changes)} change(s) in {len(file_set)} file(s)")
