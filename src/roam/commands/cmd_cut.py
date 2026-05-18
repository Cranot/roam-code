"""Minimum cut analysis — find fragile domain boundaries.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cut outputs are invocation-scoped graph-partition
boundary enumerations (candidate edge cuts ranked by separation
quality) — not per-location code violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1224-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

_MAX_GRAPH_SYMBOLS = 5000


@roam_capability(
    name="cut",
    category="architecture",
    summary="Minimum cut analysis — find fragile domain boundaries",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("cut")
@click.option("--between", nargs=2, default=None, help="Analyze boundary between two clusters")
@click.option("--leak-edges", "leak_edges", is_flag=True, help="Focus on leak edge analysis")
@click.option("--top", "top_n", default=10, type=int, help="Show top <N> boundaries")
@click.pass_context
def cut(ctx, between, leak_edges, top_n):
    """Minimum cut analysis — find fragile domain boundaries.

    Unlike ``split`` (which decomposes a single file), this command finds
    the thinnest boundaries between architectural clusters using graph
    min-cut analysis.

    Computes minimum edge cuts between architectural clusters to identify
    the thinnest (most fragile) boundaries and the highest-impact "leak
    edges" whose removal would best improve domain isolation.

    \b
    Examples:
      roam cut
      roam cut --top 5
      roam cut --between auth payments
      roam cut --leak-edges --top 20

    See also ``split`` (decompose a single file), ``clusters``
    (community-detection groupings), and ``layers`` (dependency-layer
    violations).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        try:
            import networkx as nx

            from roam.graph.builder import build_symbol_graph
            from roam.graph.clusters import detect_clusters, label_clusters
        except ImportError:
            click.echo("VERDICT: NetworkX required for cut analysis")
            return

        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if sym_count > _MAX_GRAPH_SYMBOLS and not between:
            # W1086 (Pattern-1A): mirrors the W1085 cmd_fingerprint canonical
            # fix verbatim. Refuse-on-prerequisite must surface
            # partial_success + closed-enum state. The pre-W1086 envelope
            # ({"verdict": msg, "symbol_count": sym_count}) was structurally
            # indistinguishable from "analyzed cleanly with no findings" to
            # any consumer that only reads summary fields.
            # LAW 4: terminal token must hit the concrete-noun anchor set
            # (formatter.concrete_plural_terminals -> "symbols"). Put the
            # cap clause first so the sentence ends on "symbols".
            verdict = f"Skipped cut above cap {_MAX_GRAPH_SYMBOLS:,}: graph has {sym_count:,} symbols"
            hint = (
                "Index a subdirectory with `roam index <path>` to narrow the "
                "analyzed subset, or raise `_MAX_GRAPH_SYMBOLS` in "
                "src/roam/commands/cmd_cut.py."
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "cut",
                            summary={
                                "verdict": verdict,
                                "symbol_count": sym_count,
                                "hard_cap": _MAX_GRAPH_SYMBOLS,
                                "partial_success": True,
                                "state": "graph_too_large",
                                "cap_threshold": _MAX_GRAPH_SYMBOLS,
                                "actual_count": sym_count,
                            },
                            hint=hint,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo(f"HINT: {hint}")
            return

        # W607-EI -- substrate-boundary plumbing for cmd_cut.
        # ``_run_check_ei`` wraps each substrate helper so an uncaught
        # raise in any one boundary degrades to a sensible empty-floor
        # default AND surfaces a marker in ``_w607ei_warnings_out``
        # rather than crashing the cut command outright. cmd_cut is the
        # graph-cut command (minimum edge cuts between architectural
        # clusters), one leg of the structural-analysis family alongside
        # cmd_closure (transitive closure) and cmd_simulate (W607-EF,
        # counterfactual transforms). A raise inside ``detect_clusters``
        # / ``label_clusters`` / ``nx.minimum_edge_cut`` /
        # ``nx.edge_betweenness_centrality`` / or any downstream verdict
        # / envelope composer used to crash the cut command outright.
        # Marker family ``cut_<phase>_failed:<exc_class>:<detail>``.
        # Substrates wrapped:
        #
        #   * detect_clusters         -- detect_clusters + label_clusters
        #                                + cluster_nodes grouping
        #   * compute_min_cuts        -- the boundaries loop
        #                                (cross-edges + min-cut + thinness)
        #   * extract_leak_edges      -- edge_betweenness leak-edge ranking
        #   * compose_verdict         -- LAW 6 single-line floor
        #   * compose_facts           -- agent_contract.facts list
        #   * compose_next_commands   -- agent_contract.next_commands
        #   * serialize_envelope      -- JSON envelope emission
        #   * format_text_output      -- text path boundary table printing
        #
        # W978 7-discipline applied: (1) f-string verdict floor uses
        # literal zero-count text -- no Name references, (2) default={...}
        # carries plain literals, (3) no json.dumps(default=str) needed
        # (no datetimes), (4) ``cut_*`` prefix is unique
        # (collision-checked by cross-prefix-discipline test), (5) len()
        # at kwarg-bind is gated by the envelope fallback, (6) len() /
        # if x: on a poisoned object only runs after the empty-floor
        # guard, (7) no dict.get(key, expensive_default) calls -- all
        # defaults are immutable literals.
        _w607ei_warnings_out: list[str] = []

        def _run_check_ei(phase, fn, *args, default=None, **kwargs):
            """Run one substrate helper with W607-EI marker emission.

            On a clean call the result is returned as-is. On an uncaught
            exception, surface a
            ``cut_<phase>_failed:<exc_class>:<detail>`` marker via
            ``_w607ei_warnings_out`` and return *default* -- the
            envelope still emits cleanly with the remaining substrates.
            """
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- top-level disclosure
                _w607ei_warnings_out.append(f"cut_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

        G = build_symbol_graph(conn)

        if len(G) < 2:
            # W1010 Pattern 2: degenerate graph (<2 nodes). Pre-fix envelope
            # said "No cross-cluster boundaries found" — indistinguishable
            # from a clean multi-cluster graph with no leakage. Surface
            # graph_too_small + partial_success so consumers can tell the
            # difference between "couldn't analyze" and "analyzed cleanly".
            verdict = f"Skipped cut analysis: graph has {len(G)} symbols (need >= 2)"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "cut",
                            summary={
                                "verdict": verdict,
                                "boundaries_analyzed": 0,
                                "fragile_boundaries": 0,
                                "leak_edges_found": 0,
                                "partial_success": True,
                                "state": "graph_too_small",
                            },
                            boundaries=[],
                            leak_edges=[],
                            hint="Run 'roam init' to ensure the index has at least two symbols, or index a subdirectory.",
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        # W607-EI: ``detect_clusters`` substrate -- detect_clusters +
        # label_clusters + cluster_nodes grouping. A raise inside any
        # of these degrades to empty clusters/labels/cluster_nodes so
        # the downstream substrates still compose a coherent envelope.
        def _detect_clusters():
            clusters_local = detect_clusters(G)
            labels_local = label_clusters(clusters_local, conn)
            cluster_nodes_local: dict[int, set] = {}
            for node_id, cid in clusters_local.items():
                cluster_nodes_local.setdefault(cid, set()).add(node_id)
            return (clusters_local, labels_local, cluster_nodes_local)

        cluster_bundle = _run_check_ei(
            "detect_clusters",
            _detect_clusters,
            default=({}, {}, {}),
        )
        if cluster_bundle is None:
            cluster_bundle = ({}, {}, {})
        clusters, labels, cluster_nodes = cluster_bundle
        if clusters is None:
            clusters = {}
        if labels is None:
            labels = {}
        if cluster_nodes is None:
            cluster_nodes = {}

        # Build undirected version for min-cut
        UG = G.to_undirected()

        # Need at least 2 clusters for boundary analysis
        cluster_ids = sorted(cluster_nodes.keys())

        boundaries: list[dict] = []

        # W607-EI: ``compute_min_cuts`` substrate -- the boundaries loop
        # (cross-edges + min-cut + thinness). A raise inside the loop
        # (e.g. malformed cluster bookkeeping, KeyError on G.edges())
        # degrades to an empty boundaries list so the verdict still
        # composes against zero-counted cut analysis.
        def _compute_min_cuts():
            boundaries_local: list[dict] = []
            for i, c1 in enumerate(cluster_ids):
                for c2 in cluster_ids[i + 1 :]:
                    if between:
                        # Filter to requested pair
                        l1 = labels.get(c1, str(c1)).lower()
                        l2 = labels.get(c2, str(c2)).lower()
                        if not (
                            (between[0].lower() in l1 and between[1].lower() in l2)
                            or (between[1].lower() in l1 and between[0].lower() in l2)
                        ):
                            continue

                    # Count cross-edges
                    cross_edges: list[tuple] = []
                    nodes_c1 = cluster_nodes.get(c1, set())
                    nodes_c2 = cluster_nodes.get(c2, set())
                    for u, v in G.edges():
                        if (u in nodes_c1 and v in nodes_c2) or (u in nodes_c2 and v in nodes_c1):
                            cross_edges.append((u, v))

                    if not cross_edges:
                        continue

                    # Find a pair of connected nodes across clusters for min-cut seed
                    src_node = None
                    tgt_node = None
                    for u, v in cross_edges:
                        if u in nodes_c1 and v in nodes_c2:
                            src_node, tgt_node = u, v
                            break
                        elif u in nodes_c2 and v in nodes_c1:
                            src_node, tgt_node = u, v
                            break

                    if src_node is None:
                        continue

                    try:
                        min_cut_edges = nx.minimum_edge_cut(UG, src_node, tgt_node)
                        min_cut_size = len(min_cut_edges)
                    except (nx.NetworkXError, nx.NetworkXUnbounded, nx.exception.NetworkXError):
                        min_cut_edges = set()
                        min_cut_size = len(cross_edges)

                    thinness = min_cut_size / len(cross_edges) if cross_edges else 1.0
                    fragile = thinness < 0.4

                    # Get edge details for cut edges
                    cut_details: list[dict] = []
                    for u, v in sorted(min_cut_edges):
                        u_node = G.nodes.get(u, {})
                        v_node = G.nodes.get(v, {})
                        cut_details.append(
                            {
                                "source": u_node.get("name", str(u)),
                                "source_file": u_node.get("file_path", ""),
                                "target": v_node.get("name", str(v)),
                                "target_file": v_node.get("file_path", ""),
                            }
                        )

                    boundaries_local.append(
                        {
                            "cluster_a": labels.get(c1, f"cluster_{c1}"),
                            "cluster_b": labels.get(c2, f"cluster_{c2}"),
                            "cross_edges": len(cross_edges),
                            "min_cut": min_cut_size,
                            "thinness": round(thinness, 2),
                            "fragile": fragile,
                            "cut_edges": cut_details[:5],
                        }
                    )
            # Sort by thinness (most fragile first)
            boundaries_local.sort(key=lambda b: b["thinness"])
            return boundaries_local[:top_n]

        boundaries = _run_check_ei(
            "compute_min_cuts",
            _compute_min_cuts,
            default=[],
        )
        if boundaries is None:
            boundaries = []

        # W607-EI: ``extract_leak_edges`` substrate -- edge_betweenness
        # leak-edge ranking. A raise inside ``edge_betweenness_centrality``
        # or the cross-cluster filter loop degrades to an empty list so
        # the envelope still composes.
        def _extract_leak_edges():
            leak_edge_list_local: list[dict] = []
            if not (leak_edges or not between):
                return leak_edge_list_local
            try:
                ebc = nx.edge_betweenness_centrality(UG)
            except Exception:
                ebc = {}

            for (u, v), bc in sorted(ebc.items(), key=lambda x: -x[1]):
                u_cluster = clusters.get(u)
                v_cluster = clusters.get(v)
                if u_cluster is not None and v_cluster is not None and u_cluster != v_cluster:
                    u_node = G.nodes.get(u, {})
                    v_node = G.nodes.get(v, {})
                    leak_edge_list_local.append(
                        {
                            "source": u_node.get("name", str(u)),
                            "source_file": u_node.get("file_path", ""),
                            "target": v_node.get("name", str(v)),
                            "target_file": v_node.get("file_path", ""),
                            "betweenness": round(bc, 4),
                            "suggestion": (
                                f"Extract interface between "
                                f"{u_node.get('name', str(u))} and "
                                f"{v_node.get('name', str(v))}"
                            ),
                        }
                    )
                    if len(leak_edge_list_local) >= top_n:
                        break
            return leak_edge_list_local

        leak_edge_list = _run_check_ei(
            "extract_leak_edges",
            _extract_leak_edges,
            default=[],
        )
        if leak_edge_list is None:
            leak_edge_list = []

        # W607-EI: ``compose_verdict`` substrate -- LAW 6 single-line
        # boundary-analysis floor. A raise degrades to the literal
        # zero-floor string with explicit empty counts -- the
        # W811/W817 Pattern-2 guard: never collapse to a SAFE/passed
        # verdict on the degraded path. W978 #1: f-string verdict
        # floor uses plain text, no Name references inside the
        # literal.
        def _compose_verdict():
            if not isinstance(boundaries, list):
                return ("0 boundaries analyzed, 0 fragile", 0, 0)
            fragile_count_local = sum(1 for b in boundaries if isinstance(b, dict) and b.get("fragile"))
            total_boundaries_local = len(boundaries)
            if total_boundaries_local == 0:
                verdict_local = "0 boundaries analyzed, 0 fragile boundaries"
            elif fragile_count_local == 0:
                verdict_local = f"{total_boundaries_local} boundaries analyzed, 0 fragile boundaries"
            else:
                verdict_local = (
                    f"{total_boundaries_local} boundaries analyzed, {fragile_count_local} fragile boundaries"
                )
            return (verdict_local, total_boundaries_local, fragile_count_local)

        verdict_bundle = _run_check_ei(
            "compose_verdict",
            _compose_verdict,
            default=("0 boundaries analyzed, 0 fragile boundaries", 0, 0),
        )
        if verdict_bundle is None:
            verdict_bundle = ("0 boundaries analyzed, 0 fragile boundaries", 0, 0)
        verdict, total_boundaries, fragile_count = verdict_bundle
        if not isinstance(verdict, str) or not verdict:
            verdict = "0 boundaries analyzed, 0 fragile boundaries"

        # W607-EI: ``compose_facts`` substrate -- curated
        # ``agent_contract.facts`` list. A raise degrades to a single
        # verdict-only fact so LAW 6 verdict-first invariant holds.
        def _compose_facts():
            leak_count_local = len(leak_edge_list) if isinstance(leak_edge_list, list) else 0
            facts_local = [
                verdict,
                f"{fragile_count} fragile clusters",
                f"{leak_count_local} leak edges",
            ]
            return facts_local

        facts = _run_check_ei(
            "compose_facts",
            _compose_facts,
            default=[verdict],
        )
        if facts is None:
            facts = [verdict]

        # W607-EI: ``compose_next_commands`` substrate -- conditional
        # advisory next-step suggestions. A raise degrades to an empty
        # list so the agent_contract still composes.
        def _compose_next_commands():
            cmds: list[str] = []
            if fragile_count > 0:
                cmds.append("roam clusters")
            if isinstance(leak_edge_list, list) and leak_edge_list:
                cmds.append("roam layers")
            return cmds

        next_commands = _run_check_ei(
            "compose_next_commands",
            _compose_next_commands,
            default=[],
        )
        if next_commands is None:
            next_commands = []

        # JSON output
        if json_mode:
            # W607-EI: ``serialize_envelope`` substrate -- json_envelope
            # construction + click.echo emission. The wrap protects
            # against crashes inside the formatter call so the marker
            # surfaces and the function returns cleanly.
            leak_count = len(leak_edge_list) if isinstance(leak_edge_list, list) else 0
            envelope_summary: dict = {
                "verdict": verdict,
                "boundaries_analyzed": total_boundaries,
                "fragile_boundaries": fragile_count,
                "leak_edges_found": leak_count,
            }
            envelope_kwargs: dict = dict(
                summary=envelope_summary,
                boundaries=boundaries,
                leak_edges=leak_edge_list,
                agent_contract={
                    "facts": facts,
                    "risks": [],
                    "next_commands": next_commands,
                    "confidence": None,
                },
            )
            # W607-EI: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard.
            if _w607ei_warnings_out:
                envelope_summary["partial_success"] = True
                envelope_summary["warnings_out"] = list(_w607ei_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607ei_warnings_out)

            def _serialize_envelope():
                click.echo(to_json(json_envelope("cut", **envelope_kwargs)))

            _run_check_ei("serialize_envelope", _serialize_envelope, default=None)
            return

        # W607-EI: ``format_text_output`` substrate -- the human-readable
        # text emission path. A raise inside the boundary or leak-edge
        # loop (e.g. KeyError on a malformed boundary dict) degrades to
        # a verdict-only emission so the user still sees the LAW 6
        # floor.
        def _format_text_output():
            click.echo(f"VERDICT: {verdict}")
            click.echo()

            for b in boundaries:
                if not isinstance(b, dict):
                    continue
                tag = "<< FRAGILE" if b.get("fragile") else "(adequate)"
                click.echo(f"BOUNDARY: {b.get('cluster_a', '?')} <-> {b.get('cluster_b', '?')}")
                click.echo(
                    f"  Cross-edges: {b.get('cross_edges', 0)} | Min cut: {b.get('min_cut', 0)} edges | "
                    f"Thinness: {b.get('thinness', 0)}  {tag}"
                )
                if b.get("cut_edges"):
                    click.echo("  Cut edges:")
                    for ce in b["cut_edges"]:
                        click.echo(
                            f"    {ce.get('source_file', '')}::{ce.get('source', '')} -> "
                            f"{ce.get('target_file', '')}::{ce.get('target', '')}"
                        )
                click.echo()

            if leak_edge_list:
                click.echo("LEAK EDGES (highest blast-radius amplification):")
                for i, le in enumerate(leak_edge_list[:5], 1):
                    click.echo(
                        f"  {i}. {le.get('source_file', '')}::{le.get('source', '')} -> "
                        f"{le.get('target_file', '')}::{le.get('target', '')}"
                    )
                    click.echo(f"     Edge betweenness: {le.get('betweenness', 0)}")
                    click.echo(f"     Suggest: {le.get('suggestion', '')}")

        _run_check_ei("format_text_output", _format_text_output, default=None)
        # Marker accumulator handles disclosure on the text path -- the
        # warning rides into ``_w607ei_warnings_out`` even when
        # text-mode output is human-targeted (JSON mode carries the
        # structured disclosure surface).
