"""Show blast radius: what breaks if a symbol changes."""

from __future__ import annotations

import time

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db

# W607-T -- hoisted from inside ``impact()`` so monkeypatch on
# ``roam.commands.cmd_impact.build_symbol_graph`` works the same way the
# sibling W607-S guard works on cmd_diagnose. The graph builder is imported
# lazily everywhere it's used; the hoisting is import-time only and changes
# no observable behavior beyond making the function patchable at the
# module-attribute boundary.
from roam.graph.builder import build_symbol_graph
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import (
    BLAST_RADIUS_AFFECTED_FILES,
    BLAST_RADIUS_AFFECTED_SYMBOLS,
    BLAST_RADIUS_AFFECTED_TOTAL,
    REACH_PCT_DEFINITION,
    WEIGHTED_IMPACT_DEFINITION,
)
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log


# W641-followup-A — domain blast-radius tier to canonical risk-LEVEL projection.
# cmd_impact has no pre-existing risk_level emit; the canonical bucket is
# derived from the same polarity ``_impact_verdict`` already uses for the
# verdict prefix (Large / Moderate / Small / No dependents). Thresholds:
#
#   reach_pct >= 10  OR  affected_symbols >= 50  -> Large    -> "high"
#   reach_pct >= 2   OR  affected_symbols >= 10  -> Moderate -> "medium"
#   affected_symbols > 0                          -> Small    -> "low"
#   affected_symbols == 0                         -> None     -> "low"
#
# Polarity matches the canonical W631 ``higher = worse`` rank polarity, so
# downstream consumers can call ``risk_rank(summary['risk_level_canonical'])
# >= 3`` to gate on high-or-worse without re-deriving the threshold table at
# the call site (same Pattern-3a discipline as W632 / W641).
def _impact_risk_level(affected_symbols: int, reach_pct: float) -> str:
    """Project blast-radius metrics onto the canonical W631 risk-LEVEL set.

    Returns a string in :data:`roam.output.risk.RISK_LEVELS`
    (``critical``/``high``/``medium``/``low``). ``critical`` is reserved for
    a future weighted_impact-aware tier; today the helper saturates at
    ``high`` for any blast that meets the large-radius threshold.

    Thresholds are conservative and mirror the existing verdict polarity in
    :func:`_impact_verdict` so an agent reading the verdict prefix
    (``Large``/``Moderate``/``Small``/``No``) sees a consistent canonical
    rank. Conservative: do NOT escalate to ``critical`` on weighted_impact
    alone — graph size makes that signal unstable, and the W531 CI-safety
    lesson is that a wobbly threshold MUST NOT promote a finding into a
    CI-gating rank.
    """
    if reach_pct >= 10 or affected_symbols >= 50:
        return "high"
    if reach_pct >= 2 or affected_symbols >= 10:
        return "medium"
    return "low"


def _bounded_bfs(
    RG,
    sym_id,
    *,
    max_depth: int | None,
    max_callers: int | None,
    deadline: float | None,
):
    """Bounded BFS over reverse graph from ``sym_id``.

    Caps applied:

    - ``max_depth``: traversal stops past ``max_depth`` hops (None = unlimited)
    - ``max_callers``: at each frontier, fan-out is capped at this many new
      nodes; further siblings are dropped (None = unlimited)
    - ``deadline``: wall-clock cutoff in ``time.monotonic()`` units; checked
      every 1000 nodes so we don't pay the syscall on tight loops

    Returns ``(dependents_set, hit_caller_cap, hit_depth_cap, hit_timeout)``.
    """
    dependents: set = set()
    if sym_id not in RG:
        return dependents, False, False, False

    hit_caller_cap = False
    hit_depth_cap = False
    hit_timeout = False

    # Frontier-based BFS so we can apply per-frontier fan-out caps.
    frontier: list = [sym_id]
    depth = 0
    nodes_visited = 0
    while frontier:
        if max_depth is not None and depth >= int(max_depth):
            # Frontier still has items past the depth cap — flag and bail.
            hit_depth_cap = True
            break
        next_frontier: list = []
        for node in frontier:
            for succ in RG.successors(node):
                if succ in dependents or succ == sym_id:
                    continue
                if max_callers is not None and len(dependents) >= int(max_callers):
                    hit_caller_cap = True
                    break
                dependents.add(succ)
                next_frontier.append(succ)
                nodes_visited += 1
                if deadline is not None and nodes_visited % 1000 == 0:
                    if time.monotonic() >= deadline:
                        hit_timeout = True
                        break
            if hit_caller_cap or hit_timeout:
                break
        if hit_caller_cap or hit_timeout:
            break
        frontier = next_frontier
        depth += 1
    return dependents, hit_caller_cap, hit_depth_cap, hit_timeout


def _collect_dependents(
    G,
    RG,
    sym_id,
    conn,
    max_hops: int | None = None,
    *,
    max_callers: int | None = None,
    deadline: float | None = None,
):
    """Collect affected files, direct callers by kind, and SF test files.

    When ``max_hops`` is set, the BFS is bounded to that many hops instead
    of expanding to the full transitive descendants set. Additional caps
    (``max_callers``, ``deadline``) bound fan-out / wall-clock for
    high-fan-in symbols (e.g. shared hooks with 500+ callers).

    Returns the legacy 5-tuple plus a trailing ``state`` dict tracking
    which caps fired so the caller can surface ``partial_success`` /
    ``truncated`` envelope flags.
    """
    import networkx as nx

    state = {"hit_caller_cap": False, "hit_depth_cap": False, "hit_timeout": False}

    if max_callers is None and deadline is None:
        # Legacy fast path — preserve original semantics exactly.
        if max_hops is None:
            dependents = nx.descendants(RG, sym_id)
        else:
            lengths = nx.single_source_shortest_path_length(RG, sym_id, cutoff=int(max_hops))
            dependents = {n for n in lengths if n != sym_id}
    else:
        dependents, hit_cap, hit_depth, hit_to = _bounded_bfs(
            RG, sym_id, max_depth=max_hops, max_callers=max_callers, deadline=deadline
        )
        state["hit_caller_cap"] = hit_cap
        state["hit_depth_cap"] = hit_depth
        state["hit_timeout"] = hit_to
    affected_files = set()
    direct_callers = set(RG.successors(sym_id))
    by_kind: dict[str, list] = {}

    for dep_id in dependents:
        node = G.nodes.get(dep_id, {})
        if not node:
            continue
        affected_files.add(node.get("file_path", "?"))
        if dep_id in direct_callers:
            edge_data = G.edges.get((dep_id, sym_id), {})
            edge_kind = edge_data.get("kind", "unknown")
            by_kind.setdefault(edge_kind, []).append(
                [
                    abbrev_kind(node.get("kind", "?")),
                    node.get("name", "?"),
                    loc(node.get("file_path", "?"), None),
                ]
            )

    # Convention-based Salesforce test discovery
    sf_test_files = set()
    for dep_id in dependents | {sym_id}:
        dep_name = G.nodes.get(dep_id, {}).get("name", "")
        if dep_name:
            conv_tests = conn.execute(
                "SELECT f.path FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE (s.name = ? OR s.name = ?) AND s.kind = 'class'",
                (f"{dep_name}Test", f"{dep_name}_Test"),
            ).fetchall()
            for ct in conv_tests:
                sf_test_files.add(ct["path"])

    return dependents, affected_files, direct_callers, by_kind, sf_test_files, state


