"""Export the structural graph as a JSON capsule (no function bodies)."""

from __future__ import annotations

import hashlib
import json as _json
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Path redaction helper
# ---------------------------------------------------------------------------

def _redact_path(path: str) -> str:
    """Hash each path component to anonymize file paths.

    The same path always maps to the same redacted name so graph edges
    remain consistent within a single capsule.
    """
    parts = path.replace("\\", "/").split("/")
    return "/".join(hashlib.sha256(p.encode()).hexdigest()[:6] for p in parts)


# ---------------------------------------------------------------------------
# Data-gathering helpers
# ---------------------------------------------------------------------------

def _gather_topology(conn) -> dict:
    """Return counts and language list for the topology section."""
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    lang_rows = conn.execute(
        "SELECT DISTINCT language FROM files WHERE language IS NOT NULL ORDER BY language"
    ).fetchall()
    languages = [r[0] for r in lang_rows if r[0]]

    return {
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "languages": languages,
    }


def _gather_symbols(conn, redact_paths: bool, no_signatures: bool) -> list[dict]:
    """Return symbol list with optional path redaction and signature omission."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path, s.line_start, "
        "s.signature, s.visibility, s.is_exported "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY f.path, s.line_start"
    ).fetchall()

    # Build fan-in / fan-out lookup in one pass each to avoid N+1 queries
    fan_in_rows = conn.execute(
        "SELECT target_id, COUNT(*) as cnt FROM edges GROUP BY target_id"
    ).fetchall()
    fan_in = {r[0]: r[1] for r in fan_in_rows}

    fan_out_rows = conn.execute(
        "SELECT source_id, COUNT(*) as cnt FROM edges GROUP BY source_id"
    ).fetchall()
    fan_out = {r[0]: r[1] for r in fan_out_rows}

    # Build complexity lookup
    metric_rows = conn.execute(
        "SELECT symbol_id, cognitive_complexity, halstead_volume FROM symbol_metrics"
    ).fetchall()
    metrics_map = {r[0]: r for r in metric_rows}

    result = []
    for r in rows:
        sid = r[0]  # r["id"] — use positional access since column names may vary

        file_path = r[4]  # f.path
        if redact_paths:
            file_path = _redact_path(file_path)

        # Signature
        sig = r[6]  # s.signature
        if no_signatures:
            sig = None

        # Metrics
        m = metrics_map.get(sid)
        metrics_dict: dict = {
            "cognitive_complexity": (m[1] if m else None),
            "fan_in": fan_in.get(sid, 0),
            "fan_out": fan_out.get(sid, 0),
        }
        if m and m[2] is not None:
            metrics_dict["halstead_volume"] = m[2]

        entry: dict = {
            "id": sid,
            "name": r[1],          # s.name
            "kind": r[3],          # s.kind
            "file": file_path,
            "line": r[5],          # s.line_start
            "metrics": metrics_dict,
        }
        if sig is not None:
            entry["signature"] = sig

        result.append(entry)

    return result


def _gather_edges(conn) -> list[dict]:
    """Return all symbol-level edges."""
    rows = conn.execute(
        "SELECT source_id, target_id, kind FROM edges ORDER BY source_id"
    ).fetchall()
    return [{"source": r[0], "target": r[1], "kind": r[2]} for r in rows]


def _gather_clusters(conn) -> list[dict]:
    """Return clusters with id, label and member count."""
    rows = conn.execute(
        "SELECT cluster_id, cluster_label, COUNT(*) as size "
        "FROM clusters GROUP BY cluster_id, cluster_label "
        "ORDER BY cluster_id"
    ).fetchall()
    return [{"id": r[0], "label": r[1], "size": r[2]} for r in rows]


def _gather_health(conn) -> dict:
    """Collect health metrics via metrics_history.collect_metrics."""
    from roam.commands.metrics_history import collect_metrics
    m = collect_metrics(conn)
    return {
        "score": m.get("health_score", 0),
        "cycles": m.get("cycles", 0),
        "god_components": m.get("god_components", 0),
        "layer_violations": m.get("layer_violations", 0),
        "bottlenecks": m.get("bottlenecks", 0),
        "dead_exports": m.get("dead_exports", 0),
        "tangle_ratio": m.get("tangle_ratio", 0.0),
        "avg_complexity": m.get("avg_complexity", 0.0),
    }


# ---------------------------------------------------------------------------
# Capsule builder
# ---------------------------------------------------------------------------

def _build_capsule(conn, redact_paths: bool, no_signatures: bool) -> dict:
    """Assemble the full capsule dict from DB data."""
    from roam import __version__

    ts = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    topology = _gather_topology(conn)
    symbols = _gather_symbols(conn, redact_paths=redact_paths, no_signatures=no_signatures)
    edges = _gather_edges(conn)
    clusters = _gather_clusters(conn)
    health = _gather_health(conn)

    return {
        "capsule": {
            "version": "1.0",
            "generated": ts,
            "tool_version": __version__,
            "redacted": redact_paths,
            "no_signatures": no_signatures,
        },
        "topology": topology,
        "symbols": symbols,
        "edges": edges,
        "clusters": clusters,
        "health": health,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("capsule")
@click.option("--redact-paths", is_flag=True, default=False,
              help="Anonymize file paths by hashing each path component.")
@click.option("--no-signatures", is_flag=True, default=False,
              help="Omit parameter signatures from symbol entries.")
@click.option("--output", default=None, metavar="FILE",
              help="Write the full JSON capsule to FILE instead of stdout.")
@click.pass_context
def capsule(ctx, redact_paths, no_signatures, output):
    """Export the structural graph as a portable JSON capsule.

    The capsule contains symbol signatures, call edges, cluster assignments,
    and health metrics — but never function bodies. Useful for external
    architectural review without sharing source code.

    When --output is given, the full capsule JSON is always written to the
    file regardless of --json mode, and a summary is printed to stdout.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        capsule_data = _build_capsule(conn, redact_paths=redact_paths,
                                      no_signatures=no_signatures)

    topology = capsule_data["topology"]
    health = capsule_data["health"]

    files_n = topology["files"]
    symbols_n = topology["symbols"]
    edges_n = topology["edges"]
    score = health["score"]
    cycles = health["cycles"]
    god = health["god_components"]
    langs = topology["languages"]
    langs_str = ", ".join(langs) if langs else "(none)"

    verdict = (
        f"capsule exported ({files_n} files, {symbols_n} symbols, {edges_n} edges)"
    )

    # Write to file if requested
    if output:
        out_path = Path(output)
        out_path.write_text(_json.dumps(capsule_data, indent=2, default=str),
                            encoding="utf-8")

    # JSON mode without --output: emit full capsule in envelope
    if json_mode and not output:
        click.echo(to_json(json_envelope(
            "capsule",
            summary={
                "verdict": verdict,
                "files": files_n,
                "symbols": symbols_n,
                "edges": edges_n,
                "health_score": score,
            },
            **capsule_data,
        )))
        return

    # Text summary (always shown when --output is used; default mode otherwise)
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo("Topology:")
    click.echo(f"  Files:     {files_n}")
    click.echo(f"  Symbols:   {symbols_n}")
    click.echo(f"  Edges:     {edges_n}")
    click.echo(f"  Languages: {langs_str}")
    click.echo()
    click.echo("Health:")
    click.echo(f"  Score: {score}/100")
    click.echo(f"  Cycles: {cycles}")
    click.echo(f"  God components: {god}")

    if output:
        click.echo()
        click.echo(f"Capsule written to: {output}")
