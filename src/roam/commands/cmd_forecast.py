"""Predict when per-symbol metrics will exceed thresholds by analyzing trends.

Uses Theil-Sen regression on snapshot history for aggregate metric forecasting
and ranks symbols by cognitive complexity * churn rate for per-symbol risk.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because forecast outputs are invocation-scoped trend predictions
— not per-location violations. Editor consumers should use the JSON
envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, json_envelope, to_json
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION

# ---------------------------------------------------------------------------
# Thresholds for aggregate snapshot metrics
# ---------------------------------------------------------------------------

_THRESHOLDS = {
    "health_score": {"warning": 60, "critical": 40, "higher_is_better": True},
    "avg_complexity": {"warning": 20, "critical": 30, "higher_is_better": False},
    "cycles": {"warning": 5, "critical": 10, "higher_is_better": False},
    "brain_methods": {"warning": 5, "critical": 10, "higher_is_better": False},
    "god_components": {"warning": 3, "critical": 5, "higher_is_better": False},
    "dead_exports": {"warning": 20, "critical": 50, "higher_is_better": False},
}

# Minimum absolute slope to consider a metric "trending" at all.
_MIN_ABS_SLOPE = 0.05


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_status(current, slope, horizon, metric_cfg):
    """Return one of: stable / trending / warning / alert.

    For higher_is_better metrics, alert when forecast drops below thresholds.
    For lower_is_better metrics, alert when forecast rises above thresholds.
    """
    if abs(slope) < _MIN_ABS_SLOPE:
        return "stable"

    forecast_val = current + slope * horizon
    hib = metric_cfg["higher_is_better"]
    warn_thresh = metric_cfg["warning"]
    crit_thresh = metric_cfg["critical"]

    # Already exceeded the critical threshold right now
    if hib:
        if current <= crit_thresh:
            return "alert"
        if current <= warn_thresh:
            return "warning"
    else:
        if current >= crit_thresh:
            return "alert"
        if current >= warn_thresh:
            return "warning"

    # Will exceed threshold within the horizon?
    if hib:
        if forecast_val <= crit_thresh:
            return "alert"
        if forecast_val <= warn_thresh:
            return "warning"
    else:
        if forecast_val >= crit_thresh:
            return "alert"
        if forecast_val >= warn_thresh:
            return "warning"

    return "trending"


def _aggregate_forecasts(conn, horizon):
    """Compute Theil-Sen trend + forecast for each snapshot metric.

    Returns a list of dicts, one per tracked metric.  Skips metrics that
    have fewer than 4 non-None values (Theil-Sen requires n >= 4).
    """
    from roam.graph.anomaly import theil_sen_slope

    rows = conn.execute(
        "SELECT timestamp, health_score, avg_complexity, cycles, "
        "       god_components, bottlenecks, dead_exports, brain_methods "
        "FROM snapshots ORDER BY timestamp ASC"
    ).fetchall()

    if len(rows) < 3:
        return [], len(rows)

    results = []
    for metric, cfg in _THRESHOLDS.items():
        values = [r[metric] for r in rows if r[metric] is not None]
        if len(values) < 4:
            # Not enough history -- still report current value as stable
            current = values[-1] if values else None
            if current is None:
                continue
            results.append(
                {
                    "metric": metric,
                    "current": round(float(current), 2),
                    "slope": 0.0,
                    "forecast_value": round(float(current), 2),
                    "forecast_horizon": horizon,
                    "status": "stable",
                }
            )
            continue

        ts_result = theil_sen_slope(values)
        if ts_result is None:
            continue

        slope = ts_result["slope"]
        current = values[-1]
        forecast_val = current + slope * horizon
        status = _classify_status(current, slope, horizon, cfg)

        results.append(
            {
                "metric": metric,
                "current": round(float(current), 2),
                "slope": round(slope, 4),
                "forecast_value": round(forecast_val, 2),
                "forecast_horizon": horizon,
                "status": status,
            }
        )

    return results, len(rows)


def _at_risk_symbols(conn, symbol_filter, min_slope, limit=20):
    """Rank symbols by cognitive_complexity * normalized_churn.

    High-complexity code in high-churn files is most likely to degrade
    further because each commit has a chance of adding more complexity.

    Returns a list of dicts sorted by risk_score descending.
    """
    # Fetch symbol metrics
    sym_rows = conn.execute(
        """SELECT s.id, s.name, s.qualified_name, s.kind, f.path,
                  s.line_start, sm.cognitive_complexity
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN symbol_metrics sm ON sm.symbol_id = s.id
           WHERE s.kind IN ('function', 'method')
             AND sm.cognitive_complexity IS NOT NULL
             AND sm.cognitive_complexity > 0
           ORDER BY sm.cognitive_complexity DESC"""
    ).fetchall()

    if not sym_rows:
        return []

    # Fetch churn per file
    churn_rows = conn.execute("SELECT file_id, total_churn FROM file_stats").fetchall()
    churn_map = {r["file_id"]: (r["total_churn"] or 0) for r in churn_rows}

    # Fetch file id lookup
    file_id_rows = conn.execute("SELECT id, path FROM files").fetchall()
    file_id_map = {r["path"]: r["id"] for r in file_id_rows}

    # Compute max churn for normalization (avoid division by zero)
    max_churn = max(churn_map.values(), default=1) or 1

    results = []
    for r in sym_rows:
        # Apply symbol name filter
        if symbol_filter:
            name = r["name"] or ""
            qname = r["qualified_name"] or ""
            filt = symbol_filter.lower()
            if filt not in name.lower() and filt not in qname.lower():
                continue

        file_id = file_id_map.get(r["path"])
        churn = churn_map.get(file_id, 0)
        cc = float(r["cognitive_complexity"] or 0)
        churn_norm = churn / max_churn  # 0.0 – 1.0

        # Risk score: CC scaled by churn factor
        risk_score = cc * (1.0 + churn_norm)

        # Skip symbols below the min-slope proxy threshold
        # (min_slope acts as a minimum risk coefficient — symbols
        #  with negligible cc*churn are not worth reporting)
        if risk_score < min_slope * 10:
            continue

        results.append(
            {
                "name": r["name"],
                "qualified_name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "file": r["path"],
                "line": r["line_start"],
                "cognitive_complexity": round(cc, 1),
                "churn": churn,
                "risk_score": round(risk_score, 1),
            }
        )

    results.sort(key=lambda x: x["risk_score"], reverse=True)
    return results[:limit]


# Pattern-3a sidecar: name the precise computation behind the decay rate so a
# downstream consumer never confuses it with a count-metric slope.
_TOPOLOGY_DECAY_RATE_DEFINITION = (
    "fraction of the current spectral gap (algebraic connectivity, lambda2) lost per snapshot"
)


def _spectral_gap_series(conn):
    """Read the persisted per-snapshot spectral-gap series, oldest first.

    B8 (Option-A): ``snapshots.spectral_gap`` is populated by the snapshot
    writer (one gap per health snapshot). NULL rows — legacy snapshots
    written before the column landed — are skipped so a partial-history
    series is honest rather than zero-padded.
    """
    try:
        rows = conn.execute(
            "SELECT spectral_gap FROM snapshots WHERE spectral_gap IS NOT NULL ORDER BY timestamp ASC"
        ).fetchall()
    except Exception:
        return []
    return [float(r[0]) for r in rows]


def _spectral_forecast_block(conn, horizon):
    """Spectral-instability + decay block (B8, Option-A persisted series).

    Computes the one-shot spectral instability of the CURRENT file-level
    graph, then projects the *persisted* historical gap-per-snapshot series
    (``snapshots.spectral_gap``) forward via Theil-Sen. With >= 4 historical
    gaps this yields a real "<N> snapshots to structural failure" budget;
    with fewer, ``forecast_spectral_decay`` honestly reports
    ``insufficient_history`` rather than faking a flat trend.

    The series falls back to the single current gap when no history exists
    yet (fresh repo / pre-column index) so the block is never empty.

    scipy-optional lineage is preserved: ``spectral_gap`` returns a 0.0
    sentinel + RuntimeWarning on a missing eigensolver, and we flag
    ``compute_degraded`` so a degraded compute is disclosed, never silent.
    """
    from roam.graph.builder import build_file_graph
    from roam.graph.spectral_forecast import (
        decay_alert_wording,
        forecast_spectral_decay,
        spectral_instability,
    )

    file_graph = build_file_graph(conn)
    inst = spectral_instability(file_graph)

    # Project the persisted historical gap series. Append the live current
    # gap so the most-recent point reflects the freshly-built graph even
    # before the current run's snapshot row is written.
    series = _spectral_gap_series(conn)
    if not series:
        series = [inst.spectral_gap]
    elif series[-1] != inst.spectral_gap:
        series = [*series, inst.spectral_gap]
    fc = forecast_spectral_decay(series, horizon=horizon)

    # Loud-fallback lineage: a non-trivial single connected graph whose gap is
    # exactly 0.0 means the eigensolver is unavailable, not a real flat blob.
    compute_degraded = inst.node_count >= 2 and inst.component_count == 1 and inst.spectral_gap == 0.0

    block = {
        "instability": inst.to_dict(),
        "decay": fc.to_dict(),
        "alert_wording": decay_alert_wording(fc),
        "compute_degraded": compute_degraded,
        "topology_decay_rate_definition": _TOPOLOGY_DECAY_RATE_DEFINITION,
    }
    return inst, fc, block, compute_degraded


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="forecast",
    category="health",
    summary="Predict when metrics will exceed thresholds using trend analysis",
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
@click.command("forecast")
@click.option("--symbol", default=None, help="Filter to a specific symbol name")
@click.option(
    "--horizon",
    default=30,
    type=int,
    show_default=True,
    help="Look-ahead window in snapshots/commits",
)
@click.option(
    "--alert-only",
    "alert_only",
    is_flag=True,
    help="Show only metrics with non-stable status",
)
@click.option(
    "--min-slope",
    "min_slope",
    default=0.1,
    type=float,
    show_default=True,
    help="Minimum slope (or risk coefficient) to report",
)
@click.pass_context
def forecast(ctx, symbol, horizon, alert_only, min_slope):
    """Predict when metrics will exceed thresholds using trend analysis.

    Unlike ``trends`` (which shows current metric snapshots and sparklines),
    this command uses Theil-Sen regression to predict when metrics will
    cross threshold boundaries.

    Combines Theil-Sen regression on snapshot history (aggregate trends)
    with a churn-weighted complexity ranking (per-symbol risk) to surface
    the most likely future pain points.

    With --alert-only, only metrics trending toward warning/alert thresholds
    are shown, suppressing stable metrics from the output.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        agg_trends, n_snapshots = _aggregate_forecasts(conn, horizon)

        # Apply alert-only filter on aggregate trends
        if alert_only:
            agg_trends = [t for t in agg_trends if t["status"] != "stable"]

        # Apply min-slope filter on aggregate trends
        if min_slope > 0:
            agg_trends = [t for t in agg_trends if abs(t["slope"]) >= min_slope or t["status"] in ("warning", "alert")]

        at_risk = _at_risk_symbols(conn, symbol, min_slope)

        # W837 (Pattern 2 empty-corpus disclosure): forecast draws on three
        # data layers — snapshot history (aggregate trends), per-symbol
        # complexity/churn (at-risk ranking), and the current file graph
        # (spectral block). On a freshly-indexed but symbol-less corpus all
        # three are vacuous: zero snapshots, zero symbols, and a degenerate
        # 2-node file graph that ``spectral_instability`` flags as
        # ``is_failed`` (gap 0.0 < failure band) — producing a verdict that
        # reads like a real "failure band" finding. That is a silent
        # Pattern-2 success: an agent reading ``partial_success`` saw a clean
        # run when there was nothing to forecast. Probe ``symbols`` directly
        # so we distinguish "no data to forecast on" (empty corpus) from a
        # real repo that simply lacks >= 3 snapshots yet (legitimate
        # partial-history state the verdict already discloses honestly).
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

        # B8 (Option-B): one-shot spectral-instability block from the current
        # graph. Always computed (cheap) and carried in the envelope; --alert-only
        # gates its TEXT visibility, never the JSON payload.
        spec_inst, spec_fc, spectral_block, spectral_degraded = _spectral_forecast_block(conn, horizon)

    # Summary counts
    metrics_trending = sum(1 for t in agg_trends if t["status"] in ("trending", "warning", "alert"))
    symbols_at_risk = len(at_risk)

    # W837: empty-corpus disclosure. Zero symbols indexed means there is
    # genuinely nothing to forecast — no per-symbol complexity/churn risk,
    # and the file graph degenerates to disconnected nodes whose 0.0
    # spectral gap ``spectral_instability`` flags as ``is_failed`` (an
    # artefact, not a real architectural signal). ``roam index`` writes a
    # snapshot row even for an empty corpus, so the snapshot count is NOT a
    # reliable empty signal — gate purely on the symbol count. A repo WITH
    # symbols but < 3 snapshots is NOT empty-corpus; its verdict
    # ("insufficient snapshot history") is honest and keeps partial_success
    # unset.
    empty_corpus = symbol_count == 0

    # Build verdict
    parts = []
    if n_snapshots < 3:
        parts.append("insufficient snapshot history for aggregate trends")
    elif metrics_trending:
        parts.append(
            f"{metrics_trending} metric{'s' if metrics_trending != 1 else ''} "
            f"trending toward threshold{'s' if metrics_trending != 1 else ''}"
        )
    else:
        parts.append("all aggregate metrics stable")

    if symbols_at_risk:
        parts.append(f"{symbols_at_risk} symbol{'s' if symbols_at_risk != 1 else ''} at risk")
    else:
        parts.append("no high-risk symbols found")

    verdict = ", ".join(parts)

    # W837: on an empty corpus, replace the misleading composite verdict with
    # an explicit empty-state line that names the absent corpus (Pattern 2 /
    # Pattern-1 variant D). The spectral "failure band" clause is suppressed
    # because a degenerate 2-node graph carries no real forecast signal.
    if empty_corpus:
        verdict = (
            "no data to forecast — corpus empty "
            "(0 symbols indexed; run `roam index --force` over a populated "
            "repo, then `roam health` over time to accrue history)"
        )
    # B8: append a self-sufficient spectral clause so the combined verdict still
    # works standalone (LAW 6). When a persisted gap series produced a real
    # decay projection (warning/alert), surface the "<N> snapshots to
    # structural failure" budget — terminal-anchored on `snapshots` (LAW 4).
    elif spectral_degraded:
        verdict += "; spectral gap unavailable (eigensolver missing)"
    elif spec_fc.status in ("warning", "alert") and spec_fc.snapshots_to_failure is not None:
        verdict += (
            f"; structural failure projected within {spec_fc.snapshots_to_failure} "
            f"snapshots (spectral gap {spec_fc.current_gap:.3f}, "
            f"decaying {spec_fc.slope:+.4f}/snapshot)"
        )
    elif spec_inst.is_failed:
        verdict += (
            f"; spectral gap {spec_inst.spectral_gap:.3f} in the failure band across {spec_inst.node_count} nodes"
        )
    else:
        verdict += f"; spectral gap {spec_inst.spectral_gap:.3f} ({spec_inst.verdict.lower()}) across {spec_inst.node_count} nodes"

    # --- JSON output ---
    if json_mode:
        _summary: dict = {
            "verdict": verdict,
            "snapshots_available": n_snapshots,
            "metrics_trending": metrics_trending,
            "symbols_at_risk": symbols_at_risk,
            # W1298 Pattern-3a: at_risk_symbols[*].cognitive_complexity
            # is the raw symbol_metrics value — disclose the scorer.
            "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
            # B8 Pattern-3a: name the precise spectral decay-rate computation.
            "topology_decay_rate_definition": _TOPOLOGY_DECAY_RATE_DEFINITION,
        }
        # W837: stamp the empty-corpus state so consumers reading
        # ``summary.partial_success`` / ``summary.state`` can tell a vacuous
        # forecast from a real one. Closed-enum state: ``no_data``.
        if empty_corpus:
            _summary["partial_success"] = True
            _summary["state"] = "no_data"
        click.echo(
            to_json(
                json_envelope(
                    "forecast",
                    summary=_summary,
                    # LAW 4 (W17.3): the auto-derive renders
                    # ``symbols_at_risk`` as "N symbols at risk findings"
                    # (terminal "risk" isn't a concrete plural). Pin a
                    # clean fact set anchored on "forecast" + the verdict.
                    agent_contract={
                        "facts": [
                            verdict,
                            f"forecast scope: {n_snapshots} snapshot(s) available, "
                            f"{metrics_trending} metric(s) trending",
                            f"forecast risk: {symbols_at_risk} symbol(s) approaching complexity / churn thresholds",
                            # B8: terminal-anchored on `nodes` (concrete-noun) per LAW 4.
                            f"spectral forecast: gap {spec_inst.spectral_gap:.3f} "
                            f"({spec_inst.verdict.lower()}) across {spec_inst.node_count} nodes",
                        ],
                    },
                    budget=token_budget,
                    aggregate_trends=agg_trends,
                    at_risk_symbols=at_risk,
                    spectral_forecast=spectral_block,
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    # Aggregate trends section
    if n_snapshots < 3:
        click.echo(
            f"AGGREGATE TRENDS: insufficient snapshot history "
            f"({n_snapshots} snapshot{'s' if n_snapshots != 1 else ''} available, "
            f"need >= 3)"
        )
    else:
        click.echo(f"AGGREGATE TRENDS (from {n_snapshots} snapshots):")
        if not agg_trends:
            click.echo("  all metrics stable")
        else:
            for t in agg_trends:
                slope_str = f"{t['slope']:+.4f}/snapshot"
                forecast_note = f"forecast {t['forecast_value']:.1f} in {t['forecast_horizon']} snapshots"
                flag = ""
                if t["status"] == "warning":
                    flag = "  << WARNING"
                elif t["status"] == "alert":
                    flag = "  << ALERT"
                click.echo(f"  {t['metric']:<18s}  {t['current']:.1f}, slope {slope_str}, {forecast_note}{flag}")

    click.echo()

    # B8: spectral forecast section. --alert-only suppresses it when the
    # current topology is healthy (mirrors the stable-metric suppression above).
    spectral_noteworthy = spectral_degraded or spec_inst.is_failed or spec_fc.status in ("warning", "alert")
    if not alert_only or spectral_noteworthy:
        click.echo("SPECTRAL FORECAST (modular separation of the current graph):")
        if spectral_degraded:
            click.echo("  spectral gap unavailable -- eigensolver missing (install scipy)")
        else:
            flag = "  << ALERT" if spec_inst.is_failed else ""
            click.echo(
                f"  gap {spec_inst.spectral_gap:.3f} ({spec_inst.verdict}) "
                f"across {spec_inst.node_count} nodes, {spec_inst.component_count} component(s){flag}"
            )
            # B8 Option-A: the decay projection over the persisted gap series.
            decay_flag = ""
            if spec_fc.status == "warning":
                decay_flag = "  << WARNING"
            elif spec_fc.status == "alert":
                decay_flag = "  << ALERT"
            if spec_fc.status == "insufficient_history":
                click.echo(
                    f"  decay: {spec_fc.history_points} historical gap(s) -- need >= 4 snapshots for a decay projection"
                )
            else:
                budget = (
                    f"{spec_fc.snapshots_to_failure} snapshots to structural failure"
                    if spec_fc.snapshots_to_failure is not None
                    else "no structural failure within horizon"
                )
                click.echo(
                    f"  decay: slope {spec_fc.slope:+.4f}/snapshot over "
                    f"{spec_fc.history_points} snapshots, {budget}{decay_flag}"
                )
            click.echo(f"  {spectral_block['alert_wording']}")
        click.echo()

    # At-risk symbols section
    if symbol:
        click.echo(f"AT-RISK SYMBOLS (filtered to '{symbol}'):")
    else:
        click.echo("AT-RISK SYMBOLS (high complexity in high-churn files):")

    if not at_risk:
        click.echo("  no high-risk symbols found")
    else:
        for s in at_risk:
            kind_abbr = abbrev_kind(s["kind"])
            fname = os.path.basename(s["file"])
            line = s["line"] or 0
            click.echo(
                f"  {kind_abbr} {s['name']:<30s}  "
                f"CC={s['cognitive_complexity']:.0f}  "
                f"churn={s['churn']}  "
                f"score={s['risk_score']:.1f}  "
                f"{fname}:{line}"
            )
