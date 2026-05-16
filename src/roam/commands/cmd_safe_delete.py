"""Check if a symbol can be safely deleted.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because safe-delete is a validator-not-detector: its output is
a single-symbol verdict (SAFE / REVIEW / UNSAFE) for one target on
each invocation, not a codebase-wide scan. SARIF consumers expect a
corpus of per-finding results at file:line coordinates; safe-delete
returns one deletion decision per invocation. See ``cmd_syntax_check``
for the parallel validator-not-detector disclosure pattern (W1192) +
action.yml _SUPPORTED_SARIF allowlist + W1224-audit memo.
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file as _is_test_file
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)


@roam_capability(
    name="safe-delete",
    category="refactoring",
    summary="Check if a symbol can be safely deleted",
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
@click.command("safe-delete")
@click.argument("name", metavar="SYMBOL")
@click.pass_context
def safe_delete(ctx, name):
    """Check if SYMBOL can be safely deleted.

    SYMBOL is a symbol identifier (bare name or qualified name).

    Combines dead-code check, impact analysis, and test coverage
    into a single verdict: SAFE / REVIEW / UNSAFE.

    Unlike ``dead`` (which finds all unreferenced symbols) and ``impact``
    (which shows transitive blast radius), this command fuses both signals
    with public-API heuristics into a single go/no-go deletion verdict.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            # W1272 — Pattern-2c Convention (c): unresolved exits 0 with a
            # resolution=unresolved + partial_success disclosure. A safe-
            # delete verdict on a missing symbol is "I tried and there's
            # nothing to delete" (a valid no-op success), not a tool
            # failure. Keep the FTS suggestion list in text mode.
            unresolved_block = resolution_disclosure("unresolved", target=name or "")
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "safe-delete",
                            summary={
                                "verdict": f"Symbol '{name}' not found",
                                "partial_success": True,
                                "state": "not_found",
                                **unresolved_block,
                            },
                            symbol=name or "",
                            **unresolved_block,
                        )
                    )
                )
            else:
                click.echo(symbol_not_found(conn, name, json_mode=False))
            return

        sym_id = sym["id"]
        # W1245 / W1249 — Pattern-2 variant-D: ``find_symbol`` stamps
        # ``_resolution_tier`` on the returned row so a fuzzy-LIKE-fallback
        # safe-delete decision is distinguishable from an exact-symbol
        # match. A fuzzy match may land on a different symbol than the
        # caller meant; the SAFE/REVIEW/UNSAFE verdict is still computed
        # for the resolved symbol, but the disclosure tells the agent the
        # input was degraded.
        resolution_tier = sym.get("_resolution_tier", "symbol")
        resolved_target = sym["qualified_name"] or sym["name"]
        resolution_block = resolution_disclosure(resolution_tier, target=resolved_target)

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
        import networkx as nx

        from roam.graph.builder import build_symbol_graph

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
        file_row = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
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
                reason = (
                    f"No references. File is imported but {sibling_refs} "
                    f"sibling symbols are used — this one is skipped."
                )
            elif file_imported:
                verdict = "SAFE"
                reason = "No references. File is imported but no one uses this symbol."
            else:
                verdict = "SAFE"
                reason = "No references and file is not imported by anyone."
        elif len(non_test_callers) == 0 and dependent_count > 0:
            verdict = "REVIEW"
            reason = (
                f"No direct callers but {dependent_count} transitive dependents in graph — check for dynamic usage."
            )
        elif len(non_test_callers) <= 3:
            verdict = "REVIEW"
            names = ", ".join(c["name"] for c in non_test_callers[:3])
            reason = f"{len(non_test_callers)} caller(s): {names}"
        else:
            verdict = "UNSAFE"
            reason = (
                f"{len(non_test_callers)} direct callers, "
                f"{dependent_count} transitive dependents "
                f"across {len(affected_files)} files."
            )

        # Bump SAFE → REVIEW for likely public-API symbols, but only when at
        # least one usage signal is non-zero. When ALL hard signals are zero
        # (no callers, no transitives, no tests, no sibling refs, file not
        # imported anywhere) the naming pattern alone is too weak to flip
        # the verdict — the symbol and likely its file are orphaned.
        all_signals_zero = (
            not file_imported and sibling_refs == 0 and len(test_callers) == 0
            # non_test_callers and dependent_count are 0 by virtue of SAFE verdict above
        )
        if verdict == "SAFE" and sym["is_exported"] and not all_signals_zero:
            _api_prefixes = (
                "get",
                "use",
                "create",
                "validate",
                "fetch",
                "update",
                "delete",
                "find",
                "check",
                "make",
                "build",
                "parse",
                "format",
            )
            name_lower = sym["name"].lower()
            base_name = os.path.basename(sym["file_path"]).lower()
            is_barrel = base_name.startswith("index.") or base_name == "__init__.py"
            if any(name_lower.startswith(p) for p in _api_prefixes):
                verdict = "REVIEW"
                reason = (
                    "No references found, but exported with public-API naming pattern — may be consumed externally."
                )
            elif is_barrel:
                verdict = "REVIEW"
                reason = f"No references found, but exported from {base_name} — likely part of public API."

        test_note = ""
        if test_callers:
            test_note = f"{len(test_callers)} test(s) would break"
        elif len(non_test_callers) > 0:
            test_note = "No tests cover this symbol — deletion may go unnoticed"
        else:
            test_note = "No tests reference this symbol"

        if json_mode:
            # W1245 — Pattern-2 variant-D: surface ``resolution`` +
            # ``partial_success`` in BOTH the summary and the top level so
            # LAW-6 single-line consumers can detect a degraded fuzzy match
            # on the verdict alone. The categorical SAFE/REVIEW/UNSAFE
            # verdict gets a ``[fuzzy resolution -- ...]`` suffix when the
            # resolver landed on a LIKE fallback rather than an exact name.
            summary_verdict = verdict
            if resolution_tier == "fuzzy":
                summary_verdict = (
                    f"{verdict} [fuzzy resolution -- target '{resolved_target}' may not be what you meant]"
                )
            click.echo(
                to_json(
                    json_envelope(
                        "safe-delete",
                        summary={
                            "verdict": summary_verdict,
                            "direct_callers": len(non_test_callers),
                            "affected_files": len(affected_files),
                            **resolution_block,
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
                            {
                                "name": c["name"],
                                "kind": c["kind"],
                                "file": c["file_path"],
                                "edge_kind": c["edge_kind"],
                            }
                            for c in non_test_callers[:10]
                        ],
                        **resolution_block,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"=== Safe Delete: {sym['name']} ===")
        click.echo(
            f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  "
            f"{loc(sym['file_path'], sym['line_start'])}"
        )
        click.echo()
        # W1245 — text-mode mirror of the JSON ``[fuzzy resolution ...]``
        # suffix so LAW-6 single-line consumers (grepping VERDICT) still
        # see the disclosure on a degraded resolver hit.
        if resolution_tier == "fuzzy":
            click.echo(f"VERDICT: {verdict} [fuzzy resolution -- target '{resolved_target}' may not be what you meant]")
        else:
            click.echo(f"VERDICT: {verdict}")
        click.echo(f"  {reason}")
        click.echo()
        click.echo(f"References: {len(non_test_callers)} direct, {dependent_count} transitive")
        click.echo(f"Affected files: {len(affected_files)}")
        click.echo(f"Tests: {test_note}")
        click.echo(f"File imported: {'yes' if file_imported else 'no'} | Sibling refs: {sibling_refs}")

        if non_test_callers:
            click.echo(f"\nCallers ({len(non_test_callers)}):")
            rows = []
            for c in non_test_callers[:10]:
                rows.append(
                    [
                        abbrev_kind(c["kind"]),
                        c["name"],
                        c["file_path"],
                        c["edge_kind"] or "",
                    ]
                )
            click.echo(format_table(["kind", "name", "file", "edge"], rows))
            if len(non_test_callers) > 10:
                click.echo(f"  (+{len(non_test_callers) - 10} more)")