def _uncapped_blast_total(G, RG, sym_id, own_file_path):
    """Compute the TRUE uncapped blast radius for ``sym_id``.

    W335/W342 Pattern-3a — ``impact``'s default ``--max-callers 100`` caps
    the DISPLAYED dependents list for response-size sanity, but the reported
    COUNT must be honest and AGREE with ``preflight``'s blast-radius gate.
    This helper runs the IDENTICAL computation ``cmd_preflight._check_blast_radius``
    uses (``nx.descendants`` over the reverse graph, excluding the target's
    own file from affected_files) so the two commands are provably reporting
    the same metric when used together for change-safety.

    Returns ``(total_symbols, total_files)``. On any failure the caller's
    ``_run_check`` floor handles disclosure; here we just compute.
    """
    import networkx as nx

    if sym_id not in RG:
        return 0, 0
    deps = nx.descendants(RG, sym_id)
    files: set = set()
    for d in deps:
        fp = G.nodes.get(d, {}).get("file_path")
        if fp and fp != own_file_path:
            files.add(fp)
    return len(deps), len(files)


def _find_indirect_refs(conn, sym, already_affected_files: set, *, limit: int = 50) -> list[dict]:
    """Scan source files for string-literal references to a symbol.

    picks up registry-dispatch consumers (e.g. cli's
    ``_COMMANDS = {"foo": ("module.path", "attr_name")}``) that the
    static call graph misses. Excludes the symbol's own file and any
    file already in the directly-affected set so we surface NEW edges,
    not duplicates.
    """
    import re as _re
    from pathlib import Path as _Path

    name = (sym["name"] or "").strip()
    qname = (sym["qualified_name"] or "").strip()
    if not name:
        return []
    own_file = (sym["file_path"] or "").replace("\\", "/")
    affected_norm = {p.replace("\\", "/") for p in already_affected_files}

    # Build a single regex that matches the qname OR the bare name when
    # quoted as a string literal. Bare-name-only matches generate too
    # many false positives, so we require the literal to contain a dot
    # (qualified) OR the symbol name length to be >= 5 to filter out
    # short generic names like "id" or "url".
    candidates = []
    if qname:
        candidates.append(_re.escape(qname))
    if len(name) >= 5:
        candidates.append(_re.escape(name))
    if not candidates:
        return []
    pattern = _re.compile(r"['\"](?:" + "|".join(candidates) + r")['\"]")

    # Narrow to source files only (exclude tests/docs/data).
    rows = conn.execute(
        "SELECT path FROM files WHERE COALESCE(file_role, 'source') IN ('source','config','scripts')"
    ).fetchall()
    refs: list[dict] = []
    for r in rows:
        rel = (r["path"] or "").replace("\\", "/")
        if rel == own_file or rel in affected_norm:
            continue
        full = _Path(rel)
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            refs.append({"file": rel, "line": line_no, "match": m.group(0)})
            if len(refs) >= limit:
                return refs
    return refs


def _impact_verdict(dependents, affected_files, total_syms, *, affected_count=None, affected_files_count=None):
    """Generate blast radius verdict string.

    W335/W342 Pattern-3a — the verdict COUNT must reflect the TRUE uncapped
    blast radius so it agrees with ``preflight``. When ``affected_count`` /
    ``affected_files_count`` are supplied (the honest uncapped totals), the
    verdict reports them instead of the bounded ``len(dependents)`` /
    ``len(affected_files)``. Reach-pct is likewise computed from the true
    total. Callers that don't pass the counts (legacy / floored paths) fall
    back to the bounded set lengths so behavior is unchanged on those paths.
    """
    sym_count = affected_count if affected_count is not None else len(dependents)
    file_count = affected_files_count if affected_files_count is not None else len(affected_files)
    reach_pct = (sym_count / total_syms * 100) if total_syms > 0 else 0
    if reach_pct >= 10 or sym_count >= 50:
        return (
            f"Large blast radius — {sym_count} symbols ({reach_pct:.1f}%) in {file_count} files affected",
            reach_pct,
        )
    if reach_pct >= 2 or sym_count >= 10:
        return (
            f"Moderate blast radius — {sym_count} symbols ({reach_pct:.1f}%) in {file_count} files affected",
            reach_pct,
        )
    if sym_count > 0:
        return (
            f"Small blast radius — {sym_count} symbols in {file_count} files affected",
            reach_pct,
        )
    return "No dependents — safe to change", reach_pct


