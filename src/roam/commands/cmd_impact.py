"""Show blast radius: what breaks if a symbol changes."""

from __future__ import annotations

import time

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db
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
    REACH_PCT_DEFINITION,
    WEIGHTED_IMPACT_DEFINITION,
)
from roam.runs.helpers import auto_log


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


def _impact_verdict(dependents, affected_files, total_syms):
    """Generate blast radius verdict string."""
    reach_pct = (len(dependents) / total_syms * 100) if total_syms > 0 else 0
    if reach_pct >= 10 or len(dependents) >= 50:
        return (
            f"Large blast radius — {len(dependents)} symbols ({reach_pct:.1f}%) in {len(affected_files)} files affected",
            reach_pct,
        )
    if reach_pct >= 2 or len(dependents) >= 10:
        return (
            f"Moderate blast radius — {len(dependents)} symbols ({reach_pct:.1f}%) in {len(affected_files)} files affected",
            reach_pct,
        )
    if len(dependents) > 0:
        return (
            f"Small blast radius — {len(dependents)} symbols in {len(affected_files)} files affected",
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

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
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
            not_found_env = json_envelope(
                "impact",
                summary={
                    "verdict": f"Symbol '{name}' not found",
                    "partial_success": True,
                    "state": "not_found",
                    **unresolved_disclosure,
                },
                symbol=name or "",
                **unresolved_disclosure,
            )
            # W1277 — auto-log the unresolved attempt for replay-narration
            # provenance. Silent no-op if no active run.
            auto_log(not_found_env, action="impact", target=name or "")
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

        try:
            from roam.graph.builder import build_symbol_graph
        except ImportError:
            click.echo("Graph module not available. Run `roam index` to build the dependency graph.")
            return

        G = build_symbol_graph(conn)
        if sym_id not in G:
            verdict = f"Symbol '{name}' exists in the index but is not in the dependency graph."
            if resolution_tier == "fuzzy":
                verdict = f"{verdict} [fuzzy resolution -- target '{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            tip = f"Run `roam index` to rebuild the graph, or use `roam symbol {name}` to view raw symbol data."
            not_in_graph_env = json_envelope(
                "impact",
                budget=token_budget,
                summary={
                    "verdict": verdict,
                    "affected_symbols": 0,
                    "affected_files": 0,
                    "in_graph": False,
                    # W331: stamp definitions so MCP consumers see the
                    # same envelope shape even when the target is not in
                    # the dependency graph.
                    "affected_symbols_definition": BLAST_RADIUS_AFFECTED_SYMBOLS,
                    "affected_files_definition": BLAST_RADIUS_AFFECTED_FILES,
                    # W1242 — Pattern-2 variant-D resolution disclosure.
                    **resolution_block,
                },
                symbol=sym["qualified_name"] or sym["name"],
                tip=tip,
                direct_dependents={},
                affected_file_list=[],
                indirect_refs=[],
                **resolution_block,
            )
            # W15.2 — auto-log into the active run. Silent no-op if no run.
            auto_log(not_in_graph_env, action="impact", target=name or "")
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
        (
            dependents,
            affected_files,
            direct_callers,
            by_kind,
            sf_test_files,
            bfs_state,
        ) = _collect_dependents(
            G,
            RG,
            sym_id,
            conn,
            max_hops=effective_depth,
            max_callers=effective_max_callers,
            deadline=deadline,
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
            no_dep_verdict = "no dependents"
            if resolution_tier == "fuzzy":
                no_dep_verdict = f"{no_dep_verdict} [fuzzy resolution -- target '{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            no_dep_env = json_envelope(
                "impact",
                budget=token_budget,
                summary={
                    "verdict": no_dep_verdict,
                    "affected_symbols": 0,
                    "affected_files": 0,
                    # W331: even on the leaf-symbol path the consumer
                    # still needs to know what these zero counts measure.
                    "affected_symbols_definition": BLAST_RADIUS_AFFECTED_SYMBOLS,
                    "affected_files_definition": BLAST_RADIUS_AFFECTED_FILES,
                    "weighted_impact_definition": WEIGHTED_IMPACT_DEFINITION,
                    "reach_pct_definition": REACH_PCT_DEFINITION,
                    # W1242 — Pattern-2 variant-D resolution disclosure.
                    **resolution_block,
                },
                symbol=sym["qualified_name"] or sym["name"],
                affected_symbols=0,
                affected_files=0,
                direct_dependents={},
                affected_file_list=[],
                **resolution_block,
            )
            # W15.2 — auto-log into the active run. Silent no-op if no run.
            auto_log(no_dep_env, action="impact", target=name or "")
            if sarif_mode:
                # W1165: SARIF projection for CI / GitHub Code Scanning.
                from roam.output.sarif import impact_to_sarif, write_sarif

                click.echo(write_sarif(impact_to_sarif(no_dep_env)))
            elif json_mode:
                click.echo(to_json(no_dep_env))
            else:
                click.echo("VERDICT: no dependents — safe to change")
            return

        weighted_impact = sum(ppr.get(d, 0) for d in dependents)

        # dispatch-via-registry detection. roam's call graph
        # only sees direct calls; consumers that route through string
        # lookup tables (cli ``_COMMANDS``, ask recipe registry, plugin
        # entry points) are invisible. Scan source files for string
        # literals matching this symbol's name and qualified name to
        # surface those callsites as ``indirect_refs``.
        indirect_refs = _find_indirect_refs(conn, sym, affected_files)
        verdict, reach_pct = _impact_verdict(dependents, affected_files, len(G))
        if truncated:
            # Name the limit(s) that fired so an agent can decide to re-run
            # with a larger budget. Imperative, concrete (LAWs 2, 4).
            _limit_notes = []
            if bfs_state["hit_timeout"]:
                _limit_notes.append(f"timeout={timeout}s")
            if bfs_state["hit_caller_cap"]:
                _limit_notes.append(f"max-callers={effective_max_callers}")
            if bfs_state["hit_depth_cap"]:
                _limit_notes.append(f"depth={effective_depth}")
            verdict = f"{verdict} (partial: {', '.join(_limit_notes)} — re-run with larger limits for full radius)"
        # W1242 — Pattern-2 variant-D: surface fuzzy-resolution in the verdict
        # so text-only consumers see the degradation. The exact target the
        # resolver landed on goes into the suffix so an agent can decide to
        # re-run with the precise qualified name.
        if resolution_tier == "fuzzy":
            verdict = (
                f"{verdict} [fuzzy resolution -- "
                f"target '{sym['qualified_name'] or sym['name']}' may not be what you meant]"
            )

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
        impact_env = json_envelope(
            "impact",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "affected_symbols": len(dependents),
                "affected_files": len(affected_files),
                # W336 — widen rounding from 4 -> 6 decimals. Per-node PageRank
                # values on a 20k-symbol graph fall in the 1e-5 to 1e-3 range,
                # so 4-decimal rounding truncated legitimate small sums to 0
                # even when 100 affected symbols were reached. 6 decimals
                # keeps the value human-readable AND non-zero for any real
                # blast radius.
                "weighted_impact": round(weighted_impact, 6),
                "reach_pct": round(reach_pct, 1),
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
                "weighted_impact_definition": WEIGHTED_IMPACT_DEFINITION,
                "reach_pct_definition": REACH_PCT_DEFINITION,
                # W1242 — Pattern-2 variant-D resolution disclosure. The
                # helper sets ``partial_success`` to ``resolution != "symbol"``,
                # so we override above with the combined-OR semantics.
                **{k: v for k, v in resolution_block.items() if k != "partial_success"},
            },
            symbol=sym["qualified_name"] or sym["name"],
            affected_symbols=len(dependents),
            affected_files=len(affected_files),
            weighted_impact=round(weighted_impact, 6),
            reach_pct=round(reach_pct, 1),
            direct_dependents=json_deps,
            affected_file_list=affected_file_dicts,
            sf_convention_tests=sorted(sf_test_files),
            indirect_refs=indirect_refs,
            truncated=truncated,
            partial_success=is_partial,
            state=run_state,
            limits=limits_block,
            **{k: v for k, v in resolution_block.items() if k != "partial_success"},
        )
        # W15.2 — auto-log into the active run (silent no-op if none).
        auto_log(impact_env, action="impact", target=name or "")

        if sarif_mode:
            # W1165: SARIF projection for CI / GitHub Code Scanning
            # integration. The auto_log call above stays identical to
            # the JSON / text paths so the audit ledger is invariant
            # across output formats. The --text / --json paths are
            # byte-identical to pre-W1165 (this branch short-circuits
            # before the legacy branches; nothing above it changed shape).
            from roam.output.sarif import impact_to_sarif, write_sarif

            click.echo(write_sarif(impact_to_sarif(impact_env)))
            return

        if json_mode:
            click.echo(to_json(impact_env))
            return

        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Affected symbols: {len(dependents)}  Affected files: {len(affected_files)}")
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
            click.echo(f"\nAffected files ({len(affected_files)} — ranked by impact):")
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
