"""Show runtime or security hotspots."""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
from collections import defaultdict, deque

import click

from roam.capability import roam_capability
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.formatter import json_envelope, strip_list_payloads, to_json

# W120 — hotspots is the fifth detector migrating onto the central
# findings registry (after clones W95, dead W99, complexity W102,
# bus-factor W115). Hotspots is the canonical *runtime* detector —
# every emitted finding comes from a row in ``runtime_stats``, which is
# populated by ``roam ingest-trace`` from real OTel / Jaeger / Zipkin
# data. The detector then compares the static ranking against the
# runtime ranking and tags each symbol UPGRADE / CONFIRMED / DOWNGRADE.
# All three classifications carry the ``runtime`` confidence tier —
# they all required ingested production traces. Bump this when the
# rank-discrepancy thresholds or the classification labels in
# ``roam.runtime.hotspots.compute_hotspots`` change meaningfully so
# consumers can spot rows produced under an older classifier shape.
HOTSPOTS_DETECTOR_VERSION: str = "1.0.0"

# W564: severity ordering now routed through roam.output._severity.severity_rank
_ENTRYPOINT_HINT = re.compile(
    r"(main|handler|route|endpoint|controller|serve|api|http)",
    re.IGNORECASE,
)

_SECURITY_SINKS = (
    {
        "id": "py-eval-exec",
        "title": "Dynamic code execution (eval/exec)",
        "severity": "critical",
        "languages": {"python"},
        "regex": re.compile(r"\b(?:eval|exec)\s*\("),
        "recommendation": "Avoid eval/exec. Use explicit dispatch or validated parsing.",
    },
    {
        "id": "py-os-system",
        "title": "Shell command execution (os.system)",
        "severity": "high",
        "languages": {"python"},
        "regex": re.compile(r"\bos\.system\s*\("),
        "recommendation": "Prefer subprocess with argument lists and strict input validation.",
    },
    {
        "id": "py-subprocess",
        "title": "Subprocess execution",
        "severity": "high",
        "languages": {"python"},
        "regex": re.compile(r"\bsubprocess\.(?:run|popen|call|check_call|check_output)\s*\(", re.IGNORECASE),
        "recommendation": "Avoid shell execution paths and sanitize all command inputs.",
    },
    {
        "id": "py-pickle-load",
        "title": "Unsafe deserialization (pickle.load/loads)",
        "severity": "critical",
        "languages": {"python"},
        "regex": re.compile(r"\bpickle\.(?:load|loads)\s*\("),
        "recommendation": "Do not deserialize untrusted pickle data. Use safe structured formats.",
    },
    {
        "id": "py-yaml-load",
        "title": "Unsafe YAML load",
        "severity": "high",
        "languages": {"python"},
        "regex": re.compile(r"\byaml\.load\s*\("),
        "recommendation": "Use yaml.safe_load for untrusted input.",
    },
    {
        "id": "sql-execute",
        "title": "Raw SQL execute call",
        "severity": "medium",
        "languages": {"python", "javascript", "typescript", "go", "java", "ruby", "php"},
        "regex": re.compile(r"\.\s*(?:execute|executemany)\s*\("),
        "recommendation": "Use parameterized queries and validate dynamic query fragments.",
    },
    {
        "id": "js-eval",
        "title": "Dynamic JavaScript eval",
        "severity": "critical",
        "languages": {"javascript", "typescript", "tsx", "jsx"},
        "regex": re.compile(r"\beval\s*\("),
        "recommendation": "Avoid eval. Use safe parsers or explicit function maps.",
    },
    {
        "id": "js-innerhtml",
        "title": "Direct DOM HTML injection",
        "severity": "high",
        "languages": {"javascript", "typescript", "tsx", "jsx"},
        "regex": re.compile(r"\b(?:innerHTML|outerHTML)\s*="),
        "recommendation": "Use safe DOM APIs and HTML sanitization before rendering.",
    },
    {
        "id": "react-dangerous-html",
        "title": "dangerouslySetInnerHTML usage",
        "severity": "high",
        "languages": {"javascript", "typescript", "tsx", "jsx"},
        "regex": re.compile(r"\bdangerouslySetInnerHTML\b"),
        "recommendation": "Sanitize HTML payloads and isolate trusted rendering boundaries.",
    },
    {
        "id": "node-child-process",
        "title": "child_process exec usage",
        "severity": "high",
        "languages": {"javascript", "typescript", "tsx", "jsx"},
        "regex": re.compile(r"\bchild_process\.(?:exec|execSync)\s*\("),
        "recommendation": "Prefer spawn with fixed arguments and avoid shell interpolation.",
    },
    {
        "id": "node-weak-crypto",
        "title": "Weak crypto API createCipher",
        "severity": "medium",
        "languages": {"javascript", "typescript", "tsx", "jsx"},
        "regex": re.compile(r"\bcrypto\.createCipher\s*\("),
        "recommendation": "Use createCipheriv with authenticated modern algorithms.",
    },
    {
        "id": "go-exec-command",
        "title": "Go exec.Command invocation",
        "severity": "high",
        "languages": {"go"},
        "regex": re.compile(r"\bexec\.Command\s*\("),
        "recommendation": "Validate command arguments and avoid forwarding unsanitized user input.",
    },
    {
        "id": "java-runtime-exec",
        "title": "Runtime.exec invocation",
        "severity": "high",
        "languages": {"java"},
        "regex": re.compile(r"\bRuntime\.getRuntime\(\)\.exec\s*\("),
        "recommendation": "Avoid direct process execution from request-facing code paths.",
    },
    {
        "id": "ruby-eval-system",
        "title": "Ruby eval/system usage",
        "severity": "high",
        "languages": {"ruby"},
        "regex": re.compile(r"\b(?:eval|system)\s*\("),
        "recommendation": "Avoid dynamic evaluation and shell execution with external input.",
    },
)


