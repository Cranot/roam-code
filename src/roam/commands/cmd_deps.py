"""Show file import/imported-by relationships.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because deps outputs are invocation-scoped import relationships
— not per-location violations. Editor consumers should use the JSON
envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.

W607-V -- Twenty-second-in-batch W607 consumer-layer arc. Direct sibling
of W607-U (cmd_uses direct-callers standalone). cmd_deps is the
file-substrate variant — same find_target + reverse-edge substrate
pattern, no compound recipe, smallest remaining single-target
exploration command on the file axis. Five substrate-call sites are
wrapped with ``_run_check(phase, fn, *args)`` so a raise becomes a
``deps_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607v_warnings_out`` and the envelope still emits cleanly.

W607-DB -- ADDITIVE aggregation-phase plumbing on top of W607-V's
substrate-CALL layer. Three aggregation boundaries are wrapped with
``_run_check_db(phase, fn, *args)`` so a raise becomes a
``deps_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607db_warnings_out``:

* ``compute_predicate``   -- extract dep-count predicate fields
                             (imports_count / imported_by_count /
                             filename) used to compose the verdict.
* ``compute_verdict``     -- verdict string assembly (LAW 6
                             standalone-parse + W978 literal floor).
* ``serialize_envelope``  -- ``json_envelope("deps", ...)`` projection.

Marker family ``deps_*`` -- SAME family as W607-V (additive, not a
separate prefix). The two buckets (``_w607v_warnings_out`` substrate-
CALL + ``_w607db_warnings_out`` aggregation-phase) combine at envelope-
emit time so consumers see the full degradation lineage.

The marker-prefix discipline test pins this closed-enum distinction
against sibling W607 families.

W907 verify-cycle check: no defensive "duplicated to avoid cycle"
docstrings present or added in this module.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, file_not_found_hint
from roam.db.connection import open_db
from roam.db.queries import FILE_BY_PATH, FILE_IMPORTED_BY, FILE_IMPORTS
from roam.output.formatter import format_table, json_envelope, strip_list_payloads, to_json


@roam_capability(
    name="deps",
    category="exploration",
    summary="Show file import/imported-by relationships",
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
@click.argument("path")
@click.option("--full", is_flag=True, help="Show all results without truncation")
@click.pass_context
def deps(ctx, path, full):
    """Show file import/imported-by relationships.

    Unlike ``uses`` (which shows symbol-level callers and consumers), this command shows
    file-level import and imported-by relationships, including which specific symbols are
    used from each imported file.

    \b
    Examples:
      roam deps src/auth.py
      roam deps src/auth.py --full
      roam --json deps src/auth.py

    See also ``uses`` (symbol-level refs), ``fan`` (fan-in/fan-out
    hotspots), and ``impact`` (blast radius for a symbol).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    path = path.replace("\\", "/")

    # W607-V -- per-substrate marker accumulator. Each substrate call is
    # wrapped with ``_run_check(phase, fn, *args)`` so a raise becomes a
    # ``deps_<phase>_failed:<exc_class>:<detail>`` marker via this list and
    # the envelope still emits the remaining sections cleanly.
    #
    # Marker family ``deps_*`` -- distinct from W607-U's ``uses_*``, W607-T's
    # ``impact_*``, etc. The marker-prefix discipline test pins this
    # closed-enum distinction.
    #
    # Empty bucket -> byte-identical envelope (no warnings_out key in
    # either summary or top-level, no W607-V-driven partial_success flip).
    _w607v_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-V marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``deps_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607v_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607v_warnings_out.append(f"deps_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DB -- ADDITIVE aggregation-phase plumbing on top of W607-V
    # substrate-CALL markers. W607-V already wraps 5 substrate-helper
    # boundaries (file_by_path / file_by_path_like / fetch_imports /
    # fetch_sym_edges / fetch_imported_by); W607-DB extends marker
    # coverage to the AGGREGATION-PHASE boundaries that W607-V left
    # unguarded:
    #
    #   - ``compute_predicate``   -- extraction of dep-count predicate
    #                                fields (imports_count /
    #                                imported_by_count / filename)
    #                                used to compose the verdict.
    #   - ``compute_verdict``     -- verdict string assembly. Floor to
    #                                a literal ``"deps analysis completed"``
    #                                string per LAW 6 (standalone-parse)
    #                                + W978 first-hypothesis discipline
    #                                (no re-interpolation of the same
    #                                values that just raised).
    #   - ``serialize_envelope``  -- ``json_envelope("deps", ...)``
    #                                projection (downstream contract
    #                                changes / shape regressions).
    #
    # cmd_deps is NOT a risk scorer (unlike cmd_attest / cmd_pr_bundle)
    # and has no auto_log call -- it is a file-relation traversal
    # command. So the W607-DB phase set drops ``score_classify`` /
    # ``severity_normalize`` / ``auto_log`` and keeps the 3 phases
    # above. Mirror of cmd_fan's W607-CY phase set adapted for the
    # single-mode file-relation aggregator.
    #
    # Marker family ``deps_*`` -- SAME family as W607-V (additive, not
    # a separate prefix). Empty bucket -> byte-identical envelope on
    # the success path.
    _w607db_warnings_out: list[str] = []

    def _run_check_db(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DB marker emission.

        Mirror of ``_run_check`` shape (same ``deps_<phase>_failed:``
        marker family) but writes into ``_w607db_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607db_warnings_out.append(f"deps_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:

        def _file_by_path():
            return conn.execute(FILE_BY_PATH, (path,)).fetchone()

        frow = _run_check("file_by_path", _file_by_path, default=None)
        if frow is None:

            def _file_by_path_like():
                return conn.execute(
                    "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
                    (f"%{path}",),
                ).fetchone()

            frow = _run_check("file_by_path_like", _file_by_path_like, default=None)
        if frow is None:
            if json_mode:
                _nf_summary: dict = {
                    "verdict": f"file not found: '{path}'",
                    "error": "file_not_found",
                }
                _nf_kwargs: dict = {
                    "summary": _nf_summary,
                    "file": path,
                    "hint": file_not_found_hint(path),
                }
                # W607-V -- surface substrate-CALL markers on the not-found
                # path. Flip partial_success when any marker landed so
                # consumers can distinguish a clean miss from a degraded
                # resolution attempt.
                if _w607v_warnings_out:
                    _nf_summary["warnings_out"] = list(_w607v_warnings_out)
                    _nf_summary["partial_success"] = True
                    _nf_kwargs["warnings_out"] = list(_w607v_warnings_out)
                    _nf_kwargs["partial_success"] = True
                click.echo(
                    to_json(
                        json_envelope(
                            "deps",
                            **_nf_kwargs,
                        )
                    )
                )
                raise SystemExit(1)
            click.echo(file_not_found_hint(path))
            raise SystemExit(1)

        # --- Imports ---
        def _fetch_imports():
            return conn.execute(FILE_IMPORTS, (frow["id"],)).fetchall()

        imports = _run_check("fetch_imports", _fetch_imports, default=[]) or []
        used_from: dict = {}
        if imports:
            import_file_ids = set(i["id"] for i in imports)

            def _fetch_sym_edges():
                return conn.execute(
                    "SELECT s_tgt.file_id as tgt_fid, s_tgt.name as tgt_name "
                    "FROM edges e "
                    "JOIN symbols s_src ON e.source_id = s_src.id "
                    "JOIN symbols s_tgt ON e.target_id = s_tgt.id "
                    "WHERE s_src.file_id = ? AND s_tgt.file_id != ?",
                    (frow["id"], frow["id"]),
                ).fetchall()

            sym_edges = _run_check("fetch_sym_edges", _fetch_sym_edges, default=[]) or []
            for se in sym_edges:
                if se["tgt_fid"] in import_file_ids:
                    used_from.setdefault(se["tgt_fid"], set()).add(se["tgt_name"])

        # --- Imported by ---
        def _fetch_imported_by():
            return conn.execute(FILE_IMPORTED_BY, (frow["id"],)).fetchall()

        imported_by = _run_check("fetch_imported_by", _fetch_imported_by, default=[]) or []

        if json_mode:
            # W607-DB -- compute_predicate boundary. Wraps the per-result
            # predicate-field extraction so a future schema refactor that
            # drops/renames ``path`` on the frow object would surface a
            # marker rather than crashing the envelope. Floor to a
            # documented empty-shape dict so downstream verdict / summary
            # fields stay non-null.
            _imports_count = len(imports)
            _imported_by_count = len(imported_by)

            def _compute_deps_predicate(frow_local, imp_n: int, ib_n: int):
                _path = frow_local["path"]
                return {
                    "fname": _path.split("/")[-1],
                    "path": _path,
                    "imports_count": imp_n,
                    "imported_by_count": ib_n,
                }

            _deps_predicate = _run_check_db(
                "compute_predicate",
                _compute_deps_predicate,
                frow,
                _imports_count,
                _imported_by_count,
                default={
                    "fname": "",
                    "path": "",
                    "imports_count": 0,
                    "imported_by_count": 0,
                },
            )

            # W607-DB -- compute_verdict boundary. Wraps the verdict-
            # string f-string assembly so a __format__-raising sentinel
            # under test (e.g. via the predicate extraction floor)
            # surfaces a marker rather than crashing the envelope.
            # Floor must NOT re-interpolate the same values that tripped
            # the closure (W978 first-hypothesis discipline). Use a
            # literal ``"deps analysis completed"`` floor instead (LAW 6
            # still holds: the line works standalone).
            def _build_deps_verdict(pred):
                return f"{pred['fname']}: {pred['imports_count']} imports, {pred['imported_by_count']} importers"

            _verdict = _run_check_db(
                "compute_verdict",
                _build_deps_verdict,
                _deps_predicate,
                default="deps analysis completed",
            )

            _success_summary: dict = {
                "verdict": _verdict,
                "imports": _imports_count,
                "imported_by": _imported_by_count,
                "caller_metric_definition": "raw_edge_rows (file-level: file_edges)",
            }
            _success_kwargs: dict = {
                "summary": _success_summary,
                "budget": token_budget,
                "path": _deps_predicate.get("path", ""),
                "imports": [
                    {
                        "path": i["path"],
                        "symbol_count": i["symbol_count"],
                        "used_symbols": sorted(used_from.get(i["id"], set())),
                    }
                    for i in imports
                ],
                "imported_by": [{"path": i["path"], "symbol_count": i["symbol_count"]} for i in imported_by],
            }
            # W607-V / W607-DB -- surface BOTH substrate-CALL markers
            # AND aggregation-phase markers on the success path.
            # partial_success flips so consumers can distinguish a clean
            # enumeration from one that ran with substrate degradation
            # (e.g., sym_edges JOIN raised) OR aggregation degradation
            # (compute_predicate / compute_verdict / serialize_envelope).
            # Mirror both top-level and summary slots so default-detail-
            # mode envelope stripping preserves the marker channel.
            _combined_warnings_out: list[str] = list(_w607v_warnings_out) + list(_w607db_warnings_out)
            if _combined_warnings_out:
                _success_summary["warnings_out"] = list(_combined_warnings_out)
                _success_summary["partial_success"] = True
                _success_kwargs["warnings_out"] = list(_combined_warnings_out)
                _success_kwargs["partial_success"] = True

            # W607-DB -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("deps", ...)`` would otherwise
            # crash AFTER all substrate + aggregation signals were
            # already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. Mirror of
            # cmd_fan's W607-CY serialize_envelope floor pattern.
            _envelope_floor: dict = {
                "command": "deps",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": _verdict,
                    "imports": _imports_count,
                    "imported_by": _imported_by_count,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings_out),
                },
                "warnings_out": list(_combined_warnings_out),
            }
            envelope = _run_check_db(
                "serialize_envelope",
                json_envelope,
                "deps",
                default=_envelope_floor,
                **_success_kwargs,
            )
            # W607-DB -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``deps_serialize_envelope_failed:`` marker was appended
            # to ``_w607db_warnings_out`` and the floor stub carries
            # only the pre-raise combined list. Rebuild so the new
            # marker reaches the JSON output. Clean path -> envelope
            # is the real json_envelope return value, no rebuild
            # needed.
            if envelope is _envelope_floor and _w607db_warnings_out:
                _combined_warnings_out = list(_w607v_warnings_out) + list(_w607db_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                envelope = _envelope_floor

            if not detail:
                envelope = strip_list_payloads(envelope)
            click.echo(to_json(envelope))
            return

        # --- Text output ---
        _fname = frow["path"].split("/")[-1]
        _verdict = f"{_fname}: {len(imports)} imports, {len(imported_by)} importers"
        click.echo(f"VERDICT: {_verdict}\n")
        click.echo(f"{frow['path']}")
        click.echo(f"Imports: {len(imports)}  |  Imported by: {len(imported_by)}")
        click.echo()

        if not detail:
            # Summary mode: show counts and top 5
            if imports:
                click.echo("Imports (top 5, run `roam --detail deps` or pass `--full` for the complete list):")
                for i in imports[:5]:
                    names = used_from.get(i["id"], set())
                    sym_str = ", ".join(sorted(names)[:3])
                    if len(names) > 3:
                        sym_str += f" (+{len(names) - 3})"
                    click.echo(f"  {i['path']}  ({sym_str})")
                if len(imports) > 5:
                    click.echo(f"  (+{len(imports) - 5} more)")
            else:
                click.echo("Imports: (none)")
            return

        if imports:
            rows = []
            for i in imports:
                names = used_from.get(i["id"], set())
                sym_str = ", ".join(sorted(names)[:5])
                if len(names) > 5:
                    sym_str += f" (+{len(names) - 5})"
                rows.append([i["path"], str(i["symbol_count"]), sym_str])
            click.echo("Imports:")
            click.echo(format_table(["file", "symbols", "used"], rows))
        else:
            click.echo("Imports: (none)")
        click.echo()

        if imported_by:
            rows = [[i["path"], str(i["symbol_count"])] for i in imported_by]
            click.echo("Imported by:")
            click.echo(format_table(["file", "symbols"], rows))
        else:
            click.echo("Imported by: (none)")
