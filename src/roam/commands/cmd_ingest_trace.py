"""Ingest runtime traces (OpenTelemetry, Jaeger, Zipkin, generic) into the index."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.argument("trace_file", required=False, default=None)
@click.option("--otel", "otel_file", default=None, help="OpenTelemetry JSON trace file")
@click.option("--jaeger", "jaeger_file", default=None, help="Jaeger JSON trace file")
@click.option("--zipkin", "zipkin_file", default=None, help="Zipkin JSON trace file")
@click.option("--generic", "generic_file", default=None, help="Generic JSON stats file")
@click.pass_context
def ingest_trace(ctx, trace_file, otel_file, jaeger_file, zipkin_file, generic_file):
    """Ingest runtime trace data and match spans to symbols.

    Supports OpenTelemetry (OTLP JSON), Jaeger, Zipkin, and a simple generic
    format. Auto-detects format when given a positional argument.

    \b
    Examples:
        roam ingest-trace trace.json
        roam ingest-trace --otel otel-trace.json
        roam ingest-trace --jaeger jaeger.json
        roam ingest-trace --zipkin zipkin.json
        roam ingest-trace --generic stats.json
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.runtime.trace_ingest import (
        ingest_otel_trace,
        ingest_jaeger_trace,
        ingest_zipkin_trace,
        ingest_generic_trace,
        auto_detect_format,
        ensure_runtime_table,
    )

    # Determine which file and format to use
    path = None
    fmt = None

    if otel_file:
        path, fmt = otel_file, "otel"
    elif jaeger_file:
        path, fmt = jaeger_file, "jaeger"
    elif zipkin_file:
        path, fmt = zipkin_file, "zipkin"
    elif generic_file:
        path, fmt = generic_file, "generic"
    elif trace_file:
        path = trace_file
        fmt = auto_detect_format(trace_file)
    else:
        click.echo("Error: provide a trace file (positional or via --otel/--jaeger/--zipkin/--generic)")
        from roam.exit_codes import EXIT_USAGE
        ctx.exit(EXIT_USAGE)
        return

    ingesters = {
        "otel": ingest_otel_trace,
        "jaeger": ingest_jaeger_trace,
        "zipkin": ingest_zipkin_trace,
        "generic": ingest_generic_trace,
    }

    with open_db(readonly=False) as conn:
        ensure_runtime_table(conn)
        ingester = ingesters[fmt]
        results = ingester(conn, path)

    total = len(results)
    matched = sum(1 for r in results if r["matched"])
    unmatched = total - matched

    verdict = f"{total} spans ingested, {matched} matched to symbols, {unmatched} unmatched"

    if json_mode:
        click.echo(to_json(json_envelope("ingest-trace",
            summary={
                "verdict": verdict,
                "total": total,
                "matched": matched,
                "unmatched": unmatched,
                "format": fmt,
            },
            spans=[
                {
                    "symbol_name": r["symbol_name"],
                    "file_path": r["file_path"],
                    "call_count": r["call_count"],
                    "p50_latency_ms": r["p50_latency_ms"],
                    "p99_latency_ms": r["p99_latency_ms"],
                    "error_rate": r["error_rate"],
                    "otel_db_system": r.get("otel_db_system"),
                    "otel_db_operation": r.get("otel_db_operation"),
                    "otel_db_statement_type": r.get("otel_db_statement_type"),
                    "matched": r["matched"],
                }
                for r in results
            ],
        )))
        return

    # Text output
    click.echo(f"VERDICT: {verdict}\n")

    if results:
        rows = []
        for r in results:
            err_pct = f"{r['error_rate'] * 100:.0f}%" if r["error_rate"] else "0%"
            p99_str = f"p99={r['p99_latency_ms']:.0f}ms" if r["p99_latency_ms"] is not None else "p99=n/a"
            status = "MATCHED" if r["matched"] else "UNMATCHED"
            file_str = r["file_path"] or "-"
            rows.append([
                r["symbol_name"],
                file_str,
                f"{r['call_count']} calls",
                p99_str,
                f"err={err_pct}",
                status,
            ])
        click.echo(format_table(
            ["Name", "File", "Calls", "P99", "Errors", "Status"],
            rows,
            budget=30,
        ))
