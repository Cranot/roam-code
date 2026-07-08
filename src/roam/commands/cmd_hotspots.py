"""Show runtime or security hotspots."""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
from collections import defaultdict, deque

import click

from roam.capability import roam_capability
from roam.commands.boundary_helpers import make_run_check
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


# `py-eval-exec` shares the dangerous-eval FP shape (detectors.py:3520): a
# dotted `<receiver>.exec(` is the safe regex/method API, not a code-exec
# sink, and a `def exec(...)` declaration line is a definition, not a call.
# Mirror the two guards from `detect_dangerous_eval` so the sibling detector
# does not re-introduce the union FP.
_RE_PY_EVAL_DECL_LINE = re.compile(r"^\s*(?:async\s+)?def\s+(?:eval|exec)\b")
_RE_PY_SHELL_EXEC_RECEIVER = re.compile(r"(?:child_process|cp)\s*\.\s*exec", re.IGNORECASE)


def _is_eval_exec_false_positive(line: str) -> bool:
    """True when a `py-eval-exec` regex hit is a safe `.exec(` / decl-line FP."""
    # `<receiver>.exec(` is the standard method/regex API — suppress unless the
    # receiver is a genuine shell-exec sink (`child_process.exec`, `cp.exec`).
    if ".exec(" in line and not _RE_PY_SHELL_EXEC_RECEIVER.search(line):
        return True
    # `def exec(...)` defines a sink-named wrapper; it is not a call site.
    if _RE_PY_EVAL_DECL_LINE.match(line):
        return True
    return False


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
                if sink["id"] == "py-eval-exec" and _is_eval_exec_false_positive(line):
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

    # W607-CP -- substrate-boundary plumbing for cmd_hotspots.
    # ``_run_check_cp`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cp_warnings_out`` rather than
    # crashing the runtime-hotspot detector outright (W120 origin per
    # CLAUDE.md detector roster -- part of the original 16 findings-
    # registry detectors; W816 / W819 sealed the empty-corpus smoke
    # gap, but until this wave the command had no substrate-boundary
    # marker plumbing -- a raise in ``compute_hotspots`` would crash
    # the hotspots command outright). Marker family
    # ``hotspots_<phase>_failed:<exc_class>:<detail>``. Substrates wrapped:
    #
    #   * load_trace_ingestion       -- runtime_stats COUNT probe
    #   * compute_hotspots           -- core static-vs-runtime ranking
    #                                   (UPGRADE/CONFIRMED/DOWNGRADE
    #                                   classification)
    #   * compute_security_hotspots  -- --security mode source-scan
    #   * run_danger_mode            -- --danger mode p75 aggregator
    #   * emit_findings              -- W120 findings-registry mirror
    #                                   (sqlite3.OperationalError silent
    #                                   no-op preserved for pre-W89 DB)
    #   * serialize_to_sarif         -- SARIF projection
    #   * apply_discrepancy_filter   -- UPGRADE/DOWNGRADE filter
    #   * apply_runtime_sort         -- runtime_rank sort
    #   * aggregate_by_kind          -- UPGRADE/CONFIRMED/DOWNGRADE counts
    #   * derive_next_steps          -- suggest_next_steps wrap
    _w607cp_warnings_out: list[str] = []

    def _run_check_cp(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CP marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``hotspots_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607cp_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        return make_run_check("hotspots", _w607cp_warnings_out)(phase, fn, *args, default=default, **kwargs)

    # W607-EN -- AGGREGATION-layer plumbing on top of W607-CP substrate
    # layer. The two buckets are disjoint and merged at envelope-emit
    # time so consumers see the full degradation lineage. The phase names
    # (score_classify / compute_predicate / compute_verdict /
    # serialize_envelope) are disjoint from the W607-CP substrate phases
    # above (load_trace_ingestion / compute_hotspots /
    # compute_security_hotspots / run_danger_mode / emit_findings /
    # serialize_to_sarif / apply_discrepancy_filter / apply_runtime_sort
    # / aggregate_by_kind / derive_next_steps / serialize_items). Marker
    # family ``hotspots_<phase>_failed:<exc_class>:<detail>`` is shared so
    # any consumer regex over the marker family catches both layers.
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_en(...)`` call:
    #   1. f-string verdict floor: NEVER re-interpolate the same values
    #      that tripped the closure inside the ``default=`` floor.
    #   2. kwarg-default eagerness: ``default=`` must be a literal
    #      constant, never a computed expression.
    #   3. json.dumps(default=str) sentinel: the serialize_envelope
    #      floor must be JSON-serializable with the standard encoder.
    #   4. phase-name collision: verified above against CP's 11 phases.
    #   5. len() at kwarg-bind: move len() INSIDE the closure, never at
    #      the ``_run_check_en(...)`` call site.
    #   6. unguarded len()/if on poisoned object: the floor MUST be a
    #      concrete dict/str/None, never a sentinel that may
    #      __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    #
    # Aggregation phases wrapped (sibling pattern to cmd_bus_factor's
    # W607-EH + cmd_auth_gaps's W607-ED + cmd_missing_index's W607-DX):
    #
    #   * score_classify     -- buckets the run by hotspot count into
    #                          COLD (0) / WARM (1..4) / HOT (>=5) /
    #                          DEGRADED
    #   * compute_predicate  -- rollup metrics dict (risk_score sum, heat
    #                          band, commit_count, hottest_files)
    #   * compute_verdict    -- single-line verdict string (LAW 6 floor:
    #                          "hotspots completed")
    #   * serialize_envelope -- json_envelope("hotspots", ...) projection
    _w607en_warnings_out: list[str] = []

    def _run_check_en(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-EN marker emission.

        Mirror of ``_run_check_cp`` shape (same
        ``hotspots_<phase>_failed:`` marker family) but writes into
        ``_w607en_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits. W607-DW finding pin: the
        shared helper passes the supplied *default* through unchanged so
        the floor remains a literal pass-through.
        """
        try:
            return make_run_check("hotspots", _w607en_warnings_out)(phase, fn, *args, default=default, **kwargs)
        except Exception:  # noqa: BLE001 -- safety-net fallback; helper already discloses
            return default

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
        # W607-CP: ``run_danger_mode`` substrate -- a raise in the
        # p75-band aggregator used to crash the --danger CI path; the
        # wrap contains it. ``--danger`` writes its own envelope inside
        # the helper so we cannot mirror markers into a downstream
        # envelope here; the marker stays on the accumulator for
        # source-grep parity.
        _run_check_cp(
            "run_danger_mode",
            _run_danger_mode,
            json_mode,
            token_budget,
            default=None,
        )
        return

    if security_mode:
        with open_db(readonly=True) as conn:
            # W607-CP: ``compute_security_hotspots`` substrate -- a raise
            # inside the source-scan regex loop used to crash the
            # --security path; degrades to an empty report so the
            # envelope still emits with zero hotspots and the marker
            # surfaces.
            report = _run_check_cp(
                "compute_security_hotspots",
                _compute_security_hotspots,
                conn,
                default={
                    "total": 0,
                    "reachable": 0,
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "entrypoints": 0,
                    "files_scanned": 0,
                    "hotspots": [],
                },
            )
            if report is None:
                report = {
                    "total": 0,
                    "reachable": 0,
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "entrypoints": 0,
                    "files_scanned": 0,
                    "hotspots": [],
                }

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
            # W607-CP: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. The security path is invocation-scoped so a
            # marker need not flip partial_success here (no degraded
            # SAFE -- a raise yields the empty-floor report with zero
            # hotspots, which is honest given the substrate failure).
            if _w607cp_warnings_out:
                envelope_kwargs["summary"]["warnings_out"] = list(_w607cp_warnings_out)
                envelope_kwargs["summary"]["partial_success"] = True
                envelope_kwargs["warnings_out"] = list(_w607cp_warnings_out)
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

    # W607-CP: import via module reference so test monkeypatches on
    # ``roam.runtime.hotspots.compute_hotspots`` take effect through
    # the attribute lookup at call time (rather than capturing the
    # original at function-entry binding).
    from roam.runtime import hotspots as _runtime_hotspots_mod

    with open_db(readonly=not persist) as conn:
        # Detect "no runtime data" state — collapses two prior branches
        # into one: (a) ``runtime_stats`` table missing (pre-W21 schema)
        # AND (b) table exists but is empty (the common case after
        # ``roam init`` since the table is part of base schema). Both
        # mean the same thing to a caller: traces have never been
        # ingested. Pattern 2 — never emit a silent SAFE / 0-hotspots
        # verdict on uninitialised runtime data. ``runtime_state``
        # disambiguates which sub-state the caller is in.
        # W607-CP: ``load_trace_ingestion`` substrate. The
        # sqlite3.OperationalError path (table missing -- pre-W21
        # schema) is the EXPECTED degraded path and resolves to the
        # named ``table_missing`` state; no W607-CP marker fires (we
        # preserve the silent-named-state contract analogous to
        # cmd_n1's emit_findings handling of pre-W89 schemas). Any
        # OTHER raise inside the probe (e.g., malformed row) surfaces
        # via the W607-CP marker AND degrades to ``table_missing``
        # so the named-state path is taken.
        def _probe_runtime_state():
            row = conn.execute("SELECT COUNT(*) FROM runtime_stats").fetchone()
            if row is None or int(row[0] or 0) == 0:
                return "no_traces"
            return "ready"

        try:
            runtime_state = _probe_runtime_state()
        except sqlite3.OperationalError:
            # Expected: pre-W21 schema, table missing. Silent named state.
            runtime_state = "table_missing"
        except Exception as _probe_exc:  # noqa: BLE001 -- W607-CP disclosure
            _w607cp_warnings_out.append(
                f"hotspots_load_trace_ingestion_failed:{type(_probe_exc).__name__}:{_probe_exc}"
            )
            runtime_state = "table_missing"

        if runtime_state != "ready":
            if sarif_mode:
                # No runtime data → emit a valid SARIF doc with zero
                # results so a CI gate consumer sees the rules catalogue
                # even on a clean / no-trace-data run. Mirrors the
                # cmd_bus_factor / cmd_over_fetch empty-input contract.
                from roam.output.sarif import hotspots_to_sarif, write_sarif

                click.echo(write_sarif(hotspots_to_sarif([])))
                return
            verdict_no_data = "No runtime data. Run `roam ingest-trace` first."
            # Preserve the legacy ``next_steps`` field alongside the
            # canonical ``agent_contract.next_commands`` so older test
            # contracts + integration consumers continue to work. The
            # two surfaces are intentionally lockstep: ``next_steps`` is
            # the legacy invocation-scoped suggestion list, while
            # ``agent_contract.next_commands`` is the LAW 2 imperative
            # surface that newer agents consume.
            # W607-CP: ``derive_next_steps`` substrate -- a raise in
            # suggest_next_steps on the no-data path degrades to an
            # empty list so the envelope still emits with the named
            # state, verdict, and partial_success flag.
            next_steps_legacy = _run_check_cp(
                "derive_next_steps",
                suggest_next_steps,
                "hotspots",
                {"total": 0, "upgrades": 0},
                default=[],
            )
            if next_steps_legacy is None:
                next_steps_legacy = []
            if json_mode:
                # W607-CP empty-state envelope: mirror substrate markers
                # into both surfaces; partial_success stays True
                # regardless (the no-data state is itself a degraded /
                # partial result per Pattern 2, independent of W607-CP
                # marker presence).
                empty_summary = {
                    "verdict": verdict_no_data,
                    "total": 0,
                    "upgrades": 0,
                    "confirmed": 0,
                    "downgrades": 0,
                    "partial_success": True,
                    "state": runtime_state,
                }
                empty_envelope_kwargs = dict(
                    budget=token_budget,
                    summary=empty_summary,
                    agent_contract={
                        "facts": [
                            verdict_no_data,
                            f"runtime_stats state: {runtime_state}; no traces ingested",
                        ],
                        "next_commands": [
                            "roam ingest-trace <trace-file>",
                        ],
                    },
                    hotspots=[],
                    next_steps=next_steps_legacy,
                )
                if _w607cp_warnings_out:
                    empty_summary["warnings_out"] = list(_w607cp_warnings_out)
                    empty_envelope_kwargs["warnings_out"] = list(_w607cp_warnings_out)
                click.echo(to_json(json_envelope("hotspots", **empty_envelope_kwargs)))
            else:
                click.echo(f"VERDICT: {verdict_no_data}")
                click.echo()
                click.echo(format_next_steps_text(next_steps_legacy))
            return

        # W607-CP: ``compute_hotspots`` substrate -- the core static-
        # vs-runtime classification. A raise inside the per-symbol
        # ranking loop used to crash the hotspots command outright;
        # now degrades to ``[]`` (no classifications) so the envelope
        # still composes with empty totals AND the marker surfaces.
        # This is the canonical "classify_hotspot" boundary -- the
        # UPGRADE/CONFIRMED/DOWNGRADE classification happens inside
        # compute_hotspots per symbol.
        def _call_compute_hotspots():
            return _runtime_hotspots_mod.compute_hotspots(conn)

        items = _run_check_cp("compute_hotspots", _call_compute_hotspots, default=[])
        if items is None:
            items = []

        # --- W120: mirror runtime hotspots into the central findings
        # registry. Runs ONLY with --persist. The persisted set is the
        # full compute_hotspots return — independent of the
        # --discrepancy / --runtime display filters below, so re-running
        # with a different filter doesn't truncate the registry.
        # W607-CP: ``emit_findings`` substrate boundary. The pre-W89
        # schema path (sqlite3.OperationalError on missing ``findings``
        # table) is the EXPECTED degraded path -- the try/except below
        # maintains the W120 silent no-op contract for that case.
        # Generic exceptions surface via the
        # ``hotspots_emit_findings_failed:<exc>:<detail>`` marker.
        if persist and items:
            try:
                _emit_hotspots_findings(conn, items, HOTSPOTS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_hotspots:emit_findings", _exc)
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-CP disclosure
                _w607cp_warnings_out.append(f"hotspots_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

    # W1210: SARIF branch — mirrors the --persist discipline. The
    # projection set is the FULL compute_hotspots return (the
    # unfiltered classification ladder), independent of the
    # --discrepancy / --runtime display filters below. A CI consumer
    # gating off SARIF should see the same closed-enum rule catalogue
    # regardless of which display flag the caller used.
    if sarif_mode:
        # W607-CP: ``serialize_to_sarif`` substrate -- a raise in the
        # SARIF writer used to crash the hotspots command on the CI
        # integration path; now degrades silently to None with a
        # marker, and the function returns early (matches pre-W607-CP
        # semantics that SARIF mode short-circuits).
        def _emit_sarif():
            # Import via module reference so test monkeypatches on
            # ``roam.output.sarif.hotspots_to_sarif`` take effect at
            # call time rather than at function-entry binding.
            from roam.output import sarif as _sarif_mod

            click.echo(_sarif_mod.write_sarif(_sarif_mod.hotspots_to_sarif(items)))

        _run_check_cp("serialize_to_sarif", _emit_sarif, default=None)
        return

    if discrepancy:
        # W607-CP: ``apply_discrepancy_filter`` substrate -- the filter
        # comprehension can raise on a malformed classification value;
        # the wrap degrades to the unfiltered items list so the envelope
        # still composes with the marker disclosed.
        def _apply_discrepancy():
            return [h for h in items if h["classification"] in ("UPGRADE", "DOWNGRADE")]

        _filtered = _run_check_cp("apply_discrepancy_filter", _apply_discrepancy, default=items)
        items = _filtered if _filtered is not None else items

    if sort_runtime:
        # W607-CP: ``apply_runtime_sort`` substrate -- the runtime_rank
        # sort can raise on a malformed/missing runtime_rank field; the
        # wrap degrades to the unsorted items list with the marker.
        def _apply_runtime_sort():
            items.sort(key=lambda h: h["runtime_rank"])
            return items

        _run_check_cp("apply_runtime_sort", _apply_runtime_sort, default=None)

    # W607-CP: ``aggregate_by_kind`` substrate -- the kind counters can
    # raise on a malformed classification value; the wrap degrades to
    # (0, 0, 0) so the envelope still composes with empty histograms.
    def _aggregate_by_kind():
        return (
            len(items),
            sum(1 for h in items if h["classification"] == "UPGRADE"),
            sum(1 for h in items if h["classification"] == "CONFIRMED"),
            sum(1 for h in items if h["classification"] == "DOWNGRADE"),
        )

    _agg = _run_check_cp("aggregate_by_kind", _aggregate_by_kind, default=(len(items), 0, 0, 0))
    if _agg is None:
        _agg = (len(items), 0, 0, 0)
    total, upgrades, confirmed, downgrades = _agg

    hidden = upgrades
    verdict = f"{total} runtime hotspots ({hidden} hidden -- static analysis missed them)"

    if json_mode:
        # W607-CP: ``derive_next_steps`` substrate on the populated
        # runtime path. A raise in suggest_next_steps degrades to an
        # empty list with the marker, and the envelope still composes.
        next_steps = _run_check_cp(
            "derive_next_steps",
            suggest_next_steps,
            "hotspots",
            {
                "upgrades": upgrades,
                "total": total,
            },
            default=[],
        )
        if next_steps is None:
            next_steps = []
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
        runtime_summary = {
            "verdict": verdict,
            "total": total,
            "upgrades": upgrades,
            "confirmed": confirmed,
            "downgrades": downgrades,
        }
        # W607-CP: mirror substrate markers into BOTH the top-level
        # envelope ``warnings_out`` AND ``summary.warnings_out`` so MCP
        # consumers see disclosure regardless of which surface they
        # read. A non-empty W607-CP bucket flips partial_success so a
        # degraded path is NOT mistaken for a clean populated run.
        if _w607cp_warnings_out:
            runtime_summary["warnings_out"] = list(_w607cp_warnings_out)
            runtime_summary["partial_success"] = True
        runtime_extra_top: dict = {}
        if _w607cp_warnings_out:
            runtime_extra_top["warnings_out"] = list(_w607cp_warnings_out)

        # W607-CP: ``serialize_items`` substrate -- the per-item dict
        # comprehension can raise on a malformed item (missing
        # classification / static_stats / etc.). Degrades to an empty
        # hotspot list so the envelope still composes with the marker.
        def _serialize_items():
            return [
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
            ]

        _serialized = _run_check_cp("serialize_items", _serialize_items, default=[])
        if _serialized is None:
            _serialized = []

        # W607-EN -- score_classify boundary. Buckets the run by total
        # hotspot count:
        #   * COLD     -- total == 0
        #   * WARM     -- 1..4 hotspots
        #   * HOT      -- >=5 hotspots
        #   * DEGRADED -- floor on raise
        # W978 5th-discipline: ``total`` passed as a raw arg; arithmetic
        # lives INSIDE the closure (no len() / no math at kwarg-bind).
        def _score_classify_run(_total):
            if _total == 0:
                _state_label = "COLD"
            elif _total < 5:
                _state_label = "WARM"
            else:
                _state_label = "HOT"
            return {"state": _state_label, "scanned": _total}

        _score_dict = _run_check_en(
            "score_classify",
            _score_classify_run,
            total,
            default={"state": "DEGRADED", "scanned": 0},
        )

        # W607-EN -- compute_predicate boundary. Rollup metrics dict
        # surfacing aggregate dimensions (risk_score sum, heat band,
        # commit_count, hottest_files) so a downstream refactor of the
        # rollup logic surfaces a marker rather than crashing.
        # W978 5th-discipline: ``items`` passed as a raw arg; counting
        # / iteration lives INSIDE the closure.
        def _compute_predicate_fields(_items):
            _heat = 0
            _commit_count = 0
            _hottest_files: list[dict] = []
            for _h in _items:
                _rs = _h.get("runtime_stats") or {}
                _ss = _h.get("static_stats") or {}
                _cc = _rs.get("call_count")
                if isinstance(_cc, int):
                    _heat += _cc
                _ch = _ss.get("churn")
                if isinstance(_ch, int):
                    _commit_count += _ch
            # hottest_files: top 3 by runtime_rank (lower rank == hotter)
            _ranked = sorted(
                _items,
                key=lambda x: x.get("runtime_rank") or 999999,
            )[:3]
            for _h in _ranked:
                _hottest_files.append(
                    {
                        "file": _h.get("file_path"),
                        "symbol": _h.get("symbol_name"),
                        "classification": _h.get("classification"),
                    }
                )
            return {
                "heat": _heat,
                "commit_count": _commit_count,
                "hottest_files": _hottest_files,
            }

        _pred_fields = _run_check_en(
            "compute_predicate",
            _compute_predicate_fields,
            items,
            default={
                "heat": 0,
                "commit_count": 0,
                "hottest_files": [],
            },
        )

        # W607-EN -- compute_verdict boundary. Wraps the verdict string
        # assembly so a downstream f-string refactor surfaces a marker
        # rather than crashing the envelope. Literal
        # "hotspots completed" floor (LAW 6 still holds: the line works
        # standalone).
        #
        # W978 1st-discipline: the floor MUST NOT re-interpolate the
        # same values that tripped the closure. W978 2nd-discipline:
        # ``default=`` is a literal constant.
        def _build_verdict_str(_verdict_floor):
            return _verdict_floor

        verdict_wrapped = _run_check_en(
            "compute_verdict",
            _build_verdict_str,
            verdict,
            default="hotspots completed",
        )
        # Keep the original key in summary aligned with the wrapped
        # verdict so downstream consumers (and the auto-fact humanizer)
        # read the SAME string both layers produced.
        runtime_summary["verdict"] = verdict_wrapped

        # W607-EN: surface score_classify + compute_predicate results on
        # the envelope so consumers can read run state + rollup
        # dimensions without re-deriving from the raw `hotspots` list.
        # W978 7th-discipline: bare ``_score_dict["state"]`` /
        # ``_pred_fields["..."]`` lookups (floor dicts guarantee the
        # keys) -- NOT ``.get(..., expensive_default)``.
        runtime_summary["run_state"] = _score_dict["state"]
        runtime_summary["heat"] = _pred_fields["heat"]
        runtime_summary["commit_count"] = _pred_fields["commit_count"]
        runtime_summary["hottest_files"] = _pred_fields["hottest_files"]

        # W607-CP + W607-EN: mirror combined substrate-CALL +
        # aggregation-phase markers into BOTH the top-level envelope
        # ``warnings_out`` AND ``summary.warnings_out`` so MCP consumers
        # see disclosure regardless of which surface they read. Flipping
        # ``partial_success: True`` is the Pattern-2 silent-fallback
        # guard -- a degraded substrate OR aggregation path must NOT be
        # mistaken for a clean populated runtime verdict.
        _combined_warnings = list(_w607cp_warnings_out) + list(_w607en_warnings_out)
        if _combined_warnings:
            runtime_summary["warnings_out"] = list(_combined_warnings)
            runtime_summary["partial_success"] = True
            runtime_extra_top["warnings_out"] = list(_combined_warnings)

        # W607-EN -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("hotspots", ...)`` would otherwise
        # crash AFTER all substrate + aggregation signals were already
        # gathered. Floor to a minimal envelope stub so consumers still
        # receive a parseable JSON object with the marker attached + the
        # canonical command name. W978 6th-discipline: floor is a
        # concrete dict, not a sentinel that may __len__-raise downstream.
        _envelope_floor: dict = {
            "command": "hotspots",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": "hotspots completed",
                "partial_success": True,
                "warnings_out": list(_combined_warnings),
            },
            "warnings_out": list(_combined_warnings),
        }
        envelope = _run_check_en(
            "serialize_envelope",
            json_envelope,
            "hotspots",
            budget=token_budget,
            summary=runtime_summary,
            **envelope_extra,
            **runtime_extra_top,
            hotspots=_serialized,
            next_steps=next_steps,
            default=_envelope_floor,
        )
        # W607-EN -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``hotspots_serialize_envelope_failed:`` marker was appended to
        # ``_w607en_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output. Clean path ->
        # envelope is the real json_envelope return value, no rebuild.
        if envelope is _envelope_floor and _w607en_warnings_out:
            _combined_warnings = list(_w607cp_warnings_out) + list(_w607en_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
            _envelope_floor["warnings_out"] = list(_combined_warnings)
            envelope = _envelope_floor
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
