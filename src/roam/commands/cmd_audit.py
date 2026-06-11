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

    # W607-P: per-phase warnings_out accumulator + helper-CALL wrapper.
    # Sixteenth-in-batch W607 consumer-layer arc. Mirrors W607-O's
    # cmd_dashboard idiom: each substrate-capture boundary is wrapped
    # so a future refactor of any subcommand (or an unexpected
    # CliRunner / runner.invoke raise above _capture's own guards) is
    # still surfaced as a ``audit_<phase>_failed:<exc_class>:<detail>``
    # marker instead of crashing the audit envelope. The empty-bucket
    # discipline holds: no warnings_out → byte-identical envelope.
    _w607p_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None):
        """Run one substrate-capture with W607-P marker emission.

        Clean call returns the result as-is. On an uncaught raise,
        surface ``audit_<phase>_failed:<exc_class>:<detail>`` via
        ``_w607p_warnings_out`` and substitute *default* — the envelope
        still emits the remaining substrates cleanly.
        """
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607p_warnings_out.append(f"audit_{phase}_failed:{type(exc).__name__}:{exc}")
            return default if default is not None else {}

    health = _run_check("health", _capture, ["health"], default={})
    debt = _run_check("debt", _capture, ["debt"], default={})
    dead = _run_check("dead", _capture, ["dead"], default={})
    test_pyramid = _run_check("test_pyramid", _capture, ["test-pyramid"], default={})
    api = _run_check("api", _capture, ["api", "--limit", "0"], default={})
    stats = _run_check("stats", _capture, ["stats"], default={})
    hotspots = _run_check("hotspots", _capture, ["hotspots", "--danger"], default={})
    # Doc hygiene: dangling markdown links / hrefs / backticks / anchors.
    # Capped at the default --limit so the audit envelope stays small.
    stale_refs = _run_check("stale_refs", _capture, ["stale-refs"], default={})

    # W607-DM: producer-side substrate-CALL plumbing layered ON TOP of
    # the W607-P sub-command capture wrap (which guards the 8 _capture
    # boundaries above). W607-DM extends the marker family to the
    # POST-capture substrate boundaries — score extraction, verdict
    # composition, section assembly, envelope serialization, and text
    # formatting — so a raise inside `_summary_field`, the verdict
    # f-string, the sections dict-build, or `to_json` no longer torpedoes
    # the audit envelope without lineage.
    #
    # Pair: cmd_metrics_push (W607-DI consumer) ↔ cmd_audit (W607-DM
    # producer). The metrics-emission 2-way is closed at the
    # substrate-CALL layer — both ends of the audit-envelope handoff
    # carry W607 plumbing, so a degradation on either side surfaces
    # via warnings_out instead of crashing.
    #
    # Marker family ``audit_*`` (canonical; same prefix as W607-P, but
    # disjoint phase-name sub-vocabulary so the two layers compose
    # without collision).
    _w607dm_warnings_out: list[str] = []

    def _run_check_dm(phase: str, fn, *args, default=None, **kwargs):
        """Run one W607-DM substrate-CALL with marker emission.

        Clean call returns the result as-is. On an uncaught raise,
        surface ``audit_<phase>_failed:<exc_class>:<detail>`` via
        ``_w607dm_warnings_out`` and substitute *default*. The envelope
        still emits the remaining substrates cleanly.

        ``default`` is returned VERBATIM on raise (including ``None``)
        so callers can distinguish a degraded-but-empty result (``{}``)
        from a degraded-no-output result (``None``). This is critical
        for any ``rendered is None``-style guard on the serialize_envelope
        path (matches the W607-DP / W607-DW canonical helper template).
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607dm_warnings_out.append(f"audit_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # Top-level scores so consumers can branch fast. Wrapped through
    # the W607-DM ``compute_scores`` substrate so a future refactor of
    # ``_summary_field`` (or a monkeypatch surfacing an unexpected
    # raise) does not crash the audit before the envelope composes.
    def _compute_scores():
        return {
            "health_score": _summary_field(health, "health_score", "score"),
            "debt_total": _summary_field(debt, "total_minutes", "estimated_minutes", "total"),
            "dead_count": _summary_field(dead, "dead_count", "total"),
            "danger_count": _summary_field(hotspots, "count", "danger_count", default=0),
            "pyramid_total": _summary_field(test_pyramid, "total", default=0),
            "api_count": _summary_field(api, "count", default=0),
            "file_total": _summary_field(stats, "file_total", default=0),
            "sym_total": _summary_field(stats, "symbol_total", default=0),
            "coverage_pct": _summary_field(health, "imported_coverage_pct"),
            "stale_ref_count": _summary_field(stale_refs, "stale_refs", "missing_targets", default=0),
        }

    _scores = _run_check_dm("compute_scores", _compute_scores, default={})
    # Per-key extraction defaults (W978 #6: degraded _scores still a dict).
    health_score = _scores.get("health_score") if isinstance(_scores, dict) else None
    debt_total = _scores.get("debt_total") if isinstance(_scores, dict) else None
    dead_count = _scores.get("dead_count") if isinstance(_scores, dict) else None
    danger_count = _scores.get("danger_count", 0) if isinstance(_scores, dict) else 0
    pyramid_total = _scores.get("pyramid_total", 0) if isinstance(_scores, dict) else 0
    api_count = _scores.get("api_count", 0) if isinstance(_scores, dict) else 0
    file_total = _scores.get("file_total", 0) if isinstance(_scores, dict) else 0
    sym_total = _scores.get("sym_total", 0) if isinstance(_scores, dict) else 0
    coverage_pct = _scores.get("coverage_pct") if isinstance(_scores, dict) else None
    stale_ref_count = _scores.get("stale_ref_count", 0) if isinstance(_scores, dict) else 0

    # Verdict — stack-rank the most pressing dimension. Wrapped through
    # the W607-DM ``compose_verdict`` substrate so a future refactor of
    # the f-string assembly (or a monkeypatched float-format raise) does
    # not torpedo the envelope.
    def _compose_verdict():
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
            return "AUDIT — pressures: " + ", ".join(pressures)
        return (
            f"AUDIT — health {health_score or '?'}/100, "
            f"{file_total} files, {sym_total} symbols, "
            f"{api_count} public-API symbols"
        )

    # W978 #1: verdict floor is a non-empty literal string so a degraded
    # compose_verdict still satisfies LAW 6.
    verdict = _run_check_dm("compose_verdict", _compose_verdict, default="AUDIT — verdict unavailable")

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

    # Section assembly — wrapped through the W607-DM ``assemble_sections``
    # substrate so a future ``.get`` chain on a degraded sub-envelope
    # (W607-P would have already wrapped the substrate capture; W607-DM
    # adds defense in depth at the dict-build site) does not crash.
    def _assemble_sections():
        return {
            "health": health if not brief else {"summary": health.get("summary", {})},
            "debt": debt if not brief else {"summary": debt.get("summary", {})},
            "dead": dead if not brief else {"summary": dead.get("summary", {})},
            "test_pyramid": (test_pyramid if not brief else {"summary": test_pyramid.get("summary", {})}),
            "hotspots_danger": (hotspots if not brief else {"summary": hotspots.get("summary", {})}),
            "stats": stats if not brief else {"summary": stats.get("summary", {})},
            "stale_refs": (stale_refs if not brief else {"summary": stale_refs.get("summary", {})}),
        }

    sections = _run_check_dm("assemble_sections", _assemble_sections, default={})

    # Unique-signal discovery hints (LAW 11: server-side hints teaching
    # better tools).  Several commands produce signal not available
    # elsewhere — surface them as imperative pointers so agents reading
    # the audit envelope discover them without scraping prose.  See
    # `the dogfood synthesis notes` section "NEW in v3".
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

    # W607-P: surface warnings_out only on the disclosure path so the
    # clean envelope stays byte-identical. Mirrors W607-N/O contract on
    # cmd_doctor / cmd_dashboard: top-level for `_ALWAYS_PRESERVED_LIST_FIELDS`
    # survival AND summary-mirror for consumers reading only the summary
    # block. partial_success flips True exactly when warnings_out is
    # non-empty (consumers can branch on summary.partial_success alone).
    envelope_kwargs = {
        "summary": summary,
        "sections": sections,
        "api_count": api_count,
        "discoverable_via": discoverable_via,
        "next_steps": next_steps,
    }
    # Merge both W607-P (capture-layer) and W607-DM (post-capture)
    # buckets onto the envelope. Pattern-2 guard: ANY marker in either
    # bucket flips partial_success=True.
    combined_warnings_out = list(_w607p_warnings_out) + list(_w607dm_warnings_out)
    if combined_warnings_out:
        summary["partial_success"] = True
        summary["warnings_out"] = list(combined_warnings_out)
        envelope_kwargs["warnings_out"] = list(combined_warnings_out)

    if json_mode:
        # W607-DM ``serialize_envelope`` substrate boundary. A raise in
        # ``json_envelope`` or ``to_json`` (e.g. a non-serializable
        # section payload) surfaces as the canonical marker; the
        # command still emits a minimal envelope on the degraded path.
        def _serialize_envelope():
            return to_json(json_envelope("audit", **envelope_kwargs))

        rendered = _run_check_dm("serialize_envelope", _serialize_envelope, default=None)
        # W978 #6: ``rendered is None`` guard before echo so a degraded
        # serialize_envelope does not crash on the print path.
        if rendered is None:
            # Re-surface the marker that the wrapper just appended so
            # consumers reading stdout see the disclosure.
            summary["partial_success"] = True
            summary["warnings_out"] = list(_w607p_warnings_out) + list(_w607dm_warnings_out)
            # Minimal hand-rolled fallback envelope (W607-DM safety net).
            click.echo(
                _json.dumps(
                    {
                        "command": "audit",
                        "summary": summary,
                        "warnings_out": summary["warnings_out"],
                    }
                )
            )
            return
        click.echo(rendered)
        return

    # Text path — wrapped through the W607-DM ``format_text`` substrate
    # so a raise during click.echo formatting (e.g. a __str__ raise on
    # a degraded numeric field) surfaces a marker rather than crashing.
    def _format_text():
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
        return None

    _run_check_dm("format_text", _format_text, default=None)
