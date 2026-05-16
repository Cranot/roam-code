"""Map symbols/files to their test coverage.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because testmap outputs are invocation-scoped test-coverage
relationship rollups (which test files exercise a target symbol /
file, direct vs indirect, with edge-kind breakdown) — not per-
location code violations. The map describes a normal architectural
relationship (which tests cover which production code) rather than a
defect at a source coordinate; SARIF audiences scan for per-finding
rule_id + region rows. See ``cmd_test_gaps`` for the parallel
absence-of-edge disclosure pattern (W1230) + action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1224-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file as _is_test_file
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    format_edge_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)


def _test_map_symbol(conn, sym, *, resolution_tier: str = "symbol"):
    """Show test files that exercise a given symbol.

    W1245 Pattern-2 variant-D: ``resolution_tier`` lets the text verdict
    surface a ``[fuzzy resolution]`` suffix when the resolver matched
    via LIKE-fallback rather than an exact-name rung.
    """
    fuzzy_suffix = " [fuzzy resolution]" if resolution_tier == "fuzzy" else ""
    # Pre-compute test counts for verdict (lightweight queries)
    callers_pre = conn.execute(
        "SELECT s.name, f.path as file_path FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id WHERE e.target_id = ?",
        (sym["id"],),
    ).fetchall()
    direct_pre = [c for c in callers_pre if _is_test_file(c["file_path"])]

    # Round 4 #19: a test file importing the SAME module but not
    # exercising this specific symbol used to leave the verdict
    # ambiguous ("no tests found" + "1 test file importing 2 symbols").
    # We pre-count the file-level importers up front so the verdict
    # can include indirect-coverage context.
    indirect_pre: list[dict] = []
    sym_file_row = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
    if sym_file_row is not None:
        indirect_pre = [
            r
            for r in conn.execute(
                "SELECT f.path, fe.symbol_count "
                "FROM file_edges fe "
                "JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id = ?",
                (sym_file_row["id"],),
            ).fetchall()
            if _is_test_file(r["path"])
        ]

    if direct_pre:
        sym_verdict = f"{len(direct_pre)} direct test{'s' if len(direct_pre) != 1 else ''} for {sym['name']}"
    elif indirect_pre:
        sym_verdict = (
            f"no direct tests for {sym['name']}; {len(indirect_pre)} test file(s) "
            "import the same module — coverage is indirect at best, this symbol is not exercised"
        )
    else:
        sym_verdict = f"no tests found for {sym['name']}"
    click.echo(f"VERDICT: {sym_verdict}{fuzzy_suffix}\n")
    click.echo(
        f"Test coverage for: {sym['name']} ({abbrev_kind(sym['kind'])}, {loc(sym['file_path'], sym['line_start'])})"
    )
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
    sym_file_id = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
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
            click.echo(
                f"\nNo tests found. This symbol has PR={pr_row['pagerank']:.4f}, in_degree={pr_row['in_degree']} — consider adding tests."
            )


def _test_map_file(conn, path):
    """Show test files that exercise a given source file."""
    frow = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
    if frow is None:
        frow = conn.execute("SELECT * FROM files WHERE path LIKE ? LIMIT 1", (f"%{path}",)).fetchone()
    if frow is None:
        click.echo(f"File not found in index: {path}")
        raise SystemExit(1)

    # Pre-compute for verdict
    importers_pre = conn.execute(
        "SELECT f.path FROM file_edges fe JOIN files f ON fe.source_file_id = f.id WHERE fe.target_file_id = ?",
        (frow["id"],),
    ).fetchall()
    test_importers_pre = [r for r in importers_pre if _is_test_file(r["path"])]
    file_verdict = (
        f"{len(test_importers_pre)} test file{'s' if len(test_importers_pre) != 1 else ''} import {frow['path']}"
        if test_importers_pre
        else f"no tests found for {frow['path']}"
    )
    click.echo(f"VERDICT: {file_verdict}\n")
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
    sym_ids = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)).fetchall()
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
            click.echo("\nSuggested symbols to test (by importance):")
            for r in risky:
                pr = r["pagerank"] or 0
                click.echo(f"  {abbrev_kind(r['kind'])}  {r['name']}  (PR={pr:.4f}, in={r['in_degree']})")


@roam_capability(
    name="test-map",
    category="refactoring",
    summary="Map a symbol or file to its test coverage",
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
@click.command("test-map")
@click.argument("name", metavar="SYMBOL_OR_PATH")
@click.pass_context
def test_map(ctx, name):
    """Map a symbol identifier or file path to its test coverage.

    Unlike ``test-gaps`` (which finds untested symbols in changed files) and
    ``affected-tests`` (which traces forward from changes to affected test files),
    this command looks up the specific test functions and files that currently
    exercise a given symbol.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    name_norm = name.replace("\\", "/")

    with open_db(readonly=True) as conn:
        if "/" in name_norm or "." in name_norm:
            frow = conn.execute("SELECT id FROM files WHERE path = ?", (name_norm,)).fetchone()
            if frow is None:
                frow = conn.execute("SELECT id FROM files WHERE path LIKE ? LIMIT 1", (f"%{name_norm}",)).fetchone()
            if frow:
                if json_mode:
                    _test_map_file_json(conn, name_norm)
                else:
                    _test_map_file(conn, name_norm)
                return

        sym = find_symbol(conn, name)
        if sym:
            # W1245 Pattern-2 variant-D: thread the resolver tier so the
            # symbol-mode envelope can disclose fuzzy LIKE-fallback matches.
            resolution_tier = sym.get("_resolution_tier", "symbol")
            if json_mode:
                _test_map_symbol_json(conn, sym, resolution_tier=resolution_tier)
            else:
                _test_map_symbol(conn, sym, resolution_tier=resolution_tier)
            return

        # W1245: unresolved target -- emit an explicit ``resolution=unresolved``
        # envelope so consumers can distinguish from an exact-match success
        # with zero coverage.
        verdict = f"Not found: {name}"
        if json_mode:
            unresolved_block = resolution_disclosure("unresolved", target=name)
            click.echo(
                to_json(
                    json_envelope(
                        "test-map",
                        summary={
                            "verdict": verdict,
                            "found": False,
                            **unresolved_block,
                        },
                        callers=[],
                        **unresolved_block,
                    )
                )
            )
        else:
            click.echo(verdict)
        raise SystemExit(1)


