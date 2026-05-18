"""``roam graph-export`` — write the symbol graph as GraphML / DOT / JSONL.

handy for plugging the in-memory NetworkX graph into external
tooling (Gephi, Cytoscape, igraph, custom analyses). Stays read-only;
no network egress.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because graph-export outputs are graph-format exports
(GraphML/DOT/JSONL) — not per-location violations. SARIF is reserved
for findings with file:line coordinates; graph-export's primary
deliverable is the graph-format export file. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.graph.builder import build_file_graph, build_symbol_graph
from roam.output.formatter import json_envelope, to_json


def _serialise_jsonl(G, output_path: Path) -> int:
    """Write graph as one JSON object per line (node | edge).

    Buffered in-memory then written via :func:`atomic_write_text` so a
    failure mid-serialise (disk full, ctrl-c) cannot leave behind a
    half-truncated JSONL file that downstream tooling would mis-parse.
    """
    from roam.atomic_io import atomic_write_text

    lines: list[str] = []
    count = 0
    for node, data in G.nodes(data=True):
        payload = {"type": "node", "id": str(node), **{k: data[k] for k in data}}
        lines.append(json.dumps(payload, default=str))
        count += 1
    for src, tgt, data in G.edges(data=True):
        payload = {"type": "edge", "src": str(src), "tgt": str(tgt), **{k: data[k] for k in data}}
        lines.append(json.dumps(payload, default=str))
        count += 1
    atomic_write_text(output_path, "\n".join(lines) + ("\n" if lines else ""))
    return count


def _serialise_dot(G, output_path: Path) -> int:
    """Write graph as a Graphviz DOT file. Pure stdlib — no pydot.

    Buffered in-memory then atomically replaced — same rationale as
    :func:`_serialise_jsonl`.
    """
    from roam.atomic_io import atomic_write_text

    parts: list[str] = ["digraph G {\n"]
    count = 0
    for node, data in G.nodes(data=True):
        label = str(data.get("name") or data.get("path") or node).replace('"', "'")
        parts.append(f'  "{node}" [label="{label}"];\n')
        count += 1
    for src, tgt in G.edges():
        parts.append(f'  "{src}" -> "{tgt}";\n')
        count += 1
    parts.append("}\n")
    atomic_write_text(output_path, "".join(parts))
    return count


def _serialise_graphml(G, output_path: Path) -> int:
    """Write graph as GraphML via NetworkX.

    NetworkX writes directly to the destination; for atomicity we route
    through a temp path in the same directory and ``os.replace`` so the
    eventual rename is intra-filesystem and atomic on POSIX + Windows.
    """
    import os
    import tempfile

    import networkx as nx

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        os.close(fd)
        nx.write_graphml(G, tmp_name)
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return G.number_of_nodes() + G.number_of_edges()


@roam_capability(
    name="graph-export",
    category="architecture",
    summary="Export the indexed graph for external tooling",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="graph-export")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["graphml", "dot", "jsonl"], case_sensitive=False),
    default="jsonl",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--scope",
    type=click.Choice(["symbol", "file"], case_sensitive=False),
    default="symbol",
    show_default=True,
    help="Symbol-level (default) or file-level dependency graph.",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Write to this path (default: ./roam-graph.<format>).",
)
@click.pass_context
def graph_export(ctx, fmt, scope, output_path) -> None:
    """Export the indexed graph for external tooling."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    fmt = fmt.lower()
    target = Path(output_path) if output_path else Path(f"roam-graph.{fmt}")

    # W607-DO -- substrate-CALL-layer plumbing for cmd_graph_export.
    # cmd_graph_export is the graph-FORMAT companion to cmd_fingerprint
    # (topology-HASH, W607-DH) and cmd_capsule (graph-BUNDLE,
    # W607-BD/W607-DK); together they close the architecture-export 3-way
    # at the substrate-CALL layer:
    #
    #   * cmd_fingerprint  -> W607-DH (topology-HASH, 11 phases)
    #   * cmd_capsule      -> W607-DK on top of W607-BD (graph-BUNDLE)
    #   * cmd_graph_export -> W607-DO (THIS WAVE; multi-format graph export)
    #
    # cmd_graph_export has NO pre-existing warnings_out channel -- W607-DO
    # is FRESH: the accumulator-based markers become the canonical
    # ``summary.warnings_out`` field outright.
    #
    # Substrates wrapped via ``_run_check_do``:
    #
    #   * build_graph             -- networkx graph construction from DB
    #                                (build_file_graph or build_symbol_graph
    #                                depending on --scope).
    #   * serialize_jsonl         -- JSONL projection + W82.1 atomic
    #                                file-write (when fmt=jsonl).
    #   * serialize_dot           -- DOT projection + W82.1 atomic
    #                                file-write (when fmt=dot).
    #   * serialize_graphml       -- GraphML projection + W82.1 atomic
    #                                file-write via tempfile + os.replace
    #                                (when fmt=graphml).
    #   * compute_export_metadata -- nodes/edges counts + LAW 6 verdict
    #                                composition.
    #   * serialize_envelope      -- json_envelope + to_json composition.
    #
    # Marker family ``graph_export_<phase>_failed:<exc_class>:<detail>``.
    # Non-empty bucket flips ``partial_success: True`` so the Pattern-2
    # silent-fallback guard holds on degraded paths.
    _w607do_warnings_out: list[str] = []

    def _run_check_do(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-DO marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``graph_export_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607do_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607do_warnings_out.append(f"graph_export_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DO: ``build_graph`` substrate -- a raise inside the networkx
    # construction degrades to an empty-graph floor rather than crashing
    # the exporter wholesale.
    def _call_build_graph():

        with open_db(readonly=True) as conn:
            if scope.lower() == "file":
                return build_file_graph(conn)
            return build_symbol_graph(conn)

    def _empty_graph():
        import networkx as nx

        return nx.DiGraph()

    G = _run_check_do("build_graph", _call_build_graph, default=None)
    if G is None:
        G = _run_check_do("build_graph_fallback", _empty_graph, default=None)
    if G is None:
        # Last-ditch literal floor: an empty DiGraph constructed directly
        # so downstream substrates have a non-None object to inspect.
        import networkx as nx

        G = nx.DiGraph()

    # W607-DO: multi-format dispatch substrate -- each ``serialize_<fmt>``
    # boundary is wrapped INDEPENDENTLY so a raise in one projection
    # (e.g., GraphML XML-escape on a hostile node attr) degrades to a
    # records=0 floor without affecting the verdict / envelope path.
    # Only ONE of the three substrates actually runs per invocation
    # (dispatched by fmt); the W978 4th-discipline phase-name collision
    # guard accepts this because each phase string is unique.
    if fmt == "jsonl":
        records = _run_check_do("serialize_jsonl", _serialise_jsonl, G, target, default=0)
    elif fmt == "dot":
        records = _run_check_do("serialize_dot", _serialise_dot, G, target, default=0)
    else:
        records = _run_check_do("serialize_graphml", _serialise_graphml, G, target, default=0)
    if records is None:
        records = 0

    # W607-DO: ``compute_export_metadata`` substrate -- nodes/edges
    # counts + LAW 6 single-line verdict composition. The closure
    # embeds every G.number_of_* lookup INSIDE the wrapped function
    # (W978 5th discipline: never index a possibly-poisoned graph at
    # the kwarg-bind site). A raise (AttributeError on a corrupted G)
    # degrades to the explicit no-data floor so the envelope still
    # emits a non-empty verdict.
    def _compute_export_metadata():
        nodes_local = G.number_of_nodes()
        edges_local = G.number_of_edges()
        verdict_local = f"{fmt.upper()} export: {nodes_local} nodes, {edges_local} edges -> {target}"
        return (nodes_local, edges_local, verdict_local)

    metadata_bundle = _run_check_do(
        "compute_export_metadata",
        _compute_export_metadata,
        default=(0, 0, "graph-export degraded (no metadata)"),
    )
    if metadata_bundle is None:
        metadata_bundle = (0, 0, "graph-export degraded (no metadata)")
    nodes, edges, verdict = metadata_bundle

    if json_mode:
        # W607-DO: stamp substrate markers onto BOTH ``summary.warnings_out``
        # and the top-level ``warnings_out``. Non-empty bucket flips
        # partial_success so degraded paths cannot be mistaken for clean
        # export (Pattern-2 silent-fallback guard).
        def _build_envelope_do():
            _summary: dict = {
                "verdict": verdict,
                "nodes": nodes,
                "edges": edges,
                "format": fmt,
                "scope": scope,
                "records_written": records,
            }
            if _w607do_warnings_out:
                _summary["partial_success"] = True
                _summary["warnings_out"] = list(_w607do_warnings_out)
            _kwargs: dict = {"output_path": str(target)}
            if _w607do_warnings_out:
                _kwargs["warnings_out"] = list(_w607do_warnings_out)
            return json_envelope(
                "graph-export",
                summary=_summary,
                **_kwargs,
            )

        # W607-DO: wrap the json_envelope composition itself so a
        # circular-ref / hostile field surfaces a marker rather than
        # crashing before to_json runs.
        _envelope = _run_check_do(
            "serialize_envelope",
            _build_envelope_do,
            default=None,
        )
        if _envelope is None:
            # Floor envelope -- the W607-DO wrap surfaced a marker but
            # we still owe a structurally valid JSON envelope to the
            # caller. Pattern-2 silent-fallback discipline: name the
            # concrete state, not SAFE/completed.
            _envelope = json_envelope(
                "graph-export",
                summary={
                    "verdict": "graph-export envelope serialization failed",
                    "nodes": nodes,
                    "edges": edges,
                    "format": fmt,
                    "scope": scope,
                    "records_written": records,
                    "partial_success": True,
                    "state": "envelope_serialize_failed",
                    "warnings_out": list(_w607do_warnings_out),
                },
                output_path=str(target),
                warnings_out=list(_w607do_warnings_out),
            )
        click.echo(to_json(_envelope))
        return
    click.echo(f"VERDICT: {verdict}")
