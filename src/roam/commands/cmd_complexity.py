"""Show per-symbol cognitive complexity metrics.

Surfaces the symbol_metrics table populated during indexing. Ranks
functions/methods by cognitive complexity to identify the hardest-to-
understand code in the project.
"""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output._severity import severity_to_confidence_level
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION

# W93-follow-up: complexity is the third detector migrating onto the
# central findings registry (after `clones` in W95 and `dead` in W99).
# The shape mirrors those two — a stable detector version stamp and a
# deterministic ``finding_id_str`` so re-runs upsert instead of
# duplicating rows. Bump this when the threshold-to-severity mapping
# in ``_severity`` changes meaningfully, since that's what the registry
# row's ``claim`` and confidence tier are derived from.
COMPLEXITY_DETECTOR_VERSION: str = "1.0.0"

# Registry-emit threshold: only symbols with cognitive_complexity >= 15
# are emitted as findings. This mirrors the existing ``_severity``
# cutoff: 15 is the floor for ``high`` severity (refactor-target tier
# in the canonical W547 lowercase vocabulary; W761 migrated this
# helper off its pre-W761 UPPER-cased spelling).
# Below that, the symbols are noise for an agent consuming the
# cross-detector registry — they appear in the per-command rankings
# but shouldn't pollute ``roam findings list``.
COMPLEXITY_FINDING_THRESHOLD: float = 15.0


def _complexity_finding_id(symbol_id: int, cognitive_score: float) -> str:
    """Stable, deterministic finding id for one complexity hotspot.

    The (symbol_id, rounded-score) pair re-identifies the same hotspot
    across runs. We round the cognitive score to the nearest int so a
    tree-sitter parse jitter that nudges the score by 0.0001 doesn't
    mint a fresh id — but a meaningful refactor that drops the score
    from 25 to 14 does (and removes the row, since it's below the emit
    threshold).
    """
    raw = f"{symbol_id}:{int(round(float(cognitive_score)))}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"complexity:hotspot:{digest}"


def _emit_complexity_findings(conn, rows) -> None:
    """Mirror each high-complexity symbol row into the findings registry.

    ``rows`` is the filtered ranking from the ``symbol_metrics`` JOIN
    (same shape used by the JSON envelope). Rows below
    ``COMPLEXITY_FINDING_THRESHOLD`` are skipped — the registry
    documents hotspots, not the long tail.

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard read path.
    """
    from roam.db.findings import (
        CONFIDENCE_STRUCTURAL,
        FindingRecord,
        emit_finding,
    )

    for r in rows:
        symbol_id = r["symbol_id"]
        if symbol_id is None:
            continue
        score = float(r["cognitive_complexity"] or 0)
        if score < COMPLEXITY_FINDING_THRESHOLD:
            continue
        severity = _severity(score)
        name = r["qualified_name"] or r["name"] or ""
        file_path = r["file_path"] or ""
        line_start = r["line_start"]
        finding_id = _complexity_finding_id(int(symbol_id), score)
        evidence = {
            "name": name,
            "kind": r["kind"],
            "file_path": file_path,
            "line_start": line_start,
            "line_end": r["line_end"],
            "cognitive_complexity": score,
            "severity": severity,
            "nesting_depth": r["nesting_depth"],
            "param_count": r["param_count"],
            "line_count": r["line_count"],
            "return_count": r["return_count"],
            "bool_op_count": r["bool_op_count"],
            "callback_depth": r["callback_depth"],
            "cyclomatic_density": _safe_metric(r, "cyclomatic_density"),
            "halstead_volume": _safe_metric(r, "halstead_volume"),
            "halstead_difficulty": _safe_metric(r, "halstead_difficulty"),
            "halstead_effort": _safe_metric(r, "halstead_effort"),
            "halstead_bugs": _safe_metric(r, "halstead_bugs"),
        }
        claim = (
            f"High cognitive complexity: {name} ({file_path}:{line_start}) — "
            f"score {score:.0f} ({severity}, threshold {COMPLEXITY_FINDING_THRESHOLD:.0f})"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol",
                subject_id=int(symbol_id),
                claim=claim,
                # Cognitive complexity is a deterministic AST measurement,
                # not a heuristic — same input file always produces the
                # same score. ``structural`` is the right tier.
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=CONFIDENCE_STRUCTURAL,
                source_detector="complexity",
                source_version=COMPLEXITY_DETECTOR_VERSION,
            ),
        )


