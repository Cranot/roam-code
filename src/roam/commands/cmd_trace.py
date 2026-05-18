"""Show shortest dependency path between two symbols.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because trace outputs are invocation-scoped call-path
enumerations — not per-location violations. Editor consumers should
use the JSON envelope directly. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1148 audit
memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, symbol_not_found_hint
from roam.db.connection import open_db
from roam.db.edge_kinds import CALL_EDGE_KINDS
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)

# Exhaustive Yen's-algorithm pathfinding is O(K*V*(V+E)) and degrades sharply
# above this threshold. Bounded BFS is always available; the exhaustive code
# path is gated behind --exhaustive on graphs this size.
_MAX_EXHAUSTIVE_GRAPH_SYMBOLS = 18000

# W493-family edge-kind union: call/calls (singular+plural writer drift) plus
# the 'uses'/'uses_trait' phantom-extender kinds documented in
# roam.db.edge_kinds. The classifier treats any of these as a runtime-coupling
# edge ("direct call chain"). Pre-W512 this site inlined ("call", "uses",
# "uses_trait") and silently mis-classified plural-'calls' rows as imports.
_STRONG_COUPLING_EDGE_KINDS: frozenset[str] = frozenset(CALL_EDGE_KINDS) | {"uses", "uses_trait"}


def _combine_resolution(src_tier: str, tgt_tier: str) -> str:
    """W1248: combine two-target tiers into a single most-degraded outcome.

    ``trace`` resolves TWO targets (source + target) where every other
    flagship wave (W1242/W1243/W1244) resolved ONE. The most-degraded
    outcome wins so a single top-level ``resolution`` field still works
    for agents that only read the top-level disclosure. Per-target tiers
    are surfaced separately via ``src_resolution`` / ``tgt_resolution``
    extension fields so consumers can distinguish "both fuzzy" from
    "source fuzzy, target exact" when needed.
    """
    if src_tier == "unresolved" or tgt_tier == "unresolved":
        return "unresolved"
    if "fuzzy" in (src_tier, tgt_tier):
        return "fuzzy"
    return "symbol"


def _verdict_fuzzy_suffix(src_tier: str, tgt_tier: str) -> str:
    """W1248: human-readable suffix when one or both targets resolved fuzzily."""
    src_fuzzy = src_tier == "fuzzy"
    tgt_fuzzy = tgt_tier == "fuzzy"
    if src_fuzzy and tgt_fuzzy:
        return " [fuzzy: src+tgt]"
    if src_fuzzy:
        return " [fuzzy: src]"
    if tgt_fuzzy:
        return " [fuzzy: tgt]"
    return ""


def _find_bounded_paths(G, source_id: int, target_id: int, *, max_hops: int, k: int) -> list[list[int]]:
    """Find up to *k* simple paths from source to target with length <= max_hops.

    Uses a bounded DFS (no full-graph traversal) so it scales to 18K+ symbol
    graphs without hard-failing. Returns paths sorted shortest-first.

    The DFS is bounded by ``max_hops`` edges (== ``max_hops + 1`` nodes) and
    cuts off as soon as ``k`` paths have been collected.
    """
    if source_id not in G or target_id not in G:
        return []
    if source_id == target_id:
        return [[source_id]]

    found: list[list[int]] = []
    # Iterative DFS to avoid Python recursion limits on long chains.
    # Each stack entry is (current_node, path_so_far, visited_set).
    stack: list = [(source_id, [source_id], {source_id})]
    max_nodes = int(max_hops) + 1  # path length in nodes

    while stack and len(found) < k:
        node, path, visited = stack.pop()
        if len(path) >= max_nodes:
            continue
        for succ in G.successors(node):
            if succ in visited:
                continue
            new_path = path + [succ]
            if succ == target_id:
                found.append(new_path)
                if len(found) >= k:
                    break
                continue
            if len(new_path) < max_nodes:
                stack.append((succ, new_path, visited | {succ}))

    found.sort(key=len)
    return found[:k]


def _classify_coupling(hops):
    """Classify path coupling strength from edge kinds.

    Returns a label like "strong (direct call chain)" based on the
    ratio of call/uses edges vs import/template edges.
    """
    kinds = [h.get("edge_kind", "") for h in hops[1:] if h.get("edge_kind")]
    total = len(kinds)
    if total == 0:
        # No symbol-level edges. 2-hop paths are file-level import shortcuts
        # (the code adds these when source file imports target file).
        # Longer paths with no edges shouldn't happen, but handle gracefully.
        if len(hops) == 2:
            return "structural (file import)"
        return "unknown"
    call_count = sum(1 for k in kinds if k in _STRONG_COUPLING_EDGE_KINDS)
    ratio = call_count / total
    if ratio == 1.0:
        return "strong (direct call chain)"
    if ratio >= 0.5:
        return "moderate (mixed call + import)"
    return "weak (via imports/template)"


def _detect_hubs(path_ids, G, threshold=50):
    """Detect hub nodes (high-degree) in path intermediates.

    Returns list of (node_id, degree) for intermediate nodes exceeding threshold.
    Skips first and last node (source/target are intentional).
    """
    hubs = []
    for node_id in path_ids[1:-1]:  # Skip source and target
        degree = G.in_degree(node_id) + G.out_degree(node_id)
        if degree > threshold:
            hubs.append((node_id, degree))
    return hubs


def _path_quality(hops, hubs):
    """Score path quality (higher = better). Three factors:

    1. Coupling: call/uses edges = 1.0 (runtime dependency), file imports = 0.5
       (structural dependency), import-only = 0.0 (weak).
    2. Directness: shorter paths are inherently more meaningful. 2-hop = 1.0,
       each extra hop reduces by 0.15.
    3. Hub penalty: high-degree intermediates make connections coincidental.
       Scales with degree — a mega-hub (degree 500+) penalizes more than a
       borderline hub (degree 50).

    Combined: coupling * 0.7 + directness * 0.3 - hub_penalty.
    """
    if not hops:
        return 0.0

    n_hops = len(hops)

    # --- Coupling score ---
    kinds = [h.get("edge_kind", "") for h in hops[1:] if h.get("edge_kind")]
    if kinds:
        total = len(kinds)
        coupling = sum(1 for k in kinds if k in _STRONG_COUPLING_EDGE_KINDS) / total
    else:
        # No edge kind info — file-level import shortcut paths.
        # These represent intentional structural dependencies (someone wrote
        # the import), so they deserve moderate coupling, not zero.
        coupling = 0.5

    # --- Directness bonus ---
    # 2 hops = 1.0, 3 hops = 0.85, 4 hops = 0.7, 5 hops = 0.55, ...
    directness = max(0.0, min(1.0, 1.0 - (n_hops - 2) * 0.15))

    base = coupling * 0.7 + directness * 0.3

    # --- Hub penalty (scales with degree) ---
    hub_penalty = 0.0
    for _, degree in hubs:
        # degree 50 → 0.30, degree 100 → 0.40, degree 250+ → 0.50 (capped)
        hub_penalty += min(0.5, 0.2 + degree / 500)

    return base - hub_penalty


def _build_hops(path_ids, annotated, G):
    """Build hop annotations for a single path."""
    hops = []
    for i, node in enumerate(annotated):
        hop = {
            "name": node["name"],
            "kind": node["kind"],
            "location": loc(node["file_path"], node["line"]),
        }
        if i > 0:
            prev_id = path_ids[i - 1]
            curr_id = path_ids[i]
            edge_kind = G.edges.get((prev_id, curr_id), {}).get("kind", "")
            if not edge_kind:
                edge_kind = G.edges.get((curr_id, prev_id), {}).get("kind", "")
            hop["edge_kind"] = edge_kind
        hops.append(hop)
    return hops


@roam_capability(
    name="trace",
    category="exploration",
    summary="Show shortest path between two symbols",
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
@click.command()
@click.argument("source")
@click.argument("target")
@click.option("-k", "k_paths", default=3, help="Number of alternative paths to find")
@click.option(
    "--max-hops",
    type=int,
    default=6,
    show_default=True,
    help=(
        "cap path search at N edges. Conservative default keeps the search "
        "bounded on production-scale graphs (18K+ symbols). Increase for "
        "longer chains; combine with --exhaustive for Yen's k-shortest."
    ),
)
@click.option(
    "--exhaustive",
    is_flag=True,
    default=False,
    help=(
        "use Yen's k-shortest algorithm over the full graph. Slow on "
        "graphs > 18K symbols; default is a bounded BFS that respects "
        "--max-hops."
    ),
)
@click.pass_context
def trace(ctx, source, target, k_paths, max_hops, exhaustive):
    """Show shortest path between two symbols.

    Unlike ``impact`` (which shows the blast radius of one symbol), this
    command finds the shortest dependency path connecting two specific
    symbols. The default search is a bounded BFS capped at ``--max-hops``
    edges; pass ``--exhaustive`` for Yen's k-shortest over the full graph.

    \b
    Examples:
      roam trace handle_login validate_token
      roam trace UserService.create AuditLog.write -k 3
      roam trace login logout --max-hops 10
      roam --json trace login logout --exhaustive

    See also ``impact`` (blast radius from one symbol), ``uses``
    (direct consumers), and ``why`` (architectural role of a symbol).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph
    from roam.graph.pathfinding import (
        find_k_paths,
        find_symbol_id_with_tier,
        format_path,
    )

    with open_db(readonly=True) as conn:
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if exhaustive and sym_count > _MAX_EXHAUSTIVE_GRAPH_SYMBOLS:
            msg = (
                f"graph too large for exhaustive search ({sym_count} symbols); "
                f"drop --exhaustive to use bounded BFS within --max-hops={max_hops}"
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "trace",
                            summary={
                                "verdict": msg,
                                "symbol_count": sym_count,
                                "state": "graph_too_large_for_exhaustive",
                                "partial_success": True,
                                "hops": 0,
                                "paths": 0,
                            },
                            source=source,
                            target=target,
                            paths=[],
                            path=None,
                            state="graph_too_large_for_exhaustive",
                            partial_success=True,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {msg}")
            return

        # W1248 — ``resolution_block`` carries ``target="src -> tgt"`` for the
        # disclosure, but ``json_envelope`` already takes ``target=<tgt>`` as a
        # top-level kwarg. ``_TOP_LEVEL_RESERVED`` lists the keys we strip
        # before splatting the block as kwargs, and we also drop
        # ``partial_success`` because the caller computes a combined-OR value.
        _TOP_LEVEL_RESERVED = {"target", "partial_success"}

        def _disclosure_for_kwargs(block: dict) -> dict:
            return {k: v for k, v in block.items() if k not in _TOP_LEVEL_RESERVED}

        def _disclosure_for_summary(block: dict) -> dict:
            # Summary already carries source/target as separate fields too,
            # but the disclosure's ``target`` is the "<src -> tgt>" descriptor
            # and is safe to keep there as a self-describing label. We only
            # strip ``partial_success`` (re-computed with combined-OR).
            return {k: v for k, v in block.items() if k != "partial_success"}

        # W1249: ``find_symbol_id_with_tier`` returns ``(ids, tier)`` where
        # tier is one of {"symbol", "fuzzy", "unresolved"} -- the exact same
        # vocabulary the old ``_detect_resolution_tier`` re-query helper
        # computed. We capture it once here and reuse it across the
        # resolved + asymmetric-unresolved + both-resolved branches below.
        src_ids, src_tier = find_symbol_id_with_tier(conn, source)
        if not src_ids:
            # W1248 Pattern-2 variant-D: unresolved source. Surface the same
            # disclosure shape as the resolved path so MCP consumers don't have
            # to special-case the failure envelope.
            unresolved_block = resolution_disclosure("unresolved", target=f"{source} -> {target}")
            unresolved_block["src_resolution"] = "unresolved"
            unresolved_block["tgt_resolution"] = "unknown"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "trace",
                            summary={
                                "verdict": f"symbol not found: '{source}'",
                                "error": "symbol_not_found",
                                "partial_success": True,
                                **_disclosure_for_summary(unresolved_block),
                            },
                            source=source,
                            target=target,
                            hint=symbol_not_found_hint(source),
                            partial_success=True,
                            **_disclosure_for_kwargs(unresolved_block),
                        )
                    )
                )
                raise SystemExit(1)
            click.echo(symbol_not_found_hint(source))
            raise SystemExit(1)

        tgt_ids, tgt_tier = find_symbol_id_with_tier(conn, target)
        if not tgt_ids:
            unresolved_block = resolution_disclosure("unresolved", target=f"{source} -> {target}")
            # Source resolved (else we'd have returned above) so ``src_tier``
            # is already populated from the W1249 tiered helper; surface it
            # so the envelope honestly reports the asymmetric outcome.
            unresolved_block["src_resolution"] = src_tier
            unresolved_block["tgt_resolution"] = "unresolved"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "trace",
                            summary={
                                "verdict": f"symbol not found: '{target}'",
                                "error": "symbol_not_found",
                                "partial_success": True,
                                **_disclosure_for_summary(unresolved_block),
                            },
                            source=source,
                            target=target,
                            hint=symbol_not_found_hint(target),
                            partial_success=True,
                            **_disclosure_for_kwargs(unresolved_block),
                        )
                    )
                )
                raise SystemExit(1)
            click.echo(symbol_not_found_hint(target))
            raise SystemExit(1)

        # W1248 / W1249 Pattern-2 variant-D: both targets resolved. The
        # W1249 tiered helper already stamped ``src_tier`` / ``tgt_tier``
        # at the lookup sites above, so the envelope can disclose whether
        # the resolver landed on the exact-match rung or the LIKE-fallback
        # rung for either side without re-querying. The most-degraded
        # outcome wins for the top-level disclosure; per-target tiers are
        # surfaced via src_resolution / tgt_resolution extension fields.
        # ``find_symbol_id`` returns up to 50 fuzzy matches and cmd_trace
        # iterates all combinations — the disclosure makes that blow-up
        # visible without gating it (W1248 mandate: annotate, don't gate).
        combined_tier = _combine_resolution(src_tier, tgt_tier)
        resolution_block = resolution_disclosure(combined_tier, target=f"{source} -> {target}")
        resolution_block["src_resolution"] = src_tier
        resolution_block["tgt_resolution"] = tgt_tier
        # W1248 drive-by: surface the fuzzy LIMIT-50 ceiling when the resolver
        # hit it, so agents can choose to refine the query rather than wade
        # through up to 2500 (50 x 50) path-combinations downstream.
        _LIKE_LIMIT = 50
        if src_tier == "fuzzy" and len(src_ids) >= _LIKE_LIMIT:
            resolution_block["src_max_results_hit"] = True
        if tgt_tier == "fuzzy" and len(tgt_ids) >= _LIKE_LIMIT:
            resolution_block["tgt_max_results_hit"] = True
        verdict_suffix = _verdict_fuzzy_suffix(src_tier, tgt_tier)

        # W607-EQ -- substrate-boundary plumbing for cmd_trace.
        # ``_run_check_eq`` wraps each substrate helper so an uncaught
        # raise in any one boundary degrades to a sensible empty-floor
        # default AND surfaces a marker in ``_w607eq_warnings_out``
        # rather than crashing the trace command outright. cmd_trace is
        # the k-shortest-paths pathfinding command (Yen's exhaustive +
        # bounded BFS), sibling to cmd_closure (W607-EM, transitive
        # closure), cmd_cut (W607-EI, minimum edge cuts), and
        # cmd_simulate (W607-EF, counterfactual transforms) in the
        # structural-analysis / pathfinding family. A raise inside
        # ``build_symbol_graph``, the path-enumeration loops
        # (``find_k_paths`` / ``_find_bounded_paths``), the per-path
        # annotation (``_build_hops`` / ``_detect_hubs`` /
        # ``_classify_coupling`` / ``_path_quality``), or any downstream
        # verdict / envelope composer used to crash the trace command
        # outright. Marker family
        # ``trace_<phase>_failed:<exc_class>:<detail>``. Substrates
        # wrapped:
        #
        #   * resolve_source_target_symbols -- captures the already-
        #                                resolved (src_ids, tgt_ids,
        #                                tier-block) for downstream
        #                                disclosure consistency.
        #   * build_dependency_graph -- networkx graph from DB.
        #   * compute_k_shortest_paths -- the k-paths enumeration over
        #                                all (sid, tid) combinations
        #                                including the file-edge shortcut.
        #   * extract_path_metrics   -- per-path hops + hubs + coupling
        #                                + quality annotations.
        #   * compose_verdict        -- LAW 6 single-line floor.
        #   * compose_facts          -- agent_contract.facts list.
        #   * compose_next_commands  -- agent_contract.next_commands.
        #   * serialize_envelope     -- JSON envelope emission.
        #   * format_text_output     -- text path-table emission.
        #
        # W978 7-discipline applied: (1) f-string verdict floor uses
        # literal zero-count text -- no Name references, (2) default=...
        # carries plain literals, (3) no json.dumps(default=str) needed
        # (no datetimes), (4) ``trace_*`` prefix is unique (collision-
        # checked by cross-prefix-discipline test), (5) len() at
        # kwarg-bind is gated by the envelope fallback, (6) len() /
        # if x: on a poisoned object only runs after the empty-floor
        # guard, (7) no dict.get(key, expensive_default) calls -- all
        # defaults are immutable literals.
        _w607eq_warnings_out: list[str] = []

        def _run_check_eq(phase, fn, *args, default=None, **kwargs):
            """Run one substrate helper with W607-EQ marker emission.

            On a clean call the result is returned as-is. On an uncaught
            exception, surface a
            ``trace_<phase>_failed:<exc_class>:<detail>`` marker via
            ``_w607eq_warnings_out`` and return *default* -- the
            envelope still emits cleanly with the remaining substrates.
            """
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- top-level disclosure
                _w607eq_warnings_out.append(f"trace_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

        # W607-EQ: ``resolve_source_target_symbols`` substrate -- the
        # source + target id lists are already resolved (with tier
        # disclosure) by the find_symbol_id_with_tier calls above. The
        # substrate captures the resolved bundle so a raise here would
        # degrade to empty id lists; in practice this is a near-no-op
        # but keeps the substrate count uniform with sibling commands.
        def _resolve_source_target_symbols():
            return (list(src_ids), list(tgt_ids), src_tier, tgt_tier)

        resolved_bundle = _run_check_eq(
            "resolve_source_target_symbols",
            _resolve_source_target_symbols,
            default=([], [], "symbol", "symbol"),
        )
        if resolved_bundle is None:
            resolved_bundle = ([], [], "symbol", "symbol")
        _safe_src_ids, _safe_tgt_ids, _safe_src_tier, _safe_tgt_tier = resolved_bundle
        if not isinstance(_safe_src_ids, list):
            _safe_src_ids = []
        if not isinstance(_safe_tgt_ids, list):
            _safe_tgt_ids = []

        # W607-EQ: ``build_dependency_graph`` substrate -- DB -> networkx
        # graph. A raise here degrades to None which the downstream
        # path-enumeration substrate handles by returning the empty
        # path list.
        G = _run_check_eq(
            "build_dependency_graph",
            build_symbol_graph,
            conn,
            default=None,
        )

        # W607-EQ: ``compute_k_shortest_paths`` substrate -- the path
        # enumeration loop. A raise inside find_k_paths / the bounded
        # DFS / the file-edge shortcut query degrades to an empty
        # unique-paths list so the downstream "no path" path kicks in.
        def _compute_k_shortest_paths():
            if G is None:
                return []
            _file_ids_local: dict = {}
            for _sid in _safe_src_ids + _safe_tgt_ids:
                row_local = conn.execute("SELECT file_id FROM symbols WHERE id = ?", (_sid,)).fetchone()
                if row_local:
                    _file_ids_local[_sid] = row_local["file_id"]

            all_paths_local: list = []
            for sid in _safe_src_ids:
                for tid in _safe_tgt_ids:
                    if sid == tid:
                        continue
                    src_fid_local = _file_ids_local.get(sid)
                    tgt_fid_local = _file_ids_local.get(tid)
                    if src_fid_local and tgt_fid_local and src_fid_local != tgt_fid_local:
                        fe_local = conn.execute(
                            "SELECT 1 FROM file_edges WHERE source_file_id = ? AND target_file_id = ?",
                            (src_fid_local, tgt_fid_local),
                        ).fetchone()
                        if fe_local:
                            all_paths_local.append([sid, tid])

                    if exhaustive:
                        paths_local = find_k_paths(G, sid, tid, k=k_paths)
                    else:
                        paths_local = _find_bounded_paths(G, sid, tid, max_hops=max_hops, k=k_paths)
                    for p_local in paths_local:
                        all_paths_local.append(p_local)

            seen_local: set = set()
            unique_paths_local: list = []
            for p_local in all_paths_local:
                key_local = tuple(p_local)
                if key_local not in seen_local:
                    seen_local.add(key_local)
                    unique_paths_local.append(p_local)
            unique_paths_local.sort(key=len)
            return unique_paths_local[:k_paths]

        unique_paths = _run_check_eq(
            "compute_k_shortest_paths",
            _compute_k_shortest_paths,
            default=[],
        )
        if not isinstance(unique_paths, list):
            unique_paths = []

        if not unique_paths:
            # Distinguish "bounded search exhausted hop budget" from "definitive
            # no path under exhaustive search". The bounded case is a partial
            # result (a longer path may exist); the exhaustive case is final.
            if exhaustive:
                no_path_state = "no_path"
                no_path_verdict = f"no path found between {source} and {target} (exhaustive)"
                partial = False
            else:
                no_path_state = "no_path_within_hops"
                no_path_verdict = (
                    f"no path between {source} and {target} within --max-hops={max_hops}; "
                    f"increase --max-hops or pass --exhaustive"
                )
                partial = True
            # W1248 — combined-OR partial_success: fuzzy resolution on either
            # target degrades the verdict regardless of whether a path was
            # found, so OR it with the no-path partial flag. The verdict
            # suffix mirrors the same signal for text-only consumers.
            no_path_verdict = f"{no_path_verdict}{verdict_suffix}"
            partial = partial or combined_tier != "symbol"
            # W607-EQ: a substrate failure earlier (e.g. graph build or
            # path enumeration) also collapses to the "no_path" branch;
            # mirror the disclosure into both envelope locations so MCP
            # consumers see the marker regardless of which surface they
            # read, and flip partial_success on the degraded path.
            if _w607eq_warnings_out:
                partial = True
            if json_mode:
                no_path_summary: dict = {
                    "verdict": no_path_verdict,
                    "hops": 0,
                    "paths": 0,
                    "state": no_path_state,
                    "partial_success": partial,
                    "max_hops": max_hops,
                    "exhaustive": exhaustive,
                    # W1248 — Pattern-2 variant-D disclosure. The
                    # helper's own partial_success is suppressed
                    # here because we OR-combine it with the
                    # no-path partial flag above.
                    **_disclosure_for_summary(resolution_block),
                }
                no_path_kwargs: dict = dict(
                    summary=no_path_summary,
                    source=source,
                    target=target,
                    path=None,
                    paths=[],
                    coupling_summary="none — no dependency path exists",
                    state=no_path_state,
                    partial_success=partial,
                    **_disclosure_for_kwargs(resolution_block),
                )
                if _w607eq_warnings_out:
                    no_path_summary["warnings_out"] = list(_w607eq_warnings_out)
                    no_path_kwargs["warnings_out"] = list(_w607eq_warnings_out)
                click.echo(to_json(json_envelope("trace", **no_path_kwargs)))
            else:
                click.echo(f"VERDICT: {no_path_verdict}\n")
                click.echo(f"No dependency path between '{source}' and '{target}'.")
                if exhaustive:
                    click.echo("These symbols are independent — changes to one cannot affect the other.")
                else:
                    click.echo(
                        f"Bounded search exhausted at --max-hops={max_hops}. "
                        "Re-run with a larger --max-hops or pass --exhaustive."
                    )
            return

        # W607-EQ: ``extract_path_metrics`` substrate -- per-path hops +
        # hubs + coupling + quality annotation. A raise inside
        # ``_build_hops`` / ``_detect_hubs`` / ``_classify_coupling`` /
        # ``_path_quality`` / ``format_path`` degrades to an empty
        # annotated_paths list which the downstream "no annotated
        # paths" guard treats as a degraded no-path verdict.
        def _extract_path_metrics():
            annotated_paths_local: list = []
            for path_ids_local in unique_paths:
                annotated_local = format_path(path_ids_local, conn)
                hops_local = _build_hops(path_ids_local, annotated_local, G)
                hubs_local = _detect_hubs(path_ids_local, G)
                coupling_local = _classify_coupling(hops_local)
                quality_local = _path_quality(hops_local, hubs_local)

                # Mark hub nodes in hops
                hub_ids_local = {h[0] for h in hubs_local}
                hub_degrees_local = {h[0]: h[1] for h in hubs_local}
                for i_local, node_id_local in enumerate(path_ids_local):
                    if node_id_local in hub_ids_local:
                        hops_local[i_local]["is_hub"] = True
                        hops_local[i_local]["hub_degree"] = hub_degrees_local[node_id_local]

                annotated_paths_local.append(
                    {
                        "path_ids": path_ids_local,
                        "hops": hops_local,
                        "coupling": coupling_local,
                        "quality": round(quality_local, 2),
                        "hub_count": len(hubs_local),
                    }
                )

            # Sort by quality (desc), then length (asc)
            annotated_paths_local.sort(key=lambda ap: (-ap["quality"], len(ap["hops"])))
            return annotated_paths_local[:k_paths]

        annotated_paths = _run_check_eq(
            "extract_path_metrics",
            _extract_path_metrics,
            default=[],
        )
        if not isinstance(annotated_paths, list):
            annotated_paths = []

        # Coupling summary: strongest coupling across ALL paths (not just best-quality).
        # A hub-mediated call chain may rank lower on quality but still reveals
        # that runtime coupling exists — the summary should reflect that.
        _COUPLING_RANK = {
            "strong (direct call chain)": 4,
            "moderate (mixed call + import)": 3,
            "weak (via imports/template)": 2,
            "structural (file import)": 1,
        }
        coupling_summary = max(
            (ap["coupling"] for ap in annotated_paths),
            key=lambda c: _COUPLING_RANK.get(c, 0),
            default="none",
        )

        # W607-EQ: when ``extract_path_metrics`` degrades to an empty
        # list, we still owe the caller a coherent envelope. The first
        # path is referenced below as ``first`` / ``first_txt``; build a
        # safe-floor placeholder so the verdict composer + envelope can
        # bind without IndexError.
        if annotated_paths:
            first_hops = annotated_paths[0].get("hops", []) or []
        else:
            first_hops = []

        # W607-EQ: ``compose_verdict`` substrate -- LAW 6 single-line
        # trace floor. A raise degrades to the literal zero-count floor
        # string -- the W811/W817 Pattern-2 guard: never collapse to a
        # SAFE/passed verdict on the degraded path.
        def _compose_verdict():
            return (
                f"trace: {len(first_hops)} hops {source}->{target}, "
                f"{len(annotated_paths)} path"
                f"{'s' if len(annotated_paths) != 1 else ''} found, "
                f"{coupling_summary}{verdict_suffix}"
            )

        trace_verdict = _run_check_eq(
            "compose_verdict",
            _compose_verdict,
            default=f"trace: 0 hops {source}->{target}, 0 paths found, none",
        )
        if not isinstance(trace_verdict, str) or not trace_verdict:
            trace_verdict = f"trace: 0 hops {source}->{target}, 0 paths found, none"

        # W607-EQ: ``compose_facts`` substrate -- agent_contract.facts
        # list. A raise degrades to a single verdict-only fact so LAW 6
        # verdict-first invariant holds.
        def _compose_facts():
            return [
                trace_verdict,
                f"{len(annotated_paths)} paths",
                f"{len(first_hops)} hops",
                f"coupling: {coupling_summary}",
            ]

        facts = _run_check_eq("compose_facts", _compose_facts, default=[trace_verdict])
        if facts is None:
            facts = [trace_verdict]

        # W607-EQ: ``compose_next_commands`` substrate -- conditional
        # advisory next-step suggestions. A raise degrades to an empty
        # list so the agent_contract still composes.
        def _compose_next_commands():
            cmds: list[str] = []
            if annotated_paths:
                cmds.append(f"roam impact {source}")
                cmds.append(f"roam impact {target}")
            return cmds

        next_commands = _run_check_eq(
            "compose_next_commands",
            _compose_next_commands,
            default=[],
        )
        if next_commands is None:
            next_commands = []

        is_partial = combined_tier != "symbol"
        # W607-EQ: any substrate marker forces partial_success on the
        # final envelope -- Pattern-2 silent-fallback guard.
        if _w607eq_warnings_out:
            is_partial = True

        if json_mode:
            # Backward-compatible: "path" = first path's hops, "hops" = first path hop count
            # W607-EQ: ``serialize_envelope`` substrate -- json_envelope
            # construction + click.echo emission. The wrap protects
            # against crashes inside the formatter call so the marker
            # surfaces and the function returns cleanly.
            envelope_summary: dict = {
                "verdict": trace_verdict,
                "hops": len(first_hops),
                "paths": len(annotated_paths),
                "coupling": coupling_summary,
                "state": "ok" if annotated_paths else "no_path",
                "max_hops": max_hops,
                "exhaustive": exhaustive,
                "partial_success": is_partial,
                # W1248 — merge disclosure (omit duplicate
                # partial_success; computed above).
                **_disclosure_for_summary(resolution_block),
            }
            envelope_kwargs: dict = dict(
                summary=envelope_summary,
                source=source,
                target=target,
                hops=len(first_hops),
                path=first_hops if annotated_paths else None,
                coupling_summary=coupling_summary,
                state="ok" if annotated_paths else "no_path",
                partial_success=is_partial,
                paths=[
                    {
                        "hops": len(ap.get("hops", [])),
                        "coupling": ap.get("coupling", ""),
                        "quality": ap.get("quality", 0.0),
                        "hub_count": ap.get("hub_count", 0),
                        "path": ap.get("hops", []),
                    }
                    for ap in annotated_paths
                ],
                agent_contract={
                    "facts": facts,
                    "risks": [],
                    "next_commands": next_commands,
                    "confidence": None,
                },
                **_disclosure_for_kwargs(resolution_block),
            )
            # W607-EQ: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read.
            if _w607eq_warnings_out:
                envelope_summary["warnings_out"] = list(_w607eq_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607eq_warnings_out)

            def _serialize_envelope():
                click.echo(to_json(json_envelope("trace", **envelope_kwargs)))

            _run_check_eq("serialize_envelope", _serialize_envelope, default=None)
            return

        # --- Text output ---
        # W607-EQ: ``format_text_output`` substrate -- the human-readable
        # text emission path. A raise inside the path loop (e.g. a
        # malformed hop dict) degrades to a verdict-only emission so the
        # user still sees the LAW 6 floor.
        def _format_text_output():
            click.echo(f"VERDICT: {trace_verdict}\n")

            if not annotated_paths:
                click.echo("(no paths to display)")
                click.echo(f"\nCoupling: {coupling_summary}")
                return

            if len(annotated_paths) == 1:
                ap = annotated_paths[0]
                hub_note = f", {ap['hub_count']} hub{'s' if ap['hub_count'] != 1 else ''}" if ap["hub_count"] else ""
                click.echo(f"Path ({len(ap['hops'])} hops, quality={ap['quality']}, {ap['coupling']}{hub_note}):")
                _print_path(ap["hops"])
            else:
                for idx, ap in enumerate(annotated_paths, 1):
                    hub_note = (
                        f", {ap['hub_count']} hub{'s' if ap['hub_count'] != 1 else ''}" if ap["hub_count"] else ""
                    )
                    click.echo(
                        f"\n=== Path {idx} of {len(annotated_paths)} "
                        f"({len(ap['hops'])} hops, quality={ap['quality']}, "
                        f"{ap['coupling']}{hub_note}) ==="
                    )
                    _print_path(ap["hops"])

            click.echo(f"\nCoupling: {coupling_summary}")

        _run_check_eq("format_text_output", _format_text_output, default=None)


def _print_path(hops):
    """Print a single path in the text format."""
    for i, hop in enumerate(hops):
        if i == 0:
            click.echo(f"    {abbrev_kind(hop['kind'])}  {hop['name']}  {hop['location']}")
        else:
            edge = hop.get("edge_kind", "")
            edge_label = f"--{edge}-->" if edge else "->"
            click.echo(f"  {edge_label}  {abbrev_kind(hop['kind'])}  {hop['name']}  {hop['location']}")
        if hop.get("is_hub"):
            click.echo(f"           ^ hub (degree {hop['hub_degree']})")