def _is_comment_line(line: str, language: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if language in {"python", "ruby"}:
        return stripped.startswith("#")
    if language in {"javascript", "typescript", "tsx", "jsx", "java", "go", "php"}:
        return stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")
    return False


def _load_symbol_spans_by_file(conn) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.qualified_name,
            s.line_start,
            s.line_end,
            f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.kind IN ('function', 'method', 'constructor')
        ORDER BY f.path, s.line_start
        """
    ).fetchall()
    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_file[r["path"]].append(
            {
                "id": r["id"],
                "name": r["name"],
                "qualified_name": r["qualified_name"],
                "line_start": int(r["line_start"] or 0),
                "line_end": int(r["line_end"] or r["line_start"] or 0),
            }
        )
    return by_file


def _find_symbol_for_line(spans: list[dict], line_no: int) -> dict | None:
    best = None
    best_span = None
    for span in spans:
        ls = int(span.get("line_start") or 0)
        le = int(span.get("line_end") or ls)
        if ls <= 0:
            continue
        if ls <= line_no <= max(le, ls):
            width = max(le, ls) - ls
            if best is None or (best_span is not None and width < best_span):
                best = span
                best_span = width
    return best


def _compute_entrypoint_distances(conn) -> tuple[dict[int, int], int]:
    edges = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
    adj: dict[int, set[int]] = defaultdict(set)
    for row in edges:
        src = int(row["source_id"])
        tgt = int(row["target_id"])
        adj[src].add(tgt)

    sym_rows = conn.execute(
        """
        SELECT id, name, kind, is_exported
        FROM symbols
        WHERE kind IN ('function', 'method', 'constructor', 'class')
        """
    ).fetchall()

    entries: set[int] = set()
    for row in sym_rows:
        name = row["name"] or ""
        is_exported = int(row["is_exported"] or 0) == 1
        if is_exported or _ENTRYPOINT_HINT.search(name):
            entries.add(int(row["id"]))

    if not entries and sym_rows:
        entries = {int(r["id"]) for r in sym_rows[:25]}

    distance: dict[int, int] = {}
    q: deque[int] = deque()
    for sid in entries:
        distance[sid] = 0
        q.append(sid)

    while q:
        current = q.popleft()
        next_depth = distance[current] + 1
        for nxt in adj.get(current, ()):
            if nxt in distance:
                continue
            distance[nxt] = next_depth
            q.append(nxt)

    return distance, len(entries)


def _security_sinks_for_language(language: str) -> list[dict]:
    lang = (language or "").lower()
    return [sink for sink in _SECURITY_SINKS if lang in sink["languages"]]


def _compute_security_hotspots(conn) -> dict:
    project_root = find_project_root()
    spans_by_file = _load_symbol_spans_by_file(conn)
    entry_distances, entry_count = _compute_entrypoint_distances(conn)

    file_rows = conn.execute(
        """
        SELECT path, COALESCE(language, '') AS language
        FROM files
        WHERE COALESCE(file_role, 'source') = 'source'
        ORDER BY path
        """
    ).fetchall()

    hits: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for row in file_rows:
        rel_path = row["path"]
        language = (row["language"] or "").lower()
        sinks = _security_sinks_for_language(language)
        if not sinks:
            continue

        full_path = project_root / rel_path
        try:
            lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        spans = spans_by_file.get(rel_path, [])
        for i, line in enumerate(lines, start=1):
            if _is_comment_line(line, language):
                continue
            for sink in sinks:
                if sink["regex"].search(line) is None:
                    continue
                dedupe_key = (rel_path, i, sink["id"])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                symbol = _find_symbol_for_line(spans, i)
                hits.append(
                    {
                        "file": rel_path,
                        "line": i,
                        "language": language,
                        "pattern_id": sink["id"],
                        "title": sink["title"],
                        "severity": sink["severity"],
                        "recommendation": sink["recommendation"],
                        "symbol_id": symbol["id"] if symbol else None,
                        "symbol": (symbol["qualified_name"] or symbol["name"] if symbol else None),
                        "code": line.strip()[:160],
                    }
                )

    symbol_ids = [h["symbol_id"] for h in hits if h["symbol_id"] is not None]
    symbol_ids = sorted(set(symbol_ids))
    pagerank_by_symbol: dict[int, float] = {}
    if symbol_ids:
        rows = batched_in(
            conn,
            "SELECT symbol_id, pagerank FROM graph_metrics WHERE symbol_id IN ({ph})",
            symbol_ids,
        )
        for r in rows:
            pagerank_by_symbol[int(r["symbol_id"])] = float(r["pagerank"] or 0.0)

    base_score = {"critical": 82, "high": 68, "medium": 52}
    for hit in hits:
        sid = hit["symbol_id"]
        hops = entry_distances.get(sid) if sid is not None else None
        reachable = hops is not None
        pagerank = pagerank_by_symbol.get(sid, 0.0) if sid is not None else 0.0

        score = base_score.get(hit["severity"], 50)
        if reachable:
            score += 12
        if hops is not None and hops <= 2:
            score += 4
        if pagerank >= 0.01:
            score += 4
        if pagerank >= 0.03:
            score += 3

        hit["reachable_from_entrypoint"] = bool(reachable)
        hit["hops_from_entrypoint"] = int(hops) if hops is not None else None
        hit["pagerank"] = round(float(pagerank), 6)
        hit["risk_score"] = min(99, int(score))

    hits.sort(
        key=lambda h: (
            -severity_rank(h["severity"]),
            0 if h["reachable_from_entrypoint"] else 1,
            -h["risk_score"],
            h["file"],
            h["line"],
            h["pattern_id"],
        )
    )

    return {
        "total": len(hits),
        "reachable": sum(1 for h in hits if h["reachable_from_entrypoint"]),
        "critical": sum(1 for h in hits if h["severity"] == "critical"),
        "high": sum(1 for h in hits if h["severity"] == "high"),
        "medium": sum(1 for h in hits if h["severity"] == "medium"),
        "entrypoints": entry_count,
        "files_scanned": len(file_rows),
        "hotspots": hits,
    }


def _run_danger_mode(json_mode: bool, token_budget: int) -> None:
    """files in the intersection of high churn × complexity × fan-in.

    Computes the 75th-percentile threshold for each metric, then lists
    files above all three thresholds. The score is the geometric mean
    of the metric ratios so a moderate-everywhere file ranks above one
    that's extreme in only one dimension.
    """
    import math as _math

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT f.path, f.id AS file_id,
                   COALESCE(fs.total_churn, 0) AS churn,
                   COALESCE(fs.complexity, 0) AS complexity,
                   (SELECT COALESCE(MAX(gm.in_degree), 0)
                      FROM symbols s
                      JOIN graph_metrics gm ON gm.symbol_id = s.id
                     WHERE s.file_id = f.id) AS max_fan_in
              FROM files f
              LEFT JOIN file_stats fs ON fs.file_id = f.id
             WHERE COALESCE(f.file_role, 'source') = 'source'
            """
        ).fetchall()

    candidates = [
        {
            "path": r["path"],
            "churn": r["churn"] or 0,
            "complexity": r["complexity"] or 0.0,
            "max_fan_in": r["max_fan_in"] or 0,
        }
        for r in rows
    ]

    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        k = max(0, min(len(sorted_v) - 1, int(len(sorted_v) * pct) - 1))
        return float(sorted_v[k])

    p75_churn = _percentile([c["churn"] for c in candidates], 0.75)
    p75_complex = _percentile([c["complexity"] for c in candidates], 0.75)
    p75_fanin = _percentile([c["max_fan_in"] for c in candidates], 0.75)

    danger = []
    for c in candidates:
        if c["churn"] >= p75_churn > 0 and c["complexity"] >= p75_complex > 0 and c["max_fan_in"] >= p75_fanin > 0:
            ratio_churn = c["churn"] / p75_churn if p75_churn else 1.0
            ratio_complex = c["complexity"] / p75_complex if p75_complex else 1.0
            ratio_fanin = c["max_fan_in"] / p75_fanin if p75_fanin else 1.0
            score = _math.exp((_math.log(ratio_churn) + _math.log(ratio_complex) + _math.log(ratio_fanin)) / 3.0)
            danger.append({**c, "danger_score": round(score, 3)})

    danger.sort(key=lambda d: d["danger_score"], reverse=True)
    verdict = (
        f"{len(danger)} file(s) in the danger zone "
        f"(p75 churn≥{int(p75_churn)}, complexity≥{p75_complex:.1f}, fan-in≥{int(p75_fanin)})"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "hotspots",
                    budget=token_budget,
                    summary={"verdict": verdict, "count": len(danger)},
                    thresholds={
                        "churn_p75": p75_churn,
                        "complexity_p75": p75_complex,
                        "fan_in_p75": p75_fanin,
                    },
                    danger_zone=danger[:50],
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if not danger:
        click.echo("No files cross all three p75 thresholds.")
        return
    click.echo(f"{'Path':<60}  {'Churn':>5}  {'Cx':>6}  {'FanIn':>5}  {'Score':>5}")
    click.echo(f"{'-' * 60}  {'-' * 5}  {'-' * 6}  {'-' * 5}  {'-' * 5}")
    for d in danger[:25]:
        path = d["path"][:60]
        click.echo(
            f"{path:<60}  {d['churn']:>5}  {d['complexity']:>6.2f}  {d['max_fan_in']:>5}  {d['danger_score']:>5.2f}"
        )


def _hotspots_finding_id(symbol_id: int, classification: str) -> str:
    """Stable, deterministic finding id for one runtime hotspot.

    The (symbol_id, classification) pair re-identifies the same hotspot
    across runs — symbols.id stays stable across re-indexes of unchanged
    code, and the classification disambiguates the same symbol surfacing
    under a different bucket on a fresh trace ingestion (e.g. a symbol
    that moves from CONFIRMED to UPGRADE as static metrics drift). A
    symbol that switches buckets produces a fresh row rather than
    upserting the old one — agents querying by classification see the
    current bucket and the previous row stays available for audit.
    """
    raw = f"{symbol_id}:{classification}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"hotspots:{classification.lower()}:{digest}"


def _emit_hotspots_findings(conn, hotspots_data: list[dict], source_version: str) -> None:
    """Mirror each runtime hotspot row into the findings registry.

    ``hotspots_data`` is the list of dicts returned by
    :func:`roam.runtime.hotspots.compute_hotspots` — each entry already
    carries ``symbol_id``, ``classification``, ``static_rank``,
    ``runtime_rank``, and the nested ``runtime_stats`` / ``static_stats``
    blocks. We emit one finding per symbol; symbols without an indexed
    ``symbol_id`` (trace data that didn't match a known symbol) are
    skipped — there's no stable subject to attach the finding to.

    Confidence tier is always ``runtime``. All three classifications
    (UPGRADE / CONFIRMED / DOWNGRADE) require ingested ``runtime_stats``
    rows; the detector cannot produce findings without real trace data.
    The classification distinguishes WHICH runtime story the symbol
    tells, but the evidence base is the same — runtime observation.

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard read path.
    """
    from roam.db.findings import CONFIDENCE_RUNTIME, FindingRecord, emit_finding

    for h in hotspots_data:
        symbol_id = h.get("symbol_id")
        if symbol_id is None:
            # Trace span didn't resolve to an indexed symbol — there's
            # no stable subject to attach to. Skip rather than emitting
            # a subject_id=NULL row that can't be joined back to the
            # codebase.
            continue
        classification = h.get("classification") or "CONFIRMED"
        symbol_name = h.get("symbol_name") or ""
        file_path = h.get("file_path") or ""
        runtime_rank = int(h.get("runtime_rank") or 0)
        static_rank = int(h.get("static_rank") or 0)
        runtime_stats = h.get("runtime_stats") or {}
        static_stats = h.get("static_stats") or {}

        finding_id = _hotspots_finding_id(int(symbol_id), classification)
        evidence = {
            "symbol_name": symbol_name,
            "file_path": file_path,
            "classification": classification,
            "static_rank": static_rank,
            "runtime_rank": runtime_rank,
            "runtime_stats": {
                "call_count": runtime_stats.get("call_count"),
                "p50_latency_ms": runtime_stats.get("p50_latency_ms"),
                "p99_latency_ms": runtime_stats.get("p99_latency_ms"),
                "error_rate": runtime_stats.get("error_rate"),
            },
            "static_stats": {
                "pagerank": static_stats.get("pagerank"),
                "complexity": static_stats.get("complexity"),
                "churn": static_stats.get("churn"),
            },
        }
        # Flag DOWNGRADE rows with an explicit disagreement note so an
        # agent filtering by ``classification == "DOWNGRADE"`` doesn't
        # have to reason about the static-vs-runtime delta from
        # rank numbers alone (W120 disagree-with-static flag).
        if classification == "DOWNGRADE":
            evidence["disagreement"] = "static rank ranked this symbol high but runtime traffic is low"
        elif classification == "UPGRADE":
            evidence["disagreement"] = "runtime traffic ranks this symbol high but static analysis missed it"

        call_count = runtime_stats.get("call_count") or 0
        p99 = runtime_stats.get("p99_latency_ms")
        p99_str = f", p99={p99:.0f}ms" if p99 is not None else ""
        claim = (
            f"Runtime hotspot ({classification}): {symbol_name} "
            f"({file_path}) — runtime_rank={runtime_rank}, "
            f"static_rank={static_rank}, calls={call_count}{p99_str}"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol",
                subject_id=int(symbol_id),
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                # All three classifications require ingested
                # ``runtime_stats`` rows — observed in production.
                # ``runtime`` is the correct tier across the board.
                confidence=CONFIDENCE_RUNTIME,
                source_detector="hotspots",
                source_version=source_version,
            ),
        )


