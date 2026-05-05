"""``roam audit`` — AI Agent Readiness Audit.

redactedPriority 1 strategic blocker per build_priorities.md. Chains
``index → describe → health → risk → dead → owner → test-map → pr-risk``
into a single structured-JSON envelope. The artifact paying customers
buy: a one-shot audit that calibrates how ready a codebase is for
agent-driven work.

Sections:

  - ``health`` — composite health score with category breakdown
  - ``debt``   — total estimated debt + top hotspots
  - ``dead``   — unused-symbol count + top wasted-LOC files
  - ``risk``   — surface high-risk files (churn × complexity × fan-in)
  - ``test_pyramid`` — unit/integration/e2e/smoke distribution
  - ``coverage`` — imported-coverage % when available
  - ``api``    — count of public symbols (the agent-facing surface)

Each section runs the corresponding sub-command in --json mode and
extracts a small set of high-signal fields. Failures from individual
sections are surfaced as ``{"error": ...}`` rows so the audit never
fails the whole report.
"""

from __future__ import annotations

import json as _json

import click
from click.testing import CliRunner

from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json


def _capture(args: list[str]) -> dict:
    """Invoke a roam subcommand in --json mode in-process."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", *args])
    if result.exit_code not in (0, 5):  # 5 = gate failure (still produces JSON)
        return {
            "_error": f"exit {result.exit_code}",
            "_command": args,
            "_output_head": (result.output or "")[:200],
        }
    try:
        return _json.loads(result.output)
    except Exception as exc:
        return {
            "_error": f"non-JSON output: {exc}",
            "_command": args,
            "_output_head": (result.output or "")[:200],
        }


def _summary_field(payload: dict, *keys: str, default=None):
    summary = payload.get("summary") or {}
    for k in keys:
        if k in summary and summary[k] is not None:
            return summary[k]
    return default


@click.command()
@click.option(
    "--brief",
    is_flag=True,
    help="Drop per-section detail; keep only the top-level summary scores.",
)
@click.pass_context
def audit(ctx, brief) -> None:
    """One-shot AI Agent Readiness Audit.

    Bundles health, debt, dead-code, risk, test-pyramid, coverage, and
    API-surface signals into a single envelope. Designed as the
    structured artifact a paid audit attaches to its report.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    health = _capture(["health"])
    debt = _capture(["debt"])
    dead = _capture(["dead"])
    test_pyramid = _capture(["test-pyramid"])
    api = _capture(["api", "--limit", "0"])
    stats = _capture(["stats"])
    hotspots = _capture(["hotspots", "--danger"])

    # Top-level scores so consumers can branch fast.
    health_score = _summary_field(health, "health_score", "score")
    debt_total = _summary_field(debt, "total_minutes", "estimated_minutes", "total")
    dead_count = _summary_field(dead, "dead_count", "total")
    danger_count = _summary_field(hotspots, "count", "danger_count", default=0)
    pyramid_total = _summary_field(test_pyramid, "total", default=0)
    api_count = _summary_field(api, "count", default=0)
    file_total = _summary_field(stats, "file_total", default=0)
    sym_total = _summary_field(stats, "symbol_total", default=0)
    coverage_pct = _summary_field(health, "imported_coverage_pct")

    # Verdict — stack-rank the most pressing dimension.
    pressures = []
    if isinstance(health_score, (int, float)) and health_score < 60:
        pressures.append(f"health {health_score}/100")
    if isinstance(danger_count, int) and danger_count > 0:
        pressures.append(f"{danger_count} danger-zone file(s)")
    if isinstance(coverage_pct, (int, float)) and coverage_pct < 40:
        pressures.append(f"coverage {coverage_pct:.0f}%")
    if pressures:
        verdict = "AUDIT — pressures: " + ", ".join(pressures)
    else:
        verdict = (
            f"AUDIT — health {health_score or '?'}/100, "
            f"{file_total} files, {sym_total} symbols, "
            f"{api_count} public-API symbols"
        )

    summary = {
        "verdict": verdict,
        "health_score": health_score,
        "debt_total": debt_total,
        "dead_count": dead_count,
        "danger_zone_count": danger_count,
        "test_count": pyramid_total,
        "api_surface": api_count,
        "imported_coverage_pct": coverage_pct,
        "file_total": file_total,
        "symbol_total": sym_total,
    }

    sections = {
        "health": health if not brief else {"summary": health.get("summary", {})},
        "debt": debt if not brief else {"summary": debt.get("summary", {})},
        "dead": dead if not brief else {"summary": dead.get("summary", {})},
        "test_pyramid": test_pyramid if not brief else {"summary": test_pyramid.get("summary", {})},
        "hotspots_danger": hotspots if not brief else {"summary": hotspots.get("summary", {})},
        "stats": stats if not brief else {"summary": stats.get("summary", {})},
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "audit",
                    summary=summary,
                    sections=sections,
                    api_count=api_count,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"{'Metric':<28}  Value")
    click.echo(f"{'-' * 28}  {'-' * 30}")
    for label, value in [
        ("health score (0-100)", health_score),
        ("debt (total)", debt_total),
        ("dead symbols", dead_count),
        ("danger-zone files", danger_count),
        ("test files indexed", pyramid_total),
        ("public API surface", api_count),
        ("imported coverage %", coverage_pct),
        ("total files", file_total),
        ("total symbols", sym_total),
    ]:
        click.echo(f"{label:<28}  {value}")
