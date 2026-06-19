"""Root cause analysis for a failing symbol.

Given a symbol suspected to be involved in a bug, ranks likely root
causes by combining four signals no other tool brings together:
(1) call graph proximity, (2) git churn, (3) cognitive complexity,
(4) co-change history with the failing symbol.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because diagnose outputs are invocation-scoped root-cause rankings
(upstream[], downstream[], cochange_partners) tied to a target — not
per-location violations. Ranked suspect lists do not map to file:line
SARIF results. See action.yml _SUPPORTED_SARIF allowlist and W1154 audit memo.
"""

from __future__ import annotations

import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index, find_symbol_with_alternatives, symbol_not_found
from roam.db.connection import open_db
from roam.graph.builder import build_symbol_graph
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION

# W607-BH -- canonical risk-LEVEL projection imports + auto_log for the
# agent-OS pre-edit triangle parity with cmd_preflight (W607-R/AW) and
# cmd_impact (W607-T/BB). cmd_diagnose joins the triangle with the same
# canonical severity vocabulary + run-ledger emission so all three
# pre-edit commands share one closed-enum risk-level alphabet and one
# auto-log boundary shape. Imports hoisted to module top-level so tests
# can monkeypatch at the ``cmd_diagnose.<attr>`` boundary the same way
# the W607-BB tests do on ``cmd_impact``.
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log


def _bfs_neighbors(G, start, depth: int, neighbors_fn) -> set:
    """Bounded BFS over a graph: collect every node reachable from
    ``start`` within ``depth`` hops via the chosen direction
    (``G.predecessors`` for upstream callers, ``G.successors`` for
    downstream callees). The start node is excluded from the result.

    Used by ``cmd_diagnose`` for the upstream/downstream suspect walks;
    factored out so the two walks share one parameterised loop instead
    of two near-identical 5-level-deep nestings (R-deepnest 2026-05-23)."""
    out: set = set()
    frontier = {start}
    for _ in range(depth):
        next_frontier: set = set()
        for nid in frontier:
            if nid not in G:
                continue
            for nb in neighbors_fn(nid):
                if nb != start and nb not in out:
                    out.add(nb)
                    next_frontier.add(nb)
        frontier = next_frontier
    return out


def _get_symbol_metrics(conn, sym_id):
    """Fetch complexity and churn for a symbol."""
    sm = conn.execute(
        "SELECT cognitive_complexity, nesting_depth, line_count FROM symbol_metrics WHERE symbol_id = ?",
        (sym_id,),
    ).fetchone()

    gm = conn.execute(
        "SELECT pagerank, in_degree, out_degree, betweenness FROM graph_metrics WHERE symbol_id = ?",
        (sym_id,),
    ).fetchone()

    file_row = conn.execute(
        "SELECT fs.commit_count, fs.total_churn, fs.cochange_entropy, "
        "       fs.health_score, f.path, f.id AS file_id "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "LEFT JOIN file_stats fs ON f.id = fs.file_id "
        "WHERE s.id = ?",
        (sym_id,),
    ).fetchone()

    commits = (file_row["commit_count"] or 0) if file_row else 0
    churn = (file_row["total_churn"] or 0) if file_row else 0

    # v12.12 — git_file_changes fallback (dogfood #11). file_stats is
    # populated by ``compute_file_stats`` during a full re-index but can
    # lag behind incremental runs, leaving recently-modified files with
    # commit_count=0 even when their git history is rich. When that
    # happens, fall back to a direct COUNT over git_file_changes so the
    # Commits column carries signal again (and the risk score doesn't
    # silently lose its churn dimension).
    if file_row and commits == 0:
        try:
            fb = conn.execute(
                "SELECT COUNT(DISTINCT commit_id) AS cc, "
                "       COALESCE(SUM(lines_added + lines_removed), 0) AS chu "
                "FROM git_file_changes WHERE file_id = ?",
                (file_row["file_id"],),
            ).fetchone()
        except sqlite3.OperationalError:
            fb = None
        if fb:
            commits = fb["cc"] or 0
            if churn == 0:
                churn = fb["chu"] or 0

    return {
        "complexity": (sm["cognitive_complexity"] or 0) if sm else 0,
        "nesting": (sm["nesting_depth"] or 0) if sm else 0,
        "line_count": (sm["line_count"] or 0) if sm else 0,
        "pagerank": round((gm["pagerank"] or 0), 4) if gm else 0,
        "in_degree": (gm["in_degree"] or 0) if gm else 0,
        "out_degree": (gm["out_degree"] or 0) if gm else 0,
        "betweenness": round((gm["betweenness"] or 0), 3) if gm else 0,
        "commits": commits,
        "churn": churn,
        "entropy": round((file_row["cochange_entropy"] or 0), 2) if file_row else 0,
        "health": (file_row["health_score"] or 0) if file_row else 0,
        "file_path": file_row["path"] if file_row else "",
    }


def _build_distribution_stats(conn):
    """Compute mean/stddev of key metrics across the codebase for z-scoring.

    Returns dict with {metric: (mean, stddev)} for adaptive normalization.
    """
    import math

    commit_rows = conn.execute("SELECT commit_count FROM file_stats WHERE commit_count IS NOT NULL").fetchall()
    cc_rows = conn.execute(
        "SELECT cognitive_complexity FROM symbol_metrics WHERE cognitive_complexity IS NOT NULL"
    ).fetchall()
    health_rows = conn.execute("SELECT health_score FROM file_stats WHERE health_score IS NOT NULL").fetchall()
    entropy_rows = conn.execute("SELECT cochange_entropy FROM file_stats WHERE cochange_entropy IS NOT NULL").fetchall()

    def _stats(values):
        if not values:
            return (1.0, 1.0)
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / max(n, 1)
        return (mean, max(math.sqrt(var), 0.01))

    return {
        "commits": _stats([r[0] for r in commit_rows]),
        "complexity": _stats([r[0] for r in cc_rows]),
        "health": _stats([r[0] for r in health_rows]),
        "entropy": _stats([r[0] for r in entropy_rows]),
    }


