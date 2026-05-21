"""Discover implicit contracts (invariants) for symbols.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because invariants outputs are invocation-scoped implicit-
contract enumerations (return-type stability, null-checks, ordering
constraints inferred from call-graph + usage patterns) — not per-
location code violations. The ``laws`` command ships SARIF when an
invariant is promoted to a checked law and a violation is observed;
the ``invariants`` discovery step itself returns candidate contracts
without source coordinates suitable for SARIF ``locations[]``. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import (
    BREAKING_RISK_DEFINITION,
    CALLER_METRIC_RAW,
    INVARIANTS_DEFINITION,
)


def _discover_invariants(conn, sym_id, sym_info):
    """Discover invariants for a single symbol."""
    name = sym_info["name"]
    kind = sym_info["kind"]
    signature = sym_info.get("signature") or ""
    file_path = sym_info["file_path"]
    line_start = sym_info.get("line_start", 0)

    # 1. Caller analysis
    callers = conn.execute(
        """SELECT s.name, s.kind, f.path as file_path, e.kind as edge_kind
           FROM edges e
           JOIN symbols s ON e.source_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE e.target_id = ?""",
        (sym_id,),
    ).fetchall()

    caller_count = len(callers)
    caller_files = set(c["file_path"] for c in callers)
    file_spread = len(caller_files)

    # 2. Callee analysis (what this symbol depends on)
    callees = conn.execute(
        """SELECT s.name, s.kind, f.path as file_path
           FROM edges e
           JOIN symbols s ON e.target_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE e.source_id = ?""",
        (sym_id,),
    ).fetchall()

    # 3. Complexity metrics
    metrics_row = conn.execute(
        """SELECT cognitive_complexity, param_count, line_count, return_count
           FROM symbol_metrics WHERE symbol_id = ?""",
        (sym_id,),
    ).fetchone()

    param_count = metrics_row["param_count"] if metrics_row else 0

    # 4. Build invariants list
    invariants = []

    # Signature contract
    # W761/W847: ``stability`` is INTERNAL VOCABULARY for the invariants
    # display contract (per-invariant payload field, distinct from the
    # canonical W547 ``severity`` envelope slot). UPPER-case retained.
    if signature:
        stability = "HIGH" if caller_count >= 10 else "MEDIUM" if caller_count >= 3 else "LOW"
        invariants.append(
            {
                "type": "SIGNATURE",
                "description": f"Signature: {signature}",
                "stability": stability,  # W761/W847 retained UPPER-case for internal vocabulary
                "detail": f"{caller_count} callers depend on this signature",
            }
        )

    # Parameter count contract
    if param_count > 0:
        invariants.append(
            {
                "type": "PARAMS",
                "description": f"Accepts {param_count} parameter(s)",
                "stability": "HIGH"
                if caller_count >= 5
                else "MEDIUM",  # W761/W847 retained UPPER-case for internal vocabulary
                "detail": f"Changing parameter count would affect {caller_count} call sites",
            }
        )

    # File spread contract
    if file_spread >= 3:
        invariants.append(
            {
                "type": "USAGE_SPREAD",
                "description": f"Used across {file_spread} files",
                "stability": "HIGH",  # W761/W847 retained UPPER-case for internal vocabulary
                "detail": "Wide usage makes this a de-facto public API",
            }
        )

    # Dependency contract (what it calls)
    if len(callees) > 0:
        dep_names = [c["name"] for c in callees[:5]]
        invariants.append(
            {
                "type": "DEPENDENCIES",
                "description": f"Depends on {len(callees)} symbol(s): {', '.join(dep_names)}",
                "stability": "MEDIUM",  # W761/W847 retained UPPER-case for internal vocabulary
                "detail": "Removing a dependency may change behavior",
            }
        )

    # Breaking risk score
    # W761/W847: ``risk_level`` is the canonical rollup field per W847
    # scope clarification (the same field name appears in cmd_preflight
    # and is intentionally distinct from the W547 ``severity`` envelope
    # slot). UPPER-case retained as INTERNAL VOCABULARY for the
    # agent-facing risk-tier display contract.
    breaking_risk = caller_count * max(file_spread, 1)
    risk_level = (
        "CRITICAL"  # W761/W847 retained UPPER-case for internal vocabulary
        if breaking_risk >= 50
        else "HIGH"
        if breaking_risk >= 20
        else "MEDIUM"
        if breaking_risk >= 5
        else "LOW"
    )

    return {
        "name": name,
        "kind": kind,
        "signature": signature,
        "file": file_path,
        "line": line_start,
        "caller_count": caller_count,
        "file_spread": file_spread,
        "callee_count": len(callees),
        "param_count": param_count,
        "invariants": invariants,
        "breaking_risk": breaking_risk,
        "risk_level": risk_level,
    }


@roam_capability(
    name="invariants",
    category="refactoring",
    summary="Discover implicit contracts for symbols",
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
@click.command("invariants")
@click.argument("target", required=False, default=None)
@click.option("--public-api", is_flag=True, help="Analyze all public/exported symbols")
@click.option("--breaking-risk", is_flag=True, help="Rank by breaking risk")
@click.option("--top", "top_n", default=20, type=int, help="Max symbols to show")
@click.pass_context
def invariants(ctx, target, public_api, breaking_risk, top_n):
    """Discover implicit contracts for symbols.

    Unlike ``check-rules`` (which evaluates explicit governance rules),
    this command auto-discovers implicit contracts from usage patterns and
    call frequency.

    Analyzes caller patterns, signatures, and usage spread to surface
    the invisible rules that must be preserved when modifying code.

    \b
    Invariant types:
      SIGNATURE     Function signature (params, return type)
      PARAMS        Parameter count and ordering
      USAGE_SPREAD  How widely the symbol is used across files
      DEPENDENCIES  What the symbol depends on
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # W607-CU -- substrate-boundary plumbing for cmd_invariants.
    # ``_run_check_cu`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cu_warnings_out`` rather than
    # crashing the architectural-invariant detector (W119 origin per
    # CLAUDE.md detector roster -- part of the original 16 findings-
    # registry substrate detectors, paired with cmd_laws). W824 sealed
    # the empty-corpus smoke; this wave layers substrate isolation on
    # top so a raise in ``_discover_invariants`` (per-symbol caller-
    # graph + complexity rollup), the file/symbol-target resolvers,
    # the public-api / breaking-risk batch queries, the sort step, or
    # the downstream verdict composer is disclosed rather than fatal.
    # Marker family ``invariants_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped:
    #
    #   * lookup_file_target               -- file path lookup (exact + LIKE)
    #   * query_file_symbols               -- per-file symbol bulk query
    #   * discover_invariants_for_file_sym -- per-symbol invariant discovery
    #                                         (file-target branch)
    #   * resolve_symbol_target            -- find_symbol() symbol mode
    #   * discover_invariants_for_symbol   -- per-symbol invariant discovery
    #                                         (symbol-target / fallback branch)
    #   * query_public_api_symbols         -- --public-api batch query
    #   * discover_invariants_public_api   -- per-symbol invariant discovery
    #                                         (--public-api batch)
    #   * query_breaking_risk_symbols      -- --breaking-risk batch query
    #   * discover_invariants_breaking_risk -- per-symbol invariant discovery
    #                                          (--breaking-risk batch)
    #   * sort_by_breaking_risk            -- ranking sort
    #   * aggregate_summary                -- total_invariants + high_risk
    #                                         histogram (W978 discipline 6:
    #                                         literal-int counts hoisted out
    #                                         of downstream summary code)
    #   * build_resolution_disclosure      -- W1245 resolution block
    #   * compose_verdict                  -- LAW 6 single-line verdict
    #   * build_envelope_symbols           -- per-symbol envelope rows
    _w607cu_warnings_out: list[str] = []

    def _run_check_cu(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CU marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an ``invariants_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607cu_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cu_warnings_out.append(f"invariants_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        results = []
        # W1245 Pattern-2 variant-D: track the resolver tier of any
        # find_symbol() callsite reached on the symbol-target branch.
        # The two callsites below (file-fallback symbol lookup AND
        # bare-symbol mode) are mutually exclusive at runtime, so a
        # single tier + resolved_name pair is sufficient. ``symbol``
        # is the default when target is a file (file path resolution
        # doesn't walk the symbol fallback chain) or absent. Only
        # ``--public-api`` / ``--breaking-risk`` batch modes skip
        # disclosure (no per-name input to grade).
        resolution_tier = "symbol"
        resolved_target: str | None = None
        target_unresolved = False

        if target:
            # Detect whether target looks like a file path (has a known extension)
            target_norm = target.replace("\\", "/")
            _known_exts = {
                ".py",
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".go",
                ".java",
                ".rb",
                ".rs",
                ".c",
                ".cpp",
                ".h",
                ".hpp",
                ".php",
                ".cs",
                ".swift",
                ".kt",
                ".scala",
                ".m",
                ".r",
                ".lua",
                ".sh",
                ".sql",
            }
            target_suffix = Path(target_norm).suffix.lower()
            looks_like_file = target_suffix in _known_exts

            if looks_like_file:
                # File mode: try exact match, then suffix/LIKE match.
                # W607-CU: ``lookup_file_target`` substrate -- the SQL
                # ``files`` table lookup. A raise here degrades to
                # ``None`` so the symbol-fallback path still runs.
                def _lookup_file_target():
                    row = conn.execute("SELECT id FROM files WHERE path = ?", (target_norm,)).fetchone()
                    if not row:
                        row = conn.execute("SELECT id FROM files WHERE path LIKE ?", (f"%{target_norm}",)).fetchone()
                    return row

                row = _run_check_cu("lookup_file_target", _lookup_file_target, default=None)

                if row:
                    # W607-CU: ``query_file_symbols`` substrate -- bulk
                    # query of symbols within the resolved file. A raise
                    # degrades to [] so the empty-results path composes
                    # the standard usage-error envelope.
                    def _query_file_symbols():
                        return conn.execute(
                            """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                                      f.path as file_path
                               FROM symbols s JOIN files f ON s.file_id = f.id
                               WHERE s.file_id = ?
                               AND s.kind IN ('function', 'method', 'class')
                               ORDER BY s.line_start""",
                            (row["id"],),
                        ).fetchall()

                    syms = _run_check_cu("query_file_symbols", _query_file_symbols, default=[])
                    if syms is None:
                        syms = []
                    # W607-CU: ``discover_invariants_for_file_sym``
                    # substrate -- per-symbol invariant discovery on
                    # the file-target branch. Per-invariant ISOLATION:
                    # a raise on one symbol degrades to None for that
                    # row and the loop continues (matches the W607-CQ
                    # template's per-substrate isolation discipline).
                    for sym in syms:
                        inv_row = _run_check_cu(
                            "discover_invariants_for_file_sym",
                            _discover_invariants,
                            conn,
                            sym["id"],
                            dict(sym),
                            default=None,
                        )
                        if inv_row is not None:
                            results.append(inv_row)
                else:
                    # Not found as file, fall back to symbol lookup.
                    # W607-CU: ``resolve_symbol_target`` substrate --
                    # the find_symbol() resolver call. A raise degrades
                    # to None -> target_unresolved disclosure.
                    sym = _run_check_cu(
                        "resolve_symbol_target",
                        find_symbol,
                        conn,
                        target,
                        default=None,
                    )
                    if sym:
                        inv_row = _run_check_cu(
                            "discover_invariants_for_symbol",
                            _discover_invariants,
                            conn,
                            sym["id"],
                            dict(sym),
                            default=None,
                        )
                        if inv_row is not None:
                            results.append(inv_row)
                        resolution_tier = sym.get("_resolution_tier", "symbol")
                        resolved_target = sym.get("qualified_name") or sym["name"]
                    else:
                        target_unresolved = True
                        resolved_target = target
            else:
                # Symbol mode
                sym = _run_check_cu(
                    "resolve_symbol_target",
                    find_symbol,
                    conn,
                    target,
                    default=None,
                )
                if sym:
                    inv_row = _run_check_cu(
                        "discover_invariants_for_symbol",
                        _discover_invariants,
                        conn,
                        sym["id"],
                        dict(sym),
                        default=None,
                    )
                    if inv_row is not None:
                        results.append(inv_row)
                    resolution_tier = sym.get("_resolution_tier", "symbol")
                    resolved_target = sym.get("qualified_name") or sym["name"]
                else:
                    target_unresolved = True
                    resolved_target = target

        elif public_api:
            # All exported symbols (functions/classes with 0 or more callers).
            # W607-CU: ``query_public_api_symbols`` substrate -- the
            # batch query. A raise degrades to [] so the empty-results
            # path emits the usage-error envelope.
            def _query_public_api_symbols():
                return conn.execute(
                    """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                              f.path as file_path
                       FROM symbols s JOIN files f ON s.file_id = f.id
                       WHERE s.kind IN ('function', 'method', 'class')
                       AND s.name NOT LIKE '\\_%' ESCAPE '\\'
                       ORDER BY s.name"""
                ).fetchall()

            syms = _run_check_cu("query_public_api_symbols", _query_public_api_symbols, default=[])
            if syms is None:
                syms = []
            for sym in syms[: top_n * 2]:  # over-fetch, then sort/trim
                inv_row = _run_check_cu(
                    "discover_invariants_public_api",
                    _discover_invariants,
                    conn,
                    sym["id"],
                    dict(sym),
                    default=None,
                )
                if inv_row is not None:
                    results.append(inv_row)

        elif breaking_risk:
            # All symbols ranked by breaking risk.
            # W607-CU: ``query_breaking_risk_symbols`` substrate.
            def _query_breaking_risk_symbols():
                return conn.execute(
                    """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                              f.path as file_path
                       FROM symbols s JOIN files f ON s.file_id = f.id
                       WHERE s.kind IN ('function', 'method', 'class')
                       ORDER BY s.name"""
                ).fetchall()

            syms = _run_check_cu("query_breaking_risk_symbols", _query_breaking_risk_symbols, default=[])
            if syms is None:
                syms = []
            for sym in syms:
                inv_row = _run_check_cu(
                    "discover_invariants_breaking_risk",
                    _discover_invariants,
                    conn,
                    sym["id"],
                    dict(sym),
                    default=None,
                )
                if inv_row is not None:
                    results.append(inv_row)

        if not results and not target and not public_api and not breaking_risk:
            # Pattern 1 Variant C: in JSON mode, emit a structured
            # envelope so MCP/wrapper consumers don't try to json.loads()
            # plain text. The state name + verdict are machine-parseable
            # and the next_commands suggest exactly how to recover.
            verdict = "Provide a TARGET symbol/file, or use --public-api or --breaking-risk."
            if json_mode:
                usage_summary: dict = {
                    "verdict": verdict,
                    "symbols_analyzed": 0,
                    "total_invariants": 0,
                    "high_risk_count": 0,
                    "partial_success": True,
                    "state": "usage_error",
                }
                usage_envelope_kwargs: dict = dict(
                    summary=usage_summary,
                    status="usage_error",
                    isError=True,
                    error_code="USAGE_ERROR",
                    error=verdict,
                    symbols=[],
                    agent_contract={
                        "facts": [verdict],
                        "next_commands": [
                            "roam invariants <symbol-or-file>",
                            "roam invariants --public-api",
                            "roam invariants --breaking-risk",
                        ],
                    },
                )
                # W607-CU: mirror substrate markers into BOTH the
                # top-level envelope ``warnings_out`` AND
                # ``summary.warnings_out`` so MCP consumers see
                # disclosure regardless of which surface they read.
                if _w607cu_warnings_out:
                    usage_summary["warnings_out"] = list(_w607cu_warnings_out)
                    usage_envelope_kwargs["warnings_out"] = list(_w607cu_warnings_out)
                click.echo(to_json(json_envelope("invariants", **usage_envelope_kwargs)))
            else:
                click.echo(verdict)
            raise SystemExit(1)

        # Sort by breaking risk if requested.
        # W607-CU: ``sort_by_breaking_risk`` substrate -- a malformed
        # result row missing ``breaking_risk`` would KeyError on the
        # sort key lookup. The wrap degrades to the unsorted ``results``
        # list so the envelope still composes.
        if breaking_risk:

            def _sort_by_breaking_risk():
                results.sort(key=lambda r: -r["breaking_risk"])
                return True

            _run_check_cu("sort_by_breaking_risk", _sort_by_breaking_risk, default=False)

        results = results[:top_n]

        # W607-CU: ``aggregate_summary`` substrate -- the
        # total_invariants + high_risk histogram. A KeyError on a
        # malformed result row (missing ``invariants`` / ``risk_level``)
        # degrades to (0, 0) so the verdict composer still produces a
        # coherent string. W978 discipline 6 (hoist literal-int counts
        # into a predicate phase): the literal-int floor lives in the
        # default tuple here so downstream summary code never operates
        # on an unguarded ``len()`` / ``if results:`` shape.
        def _aggregate_summary():
            total_inv = sum(len(r["invariants"]) for r in results)
            # W761/W847 retained UPPER-case for internal vocabulary —
            # ``risk_level`` is the canonical rollup field; comparison
            # set mirrors the source values produced upstream.
            high_r = sum(1 for r in results if r["risk_level"] in ("CRITICAL", "HIGH"))
            return (total_inv, high_r)

        agg = _run_check_cu("aggregate_summary", _aggregate_summary, default=(0, 0))
        if agg is None:
            agg = (0, 0)
        total_invariants, high_risk = agg

        # W978 discipline 6: literal-int len for the ranked envelope so
        # the verdict composer + envelope-build phases never re-evaluate
        # ``len(results)`` on a potentially-mutated list. Hoisted here
        # alongside the histogram so the predicate phase owns BOTH
        # counts.
        results_len = len(results)

        # W1245 Pattern-2 variant-D: build the disclosure block. When a
        # symbol-target callsite resolved unresolved, surface that
        # explicitly via ``resolution="unresolved"`` so the success
        # verdict on an empty results list isn't indistinguishable from
        # an exact-but-empty resolution. The batch modes (--public-api,
        # --breaking-risk) leave the disclosure as the no-op ``symbol``
        # default because there's no per-input grading to apply.
        # W607-CU: ``build_resolution_disclosure`` substrate -- a raise
        # in the disclosure helper degrades to {} so the envelope still
        # composes without the disclosure block.
        if target_unresolved:
            disclosure_tier = "unresolved"
        else:
            disclosure_tier = resolution_tier
        # Only emit a disclosure when the symbol-target branch actually
        # walked the resolver (or attempted it). The batch modes resolve
        # everything by direct DB query and don't have a tier to report.
        emit_disclosure = bool(target)
        if emit_disclosure:
            resolution_block = _run_check_cu(
                "build_resolution_disclosure",
                resolution_disclosure,
                disclosure_tier,
                target=resolved_target,
                default={},
            )
            if resolution_block is None:
                resolution_block = {}
        else:
            resolution_block = {}
        fuzzy_suffix = " [fuzzy resolution]" if disclosure_tier == "fuzzy" else ""

        # W607-CU: ``compose_verdict`` substrate -- LAW 6 single-line
        # verdict string. The single-result branch indexes into
        # ``r['name']`` / ``r['caller_count']`` / ``r['risk_level']`` /
        # ``r['invariants']`` -- KeyError-prone on a malformed result
        # row. The wrap degrades to the explicit "no data" floor so the
        # envelope still emits a non-empty verdict.
        def _compose_verdict():
            if not results:
                return f"No symbols found for: {target or 'query'}"
            if results_len == 1:
                r = results[0]
                return (
                    f"{len(r['invariants'])} invariants for {r['name']}"
                    f" ({r['caller_count']} callers, risk: {r['risk_level']})"
                    f"{fuzzy_suffix}"
                )
            return f"{total_invariants} invariants across {results_len} symbols, {high_risk} high-risk{fuzzy_suffix}"

        verdict = _run_check_cu("compose_verdict", _compose_verdict, default="no data")
        if verdict is None:
            verdict = "no data"

        if json_mode:
            ranked_summary: dict = {
                "verdict": verdict,
                "symbols_analyzed": results_len,
                "total_invariants": total_invariants,
                "high_risk_count": high_risk,
                # W331: name what an "invariant" actually is
                # here (NOT a verified property; a usage-
                # derived heuristic contract) and how the
                # breaking_risk score is computed.
                "invariants_definition": INVARIANTS_DEFINITION,
                "breaking_risk_definition": BREAKING_RISK_DEFINITION,
                # W335: per-symbol payload carries
                # caller_count = len(callers) — same
                # raw_edge_rows shape as cmd_uses /
                # cmd_context. Label it so downstream
                # consumers know it counts per-file edge
                # rows (with multiplicity), not distinct
                # upstream symbols (which would be
                # direct_in_degree).
                "caller_metric_definition": CALLER_METRIC_RAW,
                **resolution_block,
            }

            # W607-CU: ``build_envelope_symbols`` substrate -- the
            # per-symbol envelope rows. A raise here degrades to []
            # so the envelope still composes the verdict + summary.
            def _build_envelope_symbols():
                return list(results)

            envelope_symbols = _run_check_cu(
                "build_envelope_symbols",
                _build_envelope_symbols,
                default=[],
            )
            if envelope_symbols is None:
                envelope_symbols = []
            ranked_envelope_kwargs: dict = dict(
                summary=ranked_summary,
                symbols=envelope_symbols,
                **resolution_block,
            )
            # W607-CU: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard -- a degraded substrate
            # path must NOT be mistaken for a clean ranked verdict.
            if _w607cu_warnings_out:
                ranked_summary["partial_success"] = True
                ranked_summary["warnings_out"] = list(_w607cu_warnings_out)
                ranked_envelope_kwargs["warnings_out"] = list(_w607cu_warnings_out)
            click.echo(to_json(json_envelope("invariants", **ranked_envelope_kwargs)))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        for r in results:
            click.echo(f"CONTRACT: {r['name']} ({abbrev_kind(r['kind'])}, {loc(r['file'], r['line'])})")
            if r["signature"]:
                click.echo(f"  Signature: {r['signature']}")
            click.echo(f"  Callers: {r['caller_count']} across {r['file_spread']} files")
            click.echo(f"  Breaking risk: {r['risk_level']} (score: {r['breaking_risk']})")
            click.echo()

            if r["invariants"]:
                click.echo("  INVARIANTS:")
                for i, inv in enumerate(r["invariants"], 1):
                    click.echo(f"    {i}. [{inv['type']}] {inv['description']}")
                    click.echo(f"       Stability: {inv['stability']} -- {inv['detail']}")
                click.echo()
