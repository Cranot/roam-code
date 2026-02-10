"""Get the minimal context needed to safely modify a symbol."""

import os

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json
from roam.commands.resolve import ensure_index, find_symbol


_TEST_NAME_PATS = ["test_", "_test.", ".test.", ".spec."]
_TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/"]


def _is_test_file(path):
    p = path.replace("\\", "/")
    bn = os.path.basename(p)
    return any(pat in bn for pat in _TEST_NAME_PATS) or any(d in p for d in _TEST_DIR_PATS)


@click.command()
@click.argument('name')
@click.pass_context
def context(ctx, name):
    """Get the minimal context needed to safely modify a symbol.

    Returns definition, callers, callees, tests, and the exact files
    to read — everything an AI agent needs in one shot.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)

        sym_id = sym["id"]
        line_start = sym["line_start"]
        line_end = sym["line_end"] or line_start

        # --- Callers ---
        callers = conn.execute(
            "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
            "f.path as file_path, e.kind as edge_kind, e.line as edge_line "
            "FROM edges e "
            "JOIN symbols s ON e.source_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE e.target_id = ? "
            "ORDER BY f.path, s.line_start",
            (sym_id,),
        ).fetchall()

        # --- Callees ---
        callees = conn.execute(
            "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
            "f.path as file_path, e.kind as edge_kind, e.line as edge_line "
            "FROM edges e "
            "JOIN symbols s ON e.target_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE e.source_id = ? "
            "ORDER BY f.path, s.line_start",
            (sym_id,),
        ).fetchall()

        # --- Split callers into tests vs non-tests ---
        test_callers = [c for c in callers if _is_test_file(c["file_path"])]
        non_test_callers = [c for c in callers if not _is_test_file(c["file_path"])]

        # Rank callers by PageRank for high-fan symbols
        if len(non_test_callers) > 10:
            caller_ids = [c["id"] for c in non_test_callers]
            ph = ",".join("?" for _ in caller_ids)
            pr_rows = conn.execute(
                f"SELECT symbol_id, pagerank FROM graph_metrics "
                f"WHERE symbol_id IN ({ph})",
                caller_ids,
            ).fetchall()
            pr_map = {r["symbol_id"]: r["pagerank"] or 0 for r in pr_rows}
            non_test_callers = sorted(
                non_test_callers,
                key=lambda c: -pr_map.get(c["id"], 0),
            )

        # --- Test files that import the symbol's file ---
        sym_file_row = conn.execute(
            "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
        ).fetchone()
        test_importers = []
        if sym_file_row:
            importers = conn.execute(
                "SELECT f.path, fe.symbol_count "
                "FROM file_edges fe "
                "JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id = ?",
                (sym_file_row["id"],),
            ).fetchall()
            test_importers = [r for r in importers if _is_test_file(r["path"])]

        # --- Siblings (other exports in same file) ---
        siblings = conn.execute(
            "SELECT name, kind, line_start FROM symbols "
            "WHERE file_id = ? AND is_exported = 1 AND id != ? "
            "ORDER BY line_start",
            (sym["file_id"], sym_id),
        ).fetchall()

        # --- Build "files to read" list (capped for high-fan symbols) ---
        _MAX_CALLER_FILES = 10
        _MAX_CALLEE_FILES = 5
        _MAX_TEST_FILES = 5
        skipped_callers = 0
        skipped_callees = 0

        files_to_read = [{
            "path": sym["file_path"],
            "start": line_start,
            "end": line_end,
            "reason": "definition",
        }]
        seen = {sym["file_path"]}
        caller_files = 0
        for c in non_test_callers:
            if c["file_path"] not in seen:
                if caller_files >= _MAX_CALLER_FILES:
                    skipped_callers += 1
                    continue
                seen.add(c["file_path"])
                files_to_read.append({
                    "path": c["file_path"],
                    "start": c["line_start"],
                    "end": c["line_end"] or c["line_start"],
                    "reason": "caller",
                })
                caller_files += 1
        callee_files = 0
        for c in callees:
            if c["file_path"] not in seen:
                if callee_files >= _MAX_CALLEE_FILES:
                    skipped_callees += 1
                    continue
                seen.add(c["file_path"])
                files_to_read.append({
                    "path": c["file_path"],
                    "start": c["line_start"],
                    "end": c["line_end"] or c["line_start"],
                    "reason": "callee",
                })
                callee_files += 1
        test_files = 0
        for t in test_callers:
            if t["file_path"] not in seen and test_files < _MAX_TEST_FILES:
                seen.add(t["file_path"])
                files_to_read.append({
                    "path": t["file_path"],
                    "start": t["line_start"],
                    "end": t["line_end"] or t["line_start"],
                    "reason": "test",
                })
                test_files += 1
        for ti in test_importers:
            if ti["path"] not in seen and test_files < _MAX_TEST_FILES:
                seen.add(ti["path"])
                files_to_read.append({
                    "path": ti["path"], "start": 1, "end": None,
                    "reason": "test",
                })
                test_files += 1

        if json_mode:
            click.echo(to_json({
                "symbol": sym["qualified_name"] or sym["name"],
                "kind": sym["kind"],
                "signature": sym["signature"] or "",
                "location": loc(sym["file_path"], line_start),
                "definition": {
                    "file": sym["file_path"],
                    "start": line_start, "end": line_end,
                },
                "callers": [
                    {"name": c["name"], "kind": c["kind"],
                     "location": loc(c["file_path"], c["edge_line"] or c["line_start"]),
                     "edge_kind": c["edge_kind"] or ""}
                    for c in non_test_callers
                ],
                "callees": [
                    {"name": c["name"], "kind": c["kind"],
                     "location": loc(c["file_path"], c["line_start"]),
                     "edge_kind": c["edge_kind"] or ""}
                    for c in callees
                ],
                "tests": [
                    {"name": t["name"], "kind": t["kind"],
                     "location": loc(t["file_path"], t["line_start"]),
                     "edge_kind": t["edge_kind"] or ""}
                    for t in test_callers
                ],
                "test_files": [r["path"] for r in test_importers],
                "siblings": [
                    {"name": s["name"], "kind": s["kind"]}
                    for s in siblings[:10]
                ],
                "files_to_read": [
                    {"path": f["path"], "start": f["start"],
                     "end": f["end"], "reason": f["reason"]}
                    for f in files_to_read
                ],
            }))
            return

        # --- Text output ---
        sig = sym["signature"] or ""
        click.echo(f"=== Context for: {sym['name']} ===")
        click.echo(f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}"
                    f"{'  ' + sig if sig else ''}  {loc(sym['file_path'], line_start)}")
        click.echo()

        if non_test_callers:
            click.echo(f"Callers ({len(non_test_callers)}):")
            rows = []
            for c in non_test_callers[:20]:
                rows.append([
                    abbrev_kind(c["kind"]), c["name"],
                    loc(c["file_path"], c["edge_line"] or c["line_start"]),
                    c["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(non_test_callers) > 20:
                click.echo(f"  (+{len(non_test_callers) - 20} more)")
            click.echo()
        else:
            click.echo("Callers: (none)")
            click.echo()

        if callees:
            click.echo(f"Callees ({len(callees)}):")
            rows = []
            for c in callees[:15]:
                rows.append([
                    abbrev_kind(c["kind"]), c["name"],
                    loc(c["file_path"], c["line_start"]),
                    c["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(callees) > 15:
                click.echo(f"  (+{len(callees) - 15} more)")
            click.echo()
        else:
            click.echo("Callees: (none)")
            click.echo()

        if test_callers or test_importers:
            click.echo(f"Tests ({len(test_callers)} direct, {len(test_importers)} file-level):")
            for t in test_callers:
                click.echo(f"  {abbrev_kind(t['kind'])}  {t['name']}  "
                            f"{loc(t['file_path'], t['line_start'])}")
            for ti in test_importers:
                click.echo(f"  file  {ti['path']}")
        else:
            click.echo("Tests: (none)")
        click.echo()

        if siblings:
            click.echo(f"Siblings ({len(siblings)} exports in same file):")
            for s in siblings[:10]:
                click.echo(f"  {abbrev_kind(s['kind'])}  {s['name']}")
            if len(siblings) > 10:
                click.echo(f"  (+{len(siblings) - 10} more)")
            click.echo()

        skipped_total = skipped_callers + skipped_callees
        extra = f", +{skipped_total} more" if skipped_total else ""
        click.echo(f"Files to read ({len(files_to_read)}{extra}):")
        for f in files_to_read:
            end_str = f"-{f['end']}" if f["end"] and f["end"] != f["start"] else ""
            lr = f":{f['start']}{end_str}" if f["start"] else ""
            click.echo(f"  {f['path']:<50s} {lr:<12s} ({f['reason']})")
