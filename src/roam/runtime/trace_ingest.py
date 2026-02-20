"""Trace parsing and ingestion for OpenTelemetry, Jaeger, Zipkin, and generic formats."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from statistics import median


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

RUNTIME_STATS_SQL = """\
CREATE TABLE IF NOT EXISTS runtime_stats (
    id INTEGER PRIMARY KEY,
    symbol_id INTEGER REFERENCES symbols(id),
    symbol_name TEXT,
    file_path TEXT,
    trace_source TEXT,
    call_count INTEGER DEFAULT 0,
    p50_latency_ms REAL,
    p99_latency_ms REAL,
    error_rate REAL DEFAULT 0.0,
    last_seen TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
"""


def ensure_runtime_table(conn: sqlite3.Connection) -> None:
    """Create the runtime_stats table if it does not exist."""
    conn.executescript(RUNTIME_STATS_SQL)


# ---------------------------------------------------------------------------
# Symbol matching
# ---------------------------------------------------------------------------

def match_trace_to_symbol(
    conn: sqlite3.Connection,
    function_name: str,
    file_path: str | None = None,
) -> int | None:
    """Try to match a trace span to a symbol in the DB.

    Lookup chain:
    1. Exact match on name + file_path
    2. Exact match on name only (if unique)
    3. Fuzzy match on qualified_name containing function_name
    Returns symbol_id or None.
    """
    if file_path:
        # Normalize path separators for comparison
        norm = file_path.replace("\\", "/")
        rows = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.name = ? AND f.path LIKE ?",
            (function_name, f"%{norm}"),
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
        # Also try with the raw path
        if not rows:
            rows = conn.execute(
                "SELECT s.id FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE s.name = ? AND f.path LIKE ?",
                (function_name, f"%{file_path}"),
            ).fetchall()
            if len(rows) == 1:
                return rows[0][0]

    # Exact match on name only (must be unique)
    rows = conn.execute(
        "SELECT id FROM symbols WHERE name = ?",
        (function_name,),
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]

    # Fuzzy match on qualified_name
    rows = conn.execute(
        "SELECT id FROM symbols WHERE qualified_name LIKE ? LIMIT 1",
        (f"%{function_name}%",),
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]

    return None


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

def _upsert_runtime_stat(
    conn: sqlite3.Connection,
    symbol_id: int | None,
    symbol_name: str,
    file_path: str | None,
    trace_source: str,
    call_count: int,
    p50_latency_ms: float | None,
    p99_latency_ms: float | None,
    error_rate: float,
    last_seen: str | None,
) -> dict:
    """Insert or update a runtime_stats row. Returns a summary dict."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if last_seen is None:
        last_seen = now

    # Check if a row already exists for this symbol_name + trace_source
    existing = conn.execute(
        "SELECT id FROM runtime_stats WHERE symbol_name = ? AND trace_source = ?",
        (symbol_name, trace_source),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE runtime_stats SET "
            "symbol_id = ?, file_path = ?, call_count = ?, "
            "p50_latency_ms = ?, p99_latency_ms = ?, error_rate = ?, "
            "last_seen = ?, ingested_at = ? "
            "WHERE id = ?",
            (symbol_id, file_path, call_count, p50_latency_ms,
             p99_latency_ms, error_rate, last_seen, now, existing[0]),
        )
    else:
        conn.execute(
            "INSERT INTO runtime_stats "
            "(symbol_id, symbol_name, file_path, trace_source, call_count, "
            "p50_latency_ms, p99_latency_ms, error_rate, last_seen, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol_id, symbol_name, file_path, trace_source, call_count,
             p50_latency_ms, p99_latency_ms, error_rate, last_seen, now),
        )

    return {
        "symbol_name": symbol_name,
        "file_path": file_path,
        "symbol_id": symbol_id,
        "call_count": call_count,
        "p50_latency_ms": p50_latency_ms,
        "p99_latency_ms": p99_latency_ms,
        "error_rate": error_rate,
        "matched": symbol_id is not None,
    }


