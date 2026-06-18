"""Unified single-screen codebase status dashboard.

Combines health, hotspots, bus factor, dead symbols, and AI rot (vibe-check)
into a single concise view.  Queries the DB directly for speed -- no shelling
out to other commands.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because dashboard outputs are invocation-scoped health/hotspot
summaries — not per-location violations. Underlying detectors (health,
hotspots, bus-factor, dead, vibe-check) emit their own SARIF where it
fits. See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket
B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import sqlite3
import time

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Lightweight data collection helpers
# ---------------------------------------------------------------------------


def _overview(conn):
    """Basic project stats: files, symbols, edges, clusters, languages."""
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    try:
        cluster_count = conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM clusters").fetchone()[0]
    except sqlite3.Error:
        cluster_count = 0

    lang_rows = conn.execute(
        "SELECT language, COUNT(*) as cnt FROM files WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC"
    ).fetchall()
    languages = []
    for r in lang_rows:
        pct = round(r["cnt"] * 100 / files, 1) if files else 0
        languages.append({"name": r["language"], "files": r["cnt"], "pct": pct})

    # Index age
    try:
        from roam.db.connection import get_db_path

        db_path = get_db_path()
        if db_path.exists():
            index_age_s = int(time.time() - db_path.stat().st_mtime)
        else:
            index_age_s = None
    except Exception as _exc:  # noqa: BLE001 -- best-effort index-age probe; import/stat failures degrade to unknown age
        index_age_s = None

    return {
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "clusters": cluster_count,
        "languages": languages,
        "index_age_s": index_age_s,
    }


def _format_age(seconds):
    """Format seconds into a human-readable relative string."""
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _top_hotspots(conn, limit=5):
    """Top files by churn * complexity, annotated with bus factor."""
    rows = conn.execute(
        "SELECT fs.file_id, f.path, fs.total_churn, fs.complexity, "
        "fs.commit_count, fs.distinct_authors "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.total_churn > 0 "
        "ORDER BY fs.total_churn DESC "
        "LIMIT ?",
        (limit * 2,),  # over-fetch to filter tests
    ).fetchall()

    results = []
    for r in rows:
        path = r["path"]
        # skip test files
        base = path.replace("\\", "/").split("/")[-1].lower()
        if base.startswith("test_") or base.endswith("_test.py"):
            continue

        # Bus factor: count distinct authors for this file
        authors = r["distinct_authors"] or 1

        results.append(
            {
                "path": path,
                "churn": r["total_churn"] or 0,
                "complexity": round(r["complexity"] or 0, 0),
                "bus_factor": authors,
            }
        )
        if len(results) >= limit:
            break

    return results


def _risk_areas(conn):
    """Compute key risk indicators from DB."""
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 1

    # Bus factor 1 files (files with only 1 distinct author)
    try:
        bf1_count = conn.execute("SELECT COUNT(*) FROM file_stats WHERE distinct_authors = 1").fetchone()[0]
    except sqlite3.Error:
        bf1_count = 0
    bf1_pct = round(bf1_count * 100 / total_files, 1)

    # Dead symbols (high confidence only -- exported symbols with no callers)
    try:
        from roam.db.queries import UNREFERENCED_EXPORTS

        dead_rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
        # Filter test files
        dead_count = sum(
            1
            for r in dead_rows
            if not r["file_path"].replace("\\", "/").split("/")[-1].lower().startswith("test_")
            and not r["file_path"].replace("\\", "/").split("/")[-1].lower().endswith("_test.py")
        )
    except Exception as _exc:  # noqa: BLE001 -- dead-symbol probe spans import + query + row parsing; any failure degrades to 0
        dead_count = 0

    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 1
    dead_pct = round(dead_count * 100 / total_symbols, 1)

    # Cycles (SCCs)
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        cycles = find_cycles(G)
        cycle_count = len(cycles)
    except Exception as _exc:  # noqa: BLE001 -- graph build + cycle detection may raise networkx/import errors; degrade to 0 cycles
        cycle_count = 0

    return {
        "bus_factor_1_files": bf1_count,
        "bus_factor_1_pct": bf1_pct,
        "dead_symbols": dead_count,
        "dead_pct": dead_pct,
        "cycles": cycle_count,
        "total_files": total_files,
    }


def _vibe_check_canonical(conn):
    """Compute AI rot via the canonical 8-pattern algorithm.

    Pattern 3 reconciliation (W16.3): previously this function ran a
    2-pattern approximation (dead exports + hallucinated imports only)
    that produced a DIFFERENT number from ``roam vibe-check`` on the
    same codebase — flagged in the 212-eval corpus as the "AI rot 7
    vs 4" mismatch. We now delegate to ``roam.quality.ai_rot`` which
    runs the FULL 8-detector pipeline, the same code vibe-check uses.

    Cost: a few hundred ms on roam-sized repos (file-scanning detectors
    for empty handlers, stubs, comments, copy-paste). Acceptable price
    for one canonical number across the suite.

    Returns ``None`` on any failure so the dashboard never crashes on a
    partial / corrupt index.
    """
    try:
        from roam.quality.ai_rot import compute_ai_rot_score
    except ImportError:
        # ai_rot is an optional quality module — absence is expected.
        return None

    try:
        result = compute_ai_rot_score(conn)
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_dashboard:ai_rot_score", _exc)
        return None

    # Build the categories list dashboard displayed previously, now
    # populated from the full 8-pattern breakdown rather than the
    # 2-pattern approximation. Keep the same shape so JSON consumers
    # don't break.
    categories: list[dict] = []
    for key, pdata in result.patterns.items():
        if pdata["found"] > 0:
            categories.append({"name": pdata["label"], "count": pdata["found"]})
    # Sort highest-count first; cap to top 5 to keep dashboard compact.
    categories.sort(key=lambda c: c["count"], reverse=True)
    categories = categories[:5]

    return {
        "score": result.score,
        "severity": result.severity,
        "total_issues": result.total_issues,
        "categories": categories,
        # ``approximate`` was true under the old 2-pattern computation.
        # Now we delegate to the canonical detector so it's exact.
        "approximate": False,
        # Pattern 3 label fix — every envelope reporting an AI rot
        # number carries the definition string so downstream consumers
        # confirm both commands agree on the same metric.
        "ai_rot_definition": result.definition,
    }


# Back-compat alias so any external caller / test that imported the old
# name keeps working. Same behaviour as ``_vibe_check_canonical``.
_vibe_check_fast = _vibe_check_canonical


def _health_label(score):
    """Map health score to a label."""
    if score >= 80:
        return "HEALTHY"
    elif score >= 60:
        return "FAIR"
    elif score >= 40:
        return "NEEDS ATTENTION"
    else:
        return "UNHEALTHY"


# ---------------------------------------------------------------------------
# Unique-signal discovery hints (LAW 11 — server-side hints teaching better
# tools).  Several commands produce signal not available anywhere else; agents
# never discover them by name.  Surface them here as imperative pointers
# rather than replicating their full output (which would blow the response
# budget).  See `the dogfood synthesis notes` section "NEW in v3".
# ---------------------------------------------------------------------------


def _unique_signal_hints() -> dict:
    """Map unique-signal metric name -> imperative roam command that exposes it.

    Each entry is copy-paste-executable (CONSTRAINT 12) so an agent that
    skims only the verdict / discoverable_via block has a literal command
    to run for the underlying detail.
    """
    return {
        "danger_score": "roam metrics-push --dry-run",
        "algo_anti_patterns": "roam algo",
        "ai_generated_percentage": "roam ai-ratio",
        "ai_readiness_score": "roam ai-readiness",
        "ai_rot_score": "roam vibe-check",
        "module_cohesion_pct": "roam module <module>",
        "health_30d_forecast": "roam forecast",
    }


def _top_danger_files(conn, limit: int = 5) -> list[dict]:
    """Cheap top-N approximation of the ``hotspots --danger`` ranking.

    Uses the same DB columns the full danger-zone computation reads
    (``file_stats`` + max ``graph_metrics.in_degree`` per file) but
    skips the p75 thresholding — we just rank by churn × complexity ×
    max_fan_in for the headline.  Filters to source files and excludes
    tests.  Returns ``[]`` on any DB error so dashboard never crashes
    on a partial / corrupt index.

    Single SQL query, ~ms-scale even on roam-sized repos.  Safe to call
    inline from ``roam dashboard``.
    """
    try:
        rows = conn.execute(
            """
            SELECT f.path,
                   COALESCE(fs.total_churn, 0) AS churn,
                   COALESCE(fs.complexity, 0)  AS complexity,
                   (SELECT COALESCE(MAX(gm.in_degree), 0)
                      FROM symbols s
                      JOIN graph_metrics gm ON gm.symbol_id = s.id
                     WHERE s.file_id = f.id) AS max_fan_in
              FROM files f
              LEFT JOIN file_stats fs ON fs.file_id = f.id
             WHERE COALESCE(f.file_role, 'source') = 'source'
               AND COALESCE(fs.total_churn, 0)  > 0
               AND COALESCE(fs.complexity, 0)   > 0
            """
        ).fetchall()
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_dashboard:hotspot_query", _exc)
        return []

    out: list[dict] = []
    for r in rows:
        churn = r["churn"] or 0
        complexity = r["complexity"] or 0.0
        fan_in = r["max_fan_in"] or 0
        if fan_in <= 0:
            continue
        # Same shape as `roam metrics-push --dry-run` hotspots block:
        # raw churn × complexity × fan_in (LAW 4: concrete nouns, not
        # normalized scores).  Agents can still rank by this number.
        score = churn * complexity * fan_in
        out.append(
            {
                "path": r["path"],
                "danger_score": round(score, 1),
                "churn": churn,
                "complexity": round(complexity, 1),
                "max_fan_in": fan_in,
            }
        )

    out.sort(key=lambda d: d["danger_score"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="dashboard",
    category="exploration",
    summary="Unified codebase status: health, hotspots, debt, bus factor, AI rot.",
    inputs=["repo_path"],
    outputs=["overview", "health", "hotspots", "verdict"],
    examples=["roam dashboard"],
    tags=["overview", "health"],
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
@click.command("dashboard")
@click.pass_context
def dashboard(ctx):
    """Unified codebase status: health, hotspots, debt, bus factor, AI rot.

    Unlike running individual commands, this command aggregates health, hotspots,
    debt, bus factor, and vibe-check signals into a single overview with
    approximate scoring.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-O: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the dashboard aggregator. cmd_dashboard is the DB-shape
    # aggregator that consumes overview / health (collect_metrics) /
    # hotspots / risks / vibe-check / danger-zone substrates. Several
    # of these helpers ALREADY have internal try/except returning safe
    # fallbacks (None / [] / floor-zero values), but a helper itself
    # can still raise BEFORE reaching that floor (e.g., a downstream
    # substrate refactor changes the SQL shape, or networkx blows up
    # during build_symbol_graph in _risk_areas). The outer call sites
    # in dashboard() have no guards, so the envelope crashes whole.
    #
    # W607-O wraps each helper call with a single try/except that
    # emits ``dashboard_<phase>_failed:<exc>:<detail>`` markers via
    # ``warnings_out`` and falls back to a safe default — the envelope
    # still emits cleanly with the remaining sections.
    #
    # Marker family ``dashboard_*`` — distinct from W607-N's ``doctor_*``
    # (environment aggregator), W607-M's ``health_*`` (CI-gate flagship),
    # W607-L's ``minimap_*``, W607-K's ``describe_*`` (aggregator), and
    # the W607-G/H/I/J subprocess families. The marker-prefix discipline
    # keeps each consumer's scope identifiable downstream.
    #
    # Empty bucket → byte-identical envelope (no warnings_out key in
    # either ``summary`` or top-level, no partial_success key).
    _w607o_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None):
        """Run one substrate helper with W607-O marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``dashboard_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607o_warnings_out`` and return *default* — the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607o_warnings_out.append(f"dashboard_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DP: producer-side substrate-CALL plumbing LAYERED on top of
    # the W607-O capture-layer wrap (which guards the 7 helper-call
    # boundaries above). W607-DP extends the marker family to the
    # POST-capture substrate boundaries — score assembly, verdict
    # composition, section dict-build, envelope serialization, and
    # text formatting — so a raise inside the f-string verdict
    # construction, the JSON envelope kwargs build, or ``to_json`` no
    # longer torpedoes the dashboard envelope without lineage.
    #
    # Marker family ``dashboard_*`` (canonical; same prefix as W607-O,
    # but DISJOINT phase-name sub-vocabulary so the two layers compose
    # without collision):
    #   - W607-O phases: overview / collect_metrics / hotspots /
    #     risk_areas / vibe_check / discoverable_via / danger_top
    #   - W607-DP phases: compute_scores / compose_verdict /
    #     assemble_sections / serialize_envelope / format_text
    #
    # Combined-warnings discipline: ``summary.warnings_out`` mirrors
    # the top-level ``warnings_out``; both equal
    # ``_w607o_warnings_out + _w607dp_warnings_out``. ``partial_success``
    # flips True on any non-empty bucket. Empty buckets → byte-identical
    # clean envelope.
    _w607dp_warnings_out: list[str] = []

    def _run_check_dp(phase: str, fn, *args, default=None, **kwargs):
        """Run one W607-DP substrate-CALL with marker emission.

        Clean call returns the result as-is. On an uncaught raise,
        surface ``dashboard_<phase>_failed:<exc_class>:<detail>`` via
        ``_w607dp_warnings_out`` and substitute *default*. The envelope
        still emits the remaining substrates cleanly.

        ``default`` is returned VERBATIM on raise (including ``None``)
        so callers can distinguish a degraded-but-empty result (``{}``)
        from a degraded-no-output result (``None``). This is critical
        for the ``serialize_envelope`` phase whose ``rendered is None``
        guard precedes the minimal-fallback echo (W978 #6).
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607dp_warnings_out.append(f"dashboard_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # -- Overview --
        overview = _run_check(
            "overview",
            _overview,
            conn,
            default={
                "files": 0,
                "symbols": 0,
                "edges": 0,
                "clusters": 0,
                "languages": [],
                "index_age_s": None,
            },
        )

        # -- Health (reuse collect_metrics for consistency with health cmd) --
        from roam.commands.metrics_history import collect_metrics

        health = _run_check("collect_metrics", collect_metrics, conn, default={})

        # -- Top hotspots --
        hotspots = _run_check("hotspots", _top_hotspots, conn, default=[]) or []

        # -- Risk areas --
        risks = _run_check(
            "risk_areas",
            _risk_areas,
            conn,
            default={
                "bus_factor_1_files": 0,
                "bus_factor_1_pct": 0,
                "dead_symbols": 0,
                "dead_pct": 0,
                "cycles": 0,
                "total_files": 0,
            },
        )

        # -- Vibe-check (canonical 8-pattern algorithm via roam.quality.ai_rot) --
        # Pattern 3 reconciliation: this used to run a 2-pattern
        # approximation that disagreed with `roam vibe-check`. Now
        # delegates to the same code path vibe-check uses.
        vibe = _run_check("vibe_check", _vibe_check_canonical, conn, default=None)

        # -- Unique-signal discovery (LAW 11: server-side hints) --
        # `discoverable_via` is the canonical block; `danger_score_top_5`
        # is the one inline-cheap headline pulled from the same DB
        # columns the full `metrics-push` / `hotspots --danger` chain reads.
        discoverable_via = _run_check("discoverable_via", _unique_signal_hints, default={})
        danger_top = _run_check("danger_top", _top_danger_files, conn, 5, default=[]) or []

        # -- Build verdict --
        # W607-O: ``health`` may be the empty default ``{}`` if
        # collect_metrics raised — guard the score read so the envelope
        # still emits with a floor 0/UNHEALTHY label and the marker
        # disclosure carries the lineage.
        #
        # W607-DP ``compute_scores`` substrate boundary: assemble
        # health-score + label across capture results in one wrapped
        # call so a future refactor of ``health.get`` chain (or a
        # monkeypatched __getitem__ raise on a degraded ``vibe`` dict)
        # surfaces a canonical marker instead of crashing.
        def _compute_scores():
            hs_local = health.get("health_score", 0) if isinstance(health, dict) else 0
            h_label_local = _health_label(hs_local)
            return {"hs": hs_local, "h_label": h_label_local}

        _scores = _run_check_dp("compute_scores", _compute_scores, default={"hs": 0, "h_label": "UNHEALTHY"})
        # W978 #6: degraded _scores still a dict (default is a literal).
        hs = _scores.get("hs", 0) if isinstance(_scores, dict) else 0
        h_label = _scores.get("h_label", "UNHEALTHY") if isinstance(_scores, dict) else "UNHEALTHY"

        # W607-DP ``compose_verdict`` substrate boundary: the LAW 6
        # single-line floor lives here. A raise inside the f-string (e.g.
        # a poisoned ``vibe['score']`` lookup, monkeypatched __getitem__,
        # or downstream f-string format-spec failure) returns the literal
        # floor verdict instead of crashing.
        def _compose_verdict():
            vibe_part = ""
            if vibe is not None:
                vibe_part = f", AI rot {vibe['score']}/100"
            return f"Codebase is {h_label} (health {hs}/100{vibe_part})"

        # W978 #1: verdict floor is a non-empty literal string so a
        # degraded compose_verdict still satisfies LAW 6.
        verdict = _run_check_dp(
            "compose_verdict",
            _compose_verdict,
            default="DASHBOARD — verdict unavailable",
        )

        # -- JSON output --
        if json_mode:
            # W607-DP ``assemble_sections`` substrate boundary: build
            # the summary + envelope_kwargs in one wrapped call. A
            # ``.get`` chain on a degraded sub-envelope (W607-O would
            # already have wrapped the substrate capture; W607-DP adds
            # defense in depth at the dict-build site) does not crash.
            def _assemble_sections():
                unique_signals = {
                    # Concrete numeric headline that callers can act on; the
                    # full per-file list lives behind `roam metrics-push --dry-run`
                    # (linked via discoverable_via).  Empty list is a valid
                    # signal (no danger-zone files) — never crash on it.
                    "danger_score_top_5": danger_top,
                    # Server-side teaching block: tells agents *which command*
                    # produces each metric they'd otherwise have to guess at.
                    "discoverable_via": discoverable_via,
                }
                # Pattern 3 reconciliation (W16.3): expose the AI rot score
                # at a top-level path AND attach the canonical definition
                # label. Old consumers continue reading ``vibe_check.score``;
                # new consumers can rely on ``summary.ai_rot_score`` plus
                # ``summary.ai_rot_definition`` to confirm they're seeing
                # the canonical 8-pattern number.
                ai_rot_score_top = vibe["score"] if vibe is not None else None
                ai_rot_definition_top = vibe.get("ai_rot_definition") if vibe is not None else None

                _summary_block = {
                    "verdict": verdict,
                    "health_score": hs,
                    "files": overview["files"],
                    "symbols": overview["symbols"],
                    "edges": overview["edges"],
                    "danger_zone_count": len(danger_top),
                }
                if ai_rot_score_top is not None:
                    _summary_block["ai_rot_score"] = ai_rot_score_top
                if ai_rot_definition_top is not None:
                    _summary_block["ai_rot_definition"] = ai_rot_definition_top

                # W805-PP (Pattern-2): a 0-symbol corpus must NOT read as a clean
                # HEALTHY bill — there is nothing indexed to be healthy about
                # (uncoded / not yet written / index broken / wrong cwd). The
                # numeric health-band verdict ("Codebase is HEALTHY 100/100") is
                # a silent SAFE here. Disclose the empty corpus explicitly via
                # the canonical state + partial_success + an empty-naming verdict,
                # matching cmd_health's guard and the shared empty_corpus_state.
                if overview["symbols"] == 0:
                    _summary_block["verdict"] = (
                        "Codebase has 0 symbols indexed (empty corpus — run `roam index --force`)"
                    )
                    _summary_block["state"] = "empty_corpus"
                    _summary_block["partial_success"] = True

                _envelope_kwargs: dict = {
                    "overview": overview,
                    "health": {
                        "score": hs,
                        "label": h_label,
                        "label_axis": "project_health_score",
                        "label_axis_definition": (
                            "Project-health label derived from composite health "
                            "score (0-100, higher = healthier). Bands: HEALTHY "
                            ">=80, FAIR >=60, NEEDS ATTENTION >=40, UNHEALTHY <40. "
                            "NOT the same axis as vibe-check's severity label "
                            "(rot-axis, 0-100 lower = healthier)."
                        ),
                        "tangle_ratio": health.get("tangle_ratio", 0),
                        "cycles": health.get("cycles", 0),
                        "god_components": health.get("god_components", 0),
                        "bottlenecks": health.get("bottlenecks", 0),
                        "dead_exports": health.get("dead_exports", 0),
                        "layer_violations": health.get("layer_violations", 0),
                        "avg_complexity": health.get("avg_complexity", 0),
                    },
                    "hotspots": [
                        {
                            "path": h["path"],
                            "churn": h["churn"],
                            "complexity": h["complexity"],
                            "bus_factor": h["bus_factor"],
                        }
                        for h in hotspots
                    ],
                    "risks": risks,
                    "vibe_check": vibe,
                    "unique_signals": unique_signals,
                    # `next_steps` is consumed by the formatter's
                    # ``_derive_agent_contract`` and surfaces as
                    # ``agent_contract.next_commands`` — copy-paste-executable
                    # roam invocations agents on tight context can follow
                    # without re-reading the envelope.  Order matters: most
                    # broadly-useful unique signals first.
                    "next_steps": [
                        "roam vibe-check",
                        "roam ai-readiness",
                        "roam ai-ratio",
                        "roam algo",
                        "roam forecast",
                    ],
                }
                return {"summary_block": _summary_block, "envelope_kwargs": _envelope_kwargs}

            # Floor on degrade: a minimal summary + empty kwargs so the
            # serialize_envelope substrate still has structurally valid
            # input. The verdict literal floor preserved separately.
            _assembled = _run_check_dp(
                "assemble_sections",
                _assemble_sections,
                default={
                    "summary_block": {"verdict": verdict},
                    "envelope_kwargs": {},
                },
            )
            summary_block = (
                _assembled.get("summary_block", {"verdict": verdict})
                if isinstance(_assembled, dict)
                else {"verdict": verdict}
            )
            envelope_kwargs: dict = _assembled.get("envelope_kwargs", {}) if isinstance(_assembled, dict) else {}

            # W607-O + W607-DP: surface combined warnings_out on the
            # disclosure path. Both buckets feed the SAME envelope keys
            # so consumers reading either ``summary.warnings_out`` or
            # top-level ``warnings_out`` see the full lineage. Empty
            # combined bucket → byte-identical clean envelope (no new
            # keys, no partial_success flip).
            combined_warnings_out = list(_w607o_warnings_out) + list(_w607dp_warnings_out)
            if combined_warnings_out:
                summary_block["partial_success"] = True
                summary_block["warnings_out"] = list(combined_warnings_out)
                envelope_kwargs["warnings_out"] = list(combined_warnings_out)

            # W17.2 / Pattern 3c: name the axis the health label measures
            # so consumers never confuse it with vibe-check's rot-axis
            # severity (which also uses "HEALTHY" but on a different scale).
            #
            # W607-DP ``serialize_envelope`` substrate boundary: a raise
            # in ``json_envelope`` or ``to_json`` (e.g. a
            # non-serializable section payload) surfaces as the canonical
            # marker; the command still emits a minimal envelope on the
            # degraded path.
            def _serialize_envelope():
                return to_json(
                    json_envelope(
                        "dashboard",
                        budget=budget,
                        summary=summary_block,
                        **envelope_kwargs,
                    )
                )

            rendered = _run_check_dp("serialize_envelope", _serialize_envelope, default=None)
            # W978 #6: ``rendered is None`` guard before echo so a
            # degraded serialize_envelope does not crash on the print
            # path.
            if rendered is None:
                import json as _json_fallback

                # Re-surface the markers the wrapper just appended so
                # consumers reading stdout see the disclosure.
                summary_block["partial_success"] = True
                summary_block["warnings_out"] = list(_w607o_warnings_out) + list(_w607dp_warnings_out)
                click.echo(
                    _json_fallback.dumps(
                        {
                            "command": "dashboard",
                            "summary": summary_block,
                            "warnings_out": summary_block["warnings_out"],
                        }
                    )
                )
                return
            click.echo(rendered)
            return

        # -- Text output (<40 lines) --
        # W607-DP ``format_text`` substrate boundary: a raise during
        # click.echo formatting (e.g. a __str__ raise on a degraded
        # numeric field, a missing key on a degraded ``overview`` /
        # ``risks`` / ``vibe`` dict) surfaces a marker rather than
        # crashing.
        def _format_text():
            click.echo(f"VERDICT: {verdict}")
            click.echo()

            # === Overview ===
            lang_parts = []
            for lang in overview["languages"][:4]:
                lang_parts.append(f"{lang['name']} {lang['pct']:.0f}%")
            if len(overview["languages"]) > 4:
                lang_parts.append(f"+{len(overview['languages']) - 4} more")
            lang_str = ", ".join(lang_parts) if lang_parts else "none"

            click.echo("  === Overview ===")
            click.echo(f"  Files: {overview['files']} ({lang_str})")
            click.echo(
                f"  Symbols: {overview['symbols']} | Edges: {overview['edges']} | Clusters: {overview['clusters']}"
            )
            click.echo(f"  Last indexed: {_format_age(overview['index_age_s'])}")
            click.echo()

            # === Health ===
            click.echo("  === Health ===")
            click.echo(f"  Score: {hs}/100 ({h_label})")
            click.echo(
                f"  Tangle ratio: {health.get('tangle_ratio', 0)}"
                f" | Avg complexity: {health.get('avg_complexity', 0)}"
                f" | Dead symbols: {risks['dead_symbols']}"
            )
            click.echo()

            # === Top Hotspots ===
            if hotspots:
                click.echo("  === Top Hotspots (change with care) ===")
                for i, h in enumerate(hotspots, 1):
                    click.echo(
                        f"  {i}. {h['path']:<40s}"
                        f" churn:{h['churn']:<5d}"
                        f" complexity:{int(h['complexity']):<4d}"
                        f" bus-factor:{h['bus_factor']}"
                    )
                click.echo()

            # === Risk Areas ===
            click.echo("  === Risk Areas ===")
            click.echo(f"  Bus factor 1: {risks['bus_factor_1_files']} files ({risks['bus_factor_1_pct']}%)")
            click.echo(f"  Dead symbols: {risks['dead_symbols']} ({risks['dead_pct']}%)")
            click.echo(f"  Cycles: {risks['cycles']} SCCs")
            click.echo()

            # === AI Rot ===
            if vibe is not None and vibe["total_issues"] > 0:
                click.echo("  === AI Rot (vibe-check) ===")
                cat_parts = []
                for cat in vibe["categories"]:
                    cat_parts.append(f"{cat['name']} ({cat['count']})")
                cats_str = ", ".join(cat_parts) if cat_parts else "none"
                approx_note = " (approximate)" if vibe.get("approximate") else ""
                click.echo(
                    f"  Score: {vibe['score']}/100 ({vibe['severity']}){approx_note} | {vibe['total_issues']} issues"
                )
                click.echo(f"  Top: {cats_str}")
                click.echo()

            # === Unique signals (discovery hints, LAW 11) ===
            # Several commands produce signal NOT available anywhere else; surface
            # the headline + the command name so agents discover them without
            # scraping prose.  Compact: one line each, only show non-zero.
            if danger_top:
                top = danger_top[0]
                click.echo("  === Unique signals ===")
                click.echo(
                    f"  Top danger-zone file: {top['path']} "
                    f"(score={top['danger_score']}) — run `roam metrics-push --dry-run` for full list"
                )
                click.echo()

            click.echo("  Run `roam health`, `roam hotspots`, `roam vibe-check` for details.")
            click.echo("  Discover more: `roam algo` (anti-patterns), `roam ai-readiness` (agent-readiness),")
            click.echo("    `roam ai-ratio` (AI-generated %), `roam forecast` (30d health projection),")
            click.echo("    `roam module <dir>` (cohesion %).")
            return None

        _run_check_dp("format_text", _format_text, default=None)
