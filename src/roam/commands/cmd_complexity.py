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
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output._severity import severity_to_confidence_level
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
# cutoff: 15 is the floor for HIGH severity (refactor-target tier).
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
#   severity CRITICAL or HIGH (score >= 15) → "high"  (refactor target)
#   severity MEDIUM  (8 <= score < 15)       → "medium" (monitor)
#   severity LOW     (score < 8)             → "low"    (no action)
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


def _severity(score: float) -> str:
    """Map cognitive complexity score to a severity label."""
    if score >= 25:
        return "CRITICAL"
    if score >= 15:
        return "HIGH"
    if score >= 8:
        return "MEDIUM"
    return "LOW"


def _severity_icon(sev: str) -> str:
    icons = {"CRITICAL": "!!", "HIGH": "! ", "MEDIUM": "~ ", "LOW": "  "}
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
        "dogfood 2026-05-02 found agent-generated workspaces dominating)."
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

    with open_db(readonly=not persist) as conn:
        # Check if symbol_metrics table has data
        try:
            count = conn.execute("SELECT COUNT(*) FROM symbol_metrics").fetchone()[0]
        except Exception:
            count = -1
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
                click.echo(
                    to_json(
                        json_envelope(
                            "complexity",
                            summary={
                                "verdict": verdict,
                                "total": 0,
                                "state": "no_complexity_data",
                            },
                            results=[],
                            next_command="roam index --force",
                        )
                    )
                )
            else:
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
        rows = conn.execute(
            f"""SELECT sm.*, s.name, s.qualified_name, s.kind,
                       s.line_start, s.line_end, f.path as file_path
                FROM symbol_metrics sm
                JOIN symbols s ON sm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE {where_clause}
                ORDER BY sm.cognitive_complexity DESC
                LIMIT ?""",
            params + [fetch_limit],
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

        # --- W93 follow-up: mirror hotspots into the central findings registry.
        # Runs ONLY with --persist. The persisted set is independent of the
        # --top/-n display slice — we query all symbols above the emit
        # threshold so re-running with a smaller --top doesn't truncate the
        # registry. The detector-specific output (text / JSON / SARIF) below
        # is unchanged.
        if persist:
            try:
                _persist_complexity_findings(
                    conn,
                    include_tooling=include_tooling,
                    no_framework=no_framework,
                    no_imports=no_imports,
                )
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        rows = rows[:limit]

        if not rows:
            if sarif_mode:
                from roam.output.sarif import complexity_to_sarif, write_sarif

                sarif = complexity_to_sarif([], threshold=threshold or 0)
                click.echo(write_sarif(sarif))
                return
            verdict = "No matching symbols found."
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "complexity",
                            summary={"verdict": verdict, "total": 0},
                            results=[],
                        )
                    )
                )
            else:
                click.echo(verdict)
            return

        if sarif_mode:
            from roam.output.sarif import complexity_to_sarif, write_sarif

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
            sarif = complexity_to_sarif(complex_symbols, threshold=threshold or 0)
            click.echo(write_sarif(sarif))
            return

        if by_file:
            _by_file_output(conn, rows, json_mode)
            return

        # Compute distribution stats
        all_scores = conn.execute(
            "SELECT cognitive_complexity FROM symbol_metrics ORDER BY cognitive_complexity DESC"
        ).fetchall()
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
            symbol_triples = wrap_findings(symbol_values, classifier=_complexity_classify)
            distribution = confidence_distribution(symbol_triples)
            _cx_verdict = verdict_with_high_count(_cx_verdict, distribution)
            click.echo(
                to_json(
                    json_envelope(
                        "complexity",
                        summary={
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
                        },
                        budget=token_budget,
                        symbols=symbol_triples,
                    )
                )
            )
            return

        # Text output
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
            if r["kind"] not in {"import", "module_import"}
            and "import" not in (r["name"] or "").lower()
        ]
    _emit_complexity_findings(conn, rows)
    conn.commit()


def _by_file_output(conn, rows, json_mode):
    """Group complexity results by file."""
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
        click.echo(
            to_json(
                json_envelope(
                    "complexity",
                    summary={
                        "verdict": _bf_verdict,
                        "files": len(file_summaries),
                        # W331: by-file rollups still rank by cognitive complexity.
                        "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
                    },
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
                )
            )
        )
        return

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