# ---------------------------------------------------------------------------
# Latency helpers
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    """Compute a percentile from a sorted list of values."""
    if not values:
        return 0.0
    values = sorted(values)
    n = len(values)
    if n == 1:
        return values[0]
    k = (n - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return values[lo] + (values[hi] - values[lo]) * frac


# ---------------------------------------------------------------------------
# Ingesters
# ---------------------------------------------------------------------------

def ingest_generic_trace(conn: sqlite3.Connection, trace_path: str) -> list[dict]:
    """Parse a simple generic JSON trace format.

    Expected format::

        [
            {
                "function": "handle_request",
                "file": "api.py",
                "call_count": 1420,
                "p50_ms": 12.5,
                "p99_ms": 340,
                "error_rate": 0.02
            },
            ...
        ]
    """
    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []
    for entry in data:
        fn_name = entry.get("function", "")
        file_path = entry.get("file")
        call_count = entry.get("call_count", 0)
        p50 = entry.get("p50_ms")
        p99 = entry.get("p99_ms")
        err = entry.get("error_rate", 0.0)

        symbol_id = match_trace_to_symbol(conn, fn_name, file_path)
        result = _upsert_runtime_stat(
            conn, symbol_id, fn_name, file_path, "generic",
            call_count, p50, p99, err, None,
        )
        results.append(result)

    return results


def ingest_otel_trace(conn: sqlite3.Connection, trace_path: str) -> list[dict]:
    """Parse OpenTelemetry JSON trace format (OTLP JSON).

    Handles the standard OTLP JSON export structure with
    resourceSpans > scopeSpans > spans.
    """
    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Collect spans grouped by operation name
    span_groups: dict[str, list[dict]] = {}

    resource_spans = data.get("resourceSpans", data.get("resource_spans", []))
    for rs in resource_spans:
        scope_spans = rs.get("scopeSpans", rs.get("scope_spans", []))
        for ss in scope_spans:
            spans = ss.get("spans", [])
            for span in spans:
                name = span.get("name", "unknown")
                if name not in span_groups:
                    span_groups[name] = []
                span_groups[name].append(span)

    results = []
    for span_name, spans in span_groups.items():
        # Compute latency stats from durations
        durations_ms = []
        error_count = 0
        for span in spans:
            start = int(span.get("startTimeUnixNano", 0))
            end = int(span.get("endTimeUnixNano", 0))
            if start and end:
                durations_ms.append((end - start) / 1_000_000)
            status = span.get("status", {})
            if status.get("code") == 2 or status.get("code") == "STATUS_CODE_ERROR":
                error_count += 1

        call_count = len(spans)
        p50 = _percentile(durations_ms, 50) if durations_ms else None
        p99 = _percentile(durations_ms, 99) if durations_ms else None
        err_rate = error_count / call_count if call_count > 0 else 0.0

        # Extract file path from span attributes if present
        file_path = None
        for span in spans:
            attrs = span.get("attributes", [])
            for attr in attrs:
                key = attr.get("key", "")
                if key in ("code.filepath", "code.function.file"):
                    val = attr.get("value", {})
                    file_path = val.get("stringValue", val.get("string_value"))
                    break
            if file_path:
                break

        symbol_id = match_trace_to_symbol(conn, span_name, file_path)
        result = _upsert_runtime_stat(
            conn, symbol_id, span_name, file_path, "otel",
            call_count, p50, p99, err_rate, None,
        )
        results.append(result)

    return results


def ingest_jaeger_trace(conn: sqlite3.Connection, trace_path: str) -> list[dict]:
    """Parse Jaeger JSON format.

    Handles the standard Jaeger UI export structure with
    data > traces > spans.
    """
    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Collect spans grouped by operation name
    span_groups: dict[str, list[dict]] = {}

    traces = data.get("data", [data]) if isinstance(data.get("data"), list) else [data]
    for trace in traces:
        spans = trace.get("spans", [])
        for span in spans:
            name = span.get("operationName", "unknown")
            if name not in span_groups:
                span_groups[name] = []
            span_groups[name].append(span)

    results = []
    for span_name, spans in span_groups.items():
        durations_ms = []
        error_count = 0
        for span in spans:
            duration_us = span.get("duration", 0)
            durations_ms.append(duration_us / 1000.0)
            # Check tags for errors
            tags = span.get("tags", [])
            for tag in tags:
                if tag.get("key") == "error" and tag.get("value") is True:
                    error_count += 1
                    break

        call_count = len(spans)
        p50 = _percentile(durations_ms, 50) if durations_ms else None
        p99 = _percentile(durations_ms, 99) if durations_ms else None
        err_rate = error_count / call_count if call_count > 0 else 0.0

        file_path = None
        symbol_id = match_trace_to_symbol(conn, span_name, file_path)
        result = _upsert_runtime_stat(
            conn, symbol_id, span_name, file_path, "jaeger",
            call_count, p50, p99, err_rate, None,
        )
        results.append(result)

    return results


def ingest_zipkin_trace(conn: sqlite3.Connection, trace_path: str) -> list[dict]:
    """Parse Zipkin JSON format.

    Zipkin exports as a flat list of spans.
    """
    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Zipkin is a flat list of spans
    if not isinstance(data, list):
        data = [data]

    span_groups: dict[str, list[dict]] = {}
    for span in data:
        name = span.get("name", "unknown")
        if name not in span_groups:
            span_groups[name] = []
        span_groups[name].append(span)

    results = []
    for span_name, spans in span_groups.items():
        durations_ms = []
        error_count = 0
        for span in spans:
            duration_us = span.get("duration", 0)
            durations_ms.append(duration_us / 1000.0)
            tags = span.get("tags", {})
            if tags.get("error"):
                error_count += 1

        call_count = len(spans)
        p50 = _percentile(durations_ms, 50) if durations_ms else None
        p99 = _percentile(durations_ms, 99) if durations_ms else None
        err_rate = error_count / call_count if call_count > 0 else 0.0

        file_path = None
        symbol_id = match_trace_to_symbol(conn, span_name, file_path)
        result = _upsert_runtime_stat(
            conn, symbol_id, span_name, file_path, "zipkin",
            call_count, p50, p99, err_rate, None,
        )
        results.append(result)

    return results


def auto_detect_format(trace_path: str) -> str:
    """Auto-detect trace format from JSON structure.

    Returns one of: "otel", "jaeger", "zipkin", "generic".
    """
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        if "resourceSpans" in data or "resource_spans" in data:
            return "otel"
        if "data" in data and isinstance(data.get("data"), list):
            # Jaeger wraps traces in a "data" array
            inner = data["data"]
            if inner and isinstance(inner[0], dict) and "spans" in inner[0]:
                return "jaeger"
        if "spans" in data:
            return "jaeger"

    if isinstance(data, list):
        if data:
            first = data[0]
            if isinstance(first, dict):
                # Zipkin spans have traceId + id + kind
                if "traceId" in first and "id" in first:
                    return "zipkin"
                # Generic format has "function" key
                if "function" in first:
                    return "generic"

    return "generic"
