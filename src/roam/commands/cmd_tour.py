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
    from roam.output.framework_filter import is_framework_alias

    rows = conn.execute(
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
    filtered = []
    # Generic property names that suffer name-collision in the symbol
    # resolver (every ``obj.path`` reference resolves to the first
    # class with a ``path`` attribute, inflating that one symbol's
    # in-degree to dozens or hundreds). Skipping these prevents one
    # WebhookBridge.path-style false positive from dominating the
    # "Key Symbols" list.
    _GENERIC_PROP_NAMES = {
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
    for r in rows:
        if r["file_role"] == "test":
            continue
        if is_framework_alias(r["qualified_name"] or r["name"], r["kind"], r["path"]):
            continue
        # Demote/skip generic-named properties / fields — almost
        # always name collisions inflated by every ``.path`` /
        # ``.name`` reference in unrelated code. The kind in the DB
        # is the unabbreviated form (``property``, ``field``,
        # ``attribute``); the table column displays the abbreviation.
        if r["kind"] in {"property", "field", "attribute"} and r["name"].lower() in _GENERIC_PROP_NAMES:
            continue
        filtered.append(r)
    rows = filtered[:limit]
    results = []
    for r in rows:
        in_d = r["in_degree"] or 0
        out_d = r["out_degree"] or 0
        if in_d >= 5 and out_d >= 5:
            role = "Hub"
        elif in_d >= 5:
            role = "Core utility"
        elif out_d >= 5:
            role = "Orchestrator"
        elif in_d < 2 and out_d < 2:
            role = "Leaf"
        else:
            role = "Internal"
        # 12.13 — surface a docstring excerpt for newcomers. Pure
        # PageRank ranks plumbing functions (open_db, json_envelope)
        # at the top because every command imports them. The
        # docstring excerpt keeps that ranking but tells the reader
        # *what* each top symbol does, so utility-heavy lists still
        # carry orientation signal.
        doc = r["docstring"] or ""
        first_sentence = doc.split("\n\n", 1)[0].strip()
        first_line = " ".join(first_sentence.split())[:60]
        results.append(
            {
                "name": r["qualified_name"] or r["name"],
                "kind": abbrev_kind(r["kind"]),
                "role": role,
                "fan_in": in_d,
                "fan_out": out_d,
                # W361 — match W336's 6-decimal rounding for cmd_impact.
                # Tour symbols on a 25k-symbol graph have per-symbol
                # PageRank in the 1e-5 range; 4 decimals truncated to 0.
                "pagerank": round(r["pagerank"] or 0, 6),
                "location": loc(r["path"], r["line_start"]),
                "summary": first_line,
            }
        )
    return results


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
    if not layer_map:
        return []

    # Convert {node_id: layer_num} -> list of sets indexed by layer
    max_layer = max(layer_map.values()) if layer_map else 0
    layers_list = [set() for _ in range(max_layer + 1)]
    for node_id, layer_num in layer_map.items():
        layers_list[layer_num].add(node_id)

    # Collect file paths per layer, ordered by PageRank within each layer
    order = []
    seen_files = set()
    for layer_num, sym_ids in enumerate(layers_list):
        pr_rows = (
            batched_in(
                conn,
                "SELECT gm.symbol_id, gm.pagerank FROM graph_metrics gm "
                "WHERE gm.symbol_id IN ({ph}) ORDER BY gm.pagerank DESC",
                list(sym_ids),
            )
            if sym_ids
            else []
        )

        pr_lookup = {r["symbol_id"]: r["pagerank"] or 0 for r in pr_rows}

        # Get file paths for this layer's symbols, with their file_role
        # so we can skip test fixtures.
        if not sym_ids:
            continue
        file_rows = batched_in(
            conn,
            "SELECT DISTINCT f.path, s.id, COALESCE(f.file_role, 'source') AS file_role "
            "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
            list(sym_ids),
        )

        # Rank files by max PageRank of their symbols in this layer.
        # Exclude non-source roles — tests, dev scripts, generated code,
        # config metadata, and benchmark/example tooling are noise for
        # "where do I start reading this codebase". The reading order
        # should orient a newcomer in domain code; if they need config
        # they'll find pyproject.toml on their own.
        _SKIP_ROLES = {
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
        file_pr = {}
        for r in file_rows:
            if r["file_role"] in _SKIP_ROLES:
                continue
            fp = r["path"]
            if fp not in seen_files:
                pr_val = pr_lookup.get(r["id"], 0)
                file_pr[fp] = max(file_pr.get(fp, 0), pr_val)

        for fp in sorted(file_pr, key=file_pr.get, reverse=True)[:5]:
            seen_files.add(fp)
            order.append(
                {
                    "layer": layer_num,
                    "file": fp,
                    # W361 — file importance is a PageRank value; 4
                    # decimals truncated to 0 for low-rank files. Match
                    # W336's cmd_impact 6-decimal rounding.
                    "importance": round(file_pr[fp], 6),
                }
            )

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


def _tour_mermaid(conn, G, top, order):
    """Generate a Mermaid top-down diagram for the codebase tour.

    Shows the top symbols as nodes (labeled with name and role) and
    edges between them derived from the symbol graph.  Returns the
    diagram as a string.
    """
    if not top:
        return mdiagram("TD", ['    empty["No symbols indexed"]'])

    elements: list[str] = []

    # Collect symbol IDs for the top symbols so we can find edges
    top_names = {s["name"] for s in top}
    # Map qualified name -> symbol row for edge lookup
    top_rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "ORDER BY COALESCE(gm.pagerank, 0) DESC "
        "LIMIT 20"
    ).fetchall()

    id_to_info: dict[int, dict] = {}
    for r in top_rows:
        qname = r["qualified_name"] or r["name"]
        if qname in top_names or r["name"] in top_names:
            id_to_info[r["id"]] = {
                "name": r["name"],
                "qname": qname,
                "kind": r["kind"],
                "path": r["path"].replace("\\", "/"),
            }

    top_ids = set(id_to_info.keys())

    # Create nodes
    role_lookup = {s["name"]: s["role"] for s in top}
    for sid, info in sorted(id_to_info.items()):
        role = role_lookup.get(info["qname"], role_lookup.get(info["name"], ""))
        label_parts = [info["name"]]
        if role:
            label_parts.append(role)
        elements.append(mnode(info["name"], " | ".join(label_parts)))

    # Create edges among top symbols
    seen_edges: set[tuple[str, str]] = set()
    for src, tgt in G.edges:
        if src in top_ids and tgt in top_ids:
            s_name = id_to_info[src]["name"]
            t_name = id_to_info[tgt]["name"]
            if s_name != t_name:
                pair = (s_name, t_name)
                if pair not in seen_edges:
                    seen_edges.add(pair)
                    elements.append(medge(s_name, t_name))

    return mdiagram("TD", elements)


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
@click.command()
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
def tour(ctx, write_file, mermaid_mode, focus_path):
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

        # focus filter. Apply after the heavy queries so we
        # benefit from the cache / index, then drop anything outside
        # the prefix. Normalise slashes to keep Windows happy.
        if focus_path:
            scope = focus_path.replace("\\", "/").rstrip("/") + "/"

            def _in_scope(item) -> bool:
                p = (item.get("file") or "").replace("\\", "/")
                return p.startswith(scope)

            top = [t for t in top if _in_scope(t)]
            order = [o for o in order if _in_scope(o)]
            entries = [e for e in entries if _in_scope(e)]

        # Build verdict — look up the language of the actual starting
        # file rather than the project's dominant language (otherwise a
        # codebase that's mostly YAML rules but starts at a Python file
        # gets labelled "(yaml)").
        n_layers = len({item["layer"] for item in order}) if order else 0
        if order:
            start_path = order[0]["file"].replace("\\", "/")
            start_file = start_path.rsplit("/", 1)[-1]
            row = conn.execute("SELECT language FROM files WHERE path = ?", (order[0]["file"],)).fetchone()
            lang_label = row["language"] if row and row["language"] else "unknown"
        else:
            start_file = "?"
            lang_label = langs[0]["language"] if langs else "unknown"
        verdict = (
            f"tour: {stats['files']} files, {stats['symbols']} symbols, "
            f"{n_layers} layers, start at {start_file} ({lang_label})"
        )

        if mermaid_mode:
            mermaid_text = _tour_mermaid(conn, G, top, order)
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "tour",
                            summary={
                                "verdict": verdict,
                                "files": stats["files"],
                                "symbols": stats["symbols"],
                                "languages": len(langs),
                                "top_symbols": len(top),
                            },
                            budget=token_budget,
                            languages=langs,
                            statistics=stats,
                            top_symbols=top,
                            reading_order=order,
                            entry_points=entries,
                            mermaid=mermaid_text,
                        )
                    )
                )
            else:
                click.echo(mermaid_text)
            return

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "tour",
                        summary={
                            "verdict": verdict,
                            "files": stats["files"],
                            "symbols": stats["symbols"],
                            "languages": len(langs),
                            "top_symbols": len(top),
                        },
                        budget=token_budget,
                        languages=langs,
                        statistics=stats,
                        top_symbols=top,
                        reading_order=order,
                        entry_points=entries,
                    )
                )
            )
            return

        lines = []
        lines.append(f"VERDICT: {verdict}\n")
        lines.append("# Codebase Tour\n")

        # Overview
        lines.append("## Overview\n")
        lang_str = ", ".join(f"{l['language']} ({l['files']})" for l in langs[:5])
        lines.append(f"**Languages:** {lang_str}")
        lines.append(f"**Size:** {stats['files']} files, {stats['symbols']} symbols, {stats['edges']} dependency edges")
        lines.append(f"**Tests:** {stats['test_files']} test files ({stats['test_ratio']}% of codebase)")
        if stats["avg_file_health"]:
            lines.append(f"**Avg file health:** {stats['avg_file_health']}/10")
        lines.append("")

        # Top symbols. 12.13 — append a one-line docstring summary
        # for each. Pure-PageRank ranking surfaces plumbing functions
        # (open_db, json_envelope) at the top; the summary tells the
        # newcomer what each does so the list still carries
        # orientation signal even when it's utility-heavy.
        lines.append("## Key Symbols (learn these first)\n")
        for s in top:
            head = f"  {s['kind']}  {s['name']:<32}  {s['location']}"
            summary = s.get("summary") or ""
            if summary:
                lines.append(head)
                lines.append(f"      {summary}")
            else:
                lines.append(head)
        lines.append("")

        # Reading order
        if order:
            lines.append("## Suggested Reading Order\n")
            lines.append("Start from the foundation (layer 0) and work upward:\n")
            current_layer = -1
            for item in order:
                if item["layer"] != current_layer:
                    current_layer = item["layer"]
                    lines.append(
                        f"\n**Layer {current_layer}** ({'foundation' if current_layer == 0 else 'builds on layer ' + str(current_layer - 1)}):"
                    )
                lines.append(f"  - {item['file']}")
            lines.append("")

        # Entry points
        if entries:
            lines.append("## Entry Points (start exploring here)\n")
            for e in entries:
                lines.append(f"  - `{e['name']}` ({e['kind']}) at {e['location']}")
            lines.append("")

        # Tips
        lines.append("## Next Steps\n")
        lines.append("- `roam search <pattern>` — find any symbol by name")
        lines.append("- `roam context <symbol>` — get files and line ranges to read")
        lines.append("- `roam why <symbol>` — understand why a symbol matters")
        lines.append("- `roam preflight <symbol>` — safety check before modifying")
        lines.append("")

        output = "\n".join(lines)

        if write_file:
            with open(write_file, "w", encoding="utf-8") as f:
                f.write(output)
            click.echo(f"Tour written to {write_file}")
        else:
            click.echo(output)