# R22 — confidence-derivation rule for complexity rankings:
#   cognitive_complexity is a deterministic, well-validated metric, so
#   "confidence" here reflects whether the score crosses a threshold
#   that empirically predicts maintainability pain — not whether the
#   number is trustworthy (it always is).
#
#   severity critical or high (score >= 15) → "high"  (refactor target)
#   severity medium  (8 <= score < 15)       → "medium" (monitor)
#   severity low     (score < 8)             → "low"    (no action)
#
# W761: ``_severity()`` now emits the canonical lowercase W547
# vocabulary, so this mapping is direct (no case-fold pass needed in
# ``severity_to_confidence_level``). The helper remains case-insensitive
# for forward-compat with any UPPER label leaking in from external
# sources.
#
# W565 — the per-site ``severity -> confidence-level`` table moved to
# the canonical helper in :mod:`roam.output._severity`. The default
# table already maps critical/high -> "high", warning/medium ->
# "medium", info/low/unknown -> "low" — exactly the contract this
# command needs. ``severity_to_confidence_level`` is case-insensitive
# so the uppercase severity labels emitted by ``_severity()`` resolve
# unchanged.


def _complexity_classify(sym: dict) -> tuple[str, str]:
    """Map a complexity ranking entry to a (confidence, reason) tuple."""
    score = sym.get("cognitive_complexity", 0) or 0
    severity = sym.get("severity") or _severity(score)
    conf = severity_to_confidence_level(severity)
    reason = f"cognitive complexity {score:.0f} → {severity} (refactor signal)"
    return conf, reason


def _safe_metric(row, key, default=0.0):
    """Safely access a metric column that may not exist in older DBs."""
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError):
        return default


def _complexity_to_sarif_payload(symbols, *, threshold, warnings):
    """Build the SARIF projection text + write the SARIF envelope.

    Pulled out for the W607-BJ ``_run_check_bj`` wrap so a SARIF-layer
    raise surfaces a single
    ``complexity_serialize_to_sarif_failed:<exc>:<detail>`` marker and
    the caller can fall back to the JSON envelope.
    """
    from roam.output.sarif import complexity_to_sarif, write_sarif

    sarif = complexity_to_sarif(symbols, threshold=threshold, warnings=warnings)
    return write_sarif(sarif)


def _apply_role_filters(
    rows,
    *,
    include_tooling: bool,
    no_framework: bool,
    no_imports: bool,
):
    """In-Python role-filter chain for the complexity ranking.

    Pulled out of the click body so the W607-BJ ``_run_check_bj`` wrap
    can surface a single ``complexity_apply_filters_failed:<exc>`` marker
    if any of the three filter sub-passes raises (the three imports are
    independent and degrade together -- a failure in one is a substrate-
    layer failure for the whole filter stage).
    """
    out = list(rows)
    if not include_tooling:
        from roam.output.file_role_hints import is_excluded_path

        out = [r for r in out if not is_excluded_path(r["file_path"])]
    if no_framework:
        from roam.commands.cmd_health import _FRAMEWORK_NAMES

        out = [r for r in out if (r["name"] or "") not in _FRAMEWORK_NAMES]
    if no_imports:
        out = [
            r for r in out if r["kind"] not in {"import", "module_import"} and "import" not in (r["name"] or "").lower()
        ]
    return out


def _severity(score: float) -> str:
    """Map cognitive complexity score to a severity label.

    W761 — emits the canonical lowercase W547 vocabulary
    (``critical`` / ``high`` / ``medium`` / ``low``). Pre-W761 this
    helper returned UPPER-case labels which then flowed into the
    per-symbol envelope ``severity`` slot via the JSON-mode rankings
    builder (the W762 drift-guard didn't fire because the dict literal
    was ``{"severity": _severity(...)}`` — helper-indirected, not a
    bare string Constant). Lowercasing here aligns the envelope slot
    with W547 and the cmd_alerts (W649) precedent without changing
    the score thresholds.
    """
    if score >= 25:
        return "critical"
    if score >= 15:
        return "high"
    if score >= 8:
        return "medium"
    return "low"


def _severity_icon(sev: str) -> str:
    """Map a severity label to an ASCII display icon (W847 internal
    vocabulary). The icon table keys are lowercase post-W761; callers
    pass the canonical W547 spelling. UPPER-case keys are retained as
    aliases for back-compat in case a stale per-row dict (e.g. ingested
    from a pre-W761 findings registry row) flows back through this
    helper.
    """
    icons = {
        # W761: canonical W547 lowercase vocabulary.
        "critical": "!!",
        "high": "! ",
        "medium": "~ ",
        "low": "  ",
        # W761/W847 retained UPPER-case aliases for back-compat with
        # pre-W761 per-row severity strings (e.g. findings registry
        # rows mirrored before the W761 lowercase migration). The
        # display-icon table is internal vocabulary (text mode only,
        # never an envelope slot), so the aliases are intentional.
        "CRITICAL": "!!",
        "HIGH": "! ",
        "MEDIUM": "~ ",
        "LOW": "  ",
    }
    return icons.get(sev, "  ")


