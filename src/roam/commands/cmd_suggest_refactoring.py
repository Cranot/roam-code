"""Proactive refactoring recommendations ranked by structural risk signals."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.catalog.smells import run_all_detectors
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    budget_truncate,
    format_table,
    json_envelope,
    loc,
    to_json,
)


_CANDIDATES_SQL = """
SELECT
    s.id,
    s.name,
    COALESCE(s.qualified_name, s.name) AS qualified_name,
    s.kind,
    s.line_start,
    s.line_end,
    f.path AS file_path,
    COALESCE(sm.cognitive_complexity, 0) AS cognitive_complexity,
    COALESCE(sm.nesting_depth, 0) AS nesting_depth,
    COALESCE(
        sm.line_count,
        CASE
            WHEN s.line_start IS NOT NULL AND s.line_end IS NOT NULL
            THEN (s.line_end - s.line_start + 1)
            ELSE 0
        END
    ) AS line_count,
    COALESCE(sm.param_count, 0) AS param_count,
    sm.coverage_pct AS symbol_coverage_pct,
    fs.coverage_pct AS file_coverage_pct,
    COALESCE(gm.pagerank, 0.0) AS pagerank,
    COALESCE(gm.in_degree, 0) AS in_degree,
    COALESCE(gm.out_degree, 0) AS out_degree,
    COALESCE(gm.debt_score, 0.0) AS debt_score,
    COALESCE(fs.total_churn, 0) AS total_churn,
    COALESCE(fs.commit_count, 0) AS commit_count
FROM symbols s
JOIN files f ON s.file_id = f.id
LEFT JOIN symbol_metrics sm ON sm.symbol_id = s.id
LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
LEFT JOIN file_stats fs ON fs.file_id = s.file_id
WHERE s.kind IN ('function', 'method', 'class', 'interface', 'struct', 'enum')
  AND (s.is_exported = 1 OR s.kind IN ('class', 'interface', 'struct', 'enum'))
  AND f.path NOT LIKE '%/tests/%'
  AND f.path NOT LIKE '%/test/%'
  AND f.path NOT LIKE '%test\\_%' ESCAPE '\\'
  AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\'
