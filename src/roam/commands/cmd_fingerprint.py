"""Graph-Isomorphism Transfer: topology fingerprint for cross-repo comparison."""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, to_json

# Hard refusal threshold — beyond this, the spectral analysis (Fiedler vector
# via algebraic_connectivity) runs O(n³) without a sparse-eigensolver hookup
# and will exhaust memory or stall. Below this, we just warn.
# Empirical: 13.8k symbols completes in ~11s; 50k symbols in ~5 min on a
# stock laptop. The hard cap is the boundary above which "index a
# subdirectory" really is the right answer.
_HARD_CAP_SYMBOLS = 100_000
# Soft warn threshold — above this we tell the user it'll take a moment but
# we still run the analysis. Pre-v12 this was the hard refusal threshold.
_WARN_THRESHOLD_SYMBOLS = 20_000


def _format_pct_list(pcts: list[float]) -> str:
    """Format a list of percentages into a compact distribution string."""
    return " / ".join(f"{p:.0f}%" for p in pcts)


@click.command()
@click.option("--compact", is_flag=True, help="Single-line summary output")
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    default=None,
    help="Write fingerprint JSON to file",
)
@click.option(
    "--compare",
    "compare_path",
    type=click.Path(exists=True),
    default=None,
    help="Compare with a saved fingerprint JSON file",
)
@click.pass_context
def fingerprint(ctx, compact, export_path, compare_path):
    """Topology fingerprint for cross-repo comparison.

    Unlike ``capsule`` (which exports the raw graph as portable JSON),
    this command extracts a computed topology signature for cross-repo
    comparison.

    Extracts a structural signature from the codebase graph: layers,
    modularity, connectivity, clusters, hub/bridge ratio, PageRank
    distribution, and anti-patterns.

    Use --export to save and --compare to diff against another repo.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # ``--compact`` is also a top-level global flag (``LazyGroup._GLOBAL_FLAGS``).
    # When invoked as ``roam fingerprint --compact`` the parser moves the
    # flag to the group context, leaving this command's local ``compact``
    # parameter False. Honour the global value too. v12.12.
    if not compact and ctx.obj:
        compact = bool(ctx.obj.get("compact"))
    ensure_index()

    with open_db(readonly=True) as conn:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compare_fingerprints, compute_fingerprint

        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if sym_count > _HARD_CAP_SYMBOLS:
            msg = (
                f"Graph too large ({sym_count} symbols, hard cap {_HARD_CAP_SYMBOLS:,}) "
                "for fingerprint analysis. Index a subdirectory to reduce graph size, "
                "or override `_HARD_CAP_SYMBOLS` in cmd_fingerprint.py."
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "fingerprint",
                            summary={
                                "verdict": msg,
                                "symbol_count": sym_count,
                                "hard_cap": _HARD_CAP_SYMBOLS,
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {msg}")
            return
        if sym_count > _WARN_THRESHOLD_SYMBOLS and not json_mode:
            click.echo(
                f"  Note: {sym_count:,} symbols — spectral analysis may take a minute. (threshold {{:,}})".format(
                    _WARN_THRESHOLD_SYMBOLS
                ),
                err=True,
            )

        G = build_symbol_graph(conn)
        fp = compute_fingerprint(conn, G)

        topo = fp["topology"]
        n_layers = topo["layers"]
        modularity = topo["modularity"]
        fiedler = topo["fiedler"]
        tangle = topo["tangle_ratio"]

        verdict = f"{n_layers} layers, modularity {modularity:.2f}, fiedler {fiedler:.3f}, tangle {int(tangle * 100)}%"

        # -- Export --
        if export_path:
            Path(export_path).write_text(_json.dumps(fp, indent=2, default=str), encoding="utf-8")
            if not json_mode and not compact:
                click.echo(f"Fingerprint written to {export_path}")

        # -- Compare --
        comparison = None
        if compare_path:
            other_fp = _json.loads(Path(compare_path).read_text(encoding="utf-8"))
            comparison = compare_fingerprints(fp, other_fp)

        # -- JSON output --
        if json_mode:
            envelope = json_envelope(
                "fingerprint",
                summary={
                    "verdict": verdict,
                    "layers": n_layers,
                    "modularity": modularity,
                    "fiedler": fiedler,
                    "tangle_ratio": tangle,
                },
                fingerprint=fp,
            )
            if comparison:
                envelope["comparison"] = comparison
                envelope["summary"]["similarity_score"] = comparison["similarity"]
            click.echo(to_json(envelope))
            return

        # -- Compact output --
        if compact:
            sim_str = ""
            if comparison:
                sim_str = f"  similarity={comparison['similarity']:.0%}"
            click.echo(
                f"fingerprint  layers={n_layers}  mod={modularity:.3f}  "
                f"fiedler={fiedler:.4f}  tangle={tangle:.2f}  "
                f"gini={fp['pagerank_gini']:.2f}  "
                f"hubs={fp['hub_bridge_ratio']:.2f}"
                f"{sim_str}"
            )
            return

        # -- Full text output --
        click.echo(f"VERDICT: {verdict}")

        # Topology section
        click.echo("\nTOPOLOGY:")
        dist_str = _format_pct_list(topo["layer_distribution"]) if topo["layer_distribution"] else "n/a"
        click.echo(f"  Layers: {n_layers} (distribution: {dist_str})")
        click.echo(f"  Fiedler: {fiedler:.4f}")
        click.echo(f"  Modularity: {modularity:.3f}")
        click.echo(f"  Tangle ratio: {tangle:.2f}")
        click.echo(f"  Dependency direction: {fp['dependency_direction']}")

        # Clusters section (top 5)
        clusters = fp.get("clusters", [])
        if clusters:
            click.echo(f"\nCLUSTERS (top {min(5, len(clusters))}):")
            table_rows = []
            for c in clusters[:5]:
                table_rows.append(
                    [
                        c["label"],
                        f"{c['size_pct']:.0f}%",
                        f"{c['conductance']:.2f}",
                        str(c["layer"]),
                        c["pattern"],
                    ]
                )
            click.echo(
                format_table(
                    ["Label", "Size", "Conductance", "Layer", "Pattern"],
                    table_rows,
                )
            )

        # Signature section
        click.echo("\nSIGNATURE:")
        click.echo(f"  Hub/bridge ratio: {fp['hub_bridge_ratio']:.2f}")
        click.echo(f"  PageRank Gini: {fp['pagerank_gini']:.2f}")
        click.echo(f"  God objects: {fp['antipatterns']['god_objects']}")
        click.echo(f"  Cyclic clusters: {fp['antipatterns']['cyclic_clusters']}")

        # Comparison section
        if comparison:
            sim = comparison["similarity"]
            dist = comparison["euclidean_distance"]
            click.echo(f"\nVERDICT: {sim:.0%} similar (topology distance: {dist:.2f})")
            click.echo("\nCOMPARISON:")
            cmp_rows = []
            for name, m in comparison["per_metric"].items():
                delta_str = f"{m['delta']:+.4f}" if isinstance(m["delta"], float) else f"{m['delta']:+d}"
                cmp_rows.append(
                    [
                        name,
                        str(round(m["this"], 4)),
                        str(round(m["other"], 4)),
                        delta_str,
                    ]
                )
            click.echo(
                format_table(
                    ["Metric", "This repo", "Other repo", "Delta"],
                    cmp_rows,
                )
            )