@roam_capability(
    category="health",
    summary="Show per-symbol cognitive complexity rankings.",
    inputs=["target"],
    outputs=["complexity_rankings", "verdict"],
    examples=[
        "roam complexity",
        "roam complexity --top 50",
        "roam complexity my_module",
    ],
    tags=["health", "metrics"],
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
@click.command("complexity")
@click.argument("target", required=False, default=None)
@click.option(
    "--top",
    "--limit",
    "-n",
    "limit",
    default=20,
    type=int,
    help="Number of results to show (alias: --limit, -n)",
)
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=None,
    help="Minimum cognitive complexity to include",
)
@click.option(
    "--by-file",
    is_flag=True,
    help="Group results by file and show per-file summary",
)
@click.option(
    "--bumpy-road",
    is_flag=True,
    help="Detect bumpy-road pattern: files with multiple medium-complexity functions",
)
@click.option(
    "--include-tooling",
    is_flag=True,
    default=False,
    help=(
        "Include CI scripts, examples, generated code, vendor, and "
        "workspaces directories. Excluded by default — high complexity "
        "in tooling/codegen is expected and uninteresting (Python pivot "
        "dogfood found agent-generated workspaces dominating)."
    ),
)
@click.option(
    "--no-framework",
    is_flag=True,
    help="Filter framework/lifecycle/i18n shorthand symbols from rankings",
)
@click.option(
    "--no-imports",
    is_flag=True,
    help="Filter import/interop wrapper symbols from rankings",
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror complexity hotspots (cognitive_complexity >= 15) into the "
        "central findings registry — visible via "
        "``roam findings list --detector complexity``. The detector-specific "
        "output is unchanged; the registry rows are the denormalised "
        "cross-detector surface. Persisted rows ignore --top/-n display "
        "limits — all HIGH/CRITICAL symbols are written so re-running with "
        "a smaller --top doesn't truncate the registry."
    ),
)
@click.pass_context
def complexity(ctx, target, limit, threshold, by_file, bumpy_road, include_tooling, no_framework, no_imports, persist):
    """Show cognitive complexity metrics for functions and methods.

    Unlike ``health`` (which scores the whole codebase) and ``debt`` (which
    estimates remediation effort), this command ranks individual symbols by
    cognitive complexity.

    Ranks symbols by a multi-factor complexity score that accounts for
    nesting depth, boolean operators, callback depth, and control-flow
    breaks. Use --bumpy-road to find files where many functions are
    individually moderate but collectively hard to maintain.

    \b
    Examples:
      roam complexity
      roam complexity --threshold 15 --top 50
      roam complexity src/auth.py --by-file
      roam complexity --bumpy-road

    See also ``health`` (whole-codebase score), ``debt`` (remediation
    effort + ROI), and ``hotspots`` (runtime hotspots).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W1086 (Pattern 1B / Pattern 2): accumulator for silent-fallback warnings.
    # The two consumer sites are the count-probe ``sqlite3.OperationalError``
    # handler below (advisory: symbol_metrics unreadable, treat as empty) and the
    # ``--persist`` ``except sqlite3.OperationalError`` further down (pre-W89
    # findings table missing, persist silently no-ops). Per-row ``_safe_metric``
    # KeyErrors stay silent — best-effort per cell, would spam the accumulator.
    # Emitted on the JSON envelope as top-level ``warnings_out`` ONLY when
    # non-empty (hash-stable: empty-case envelope is byte-identical to
    # pre-W1086). Unblocks future W1060 SARIF runtime-notifications plumb.
    warnings: list[str] = []

    # W607-BJ -- substrate-CALL marker plumbing on the per-symbol complexity
    # ranking surface. cmd_complexity is the third leg of the
    # health/debt/complexity DB-substrate trio: cmd_health (W607-M + W607-BA,
    # ``health_*``) scores the whole codebase, cmd_debt (W607-BG, ``debt_*``)
    # ranks files by hotspot-weighted remediation cost, and cmd_complexity
    # (W607-BJ, ``complexity_*``) ranks individual symbols by cognitive
    # complexity. All three consume the same DB substrate (symbol_metrics,
    # symbols, files); each owns a distinct marker prefix family for
    # observability discipline.
    #
    # The substrate boundaries we wrap:
    #
    #   * query_symbol_metrics       -- main symbol_metrics JOIN with WHERE/
    #                                   ORDER/LIMIT for the ranking
    #   * apply_filters              -- in-Python role-filter chain (tooling,
    #                                   framework, imports)
    #   * compute_distribution_stats -- the full-table cognitive_complexity
    #                                   scan used for avg/p90/critical/high
    #   * classify_severity          -- the wrap_findings + confidence
    #                                   distribution layer
    #   * serialize_to_sarif         -- SARIF projection
    #   * emit_findings              -- W93/W102 findings-registry mirror
    #
    # Marker family ``complexity_<phase>_failed:<exc_class>:<detail>``.
    # Empty bucket -> no field added -> byte-identical envelope on the
    # happy path. Threads into BOTH the top-level ``warnings_out`` (the
    # existing W1086 accumulator -- preserved-list-field discipline) AND
    # ``summary.partial_success=True`` on the degraded path.
    _w607bj_warnings_out: list[str] = []

    def _run_check_bj(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BJ marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a
        ``complexity_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607bj_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bj_warnings_out.append(f"complexity_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # Check if symbol_metrics table has data
        try:
            count = conn.execute("SELECT COUNT(*) FROM symbol_metrics").fetchone()[0]
        except sqlite3.OperationalError:
            count = -1
            warnings.append("symbol_metrics count probe failed; treating as empty")
        if count <= 0:
            # W810 + Pattern-1B: empty symbol_metrics is a RECOVERABLE state
            # ("no data yet, re-index"), not a programmer-class failure. The
            # prior ``raise SystemExit(1)`` paired with structured stdout
            # tripped the MCP wrapper-bridge layer into emitting a generic
            # COMMAND_FAILED envelope, burying the actionable verdict. Exit
            # cleanly so the structured envelope reaches the agent. The
            # ``next_command`` field carries the recovery instruction.
            verdict = "No complexity data — re-index with `roam index --force` to populate symbols"
            if json_mode:
                summary_empty: dict = {
                    "verdict": verdict,
                    # W1298: LAW-4 concrete-noun anchor; "total" terminates
                    # the auto-derived fact on the value (digit), not a noun.
                    "functions": 0,
                    "state": "no_complexity_data",
                }
                if warnings:
                    summary_empty["partial_success"] = True
                click.echo(
                    to_json(
                        json_envelope(
                            "complexity",
                            summary=summary_empty,
                            results=[],
                            next_command="roam index --force",
                            **({"warnings_out": warnings} if warnings else {}),
                        )
                    )
                )
            else:
                if warnings:
                    for _w in warnings:
                        click.echo(f"WARNING: {_w}", err=True)
                click.echo(verdict)
            return

        if bumpy_road:
            _bumpy_road(conn, json_mode, limit, threshold)
            return

        # Build query
        where_parts = []
        params = []

        if target:
            # Filter by file path or symbol name
            where_parts.append("(f.path LIKE ? OR s.name LIKE ? OR s.qualified_name LIKE ?)")
            pattern = f"%{target}%"
            params.extend([pattern, pattern, pattern])

        if threshold is not None:
            where_parts.append("sm.cognitive_complexity >= ?")
            params.append(threshold)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        # Pull more rows than ``limit`` when default-excluding tooling
        # so the displayed top-N still has the requested count after
        # filtering. 5x is comfortable for typical exclusion shares.
        fetch_limit = limit * 5 if not include_tooling else limit
        rows = _run_check_bj(
            "query_symbol_metrics",
            lambda: conn.execute(
                f"""SELECT sm.*, s.name, s.qualified_name, s.kind,
                           s.line_start, s.line_end, f.path as file_path
                    FROM symbol_metrics sm
                    JOIN symbols s ON sm.symbol_id = s.id
                    JOIN files f ON s.file_id = f.id
                    WHERE {where_clause}
                    ORDER BY sm.cognitive_complexity DESC
                    LIMIT ?""",
                params + [fetch_limit],
            ).fetchall(),
            default=[],
        )
        if rows is None:
            rows = []

        rows = _run_check_bj(
            "apply_filters",
            _apply_role_filters,
            rows,
            include_tooling=include_tooling,
            no_framework=no_framework,
            no_imports=no_imports,
            default=rows,
        )
        if rows is None:
            rows = []

        # --- W93 follow-up: mirror hotspots into the central findings registry.
        # Runs ONLY with --persist. The persisted set is independent of the
        # --top/-n display slice — we query all symbols above the emit
        # threshold so re-running with a smaller --top doesn't truncate the
        # registry. The detector-specific output (text / JSON / SARIF) below
        # is unchanged.
        if persist:
            # W607-BJ: surface unexpected emit_findings failures via the
            # ``complexity_emit_findings_failed:<exc>:<detail>`` marker on
            # the W607-BJ bucket. The pre-W89 schema path (sqlite3
            # OperationalError on missing ``findings`` table) is the
            # EXPECTED degraded path and is handled by the dedicated
            # except branch below -- it does NOT surface via the
            # complexity_* marker family.
            try:
                _persist_complexity_findings(
                    conn,
                    include_tooling=include_tooling,
                    no_framework=no_framework,
                    no_imports=no_imports,
                )
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                # W1086 (Pattern 2): surface the silent fallback so consumers
                # know --persist asked for findings but produced none. The
                # actionable next step is to rebuild the index so the
                # W89-era findings table gets created.
                warnings.append("findings table missing; complexity findings not persisted (pre-W89 schema)")
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-BJ disclosure
                _w607bj_warnings_out.append(f"complexity_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

        rows = rows[:limit]

        # W607-BJ: merge substrate-CALL markers into the top-level
        # ``warnings`` axis (preserved-list-field discipline). The W607-BJ
        # bucket is a sub-stream of the same warnings_out field that W1086
        # already populates -- consumers see ONE list, not two. Empty
        # bucket -> no change -> byte-identical envelope on the happy
        # path.
        def _merged_warnings() -> list[str]:
            """Compose ``warnings`` (W1086) ++ ``_w607bj_warnings_out``."""
            return list(warnings) + list(_w607bj_warnings_out)

        if not rows:
            _all_w = _merged_warnings()
            if sarif_mode:
                _sarif_payload = _run_check_bj(
                    "serialize_to_sarif",
                    _complexity_to_sarif_payload,
                    [],
                    threshold=threshold or 0,
                    warnings=_all_w,
                    default=None,
                )
                if _sarif_payload is not None:
                    click.echo(_sarif_payload)
                else:
                    # Re-read merged warnings -- the wrap above may have
                    # appended its own marker.
                    _all_w = _merged_warnings()
                    if json_mode:
                        click.echo(
                            to_json(
                                json_envelope(
                                    "complexity",
                                    summary={
                                        "verdict": "SARIF projection failed; falling back to JSON envelope",
                                        "functions": 0,
                                        "partial_success": True,
                                    },
                                    results=[],
                                    **({"warnings_out": _all_w} if _all_w else {}),
                                )
                            )
                        )
                return
            verdict = "No matching symbols found."
            if json_mode:
                # W1298: use "functions" (LAW-4 concrete-noun anchor) over
                # "total" so the auto-derived fact terminal is in the
                # accepted set (test_w806_complexity_empty_corpus pin).
                # C3 (Pattern-2): explicit not_found state, matching the
                # world-model siblings (side-effects/idempotency/causal-graph)
                # so consumers switch on a field rather than parsing the verdict.
                summary_norows: dict = {"verdict": verdict, "functions": 0, "state": "not_found"}
                if _all_w:
                    summary_norows["partial_success"] = True
                click.echo(
                    to_json(
                        json_envelope(
                            "complexity",
                            summary=summary_norows,
                            results=[],
                            **({"warnings_out": _all_w} if _all_w else {}),
                        )
                    )
                )
            else:
                if _all_w:
                    for _w in _all_w:
                        click.echo(f"WARNING: {_w}", err=True)
                click.echo(verdict)
            return

        if sarif_mode:
            complex_symbols = [
                {
                    "name": r["qualified_name"] or r["name"],
                    "kind": r["kind"],
                    "file": r["file_path"],
                    "line": r["line_start"],
                    "cognitive_complexity": r["cognitive_complexity"],
                    "severity": _severity(r["cognitive_complexity"]),
                }
                for r in rows
            ]
            _all_w = _merged_warnings()
            _sarif_payload = _run_check_bj(
                "serialize_to_sarif",
                _complexity_to_sarif_payload,
                complex_symbols,
                threshold=threshold or 0,
                warnings=_all_w,
                default=None,
            )
            if _sarif_payload is not None:
                click.echo(_sarif_payload)
            else:
                # SARIF projection collapsed -> emit a degraded envelope
                # surfacing the marker so the consumer sees the rankings
                # produced but the SARIF projection failed.
                _all_w = _merged_warnings()
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "complexity",
                                summary={
                                    "verdict": "SARIF projection failed; falling back to JSON envelope",
                                    "functions": len(rows),
                                    "partial_success": True,
                                },
                                results=[],
                                **({"warnings_out": _all_w} if _all_w else {}),
                            )
                        )
                    )
            return

        if by_file:
            _by_file_output(conn, rows, json_mode, warnings=_merged_warnings())
            return

        # Compute distribution stats (W607-BJ: substrate boundary).
        # When a `target`/`threshold` filter is active, the distribution must
        # be scoped to the SAME filter as `rows` — otherwise a per-file query
        # reported REPO-WIDE avg/p90/critical/high (Pattern-3 scope mismatch:
        # `roam complexity <file>` showed identical critical_count across
        # every file). No filter → fast whole-table path (unchanged).
        if target or threshold is not None:
            all_scores = _run_check_bj(
                "compute_distribution_stats",
                lambda: conn.execute(
                    f"""SELECT sm.cognitive_complexity
                        FROM symbol_metrics sm
                        JOIN symbols s ON sm.symbol_id = s.id
                        JOIN files f ON s.file_id = f.id
                        WHERE {where_clause}
                        ORDER BY sm.cognitive_complexity DESC""",
                    params,
                ).fetchall(),
                default=[],
            )
        else:
            all_scores = _run_check_bj(
                "compute_distribution_stats",
                lambda: conn.execute(
                    "SELECT cognitive_complexity FROM symbol_metrics ORDER BY cognitive_complexity DESC"
                ).fetchall(),
                default=[],
            )
        if all_scores is None:
            all_scores = []
        scores = [r[0] for r in all_scores]
        total = len(scores)
        avg = sum(scores) / total if total else 0
        p90 = scores[int(total * 0.1)] if total > 10 else (scores[0] if scores else 0)
        critical_count = sum(1 for s in scores if s >= 25)
        high_count = sum(1 for s in scores if 15 <= s < 25)

        if json_mode:
            _worst_name = (rows[0]["qualified_name"] or rows[0]["name"]) if rows else "none"
            _worst_cc = rows[0]["cognitive_complexity"] if rows else 0
            _cx_verdict = (
                f"avg complexity {avg:.1f}, "
                f"{critical_count} critical, {high_count} high; "
                f"worst: {_worst_name}({_worst_cc:.0f})"
            )
            # R22: wrap each symbol in {value, confidence, reason}.
            # Consumers that previously read `symbols[i]["name"]` must
            # now read `symbols[i]["value"]["name"]` plus
            # `symbols[i]["confidence"]` / `symbols[i]["reason"]`.
            symbol_values = [
                {
                    "name": r["qualified_name"] or r["name"],
                    "kind": r["kind"],
                    "file": r["file_path"],
                    "line": r["line_start"],
                    "cognitive_complexity": r["cognitive_complexity"],
                    "nesting_depth": r["nesting_depth"],
                    "param_count": r["param_count"],
                    "line_count": r["line_count"],
                    "return_count": r["return_count"],
                    "bool_op_count": r["bool_op_count"],
                    "callback_depth": r["callback_depth"],
                    "cyclomatic_density": _safe_metric(r, "cyclomatic_density"),
                    "halstead_volume": _safe_metric(r, "halstead_volume"),
                    "halstead_difficulty": _safe_metric(r, "halstead_difficulty"),
                    "halstead_effort": _safe_metric(r, "halstead_effort"),
                    "halstead_bugs": _safe_metric(r, "halstead_bugs"),
                    "severity": _severity(r["cognitive_complexity"]),
                }
                for r in rows
            ]
            # W607-BJ: classify_severity substrate boundary. ``wrap_findings``
            # + ``confidence_distribution`` are the canonical
            # severity-classification layer (severity -> confidence tier
            # bucketing). A raise here is a substrate failure: emit the
            # marker and fall back to bare values without the {value,
            # confidence, reason} triples.
            _classified = _run_check_bj(
                "classify_severity",
                lambda: (wrap_findings(symbol_values, classifier=_complexity_classify),),
                default=None,
            )
            if _classified is not None:
                symbol_triples = _classified[0]
                distribution = confidence_distribution(symbol_triples)
            else:
                symbol_triples = symbol_values
                distribution = {}
            _cx_verdict = verdict_with_high_count(_cx_verdict, distribution)
            summary_main: dict = {
                "verdict": _cx_verdict,
                "total_analyzed": total,
                "average_complexity": round(avg, 1),
                "p90_complexity": round(p90, 1),
                "critical_count": critical_count,
                "high_count": high_count,
                "showing": len(rows),
                "findings_confidence_distribution": distribution,
                # W331: clarify which complexity metric the
                # rankings use — cognitive (Sonar) not McCabe.
                "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
            }
            _all_w = _merged_warnings()
            if _all_w:
                summary_main["partial_success"] = True
                # W607-BJ: mirror the marker stream onto summary.warnings_out
                # so consumers reading the summary block alone see the
                # degraded substrates (paired with the top-level mirror).
                summary_main["warnings_out"] = list(_all_w)
            click.echo(
                to_json(
                    json_envelope(
                        "complexity",
                        summary=summary_main,
                        budget=token_budget,
                        symbols=symbol_triples,
                        **({"warnings_out": _all_w} if _all_w else {}),
                    )
                )
            )
            return

        # Text output
        _all_w = _merged_warnings()
        if _all_w:
            for _w in _all_w:
                click.echo(f"WARNING: {_w}", err=True)
        _worst_name_txt = (rows[0]["qualified_name"] or rows[0]["name"]) if rows else "none"
        _worst_cc_txt = rows[0]["cognitive_complexity"] if rows else 0
        _cx_verdict_txt = (
            f"avg complexity {avg:.1f}, "
            f"{critical_count} critical, {high_count} high; "
            f"worst: {_worst_name_txt}({_worst_cc_txt:.0f})"
        )
        click.echo(f"VERDICT: {_cx_verdict_txt}")
        click.echo()
        click.echo(
            f"Cognitive complexity ({total} functions analyzed, "
            f"avg={avg:.1f}, p90={p90:.1f}, "
            f"{critical_count} critical, {high_count} high):\n"
        )

        for r in rows:
            sev = _severity(r["cognitive_complexity"])
            icon = _severity_icon(sev)
            name = r["qualified_name"] or r["name"]
            location = loc(r["file_path"], r["line_start"])
            kind = abbrev_kind(r["kind"])

            factors = []
            if r["nesting_depth"] >= 4:
                factors.append(f"nest={r['nesting_depth']}")
            if r["bool_op_count"] >= 3:
                factors.append(f"bool={r['bool_op_count']}")
            if r["callback_depth"] >= 2:
                factors.append(f"cb={r['callback_depth']}")
            if r["param_count"] >= 5:
                factors.append(f"params={r['param_count']}")
            if r["return_count"] >= 4:
                factors.append(f"ret={r['return_count']}")
            cd = _safe_metric(r, "cyclomatic_density")
            if cd > 0.15:
                factors.append(f"density={cd:.2f}")
            hv = _safe_metric(r, "halstead_volume")
            if hv > 500:
                factors.append(f"H.vol={hv:.0f}")

            factor_str = f" ({', '.join(factors)})" if factors else ""

            click.echo(f"  {icon}{r['cognitive_complexity']:5.0f}  {name:<45s} {kind} {location}{factor_str}")


def _persist_complexity_findings(
    conn,
    *,
    include_tooling: bool,
    no_framework: bool,
    no_imports: bool,
) -> None:
    """Run the persist-side query and emit findings for every hotspot.

    Independent of the display query so re-running with a smaller --top
    doesn't truncate the registry. We pull every symbol at or above
    ``COMPLEXITY_FINDING_THRESHOLD``, apply the same role filters the
    display query applies (tooling/framework/imports), then emit one
    finding per surviving row. The caller is responsible for opening
    ``conn`` writable.
    """
    rows = conn.execute(
        """SELECT sm.symbol_id, sm.cognitive_complexity, sm.nesting_depth,
                  sm.param_count, sm.line_count, sm.return_count,
                  sm.bool_op_count, sm.callback_depth,
                  sm.cyclomatic_density, sm.halstead_volume,
                  sm.halstead_difficulty, sm.halstead_effort, sm.halstead_bugs,
                  s.name, s.qualified_name, s.kind,
                  s.line_start, s.line_end, f.path as file_path
           FROM symbol_metrics sm
           JOIN symbols s ON sm.symbol_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE sm.cognitive_complexity >= ?
           ORDER BY sm.cognitive_complexity DESC""",
        (COMPLEXITY_FINDING_THRESHOLD,),
    ).fetchall()
    if not include_tooling:
        from roam.output.file_role_hints import is_excluded_path

        rows = [r for r in rows if not is_excluded_path(r["file_path"])]
    if no_framework:
        from roam.commands.cmd_health import _FRAMEWORK_NAMES

        rows = [r for r in rows if (r["name"] or "") not in _FRAMEWORK_NAMES]
    if no_imports:
        rows = [
            r
            for r in rows
            if r["kind"] not in {"import", "module_import"} and "import" not in (r["name"] or "").lower()
        ]
    _emit_complexity_findings(conn, rows)
    conn.commit()


def _by_file_output(conn, rows, json_mode, *, warnings: list[str] | None = None):
    """Group complexity results by file.

    *warnings* (W1086): when supplied, the JSON envelope surfaces the list on
    top-level ``warnings_out`` and stamps ``summary.partial_success`` so
    consumers see silent-fallback state from the parent dispatch (e.g., a
    pre-W89 ``findings`` table during ``--persist``). When ``None`` or empty,
    the envelope is byte-identical to the pre-W1086 shape.
    """
    from collections import defaultdict

    by_file = defaultdict(list)
    for r in rows:
        by_file[r["file_path"]].append(r)

    file_summaries = []
    for fpath, syms in sorted(by_file.items()):
        scores = [s["cognitive_complexity"] for s in syms]
        file_summaries.append(
            {
                "file": fpath,
                "symbols": len(syms),
                "max_complexity": max(scores),
                "avg_complexity": round(sum(scores) / len(scores), 1),
                "total_complexity": round(sum(scores), 1),
                "items": syms,
            }
        )

    file_summaries.sort(key=lambda f: f["total_complexity"], reverse=True)

    if json_mode:
        _bf_max = file_summaries[0]["max_complexity"] if file_summaries else 0
        _bf_file = file_summaries[0]["file"].split("/")[-1] if file_summaries else "none"
        _bf_verdict = f"{len(file_summaries)} files analyzed, worst file: {_bf_file} (max={_bf_max:.0f})"
        summary_bf: dict = {
            "verdict": _bf_verdict,
            "files": len(file_summaries),
            # W331: by-file rollups still rank by cognitive complexity.
            "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
        }
        if warnings:
            summary_bf["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "complexity",
                    summary=summary_bf,
                    files=[
                        {
                            "file": fs["file"],
                            "symbol_count": fs["symbols"],
                            "max_complexity": fs["max_complexity"],
                            "avg_complexity": fs["avg_complexity"],
                            "total_complexity": fs["total_complexity"],
                        }
                        for fs in file_summaries
                    ],
                    **({"warnings_out": list(warnings)} if warnings else {}),
                )
            )
        )
        return

    if warnings:
        for _w in warnings:
            click.echo(f"WARNING: {_w}", err=True)
    for fs in file_summaries:
        click.echo(
            f"  {fs['file']} — {fs['symbols']} functions, "
            f"max={fs['max_complexity']:.0f}, avg={fs['avg_complexity']:.1f}, "
            f"total={fs['total_complexity']:.0f}"
        )
        for s in sorted(fs["items"], key=lambda x: x["cognitive_complexity"], reverse=True):
            sev = _severity(s["cognitive_complexity"])
            icon = _severity_icon(sev)
            click.echo(f"    {icon}{s['cognitive_complexity']:5.0f}  {s['name']}")
        click.echo()


def _bumpy_road(conn, json_mode, limit, threshold):
    """Detect bumpy-road pattern: files with many moderate-complexity functions.

    A file with 10 functions at complexity 8 is harder to maintain than
    a file with 1 function at complexity 20, even though the single
    function scores higher. The bumpy road score captures this.
    """
    min_score = threshold or 5  # Minimum per-function complexity to count

    rows = conn.execute(
        """SELECT f.path, COUNT(*) as func_count,
                  SUM(sm.cognitive_complexity) as total,
                  AVG(sm.cognitive_complexity) as avg_cc,
                  MAX(sm.cognitive_complexity) as max_cc,
                  MAX(sm.nesting_depth) as max_nest
           FROM symbol_metrics sm
           JOIN symbols s ON sm.symbol_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE sm.cognitive_complexity >= ?
           GROUP BY f.path
           HAVING COUNT(*) >= 3
           ORDER BY SUM(sm.cognitive_complexity) DESC
           LIMIT ?""",
        (min_score, limit),
    ).fetchall()

    if not rows:
        verdict = "No bumpy-road files found."
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "complexity",
                        summary={"verdict": verdict, "total": 0},
                        bumpy_road=[],
                    )
                )
            )
        else:
            click.echo(verdict)
        return

    if json_mode:
        _br_verdict = f"{len(rows)} bumpy-road files found (3+ functions with complexity >= {min_score})"
        click.echo(
            to_json(
                json_envelope(
                    "complexity",
                    summary={
                        "verdict": _br_verdict,
                        "mode": "bumpy-road",
                        "threshold": min_score,
                        "files_found": len(rows),
                        # W331: bumpy-road also thresholds on cognitive complexity.
                        "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
                    },
                    files=[
                        {
                            "file": r["path"],
                            "complex_functions": r["func_count"],
                            "total_complexity": round(r["total"], 1),
                            "avg_complexity": round(r["avg_cc"], 1),
                            "max_complexity": round(r["max_cc"], 1),
                            "max_nesting": r["max_nest"],
                            "bumpy_score": round(r["func_count"] * r["avg_cc"], 1),
                        }
                        for r in rows
                    ],
                )
            )
        )
        return

    _br_verdict_txt = f"{len(rows)} bumpy-road files found (3+ functions with complexity >= {min_score})"
    click.echo(f"VERDICT: {_br_verdict_txt}")
    click.echo()
    click.echo(f"Bumpy-road files (3+ functions with complexity >= {min_score}):\n")
    for r in rows:
        bumpy = r["func_count"] * r["avg_cc"]
        click.echo(
            f"  {r['path']}\n"
            f"    {r['func_count']} complex functions, "
            f"total={r['total']:.0f}, avg={r['avg_cc']:.1f}, "
            f"max={r['max_cc']:.0f}, bumpy_score={bumpy:.0f}"
        )