def _risk_score(metrics, dist_stats=None):
    """Compute a composite risk score for root-cause ranking.

    Higher = more likely to be a root cause.  Combines churn,
    complexity, low health, and co-change entropy.

    When *dist_stats* is provided (from ``_build_distribution_stats``),
    uses z-score normalization — each factor is measured in standard
    deviations from the codebase mean, making thresholds adaptive to
    any project.  Falls back to fixed normalization otherwise.
    """
    if dist_stats:

        def _z(value, key):
            mean, std = dist_stats[key]
            return max(0, (value - mean) / std)

        # Clip z-scores at 3σ then normalize to [0, 1]
        churn_norm = min(_z(metrics["commits"], "commits") / 3, 1.0)
        cc_norm = min(_z(metrics["complexity"], "complexity") / 3, 1.0)
        # Health is inverted: low health = high risk
        h_mean, h_std = dist_stats["health"]
        health_risk = max(0, min((h_mean - metrics["health"]) / max(h_std, 0.01) / 3, 1.0))
        entropy_risk = min(_z(metrics["entropy"], "entropy") / 3, 1.0)
    else:
        churn_norm = min(metrics["commits"] / 50, 1.0)
        cc_norm = min(metrics["complexity"] / 30, 1.0)
        health_risk = max(0, (7 - metrics["health"]) / 7) if metrics["health"] else 0.5
        entropy_risk = metrics["entropy"]

    return round(
        churn_norm * 0.30 + cc_norm * 0.30 + health_risk * 0.25 + entropy_risk * 0.15,
        3,
    )


# W607-BH -- domain risk-tier projection for cmd_diagnose's top
# suspect. ``_risk_score`` returns a [0, 1] float (churn/cc/health/
# entropy-weighted composite). Project onto the canonical W631 risk-
# LEVEL vocabulary so cross-command floor comparators ("is the top
# suspect's risk worse than cmd_impact's blast tier?") work without
# each consumer re-deriving the threshold table at the call site
# (Pattern-3a). Thresholds chosen to mirror cmd_impact's polarity:
#
#   risk_score >= 0.70  -> "high"   (blocker-tier suspect)
#   risk_score >= 0.40  -> "medium" (review-tier suspect)
#   risk_score > 0      -> "low"    (informational-tier suspect)
#   risk_score == 0     -> "low"    (no signal)
#
# Conservative: do NOT escalate to ``"critical"`` on risk_score alone.
# The composite score has 4 dimensions (churn 30% / cc 30% / health 25%
# / entropy 15%) and a "critical" rank should require corroborating
# evidence (e.g. a runtime hotspot, a vuln reachability hit) not yet
# fused into the score. Same W531 CI-safety discipline as
# ``_impact_risk_level``.
def _diagnose_risk_level(top_risk_score: float) -> str:
    """Project a [0, 1] diagnose risk_score onto the canonical W631 set.

    Returns a string in :data:`roam.output.risk.RISK_LEVELS`
    (``critical`` / ``high`` / ``medium`` / ``low``). ``critical`` is
    reserved for a future multi-signal fusion tier; today the helper
    saturates at ``"high"`` for any score >= 0.70.
    """
    if top_risk_score is None or top_risk_score <= 0:
        return "low"
    if top_risk_score >= 0.70:
        return "high"
    if top_risk_score >= 0.40:
        return "medium"
    return "low"


def _cochange_partners(conn, file_id, limit=10):
    """Find files that frequently change together with the given file."""
    rows = conn.execute(
        """SELECT CASE WHEN cc.file_id_a = ? THEN cc.file_id_b ELSE cc.file_id_a END as partner_id,
                  cc.cochange_count, f.path
           FROM git_cochange cc
           JOIN files f ON f.id = CASE WHEN cc.file_id_a = ? THEN cc.file_id_b ELSE cc.file_id_a END
           WHERE cc.file_id_a = ? OR cc.file_id_b = ?
           ORDER BY cc.cochange_count DESC
           LIMIT ?""",
        (file_id, file_id, file_id, file_id, limit),
    ).fetchall()
    return [{"file": r["path"], "cochange_count": r["cochange_count"]} for r in rows]


def _recent_changes(conn, file_id, limit=5):
    """Get recent git commits touching this file."""
    rows = conn.execute(
        """SELECT gc.hash, gc.author, gc.message, gc.timestamp
           FROM git_commits gc
           JOIN git_file_changes gfc ON gc.id = gfc.commit_id
           WHERE gfc.file_id = ?
           ORDER BY gc.timestamp DESC
           LIMIT ?""",
        (file_id, limit),
    ).fetchall()
    return [
        {
            "hash": r["hash"][:8],
            "author": r["author"],
            "message": (r["message"] or "")[:80],
        }
        for r in rows
    ]


@roam_capability(
    name="diagnose",
    category="workflow",
    summary="Root cause analysis for a failing symbol",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "debug"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="diagnose")
