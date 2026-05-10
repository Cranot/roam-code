"""Show runtime or security hotspots."""

from __future__ import annotations

import re
from collections import defaultdict, deque

import click

from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.output.formatter import json_envelope, summary_envelope, to_json

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}
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
            _SEVERITY_ORDER.get(h["severity"], 9),
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
@click.pass_context
def hotspots(ctx, sort_runtime, discrepancy, security_mode, danger_mode):
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
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    ensure_index()

    if security_mode and (sort_runtime or discrepancy):
        click.echo("Cannot combine --security with --runtime or --discrepancy")
        raise SystemExit(1)
    if danger_mode and (sort_runtime or discrepancy or security_mode):
        click.echo("Cannot combine --danger with --runtime, --discrepancy, or --security")
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
            envelope = json_envelope(
                "hotspots",
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
            if not detail:
                envelope = summary_envelope(envelope)
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

    with open_db(readonly=True) as conn:
        # Ensure table exists for query even in readonly mode
        try:
            conn.execute("SELECT COUNT(*) FROM runtime_stats")
        except Exception:
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
            envelope = summary_envelope(envelope)
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
