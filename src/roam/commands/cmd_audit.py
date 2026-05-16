"""``roam audit`` — Codebase architecture audit.

Chains ``index → describe → health → risk → dead → owner → test-map → pr-risk``
into a single structured-JSON envelope. A one-shot audit that calibrates
how ready a codebase is for agent-driven work; same data layer powers the
"PR Replay" deliverable when paired with ``roam postmortem``.

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

Output formats: text (default) and ``--json``. ``audit`` does not expose
a ``--sarif`` flag and does not emit a top-level SARIF document — it is
a composite envelope spanning environment-scoped sections (test pyramid,
API surface, coverage %) that have no source coordinates to populate
SARIF ``locations[]``. SARIF is emitted by the composed subcommands
(``cmd_complexity``, ``cmd_health``, ``cmd_dead``, etc.) when their own
``--sarif`` flag fires directly. See ``cmd_doctor`` docstring for the
parallel "no SARIF emission" disclosure pattern (W1085 / W1144 / W1145).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because audit outputs are invocation-scoped composite audit
envelopes — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import json as _json

import click
from click.testing import CliRunner

from roam.capability import roam_capability
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


@roam_capability(
    name="audit",
    category="health",
    summary="One-shot architecture audit: health, debt, dead, risk, test pyramid, coverage, API.",
    inputs=["repo_path"],
    outputs=["health", "debt", "dead", "risk", "verdict"],
    examples=["roam audit", "roam audit --brief"],
    tags=["health", "audit", "ci"],
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
@click.command()
@click.option(
    "--brief",
    is_flag=True,
    help="Drop per-section detail; keep only the top-level summary scores.",
)
@click.pass_context
def audit(ctx, brief) -> None:
    """One-shot codebase architecture audit.

    Bundles health, debt, dead-code, risk, test-pyramid, coverage, and
    API-surface signals into a single envelope. Designed as the
    structured artifact a written audit report attaches.

    \b
    Examples:
      roam audit
      roam audit --brief
      roam --json audit
      roam --json audit --brief

    See also ``health`` (single-score snapshot), ``report`` (rendered
    Markdown report), and ``ai-readiness`` (agent-readiness scorecard).
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
    # Doc hygiene: dangling markdown links / hrefs / backticks / anchors.
    # Capped at the default --limit so the audit envelope stays small.
    stale_refs = _capture(["stale-refs"])

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
    stale_ref_count = _summary_field(stale_refs, "stale_refs", "missing_targets", default=0)

    # Verdict — stack-rank the most pressing dimension.
    pressures = []
    if isinstance(health_score, (int, float)) and health_score < 60:
        pressures.append(f"health {health_score}/100")
    if isinstance(danger_count, int) and danger_count > 0:
        pressures.append(f"{danger_count} danger-zone file(s)")
    if isinstance(coverage_pct, (int, float)) and coverage_pct < 40:
        pressures.append(f"coverage {coverage_pct:.0f}%")
    # Stale refs cross from "noise" to "pressure" at 10 — one or two
    # dangling links is a doc-hygiene paper cut, but a wave of them
    # signals an undocumented rename and breaks AI agents downstream.
    if isinstance(stale_ref_count, int) and stale_ref_count >= 10:
        pressures.append(f"{stale_ref_count} stale doc ref(s)")
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
        "stale_ref_count": stale_ref_count,
    }

    sections = {
        "health": health if not brief else {"summary": health.get("summary", {})},
        "debt": debt if not brief else {"summary": debt.get("summary", {})},
        "dead": dead if not brief else {"summary": dead.get("summary", {})},
        "test_pyramid": test_pyramid if not brief else {"summary": test_pyramid.get("summary", {})},
        "hotspots_danger": hotspots if not brief else {"summary": hotspots.get("summary", {})},
        "stats": stats if not brief else {"summary": stats.get("summary", {})},
        "stale_refs": stale_refs if not brief else {"summary": stale_refs.get("summary", {})},
    }

    # Unique-signal discovery hints (LAW 11: server-side hints teaching
    # better tools).  Several commands produce signal not available
    # elsewhere — surface them as imperative pointers so agents reading
    # the audit envelope discover them without scraping prose.  See
    # ``internal/dogfood/SYNTHESIS-2026-05-12.md`` section "NEW in v3".
    discoverable_via = {
        "danger_score": "roam metrics-push --dry-run",
        "algo_anti_patterns": "roam algo",
        "ai_generated_percentage": "roam ai-ratio",
        "ai_readiness_score": "roam ai-readiness",
        "ai_rot_score": "roam vibe-check",
        "module_cohesion_pct": "roam module <module>",
        "health_30d_forecast": "roam forecast",
    }
    next_steps = [
        "roam vibe-check",
        "roam ai-readiness",
        "roam ai-ratio",
        "roam algo",
        "roam forecast",
    ]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "audit",
                    summary=summary,
                    sections=sections,
                    api_count=api_count,
                    discoverable_via=discoverable_via,
                    next_steps=next_steps,
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
        ("stale doc refs", stale_ref_count),
    ]:
        click.echo(f"{label:<28}  {value}")

    # Advanced discovery — server-side hints (LAW 11).  Commands that
    # produce signal not surfaced by the audit sections themselves.
    click.echo()
    click.echo("Advanced discovery (unique signals):")
    click.echo("  roam metrics-push --dry-run   -- danger_score per file (churn × complexity × fan_in)")
    click.echo("  roam algo                     -- algorithmic anti-patterns")
    click.echo("  roam vibe-check               -- AI-rot score + pattern breakdown")
    click.echo("  roam ai-ratio                 -- ai_generated_percentage")
    click.echo("  roam ai-readiness             -- ai_readiness_score")
    click.echo("  roam module <dir>             -- cohesion_pct + API surface")
    click.echo("  roam forecast                 -- 30d health projection")
