"""Recommend the smallest resolved extraction that breaks each file cycle."""

from __future__ import annotations

import sqlite3

import click
import networkx as nx

from roam.capability import roam_capability
from roam.commands.resolve import empty_corpus_state, ensure_index
from roam.db.connection import find_project_root, open_db
from roam.graph.builder import build_file_graph
from roam.graph.cycles import find_cycles, find_minimum_cycle_break_edge_sets
from roam.index.relations import _extract_imported_names, _read_source_text
from roam.output.formatter import json_envelope, to_json


def _path(graph: nx.DiGraph, file_id: int) -> str:
    return str(graph.nodes[file_id].get("path", file_id))


def _imported_names(
    source_path: str,
    project_root: str,
    cache: dict[str, set[str] | None],
) -> set[str] | None:
    if source_path not in cache:
        source = _read_source_text(source_path, project_root)
        cache[source_path] = _extract_imported_names(source) if source is not None else None
    return cache[source_path]


def _crossing_symbols(
    conn: sqlite3.Connection,
    source_file_id: int,
    target_file_id: int,
    imported_names: set[str] | None,
    cache: dict[tuple[int, int], list[dict]],
) -> list[dict]:
    if imported_names is None:
        return []
    cache_key = (source_file_id, target_file_id)
    if cache_key in cache:
        return cache[cache_key]
    rows = conn.execute(
        "SELECT DISTINCT target.id, target.name, target.kind, edge.kind "
        "FROM edges edge "
        "JOIN symbols source ON source.id = edge.source_id "
        "JOIN symbols target ON target.id = edge.target_id "
        "WHERE source.file_id = ? AND target.file_id = ? "
        "ORDER BY target.name, target.kind, target.id, edge.kind",
        (source_file_id, target_file_id),
    ).fetchall()

    symbols: dict[int, dict] = {}
    for symbol_id, name, kind, edge_kind in rows:
        if name not in imported_names:
            continue
        symbol = symbols.setdefault(
            symbol_id,
            {"id": symbol_id, "name": name, "kind": kind, "relationship_kinds": []},
        )
        if edge_kind not in symbol["relationship_kinds"]:
            symbol["relationship_kinds"].append(edge_kind)
    resolved = list(symbols.values())
    names = [symbol["name"] for symbol in resolved]
    cache[cache_key] = resolved if len(names) == len(set(names)) else []
    return cache[cache_key]


def _ordered_members(graph: nx.DiGraph, members: list[int], removed: list[tuple[int, int]]) -> list[int]:
    candidate = graph.subgraph(members).copy()
    candidate.remove_edges_from(removed)
    return list(nx.lexicographical_topological_sort(candidate, key=lambda file_id: _path(graph, file_id)))


def _candidate_details(
    conn: sqlite3.Connection,
    graph: nx.DiGraph,
    members: list[int],
    removed: list[tuple[int, int]],
    project_root: str,
    imported_names_cache: dict[str, set[str] | None],
    crossing_symbols_cache: dict[tuple[int, int], list[dict]],
) -> tuple[tuple, list[int], list[dict], bool]:
    ordered = _ordered_members(graph, members, removed)
    edges = []
    for source, target in removed:
        source_path = _path(graph, source)
        edges.append(
            {
                "source": {"id": source, "file": source_path},
                "target": {"id": target, "file": _path(graph, target)},
                "symbols": _crossing_symbols(
                    conn,
                    source,
                    target,
                    _imported_names(source_path, project_root, imported_names_cache),
                    crossing_symbols_cache,
                ),
            }
        )

    resolved = all(edge["symbols"] for edge in edges)
    symbol_count = sum(len(edge["symbols"]) for edge in edges)
    ordered_paths = tuple(_path(graph, file_id) for file_id in ordered)
    edge_paths = tuple((edge["source"]["file"], edge["target"]["file"]) for edge in edges)
    rank = (not resolved, symbol_count if resolved else 0, ordered_paths, edge_paths)
    return rank, ordered, edges, resolved


def _recommendation(edge: dict, cycle_path: str) -> str:
    names = [symbol["name"] for symbol in edge["symbols"]]
    rendered = ", ".join(f"`{name}`" for name in names)
    noun = "symbol" if len(names) == 1 else "symbols"
    source = edge["source"]["file"]
    target = edge["target"]["file"]
    return (
        f"Extract {noun} {rendered} from `{target}` into a new leaf module imported by both "
        f"`{source}` and `{target}` to break `{cycle_path}`."
    )