@roam_capability(
    name="hotspots",
    category="health",
    summary="Show runtime hotspots comparing static analysis vs runtime data",
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
@click.option("--runtime", "sort_runtime", is_flag=True, help="Sort by runtime metrics")
@click.option("--discrepancy", is_flag=True, help="Only show static/runtime mismatches")
@click.option(
    "--security",
    "security_mode",
    is_flag=True,
    help="Detect security hotspots (dangerous APIs) with entry-point reachability",
)
@click.option(
    "--danger",
    "danger_mode",
    is_flag=True,
    help="Files in top quartile of churn × complexity × max-fan-in ('danger zone')",
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror runtime hotspots into the central findings registry — "
        "visible via ``roam findings list --detector hotspots``. The "
        "detector-specific output is unchanged; the registry rows are "
        "the denormalised cross-detector surface. Persists only in the "
        "default (runtime) mode — --security and --danger are separate "
        "modes that are not yet migrated. Persisting skips rows whose "
        "trace span didn't resolve to an indexed symbol; the standard "
        "JSON / text output is unaffected."
    ),
)
@click.pass_context
def hotspots(ctx, sort_runtime, discrepancy, security_mode, danger_mode, persist):
    """Show runtime hotspots comparing static analysis vs runtime data.

    Requires prior trace ingestion via ``roam ingest-trace``.

    Unlike ``smells`` (which detects structural complexity anti-patterns from
    DB queries), this command has two modes: ``--security`` scans source files
    for dangerous API patterns (eval, pickle, subprocess) with reachability
    scoring, and the default mode correlates static analysis with runtime
    trace data from ``ingest-trace``.

    \b
    Classifications:
      UPGRADE   — runtime-critical but statically safe (hidden hotspot)
      CONFIRMED — both static and runtime agree on importance
      DOWNGRADE — statically risky but low traffic

    \b
    Examples:
      roam hotspots
      roam hotspots --runtime
      roam hotspots --discrepancy
      roam hotspots --security

    See also ``ingest-trace`` (load runtime traces first), ``smells``
    (structural anti-patterns), and ``complexity`` (cognitive metrics).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    ensure_index()

    if security_mode and (sort_runtime or discrepancy):
        click.echo("Cannot combine --security with --runtime or --discrepancy")
        raise SystemExit(1)
    if danger_mode and (sort_runtime or discrepancy or security_mode):
        click.echo("Cannot combine --danger with --runtime, --discrepancy, or --security")
        raise SystemExit(1)
    # --persist mirrors the runtime hotspot view into findings; the
    # --security and --danger modes have their own subject surfaces
    # (raw file/line for security, file-level danger score) and are
    # not yet migrated to the central registry.
    if persist and (security_mode or danger_mode):
        click.echo(
            "--persist applies to the default (runtime) hotspots mode only; not supported with --security or --danger"
        )
        raise SystemExit(1)
    # W1210: SARIF emission mirrors the --persist discipline — only the
    # default (runtime) mode projects onto the hotspots/* closed-enum
    # rule catalogue. --security findings live at raw file/line with
    # their own severity vocabulary (CRITICAL/HIGH/MEDIUM + reach
    # status); --danger findings are file-level p75-band aggregates with
    # a single danger_score. Both would dilute the hotspots-detector
    # rule namespace if collapsed onto it. Fail loudly rather than emit
    # an unrelated SARIF surface (closed-enum discipline per CLAUDE.md
    # Constraint 8).
    if sarif_mode and (security_mode or danger_mode):
        click.echo(
            "--sarif applies to the default (runtime) hotspots mode only; not supported with --security or --danger"
        )
        raise SystemExit(1)

    if danger_mode:
        _run_danger_mode(json_mode, token_budget)
        return

    if security_mode:
        with open_db(readonly=True) as conn:
            report = _compute_security_hotspots(conn)

        total = report["total"]
        reachable = report["reachable"]
        verdict = (
            "No security hotspots detected"
            if total == 0
            else f"{total} security hotspots ({reachable} reachable from entry points)"
        )

        if json_mode:
            # W21.7 LAW 4: when nothing crossed the threshold, the auto-derive
            # would emit ``"0 total findings"`` + ``"0 reachable findings"``
            # etc. — pure noise. Pin an explicit healthy-codebase fact.
            envelope_kwargs = dict(
                budget=token_budget,
                summary={
                    "verdict": verdict,
                    "mode": "security",
                    "total": total,
                    "reachable": reachable,
                    "critical": report["critical"],
                    "high": report["high"],
                    "medium": report["medium"],
                },
                mode="security",
                signals={
                    "entrypoints": report["entrypoints"],
                    "files_scanned": report["files_scanned"],
                },
                hotspots=report["hotspots"],
            )
            if total == 0:
                envelope_kwargs["agent_contract"] = {
                    "facts": [
                        verdict,
                        "no security hotspots above threshold; codebase is healthy",
                    ],
                }
            envelope = json_envelope("hotspots", **envelope_kwargs)
            if not detail:
                envelope = strip_list_payloads(envelope)
            click.echo(to_json(envelope))
            return

        click.echo(f"VERDICT: {verdict}")
        click.echo()
        if not report["hotspots"]:
            return

        shown = report["hotspots"] if detail else report["hotspots"][:10]
        if not detail:
            click.echo(
                "Top security hotspots (showing {} of {}, run `roam --detail hotspots --security` for the full list):".format(
                    len(shown),
                    total,
                )
            )

        for item in shown:
            reach = "REACH" if item["reachable_from_entrypoint"] else "LOCAL"
            symbol = item["symbol"] or "<module>"
            click.echo(
                "  [{:<8}] [{}] {}:{} {} -- {}".format(
                    item["severity"].upper(),
                    reach,
                    item["file"],
                    item["line"],
                    symbol,
                    item["title"],
                )
            )
            if detail:
                if item["code"]:
                    click.echo(f"    code: {item['code']}")
                if item["reachable_from_entrypoint"]:
                    click.echo(f"    entrypoint distance: {item['hops_from_entrypoint']} hop(s)")
                click.echo(f"    recommendation: {item['recommendation']}")
                click.echo()
        return

    from roam.runtime.hotspots import compute_hotspots

    with open_db(readonly=not persist) as conn:
        # Ensure table exists for query even in readonly mode
        try:
            conn.execute("SELECT COUNT(*) FROM runtime_stats")
        except Exception:
            if sarif_mode:
                # No runtime data → emit a valid SARIF doc with zero
                # results so a CI gate consumer sees the rules catalogue
                # even on a clean / no-trace-data run. Mirrors the
                # cmd_bus_factor / cmd_over_fetch empty-input contract.
                from roam.output.sarif import hotspots_to_sarif, write_sarif

                click.echo(write_sarif(hotspots_to_sarif([])))
                return
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "hotspots",
                            budget=token_budget,
                            summary={
                                "verdict": "No runtime data. Run `roam ingest-trace` first.",
                                "total": 0,
                                "upgrades": 0,
                                "confirmed": 0,
                                "downgrades": 0,
                            },
                            hotspots=[],
                        )
                    )
                )
            else:
                click.echo("VERDICT: No runtime data. Run `roam ingest-trace` first.")
            return

        items = compute_hotspots(conn)

        # --- W120: mirror runtime hotspots into the central findings
        # registry. Runs ONLY with --persist. The persisted set is the
        # full compute_hotspots return — independent of the
        # --discrepancy / --runtime display filters below, so re-running
        # with a different filter doesn't truncate the registry.
        if persist and items:
            try:
                _emit_hotspots_findings(conn, items, HOTSPOTS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

    # W1210: SARIF branch — mirrors the --persist discipline. The
    # projection set is the FULL compute_hotspots return (the
    # unfiltered classification ladder), independent of the
    # --discrepancy / --runtime display filters below. A CI consumer
    # gating off SARIF should see the same closed-enum rule catalogue
    # regardless of which display flag the caller used.
    if sarif_mode:
        from roam.output.sarif import hotspots_to_sarif, write_sarif

        click.echo(write_sarif(hotspots_to_sarif(items)))
        return

    if discrepancy:
        items = [h for h in items if h["classification"] in ("UPGRADE", "DOWNGRADE")]

    if sort_runtime:
        items.sort(key=lambda h: h["runtime_rank"])

    total = len(items)
    upgrades = sum(1 for h in items if h["classification"] == "UPGRADE")
    confirmed = sum(1 for h in items if h["classification"] == "CONFIRMED")
    downgrades = sum(1 for h in items if h["classification"] == "DOWNGRADE")

    hidden = upgrades
    verdict = f"{total} runtime hotspots ({hidden} hidden -- static analysis missed them)"

    if json_mode:
        next_steps = suggest_next_steps(
            "hotspots",
            {
                "upgrades": upgrades,
                "total": total,
            },
        )
        # W21.7 LAW 4: explicit-facts when the runtime view is empty so we
        # don't ship ``"0 total findings"`` / ``"0 upgrades findings"``.
        envelope_extra = {}
        if total == 0:
            envelope_extra["agent_contract"] = {
                "facts": [
                    verdict,
                    "no runtime hotspots to report; ingest traces or rerun against a populated dataset",
                ],
            }
        envelope = json_envelope(
            "hotspots",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "total": total,
                "upgrades": upgrades,
                "confirmed": confirmed,
                "downgrades": downgrades,
            },
            **envelope_extra,
            hotspots=[
                {
                    "symbol": h["symbol_name"],
                    "file": h["file_path"],
                    "static_rank": h["static_rank"],
                    "runtime_rank": h["runtime_rank"],
                    "classification": h["classification"],
                    "importance": round(h["static_stats"].get("pagerank", 0.0), 6),
                    "stats": {
                        "runtime": h["runtime_stats"],
                        "static": h["static_stats"],
                    },
                }
                for h in items
            ],
            next_steps=next_steps,
        )
        if not detail:
            envelope = strip_list_payloads(envelope)
        click.echo(to_json(envelope))
        return

    # Text output
    click.echo(f"VERDICT: {verdict}\n")

    if not items:
        click.echo("  (no runtime data ingested)")
        ns_text = format_next_steps_text(
            suggest_next_steps(
                "hotspots",
                {
                    "upgrades": upgrades,
                    "total": total,
                },
            )
        )
        if ns_text:
            click.echo(ns_text)
        return

    # Summary mode: show top 5 hotspots only
    if not detail:
        click.echo(f"Top hotspots (showing 5 of {total}, run `roam --detail hotspots` for the full list):")
        for h in items[:5]:
            file_str = h["file_path"] or "-"
            symbol_loc = f"{file_str}::{h['symbol_name']}" if file_str != "-" else h["symbol_name"]
            click.echo(f"  [{h['classification']}] {symbol_loc}")
        return

    for h in items:
        rs = h["runtime_stats"]
        ss = h["static_stats"]
        file_str = h["file_path"] or "-"
        symbol_loc = f"{file_str}::{h['symbol_name']}" if file_str != "-" else h["symbol_name"]

        click.echo(f"  {symbol_loc}")
        click.echo(
            f"    Static:  churn={ss['churn']}, CC={ss['complexity']}, "
            f"PageRank={ss['pagerank']:.4f}  -- ranked #{h['static_rank']}"
        )

        calls_str = f"{rs['call_count']}"
        if rs["call_count"] >= 1000:
            calls_str = (
                f"{rs['call_count'] / 1000:.0f}K"
                if rs["call_count"] < 1_000_000
                else f"{rs['call_count'] / 1_000_000:.1f}M"
            )

        p99_str = f"p99={rs['p99_latency_ms']:.0f}ms" if rs["p99_latency_ms"] is not None else "p99=n/a"
        err_str = f"err={rs['error_rate'] * 100:.1f}%" if rs["error_rate"] else "err=0%"

        click.echo(f"    Runtime: {calls_str} calls, {p99_str}, {err_str} -- ranked #{h['runtime_rank']}")
        click.echo(f"    >> {h['classification']}")
        click.echo()

    next_steps = suggest_next_steps(
        "hotspots",
        {
            "upgrades": upgrades,
            "total": total,
        },
    )
    ns_text = format_next_steps_text(next_steps)
    if ns_text:
        click.echo(ns_text)
