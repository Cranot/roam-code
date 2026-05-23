"""Find all consumers of a symbol: callers, importers, inheritors.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because uses outputs are invocation-scoped consumer rankings —
not per-location violations. Editor consumers should use the JSON
envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.

W607-U -- Twenty-first-in-batch W607 consumer-layer arc. Direct sibling
of W607-T (cmd_impact blast-radius standalone). cmd_uses is the
direct-callers standalone — depth-1 reverse graph via SQL JOIN on edges
+ language-aware JS-family text fallback. Five substrate-call sites are
wrapped with ``_run_check(phase, fn, *args)`` so a raise becomes a
``uses_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607u_warnings_out`` and the envelope still emits cleanly.

Marker family ``uses_*`` -- distinct from W607-T's ``impact_*``,
W607-S's ``diagnose_*``, W607-R's ``preflight_*``, W607-Q's ``pr_risk_*``,
etc. The marker-prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check: no defensive "duplicated to avoid cycle"
docstrings added. The lazy ``import re`` inside ``_test_text_consumers``
predates this wave; it is a deferred-use import (the helper is rarely
called on non-JS-family targets), not a cycle hedge.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.resolve import ensure_index, symbol_not_found_hint
from roam.db.connection import find_project_root, open_db
from roam.languages import JS_FAMILY_LANGUAGES
from roam.output.formatter import abbrev_kind, format_table, json_envelope, loc, to_json
from roam.output.metric_definitions import CALLER_METRIC_RAW


def _test_text_consumers(conn, name: str, existing_files: set[str]) -> list[dict]:
    """Find test-file mentions when no symbol edge could be created.

    JS/Vitest tests often contain only top-level imports and test callbacks,
    leaving the resolver without a concrete source symbol for an edge. This
    fallback is deliberately scoped to test files and exact identifier matches.
    """
    import re

    project_root = find_project_root()
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    consumers: list[dict] = []
    for f in conn.execute("SELECT path FROM files").fetchall():
        path = f["path"]
        if path in existing_files or not is_test_file(path):
            continue
        try:
            source = (project_root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = pattern.search(source)
        if not match:
            continue
        line = source.count("\n", 0, match.start()) + 1
        consumers.append(
            {
                "name": path.rsplit("/", 1)[-1],
                "qualified_name": path,
                "kind": "test",
                "line_start": line,
                "path": path,
                "edge_kind": "test",
                "edge_line": line,
                "target_name": name,
            }
        )
    return consumers


@roam_capability(
    category="exploration",
    summary="Show all consumers of a symbol: callers, importers, inheritors.",
    inputs=["name"],
    outputs=["consumers"],
    examples=[
        "roam uses handleSave",
        "roam uses AuthService --full",
    ],
    tags=["exploration", "consumers"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.argument("name", metavar="SYMBOL")
@click.option("--full", is_flag=True, help="Show all results without truncation")
@click.pass_context
def uses(ctx, name, full):
    """Show all consumers of SYMBOL: callers, importers, inheritors.

    SYMBOL is a symbol identifier (bare name or qualified name). Unlike
    ``impact`` (which computes transitive blast radius via graph
    traversal), this command lists direct consumers grouped by
    relationship type.

    Also available as ``roam refs <SYMBOL>`` — the grep-familiar alias.

    \b
    Examples:
      roam uses handle_login
      roam refs handle_login
      roam uses UserService.create --full
      roam --json uses authenticate

    See also ``impact`` (transitive blast radius), ``deps`` (file-level
    imports), and ``refs-text`` (string-literal audit with verdicts).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-U -- per-substrate marker accumulator. Each substrate call is
    # wrapped with ``_run_check(phase, fn, *args)`` so a raise becomes a
    # ``uses_<phase>_failed:<exc_class>:<detail>`` marker via this list and
    # the envelope still emits the remaining sections cleanly.
    #
    # Marker family ``uses_*`` -- distinct from W607-T's ``impact_*``,
    # W607-S's ``diagnose_*``, W607-R's ``preflight_*``, etc. The
    # marker-prefix discipline test pins this closed-enum distinction.
    #
    # Empty bucket -> byte-identical envelope (no warnings_out key in
    # either summary or top-level, no W607-U-driven partial_success flip).
    _w607u_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-U marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``uses_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607u_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607u_warnings_out.append(f"uses_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-DE: aggregation-phase marker plumbing (additive) -----------
    # cmd_uses is the direct-callers / consumers lookup -- depth-1 reverse
    # graph via SQL JOIN on edges + language-aware JS-family text fallback.
    # W607-U (above) plumbed the substrate-CALL layer (5 boundaries:
    # resolve_symbol_exact / resolve_symbol_fuzzy / fetch_consumers /
    # fetch_target_langs / test_text_consumers). W607-DE adds the
    # AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket the consumer shape into a state label
    #                           (HAS_USERS / NO_USERS / EMPTY / DEGRADED)
    #   compute_predicate    -- user-count metrics (caller_count +
    #                           callee_count-equivalent + total)
    #   compute_verdict      -- composite verdict-string assembly
    #   serialize_envelope   -- json_envelope("uses", ...) projection
    #
    # Marker family ``uses_*`` -- SAME family as W607-U (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope on the
    # success path. Both buckets are combined at envelope-emit time so
    # consumers see the full degradation lineage in marker-emission order.
    # The additive bucket stays distinguishable via its phase names
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``) which do NOT collide with W607-U substrate
    # phase names.
    #
    # SYMBOL-RELATIONS TRIO pairing analogue -- this is the third leg
    # closure at the aggregation layer:
    #   cmd_uses    (W607-U substrate + W607-DE THIS -- agg added)
    #   cmd_deps    (W607-V substrate + W607-DB landed -- agg added)
    #   cmd_relate  (W607-W substrate + W607-DA landed -- agg added)
    # After DE lands, all 3 members have aggregation-phase layer.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_de(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(rows)``). cmd_taint's W607-CJ added
    # the 5th discipline (move ``len()`` INSIDE the closure, not at the
    # kwarg-bind site). cmd_audit_trail_export's W607-CR added the 7th
    # discipline (use bare ``dict[key]`` lookup when the floor dict
    # guarantees the key, NOT ``dict.get(key, expensive_default)`` which
    # evaluates default eagerly).
    #
    # W607-U/DE PHASE-NAME COLLISION CHECK (W978 4th-discipline): W607-U
    # phase names (resolve_symbol_exact / resolve_symbol_fuzzy /
    # fetch_consumers / fetch_target_langs / test_text_consumers) do NOT
    # collide with score_classify / compute_predicate / compute_verdict /
    # serialize_envelope, so no rename is required.
    _w607de_warnings_out: list[str] = []

    def _run_check_de(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DE marker emission.

        Mirror of ``_run_check`` shape (same
        ``uses_<phase>_failed:`` marker family) but writes into
        ``_w607de_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607de_warnings_out.append(f"uses_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # Find the target symbol(s) by name
        def _resolve_exact():
            return conn.execute(
                "SELECT id, name, kind, qualified_name FROM symbols WHERE name = ?",
                (name,),
            ).fetchall()

        targets = _run_check("resolve_symbol_exact", _resolve_exact, default=[]) or []

        if not targets:
            # Try LIKE search
            def _resolve_fuzzy():
                return conn.execute(
                    "SELECT id, name, kind, qualified_name FROM symbols WHERE name LIKE ? LIMIT 50",
                    (f"%{name}%",),
                ).fetchall()

            targets = _run_check("resolve_symbol_fuzzy", _resolve_fuzzy, default=[]) or []

        if not targets:
            # JSON mode must always emit an envelope — never plaintext.
            # Pre-v12, the plaintext hint was printed unconditionally and
            # downstream parsers (recipe runner, MCP tool wrappers,
            # `roam ask`) crashed on the non-JSON output.
            if json_mode:
                _nf_summary: dict = {
                    "verdict": f"symbol not found: '{name}'",
                    "total_consumers": 0,
                    "total_files": 0,
                    "error": "symbol_not_found",
                }
                _nf_kwargs: dict = {
                    "summary": _nf_summary,
                    "symbol": name,
                    "consumers": {},
                    "hint": symbol_not_found_hint(name),
                }
                # W607-U -- surface substrate-CALL markers on the not-found
                # path. Flip partial_success when any marker landed so
                # consumers can distinguish a clean miss from a degraded
                # resolution attempt.
                if _w607u_warnings_out:
                    _nf_summary["warnings_out"] = list(_w607u_warnings_out)
                    _nf_summary["partial_success"] = True
                    _nf_kwargs["warnings_out"] = list(_w607u_warnings_out)
                    _nf_kwargs["partial_success"] = True
                click.echo(
                    to_json(
                        json_envelope(
                            "uses",
                            **_nf_kwargs,
                        )
                    )
                )
                raise SystemExit(1)
            click.echo(symbol_not_found_hint(name))
            # "Make fallback chains loud": if resolution RAISED (rather than
            # cleanly returning zero rows), the exception was captured in
            # _w607u_warnings_out by _run_check and would otherwise be
            # invisible in text mode -- the user sees only "Symbol not found",
            # masking a degraded-resolution failure as a clean miss (Pattern-2
            # silent fallback). Surface the captured marker(s) on stderr so the
            # underlying cause is diagnosable without losing the existing
            # human-facing hint on stdout.
            if _w607u_warnings_out:
                for _marker in _w607u_warnings_out:
                    click.echo(_marker, err=True)
            raise SystemExit(1)

        target_ids = [t["id"] for t in targets]
        placeholders = ",".join("?" for _ in target_ids)

        # Find ALL edges pointing TO these targets
        def _fetch_consumers():
            return list(
                conn.execute(
                    f"""SELECT s.name, s.qualified_name, s.kind, s.line_start,
                           f.path, e.kind as edge_kind, e.line as edge_line,
                           t.name as target_name
                    FROM edges e
                    JOIN symbols s ON e.source_id = s.id
                    JOIN symbols t ON e.target_id = t.id
                    JOIN files f ON s.file_id = f.id
                    WHERE e.target_id IN ({placeholders})
                    ORDER BY e.kind, f.path, s.line_start""",
                    target_ids,
                ).fetchall()
            )

        rows = _run_check("fetch_consumers", _fetch_consumers, default=[]) or []

        # 12.13 perf — only scan test files for text mentions when the
        # target lives in a language where the symbol resolver leaves
        # gaps (JS / TS / Vue / Svelte). Python / Go / Rust resolvers
        # already produce edges for every test reference, so the
        # fallback was just a 4-second-per-call no-op on those repos
        # (590 file reads against this Python repo to find the same
        # answer the edges table already had). Skipping it on
        # languages that don't need it brings ``roam uses`` from
        # ~700ms warm to ~120ms.
        def _fetch_target_langs():
            return conn.execute(
                f"SELECT DISTINCT f.language FROM symbols s JOIN files f ON s.file_id = f.id "
                f"WHERE s.id IN ({placeholders})",
                target_ids,
            ).fetchall()

        target_files = _run_check("fetch_target_langs", _fetch_target_langs, default=[]) or []
        target_langs = {(r["language"] or "").lower() for r in target_files}
        if target_langs & set(JS_FAMILY_LANGUAGES):
            extras = (
                _run_check(
                    "test_text_consumers",
                    _test_text_consumers,
                    conn,
                    name,
                    {r["path"] for r in rows if is_test_file(r["path"])},
                    default=[],
                )
                or []
            )
            rows.extend(extras)

        if not rows:
            if json_mode:
                _nr_summary: dict = {
                    "verdict": f"no consumers of '{name}' found",
                    "total_consumers": 0,
                    "production_consumers": 0,
                    "test_consumers": 0,
                    "tested": False,
                    "total_files": 0,
                    "caller_metric_definition": CALLER_METRIC_RAW,
                }
                _nr_kwargs: dict = {
                    "summary": _nr_summary,
                    "symbol": name,
                    "consumers": {},
                }
                # W607-U -- surface substrate-CALL markers on the no-rows
                # path. Flip partial_success when any marker landed.
                if _w607u_warnings_out:
                    _nr_summary["warnings_out"] = list(_w607u_warnings_out)
                    _nr_summary["partial_success"] = True
                    _nr_kwargs["warnings_out"] = list(_w607u_warnings_out)
                    _nr_kwargs["partial_success"] = True
                click.echo(
                    to_json(
                        json_envelope(
                            "uses",
                            **_nr_kwargs,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: no consumers of '{name}' found.\n")
                click.echo(f"No consumers of '{name}' found.")
            return

        # Group by edge kind
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r["edge_kind"], []).append(r)

        def _scope(row) -> str:
            return "test" if is_test_file(row["path"]) else "production"

        def _dedupe(items):
            seen = set()
            deduped = []
            for item in items:
                key = (item["qualified_name"], item["path"], item["edge_kind"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(item)
            return deduped

        deduped_rows = _dedupe(rows)
        production_rows = [r for r in deduped_rows if _scope(r) == "production"]
        test_rows = [r for r in deduped_rows if _scope(r) == "test"]

        # Dedup within each group by (name, path)
        kind_labels = {
            "call": "Called by",
            "import": "Imported by",
            "inherits": "Extended by",
            "implements": "Implemented by",
            "uses_trait": "Used by (trait)",
            "template": "Used in template",
            "test": "Mentioned in tests",
        }

        if json_mode:
            json_groups = {}
            for kind, items in by_kind.items():
                deduped = _dedupe(items)
                json_groups[kind] = [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "location": loc(r["path"], r["line_start"]),
                        "scope": _scope(r),
                    }
                    for r in deduped
                ]
            files = set(r["path"] for r in rows)

            # W607-DE -- compute_verdict boundary. Wraps the verdict-string
            # assembly so a downstream f-string format raise (e.g., on a
            # poisoned len() result) surfaces a marker rather than crashing
            # the envelope. Floor literal "uses completed" satisfies LAW 6
            # (one-line standalone verdict).
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: raw lists passed as args;
            # ``len()`` lives INSIDE the closure (cmd_taint W607-CJ
            # 5th-discipline anchor). Floor is a literal constant.
            def _build_verdict_str(_name, _production_rows, _test_rows, _files):
                return (
                    f"'{_name}': {len(_production_rows)} production consumers, "
                    f"{len(_test_rows)} test consumers in {len(_files)} files"
                )

            _verdict = _run_check_de(
                "compute_verdict",
                _build_verdict_str,
                name,
                production_rows,
                test_rows,
                files,
                default="uses completed",
            )

            # W607-DE -- score_classify boundary. Wraps the consumer-shape
            # bucketing into a state label (HAS_USERS / NO_USERS / EMPTY /
            # DEGRADED) so a downstream refactor of state-selection logic
            # surfaces a marker rather than crashing. Floor returns
            # "DEGRADED" so downstream serialize_envelope stays non-null.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: raw lists passed as args;
            # ``len()`` lives INSIDE the closure. Floor dict is literal.
            def _score_classify_users(_production_rows, _test_rows):
                _n_prod = len(_production_rows) if _production_rows is not None else 0
                _n_test = len(_test_rows) if _test_rows is not None else 0
                if _n_prod == 0 and _n_test == 0:
                    _state = "EMPTY"
                elif _n_prod > 0:
                    _state = "HAS_USERS"
                else:
                    _state = "TEST_ONLY"
                return {
                    "state": _state,
                    "production_count": _n_prod,
                    "test_count": _n_test,
                }

            _score_dict = _run_check_de(
                "score_classify",
                _score_classify_users,
                production_rows,
                test_rows,
                default={
                    "state": "DEGRADED",
                    "production_count": 0,
                    "test_count": 0,
                },
            )

            # W607-DE -- compute_predicate boundary. Wraps the consumer-count
            # predicate metric extraction (total_consumers + production +
            # test) so a future schema refactor that drops or renames fields
            # on the ``json_groups`` rows surfaces a marker rather than
            # crashing the envelope. Floor to documented zero-counts so
            # downstream summary fields stay non-null.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: raw dict passed as arg;
            # ``sum(len(...))`` lives INSIDE the closure. Floor dict is
            # literal.
            def _compute_predicate_fields(_json_groups, _production_rows, _test_rows) -> dict:
                _total = sum(len(v) for v in _json_groups.values())
                return {
                    "total_consumers": _total,
                    "production_consumers": len(_production_rows),
                    "test_consumers": len(_test_rows),
                }

            _pred_fields = _run_check_de(
                "compute_predicate",
                _compute_predicate_fields,
                json_groups,
                production_rows,
                test_rows,
                default={
                    "total_consumers": 0,
                    "production_consumers": 0,
                    "test_consumers": 0,
                },
            )

            # W978 KWARG-DEFAULT EAGERNESS NOTE (W607-CR 7th-discipline
            # anchor): do NOT use ``_pred_fields.get("total_consumers",
            # sum(...))`` -- the second arg evaluates EAGERLY. _pred_fields
            # ALWAYS carries the keys (either real value or floor 0), so a
            # bare lookup is correct.
            _success_summary: dict = {
                "verdict": _verdict,
                "total_consumers": _pred_fields["total_consumers"],
                "production_consumers": _pred_fields["production_consumers"],
                "test_consumers": _pred_fields["test_consumers"],
                "tested": bool(test_rows),
                "total_files": len(files),
                "caller_metric_definition": CALLER_METRIC_RAW,
                # W607-DE: surface score_classify state on the envelope so
                # consumers can read the consumer-shape classification
                # without re-deriving from raw counts.
                "consumer_state": _score_dict["state"],
            }
            _success_kwargs: dict = {
                "summary": _success_summary,
                "budget": token_budget,
                "symbol": name,
                "consumers": json_groups,
                "total_files": len(files),
            }
            # W607-U / W607-DE -- surface substrate-CALL markers AND
            # aggregation-phase markers on the success path. Both buckets
            # share the canonical ``uses_*`` marker family (W607-DE is
            # additive, not a separate prefix). The additive bucket stays
            # distinguishable via its phase names.
            _combined_warnings_out = list(_w607u_warnings_out) + list(_w607de_warnings_out)
            if _combined_warnings_out:
                _success_summary["warnings_out"] = list(_combined_warnings_out)
                _success_summary["partial_success"] = True
                _success_kwargs["warnings_out"] = list(_combined_warnings_out)
                _success_kwargs["partial_success"] = True

            # W607-DE -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor that
            # breaks ``json_envelope("uses", ...)`` would otherwise crash
            # AFTER all substrate + aggregation signals were already
            # gathered. Floor to a minimal envelope stub so consumers still
            # receive a parseable JSON object with the marker attached + the
            # canonical command name. Mirror of cmd_relate W607-DA's
            # serialize_envelope floor pattern.
            _envelope_floor: dict = {
                "command": "uses",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": _verdict,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings_out),
                },
                "warnings_out": list(_combined_warnings_out),
            }
            _envelope = _run_check_de(
                "serialize_envelope",
                json_envelope,
                "uses",
                default=_envelope_floor,
                **_success_kwargs,
            )
            # W607-DE -- if ``serialize_envelope`` raised AFTER the combined
            # bucket was already snapshotted, the new
            # ``uses_serialize_envelope_failed:`` marker was appended to
            # ``_w607de_warnings_out`` and the floor stub carries only the
            # pre-raise combined list. Rebuild the floor stub's
            # warnings_out so the new marker reaches the JSON output.
            if _envelope is _envelope_floor and _w607de_warnings_out:
                _combined_warnings_out = list(_w607u_warnings_out) + list(_w607de_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                _envelope = _envelope_floor

            click.echo(to_json(_envelope))
            return

        total = 0
        # Compute totals for verdict
        _files_set = set(r["path"] for r in rows)
        click.echo(
            f"VERDICT: '{name}': {len(production_rows)} production consumers, "
            f"{len(test_rows)} test consumers in {len(_files_set)} files\n"
        )
        click.echo(f"=== Consumers of '{name}' ===\n")

        # Show in a consistent order, then any remaining kinds
        display_order = ["call", "import", "template", "inherits", "implements", "uses_trait"]
        remaining = [k for k in by_kind if k not in display_order]
        for kind in display_order + remaining:
            items = by_kind.get(kind)
            if not items:
                continue

            deduped = _dedupe(items)

            label = kind_labels.get(kind, kind)
            total += len(deduped)

            table_rows = []
            for r in deduped:
                table_rows.append(
                    [
                        abbrev_kind(r["kind"]),
                        r["name"],
                        loc(r["path"], r["line_start"]),
                        _scope(r),
                    ]
                )

            click.echo(f"-- {label} ({len(deduped)}) --")
            click.echo(
                format_table(
                    ["Kind", "Name", "Location", "Scope"],
                    table_rows,
                    budget=0 if full else 20,
                )
            )
            click.echo()

        # File summary: which files depend on this symbol
        files = set()
        for r in rows:
            files.add(r["path"])
        click.echo(f"Total: {total} consumers across {len(files)} files")
