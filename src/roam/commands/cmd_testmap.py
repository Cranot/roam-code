"""Map symbols/files to their test coverage."""

from __future__ import annotations

import os

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_edge_kind, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol
from roam.commands.changed_files import is_test_file as _is_test_file


def _test_map_symbol(conn, sym):
    """Show test files that exercise a given symbol."""
    click.echo(f"Test coverage for: {sym['name']} ({abbrev_kind(sym['kind'])}, {loc(sym['file_path'], sym['line_start'])})")
    click.echo()

    # Direct tests: edges where source is in a test file and target is this symbol
    callers = conn.execute(
        "SELECT s.name, s.kind, f.path as file_path, e.kind as edge_kind, e.line as edge_line "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id = ?",
        (sym["id"],),
    ).fetchall()

    direct_tests = [c for c in callers if _is_test_file(c["file_path"])]
    if direct_tests:
        click.echo(f"Direct tests ({len(direct_tests)}):")
        for t in direct_tests:
            edge = format_edge_kind(t["edge_kind"])
            click.echo(f"  {t['name']:<25s} {abbrev_kind(t['kind'])}  {loc(t['file_path'], t['edge_line'])}   ({edge})")
    else:
        click.echo("Direct tests: (none)")

    click.echo()

    # Test files importing the symbol's file
    test_importers = []
    sym_file_id = conn.execute(
        "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
    ).fetchone()
    if sym_file_id:
        importers = conn.execute(
            "SELECT f.path, fe.symbol_count "
            "FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id = ?",
            (sym_file_id["id"],),
        ).fetchall()
        test_importers = [r for r in importers if _is_test_file(r["path"])]
        if test_importers:
            click.echo(f"Test files importing {sym['file_path']} ({len(test_importers)}):")
            for r in test_importers:
                click.echo(f"  {r['path']:<45s} {r['symbol_count']} symbols used")
        else:
            click.echo(f"Test files importing {sym['file_path']}: (none)")

    # Convention-based: look for NameTest or Name_Test classes (Salesforce convention)
    base_name = sym["name"]
    convention_tests = conn.execute(
        "SELECT s.name, s.kind, f.path FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE (s.name = ? OR s.name = ?) AND s.kind = 'class'",
        (f"{base_name}Test", f"{base_name}_Test"),
    ).fetchall()
    if convention_tests:
        click.echo()
        click.echo(f"Convention-based test classes ({len(convention_tests)}):")
        for ct in convention_tests:
            click.echo(f"  {ct['name']:<25s} {abbrev_kind(ct['kind'])}  {ct['path']}")

    # Suggest when no tests found
    if not direct_tests and not test_importers and not convention_tests:
        pr_row = conn.execute(
            "SELECT pagerank, in_degree FROM graph_metrics WHERE symbol_id = ?",
            (sym["id"],),
        ).fetchone()
        if pr_row and (pr_row["pagerank"] or 0) > 0:
            click.echo(f"\nNo tests found. This symbol has PR={pr_row['pagerank']:.4f}, in_degree={pr_row['in_degree']} — consider adding tests.")


def _test_map_file(conn, path):
    """Show test files that exercise a given source file."""
    frow = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT * FROM files WHERE path LIKE ? LIMIT 1", (f"%{path}",)
        ).fetchone()
    if frow is None:
        click.echo(f"File not found in index: {path}")
        raise SystemExit(1)

    click.echo(f"Test coverage for: {frow['path']}")
    click.echo()

    # Test files that import this file
    importers = conn.execute(
        "SELECT f.path, fe.symbol_count "
        "FROM file_edges fe "
        "JOIN files f ON fe.source_file_id = f.id "
        "WHERE fe.target_file_id = ?",
        (frow["id"],),
    ).fetchall()
    test_importers = [r for r in importers if _is_test_file(r["path"])]

    if test_importers:
        click.echo(f"Test files importing {frow['path']} ({len(test_importers)}):")
        for r in test_importers:
            # List test functions in that test file
            test_syms = conn.execute(
                "SELECT s.name, s.kind, s.line_start FROM symbols s "
                "WHERE s.file_id = (SELECT id FROM files WHERE path = ?) "
                "AND s.kind IN ('function', 'method') "
                "AND s.name LIKE 'test%' "
                "ORDER BY s.line_start",
                (r["path"],),
            ).fetchall()
            click.echo(f"  {r['path']:<45s} {r['symbol_count']} symbols used")
            for ts in test_syms:
                click.echo(f"    {abbrev_kind(ts['kind'])}  {ts['name']}  L{ts['line_start']}")
    else:
        click.echo(f"Test files importing {frow['path']}: (none)")

    click.echo()

    # Also show direct test references to symbols in this file
    sym_ids = conn.execute(
        "SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)
    ).fetchall()
    test_caller_files = []
    if sym_ids:
        ph = ",".join("?" for _ in sym_ids)
        ids = [s["id"] for s in sym_ids]
        test_callers = conn.execute(
            f"SELECT DISTINCT f.path "
            f"FROM edges e "
            f"JOIN symbols s ON e.source_id = s.id "
            f"JOIN files f ON s.file_id = f.id "
            f"WHERE e.target_id IN ({ph})",
            ids,
        ).fetchall()
        test_caller_files = [r["path"] for r in test_callers if _is_test_file(r["path"])]
        if test_caller_files:
            click.echo(f"Test files referencing symbols in {frow['path']} ({len(test_caller_files)}):")
            for tf in test_caller_files:
                click.echo(f"  {tf}")

    # Suggest high-risk untested symbols when no tests found
    if not test_importers and not test_caller_files:
        risky = conn.execute(
            "SELECT s.name, s.kind, gm.pagerank, gm.in_degree "
            "FROM symbols s "
            "JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "WHERE s.file_id = ? AND s.kind IN ('function', 'class', 'method') "
            "ORDER BY gm.pagerank DESC LIMIT 5",
            (frow["id"],),
        ).fetchall()
        if risky:
            click.echo(f"\nSuggested symbols to test (by importance):")
            for r in risky:
                pr = r["pagerank"] or 0
                click.echo(f"  {abbrev_kind(r['kind'])}  {r['name']}  (PR={pr:.4f}, in={r['in_degree']})")