ORDER BY f.path, s.line_start
"""


_SEVERITY_WEIGHT = {
    "critical": 2.0,
    "warning": 1.0,
    "info": 0.5,
}


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    max_v = max(values)
    if max_v <= 0:
        return [0.0 for _ in values]
    return [min(1.0, max(0.0, (v / max_v))) for v in values]


def _coverage_pct(row: dict) -> float | None:
    sym_cov = row.get("symbol_coverage_pct")
    if sym_cov is not None:
        return float(sym_cov)
    file_cov = row.get("file_coverage_pct")
    if file_cov is not None:
        return float(file_cov)
    return None


def _coverage_gap(row: dict) -> float:
    cov = _coverage_pct(row)
    if cov is None:
        # Unknown imported coverage should not dominate ranking.
        return 0.35
    return min(1.0, max(0.0, (80.0 - cov) / 80.0))


def _parse_location(location: str) -> tuple[str, int | None]:
    text = (location or "").replace("\\", "/").strip()
    if not text:
        return "", None
    path, sep, tail = text.rpartition(":")
    if sep and tail.isdigit():
        return path, int(tail)
    return text, None


def _line_span(symbol: dict) -> tuple[int, int]:
    start = int(symbol.get("line_start") or 0)
    end = int(symbol.get("line_end") or start)
    if end < start:
        end = start
    return start, end


def _best_symbol_for_smell(
    symbols: list[dict],
    *,
    line: int | None,
    symbol_name: str,
) -> dict | None:
    if not symbols:
        return None

    if line is not None:
        matches = []
        for sym in symbols:
            start, end = _line_span(sym)
            if start <= line <= end:
                matches.append((end - start, start, sym))
        if matches:
            matches.sort(key=lambda t: (t[0], t[1], t[2]["name"]))
            return matches[0][2]

    if symbol_name:
        wanted = symbol_name.lower()
        for sym in symbols:
            if (sym.get("name") or "").lower() == wanted:
                return sym
    return None


def _build_smell_index(findings: list[dict], candidates: list[dict]) -> dict[int, dict]:
    by_file: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)

    for sym in candidates:
        path = (sym.get("file_path") or "").replace("\\", "/")
        by_file[path].append(sym)
        key = (sym.get("name") or "").lower()
        if key:
            by_name[key].append(sym)

    index: dict[int, dict] = {}
    for finding in findings:
        path, line = _parse_location(str(finding.get("location") or ""))
        symbol_name = str(finding.get("symbol_name") or "")
        bucket = _best_symbol_for_smell(
            by_file.get(path, []),
            line=line,
            symbol_name=symbol_name,
        )
        if bucket is None and symbol_name:
            matches = by_name.get(symbol_name.lower(), [])
            if len(matches) == 1:
                bucket = matches[0]
        if bucket is None:
            continue

        sym_id = int(bucket["id"])
        row = index.setdefault(
            sym_id,
            {
                "count": 0,
                "critical": 0,
                "warning": 0,
                "info": 0,
                "weighted": 0.0,
                "types": set(),
            },
        )
        sev = str(finding.get("severity") or "info").lower()
        if sev not in ("critical", "warning", "info"):
            sev = "info"
        row["count"] += 1
        row[sev] += 1
        row["weighted"] += _SEVERITY_WEIGHT[sev]
        smell_id = str(finding.get("smell_id") or "").strip()
        if smell_id:
            row["types"].add(smell_id)

    for row in index.values():
        row["types"] = sorted(row["types"])
    return index


def _suggest_action(row: dict, smells: dict) -> str:
    kind = str(row.get("kind") or "")
    cc = float(row.get("cognitive_complexity") or 0.0)
    line_count = int(row.get("line_count") or 0)
    coupling = int(row.get("in_degree") or 0) + int(row.get("out_degree") or 0)
    smell_count = int(smells.get("count") or 0)
    nesting = int(row.get("nesting_depth") or 0)

    if kind in ("class", "interface", "struct", "enum") and line_count >= 220:
        return "split"
    if cc >= 28 or line_count >= 90:
        return "extract"
    if coupling >= 16:
        return "decouple"
    if smell_count >= 2 or nesting >= 4:
        return "simplify"
    return "extract"


def _effort_bucket(row: dict, smells: dict, coverage_gap: float) -> str:
    points = 0
    cc = float(row.get("cognitive_complexity") or 0.0)
    line_count = int(row.get("line_count") or 0)
    coupling = int(row.get("in_degree") or 0) + int(row.get("out_degree") or 0)
    churn = int(row.get("total_churn") or 0)
    smell_count = int(smells.get("count") or 0)

    if cc >= 30:
        points += 2
    elif cc >= 15:
        points += 1

    if line_count >= 120:
        points += 2
    elif line_count >= 60:
        points += 1

    if coupling >= 20:
        points += 2
    elif coupling >= 10:
        points += 1

    if churn >= 300:
        points += 1
    if smell_count >= 3:
        points += 1
    if coverage_gap >= 0.6:
        points += 1

    if points <= 2:
        return "S"
    if points <= 5:
        return "M"
    return "L"


def _reasons(
    row: dict,
    smells: dict,
    *,
    complexity_n: float,
    coupling_n: float,
    churn_n: float,
    smell_n: float,
    debt_n: float,
    coverage_gap: float,
) -> list[str]:
    reasons: list[str] = []
    cc = float(row.get("cognitive_complexity") or 0.0)
    nesting = int(row.get("nesting_depth") or 0)
    coupling = int(row.get("in_degree") or 0) + int(row.get("out_degree") or 0)
    churn = int(row.get("total_churn") or 0)
    commits = int(row.get("commit_count") or 0)
    debt = float(row.get("debt_score") or 0.0)
    coverage_pct = _coverage_pct(row)

    if complexity_n >= 0.6 or cc >= 20:
        reasons.append(f"High complexity (CC {cc:.0f}, nesting {nesting})")
    if coupling_n >= 0.6 or coupling >= 10:
        reasons.append(
            "High coupling "
            f"(fan-in {int(row.get('in_degree') or 0)}, fan-out {int(row.get('out_degree') or 0)})",
        )
    if churn_n >= 0.6 and churn > 0:
        reasons.append(f"High churn ({churn} changed lines across {commits} commits)")
    if smell_n >= 0.45 and int(smells.get("count") or 0) > 0:
        critical = int(smells.get("critical") or 0)
        reasons.append(f"Smell signals ({int(smells.get('count') or 0)} findings, {critical} critical)")
    if coverage_gap >= 0.45:
        if coverage_pct is None:
            reasons.append("Coverage unknown (no imported coverage data)")
        else:
            reasons.append(f"Low test coverage ({coverage_pct:.1f}%)")
    if debt_n >= 0.55 or debt >= 0.3:
        reasons.append(f"High structural debt score ({debt:.3f})")

    if not reasons:
        reasons.append("Combined risk signals suggest refactoring leverage")
    return reasons


@click.command("suggest-refactoring")
@click.option(
    "-n",
    "--limit",
    default=20,
    show_default=True,
    help="Maximum number of recommendations to return.",
)
@click.option(
    "--min-score",
    default=45,
    show_default=True,
    type=int,
    help="Only include recommendations with score >= N (0-100).",
)
@click.pass_context
def suggest_refactoring(ctx, limit, min_score):
    """Rank symbols that are likely to yield high-value refactoring wins."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = bool(ctx.obj.get("detail", False)) if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        candidates = [dict(r) for r in conn.execute(_CANDIDATES_SQL).fetchall()]
        smells = run_all_detectors(conn)

    smell_index = _build_smell_index(smells, candidates)

    complexity_raw: list[float] = []
    coupling_raw: list[float] = []
    churn_raw: list[float] = []
    smell_raw: list[float] = []
    debt_raw: list[float] = []
    coverage_raw: list[float] = []
    for row in candidates:
        sym_smells = smell_index.get(row["id"], {})
        cc = float(row.get("cognitive_complexity") or 0.0)
        line_count = int(row.get("line_count") or 0)
        nesting = int(row.get("nesting_depth") or 0)
        complexity_raw.append(cc + (nesting * 2.0) + max(0, line_count - 30) / 30.0)
        coupling_raw.append(float(int(row.get("in_degree") or 0) + int(row.get("out_degree") or 0)))
        churn_raw.append(float(int(row.get("total_churn") or 0)))
        smell_raw.append(float(sym_smells.get("weighted") or 0.0))
        debt_raw.append(float(row.get("debt_score") or 0.0))
        coverage_raw.append(_coverage_gap(row))

    complexity_n = _normalize(complexity_raw)
    coupling_n = _normalize(coupling_raw)
    churn_n = _normalize(churn_raw)
    smell_n = _normalize(smell_raw)
    debt_n = _normalize(debt_raw)

    scored: list[dict] = []
    for idx, row in enumerate(candidates):
        sym_smells = smell_index.get(
            row["id"],
            {"count": 0, "critical": 0, "warning": 0, "info": 0, "weighted": 0.0, "types": []},
        )
        cov_gap = coverage_raw[idx]
        weighted = (
            (0.25 * complexity_n[idx])
            + (0.20 * coupling_n[idx])
            + (0.15 * churn_n[idx])
            + (0.15 * smell_n[idx])
            + (0.15 * cov_gap)
            + (0.10 * debt_n[idx])
        )
        score = int(round(weighted * 100))

        reasons = _reasons(
            row,
            sym_smells,
            complexity_n=complexity_n[idx],
            coupling_n=coupling_n[idx],
            churn_n=churn_n[idx],
            smell_n=smell_n[idx],
            debt_n=debt_n[idx],
            coverage_gap=cov_gap,
        )
        action = _suggest_action(row, sym_smells)
        effort = _effort_bucket(row, sym_smells, cov_gap)

        coverage_pct = _coverage_pct(row)
        scored.append(
            {
                "symbol": row["qualified_name"] or row["name"],
                "name": row["name"],
                "kind": row["kind"],
                "file": row["file_path"],
                "line": int(row.get("line_start") or 1),
                "score": score,
                "effort": effort,
                "action": action,
                "reasons": reasons,
                "signals": {
                    "complexity": float(row.get("cognitive_complexity") or 0.0),
                    "nesting_depth": int(row.get("nesting_depth") or 0),
                    "line_count": int(row.get("line_count") or 0),
                    "fan_in": int(row.get("in_degree") or 0),
                    "fan_out": int(row.get("out_degree") or 0),
                    "pagerank": round(float(row.get("pagerank") or 0.0), 6),
                    "debt_score": round(float(row.get("debt_score") or 0.0), 3),
                    "total_churn": int(row.get("total_churn") or 0),
                    "commit_count": int(row.get("commit_count") or 0),
                    "smell_count": int(sym_smells.get("count") or 0),
                    "critical_smells": int(sym_smells.get("critical") or 0),
                    "coverage_pct": round(coverage_pct, 1) if coverage_pct is not None else None,
                },
            },
        )

    scored.sort(
        key=lambda x: (
            -x["score"],
            -float(x["signals"]["pagerank"]),
            x["file"],
            x["line"],
            x["symbol"],
        ),
    )

    filtered = [item for item in scored if item["score"] >= int(min_score)]
    shown = filtered[: int(limit)]

    top_score = shown[0]["score"] if shown else 0
    verdict = (
        f"{len(filtered)} refactoring candidate(s) scored >= {min_score}/100"
        if filtered
        else f"No symbols scored >= {min_score}/100"
    )

    if json_mode:
        payload = json_envelope(
            "suggest-refactoring",
            summary={
                "verdict": verdict,
                "considered_symbols": len(scored),
                "candidates": len(filtered),
                "shown": len(shown),
                "min_score": int(min_score),
                "top_score": top_score,
            },
            recommendations=shown,
            scoring={
                "weights": {
                    "complexity": 0.25,
                    "coupling": 0.20,
                    "churn": 0.15,
                    "smells": 0.15,
                    "coverage_gap": 0.15,
                    "debt": 0.10,
                },
            },
        )
        if not detail:
            payload.pop("scoring", None)
        click.echo(to_json(payload))
        return

    if not shown:
        click.echo(f"VERDICT: {verdict}")
        return

    headers = ["Score", "Eff", "Action", "Symbol", "Location", "Reasons"]
    rows: list[list[str]] = []
    for item in shown:
        reasons = item["reasons"] if detail else item["reasons"][:2]
        rows.append(
            [
                str(item["score"]),
                item["effort"],
                item["action"],
                f"{abbrev_kind(item['kind'])} {item['symbol']}",
                loc(item["file"], item["line"]),
                "; ".join(reasons),
            ],
        )

    lines = [
        "Refactoring recommendations",
        "",
        f"VERDICT: {verdict}",
        "",
        format_table(headers, rows),
    ]
    if len(filtered) > len(shown):
        lines.append("")
        lines.append(f"(+{len(filtered) - len(shown)} more candidates; increase --limit to view all)")
    click.echo(budget_truncate("\n".join(lines), token_budget))