def _analyze_cycle(
    conn: sqlite3.Connection,
    graph: nx.DiGraph,
    members: list[int],
    project_root: str,
    imported_names_cache: dict[str, set[str] | None],
    crossing_symbols_cache: dict[tuple[int, int], list[dict]],
) -> dict:
    candidates = find_minimum_cycle_break_edge_sets(graph, members)
    if not candidates:
        ordered = sorted(members, key=lambda file_id: _path(graph, file_id))
        return {
            "members": [{"id": file_id, "file": _path(graph, file_id)} for file_id in ordered],
            "closing_edges": [],
            "recommendations": [],
            "recommendation_state": "minimum_break_not_computed",
            "recommendation_reason": "exact search is limited to SCCs with at most 14 internal edges",
        }

    _rank, ordered, edges, resolved = min(
        (
            _candidate_details(
                conn,
                graph,
                members,
                removed,
                project_root,
                imported_names_cache,
                crossing_symbols_cache,
            )
            for removed in candidates
        ),
        key=lambda item: item[0],
    )
    ordered_paths = [_path(graph, file_id) for file_id in ordered]
    cycle_path = " → ".join([*ordered_paths, ordered_paths[0]])
    recommendations = []
    if resolved:
        for edge in edges:
            edge["recommendation"] = _recommendation(edge, cycle_path)
            recommendations.append(edge["recommendation"])

    return {
        "members": [{"id": file_id, "file": _path(graph, file_id)} for file_id in ordered],
        "closing_edges": edges,
        "recommendations": recommendations,
        "recommendation_state": "resolved" if resolved else "unresolved_crossing_symbols",
    }


@roam_capability(
    name="cycle-break",
    category="architecture",
    summary="Recommend the minimum resolved extraction that breaks each file cycle",
    maturity="stable",
    mcp_expose=False,
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="cycle-break")
@click.option("--json", "json_output", is_flag=True, help="Output in JSON format.")
@click.pass_context
def cycle_break(ctx, json_output):
    """Recommend minimal symbol extractions that break file dependency cycles."""
    json_mode = json_output or (ctx.obj.get("json") if ctx.obj else False)
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        empty = empty_corpus_state(conn)
        if empty is not None:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "cycle-break",
                            summary={"verdict": "no dependency graph to analyze", "cycle_count": 0, **empty},
                            cycles=[],
                            budget=token_budget,
                        )
                    )
                )
            return

        graph = build_file_graph(conn)
        raw_cycles = find_cycles(graph)
        project_root = str(find_project_root())
        imported_names_cache: dict[str, set[str] | None] = {}
        crossing_symbols_cache: dict[tuple[int, int], list[dict]] = {}
        findings = [
            _analyze_cycle(
                conn,
                graph,
                members,
                project_root,
                imported_names_cache,
                crossing_symbols_cache,
            )
            for members in raw_cycles
        ]
        recommendation_count = sum(len(finding["recommendations"]) for finding in findings)
        cycle_noun = "cycle" if len(findings) == 1 else "cycles"
        recommendation_noun = "recommendation" if recommendation_count == 1 else "recommendations"
        verdict = (
            f"{len(findings)} file dependency {cycle_noun}; "
            f"{recommendation_count} minimal extraction {recommendation_noun}"
            if findings
            else "No file dependency cycles"
        )

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "cycle-break",
                        summary={
                            "verdict": verdict,
                            "cycle_count": len(findings),
                            "recommendation_count": recommendation_count,
                            "cycle_count_definition": (
                                "strongly-connected components (Tarjan SCC) of the indexed file dependency graph"
                            ),
                        },
                        cycles=findings,
                        budget=token_budget,
                    )
                )
            )
            return

        if not findings:
            return
        click.echo(f"VERDICT: {verdict}")
        for index, finding in enumerate(findings, 1):
            members = [member["file"] for member in finding["members"]]
            click.echo(f"\n  cycle {index}: {' → '.join([*members, members[0]])}")
            for edge in finding["closing_edges"]:
                click.echo(f"    closing edge: {edge['source']['file']} → {edge['target']['file']}")
                names = ", ".join(symbol["name"] for symbol in edge["symbols"])
                if names:
                    click.echo(f"    symbols: {names}")
            for recommendation in finding["recommendations"]:
                click.echo(f"    recommendation: {recommendation}")