def _test_map_symbol_json(conn, sym, *, resolution_tier: str = "symbol"):
    """JSON output for test-map on a symbol.

    W1245 Pattern-2 variant-D: ``resolution_tier`` discloses which
    resolver rung matched (``symbol`` for exact-name, ``fuzzy`` for
    LIKE-fallback). The envelope summary and top-level both carry the
    disclosure so IDE consumers reading only the verdict still see the
    degradation via the ``[fuzzy resolution]`` suffix.
    """
    callers = conn.execute(
        "SELECT s.name, s.kind, f.path as file_path, e.kind as edge_kind "
        "FROM edges e JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id WHERE e.target_id = ?",
        (sym["id"],),
    ).fetchall()
    direct_tests = [c for c in callers if _is_test_file(c["file_path"])]

    sym_file_id = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
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

    total_test_coverage = len(direct_tests) + len(test_importers) + len(convention_tests)
    resolved_name = sym.get("qualified_name") or sym["name"]
    fuzzy_suffix = " [fuzzy resolution]" if resolution_tier == "fuzzy" else ""
    sym_verdict = (
        f"{total_test_coverage} test reference{'s' if total_test_coverage != 1 else ''} for {sym['name']}: "
        f"{len(direct_tests)} direct, {len(test_importers)} importer{'s' if len(test_importers) != 1 else ''}"
        f"{fuzzy_suffix}"
        if total_test_coverage
        else f"no tests found for {sym['name']}{fuzzy_suffix}"
    )
    resolution_block = resolution_disclosure(resolution_tier, target=resolved_name)
    click.echo(
        to_json(
            json_envelope(
                "test-map",
                summary={
                    "verdict": sym_verdict,
                    "direct_tests": len(direct_tests),
                    "test_importers": len(test_importers),
                    "convention_tests": len(convention_tests),
                    **resolution_block,
                },
                name=sym["name"],
                kind=sym["kind"],
                location=loc(sym["file_path"], sym["line_start"]),
                direct_tests=[
                    {
                        "name": t["name"],
                        "kind": t["kind"],
                        "file": t["file_path"],
                        "edge_kind": t["edge_kind"],
                    }
                    for t in direct_tests
                ],
                test_importers=[{"path": r["path"], "symbols_used": r["symbol_count"]} for r in test_importers],
                convention_tests=[
                    {"name": ct["name"], "kind": ct["kind"], "path": ct["path"]} for ct in convention_tests
                ],
                **resolution_block,
            )
        )
    )


def _test_map_file_json(conn, path):
    """JSON output for test-map on a file."""
    frow = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
    if frow is None:
        frow = conn.execute("SELECT * FROM files WHERE path LIKE ? LIMIT 1", (f"%{path}",)).fetchone()
    if frow is None:
        click.echo(
            to_json(
                json_envelope(
                    "test-map",
                    summary={"error": True},
                    error=f"File not found: {path}",
                )
            )
        )
        return

    importers = conn.execute(
        "SELECT f.path, fe.symbol_count FROM file_edges fe "
        "JOIN files f ON fe.source_file_id = f.id WHERE fe.target_file_id = ?",
        (frow["id"],),
    ).fetchall()
    test_importers = [r for r in importers if _is_test_file(r["path"])]

    sym_ids = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)).fetchall()
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

    total_file_coverage = len(test_importers) + len(test_caller_files)
    file_verdict = (
        f"{total_file_coverage} test file{'s' if total_file_coverage != 1 else ''} cover {frow['path']}: "
        f"{len(test_importers)} importer{'s' if len(test_importers) != 1 else ''}, "
        f"{len(test_caller_files)} caller file{'s' if len(test_caller_files) != 1 else ''}"
        if total_file_coverage
        else f"no tests found for {frow['path']}"
    )
    click.echo(
        to_json(
            json_envelope(
                "test-map",
                summary={
                    "verdict": file_verdict,
                    "test_importers": len(test_importers),
                    "test_callers": len(test_caller_files),
                },
                path=frow["path"],
                test_importers=[{"path": r["path"], "symbols_used": r["symbol_count"]} for r in test_importers],
                test_callers=test_caller_files,
            )
        )
    )