@click.command("test-map")
@click.argument('name')
@click.pass_context
def test_map(ctx, name):
    """Map a symbol or file to its test coverage."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    name_norm = name.replace("\\", "/")

    with open_db(readonly=True) as conn:
        if "/" in name_norm or "." in name_norm:
            frow = conn.execute("SELECT id FROM files WHERE path = ?", (name_norm,)).fetchone()
            if frow is None:
                frow = conn.execute(
                    "SELECT id FROM files WHERE path LIKE ? LIMIT 1", (f"%{name_norm}",)
                ).fetchone()
            if frow:
                if json_mode:
                    _test_map_file_json(conn, name_norm)
                else:
                    _test_map_file(conn, name_norm)
                return

        sym = find_symbol(conn, name)
        if sym:
            if json_mode:
                _test_map_symbol_json(conn, sym)
            else:
                _test_map_symbol(conn, sym)
            return

        click.echo(f"Not found: {name}")
        raise SystemExit(1)


def _test_map_symbol_json(conn, sym):
    """JSON output for test-map on a symbol."""
    callers = conn.execute(
        "SELECT s.name, s.kind, f.path as file_path, e.kind as edge_kind "
        "FROM edges e JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id WHERE e.target_id = ?",
        (sym["id"],),
    ).fetchall()
    direct_tests = [c for c in callers if _is_test_file(c["file_path"])]

    sym_file_id = conn.execute(
        "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
    ).fetchone()
    test_importers = []
    if sym_file_id:
        importers = conn.execute(
            "SELECT f.path, fe.symbol_count FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id WHERE fe.target_file_id = ?",
            (sym_file_id["id"],),
        ).fetchall()
        test_importers = [r for r in importers if _is_test_file(r["path"])]

    # Convention-based: look for NameTest or Name_Test classes (Salesforce convention)
    base_name = sym["name"]
    convention_tests = conn.execute(
        "SELECT s.name, s.kind, f.path FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE (s.name = ? OR s.name = ?) AND s.kind = 'class'",
        (f"{base_name}Test", f"{base_name}_Test"),
    ).fetchall()

    click.echo(to_json(json_envelope("test-map",
        summary={
            "direct_tests": len(direct_tests),
            "test_importers": len(test_importers),
            "convention_tests": len(convention_tests),
        },
        name=sym["name"], kind=sym["kind"],
        location=loc(sym["file_path"], sym["line_start"]),
        direct_tests=[
            {"name": t["name"], "kind": t["kind"], "file": t["file_path"],
             "edge_kind": t["edge_kind"]}
            for t in direct_tests
        ],
        test_importers=[
            {"path": r["path"], "symbols_used": r["symbol_count"]}
            for r in test_importers
        ],
        convention_tests=[
            {"name": ct["name"], "kind": ct["kind"], "path": ct["path"]}
            for ct in convention_tests
        ],
    )))


def _test_map_file_json(conn, path):
    """JSON output for test-map on a file."""
    frow = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT * FROM files WHERE path LIKE ? LIMIT 1", (f"%{path}",)
        ).fetchone()
    if frow is None:
        click.echo(to_json(json_envelope("test-map",
            summary={"error": True},
            error=f"File not found: {path}",
        )))
        return

    importers = conn.execute(
        "SELECT f.path, fe.symbol_count FROM file_edges fe "
        "JOIN files f ON fe.source_file_id = f.id WHERE fe.target_file_id = ?",
        (frow["id"],),
    ).fetchall()
    test_importers = [r for r in importers if _is_test_file(r["path"])]

    sym_ids = conn.execute(
        "SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)
    ).fetchall()
    test_caller_files = []
    if sym_ids:
        ph = ",".join("?" for _ in sym_ids)
        ids = [s["id"] for s in sym_ids]
        test_callers = conn.execute(
            f"SELECT DISTINCT f.path FROM edges e "
            f"JOIN symbols s ON e.source_id = s.id "
            f"JOIN files f ON s.file_id = f.id WHERE e.target_id IN ({ph})",
            ids,
        ).fetchall()
        test_caller_files = [r["path"] for r in test_callers if _is_test_file(r["path"])]

    click.echo(to_json(json_envelope("test-map",
        summary={
            "test_importers": len(test_importers),
            "test_callers": len(test_caller_files),
        },
        path=frow["path"],
        test_importers=[
            {"path": r["path"], "symbols_used": r["symbol_count"]}
            for r in test_importers
        ],
        test_callers=test_caller_files,
    )))