@click.argument("name", required=False, default=None, metavar="[SYMBOL]")
@click.option("--depth", default=2, help="How many hops to analyze (default 2)")
@click.option(
    "--batch",
    "batch_input",
    type=str,
    default=None,
    help='read newline-separated symbol names from this file ("-" for stdin) and run diagnose on each.',
)
@click.pass_context
def diagnose_cmd(ctx, name, depth, batch_input):
    """Root cause analysis for a failing SYMBOL.

    SYMBOL is a symbol identifier (bare name or qualified name); omit it
    when using ``--batch`` to read identifiers from a file. Unlike
    ``why`` (which explains a symbol's architectural role), this command
    ranks upstream and downstream symbols by risk score to find likely
    root causes of failures.

    Given a symbol suspected of causing a bug, ranks upstream callers
    and downstream callees by a composite risk score combining:
    git churn, cognitive complexity, file health, and co-change entropy.

    Also shows co-change partners and recent git history for the
    symbol's file.

    \b
    Examples:
      roam diagnose handle_payment
      roam diagnose UserService.create --depth 3
      roam diagnose checkout --batch failing_symbols.txt

    See also ``why`` (architectural role), ``preflight`` (pre-change
    safety), and ``impact`` (blast radius alone).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # W607-DN -- ADDITIVE pre-substrate (``load_index``) plumbing on top of
    # the W607-S substrate-CALL + W607-BH aggregation-phase markers. W607-S
    # already wraps the substrate-helper boundaries (resolve_symbol /
    # build_graph / target_metrics / dist_stats / ranked_upstream /
    # ranked_downstream / cochange_partners / recent_commits / next_steps /
    # index_status); W607-BH already wraps the aggregation-phase boundaries
    # (verdict_synthesis / severity_normalize / auto_log /
    # serialize_envelope). W607-DN extends marker coverage to the
    # PRE-SUBSTRATE boundary that BOTH layers leave unguarded:
    #
    #   - ``load_index`` -- ``ensure_index()`` opens the SQLite DB + ensures
    #                       the schema is migrated. A raise here (corrupt
    #                       DB, partial index, missing parent .roam/ dir,
    #                       file-permission failure) would otherwise crash
    #                       cmd_diagnose BEFORE either W607-S or W607-BH
    #                       got a chance to accumulate markers, so the
    #                       agent loses ALL signal -- not just the
    #                       degraded section.
    #
    # cmd_diagnose is the root-cause companion to cmd_preflight + cmd_impact
    # in the agent-OS pre-edit triangle. With W607-DN landed, all three
    # surfaces are plumbed end-to-end from PRE-SUBSTRATE through
    # AGGREGATION -- there is no boundary left where a raise crashes the
    # envelope silently.
    #
    # Marker family ``diagnose_*`` -- same family as W607-S + W607-BH
    # (additive, not a separate prefix). Empty bucket -> byte-identical
    # envelope. The combined-bucket merger sites downstream sum all three
    # buckets in marker-emission order: W607-DN (load_index) -> W607-S
    # (substrate) -> W607-BH (aggregation).
    _w607dn_warnings_out: list[str] = []

    def _run_check_dn(phase: str, fn, *args, default=None, **kwargs):
        """Run one pre-substrate boundary with W607-DN marker emission.

        Mirror of ``_run_check`` / ``_run_check_bh`` shape (same
        ``diagnose_<phase>_failed:`` marker family) but writes into
        ``_w607dn_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dn_warnings_out.append(f"diagnose_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    _run_check_dn("load_index", ensure_index, default=None)

    # W607-S — substrate-CALL marker accumulator (nineteenth-in-batch
    # W607 consumer-layer arc). cmd_diagnose composes 4-5 substrate
    # helpers (find_symbol_with_alternatives / build_symbol_graph /
    # _get_symbol_metrics / _build_distribution_stats / _build_ranked
    # / _cochange_partners / _recent_changes / suggest_next_steps) into
    # a root-cause ranking envelope. Each helper has its own internal
    # error surface (SQL queries can raise OperationalError, the graph
    # builder can raise on schema-shape regression, _get_symbol_metrics
    # already has an inner try/except for the git_file_changes fallback),
    # but a helper itself can still raise BEFORE producing a safe floor.
    # The outer call sites in diagnose() previously had no guards, so
    # the envelope crashed whole. W607-S wraps each substrate boundary
    # with ``_run_check(phase, fn, *args)`` so the raise becomes a
    # ``diagnose_<phase>_failed:<exc_class>:<detail>`` marker via
    # ``_w607s_warnings_out`` and the envelope still emits the remaining
    # sections cleanly.
    #
    # Marker family ``diagnose_*`` — distinct from W607-R's ``preflight_*``,
    # W607-Q's ``pr_risk_*``, W607-P's ``audit_*``, W607-O's
    # ``dashboard_*``, W607-N's ``doctor_*``, W607-M's ``health_*``,
    # W607-L's ``minimap_*``, W607-K's ``describe_*``. The marker-prefix
    # discipline test pins this closed-enum distinction.
    #
    # Empty bucket -> byte-identical envelope (no warnings_out key in
    # either summary or top-level, no W607-S-driven partial_success flip;
    # the W1244 resolution-disclosure path still flips partial_success on
    # its own axis).
    _w607s_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-S marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``diagnose_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607s_warnings_out`` and return *default* — the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607s_warnings_out.append(f"diagnose_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BH -- ADDITIVE aggregation-phase plumbing on top of the
    # W607-S substrate-CALL markers. W607-S already wrapped the
    # substrate-helper boundaries (resolve_symbol / build_graph /
    # target_metrics / dist_stats / ranked_upstream / ranked_downstream /
    # cochange_partners / recent_commits / next_steps / index_status);
    # W607-BH extends marker coverage to the AGGREGATION-PHASE
    # boundaries that W607-S left unguarded:
    #
    #   - ``verdict_synthesis``    -- top-suspect verdict text build
    #   - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
    #                                 from the top suspect's risk_score
    #                                 + integer rank cluster
    #   - ``auto_log``             -- active-run ledger write (silent
    #                                 no-op if no run is active, but the
    #                                 underlying ``auto_log`` can still
    #                                 raise on HMAC chain misshape or
    #                                 filesystem failures)
    #   - ``serialize_envelope``   -- ``json_envelope("diagnose", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions)
    #
    # cmd_diagnose is the ROOT-CAUSE COMPANION to cmd_preflight (the
    # AGENT-OS PRE-EDIT SAFETY GATE) + cmd_impact (the BLAST-RADIUS
    # COMPANION) per CLAUDE.md LAW 1. With W607-BH landed, the agent-OS
    # pre-edit triangle is W607-plumbed end-to-end on BOTH the
    # substrate-CALL layer (W607-R + W607-T + W607-S) AND the
    # aggregation-phase layer (W607-AW + W607-BB + W607-BH). Each
    # command has dual-bucket plumbing with combined-warnings emission.
    #
    # Marker family ``diagnose_*`` -- same family as W607-S (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope.
    _w607bh_warnings_out: list[str] = []

    def _run_check_bh(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-BH marker emission.

        Mirror of ``_run_check`` shape (same ``diagnose_<phase>_failed:``
        marker family) but writes into ``_w607bh_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607bh_warnings_out.append(f"diagnose_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # batch mode runs diagnose on N symbols. Stream output as
    # one envelope per symbol so the consumer can newline-split the JSON.
    if batch_input:
        import sys as _sys

        if batch_input == "-":
            stream = _sys.stdin
            close_on_exit = False
        else:
            stream = open(batch_input, encoding="utf-8")
            close_on_exit = True
        try:
            names = [ln.strip() for ln in stream if ln.strip()]
        finally:
            if close_on_exit:
                stream.close()
        results = []
        any_degraded = False
        with open_db(readonly=True) as conn:
            # Hoisted from inside the per-symbol loop: distribution stats
            # scan four full tables (commit_count, complexity, health,
            # entropy) and the result is invariant across the batch. Doing
            # it once instead of per-symbol turns O(N) full-table scans
            # into one.
            #
            # W607-S: ``_build_distribution_stats`` floors to a usable
            # fallback shape so per-symbol ``_risk_score`` calls below
            # can still resolve normalisation factors without crashing
            # the whole batch.
            _dist_floor = {
                "commits": (1.0, 1.0),
                "complexity": (1.0, 1.0),
                "health": (1.0, 1.0),
                "entropy": (1.0, 1.0),
            }
            dist = _run_check(
                "dist_stats",
                _build_distribution_stats,
                conn,
                default=_dist_floor,
            )
            for nm in names:
                sym, _alts = _run_check(
                    "resolve_symbol",
                    find_symbol_with_alternatives,
                    conn,
                    nm,
                    default=(None, []),
                )
                if sym is None:
                    # W1244 Pattern-2 variant-D: disclose ``resolution=unresolved``
                    # so per-item shape stays uniform across resolved + failed entries.
                    unresolved_block = resolution_disclosure("unresolved", target=nm)
                    any_degraded = True
                    results.append(
                        {
                            "name": nm,
                            "error": "symbol not found",
                            **unresolved_block,
                        }
                    )
                    continue
                metrics = _run_check(
                    "metrics",
                    _get_symbol_metrics,
                    conn,
                    sym["id"],
                    default={
                        "complexity": 0,
                        "nesting": 0,
                        "line_count": 0,
                        "pagerank": 0,
                        "in_degree": 0,
                        "out_degree": 0,
                        "betweenness": 0,
                        "commits": 0,
                        "churn": 0,
                        "entropy": 0,
                        "health": 0,
                        "file_path": "",
                    },
                )
                risk = _run_check(
                    "risk_score",
                    _risk_score,
                    metrics,
                    dist,
                    default=0.0,
                )
                # W1244 / W1249 Pattern-2 variant-D: ``find_symbol_with_alternatives``
                # stamps ``_resolution_tier`` on each returned row; we read it
                # straight off and merge the disclosure into each result entry
                # so consumers can distinguish exact-match successes from
                # fuzzy-fallback ones.
                tier = sym.get("_resolution_tier", "symbol")
                if tier != "symbol":
                    any_degraded = True
                disclosure = resolution_disclosure(tier, target=sym["qualified_name"] or sym["name"])
                results.append(
                    {
                        "name": nm,
                        "risk_score": risk,
                        "kind": sym["kind"],
                        "file": sym["file_path"],
                        "line": sym["line_start"],
                        **disclosure,
                    }
                )
        if json_mode:
            # W1244: top-level partial_success flips true when ANY per-item
            # entry resolved non-exactly (fuzzy or unresolved). Per the W324
            # cmd_annotate template -- the underlying ranking may still be
            # valid but the success verdict must reflect the degradation.
            #
            # W607-S: a non-empty marker bucket also flips partial_success
            # so a consumer reading only the summary can distinguish "clean
            # batch" from "batch ran with substrate degradation".
            # W607-S + W607-BH -- combine both buckets for the batch
            # path. The aggregation-phase BH bucket is empty in batch
            # mode today (batch does not call verdict_synthesis /
            # severity_normalize / auto_log / serialize_envelope on each
            # row), but include it for shape uniformity so a future
            # batch-path BH wrap lands cleanly.
            _combined_batch = list(_w607dn_warnings_out) + list(_w607s_warnings_out) + list(_w607bh_warnings_out)
            batch_partial = any_degraded or bool(_combined_batch)
            batch_summary: dict = {
                "verdict": f"{len(results)} symbol(s) diagnosed",
                "count": len(results),
                "partial_success": batch_partial,
            }
            batch_kwargs: dict = {
                "summary": batch_summary,
                "results": results,
                "partial_success": batch_partial,
            }
            if _combined_batch:
                batch_summary["warnings_out"] = list(_combined_batch)
                batch_kwargs["warnings_out"] = list(_combined_batch)
            click.echo(to_json(json_envelope("diagnose.batch", **batch_kwargs)))
            return
        click.echo(f"VERDICT: {len(results)} symbol(s) diagnosed")
        click.echo()
        click.echo(f"{'Name':<32}  {'Risk':>5}  {'Kind':<10}  Location")
        click.echo(f"{'-' * 32}  {'-' * 5}  {'-' * 10}  {'-' * 30}")
        for r in results:
            if "error" in r:
                click.echo(f"{r['name']:<32}  {'-':>5}  {'(error)':<10}  {r['error']}")
                continue
            click.echo(f"{r['name'][:32]:<32}  {r['risk_score']:>5}  {r['kind'][:10]:<10}  {r['file']}:{r['line']}")
        return

    if not name:
        from roam.output.errors import MISSING_REQUIRED_ARG, structured_usage_error

        raise structured_usage_error(MISSING_REQUIRED_ARG, "Pass a symbol name or use --batch <file>.")

    with open_db(readonly=True) as conn:
        sym, alternatives = _run_check(
            "resolve_symbol",
            find_symbol_with_alternatives,
            conn,
            name,
            default=(None, []),
        )
        if sym is None:
            # W1272 — Pattern-2c Convention (c): unresolved exits 0 with a
            # resolution=unresolved + partial_success disclosure so agents
            # can distinguish a name-typo from a tool/IO failure. Text
            # mode keeps the suggestion list (most useful next step for a
            # human staring at a typo).
            unresolved_block = resolution_disclosure("unresolved", target=name or "")
            if json_mode:
                # W607-S — surface substrate-CALL markers on the
                # not-found path. If find_symbol_with_alternatives
                # raised, the W607-S wrapper floored to (None, []) and
                # the marker lives in ``_w607s_warnings_out``; pin it
                # onto the envelope so the agent sees the cause.
                # partial_success is already True on this branch.
                _ur_summary: dict = {
                    "verdict": f"Symbol '{name}' not found",
                    "partial_success": True,
                    "state": "not_found",
                    **unresolved_block,
                }
                _ur_kwargs: dict = {
                    "summary": _ur_summary,
                    "symbol": name or "",
                    **unresolved_block,
                }
                # W607-S + W607-BH -- combine substrate-CALL and
                # aggregation-phase buckets BEFORE threading into the
                # envelope so the not-found degradation lineage is
                # visible in marker-emission order. The BH bucket is
                # empty on this branch (aggregation phases fire only
                # after the success path returns), but the field shape
                # stays uniform across paths.
                _combined_ur = list(_w607dn_warnings_out) + list(_w607s_warnings_out) + list(_w607bh_warnings_out)
                if _combined_ur:
                    _ur_summary["warnings_out"] = list(_combined_ur)
                    _ur_kwargs["warnings_out"] = list(_combined_ur)
                click.echo(to_json(json_envelope("diagnose", **_ur_kwargs)))
            else:
                click.echo(symbol_not_found(conn, name, json_mode=False))
            return

        sym_id = sym["id"]
        # W1244 / W1249 Pattern-2 variant-D: ``find_symbol_with_alternatives``
        # stamps ``_resolution_tier`` on the returned row so the envelope can
        # distinguish a fully-resolved success from a degraded fuzzy-match
        # success that may have landed on a different target -- root-cause
        # ranking on the wrong target is exactly the silent-fallback anti-
        # pattern this disclosure exists to prevent.
        resolution_tier = sym.get("_resolution_tier", "symbol")
        resolution_block = resolution_disclosure(resolution_tier, target=sym["qualified_name"] or sym["name"])
        did_you_mean = [
            {
                "name": (alt["qualified_name"] or alt["name"]),
                "kind": alt["kind"],
                "location": loc(alt["file_path"], alt["line_start"]),
            }
            for alt in alternatives[:5]
        ]
        # W607-S: ``build_symbol_graph`` can raise on a downstream
        # schema-shape refactor or a corrupted index. Floor to an empty
        # graph so the isolated-in-graph branch below catches the case
        # uniformly (sym_id not in {} -> True -> isolated_in_graph).
        import networkx as _nx_floor

        G = _run_check(
            "build_graph",
            build_symbol_graph,
            conn,
            default=_nx_floor.DiGraph(),
        )

        if sym_id not in G:
            # Pattern 1B/1C: must emit structured JSON in --json mode rather
            # than dumping plain text + SystemExit(1) which strips the
            # structured signal at the MCP wrapper. The symbol resolved
            # cleanly -- it's just disconnected from the call graph (no
            # callers/callees indexed). Treat as partial_success: the
            # resolution worked but the ranking degraded to empty.
            sym_name = sym["qualified_name"] or sym["name"]
            verdict = f"Symbol '{sym_name}' resolved but is not connected in the dependency graph"
            hint_text = (
                "Run `roam index` to rebuild the graph. If the symbol has no "
                "callers or callees, it may not appear in the graph."
            )
            if json_mode:
                # W607-S — surface substrate-CALL markers on the
                # isolated-in-graph path. If build_symbol_graph raised,
                # the W607-S wrapper floored to an empty graph and the
                # marker lives in ``_w607s_warnings_out``; pin it onto
                # the envelope. partial_success is already True here.
                _iso_metrics = _run_check(
                    "target_metrics",
                    _get_symbol_metrics,
                    conn,
                    sym_id,
                    default={
                        "complexity": 0,
                        "nesting": 0,
                        "line_count": 0,
                        "pagerank": 0,
                        "in_degree": 0,
                        "out_degree": 0,
                        "betweenness": 0,
                        "commits": 0,
                        "churn": 0,
                        "entropy": 0,
                        "health": 0,
                        "file_path": "",
                    },
                )
                _iso_summary: dict = {
                    "target": sym_name,
                    "verdict": verdict,
                    "partial_success": True,
                    "state": "isolated_in_graph",
                    **resolution_block,
                }
                _iso_kwargs: dict = {
                    "summary": _iso_summary,
                    "symbol": sym_name,
                    "hint": hint_text,
                    "target_metrics": _iso_metrics,
                    "upstream": [],
                    "downstream": [],
                    "cochange_partners": [],
                    "recent_commits": [],
                    "did_you_mean": did_you_mean,
                    **resolution_block,
                }
                # W607-S + W607-BH -- combine substrate-CALL and
                # aggregation-phase buckets BEFORE threading into the
                # envelope so the isolated-in-graph degradation lineage
                # is visible in marker-emission order.
                _combined_iso = list(_w607dn_warnings_out) + list(_w607s_warnings_out) + list(_w607bh_warnings_out)
                if _combined_iso:
                    _iso_summary["warnings_out"] = list(_combined_iso)
                    _iso_kwargs["warnings_out"] = list(_combined_iso)
                click.echo(to_json(json_envelope("diagnose", **_iso_kwargs)))
                return
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"  Tip: {hint_text}")
            raise SystemExit(1)

        _metrics_floor = {
            "complexity": 0,
            "nesting": 0,
            "line_count": 0,
            "pagerank": 0,
            "in_degree": 0,
            "out_degree": 0,
            "betweenness": 0,
            "commits": 0,
            "churn": 0,
            "entropy": 0,
            "health": 0,
            "file_path": "",
        }
        target_metrics = _run_check(
            "target_metrics",
            _get_symbol_metrics,
            conn,
            sym_id,
            default=_metrics_floor,
        )
        dist_stats = _run_check(
            "dist_stats",
            _build_distribution_stats,
            conn,
            default={
                "commits": (1.0, 1.0),
                "complexity": (1.0, 1.0),
                "health": (1.0, 1.0),
                "entropy": (1.0, 1.0),
            },
        )

        # Upstream callers (predecessors) + downstream callees (successors)
        # both reach via a bounded BFS over the call graph. The helper
        # below replaces 2 near-identical 5-level-deep loops with one
        # parameterised walk (R-deepnest cleanup, 2026-05-23).
        upstream_ids = _bfs_neighbors(G, sym_id, depth, G.predecessors)
        downstream_ids = _bfs_neighbors(G, sym_id, depth, G.successors)

        # Rank upstream by risk score
        def _build_ranked(sym_ids, direction):
            ranked = []
            for sid in sym_ids:
                row = conn.execute(
                    "SELECT s.name, s.qualified_name, s.kind, s.line_start, f.path, f.id as file_id "
                    "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                    (sid,),
                ).fetchone()
                if not row:
                    continue
                metrics = _get_symbol_metrics(conn, sid)
                risk = _risk_score(metrics, dist_stats)
                ranked.append(
                    {
                        "name": row["qualified_name"] or row["name"],
                        "kind": abbrev_kind(row["kind"]),
                        "location": loc(row["path"], row["line_start"]),
                        "risk_score": risk,
                        "complexity": metrics["complexity"],
                        "commits": metrics["commits"],
                        "health": metrics["health"],
                        "entropy": metrics["entropy"],
                        "direction": direction,
                    }
                )
            ranked.sort(key=lambda x: -x["risk_score"])
            return ranked

        upstream_ranked = _run_check(
            "ranked_upstream",
            _build_ranked,
            upstream_ids,
            "upstream",
            default=[],
        )
        downstream_ranked = _run_check(
            "ranked_downstream",
            _build_ranked,
            downstream_ids,
            "downstream",
            default=[],
        )

        # Co-change partners
        file_row = conn.execute(
            "SELECT f.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
            (sym_id,),
        ).fetchone()
        cochanges = (
            _run_check(
                "cochange_partners",
                _cochange_partners,
                conn,
                file_row["id"],
                default=[],
            )
            if file_row
            else []
        )
        recent = (
            _run_check(
                "recent_commits",
                _recent_changes,
                conn,
                file_row["id"],
                default=[],
            )
            if file_row
            else []
        )

        # Build verdict
        # W607-BH -- wrap the top-suspect verdict synthesis. A malformed
        # ranked-suspect row (post-refactor key rename on ``name`` /
        # ``risk_score`` / ``complexity`` / ``commits`` / ``health``)
        # would raise KeyError here; the wrap floors to a safe verdict
        # string so the envelope still emits the raw suspect lists with
        # the marker attached.
        all_suspects = upstream_ranked[:5] + downstream_ranked[:5]

        def _build_verdict() -> str:
            if all_suspects:
                top = all_suspects[0]
                return (
                    f"Top suspect: {top['name']} "
                    f"(risk={top['risk_score']:.2f}, cc={top['complexity']}, "
                    f"commits={top['commits']}, health={top['health']}/10)"
                )
            return "No upstream/downstream symbols found within depth range."

        verdict = _run_check_bh(
            "verdict_synthesis",
            _build_verdict,
            default="No upstream/downstream symbols found within depth range.",
        )
        # W1244 Pattern-2 variant-D: suffix the verdict when resolution was
        # degraded so a single-line verdict consumer (LAW 6) still sees the
        # disclosure even without the full envelope.
        if resolution_tier != "symbol":
            verdict = (
                f"{verdict} [fuzzy resolution -- target "
                f"'{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            )

        # W607-BH -- canonical W631 risk-LEVEL projection from the top
        # suspect's risk_score. Mirrors the cmd_impact W607-BB
        # risk_classify + risk_normalize cluster shape so cross-command
        # consumers can compare diagnose's top-suspect tier against
        # impact's blast-radius tier without re-deriving the threshold
        # table at the call site (Pattern-3a). When ``_diagnose_risk_level``
        # raises (future signature change) the wrap floors the domain
        # tier to ``None`` and surfaces ``severity_classification: "unknown"``
        # in the envelope summary alongside the canonical W631 ``"low"``
        # floor on ``risk_level_canonical``. The underlying action (emit
        # the root-cause ranking) stays -- degraded outcomes are valid
        # design; the LIE we prevent is a clean classified verdict when
        # severity_normalize actually raised.
        _top_risk_score = all_suspects[0]["risk_score"] if all_suspects else 0.0
        _diagnose_domain_level = _run_check_bh(
            "severity_normalize",
            _diagnose_risk_level,
            _top_risk_score,
            default=None,
        )
        # Domain-tier raised -> mark classification unknown so the
        # envelope discloses the degraded outcome.
        _severity_classification_state = "unknown" if _diagnose_domain_level is None else "classified"
        risk_level_canonical = _run_check_bh(
            "severity_normalize",
            lambda level: normalize_risk_level(level) or "low",
            _diagnose_domain_level,
            default="low",
        )
        risk_rank_int = _run_check_bh(
            "severity_normalize",
            risk_rank,
            risk_level_canonical,
            default=1,
        )
        # Verdict augmentation per LAW 6 (line works standalone): append
        # the canonical risk_level in a closed-enum parenthesis so a
        # consumer parsing only the verdict string sees the canonical
        # bucket directly.
        verdict = f"{verdict} (risk_level {risk_level_canonical})"

        _target_name = sym["qualified_name"] or sym["name"]
        _top_suspect = all_suspects[0]["name"] if all_suspects else ""
        _next_steps = _run_check(
            "next_steps",
            suggest_next_steps,
            "diagnose",
            {
                "symbol": _target_name,
                "top_suspect": _top_suspect,
            },
            default=[],
        )

        # Round 4 #20 / U: index status is now a top-level envelope
        # field AND prints before the VERDICT in text mode so an agent
        # reading top-down can't miss a stale-index warning.
        from roam.commands.resolve import index_status as _index_status

        index_status_payload = _run_check(
            "index_status",
            _index_status,
            default=None,
        )

        if json_mode:
            _success_summary: dict = {
                "target": _target_name,
                "verdict": verdict,
                "upstream_count": len(upstream_ranked),
                "downstream_count": len(downstream_ranked),
                "ambiguous": bool(did_you_mean),
                "caller_metric_definition": "transitive_upstream_bfs",
                # W1298 Pattern-3a: the ``complexity`` field on each
                # upstream/downstream/target row is raw
                # cognitive_complexity from symbol_metrics, renamed in
                # the envelope but identical to cmd_complexity's reading.
                "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
                # W607-BH -- canonical W631 risk-LEVEL projection + integer
                # rank. Projected from the top suspect's risk_score via
                # ``_diagnose_risk_level`` so cross-command floor
                # comparators ("is diagnose's top-suspect risk worse than
                # cmd_impact's blast tier?") work without each consumer
                # re-deriving the threshold table at the call site
                # (Pattern-3a). Mirrors the cmd_impact W641-followup-A
                # emit pattern.
                "risk_level_canonical": risk_level_canonical,
                "risk_rank": risk_rank_int,
                # W607-BH -- SEVERITY-NORMALIZE DEGRADATION sentinel.
                # When the ``severity_normalize`` boundary raises (and
                # the classify result floors to ``None``), surface
                # ``severity_classification: "unknown"`` so the agent
                # sees the degraded outcome alongside the canonical
                # floor ("low") rather than mistaking the floor for a
                # real classification. Empty bucket -> ``"classified"``
                # (clean path). Mirror of cmd_impact's
                # ``risk_classification`` sentinel.
                "severity_classification": _severity_classification_state,
                # W1244 Pattern-2 variant-D resolution disclosure --
                # the partial_success polarity flips True for any
                # non-``symbol`` tier per resolution_disclosure().
                **resolution_block,
            }
            _success_kwargs: dict = {
                "summary": _success_summary,
                "target_metrics": target_metrics,
                "upstream": upstream_ranked[:15],
                "downstream": downstream_ranked[:15],
                "cochange_partners": cochanges,
                "recent_commits": recent,
                "did_you_mean": did_you_mean,
                "next_steps": _next_steps,
                # W607-BH -- top-level mirror of summary.risk_level_canonical
                # / summary.risk_rank so consumers reading the top-level
                # envelope (not just ``summary``) get the same canonical
                # bucket without re-deriving it. Mirrors the W641
                # cmd_impact pattern.
                "risk_level_canonical": risk_level_canonical,
                "risk_rank": risk_rank_int,
                **resolution_block,
            }
            # W607-S + W607-BH -- combine substrate-CALL and aggregation-
            # phase buckets BEFORE threading into the envelope. Both
            # buckets share the ``diagnose_*`` marker family (W607-BH is
            # additive coverage of aggregation-phase boundaries on top
            # of W607-S's helper-call boundaries). Empty combined bucket
            # -> byte-identical envelope (no warnings_out keys added;
            # no partial_success flip from this axis -- the W1244
            # resolution-disclosure axis still flips it on its own).
            # Non-empty bucket -> warnings_out at top-level AND summary
            # mirror, plus summary.partial_success=True so the agent
            # can distinguish "clean diagnose" from "diagnose ran with
            # substrate degradation" via the summary alone.
            _combined_warnings_out = list(_w607dn_warnings_out) + list(_w607s_warnings_out) + list(_w607bh_warnings_out)
            if _combined_warnings_out:
                _success_summary["warnings_out"] = list(_combined_warnings_out)
                _success_summary["partial_success"] = True
                _success_kwargs["warnings_out"] = list(_combined_warnings_out)
                _success_kwargs["partial_success"] = True
            # W607-BH -- wrap the envelope serialization itself. A
            # downstream schema-shape refactor that breaks
            # ``json_envelope("diagnose", ...)`` would otherwise crash
            # AFTER all substrate + aggregation signals were already
            # gathered. Floor to a minimal envelope stub so consumers
            # still receive a parseable JSON object with the marker
            # attached + the canonical command name.
            _envelope_floor = {
                "command": "diagnose",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": verdict,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings_out),
                },
                "warnings_out": list(_combined_warnings_out),
            }
            envelope = _run_check_bh(
                "serialize_envelope",
                json_envelope,
                "diagnose",
                default=_envelope_floor,
                **_success_kwargs,
            )
            # W607-BH -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``diagnose_serialize_envelope_failed:`` marker was
            # appended to ``_w607bh_warnings_out`` and the floor stub
            # carries only the old combined list. Rebuild the floor
            # stub's warnings_out so the new marker reaches the JSON
            # output. Clean path -> envelope is the real
            # json_envelope return value, no rebuild needed.
            if envelope is _envelope_floor and _w607bh_warnings_out:
                _combined_warnings_out = (
                    list(_w607dn_warnings_out) + list(_w607s_warnings_out) + list(_w607bh_warnings_out)
                )
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                envelope = _envelope_floor
            if index_status_payload is not None:
                envelope["index_status"] = index_status_payload
            # W607-BH -- auto_log emission for the agent-OS pre-edit
            # triangle parity (cmd_preflight + cmd_impact + cmd_diagnose).
            # Silent no-op if no active run; the wrap surfaces HMAC
            # chain-misshape / filesystem failures as
            # ``diagnose_auto_log_failed:...`` markers instead of
            # crashing the envelope after it was already built.
            _run_check_bh(
                "auto_log",
                auto_log,
                envelope,
                action="diagnose",
                target=_target_name or "",
                default=None,
            )
            # W607-BH -- if ``auto_log`` raised, rebuild the envelope so
            # the marker reaches the JSON output. Empty bucket (clean
            # auto_log) -> envelope stays byte-identical to the version
            # already built above.
            if _w607bh_warnings_out and not any(
                m.startswith("diagnose_auto_log_failed:") for m in (_success_summary.get("warnings_out") or [])
            ):
                _combined_warnings_out = (
                    list(_w607dn_warnings_out) + list(_w607s_warnings_out) + list(_w607bh_warnings_out)
                )
                _success_summary["warnings_out"] = list(_combined_warnings_out)
                _success_summary["partial_success"] = True
                _success_kwargs["warnings_out"] = list(_combined_warnings_out)
                _success_kwargs["partial_success"] = True
                envelope = _run_check_bh(
                    "serialize_envelope",
                    json_envelope,
                    "diagnose",
                    default=_envelope_floor,
                    **_success_kwargs,
                )
                if index_status_payload is not None:
                    envelope["index_status"] = index_status_payload
            click.echo(to_json(envelope))
            return

        # Text output — index-staleness warning lands FIRST so it can't
        # be missed when scanning top-down.
        if index_status_payload and not index_status_payload.get("fresh"):
            click.echo(f"NOTE: {index_status_payload['hint']}")
            click.echo()
        click.echo(f"VERDICT: {verdict}")
        sym_name = sym["qualified_name"] or sym["name"]
        click.echo(f"Diagnose: {sym_name}")
        click.echo(f"  {loc(target_metrics['file_path'], sym['line_start'])}")
        click.echo(
            f"  complexity={target_metrics['complexity']}, "
            f"commits={target_metrics['commits']}, "
            f"health={target_metrics['health']}/10\n"
        )
        if did_you_mean:
            click.echo("Did you mean (other matches, ranked by importance):")
            for alt in did_you_mean:
                click.echo(f"  {abbrev_kind(alt['kind'])}  {alt['name']}  {alt['location']}")
            click.echo("  Tip: use file:symbol (e.g. TransactionEntryModal.vue:handleSave) to disambiguate.\n")

        if upstream_ranked:
            click.echo("Upstream suspects (callers, ranked by risk):\n")
            rows = [
                [
                    r["name"],
                    r["kind"],
                    f"{r['risk_score']:.2f}",
                    str(r["complexity"]),
                    str(r["commits"]),
                    f"{r['health']}/10",
                    r["location"],
                ]
                for r in upstream_ranked[:10]
            ]
            click.echo(
                format_table(
                    ["Symbol", "Kind", "Risk", "CC", "Commits", "Health", "Location"],
                    rows,
                )
            )

        if downstream_ranked:
            click.echo("\nDownstream suspects (callees, ranked by risk):\n")
            rows = [
                [
                    r["name"],
                    r["kind"],
                    f"{r['risk_score']:.2f}",
                    str(r["complexity"]),
                    str(r["commits"]),
                    f"{r['health']}/10",
                    r["location"],
                ]
                for r in downstream_ranked[:10]
            ]
            click.echo(
                format_table(
                    ["Symbol", "Kind", "Risk", "CC", "Commits", "Health", "Location"],
                    rows,
                )
            )

        if cochanges:
            click.echo("\nCo-change partners (files that change together):\n")
            for c in cochanges[:8]:
                click.echo(f"  {c['file']}  ({c['cochange_count']} co-changes)")

        if recent:
            click.echo(f"\nRecent commits to {target_metrics['file_path']}:\n")
            for c in recent:
                click.echo(f"  {c['hash']}  {c['author']:<20} {c['message']}")

        _ns_text = format_next_steps_text(_next_steps)
        if _ns_text:
            click.echo(_ns_text)
