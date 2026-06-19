"""Generate a zero-effort onboarding guide for the codebase.

Produces a structured architecture tour: overview, top symbols by
importance, reading order based on topological layers, entry points,
and detected patterns.  Always current because it is computed from
the index, not hand-written documentation.

Prefer ``roam understand --tour`` for the unified single-call alternative.
The ``--mermaid`` flag is also available there (``roam understand --tour
--mermaid``).  This command is kept as a standalone entry point for its
``--write FILE`` flag, which saves the tour to a Markdown file.

Helper functions in this module (``_top_symbols``, ``_reading_order``,
``_entry_points``, ``_tour_mermaid``) are imported by
``roam.commands.cmd_understand`` and must not be removed.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because tour outputs are invocation-scoped topological
onboarding rankings — not per-location violations. Editor consumers
should use the JSON envelope directly. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1148 audit
memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, open_db
from roam.graph.builder import build_symbol_graph
from roam.graph.layers import detect_layers
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json
from roam.output.mermaid import diagram as mdiagram
from roam.output.mermaid import edge as medge
from roam.output.mermaid import node as mnode

_GENERIC_PROP_NAMES = frozenset(
    {
        "path",
        "name",
        "value",
        "key",
        "id",
        "data",
        "type",
        "kind",
        "role",
        "file",
        "time",
        "count",
        "size",
        "length",
        "args",
        "kwargs",
        "self",
        "cls",
    }
)
_GENERIC_PROP_KINDS = frozenset({"property", "field", "attribute"})
_READING_ORDER_SKIP_ROLES = frozenset(
    {
        "test",
        "scripts",
        "generated",
        "vendored",
        "data",
        "examples",
        "build",
        "ci",
        "docs",
        "config",
    }
)


def _top_symbol_rows(conn, limit):
    return conn.execute(
        """SELECT gm.symbol_id, gm.pagerank, gm.in_degree, gm.out_degree,
                  s.name, s.qualified_name, s.kind, f.path, s.line_start,
                  s.docstring,
                  COALESCE(f.file_role, 'source') AS file_role
           FROM graph_metrics gm
           JOIN symbols s ON gm.symbol_id = s.id
           JOIN files f ON s.file_id = f.id
           ORDER BY gm.pagerank DESC
           LIMIT ?""",
        (limit * 6,),
    ).fetchall()


def _is_framework_alias_symbol(row) -> bool:
    from roam.output.framework_filter import is_framework_alias

    return is_framework_alias(row["qualified_name"] or row["name"], row["kind"], row["path"])


def _is_generic_property_symbol(row) -> bool:
    return row["kind"] in _GENERIC_PROP_KINDS and row["name"].lower() in _GENERIC_PROP_NAMES


def _is_top_symbol_candidate(row) -> bool:
    if row["file_role"] == "test":
        return False
    if _is_framework_alias_symbol(row):
        return False
    return not _is_generic_property_symbol(row)


def _tour_symbol_role(in_degree: int, out_degree: int) -> str:
    if in_degree >= 5 and out_degree >= 5:
        return "Hub"
    if in_degree >= 5:
        return "Core utility"
    if out_degree >= 5:
        return "Orchestrator"
    if in_degree < 2 and out_degree < 2:
        return "Leaf"
    return "Internal"


def _symbol_doc_summary(docstring) -> str:
    doc = docstring or ""
    first_sentence = doc.split("\n\n", 1)[0].strip()
    return " ".join(first_sentence.split())[:60]


def _format_top_symbol(row):
    in_degree = row["in_degree"] or 0
    out_degree = row["out_degree"] or 0
    return {
        "name": row["qualified_name"] or row["name"],
        "kind": abbrev_kind(row["kind"]),
        "role": _tour_symbol_role(in_degree, out_degree),
        "fan_in": in_degree,
        "fan_out": out_degree,
        # W361 — match W336's 6-decimal rounding for cmd_impact.
        # Tour symbols on a 25k-symbol graph have per-symbol PageRank
        # in the 1e-5 range; 4 decimals truncated to 0.
        "pagerank": round(row["pagerank"] or 0, 6),
        "location": loc(row["path"], row["line_start"]),
        "summary": _symbol_doc_summary(row["docstring"]),
    }


def _layers_by_number(layer_map):
    if not layer_map:
        return []

    layers = [set() for _ in range(max(layer_map.values()) + 1)]
    for node_id, layer_num in layer_map.items():
        layers[layer_num].add(node_id)
    return layers


def _layer_pagerank_lookup(conn, sym_ids):
    if not sym_ids:
        return {}

    rows = batched_in(
        conn,
        "SELECT gm.symbol_id, gm.pagerank FROM graph_metrics gm WHERE gm.symbol_id IN ({ph}) ORDER BY gm.pagerank DESC",
        list(sym_ids),
    )
    return {row["symbol_id"]: row["pagerank"] or 0 for row in rows}


def _layer_file_rows(conn, sym_ids):
    if not sym_ids:
        return []

    return batched_in(
        conn,
        "SELECT DISTINCT f.path, s.id, COALESCE(f.file_role, 'source') AS file_role "
        "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
        list(sym_ids),
    )


def _rankable_reading_file(row, seen_files):
    if row["file_role"] in _READING_ORDER_SKIP_ROLES:
        return None
    path = row["path"]
    return None if path in seen_files else path


def _rank_layer_files(file_rows, pagerank_lookup, seen_files):
    file_pr = {}
    for row in file_rows:
        path = _rankable_reading_file(row, seen_files)
        if path is None:
            continue
        pagerank = pagerank_lookup.get(row["id"], 0)
        file_pr[path] = max(file_pr.get(path, 0), pagerank)
    return file_pr


def _reading_entries_for_layer(layer_num, file_pr, seen_files):
    entries = []
    for path in sorted(file_pr, key=file_pr.get, reverse=True)[:5]:
        seen_files.add(path)
        entries.append(
            {
                "layer": layer_num,
                "file": path,
                # W361 — file importance is a PageRank value; 4 decimals
                # truncated to 0 for low-rank files. Match W336's
                # cmd_impact 6-decimal rounding.
                "importance": round(file_pr[path], 6),
            }
        )
    return entries


def _top_symbols(conn, G, limit=10):
    """Return the top-N source-symbol entries by PageRank with role context.

    Pulls 6x the requested limit so the framework-alias filter and the
    test-fixture filter can strip ranks that inflate centrality without
    being meaningful for a newcomer reading the codebase.

    v12.12.5: skip symbols whose file is classified as ``test``.
    pytest fixtures (``cli_runner``, ``indexed_project``,
    ``project_factory``, …) have huge fan-in because every test imports
    them, so they outrank actual source symbols on PageRank. They are
    not what a newcomer should "learn first" — they're test scaffolding,
    not project domain. The framework_alias filter handles Vue/React
    aliases; this layer handles test fixtures.
    """
    rows = _top_symbol_rows(conn, limit)
    candidates = (row for row in rows if _is_top_symbol_candidate(row))
    return [_format_top_symbol(row) for row in list(candidates)[:limit]]


def _reading_order(conn, G):
    """Suggest a reading order based on topological layers (bottom-up).

    v12.12.5: skip files whose ``file_role`` is ``test``. The previous
    output started a newcomer at ``tests/conftest.py`` because pytest
    fixtures sit at the bottom of the topological layer (everything
    depends on them) and rank highly within that layer. A newcomer
    landing on a test fixture file as their *first* read is exactly
    the wrong shape — the tour should orient them in source code.
    """
    layer_map = detect_layers(G)
    order = []
    seen_files = set()
    for layer_num, sym_ids in enumerate(_layers_by_number(layer_map)):
        pagerank_lookup = _layer_pagerank_lookup(conn, sym_ids)
        file_rows = _layer_file_rows(conn, sym_ids)
        file_pr = _rank_layer_files(file_rows, pagerank_lookup, seen_files)
        order.extend(_reading_entries_for_layer(layer_num, file_pr, seen_files))

    return order


def _entry_points(conn):
    """Fetch entry points as starting exploration targets.

    Test-file symbols are excluded: ``test_*`` functions and ``TestX.test_y``
    methods have no callers in the production graph (``in_degree = 0`` by
    construction) which would otherwise inflate the entry-points list with
    test scaffolding instead of newcomer-relevant production code. Mirrors
    the test-file filter applied to ``_top_symbols``.
    """
    rows = conn.execute(
        """SELECT s.name, s.qualified_name, s.kind, f.path, s.line_start
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
           WHERE (gm.in_degree IS NULL OR gm.in_degree = 0)
           AND s.kind IN ('function', 'method', 'class')
           AND s.is_exported = 1
           AND COALESCE(f.file_role, 'source') != 'test'
           ORDER BY gm.pagerank DESC
           LIMIT 15"""
    ).fetchall()
    return [
        {
            "name": r["qualified_name"] or r["name"],
            "kind": abbrev_kind(r["kind"]),
            "location": loc(r["path"], r["line_start"]),
        }
        for r in rows
    ]


def _language_breakdown(conn):
    """Get language distribution."""
    rows = conn.execute(
        "SELECT language, COUNT(*) as cnt FROM files WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC"
    ).fetchall()
    return [{"language": r["language"], "files": r["cnt"]} for r in rows]


def _patterns(conn):
    """Detect high-level patterns from the graph."""
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    # Test file ratio
    test_files = conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE '%test%' OR path LIKE '%spec%'").fetchone()[0]

    # Health score
    health_row = conn.execute(
        "SELECT AVG(health_score) as avg_hs FROM file_stats WHERE health_score IS NOT NULL"
    ).fetchone()
    avg_health = round(health_row["avg_hs"], 1) if health_row and health_row["avg_hs"] else None

    return {
        "files": total_files,
        "symbols": total_symbols,
        "edges": total_edges,
        "test_files": test_files,
        "test_ratio": round(test_files / total_files * 100, 1) if total_files else 0,
        "avg_file_health": avg_health,
    }


def _top_symbol_match_names(top):
    return {symbol["name"] for symbol in top}


def _tour_mermaid_rows(conn):
    return conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "ORDER BY COALESCE(gm.pagerank, 0) DESC "
        "LIMIT 20"
    ).fetchall()


def _row_matches_top_symbol(row, top_names) -> bool:
    qname = row["qualified_name"] or row["name"]
    return qname in top_names or row["name"] in top_names


def _tour_symbol_info(row):
    qname = row["qualified_name"] or row["name"]
    return {
        "name": row["name"],
        "qname": qname,
        "kind": row["kind"],
        "path": row["path"].replace("\\", "/"),
    }


def _tour_id_to_info(rows, top_names):
    return {row["id"]: _tour_symbol_info(row) for row in rows if _row_matches_top_symbol(row, top_names)}


def _tour_node_element(info, role_lookup):
    role = role_lookup.get(info["qname"], role_lookup.get(info["name"], ""))
    label = " | ".join([info["name"], role]) if role else info["name"]
    return mnode(info["name"], label)


def _tour_node_elements(id_to_info, top):
    role_lookup = {symbol["name"]: symbol["role"] for symbol in top}
    return [_tour_node_element(info, role_lookup) for _sid, info in sorted(id_to_info.items())]


def _tour_edge_if_top(src, tgt, top_ids, id_to_info, seen_edges):
    if src not in top_ids or tgt not in top_ids:
        return None

    source_name = id_to_info[src]["name"]
    target_name = id_to_info[tgt]["name"]
    if source_name == target_name:
        return None

    pair = (source_name, target_name)
    if pair in seen_edges:
        return None

    seen_edges.add(pair)
    return medge(source_name, target_name)


def _tour_edge_elements(G, id_to_info):
    top_ids = set(id_to_info)
    seen_edges: set[tuple[str, str]] = set()
    elements = []
    for src, tgt in G.edges:
        edge = _tour_edge_if_top(src, tgt, top_ids, id_to_info, seen_edges)
        if edge:
            elements.append(edge)
    return elements


def _tour_mermaid(conn, G, top, order):
    """Generate a Mermaid top-down diagram for the codebase tour.

    Shows the top symbols as nodes (labeled with name and role) and
    edges between them derived from the symbol graph.  Returns the
    diagram as a string.
    """
    if not top:
        return mdiagram("TD", ['    empty["No symbols indexed"]'])

    top_names = _top_symbol_match_names(top)
    id_to_info = _tour_id_to_info(_tour_mermaid_rows(conn), top_names)
    elements = _tour_node_elements(id_to_info, top)
    elements.extend(_tour_edge_elements(G, id_to_info))
    return mdiagram("TD", elements)


def _in_focus_scope(item, scope) -> bool:
    path = (item.get("file") or "").replace("\\", "/")
    return path.startswith(scope)


def _apply_focus_filter(top, order, entries, focus_path):
    if not focus_path:
        return top, order, entries

    scope = focus_path.replace("\\", "/").rstrip("/") + "/"
    return (
        [item for item in top if _in_focus_scope(item, scope)],
        [item for item in order if _in_focus_scope(item, scope)],
        [item for item in entries if _in_focus_scope(item, scope)],
    )


def _starting_file_info(conn, order, langs):
    if not order:
        language = langs[0]["language"] if langs else "unknown"
        return "?", language

    start_path = order[0]["file"].replace("\\", "/")
    start_file = start_path.rsplit("/", 1)[-1]
    row = conn.execute("SELECT language FROM files WHERE path = ?", (order[0]["file"],)).fetchone()
    language = row["language"] if row and row["language"] else "unknown"
    return start_file, language


def _tour_verdict(conn, langs, stats, order):
    start_file, lang_label = _starting_file_info(conn, order, langs)
    n_layers = len({item["layer"] for item in order}) if order else 0
    return (
        f"tour: {stats['files']} files, {stats['symbols']} symbols, "
        f"{n_layers} layers, start at {start_file} ({lang_label})"
    )


def _tour_summary(verdict, stats, langs, top):
    return {
        "verdict": verdict,
        "files": stats["files"],
        "symbols": stats["symbols"],
        "languages": len(langs),
        "top_symbols": len(top),
    }


def _tour_json_envelope(verdict, token_budget, langs, stats, top, order, entries, mermaid_text=None):
    payload = {
        "languages": langs,
        "statistics": stats,
        "top_symbols": top,
        "reading_order": order,
        "entry_points": entries,
    }
    if mermaid_text is not None:
        payload["mermaid"] = mermaid_text
    # Pass budget as an explicit kwarg (not buried in **payload) so the static
    # budget-coverage survey (test_budget_coverage_survey) detects forwarding;
    # functionally identical — json_envelope consumes `budget` either way.
    return json_envelope("tour", summary=_tour_summary(verdict, stats, langs, top), budget=token_budget, **payload)


def _overview_lines(langs, stats):
    lang_str = ", ".join(f"{item['language']} ({item['files']})" for item in langs[:5])
    lines = [
        "## Overview\n",
        f"**Languages:** {lang_str}",
        f"**Size:** {stats['files']} files, {stats['symbols']} symbols, {stats['edges']} dependency edges",
        f"**Tests:** {stats['test_files']} test files ({stats['test_ratio']}% of codebase)",
    ]
    if stats["avg_file_health"]:
        lines.append(f"**Avg file health:** {stats['avg_file_health']}/10")
    lines.append("")
    return lines


def _key_symbol_lines(top):
    lines = ["## Key Symbols (learn these first)\n"]
    for symbol in top:
        head = f"  {symbol['kind']}  {symbol['name']:<32}  {symbol['location']}"
        summary = symbol.get("summary") or ""
        lines.append(head)
        if summary:
            lines.append(f"      {summary}")
    lines.append("")
    return lines


def _layer_label(layer_num):
    if layer_num == 0:
        return "foundation"
    return f"builds on layer {layer_num - 1}"


def _reading_order_lines(order):
    if not order:
        return []

    lines = ["## Suggested Reading Order\n", "Start from the foundation (layer 0) and work upward:\n"]
    current_layer = -1
    for item in order:
        if item["layer"] != current_layer:
            current_layer = item["layer"]
            lines.append(f"\n**Layer {current_layer}** ({_layer_label(current_layer)}):")
        lines.append(f"  - {item['file']}")
    lines.append("")
    return lines


def _entry_point_lines(entries):
    if not entries:
        return []

    lines = ["## Entry Points (start exploring here)\n"]
    lines.extend(f"  - `{entry['name']}` ({entry['kind']}) at {entry['location']}" for entry in entries)
    lines.append("")
    return lines


def _next_step_lines():
    return [
        "## Next Steps\n",
        "- `roam search <pattern>` — find any symbol by name",
        "- `roam context <symbol>` — get files and line ranges to read",
        "- `roam why <symbol>` — understand why a symbol matters",
        "- `roam preflight <symbol>` — safety check before modifying",
        "",
    ]


def _render_tour_markdown(verdict, langs, stats, top, order, entries):
    lines = [f"VERDICT: {verdict}\n", "# Codebase Tour\n"]
    lines.extend(_overview_lines(langs, stats))
    lines.extend(_key_symbol_lines(top))
    lines.extend(_reading_order_lines(order))
    lines.extend(_entry_point_lines(entries))
    lines.extend(_next_step_lines())
    return "\n".join(lines)


def _write_or_echo_tour(output, write_file):
    if write_file:
        with open(write_file, "w", encoding="utf-8") as file:
            file.write(output)
        click.echo(f"Tour written to {write_file}")
        return
    click.echo(output)


@roam_capability(
    name="tour",
    category="exploration",
    summary="Generate a guided reading-order tour: top symbols, entry points, layers.",
    inputs=["repo_path"],
    outputs=["top_symbols", "reading_order", "entry_points", "verdict"],
    examples=["roam tour", "roam tour --write TOUR.md", "roam tour --mermaid"],
    tags=["onboarding", "exploration"],
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
@click.command(name="tour")
@click.option(
    "--write",
    "write_file",
    default=None,
    type=click.Path(),
    help="Write the tour to a Markdown file instead of stdout",
)
@click.option("--mermaid", "mermaid_mode", is_flag=True, help="Output Mermaid diagram")
@click.option(
    "--focus",
    "focus_path",
    type=str,
    default=None,
    help="limit tour items (top symbols, reading order, entries) to files under this path prefix.",
)
@click.pass_context
def tour_command(ctx, write_file, mermaid_mode, focus_path):
    """Generate a codebase onboarding tour.

    Produces a structured guide: project overview, top symbols to learn,
    suggested reading order, entry points, and codebase statistics.
    Always current because it is derived from the index.

    Unlike ``understand --tour`` (which appends tour data to the full
    overview), this command provides a standalone guided reading order with
    ``--write FILE`` for persisting onboarding documentation.

    \b
    Examples:
      roam tour
      roam tour --write ONBOARDING.md
      roam tour --mermaid
      roam tour --focus src/auth

    See also ``understand`` (full project overview), ``minimap``
    (compact codebase map), and ``describe`` (per-symbol explainer).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)

        langs = _language_breakdown(conn)
        stats = _patterns(conn)
        top = _top_symbols(conn, G, limit=10)
        order = _reading_order(conn, G)
        entries = _entry_points(conn)

        top, order, entries = _apply_focus_filter(top, order, entries, focus_path)
        verdict = _tour_verdict(conn, langs, stats, order)

        if mermaid_mode:
            mermaid_text = _tour_mermaid(conn, G, top, order)
            if json_mode:
                envelope = _tour_json_envelope(verdict, token_budget, langs, stats, top, order, entries, mermaid_text)
                click.echo(to_json(envelope))
            else:
                click.echo(mermaid_text)
            return

        if json_mode:
            envelope = _tour_json_envelope(verdict, token_budget, langs, stats, top, order, entries)
            click.echo(to_json(envelope))
            return

        output = _render_tour_markdown(verdict, langs, stats, top, order, entries)
        _write_or_echo_tour(output, write_file)


# Keep the existing module attribute for lazy CLI loading and direct test imports.
tour = tour_command