@roam_capability(
    category="exploration",
    summary="Show blast radius: what breaks if a symbol changes.",
    inputs=["name"],
    outputs=["affected_symbols", "verdict"],
    examples=[
        "roam impact handleSave",
        "roam impact AuthService --hops 3",
    ],
    tags=["exploration", "blast", "agent"],
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
@click.argument("name", metavar="SYMBOL")
@click.option(
    "--hops",
    type=int,
    default=None,
    help=(
        "bound the BFS at N hops (legacy alias for --depth). "
        "``--hops 1`` mirrors ``roam uses``; ``--hops 2`` shows callers "
        "of callers; useful to scope a refactor to a controlled radius."
    ),
)
@click.option(
    "--depth",
    type=int,
    default=3,
    show_default=True,
    help=(
        "cap BFS depth (number of hops). Conservative default keeps the "
        "command bounded for high-fan-in symbols (e.g. shared hooks with "
        "500+ callers). Use ``--hops 0`` or a large ``--depth`` for the "
        "full transitive radius."
    ),
)
@click.option(
    "--max-callers",
    type=int,
    default=100,
    show_default=True,
    help=(
        "cap total fan-out at N callers. When exceeded, the envelope sets "
        "``truncated: true`` and ``partial_success: true``; the first N "
        "callers are returned."
    ),
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help=(
        "graceful wall-clock cap in seconds. On hit, returns what we have "
        "with ``state: timeout`` + ``partial_success: true``."
    ),
)
@click.pass_context
def impact(ctx, name, hops, depth, max_callers, timeout):
    """Show blast radius: what breaks if SYMBOL changes.

    SYMBOL is a symbol identifier (bare name or qualified name). Unlike
    ``uses`` (which lists direct callers), this command computes the
    transitive blast radius (bounded by default) including affected
    files and PageRank-weighted importance.

    \b
    Examples:
      roam impact handle_login
      roam impact User --depth 5
      roam impact useThemeClasses --max-callers 200 --timeout 60
      roam --json impact MyClass.method

    See also ``uses`` (direct callers only), ``preflight`` (full
    pre-change checklist), and ``trace`` (k-shortest call paths).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # ``--hops`` is the legacy alias; if user passed it, it overrides
    # ``--depth`` (preserves prior behavior of explicit unbounded
    # opt-in via large --hops). 0 means unbounded.
    if hops is not None:
        effective_depth = None if hops <= 0 else int(hops)
    else:
        effective_depth = None if depth <= 0 else int(depth)
    effective_max_callers = None if max_callers <= 0 else int(max_callers)
    deadline = (time.monotonic() + float(timeout)) if timeout and timeout > 0 else None

    # W607-T -- substrate-CALL-boundary warnings_out accumulator. cmd_impact
    # composes 4-5 substrate consumers (find_symbol resolution, the
    # build_symbol_graph builder, _collect_dependents BFS / sf-test lookup,
    # personalized_pagerank weighting, _find_indirect_refs registry scan).
    # Each helper has its own internal floors for the common-case error
    # shapes (the W336 ImportError swallow on personalized_pagerank is
    # preserved -- it's a distinct floor, NOT a substrate failure to
    # disclose), but a helper itself can still raise BEFORE producing a
    # safe floor (downstream SQL-shape refactor, networkx blowing up on a
    # corrupted edge row, build_symbol_graph import-time failure,
    # sqlite3.OperationalError on a missing table). The outer call sites in
    # ``impact()`` previously had no guards, so the envelope crashed whole.
    # W607-T wraps each substrate boundary with ``_run_check(phase, fn, *args)``
    # so the raise becomes a ``impact_<phase>_failed:<exc_class>:<detail>``
    # marker via ``_w607t_warnings_out`` and the envelope still emits the
    # remaining sections cleanly.
    #
    # Marker family ``impact_*`` -- distinct from W607-S's ``diagnose_*``,
    # W607-R's ``preflight_*``, W607-Q's ``pr_risk_*``, W607-P's ``audit_*``,
    # W607-O's ``dashboard_*``, W607-N's ``doctor_*``, W607-M's ``health_*``,
    # W607-L's ``minimap_*``, W607-K's ``describe_*``. The marker-prefix
    # discipline test pins this closed-enum distinction.
    #
    # Empty bucket -> byte-identical envelope (no warnings_out key in
    # either summary or top-level, no W607-T-driven partial_success flip;
    # the W1242 resolution-disclosure path still flips partial_success on
    # its own axis).
    _w607t_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-T marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``impact_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607t_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607t_warnings_out.append(f"impact_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BB -- ADDITIVE plumbing on top of the W607-T substrate-CALL
    # markers. W607-T already wrapped the five substrate-helper boundaries
    # (resolve_symbol / build_graph / collect_dependents / indirect_refs /
    # verdict_synthesis); W607-BB extends marker coverage to the
    # AGGREGATION-PHASE boundaries that W607-T left unguarded:
    #
    #   - ``weighted_impact``    -- ``sum(...) + round(..., 6)`` rollup
    #                               (W336/W439 weighted-impact rounding axis)
    #   - ``risk_classify``      -- ``_impact_risk_level(...)`` domain tier
    #   - ``risk_normalize``     -- ``normalize_risk_level(...) + risk_rank(...)``
    #                               canonical-projection cluster
    #   - ``auto_log``           -- active-run ledger write (silent no-op if
    #                               no run is active, but the underlying
    #                               ``auto_log`` can still raise on HMAC
    #                               chain misshape or filesystem failures)
    #   - ``serialize_sarif``    -- ``impact_to_sarif(...)`` projection
    #                               (only fires on --sarif paths)
    #
    # cmd_impact is the BLAST-RADIUS COMPANION to cmd_preflight (the
    # AGENT-OS PRE-EDIT SAFETY GATE) per CLAUDE.md LAW 1. cmd_preflight
    # delegates to the same blast-radius substrate cmd_impact owns. A
    # silent crash inside an aggregation-phase boundary here would either
    # (a) propagate up through preflight and defeat the change-safety gate,
    # or (b) crash the standalone blast-radius envelope after all five
    # substrate signals were already gathered. W607-BB wraps the post-
    # compute boundaries so the envelope still surfaces a marker even
    # when the aggregation phase itself raises.
    #
    # Marker family ``impact_*`` -- same family as W607-T (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope.
    _w607bb_warnings_out: list[str] = []

    def _run_check_bb(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-BB marker emission.

        Mirror of ``_run_check`` shape (same ``impact_<phase>_failed:``
        marker family) but writes into ``_w607bb_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bb_warnings_out.append(f"impact_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        sym = _run_check("resolve_symbol", find_symbol, conn, name, default=None)
        if sym is None:
            # W1272 — Pattern-2c Convention (c): unresolved is a real
            # success of "I tried and there's nothing to analyze". The
            # envelope discloses resolution=unresolved + partial_success
            # so downstream agents see the degraded outcome explicitly,
            # but the exit code stays 0 so CI doesn't conflate a
            # name-typo with a tool/IO failure. Pre-W1272 this branch
            # auto-logged the miss + raised SystemExit(1); per the
            # W1268 audit the auto-log is reserved for success-path
            # blast-radius events and exit-0 is the canonical
            # Convention (c) shape (cf. cmd_dead --extinction).
            #
            # W1277 — RESTORE auto_log on the unresolved path. The W1276
            # "no auto_log on not-found" stance created a signal-loss
            # risk on the replay-narration surface: when an agent runs
            # ``roam impact <typo>`` and gets a Convention-c envelope
            # back, the run ledger no longer carried any trace that the
            # attempt happened. Under Convention (c), unresolved IS a
            # real success (of the "nothing to analyze" kind), so it
            # belongs in the agent-decision timeline alongside resolved
            # attempts. The envelope's partial_success=True + the
            # ``resolution: unresolved`` field on summary let the
            # replay-narrator render "agent tried to impact <name> →
            # unresolved" rather than rendering silence.
            unresolved_disclosure = resolution_disclosure("unresolved", target=name or "")
            # W641-followup-A — canonical risk-LEVEL projection. An unresolved
            # symbol has zero blast radius (we tried and there's nothing to
            # analyze), so the canonical rank is the W631 floor "low". Emitted
            # unconditionally so an agent can call
            # ``risk_rank(summary["risk_level_canonical"])`` without
            # None-handling on every envelope shape.
            _not_found_canonical = normalize_risk_level("low") or "low"
            _nf_summary: dict = {
                "verdict": f"Symbol '{name}' not found (risk_level {_not_found_canonical})",
                "partial_success": True,
                "state": "not_found",
                "risk_level_canonical": _not_found_canonical,
                "risk_rank": risk_rank(_not_found_canonical),
                **unresolved_disclosure,
            }
            _nf_kwargs: dict = {
                "summary": _nf_summary,
                "symbol": name or "",
                "risk_level_canonical": _not_found_canonical,
                "risk_rank": risk_rank(_not_found_canonical),
                **unresolved_disclosure,
            }
            # W607-T -- surface substrate-CALL markers on the not-found path.
            # partial_success is already True on this branch (unresolved
            # disclosure), but we still thread warnings_out top-level +
            # summary mirror so consumers see the raise that landed us here.
            # W607-T + W607-BB — combine substrate-CALL and aggregation-phase
            # buckets BEFORE threading into the envelope so consumers see the
            # full degradation lineage in marker-emission order. Empty
            # combined bucket on the not-found path collapses to no
            # warnings_out keys (no envelope-shape change).
            _combined_nf = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
            if _combined_nf:
                _nf_summary["warnings_out"] = list(_combined_nf)
                _nf_kwargs["warnings_out"] = list(_combined_nf)
            not_found_env = json_envelope("impact", **_nf_kwargs)
            # W1277 — auto-log the unresolved attempt for replay-narration
            # provenance. Silent no-op if no active run.
            # W607-BB — wrap the active-run write so HMAC chain-misshape /
            # filesystem failures / .roam/runs corruption surface as
            # ``impact_auto_log_failed:...`` instead of crashing the
            # envelope after it was already built.
            _run_check_bb(
                "auto_log",
                auto_log,
                not_found_env,
                action="impact",
                target=name or "",
                default=None,
            )
            # W607-BB — if ``auto_log`` raised, rebuild the not-found envelope
            # so the marker reaches the JSON output. Empty bucket (clean
            # auto_log) -> not_found_env stays byte-identical to the version
            # already built above.
            if _w607bb_warnings_out and not any(
                m.startswith("impact_auto_log_failed:") for m in (_nf_summary.get("warnings_out") or [])
            ):
                _combined_nf = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
                _nf_summary["warnings_out"] = list(_combined_nf)
                _nf_kwargs["warnings_out"] = list(_combined_nf)
                not_found_env = json_envelope("impact", **_nf_kwargs)
            if json_mode:
                click.echo(to_json(not_found_env))
            else:
                # Preserve the suggestion list in text mode — it remains
                # the most useful next step for a human user staring at a
                # typo. ``symbol_not_found`` is text-only here (json_mode
                # is False).
                click.echo(symbol_not_found(conn, name, json_mode=False))
            return
        sym_id = sym["id"]
        # W1242 / W1249 — Pattern-2 variant-D: ``find_symbol`` stamps
        # ``_resolution_tier`` on the returned row so the envelope can
        # distinguish a fully-resolved success from a degraded fuzzy-match
        # success that may have landed on a different target. Drives the
        # resolution disclosure merged into every envelope branch below +
        # the optional verdict suffix.
        resolution_tier = sym.get("_resolution_tier", "symbol")
        resolution_block = resolution_disclosure(resolution_tier, target=sym["qualified_name"] or sym["name"])

        if not json_mode:
            click.echo(
                f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  {loc(sym['file_path'], sym['line_start'])}"
            )
            click.echo()

        # W607-T -- the graph builder is the next substrate. Floor to an
        # empty ``nx.DiGraph()`` so the isolated/in-graph branch catches a
        # failure uniformly (sym_id is never in an empty graph -> the
        # not_in_graph envelope path fires with the marker attached).
        # ``build_symbol_graph`` is imported at module top-level (hoisted
        # from the old lazy ``try/except ImportError`` block per W607-T) so
        # tests can monkeypatch it at the module-attribute boundary.
        import networkx as _nx_for_floor

        G = _run_check(
            "build_graph",
            build_symbol_graph,
            conn,
            default=_nx_for_floor.DiGraph(),
        )
        if sym_id not in G:
            # W641-followup-A — canonical risk-LEVEL projection. A symbol
            # outside the dependency graph has no measurable blast radius —
            # rank "low" by W631 polarity. Emitted unconditionally so a
            # downstream agent reading the envelope can floor-compare without
            # branching on the not-in-graph state.
            _ngraph_canonical = normalize_risk_level("low") or "low"
            verdict = f"Symbol '{name}' exists in the index but is not in the dependency graph."
            if resolution_tier == "fuzzy":
                verdict = f"{verdict} [fuzzy resolution -- target '{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            verdict = f"{verdict} (risk_level {_ngraph_canonical})"
            tip = f"Run `roam index` to rebuild the graph, or use `roam symbol {name}` to view raw symbol data."
            _ng_summary: dict = {
                "verdict": verdict,
                "affected_symbols": 0,
                "affected_files": 0,
                "in_graph": False,
                "risk_level_canonical": _ngraph_canonical,
                "risk_rank": risk_rank(_ngraph_canonical),
                # W331: stamp definitions so MCP consumers see the
                # same envelope shape even when the target is not in
                # the dependency graph.
                "affected_symbols_definition": BLAST_RADIUS_AFFECTED_SYMBOLS,
                "affected_files_definition": BLAST_RADIUS_AFFECTED_FILES,
                # W1242 — Pattern-2 variant-D resolution disclosure.
                **resolution_block,
            }
            _ng_kwargs: dict = {
                "budget": token_budget,
                "summary": _ng_summary,
                "symbol": sym["qualified_name"] or sym["name"],
                "tip": tip,
                "direct_dependents": {},
                "affected_file_list": [],
                "indirect_refs": [],
                "risk_level_canonical": _ngraph_canonical,
                "risk_rank": risk_rank(_ngraph_canonical),
                **resolution_block,
            }
            # W607-T -- surface substrate-CALL markers on the not-in-graph
            # path (also fires when build_graph failed and floored to an
            # empty DiGraph). partial_success flips True so consumers see
            # the substrate degradation alongside the missing-from-graph
            # state.
            # W607-T + W607-BB — combine both buckets BEFORE threading into
            # the envelope so the not-in-graph degradation lineage is visible
            # in marker-emission order.
            _combined_ng = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
            if _combined_ng:
                _ng_summary["warnings_out"] = list(_combined_ng)
                _ng_summary["partial_success"] = True
                _ng_kwargs["warnings_out"] = list(_combined_ng)
                _ng_kwargs["partial_success"] = True
            not_in_graph_env = json_envelope("impact", **_ng_kwargs)
            # W15.2 — auto-log into the active run. Silent no-op if no run.
            # W607-BB — wrap so HMAC chain-misshape / fs failures surface as
            # ``impact_auto_log_failed:...`` instead of crashing.
            _run_check_bb(
                "auto_log",
                auto_log,
                not_in_graph_env,
                action="impact",
                target=name or "",
                default=None,
            )
            # W607-BB — rebuild on auto_log raise so the marker reaches the
            # JSON output. Empty bucket -> envelope is byte-identical.
            if _w607bb_warnings_out and not any(
                m.startswith("impact_auto_log_failed:") for m in (_ng_summary.get("warnings_out") or [])
            ):
                _combined_ng = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
                _ng_summary["warnings_out"] = list(_combined_ng)
                _ng_summary["partial_success"] = True
                _ng_kwargs["warnings_out"] = list(_combined_ng)
                _ng_kwargs["partial_success"] = True
                not_in_graph_env = json_envelope("impact", **_ng_kwargs)
            if sarif_mode:
                # W1165: SARIF projection for CI / GitHub Code Scanning.
                # The auto_log call above stays identical across formats so
                # the audit ledger is invariant.
                from roam.output.sarif import impact_to_sarif, write_sarif

                click.echo(write_sarif(impact_to_sarif(not_in_graph_env)))
            elif json_mode:
                click.echo(to_json(not_in_graph_env))
            else:
                click.echo(f"{verdict}\n  Tip: {tip}")
            return

        RG = G.reverse()
        # W607-T -- _collect_dependents composes BFS + sf-test SQL lookups,
        # either of which can raise on a corrupted edge row or SQL-shape
        # regression. Floor to the same 6-tuple shape downstream consumers
        # expect (empty sets + clean BFS state) so the success path still
        # emits a leaf-style envelope with the marker attached.
        _collect_floor = (
            set(),  # dependents
            set(),  # affected_files
            set(),  # direct_callers
            {},  # by_kind
            set(),  # sf_test_files
            {"hit_caller_cap": False, "hit_depth_cap": False, "hit_timeout": False},
        )
        (
            dependents,
            affected_files,
            direct_callers,
            by_kind,
            sf_test_files,
            bfs_state,
        ) = _run_check(
            "collect_dependents",
            _collect_dependents,
            G,
            RG,
            sym_id,
            conn,
            max_hops=effective_depth,
            max_callers=effective_max_callers,
            deadline=deadline,
            default=_collect_floor,
        )
        truncated = bfs_state["hit_caller_cap"] or bfs_state["hit_depth_cap"] or bfs_state["hit_timeout"]
        if bfs_state["hit_timeout"]:
            run_state = "timeout"
        elif bfs_state["hit_caller_cap"]:
            run_state = "caller_cap"
        elif bfs_state["hit_depth_cap"]:
            run_state = "depth_cap"
        else:
            run_state = "ok"

        # W335/W342 Pattern-3a — HONEST UNCAPPED TOTAL. The BFS above is
        # bounded by ``--depth`` / ``--max-callers`` / ``--timeout`` so the
        # ``dependents`` SET (and every displayed list derived from it) stays
        # response-size-safe. But the reported COUNT must agree with
        # ``preflight``'s blast-radius gate, which runs the FULL transitive
        # ``nx.descendants`` reverse-reachability. Compute that uncapped total
        # here using the IDENTICAL computation preflight uses
        # (cmd_preflight._check_blast_radius) so the two commands are provably
        # reporting the same metric for the same target. The verdict + summary
        # counts below reflect this TRUE total; only the listed dependents stay
        # capped. ``cap_applied`` makes the truncation LOUD (Pattern-1 variant-D
        # lineage disclosure) instead of silently contradicting preflight.
        own_file_path = sym["file_path"]
        total_symbols, total_files = _run_check(
            "blast_total",
            _uncapped_blast_total,
            G,
            RG,
            sym_id,
            own_file_path,
            default=(len(dependents), len(affected_files)),
        )
        # Floor: the uncapped total can never be SMALLER than what the bounded
        # BFS already reached (a depth/caller cap only ever drops dependents).
        # If the uncapped pass floored (raise -> default), keep it consistent.
        total_symbols = max(total_symbols, len(dependents))
        total_files = max(total_files, len(affected_files))
        displayed_symbols = len(dependents)
        displayed_files = len(affected_files)
        cap_applied = total_symbols > displayed_symbols or total_files > displayed_files

        # Personalized PageRank for distance-weighted importance (Gleich 2015).
        # W336 — use the shared ``personalized_pagerank`` helper so we get the
        # numpy-free degree-based fallback when scipy/numpy aren't installed.
        # The bare ``nx.pagerank`` call previously raised ImportError on such
        # environments, the bare ``except`` swallowed it, ppr stayed empty,
        # and weighted_impact silently zeroed regardless of true blast radius.
        ppr: dict[int, float] = {}
        if dependents:
            try:
                from roam.graph.pagerank import personalized_pagerank

                ppr = personalized_pagerank(RG, {sym_id: 1.0}, alpha=0.85)
            except Exception:
                pass

        if not dependents:
            # W641-followup-A — canonical risk-LEVEL projection. A leaf
            # symbol with zero dependents is the safest possible state:
            # canonical rank "low" (W631 floor). Augment the verdict with
            # the canonical bucket per LAW 6 (verdict line works standalone).
            _no_dep_canonical = normalize_risk_level("low") or "low"
            no_dep_verdict = f"no dependents (risk_level {_no_dep_canonical})"
            if resolution_tier == "fuzzy":
                no_dep_verdict = f"{no_dep_verdict} [fuzzy resolution -- target '{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            _nd_summary: dict = {
                "verdict": no_dep_verdict,
                "affected_symbols": 0,
                "affected_files": 0,
                "risk_level_canonical": _no_dep_canonical,
                "risk_rank": risk_rank(_no_dep_canonical),
                # W331: even on the leaf-symbol path the consumer
                # still needs to know what these zero counts measure.
                "affected_symbols_definition": BLAST_RADIUS_AFFECTED_SYMBOLS,
                "affected_files_definition": BLAST_RADIUS_AFFECTED_FILES,
                "weighted_impact_definition": WEIGHTED_IMPACT_DEFINITION,
                "reach_pct_definition": REACH_PCT_DEFINITION,
                # W1242 — Pattern-2 variant-D resolution disclosure.
                **resolution_block,
            }
            _nd_kwargs: dict = {
                "budget": token_budget,
                "summary": _nd_summary,
                "symbol": sym["qualified_name"] or sym["name"],
                "affected_symbols": 0,
                "affected_files": 0,
                "direct_dependents": {},
                "affected_file_list": [],
                "risk_level_canonical": _no_dep_canonical,
                "risk_rank": risk_rank(_no_dep_canonical),
                **resolution_block,
            }
            # W607-T -- surface substrate-CALL markers on the leaf path.
            # Empty bucket -> byte-identical envelope. Non-empty bucket ->
            # warnings_out at top-level AND summary mirror, plus
            # partial_success=True so consumers see the substrate
            # degradation even on a no-dependents leaf.
            # W607-T + W607-BB — combine both buckets BEFORE threading into
            # the envelope so the leaf-symbol degradation lineage is visible
            # in marker-emission order.
            _combined_nd = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
            if _combined_nd:
                _nd_summary["warnings_out"] = list(_combined_nd)
                _nd_summary["partial_success"] = True
                _nd_kwargs["warnings_out"] = list(_combined_nd)
                _nd_kwargs["partial_success"] = True
            no_dep_env = json_envelope("impact", **_nd_kwargs)
            # W15.2 — auto-log into the active run. Silent no-op if no run.
            # W607-BB — wrap auto_log so HMAC chain-misshape / fs failures
            # surface as ``impact_auto_log_failed:...`` markers.
            _run_check_bb(
                "auto_log",
                auto_log,
                no_dep_env,
                action="impact",
                target=name or "",
                default=None,
            )
            # W607-BB — rebuild on auto_log raise so the marker reaches the
            # JSON output. Empty bucket -> envelope is byte-identical.
            if _w607bb_warnings_out and not any(
                m.startswith("impact_auto_log_failed:") for m in (_nd_summary.get("warnings_out") or [])
            ):
                _combined_nd = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
                _nd_summary["warnings_out"] = list(_combined_nd)
                _nd_summary["partial_success"] = True
                _nd_kwargs["warnings_out"] = list(_combined_nd)
                _nd_kwargs["partial_success"] = True
                no_dep_env = json_envelope("impact", **_nd_kwargs)
            if sarif_mode:
                # W1165: SARIF projection for CI / GitHub Code Scanning.
                from roam.output.sarif import impact_to_sarif, write_sarif

                click.echo(write_sarif(impact_to_sarif(no_dep_env)))
            elif json_mode:
                click.echo(to_json(no_dep_env))
            else:
                click.echo("VERDICT: no dependents — safe to change")
            return

        # W607-BB — wrap the weighted_impact rollup compute. This is the
        # W336/W439 weighted-impact rounding axis (widened from 4 -> 6
        # decimals so per-node PageRank values on a 20k-symbol graph stay
        # non-zero). A malformed ppr dict (non-float values from a future
        # personalized_pagerank refactor) would raise TypeError on the
        # sum(...) generator; the wrap floors to ``0.0`` so the envelope
        # still emits the raw dependents/affected_files counts alongside
        # the marker. The rounding stays canonical (round(..., 6) per W336),
        # NOT the historical truncation that W439 sealed.
        def _compute_weighted_impact() -> float:
            return sum(ppr.get(d, 0) for d in dependents)

        weighted_impact = _run_check_bb(
            "weighted_impact",
            _compute_weighted_impact,
            default=0.0,
        )

        # dispatch-via-registry detection. roam's call graph
        # only sees direct calls; consumers that route through string
        # lookup tables (cli ``_COMMANDS``, ask recipe registry, plugin
        # entry points) are invisible. Scan source files for string
        # literals matching this symbol's name and qualified name to
        # surface those callsites as ``indirect_refs``.
        #
        # W607-T -- the registry scan walks every source-file row + reads
        # disk; OSError / sqlite3.OperationalError can raise here.
        # Floor to an empty list so the envelope still emits cleanly with
        # the marker attached.
        indirect_refs = _run_check(
            "indirect_refs",
            _find_indirect_refs,
            conn,
            sym,
            affected_files,
            default=[],
        )
        # W607-T -- the verdict synthesis is the only purely-arithmetic
        # substrate here, but a corrupted graph (len(G) == 0 while
        # dependents is non-empty) would still emit a usable verdict --
        # the wrap is precautionary, not load-bearing. Default mirrors
        # the leaf-symbol verdict shape so the envelope can still emit.
        verdict, reach_pct = _run_check(
            "verdict_synthesis",
            _impact_verdict,
            dependents,
            affected_files,
            len(G),
            affected_count=total_symbols,
            affected_files_count=total_files,
            default=("No dependents — safe to change", 0.0),
        )
        if cap_applied:
            # W335/W342 Pattern-3a — the verdict COUNT above is the honest
            # uncapped total (agrees with preflight); the LISTED dependents are
            # capped for response size. Disclose the display cap LOUDLY so an
            # agent reading only the verdict knows the symbol/file lists are a
            # subset, NOT that the count itself is partial (Pattern-1 variant-D
            # lineage disclosure). Imperative, concrete (LAWs 2, 4).
            _limit_notes = []
            _raise_flags = []
            if bfs_state["hit_timeout"]:
                _limit_notes.append(f"timeout={timeout}s")
                _raise_flags.append("--timeout")
            if bfs_state["hit_caller_cap"]:
                _limit_notes.append(f"max-callers={effective_max_callers}")
                _raise_flags.append("--max-callers")
            if bfs_state["hit_depth_cap"]:
                _limit_notes.append(f"depth={effective_depth}")
                _raise_flags.append("--depth")
            _cap_cause = f" ({', '.join(_limit_notes)})" if _limit_notes else ""
            # Name the exact flag(s) that capped the display so the next step is
            # executable (CONSTRAINT 12, LAW 2). Falls back to --max-callers.
            _raise = "/".join(_raise_flags) if _raise_flags else "--max-callers"
            verdict = (
                f"{verdict} — listing {displayed_symbols} of {total_symbols} affected symbols"
                f"{_cap_cause}; raise {_raise} to list more"
            )
        # W1242 — Pattern-2 variant-D: surface fuzzy-resolution in the verdict
        # so text-only consumers see the degradation. The exact target the
        # resolver landed on goes into the suffix so an agent can decide to
        # re-run with the precise qualified name.
        if resolution_tier == "fuzzy":
            verdict = (
                f"{verdict} [fuzzy resolution -- "
                f"target '{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            )

        # W641-followup-A — canonical risk-LEVEL projection. The local
        # 4-tier blast-radius polarity (Large/Moderate/Small/None — see
        # ``_impact_verdict`` + ``_impact_risk_level``) projects onto the
        # canonical W631 vocabulary via :func:`normalize_risk_level`. The
        # ``or "low"`` floor mirrors the W531 CI-safety lesson (a typo'd
        # label must NOT promote a finding into a CI-failing rank) and
        # matches the same pattern as cmd_pr_risk's W641 emit. The integer
        # ``risk_rank`` floor lets cross-command consumers compare blast
        # radius against e.g. ``migration_plan --max-risk medium`` without
        # re-deriving the W631 rank table at the call site (Pattern-3a).
        # W607-BB — wrap the domain-tier classify + canonical-projection
        # cluster. A future signature change on ``_impact_risk_level``
        # (e.g. accepting weighted_impact as a third argument) or a
        # ``normalize_risk_level`` regression would raise here; the wrap
        # floors both axes to the W631 "low" rank + integer 1 so the
        # envelope still emits the raw callers/callees lists with a
        # ``risk_level_canonical: "low"`` sentinel and a
        # ``risk_classification: "unknown"`` summary mirror (set below
        # only if classify or normalize raised).
        # W335/W342 — classify on the HONEST uncapped total + true reach_pct
        # so the risk tier agrees with preflight's blast severity rather than
        # being understated by the display cap.
        _impact_domain_level = _run_check_bb(
            "risk_classify",
            _impact_risk_level,
            total_symbols,
            reach_pct,
            default=None,
        )
        # Domain-tier raised -> mark classification unknown so the envelope
        # discloses the degraded outcome.
        _risk_classification_state = "unknown" if _impact_domain_level is None else "classified"
        risk_level_canonical = _run_check_bb(
            "risk_normalize",
            lambda level: normalize_risk_level(level) or "low",
            _impact_domain_level,
            default="low",
        )
        risk_rank_int = _run_check_bb(
            "risk_normalize",
            risk_rank,
            risk_level_canonical,
            default=1,
        )
        # Verdict augmentation per LAW 6 (line works standalone): append the
        # canonical risk_level in a closed-enum parenthesis so a consumer
        # parsing only the verdict string sees the canonical bucket directly.
        verdict = f"{verdict} (risk_level {risk_level_canonical})"

        # Build the full envelope so we can both auto-log and emit it.
        # Look up global PageRank for dependent symbols (used in JSON path and
        # in text-mode "Affected files" ranking).
        global_pr: dict[int, float] = {}
        try:
            pr_rows = conn.execute("SELECT symbol_id, pagerank FROM graph_metrics").fetchall()
            global_pr = {r["symbol_id"]: r["pagerank"] for r in pr_rows}
        except Exception:
            pass

        json_deps = {ek: [{"name": i[1], "kind": i[0], "file": i[2]} for i in items] for ek, items in by_kind.items()}
        # Build affected file list with importance scores.
        # File importance = max PageRank of any dependent symbol in that file.
        file_importance: dict[str, float] = {}
        for dep_id in dependents:
            node = G.nodes.get(dep_id, {})
            fp = node.get("file_path", "?")
            pr_val = global_pr.get(dep_id, ppr.get(dep_id, 0.0))
            if pr_val > file_importance.get(fp, 0.0):
                file_importance[fp] = pr_val

        affected_file_dicts = [
            {"path": fp, "importance": round(file_importance.get(fp, 0.0), 6)} for fp in sorted(affected_files)
        ]
        limits_block = {
            "depth": effective_depth,
            "max_callers": effective_max_callers,
            "timeout_s": float(timeout) if timeout and timeout > 0 else None,
        }
        # W1242 — Pattern-2 variant-D: partial_success is true when EITHER
        # the BFS truncated OR the resolver landed on a fuzzy match. Both
        # conditions degrade the verdict's reliability; agents need to see
        # both via one flag.
        is_partial = truncated or resolution_tier != "symbol"
        # W335/W342 Pattern-3a — cap-disclosure block. The reported COUNT
        # (``affected_symbols`` / ``affected_files``) is now the TRUE uncapped
        # total so it AGREES with preflight; the displayed lists stay capped.
        # ``cap_applied`` + ``displayed`` + ``total`` make the truncation LOUD
        # (Pattern-1 variant-D lineage disclosure) so a consumer comparing
        # impact↔preflight sees the same number AND knows the listed subset is
        # capped. Stamped on both summary and top-level for either reader.
        _cap_block: dict = {
            "affected_symbols_total": total_symbols,
            "affected_files_total": total_files,
            "cap_applied": cap_applied,
            "displayed": displayed_symbols,
            "total": total_symbols,
            "displayed_files": displayed_files,
        }
        _success_summary: dict = {
            "verdict": verdict,
            # W335/W342 — TRUE uncapped totals (match preflight). The capped
            # display subset is surfaced via ``displayed`` / ``displayed_files``
            # in the cap-disclosure block below.
            "affected_symbols": total_symbols,
            "affected_files": total_files,
            **_cap_block,
            # W336 — widen rounding from 4 -> 6 decimals. Per-node PageRank
            # values on a 20k-symbol graph fall in the 1e-5 to 1e-3 range,
            # so 4-decimal rounding truncated legitimate small sums to 0
            # even when 100 affected symbols were reached. 6 decimals
            # keeps the value human-readable AND non-zero for any real
            # blast radius.
            "weighted_impact": round(weighted_impact, 6),
            "reach_pct": round(reach_pct, 1),
            # W641-followup-A — canonical W631 risk-LEVEL projection +
            # integer rank. Projected from the local domain blast-radius
            # tier via ``_impact_risk_level`` so cross-command floor
            # comparators ("is this impact worse than migration_plan's
            # ``--max-risk medium``?") work without each consumer
            # re-deriving the threshold table at the call site (Pattern-3a).
            "risk_level_canonical": risk_level_canonical,
            "risk_rank": risk_rank_int,
            # W607-BB -- RISK-CLASSIFY DEGRADATION sentinel. When the
            # ``risk_classify`` boundary raises (and the classify result
            # floors to ``None``), surface ``risk_classification: "unknown"``
            # so the agent sees the degraded outcome alongside the canonical
            # floor ("low") rather than mistaking the floor for a real
            # classification. Empty bucket -> ``"classified"`` (clean path).
            "risk_classification": _risk_classification_state,
            "sf_convention_tests": len(sf_test_files),
            "truncated": truncated,
            "partial_success": is_partial,
            "state": run_state,
            "limits": limits_block,
            # W331: stamp definitions so consumers know exactly what
            # the four blast-radius numbers represent. Strings live
            # in roam.output.metric_definitions to prevent drift
            # between cmd_impact and cmd_preflight.
            "affected_symbols_definition": BLAST_RADIUS_AFFECTED_SYMBOLS,
            "affected_files_definition": BLAST_RADIUS_AFFECTED_FILES,
            # W335/W342 Pattern-3a — name the EXACT computation behind the
            # uncapped totals so impact↔preflight are provably the same
            # metric. Both run nx.descendants over the reverse graph.
            "affected_metric_definition": BLAST_RADIUS_AFFECTED_TOTAL,
            "weighted_impact_definition": WEIGHTED_IMPACT_DEFINITION,
            "reach_pct_definition": REACH_PCT_DEFINITION,
            # W1242 — Pattern-2 variant-D resolution disclosure. The
            # helper sets ``partial_success`` to ``resolution != "symbol"``,
            # so we override above with the combined-OR semantics.
            **{k: v for k, v in resolution_block.items() if k != "partial_success"},
        }
        _success_kwargs: dict = {
            "budget": token_budget,
            "summary": _success_summary,
            "symbol": sym["qualified_name"] or sym["name"],
            # W335/W342 — TRUE uncapped totals (match preflight) at top-level
            # too; cap-disclosure block names the displayed subset.
            "affected_symbols": total_symbols,
            "affected_files": total_files,
            **_cap_block,
            "affected_metric_definition": BLAST_RADIUS_AFFECTED_TOTAL,
            "weighted_impact": round(weighted_impact, 6),
            "reach_pct": round(reach_pct, 1),
            # W641-followup-A — top-level mirror of summary.risk_level_canonical
            # / summary.risk_rank so consumers reading the top-level envelope
            # (not just ``summary``) get the same canonical bucket without
            # re-deriving it. Mirrors the W641 cmd_pr_risk pattern.
            "risk_level_canonical": risk_level_canonical,
            "risk_rank": risk_rank_int,
            "direct_dependents": json_deps,
            "affected_file_list": affected_file_dicts,
            "sf_convention_tests": sorted(sf_test_files),
            "indirect_refs": indirect_refs,
            "truncated": truncated,
            "partial_success": is_partial,
            "state": run_state,
            "limits": limits_block,
            **{k: v for k, v in resolution_block.items() if k != "partial_success"},
        }
        # W607-T -- surface substrate-CALL markers on the success path.
        # Empty bucket -> byte-identical envelope (no warnings_out keys
        # added; no partial_success flip from this axis -- the W1242
        # resolution-disclosure + truncation axes still flip it on their
        # own). Non-empty bucket -> warnings_out at top-level AND summary
        # mirror, plus summary.partial_success=True so the agent can
        # distinguish "clean impact" from "impact ran with substrate
        # degradation" via the summary alone.
        # W607-T + W607-BB -- combine substrate-CALL and aggregation-phase
        # buckets BEFORE threading into the envelope. Both buckets share the
        # ``impact_*`` marker family (W607-BB is additive coverage of
        # aggregation-phase boundaries on top of W607-T's helper-call
        # boundaries). Empty combined bucket -> byte-identical envelope (no
        # warnings_out keys added; no partial_success flip from this axis --
        # the W1242 resolution-disclosure + truncation axes still flip it on
        # their own). Non-empty bucket -> warnings_out at top-level AND
        # summary mirror, plus summary.partial_success=True so the agent can
        # distinguish "clean impact" from "impact ran with substrate
        # degradation" via the summary alone.
        _combined_warnings_out = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
        if _combined_warnings_out:
            _success_summary["warnings_out"] = list(_combined_warnings_out)
            _success_summary["partial_success"] = True
            _success_kwargs["warnings_out"] = list(_combined_warnings_out)
            _success_kwargs["partial_success"] = True
        impact_env = json_envelope("impact", **_success_kwargs)
        # W15.2 — auto-log into the active run (silent no-op if none).
        # W607-BB -- wrap so HMAC chain-misshape / fs failures surface as
        # ``impact_auto_log_failed:...`` markers and the envelope still
        # reaches stdout.
        _run_check_bb(
            "auto_log",
            auto_log,
            impact_env,
            action="impact",
            target=name or "",
            default=None,
        )
        # W607-BB -- if ``auto_log`` raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log) ->
        # impact_env stays byte-identical to the version built above.
        if _w607bb_warnings_out and not any(
            m.startswith("impact_auto_log_failed:") for m in (_success_summary.get("warnings_out") or [])
        ):
            _combined_warnings_out = list(_w607t_warnings_out) + list(_w607bb_warnings_out)
            _success_summary["warnings_out"] = list(_combined_warnings_out)
            _success_summary["partial_success"] = True
            _success_kwargs["warnings_out"] = list(_combined_warnings_out)
            _success_kwargs["partial_success"] = True
            impact_env = json_envelope("impact", **_success_kwargs)

        if sarif_mode:
            # W1165: SARIF projection for CI / GitHub Code Scanning
            # integration. The auto_log call above stays identical to
            # the JSON / text paths so the audit ledger is invariant
            # across output formats. The --text / --json paths are
            # byte-identical to pre-W1165 (this branch short-circuits
            # before the legacy branches; nothing above it changed shape).
            from roam.output.sarif import impact_to_sarif, write_sarif

            # W607-BB -- wrap the SARIF projection. The helper walks the
            # envelope dict + builds the SARIF result list; a malformed
            # envelope shape (post-refactor field rename) would raise
            # KeyError here. Floor to an empty SARIF stub so CI consumers
            # still receive a valid SARIF document with the marker
            # attached.
            _sarif_floor = {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json", "runs": []}
            sarif_doc = _run_check_bb(
                "serialize_sarif",
                impact_to_sarif,
                impact_env,
                default=_sarif_floor,
            )
            click.echo(write_sarif(sarif_doc))
            return

        if json_mode:
            click.echo(to_json(impact_env))
            return

        click.echo(f"VERDICT: {verdict}\n")
        # W335/W342 Pattern-3a — report the HONEST uncapped totals (agree with
        # preflight) and disclose the display cap so the counts never silently
        # contradict the peer command. "100 of 1847 affected symbols (capped)".
        if cap_applied:
            click.echo(
                f"Affected symbols: {displayed_symbols} of {total_symbols} (capped)  "
                f"Affected files: {displayed_files} of {total_files} (capped)"
            )
        else:
            click.echo(f"Affected symbols: {total_symbols}  Affected files: {total_files}")
        if indirect_refs:
            click.echo(
                f"Indirect refs (registry / string-dispatch): {len(indirect_refs)} site(s) — "
                "agent-blast may be larger than direct call graph indicates"
            )
        click.echo()

        if by_kind:
            for edge_kind in sorted(by_kind.keys()):
                items = by_kind[edge_kind]
                click.echo(f"Direct dependents ({edge_kind}, {len(items)}):")
                click.echo(format_table(["kind", "name", "file"], items, budget=15))
                click.echo()
            if len(dependents) > len(direct_callers):
                click.echo(f"(+{len(dependents) - len(direct_callers)} transitive dependents)")

        if affected_files:
            # 12.13 — rank files by max-dependent PageRank instead of
            # alphabetically. The user reading "Affected files" wants
            # to know which files matter most — alphabetical order
            # surfaced ``benchmarks/`` and ``bench-repos/`` ahead of
            # the actually-important ``src/roam/cli.py`` for queries
            # against this repo. PageRank-ranked top-20 puts the
            # impactful files first; the rest are cut by the +N more
            # tail.
            try:
                from roam.graph.pagerank import global_pagerank

                _global_pr = global_pagerank(G)
            except Exception:
                _global_pr = {}
            _file_pr: dict[str, float] = {}
            for dep_id in dependents:
                fp = G.nodes.get(dep_id, {}).get("file_path", "?")
                pr_val = _global_pr.get(dep_id, 0.0)
                if pr_val > _file_pr.get(fp, 0.0):
                    _file_pr[fp] = pr_val
            ranked_files = sorted(affected_files, key=lambda fp: -_file_pr.get(fp, 0.0))
            # W335/W342 — show displayed/total when the BFS cap dropped files.
            _files_hdr = f"{len(affected_files)} of {total_files} (capped)" if cap_applied else str(len(affected_files))
            click.echo(f"\nAffected files ({_files_hdr} — ranked by impact):")
            for fp in ranked_files[:20]:
                click.echo(f"  {fp}")
            if len(affected_files) > 20:
                click.echo(f"  (+{len(affected_files) - 20} more)")

        if sf_test_files:
            click.echo(f"\nSalesforce convention tests ({len(sf_test_files)}):")
            for tf in sorted(sf_test_files):
                click.echo(f"  {tf}")

        # — point at the natural next command.
        from roam.commands.next_steps import format_next_steps_text, suggest_next_steps

        _ns = suggest_next_steps(
            "impact",
            {
                "symbol": name or "",
                "affected_symbols": len(dependents),
            },
        )
        _ns_text = format_next_steps_text(_ns)
        if _ns_text:
            click.echo(_ns_text)
