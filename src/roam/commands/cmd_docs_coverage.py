"""Documentation coverage and staleness analysis for exported symbols.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because docs-coverage outputs are invocation-scoped coverage
percentages (documented vs undocumented exported symbols rolled up by
file / package) — not per-location code violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1224-audit memo.
"""

from __future__ import annotations

from collections import defaultdict

import click

from roam.capability import roam_capability
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
  AND COALESCE(f.file_role, 'source') NOT IN ('test', 'tests')
  AND f.path NOT LIKE 'tests/%'
  AND f.path NOT LIKE 'test/%'
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


# W1463 -- the state an empty denominator produces.
#
# ``_compute_coverage`` used to return ``100.0`` for ``total <= 0``: an
# uncomputable signal collapsing into the exact value that means perfect,
# which the ``--threshold`` gate then compared and passed. Coverage over
# an empty set is UNKNOWN, not 100%. The sibling gate on the same index
# already refuses -- ``roam py-types --ci --min-coverage 95`` prints
# "GATE FAILED: type coverage not computable (no_public_python_functions)"
# and exits 5 -- so this is that decision, one command over.
_NO_PUBLIC_SYMBOLS = "no_public_symbols"


def _compute_coverage(symbols: list[dict]) -> tuple[int, int, float | None]:
    """``(total, documented, pct)`` with *pct* ``None`` when uncomputable.

    ``None`` means "there was no denominator", which is a different fact
    from any number. Callers must branch on it rather than formatting it.
    """
    total = len(symbols)
    documented = sum(1 for s in symbols if _has_docs(s))
    if total <= 0:
        return 0, 0, None
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


@roam_capability(
    name="docs-coverage",
    category="refactoring",
    summary="Analyze exported-symbol doc coverage and stale docs in one report",
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
    API surface and prioritizes missing docs by symbol importance. For
    dangling file references (markdown links and backtick paths whose
    target no longer exists), see ``stale-refs``.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        rows = conn.execute(_PUBLIC_SYMBOLS_SQL).fetchall()

    symbols = [_to_symbol_dict(r) for r in rows]
    total_public, documented_public, coverage_pct = _compute_coverage(symbols)
    missing = _missing_docs(symbols)
    stale = _stale_docs(symbols, days)

    quality_buckets: defaultdict[str, int] = defaultdict(int, {"ABSENT": 0, "SHALLOW": 0, "RICH": 0})
    quality_samples: dict[str, list[dict]] = {"ABSENT": [], "SHALLOW": [], "RICH": []}
    if quality:
        for s in symbols:
            bucket, _signals = _docstring_quality(s.get("docstring") or "")
            quality_buckets[bucket] += 1
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

    # W1463: uncomputable is not "meets the bar". A gate asked for a number
    # over an empty denominator gets a refusal, not a pass. Reporting mode
    # (no --threshold) still exits 0 -- "this project exports nothing public"
    # is a complete answer when nobody asked it to certify anything.
    coverage_computable = coverage_pct is not None
    gate_passed = True
    if threshold > 0 and not coverage_computable:
        gate_passed = False
    elif threshold > 0 and coverage_pct is not None and coverage_pct < float(threshold):
        gate_passed = False

    coverage_display = f"{coverage_pct:.1f}%" if coverage_pct is not None else "not computable"

    if json_mode:
        # W17.2 / Pattern 3c: name the inclusion criterion so consumers
        # know that `docs-coverage`'s "public_symbols" count is the
        # export-marker subset (smaller than `api`'s no-underscore
        # subset). The two counts differ for a reason — label the
        # difference rather than hiding it.
        from roam.quality.public_symbols import (
            CRITERION_HAS_EXPORT_MARKER,
        )
        from roam.quality.public_symbols import (
            definition as _ps_def,
        )

        summary_payload = {
            "public_symbols": total_public,
            "public_symbols_inclusion_criterion": CRITERION_HAS_EXPORT_MARKER,
            "public_symbols_definition": _ps_def(),
            "documented_symbols": documented_public,
            "coverage_pct": coverage_pct,
            # W1463: ``coverage_pct: null`` is the honest shape for an empty
            # denominator, and this flag is what a consumer branches on so it
            # never has to guess whether a number is a measurement or a floor.
            # Mirrors ``py-types``' ``coverage_pct_computable``.
            "coverage_pct_computable": coverage_computable,
            "missing_docs": len(missing),
            "stale_docs": len(stale),
            "threshold": threshold,
            "gate_passed": gate_passed,
            "verdict": (
                f"{coverage_display} doc coverage ({documented_public}/{total_public} public symbols)"
                if coverage_computable
                else f"doc coverage not computable ({_NO_PUBLIC_SYMBOLS})"
            ),
        }
        if not coverage_computable:
            # Unlike py-types (where "this project has no Python" is a
            # complete answer about a language), docs-coverage was ASKED for
            # a number about this index and could not produce one. Say so.
            summary_payload["state"] = _NO_PUBLIC_SYMBOLS
            summary_payload["partial_success"] = True
        if quality:
            summary_payload["quality_buckets"] = dict(quality_buckets)
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
    click.echo(f"  Public symbols: {total_public}\n  Documented: {documented_public}\n  Coverage: {coverage_display}")
    if not coverage_computable:
        click.echo(f"  ({_NO_PUBLIC_SYMBOLS}: coverage over an empty set is unknown, not 100%)")
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
        if coverage_computable:
            click.echo(f"\n  GATE FAILED: coverage {coverage_display} below threshold {threshold}%")
        else:
            click.echo(f"\n  GATE FAILED: doc coverage not computable ({_NO_PUBLIC_SYMBOLS}) — required {threshold}%")
        from roam.exit_codes import EXIT_GATE_FAILURE

        ctx.exit(EXIT_GATE_FAILURE)
