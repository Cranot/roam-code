"""Documentation coverage and staleness analysis for exported symbols."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.commands.cmd_doc_staleness import _analyze_staleness
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

_PUBLIC_SYMBOLS_SQL = """
SELECT s.id, s.name, s.kind, s.signature,
       s.line_start, s.line_end, s.docstring,
       s.visibility, s.is_exported,
       f.path AS file_path,
       COALESCE(gm.pagerank, 0.0) AS pagerank
FROM symbols s
JOIN files f ON s.file_id = f.id
LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
WHERE s.kind IN ('function', 'class', 'method', 'interface', 'struct', 'enum')
  AND s.is_exported = 1
  AND s.line_start IS NOT NULL
  AND s.line_end IS NOT NULL
  AND s.line_end >= s.line_start
  AND f.path NOT LIKE '%/tests/%'
  AND f.path NOT LIKE '%/test/%'
  AND f.path NOT LIKE '%test\\_%' ESCAPE '\\'
  AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\'
ORDER BY pagerank DESC, f.path, s.line_start
"""


def _to_symbol_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "signature": row["signature"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "docstring": row["docstring"] or "",
        "visibility": row["visibility"] or "public",
        "is_exported": bool(row["is_exported"]),
        "file_path": row["file_path"],
        "pagerank": float(row["pagerank"] or 0.0),
    }


def _has_docs(symbol: dict) -> bool:
    return bool((symbol.get("docstring") or "").strip())


def _docstring_quality(text: str) -> tuple[str, dict]:
    """bucket a docstring into PRESENT / SHALLOW / RICH.

    PRESENT: any non-empty docstring.
    SHALLOW: present, < 80 chars, no examples block, no parameter mentions.
    RICH:    >= 80 chars AND (mentions params/returns/raises OR has an
             examples or fenced code block).

    Returns ``(bucket, signals)`` where signals records the boolean checks
    that contributed to the verdict — useful for explaining a low score.
    """
    s = (text or "").strip()
    signals = {
        "length": len(s),
        "has_params": False,
        "has_returns": False,
        "has_raises": False,
        "has_example": False,
    }
    if not s:
        return "ABSENT", signals
    lower = s.lower()
    signals["has_params"] = "param" in lower or "args:" in lower or "arguments:" in lower or ":param" in lower
    signals["has_returns"] = "return" in lower or ":returns:" in lower
    signals["has_raises"] = "raise" in lower or ":raises:" in lower
    signals["has_example"] = ">>>" in s or "```" in s or "example" in lower or "examples\n" in lower
    rich_signal = signals["has_params"] or signals["has_returns"] or signals["has_example"]
    if len(s) >= 80 and rich_signal:
        return "RICH", signals
    return "SHALLOW", signals


def _compute_coverage(symbols: list[dict]) -> tuple[int, int, float]:
    total = len(symbols)
    documented = sum(1 for s in symbols if _has_docs(s))
    if total <= 0:
        return 0, 0, 100.0
    pct = (documented / total) * 100.0
    return total, documented, round(pct, 1)


def _missing_docs(symbols: list[dict]) -> list[dict]:
    missing = [s for s in symbols if not _has_docs(s)]
    missing.sort(
        key=lambda s: (-float(s.get("pagerank", 0.0)), s["file_path"], s["line_start"]),
    )
    return [
        {
            "name": s["name"],
            "kind": s["kind"],
            "file": s["file_path"],
            "line": s["line_start"],
            "pagerank": round(float(s.get("pagerank", 0.0)), 6),
        }
        for s in missing
    ]


def _stale_docs(symbols: list[dict], threshold_days: int) -> list[dict]:
    documented = [s for s in symbols if _has_docs(s)]
    if not documented:
        return []

    by_file: dict[str, list[dict]] = defaultdict(list)
    for s in documented:
        by_file[s["file_path"]].append(
            {
                "name": s["name"],
                "kind": s["kind"],
                "file_path": s["file_path"],
                "line_start": s["line_start"],
                "line_end": s["line_end"],
                "docstring": s["docstring"],
            }
        )

    return _analyze_staleness(by_file, find_project_root(), threshold_days)


@click.command("docs-coverage")
@click.option(
    "--limit",
    default=20,
    show_default=True,
    help="Maximum number of missing/stale symbols to display.",
)
@click.option(
    "--days",
    default=90,
    show_default=True,
    help="Staleness threshold in days (body changed N+ days after docs).",
)
@click.option(
    "--threshold",
    type=int,
    default=0,
    show_default=True,
    help="Fail with exit code 5 if coverage %% is below threshold (0 = no gate).",
)
@click.option(
    "--quality",
    is_flag=True,
    help="bucket each documented symbol into ABSENT/SHALLOW/RICH.",
)
@click.pass_context
def docs_coverage(ctx, limit, days, threshold, quality):
    """Analyze exported-symbol doc coverage and stale docs in one report.

    Reports coverage percentage, PageRank-ranked missing-doc hotlist, and
    stale docs for the public API surface.  Use ``--threshold`` as a CI
    gate (exits with code 5 if coverage is below the threshold).

    Unlike ``doc-staleness`` (which scans ALL symbols including private
    ones for stale docstrings), this command focuses on the exported public
    API surface and prioritizes missing docs by symbol importance.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        rows = conn.execute(_PUBLIC_SYMBOLS_SQL).fetchall()

    symbols = [_to_symbol_dict(r) for r in rows]
    total_public, documented_public, coverage_pct = _compute_coverage(symbols)
    missing = _missing_docs(symbols)
    stale = _stale_docs(symbols, days)

    quality_buckets: dict[str, int] = {"ABSENT": 0, "SHALLOW": 0, "RICH": 0}
    quality_samples: dict[str, list[dict]] = {"ABSENT": [], "SHALLOW": [], "RICH": []}
    if quality:
        for s in symbols:
            bucket, _signals = _docstring_quality(s.get("docstring") or "")
            quality_buckets[bucket] = quality_buckets.get(bucket, 0) + 1
            samples = quality_samples.setdefault(bucket, [])
            if len(samples) < 5:
                samples.append(
                    {
                        "name": s["name"],
                        "kind": s["kind"],
                        "file": s["file_path"],
                        "line": s["line_start"],
                    }
                )

    display_missing = missing[:limit]
    display_stale = stale[:limit]

    gate_passed = True
    if threshold > 0 and coverage_pct < float(threshold):
        gate_passed = False

    if json_mode:
        summary_payload = {
            "public_symbols": total_public,
            "documented_symbols": documented_public,
            "coverage_pct": coverage_pct,
            "missing_docs": len(missing),
            "stale_docs": len(stale),
            "threshold": threshold,
            "gate_passed": gate_passed,
            "verdict": (f"{coverage_pct:.1f}% doc coverage ({documented_public}/{total_public} public symbols)"),
        }
        if quality:
            summary_payload["quality_buckets"] = quality_buckets
        payload = json_envelope(
            "docs-coverage",
            summary=summary_payload,
            missing_docs=display_missing,
            stale_docs=display_stale,
            threshold_days=days,
            quality_samples=quality_samples if quality else {},
        )
        click.echo(to_json(payload))

        if not gate_passed:
            from roam.exit_codes import EXIT_GATE_FAILURE

            ctx.exit(EXIT_GATE_FAILURE)
        return

    click.echo("Documentation coverage\n")
    click.echo(f"  Public symbols: {total_public}\n  Documented: {documented_public}\n  Coverage: {coverage_pct:.1f}%")
    click.echo(f"  Missing docs: {len(missing)}\n  Stale docs (>{days}d): {len(stale)}")

    if quality:
        click.echo("\nQuality buckets:")
        for bucket in ("ABSENT", "SHALLOW", "RICH"):
            n = quality_buckets.get(bucket, 0)
            sample = quality_samples.get(bucket) or []
            sample_text = ", ".join(f"{s['name']} ({s['file']}:{s['line']})" for s in sample[:3]) or "—"
            click.echo(f"  {bucket:<8}  {n:>5}  e.g. {sample_text}")

    if display_missing:
        click.echo("\nTop undocumented symbols (PageRank-ranked):")
        for item in display_missing:
            click.echo(
                f"  {item['name']:<25s} {abbrev_kind(item['kind']):<5s} "
                f"{loc(item['file'], item['line'])}  PR={item['pagerank']:.6f}"
            )

    if display_stale:
        click.echo(f"\nStale docs (>{days} days drift):")
        for item in display_stale:
            click.echo(
                f"  {item['name']:<25s} {abbrev_kind(item['kind']):<5s} "
                f"{loc(item['file'], item['line'])}  drift={item['drift_days']}d"
            )

    if not gate_passed:
        click.echo(f"\n  GATE FAILED: coverage {coverage_pct:.1f}% below threshold {threshold}%")
        from roam.exit_codes import EXIT_GATE_FAILURE

        ctx.exit(EXIT_GATE_FAILURE)
