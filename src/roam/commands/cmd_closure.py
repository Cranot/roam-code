"""Compute the minimal set of changes needed when modifying a symbol.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because closure outputs are invocation-scoped change-closure
envelopes — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
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
    changes.append(
        {
            "change_type": "update_definition" if not delete else "delete_definition",
            "file": sym["file_path"],
            "line": sym["line_start"],
            "name": sym_name,
            "kind": sym["kind"],
            "reason": "symbol definition",
        }
    )
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
        changes.append(
            {
                "change_type": change_type,
                "file": fp,
                "line": caller["line_start"],
                "name": caller["name"],
                "kind": caller["kind"],
                "reason": reason,
            }
        )
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
            changes.append(
                {
                    "change_type": "update_test",
                    "file": fp,
                    "line": None,
                    "name": "",
                    "kind": "test_file",
                    "reason": f"test file referencing {sym_name}",
                }
            )
            seen_files.add(fp)

    # 4. Re-exports — files that import this symbol's file and re-export symbols
    file_row = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
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
                    "SELECT s.name FROM symbols s WHERE s.file_id = ? AND s.name = ? AND s.is_exported = 1",
                    (imp["id"], sym_name),
                ).fetchone()
                if re_export:
                    changes.append(
                        {
                            "change_type": "update_import",
                            "file": fp,
                            "line": None,
                            "name": sym_name,
                            "kind": "re_export",
                            "reason": f"re-exports {sym_name}",
                        }
                    )
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
                        changes.append(
                            {
                                "change_type": "update_doc",
                                "file": fp,
                                "line": None,
                                "name": sym_name,
                                "kind": "string_ref",
                                "reason": f"contains string reference to '{sym_name}'",
                            }
                        )
                        seen_files.add(fp)
            except (OSError, IOError):
                pass

    return changes


def _closure_verdict(changes, sym_name):
    """Generate a verdict line from change list."""
    file_set = set(c["file"] for c in changes)
    return f"closure for {sym_name} requires {len(changes)} change(s) in {len(file_set)} file(s)"


@roam_capability(
    name="closure",
    category="refactoring",
    summary="Compute the minimal set of changes needed when modifying a symbol",
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
@click.command()
@click.argument("name", metavar="SYMBOL")
@click.option("--rename", default=None, help="New name for rename closure")
@click.option("--delete", "delete_mode", is_flag=True, help="Deletion closure")
@click.pass_context
def closure(ctx, name, rename, delete_mode):
    """Compute the minimal set of changes needed when modifying SYMBOL.

    SYMBOL is a symbol identifier (bare name or qualified name). Unlike
    ``impact`` (which shows what might break), this command computes the
    minimal set of files and lines that must change when modifying the
    symbol.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            # W1272 — Pattern-2c Convention (c): unresolved exits 0 with a
            # resolution=unresolved + partial_success disclosure. A
            # closure on a missing symbol is "I tried and there's
            # nothing to change" (a valid no-op success), not a tool
            # failure. Keep the FTS suggestion list in text mode.
            unresolved_block = resolution_disclosure("unresolved", target=name or "")
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "closure",
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

        # W1245 / W1249 — Pattern-2 variant-D: ``find_symbol`` stamps
        # ``_resolution_tier`` on the returned row so a fuzzy-LIKE-fallback
        # closure is distinguishable from an exact-symbol match. A fuzzy
        # match still produces a valid change set, but for a symbol that
        # may not be the one the caller intended — the disclosure tells
        # the agent the input was degraded so it can re-confirm before
        # editing.
        resolution_tier = sym.get("_resolution_tier", "symbol")
        resolved_target = sym["qualified_name"] or sym["name"]
        resolution_block = resolution_disclosure(resolution_tier, target=resolved_target)

        # W607-EM -- substrate-boundary plumbing for cmd_closure.
        # ``_run_check_em`` wraps each substrate helper so an uncaught
        # raise in any one boundary degrades to a sensible empty-floor
        # default AND surfaces a marker in ``_w607em_warnings_out``
        # rather than crashing the closure command outright. cmd_closure
        # is the transitive-closure command (forward/backward dependency
        # closure traversal), one leg of the structural-analysis family
        # alongside cmd_cut (W607-EI, minimum edge cuts) and cmd_simulate
        # (W607-EF, counterfactual transforms). A raise inside
        # ``_collect_closure`` (DB callers query + test-pattern query +
        # re-export query + doc-rename scan), the change-type grouping,
        # or any downstream verdict / envelope composer used to crash
        # the closure command outright. Marker family
        # ``closure_<phase>_failed:<exc_class>:<detail>``. Substrates
        # wrapped:
        #
        #   * resolve_seed_symbols    -- the seed-symbol resolution
        #                                disclosure (already executed
        #                                above; the substrate captures
        #                                resolution-tier handling)
        #   * build_dependency_graph  -- _collect_closure (DB-driven
        #                                callers/tests/re-exports/docs)
        #   * compute_transitive_closure -- change-type grouping (by_type)
        #   * extract_closure_metrics -- file_set + counts
        #   * compose_verdict         -- LAW 6 single-line floor
        #   * compose_facts           -- agent_contract.facts list
        #   * compose_next_commands   -- agent_contract.next_commands
        #   * serialize_envelope      -- JSON envelope emission
        #   * format_text_output      -- text path table printing
        #
        # W978 7-discipline applied: (1) f-string verdict floor uses
        # literal zero-count text -- no Name references, (2) default=...
        # carries plain literals, (3) no json.dumps(default=str) needed
        # (no datetimes), (4) ``closure_*`` prefix is unique
        # (collision-checked by cross-prefix-discipline test), (5) len()
        # at kwarg-bind is gated by the envelope fallback, (6) len() /
        # if x: on a poisoned object only runs after the empty-floor
        # guard, (7) no dict.get(key, expensive_default) calls -- all
        # defaults are immutable literals.
        _w607em_warnings_out: list[str] = []

        def _run_check_em(phase, fn, *args, default=None, **kwargs):
            """Run one substrate helper with W607-EM marker emission.

            On a clean call the result is returned as-is. On an uncaught
            exception, surface a
            ``closure_<phase>_failed:<exc_class>:<detail>`` marker via
            ``_w607em_warnings_out`` and return *default* -- the
            envelope still emits cleanly with the remaining substrates.
            """
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- top-level disclosure
                _w607em_warnings_out.append(f"closure_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

        # W607-EM: ``resolve_seed_symbols`` substrate -- capture the
        # resolution_tier / resolved_target handling. A raise here
        # degrades to the symbol tier with the bare name so the
        # downstream substrates still compose against a coherent
        # resolution disclosure.
        def _resolve_seed_symbols():
            tier_local = resolution_tier
            target_local = resolved_target
            block_local = resolution_block
            return (tier_local, target_local, block_local)

        seed_bundle = _run_check_em(
            "resolve_seed_symbols",
            _resolve_seed_symbols,
            default=("symbol", sym["name"], {}),
        )
        if seed_bundle is None:
            seed_bundle = ("symbol", sym["name"], {})
        _tier, _target, _block = seed_bundle

        # W607-EM: ``build_dependency_graph`` substrate -- _collect_closure
        # is the DB-driven change-set builder (callers + tests +
        # re-exports + docs). A raise inside any of the SQL queries or
        # the doc-rename file-read loop degrades to an empty change set
        # so the verdict still composes against zero-counted closure
        # analysis.
        changes = _run_check_em(
            "build_dependency_graph",
            _collect_closure,
            conn,
            sym,
            rename=rename,
            delete=delete_mode,
            default=[],
        )
        if not isinstance(changes, list):
            changes = []

        # W607-EM: ``compute_transitive_closure`` substrate -- group the
        # change list by change_type. A raise inside the loop (e.g. a
        # poison change dict) degrades to an empty grouping so the
        # verdict / envelope still compose.
        def _compute_transitive_closure():
            by_type_local: dict[str, list] = {}
            for c in changes:
                if not isinstance(c, dict):
                    continue
                by_type_local.setdefault(c.get("change_type", "unknown"), []).append(c)
            return by_type_local

        by_type = _run_check_em(
            "compute_transitive_closure",
            _compute_transitive_closure,
            default={},
        )
        if not isinstance(by_type, dict):
            by_type = {}

        # W607-EM: ``extract_closure_metrics`` substrate -- file_set +
        # counts + a safe-materialized changes list for downstream
        # consumers. A raise inside the set comprehension or the
        # safe-changes materialization (e.g. poison change list whose
        # __iter__ raises) degrades to an empty file_set + zero counts
        # + empty safe-changes so the verdict + envelope still
        # compose. Materializing ``safe_changes`` inside this wrap
        # protects every downstream iteration of ``changes`` from a
        # raise inside the original list's __iter__.
        def _extract_closure_metrics():
            safe_changes_local = [c for c in changes if isinstance(c, dict)]
            file_set_local = set(c["file"] for c in safe_changes_local if "file" in c)
            total_changes_local = len(safe_changes_local)
            files_affected_local = len(file_set_local)
            return (
                safe_changes_local,
                file_set_local,
                total_changes_local,
                files_affected_local,
            )

        metrics_bundle = _run_check_em(
            "extract_closure_metrics",
            _extract_closure_metrics,
            default=([], set(), 0, 0),
        )
        if metrics_bundle is None:
            metrics_bundle = ([], set(), 0, 0)
        safe_changes, file_set, total_changes, files_affected = metrics_bundle
        if not isinstance(safe_changes, list):
            safe_changes = []
        if not isinstance(file_set, set):
            file_set = set()

        mode = "rename" if rename else ("delete" if delete_mode else "modify")

        # W607-EM: ``compose_verdict`` substrate -- LAW 6 single-line
        # closure floor. A raise degrades to the literal zero-floor
        # string with explicit empty counts -- the W811/W817 Pattern-2
        # guard: never collapse to a SAFE/passed verdict on the
        # degraded path. W978 #1: f-string verdict floor uses plain
        # text, no Name references inside the literal.
        def _compose_verdict():
            sym_name_local = sym["name"]
            if total_changes == 0:
                verdict_local = f"closure for {sym_name_local} requires 0 changes in 0 files"
            else:
                verdict_local = _closure_verdict(safe_changes, sym_name_local)
            if resolution_tier == "fuzzy":
                verdict_local = (
                    f"{verdict_local} [fuzzy resolution -- target '{resolved_target}' may not be what you meant]"
                )
            return verdict_local

        verdict = _run_check_em(
            "compose_verdict",
            _compose_verdict,
            default=f"closure for {sym['name']} requires 0 changes in 0 files",
        )
        if not isinstance(verdict, str) or not verdict:
            verdict = f"closure for {sym['name']} requires 0 changes in 0 files"

        # W607-EM: ``compose_facts`` substrate -- curated
        # ``agent_contract.facts`` list. A raise degrades to a single
        # verdict-only fact so LAW 6 verdict-first invariant holds.
        def _compose_facts():
            facts_local = [
                verdict,
                f"{total_changes} changes",
                f"{files_affected} files",
            ]
            return facts_local

        facts = _run_check_em(
            "compose_facts",
            _compose_facts,
            default=[verdict],
        )
        if facts is None:
            facts = [verdict]

        # W607-EM: ``compose_next_commands`` substrate -- conditional
        # advisory next-step suggestions. A raise degrades to an empty
        # list so the agent_contract still composes.
        def _compose_next_commands():
            cmds: list[str] = []
            if total_changes > 0:
                cmds.append(f"roam impact {sym['name']}")
            if files_affected > 0:
                cmds.append(f"roam preflight {sym['name']}")
            return cmds

        next_commands = _run_check_em(
            "compose_next_commands",
            _compose_next_commands,
            default=[],
        )
        if next_commands is None:
            next_commands = []

        if json_mode:
            # W607-EM: ``serialize_envelope`` substrate -- json_envelope
            # construction + click.echo emission. The wrap protects
            # against crashes inside the formatter call so the marker
            # surfaces and the function returns cleanly.
            envelope_summary: dict = {
                "verdict": verdict,
                "total_changes": total_changes,
                "files_affected": files_affected,
                "mode": mode,
                **resolution_block,
            }
            envelope_kwargs: dict = dict(
                summary=envelope_summary,
                symbol=sym["qualified_name"] or sym["name"],
                kind=sym["kind"],
                location=loc(sym["file_path"], sym["line_start"]),
                mode=mode,
                rename_to=rename,
                total_changes=total_changes,
                files_affected=files_affected,
                changes=[
                    {
                        "change_type": c.get("change_type"),
                        "file": c.get("file"),
                        "line": c.get("line"),
                        "name": c.get("name"),
                        "kind": c.get("kind"),
                        "reason": c.get("reason"),
                    }
                    for c in safe_changes
                ],
                by_type={
                    ct: [
                        {
                            "name": c.get("name"),
                            "file": c.get("file"),
                            "line": c.get("line"),
                            "reason": c.get("reason"),
                        }
                        for c in items
                        if isinstance(c, dict)
                    ]
                    for ct, items in by_type.items()
                },
                agent_contract={
                    "facts": facts,
                    "risks": [],
                    "next_commands": next_commands,
                    "confidence": None,
                },
                **resolution_block,
            )
            # W607-EM: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard.
            if _w607em_warnings_out:
                envelope_summary["partial_success"] = True
                envelope_summary["warnings_out"] = list(_w607em_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607em_warnings_out)

            def _serialize_envelope():
                click.echo(to_json(json_envelope("closure", **envelope_kwargs)))

            _run_check_em("serialize_envelope", _serialize_envelope, default=None)
            return

        # W607-EM: ``format_text_output`` substrate -- the human-readable
        # text emission path. A raise inside the by_type loop (e.g.
        # KeyError on a malformed change dict) degrades to a
        # verdict-only emission so the user still sees the LAW 6
        # floor.
        def _format_text_output():
            click.echo(f"VERDICT: {verdict}")
            click.echo()
            click.echo(
                f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  "
                f"{loc(sym['file_path'], sym['line_start'])}"
            )
            if rename:
                click.echo(f"Mode: rename -> {rename}")
            elif delete_mode:
                click.echo("Mode: delete")
            else:
                click.echo("Mode: modify")
            click.echo()

            for change_type in sorted(by_type.keys()):
                items = by_type[change_type]
                if not isinstance(items, list):
                    continue
                click.echo(f"{change_type} ({len(items)}):")
                rows = []
                for c in items[:20]:
                    if not isinstance(c, dict):
                        continue
                    rows.append(
                        [
                            abbrev_kind(c.get("kind") or ""),
                            c.get("name") or "(file)",
                            loc(c.get("file") or "", c.get("line")),
                            c.get("reason") or "",
                        ]
                    )
                click.echo(format_table(["kind", "name", "location", "reason"], rows))
                if len(items) > 20:
                    click.echo(f"  (+{len(items) - 20} more)")
                click.echo()

            click.echo(f"Total: {total_changes} change(s) in {files_affected} file(s)")

        _run_check_em("format_text_output", _format_text_output, default=None)
        # Marker accumulator handles disclosure on the text path -- the
        # warning rides into ``_w607em_warnings_out`` even when
        # text-mode output is human-targeted (JSON mode carries the
        # structured disclosure surface).
