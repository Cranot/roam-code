"""Show blast radius: what breaks if a symbol changes."""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    to_json,
)


def _collect_dependents(G, RG, sym_id, conn, max_hops: int | None = None):
    """Collect affected files, direct callers by kind, and SF test files.

    When ``max_hops`` is set, the BFS is bounded to that many hops instead
    of expanding to the full transitive descendants set.
    """
    import networkx as nx

    if max_hops is None:
        dependents = nx.descendants(RG, sym_id)
    else:
        # Bounded BFS via single_source_shortest_path_length(cutoff=N).
        lengths = nx.single_source_shortest_path_length(RG, sym_id, cutoff=int(max_hops))
        dependents = {n for n in lengths if n != sym_id}
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

    return dependents, affected_files, direct_callers, by_kind, sf_test_files


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
            f"Large blast radius — {len(dependents)} symbols ({reach_pct:.0f}%) in {len(affected_files)} files affected",
            reach_pct,
        )
    if reach_pct >= 2 or len(dependents) >= 10:
        return (
            f"Moderate blast radius — {len(dependents)} symbols ({reach_pct:.0f}%) in {len(affected_files)} files affected",
            reach_pct,
        )
    if len(dependents) > 0:
        return (
            f"Small blast radius — {len(dependents)} symbols in {len(affected_files)} files affected",
            reach_pct,
        )
    return "No dependents — safe to change", reach_pct


@click.command()
@click.argument("name")
@click.option(
    "--hops",
    type=int,
    default=None,
    help=(
        "bound the BFS at N hops (default: full transitive). "
        "``--hops 1`` mirrors ``roam uses``; ``--hops 2`` shows callers "
        "of callers; useful to scope a refactor to a controlled radius."
    ),
)
@click.pass_context
def impact(ctx, name, hops):
    """Show blast radius: what breaks if a symbol changes.

    Unlike ``uses`` (which lists direct callers), this command computes the
    full transitive blast radius including affected files and PageRank-weighted
    importance.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            click.echo(symbol_not_found(conn, name, json_mode=json_mode))
            raise SystemExit(1)
        sym_id = sym["id"]

        if not json_mode:
            click.echo(
                f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  {loc(sym['file_path'], sym['line_start'])}"
            )
            click.echo()

        try:
            import networkx as nx

            from roam.graph.builder import build_symbol_graph
        except ImportError:
            click.echo("Graph module not available. Run `roam index` to build the dependency graph.")
            return

        G = build_symbol_graph(conn)
        if sym_id not in G:
            verdict = f"Symbol '{name}' exists in the index but is not in the dependency graph."
            tip = f"Run `roam index` to rebuild the graph, or use `roam symbol {name}` to view raw symbol data."
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "impact",
                            budget=token_budget,
                            summary={
                                "verdict": verdict,
                                "affected_symbols": 0,
                                "affected_files": 0,
                                "in_graph": False,
                            },
                            symbol=sym["qualified_name"] or sym["name"],
                            tip=tip,
                            direct_dependents={},
                            affected_file_list=[],
                            indirect_refs=[],
                        )
                    )
                )
            else:
                click.echo(f"{verdict}\n  Tip: {tip}")
            return

        RG = G.reverse()
        dependents, affected_files, direct_callers, by_kind, sf_test_files = _collect_dependents(
            G, RG, sym_id, conn, max_hops=hops
        )

        # Personalized PageRank for distance-weighted importance (Gleich 2015)
        ppr = {}
        if dependents:
            try:
                ppr = nx.pagerank(RG, alpha=0.85, personalization={sym_id: 1.0})
            except Exception:
                pass

        if not dependents:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "impact",
                            budget=token_budget,
                            summary={"verdict": "no dependents", "affected_symbols": 0, "affected_files": 0},
                            symbol=sym["qualified_name"] or sym["name"],
                            affected_symbols=0,
                            affected_files=0,
                            direct_dependents={},
                            affected_file_list=[],
                        )
                    )
                )
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

        if json_mode:
            # Look up global PageRank for dependent symbols
            global_pr: dict[int, float] = {}
            try:
                pr_rows = conn.execute("SELECT symbol_id, pagerank FROM graph_metrics").fetchall()
                global_pr = {r["symbol_id"]: r["pagerank"] for r in pr_rows}
            except Exception:
                pass

            json_deps = {
                ek: [{"name": i[1], "kind": i[0], "file": i[2]} for i in items] for ek, items in by_kind.items()
            }
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
            click.echo(
                to_json(
                    json_envelope(
                        "impact",
                        budget=token_budget,
                        summary={
                            "verdict": verdict,
                            "affected_symbols": len(dependents),
                            "affected_files": len(affected_files),
                            "weighted_impact": round(weighted_impact, 4),
                            "reach_pct": round(reach_pct, 1),
                            "sf_convention_tests": len(sf_test_files),
                        },
                        symbol=sym["qualified_name"] or sym["name"],
                        affected_symbols=len(dependents),
                        affected_files=len(affected_files),
                        weighted_impact=round(weighted_impact, 4),
                        reach_pct=round(reach_pct, 1),
                        direct_dependents=json_deps,
                        affected_file_list=affected_file_dicts,
                        sf_convention_tests=sorted(sf_test_files),
                        indirect_refs=indirect_refs,
                    )
                )
            )
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
