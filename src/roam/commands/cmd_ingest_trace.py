"""Ingest runtime traces (OpenTelemetry, Jaeger, Zipkin, generic) into the index.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam ingest-trace`` is a state-mutating ingest
command — its output is invocation-scoped ingest status (rows
inserted, spans parsed, services discovered), not per-location code
violations with file:line coordinates. The hotspot findings produced
downstream by ``roam hotspots`` consume the ingested traces; SARIF
exposure belongs there, not on the ingest step. See ``cmd_mutate``
for the parallel state-mutating disclosure pattern (W1180) +
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

import json

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.exit_codes import EXIT_ERROR
from roam.output.formatter import format_table, json_envelope, to_json


def _emit_parse_error(json_mode: bool, trace_path: str, message: str) -> None:
    """Emit a structured error envelope on a parse / IO failure.

    Pattern 1B — the underlying failure already carried structured
    signal (the message names the file and the parse error). The CLI
    bridge must preserve that signal rather than letting a raw
    traceback escape and triggering the wrapper's generic
    ``COMMAND_FAILED`` envelope. Pattern 2 — disclose the partial /
    failed state explicitly; never silent-fallback to a SUCCESS verdict.
    """
    verdict = f"Failed to parse trace file: {trace_path}"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ingest-trace",
                    summary={
                        "verdict": verdict,
                        "total": 0,
                        "matched": 0,
                        "unmatched": 0,
                        "partial_success": True,
                        "state": "parse_error",
                    },
                    error=message,
                    hint="Verify the file is valid JSON and matches the declared --otel/--jaeger/--zipkin/--generic format.",
                    agent_contract={
                        "facts": [
                            verdict,
                            f"trace ingestion aborted on {trace_path}",
                        ],
                        "next_commands": [
                            "roam ingest-trace --help",
                        ],
                    },
                    spans=[],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(message)


@roam_capability(
    name="ingest-trace",
    category="health",
    summary="Ingest runtime trace data and match spans to symbols",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
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
    format. Auto-detects format when given a positional argument. This command
    is a prerequisite for ``hotspots`` — run it first to populate runtime
    statistics from trace data, then use ``hotspots`` to classify functions as
    UPGRADE/CONFIRMED/DOWNGRADE based on combined static and runtime signals.

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
        auto_detect_format,
        ensure_runtime_table,
        ingest_generic_trace,
        ingest_jaeger_trace,
        ingest_otel_trace,
        ingest_zipkin_trace,
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
        try:
            fmt = auto_detect_format(trace_file)
        except ValueError as exc:
            _emit_parse_error(json_mode, trace_file, str(exc))
            ctx.exit(EXIT_ERROR)
            return
    else:
        from roam.exit_codes import EXIT_USAGE

        # Pattern 1B/1C discipline: emit a structured envelope in JSON mode
        # so MCP wrappers see actionable state, not a raw COMMAND_FAILED.
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "ingest-trace",
                        summary={
                            "verdict": "no trace file provided",
                            "state": "usage_error",
                            "partial_success": True,
                        },
                        status="usage_error",
                        isError=True,
                        error_code="USAGE_ERROR",
                        error="no trace file provided",
                        hint="Pass a positional trace file or use --otel/--jaeger/--zipkin/--generic.",
                    )
                )
            )
        else:
            click.echo("Error: provide a trace file (positional or via --otel/--jaeger/--zipkin/--generic)")
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
        # Wrap the ingester call so malformed JSON / OSError become a
        # structured error envelope rather than a raw traceback. The
        # specific ingesters can also raise ``ValueError`` on a
        # type-mismatched root (e.g. an OTel doc handed to the generic
        # ingester); both surfaces are routed through the same envelope.
        try:
            results = ingester(conn, path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            _emit_parse_error(json_mode, path, str(exc))
            ctx.exit(EXIT_ERROR)
            return

    total = len(results)
    matched = sum(1 for r in results if r["matched"])
    unmatched = total - matched

    verdict = f"{total} spans ingested, {matched} matched to symbols, {unmatched} unmatched"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ingest-trace",
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
                )
            )
        )
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
            rows.append(
                [
                    r["symbol_name"],
                    file_str,
                    f"{r['call_count']} calls",
                    p99_str,
                    f"err={err_pct}",
                    status,
                ]
            )
        click.echo(
            format_table(
                ["Name", "File", "Calls", "P99", "Errors", "Status"],
                rows,
                budget=30,
            )
        )
