"""Show how a set of symbols relate: shared deps, call chains, conflicts.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because relate outputs are invocation-scoped relation-graph
rankings (shared dependencies, call chains, conflicts) — not
per-location violations. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.

W607-W -- Twenty-third-in-batch W607 consumer-layer arc. Direct sibling
of W607-V (cmd_deps file-substrate variant). cmd_relate is the
multi-target / two-target axis variant -- same per-substrate marker
plumbing, but each input symbol goes through its own resolver pass so
two-target failures surface as two distinct ``resolve_symbol`` markers
(source_resolve and target_resolve, conceptually) on the same envelope.
Each substrate-call site (per-input resolve, file resolve, graph build,
direct edges, shared deps/callers, distance matrix, conflict detection,
cohesion, connecting-path lookup) is wrapped with
``_run_check(phase, fn, *args)`` so a raise becomes a
``relate_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607w_warnings_out`` and the envelope still emits cleanly.

Marker family ``relate_*`` -- distinct from W607-V's ``deps_*``, W607-U's
``uses_*``, W607-T's ``impact_*``, etc. The marker-prefix discipline
test pins this closed-enum distinction.

W907 verify-cycle check: no defensive "duplicated to avoid cycle"
docstrings present or added in this module. The ``import networkx as nx``
lazy imports in ``_compute_distance_matrix`` and ``_find_connecting_path``
are genuine deferred-load imports (networkx is ~500ms cold-start), NOT
cargo-cult cycle hedges -- left untouched.

Pattern 1 Variant D preservation: cmd_relate already emits
``resolution_disclosure`` (the W1245 per-input + combined-tier
disclosure). The W607-W wave does NOT alter that surface -- the
``resolutions`` array, the combined ``resolution`` block, and the
``fuzzy_suffix`` verdict-tail logic all stay byte-identical. W607-W
adds an orthogonal substrate-CALL marker channel.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found_hint
from roam.db.connection import open_db
from roam.db.edge_kinds import CALL_EDGE_KINDS
from roam.output.formatter import json_envelope, resolution_disclosure, to_json

# Conflict-risk edge kinds: callers (W512 canonical union) + the
# 'uses' / 'uses_trait' phantom-extender kinds documented in
# roam.db.edge_kinds (Selection guide §"Sites that mix in additional
# edge kinds"). Anchored on a named constant so a future writer adding
# 'uses_imports' or similar can be added in one place.
_CONFLICT_MODIFIER_EDGE_KINDS: frozenset[str] = frozenset(CALL_EDGE_KINDS) | {"uses", "uses_trait"}


def _resolve_symbols_from_files(conn, file_paths):
    """Resolve all symbol IDs from file paths or directory prefixes."""
    symbol_ids = []
    for fp in file_paths:
        # Normalize path separators
        fp_norm = fp.replace("\\", "/")
        rows = conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.path LIKE ?",
            (f"%{fp_norm}%",),
        ).fetchall()
        for r in rows:
            symbol_ids.append(r[0])
    return symbol_ids


def _get_symbol_info(conn, node_id):
    """Get name, kind, file_path for a symbol node ID."""
    row = conn.execute(
        "SELECT s.name, s.kind, f.path AS file_path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
        (node_id,),
    ).fetchone()
    if row:
        return {"name": row["name"], "kind": row["kind"], "file_path": row["file_path"]}
    return None


def _find_shared_dependencies(G, input_ids):
    """Find symbols that multiple input symbols depend on (common targets)."""
    # For each input symbol, find its successors (what it calls/depends on)
    dep_map = {}  # target_id -> set of input_ids that depend on it
    for nid in input_ids:
        if nid not in G:
            continue
        for succ in G.successors(nid):
            if succ in input_ids:
                continue  # Skip input symbols themselves
            dep_map.setdefault(succ, set()).add(nid)

    # Keep only those depended on by 2+ input symbols
    shared = {tid: callers for tid, callers in dep_map.items() if len(callers) >= 2}
    return shared


def _find_shared_callers(G, input_ids):
    """Find symbols that call multiple input symbols (common predecessors)."""
    caller_map = {}  # caller_id -> set of input_ids it calls
    for nid in input_ids:
        if nid not in G:
            continue
        for pred in G.predecessors(nid):
            if pred in input_ids:
                continue
            caller_map.setdefault(pred, set()).add(nid)

    shared = {cid: callees for cid, callees in caller_map.items() if len(callees) >= 2}
    return shared


def _compute_distance_matrix(G, input_ids, depth):
    """Compute shortest-path distances between all pairs of input symbols."""
    import networkx as nx

    undirected = G.to_undirected()
    matrix = {}
    for i, src in enumerate(input_ids):
        for j, tgt in enumerate(input_ids):
            if i >= j:
                continue
            if src not in G or tgt not in G:
                matrix[(src, tgt)] = None
                continue
            try:
                dist = nx.shortest_path_length(undirected, src, tgt)
                if dist > depth:
                    matrix[(src, tgt)] = None
                else:
                    matrix[(src, tgt)] = dist
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                matrix[(src, tgt)] = None
    return matrix


def _find_direct_edges(G, input_ids):
    """Find direct edges between input symbols."""
    edges = []
    input_set = set(input_ids)
    for src in input_ids:
        if src not in G:
            continue
        for tgt in G.successors(src):
            if tgt in input_set and tgt != src:
                kind = G.edges[src, tgt].get("kind", "unknown")
                edges.append((src, tgt, kind))
    return edges


def _find_connecting_path(G, src, tgt, depth):
    """Find shortest path between two nodes, respecting depth limit."""
    import networkx as nx

    if src not in G or tgt not in G:
        return None
    # Try directed first
    try:
        path = nx.shortest_path(G, src, tgt)
        if len(path) - 1 <= depth:
            return path
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass
    # Try undirected
    try:
        undirected = G.to_undirected()
        path = nx.shortest_path(undirected, src, tgt)
        if len(path) - 1 <= depth:
            return path
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass
    return None


def _detect_conflicts(G, input_ids, shared_deps, conn):
    """Detect conflict risks: input symbols that both have outgoing edges to the same dependency."""
    conflicts = []
    for dep_id, callers in shared_deps.items():
        # Check if multiple inputs have edges that indicate modification
        # (calls, uses — things that could conflict)
        modifiers = set()
        for caller_id in callers:
            if G.has_edge(caller_id, dep_id):
                edge_kind = G.edges[caller_id, dep_id].get("kind", "")
                if edge_kind in _CONFLICT_MODIFIER_EDGE_KINDS:
                    modifiers.add(caller_id)
        if len(modifiers) >= 2:
            dep_info = _get_symbol_info(conn, dep_id)
            if dep_info:
                conflicts.append(
                    {
                        "symbol": dep_info["name"],
                        "symbol_id": dep_id,
                        "modified_by": list(modifiers),
                        "recommendation": "coordinate changes to avoid race conditions",
                    }
                )
    return conflicts


def _compute_cohesion(distance_matrix, input_count):
    """Compute cohesion score: average inverse distance, normalized 0-1."""
    if input_count < 2:
        return 1.0
    distances = [d for d in distance_matrix.values() if d is not None]
    if not distances:
        return 0.0
    # Inverse distance: 1/d, averaged
    inv_sum = sum(1.0 / d for d in distances if d > 0)
    # Number of pairs
    n_pairs = input_count * (input_count - 1) // 2
    if n_pairs == 0:
        return 0.0
    cohesion = inv_sum / n_pairs
    # Clamp to 0-1
    return min(1.0, max(0.0, cohesion))


@roam_capability(
    name="relate",
    category="exploration",
    summary="Show how a set of symbols relate to each other",
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
@click.command()
@click.argument("symbols", nargs=-1)
@click.option("--path", "files", multiple=True, help="Include symbols from file/dir path (repeatable)")
@click.option(
    "--file",
    "files",
    multiple=True,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.option("--depth", default=3, help="Max hops for connecting paths (default 3)")
@click.pass_context
def relate(ctx, symbols, files, depth):
    """Show how a set of symbols relate to each other.

    Unlike ``coupling`` (which measures file-level temporal co-change),
    this command shows structural relationships between specific symbols
    including shared dependencies and distance.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph

    # W607-W -- per-substrate marker accumulator. Each substrate call is
    # wrapped with ``_run_check(phase, fn, *args)`` so a raise becomes a
    # ``relate_<phase>_failed:<exc_class>:<detail>`` marker via this list
    # and the envelope still emits the remaining sections cleanly.
    #
    # Marker family ``relate_*`` -- distinct from W607-V's ``deps_*``,
    # W607-U's ``uses_*``, W607-T's ``impact_*``, etc. The marker-prefix
    # discipline test pins this closed-enum distinction.
    #
    # Two-target axis (mission brief): cmd_relate accepts N symbols
    # positionally, so per-input resolver passes each go through their
    # own ``_run_check("resolve_symbol", ...)`` boundary. Two failing
    # inputs produce two distinct markers on the same envelope, mirroring
    # the source_resolve / target_resolve conceptual split.
    #
    # Empty bucket -> byte-identical envelope (no warnings_out key in
    # either summary or top-level, no W607-W-driven partial_success flip).
    _w607w_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-W marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``relate_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607w_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607w_warnings_out.append(f"relate_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-DA: aggregation-phase marker plumbing (additive) -----------
    # cmd_relate is the symbol-relations command -- finds connecting paths
    # between symbols, shared deps / callers, conflict risks, distance
    # matrix, and cohesion. W607-W (above) plumbed the substrate-CALL layer
    # (11 boundaries: build_graph / resolve_symbol / resolve_files /
    # get_symbol_info / find_direct_edges / find_shared_deps /
    # find_shared_callers / compute_distance_matrix / detect_conflicts /
    # compute_cohesion / find_connecting_path). W607-DA adds the
    # AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket the relation shape into a state label
    #                           (DIRECT_DOMINANT / INDIRECT_ONLY / NO_PATH /
    #                           EMPTY)
    #   compute_predicate    -- path-finding metrics (path_count +
    #                           shortest_length + max_length)
    #   compute_verdict      -- composite verdict-string assembly
    #   serialize_envelope   -- json_envelope("relate", ...) projection
    #
    # Marker family ``relate_*`` -- SAME family as W607-W (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope on the
    # success path. Both buckets are combined at envelope-emit time so
    # consumers see the full degradation lineage in marker-emission order.
    # The additive bucket stays distinguishable via its phase names
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``) which do NOT collide with W607-W substrate
    # phase names.
    #
    # SYMBOL-RELATIONS TRIO pairing analogue -- pattern reused here for the
    # graph-traversal command:
    #   cmd_uses    (W607-U substrate only -- agg untouched)
    #   cmd_deps    (W607-V substrate only -- agg untouched)
    #   cmd_relate  (W607-W substrate + W607-DA THIS -- closes the trio at
    #                substrate-CALL layer; relate gets agg)
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_da(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(distance_matrix)``). A computed
    # default expression evaluates BEFORE the wrap call, so a raise
    # inside the expression escapes the try-block. cmd_sbom's W607-CG
    # sealed this axis. cmd_taint's W607-CJ added the 5th discipline
    # (move ``len()`` INSIDE the closure, not at the kwarg-bind site).
    # cmd_audit_trail_export's W607-CR added the 7th discipline (use bare
    # ``dict[key]`` lookup when the floor dict guarantees the key, NOT
    # ``dict.get(key, expensive_default)`` which evaluates default
    # eagerly).
    #
    # W607-W/DA PHASE-NAME COLLISION CHECK (W978 4th-discipline): W607-W
    # phase names (build_graph / resolve_symbol / resolve_files /
    # get_symbol_info / find_direct_edges / find_shared_deps /
    # find_shared_callers / compute_distance_matrix / detect_conflicts /
    # compute_cohesion / find_connecting_path) do NOT collide with
    # score_classify / compute_predicate / compute_verdict /
    # serialize_envelope, so no rename is required.
    _w607da_warnings_out: list[str] = []

    def _run_check_da(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DA marker emission.

        Mirror of ``_run_check`` shape (same
        ``relate_<phase>_failed:`` marker family) but writes into
        ``_w607da_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607da_warnings_out.append(f"relate_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        G = _run_check("build_graph", build_symbol_graph, conn, default=None)
        if G is None:
            # Fallback to an empty DiGraph-like shim so downstream substrates
            # don't crash on .successors / .predecessors / membership. Each
            # substrate is itself wrapped, but a None graph would surface a
            # second marker on every helper -- noisier than necessary.
            import networkx as nx

            G = nx.DiGraph()

        # Resolve input symbols to node IDs
        input_ids = []
        input_names = {}  # id -> name
        unresolved: list[str] = []
        # W1245 Pattern-2 variant-D: track per-input resolver tier so the
        # JSON envelope can disclose fuzzy-LIKE-fallback matches per input
        # AND OR-combine into a single top-level partial_success flag.
        # Each record: {"input": <raw>, "resolved": <qualified>, "tier": <enum>}.
        per_input_resolutions: list[dict[str, str]] = []

        for name in symbols:
            # W607-W: each per-input resolver pass is its own substrate
            # boundary. Two failing inputs produce two distinct markers
            # (two-target axis). The marker detail includes the exception
            # text but not the raw input name -- agents inspect the
            # ``resolutions`` array (P1VD disclosure) to map markers back
            # to inputs.
            sym = _run_check("resolve_symbol", find_symbol, conn, name, default=None)
            if sym:
                sid = sym["id"]
                tier = sym.get("_resolution_tier", "symbol")
                resolved_name = sym.get("qualified_name") or sym["name"]
                per_input_resolutions.append({"input": name, "resolved": resolved_name, "tier": tier})
                if sid not in input_names:
                    input_ids.append(sid)
                    input_names[sid] = sym["name"]
            else:
                per_input_resolutions.append({"input": name, "resolved": name, "tier": "unresolved"})
                if json_mode:
                    # Don't pollute the JSON envelope with a plaintext
                    # hint. Track unresolved names and surface them in
                    # the envelope's summary.
                    unresolved.append(name)
                else:
                    click.echo(symbol_not_found_hint(name))
                    raise SystemExit(1)

        # Resolve symbols from --path paths
        if files:
            file_ids = _run_check("resolve_files", _resolve_symbols_from_files, conn, files, default=[]) or []
            for sid in file_ids:
                if sid not in input_names:
                    info = _run_check("get_symbol_info", _get_symbol_info, conn, sid, default=None)
                    if info:
                        input_ids.append(sid)
                        input_names[sid] = info["name"]

        if not input_ids:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "relate",
                            summary={
                                "verdict": "no symbols to analyze — provide a symbol name or --path",
                                "state": "usage_error",
                                "partial_success": True,
                            },
                            hint="Provide at least one valid symbol name or --path path. "
                            "Use `roam search <partial-name>` to find symbol names.",
                        )
                    )
                )
            else:
                click.echo(
                    "No symbols to analyze.\n"
                    "  Tip: Provide at least one valid symbol name or --path path.\n"
                    "       Use `roam search <partial-name>` to find symbol names."
                )
            raise SystemExit(1)

        # Analysis -- each substrate goes through ``_run_check`` so a
        # raise inside one phase surfaces a marker but the envelope still
        # emits the remaining phases.
        direct_edges = _run_check("find_direct_edges", _find_direct_edges, G, input_ids, default=[]) or []
        shared_deps = _run_check("find_shared_deps", _find_shared_dependencies, G, input_ids, default={}) or {}
        shared_callers = _run_check("find_shared_callers", _find_shared_callers, G, input_ids, default={}) or {}
        distance_matrix = (
            _run_check("compute_distance_matrix", _compute_distance_matrix, G, input_ids, depth, default={}) or {}
        )
        conflicts = _run_check("detect_conflicts", _detect_conflicts, G, input_ids, shared_deps, conn, default=[]) or []
        cohesion = _run_check("compute_cohesion", _compute_cohesion, distance_matrix, len(input_ids), default=0.0)
        if cohesion is None:
            cohesion = 0.0

        # Build relationship list for each pair
        relationships = []
        for i, src in enumerate(input_ids):
            for j, tgt in enumerate(input_ids):
                if i >= j:
                    continue
                src_name = input_names[src]
                tgt_name = input_names[tgt]
                dist = distance_matrix.get((src, tgt))
                if dist is None:
                    dist = distance_matrix.get((tgt, src))

                # Check for direct edge
                has_direct = False
                edge_kind = None
                for s, t, k in direct_edges:
                    if (s == src and t == tgt) or (s == tgt and t == src):
                        has_direct = True
                        edge_kind = k
                        break

                # Find intermediate node if distance is 2
                via = None
                if dist is not None and dist == 2 and not has_direct:
                    path = _run_check("find_connecting_path", _find_connecting_path, G, src, tgt, depth, default=None)
                    if path and len(path) == 3:
                        mid_info = _run_check("get_symbol_info", _get_symbol_info, conn, path[1], default=None)
                        if mid_info:
                            via = mid_info["name"]

                if has_direct:
                    kind = f"DIRECT {edge_kind.upper()}" if edge_kind else "DIRECT"
                elif dist is not None:
                    kind = "INDIRECT"
                else:
                    kind = "NO PATH"

                relationships.append(
                    {
                        "source": src_name,
                        "target": tgt_name,
                        "kind": kind,
                        "distance": dist,
                        "via": via,
                    }
                )

        # Build shared_deps output
        shared_deps_out = []
        for dep_id, callers in shared_deps.items():
            dep_info = _run_check("get_symbol_info", _get_symbol_info, conn, dep_id, default=None)
            if dep_info:
                shared_deps_out.append(
                    {
                        "name": dep_info["name"],
                        "used_by": [input_names[c] for c in callers if c in input_names],
                    }
                )

        # Build shared_callers output
        shared_callers_out = []
        for caller_id, callees in shared_callers.items():
            caller_info = _run_check("get_symbol_info", _get_symbol_info, conn, caller_id, default=None)
            if caller_info:
                shared_callers_out.append(
                    {
                        "name": caller_info["name"],
                        "calls": [input_names[c] for c in callees if c in input_names],
                    }
                )

        # Build conflicts output
        conflicts_out = []
        for c in conflicts:
            conflicts_out.append(
                {
                    "symbol": c["symbol"],
                    "modified_by": [input_names[m] for m in c["modified_by"] if m in input_names],
                    "recommendation": c["recommendation"],
                }
            )

        # Build distance matrix output
        dist_matrix_out = {}
        for sid in input_ids:
            name = input_names[sid]
            dist_matrix_out[name] = {}
            for sid2 in input_ids:
                name2 = input_names[sid2]
                if sid == sid2:
                    dist_matrix_out[name][name2] = 0
                else:
                    key = (min(sid, sid2), max(sid, sid2))
                    d = distance_matrix.get(key)
                    dist_matrix_out[name][name2] = d

        # W1245 Pattern-2 variant-D: aggregate per-input tiers into a
        # single top-level disclosure. Most-degraded wins so the top-level
        # ``resolution`` field stays useful for LAW-6 single-field consumers
        # (unresolved > fuzzy > symbol). Per-input tiers stay on the
        # ``resolutions`` array so callers can distinguish "all fuzzy" from
        # "one fuzzy among many exact" when needed.
        any_unresolved = any(r["tier"] == "unresolved" for r in per_input_resolutions)
        any_fuzzy = any(r["tier"] == "fuzzy" for r in per_input_resolutions)
        if any_unresolved:
            combined_tier = "unresolved"
        elif any_fuzzy:
            combined_tier = "fuzzy"
        else:
            combined_tier = "symbol"
        resolution_block = resolution_disclosure(combined_tier)
        fuzzy_suffix = " [fuzzy resolution]" if combined_tier == "fuzzy" else ""

        # W607-DA -- compute_verdict boundary. Wraps the verdict-string
        # assembly so a downstream f-string refactor (non-numeric values
        # from a vocabulary refactor, or a __format__-raising sentinel)
        # surfaces a marker rather than crashing the envelope. Floor must
        # NOT re-interpolate the same values that tripped the closure
        # (W978 first-hypothesis). Use the literal "relate completed"
        # floor (LAW 6 still holds: the line works standalone).
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: raw lists passed as args;
        # ``len()`` lives INSIDE the closure (cmd_taint W607-CJ
        # 5th-discipline anchor). ``cohesion`` is a numeric scalar, no
        # __format__ raise risk under normal flow.
        def _build_verdict_str(_input_ids, _cohesion, _direct_edges, _conflicts_out, _fuzzy_suffix):
            return (
                f"{len(_input_ids)} symbols analyzed, "
                f"cohesion {_cohesion:.2f}, "
                f"{len(_direct_edges)} direct edges, "
                f"{len(_conflicts_out)} conflict risks"
                f"{_fuzzy_suffix}"
            )

        verdict = _run_check_da(
            "compute_verdict",
            _build_verdict_str,
            input_ids,
            cohesion,
            direct_edges,
            conflicts_out,
            fuzzy_suffix,
            default="relate completed",
        )

        if json_mode:
            # W607-DA -- score_classify boundary. Wraps the relation-shape
            # bucketing (direct edges vs indirect paths vs no path) into a
            # state label (DIRECT_DOMINANT / INDIRECT_ONLY / NO_PATH /
            # EMPTY) so a downstream refactor of the state-selection logic
            # surfaces a marker rather than crashing. Floor returns a
            # documented "DEGRADED" state so downstream serialize_envelope
            # stays non-null.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: raw lists passed as args;
            # ``len()`` and iteration live INSIDE the closure (cmd_taint
            # W607-CJ 5th-discipline anchor). Floor dict is a literal
            # constant.
            def _score_classify_relations(_relationships, _direct_edges):
                _n_rels = len(_relationships) if _relationships is not None else 0
                _n_direct = len(_direct_edges) if _direct_edges is not None else 0
                if _n_rels == 0:
                    _state = "EMPTY"
                elif _n_direct > 0:
                    _state = "DIRECT_DOMINANT"
                else:
                    _has_path = any((r.get("distance") is not None) for r in _relationships if isinstance(r, dict))
                    _state = "INDIRECT_ONLY" if _has_path else "NO_PATH"
                return {"state": _state, "relationship_count": _n_rels}

            _score_dict = _run_check_da(
                "score_classify",
                _score_classify_relations,
                relationships,
                direct_edges,
                default={"state": "DEGRADED", "relationship_count": 0},
            )

            # W607-DA -- compute_predicate boundary. Wraps the path-finding
            # metrics extraction (path_count + shortest_length +
            # max_length) so a future schema refactor that drops or renames
            # fields on the ``relationships`` rows surfaces a marker rather
            # than crashing the envelope. Floor to documented zero-counts
            # matching the empty-relations shape so downstream summary
            # fields stay non-null.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: raw list passed as arg;
            # iteration + ``min()`` / ``max()`` live INSIDE the closure
            # (cmd_taint W607-CJ 5th-discipline anchor). Floor dict is a
            # literal constant.
            def _compute_predicate_fields(_relationships) -> dict:
                _distances = [
                    r.get("distance") for r in _relationships if isinstance(r, dict) and r.get("distance") is not None
                ]
                _path_count = len(_distances)
                if _distances:
                    _shortest = min(_distances)
                    _max = max(_distances)
                else:
                    _shortest = 0
                    _max = 0
                return {
                    "path_count": _path_count,
                    "shortest_length": _shortest,
                    "max_length": _max,
                }

            _pred_fields = _run_check_da(
                "compute_predicate",
                _compute_predicate_fields,
                relationships,
                default={
                    "path_count": 0,
                    "shortest_length": 0,
                    "max_length": 0,
                },
            )

            # W978 KWARG-DEFAULT EAGERNESS NOTE (W607-CR 7th-discipline
            # anchor): do NOT use ``_pred_fields.get("path_count",
            # len(relationships))`` -- the second arg evaluates EAGERLY.
            # _pred_fields ALWAYS carries the keys (either real value or
            # floor 0), so a bare lookup is correct.
            _summary: dict = {
                "verdict": verdict,
                "symbol_count": len(input_ids),
                "cohesion": round(cohesion, 2),
                "direct_edges": len(direct_edges),
                "conflict_risks": len(conflicts_out),
                # W607-DA: surface score_classify state + predicate metrics
                # on the envelope so consumers can read the relation shape
                # without re-deriving from raw counts.
                "relation_state": _score_dict["state"],
                "path_count": _pred_fields["path_count"],
                "shortest_length": _pred_fields["shortest_length"],
                "max_length": _pred_fields["max_length"],
                **resolution_block,
            }
            _kwargs: dict = {
                "summary": _summary,
                "relationships": relationships,
                "shared_deps": shared_deps_out,
                "shared_callers": shared_callers_out,
                "conflicts": conflicts_out,
                "distance_matrix": dist_matrix_out,
                "resolutions": per_input_resolutions,
                **resolution_block,
            }
            # W607-W / W607-DA -- surface substrate-CALL markers AND
            # aggregation-phase markers on the success path. partial_success
            # flips so consumers can distinguish a clean relation analysis
            # from one that ran with substrate degradation (e.g.,
            # compute_distance_matrix raised but direct_edges + shared_deps
            # still produced rows). Mirror both top-level and summary slots
            # so default-detail-mode envelope stripping preserves the
            # marker channel.
            #
            # Both buckets share the canonical ``relate_*`` marker family
            # (W607-DA is additive, not a separate prefix); the additive
            # bucket stays distinguishable via its phase names.
            #
            # NOTE: the existing P1VD ``resolution_block`` is orthogonal --
            # ``partial_success`` can flip from EITHER axis. We use direct
            # assignment (not OR) because a True from either side wins;
            # consumers should not rely on which axis flipped it.
            _combined_warnings_out = list(_w607w_warnings_out) + list(_w607da_warnings_out)
            if _combined_warnings_out:
                _summary["warnings_out"] = list(_combined_warnings_out)
                _summary["partial_success"] = True
                _kwargs["warnings_out"] = list(_combined_warnings_out)
                _kwargs["partial_success"] = True

            # W607-DA -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor that
            # breaks ``json_envelope("relate", ...)`` would otherwise crash
            # AFTER all substrate + aggregation signals were already
            # gathered. Floor to a minimal envelope stub so consumers still
            # receive a parseable JSON object with the marker attached + the
            # canonical command name. Mirror of cmd_postmortem W607-CV's
            # serialize_envelope floor pattern.
            _envelope_floor: dict = {
                "command": "relate",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": verdict,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings_out),
                },
                "warnings_out": list(_combined_warnings_out),
            }
            _envelope = _run_check_da(
                "serialize_envelope",
                json_envelope,
                "relate",
                default=_envelope_floor,
                **_kwargs,
            )
            # W607-DA -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``relate_serialize_envelope_failed:`` marker was appended to
            # ``_w607da_warnings_out`` and the floor stub carries only the
            # pre-raise combined list. Rebuild the floor stub's
            # warnings_out so the new marker reaches the JSON output.
            # Clean path -> envelope is the real json_envelope return
            # value, no rebuild needed.
            if _envelope is _envelope_floor and _w607da_warnings_out:
                _combined_warnings_out = list(_w607w_warnings_out) + list(_w607da_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                _envelope = _envelope_floor

            click.echo(to_json(_envelope))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")

        if relationships:
            click.echo("\nRELATIONSHIPS:")
            for rel in relationships:
                dist_str = f"distance {rel['distance']}" if rel["distance"] is not None else "no path"
                via_str = f" via {rel['via']}" if rel.get("via") else ""
                click.echo(f"  {rel['source']} -> {rel['target']}    {rel['kind']} ({dist_str}){via_str}")

        if shared_deps_out:
            click.echo("\nSHARED DEPENDENCIES:")
            for dep in shared_deps_out:
                click.echo(f"  {dep['name']}    used by: {', '.join(dep['used_by'])}")

        if shared_callers_out:
            click.echo("\nSHARED CALLERS:")
            for caller in shared_callers_out:
                click.echo(f"  {caller['name']}    calls: {', '.join(caller['calls'])}")

        if conflicts_out:
            click.echo("\nCONFLICT RISKS:")
            for c in conflicts_out:
                click.echo(f"  {c['symbol']} -- modified by {' AND '.join(c['modified_by'])}")
                click.echo(f"  Recommendation: {c['recommendation']}")

        # Distance matrix
        if len(input_ids) >= 2:
            click.echo("\nDISTANCE MATRIX:")
            names = [input_names[sid] for sid in input_ids]
            # Header
            max_name_len = max(len(n) for n in names)
            header = " " * (max_name_len + 2)
            for n in names:
                header += f"{n:>{max_name_len + 2}}"
            click.echo(header)
            for sid in input_ids:
                name = input_names[sid]
                row = f"  {name:<{max_name_len}}"
                for sid2 in input_ids:
                    name2 = input_names[sid2]
                    if sid == sid2:
                        row += f"{'-':>{max_name_len + 2}}"
                    else:
                        d = dist_matrix_out[name][name2]
                        val = str(d) if d is not None else "-"
                        row += f"{val:>{max_name_len + 2}}"
                click.echo(row)
