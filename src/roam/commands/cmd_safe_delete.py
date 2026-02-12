"""Check if a symbol can be safely deleted."""

import os

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


_TEST_NAME_PATS = ["test_", "_test.", ".test.", ".spec."]
_TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/"]


def _is_test_file(path):
    p = path.replace("\\", "/")
    bn = os.path.basename(p)
    return any(pat in bn for pat in _TEST_NAME_PATS) or any(d in p for d in _TEST_DIR_PATS)


@click.command("safe-delete")
@click.argument('name')
@click.pass_context
def safe_delete(ctx, name):
    """Check if a symbol can be safely deleted.

    Combines dead-code check, impact analysis, and test coverage
    into a single verdict: SAFE / REVIEW / UNSAFE.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)

        sym_id = sym["id"]

        # --- Direct references ---
        callers = conn.execute(
            "SELECT s.name, s.kind, f.path as file_path, e.kind as edge_kind "
            "FROM edges e JOIN symbols s ON e.source_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE e.target_id = ?",
            (sym_id,),
        ).fetchall()

        test_callers = [c for c in callers if _is_test_file(c["file_path"])]
        non_test_callers = [c for c in callers if not _is_test_file(c["file_path"])]

        # --- Transitive impact ---
        from roam.graph.builder import build_symbol_graph
        import networkx as nx

        G = build_symbol_graph(conn)
        dependent_count = 0
        affected_files = set()
        if sym_id in G:
            RG = G.reverse()
            dependents = nx.descendants(RG, sym_id)
            dependent_count = len(dependents)
            for d in dependents:
                node = G.nodes.get(d, {})
                fp = node.get("file_path")
                if fp:
                    affected_files.add(fp)

        # --- File-level import check ---
        file_imported = False
        file_row = conn.execute(
            "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
        ).fetchone()
        if file_row:
            imp = conn.execute(
                "SELECT COUNT(*) FROM file_edges WHERE target_file_id = ?",
                (file_row["id"],),
            ).fetchone()[0]
            file_imported = imp > 0

        # --- Sibling check (other symbols in same file that ARE referenced) ---
        sibling_refs = 0
        if file_row:
            sibling_refs = conn.execute(
                "SELECT COUNT(*) FROM symbols s "
                "WHERE s.file_id = ? AND s.is_exported = 1 AND s.id != ? "
                "AND s.id IN (SELECT target_id FROM edges)",
                (file_row["id"], sym_id),
            ).fetchone()[0]

        # --- Verdict ---
        if len(non_test_callers) == 0 and dependent_count == 0:
            if file_imported and sibling_refs > 0:
                verdict = "SAFE"
                reason = (f"No references. File is imported but {sibling_refs} "
                          f"sibling symbols are used — this one is skipped.")
            elif file_imported:
                verdict = "SAFE"
                reason = "No references. File is imported but no one uses this symbol."
            else:
                verdict = "SAFE"
                reason = "No references and file is not imported by anyone."
        elif len(non_test_callers) == 0 and dependent_count > 0:
            verdict = "REVIEW"
            reason = (f"No direct callers but {dependent_count} transitive "
                      f"dependents in graph — check for dynamic usage.")
        elif len(non_test_callers) <= 3:
            verdict = "REVIEW"
            names = ", ".join(c["name"] for c in non_test_callers[:3])
            reason = f"{len(non_test_callers)} caller(s): {names}"
        else:
            verdict = "UNSAFE"
            reason = (f"{len(non_test_callers)} direct callers, "
                      f"{dependent_count} transitive dependents "
                      f"across {len(affected_files)} files.")

        # Bump SAFE → REVIEW for likely public-API symbols
        if verdict == "SAFE" and sym["is_exported"]:
            _api_prefixes = ("get", "use", "create", "validate",
                             "fetch", "update", "delete", "find",
                             "check", "make", "build", "parse", "format")
            name_lower = sym["name"].lower()
            base_name = os.path.basename(sym["file_path"]).lower()
            is_barrel = base_name.startswith("index.") or base_name == "__init__.py"
            if any(name_lower.startswith(p) for p in _api_prefixes):
                verdict = "REVIEW"
                reason = ("No references found, but exported with public-API "
                          "naming pattern — may be consumed externally.")
            elif is_barrel:
                verdict = "REVIEW"
                reason = ("No references found, but exported from "
                          f"{base_name} — likely part of public API.")

        test_note = ""
        if test_callers:
            test_note = f"{len(test_callers)} test(s) would break"
        elif len(non_test_callers) > 0:
            test_note = "No tests cover this symbol — deletion may go unnoticed"
        else:
            test_note = "No tests reference this symbol"

        if json_mode:
            click.echo(to_json(json_envelope("safe-delete",
                summary={
                    "verdict": verdict,
                    "direct_callers": len(non_test_callers),
                    "affected_files": len(affected_files),
                },
                symbol=sym["qualified_name"] or sym["name"],
                kind=sym["kind"],
                location=loc(sym["file_path"], sym["line_start"]),
                verdict=verdict,
                reason=reason,
                direct_callers=len(non_test_callers),
                transitive_dependents=dependent_count,
                affected_files=len(affected_files),
                test_callers=len(test_callers),
                test_note=test_note,
                file_imported=file_imported,
                sibling_refs=sibling_refs,
                callers=[
                    {"name": c["name"], "kind": c["kind"],
                     "file": c["file_path"], "edge_kind": c["edge_kind"]}
                    for c in non_test_callers[:10]
                ],
            )))
            return

        # --- Text output ---
        click.echo(f"=== Safe Delete: {sym['name']} ===")
        click.echo(f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  "
                    f"{loc(sym['file_path'], sym['line_start'])}")
        click.echo()
        click.echo(f"Verdict: {verdict}")
        click.echo(f"  {reason}")
        click.echo()
        click.echo(f"References: {len(non_test_callers)} direct, "
                    f"{dependent_count} transitive")
        click.echo(f"Affected files: {len(affected_files)}")
        click.echo(f"Tests: {test_note}")
        click.echo(f"File imported: {'yes' if file_imported else 'no'} "
                    f"| Sibling refs: {sibling_refs}")

        if non_test_callers:
            click.echo(f"\nCallers ({len(non_test_callers)}):")
            rows = []
            for c in non_test_callers[:10]:
                rows.append([
                    abbrev_kind(c["kind"]), c["name"],
                    c["file_path"], c["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "file", "edge"], rows))
            if len(non_test_callers) > 10:
                click.echo(f"  (+{len(non_test_callers) - 10} more)")
