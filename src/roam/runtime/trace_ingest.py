"""Trace parsing and ingestion for OpenTelemetry, Jaeger, Zipkin, and generic formats."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import NamedTuple

from roam.output.formatter import WarningsOut

# OTel span-status codes that signal an error. Both numeric (``2`` per
# the OTLP wire format) and the canonical string label are accepted —
# different producers emit different shapes. Centralised here so the
# OTel parser stays a one-liner and the discipline is visible to
# auditors.
_OTEL_ERROR_STATUS_CODES: frozenset[object] = frozenset({2, "STATUS_CODE_ERROR", "ERROR"})

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# The runtime_stats table is defined in roam.db.schema (SCHEMA_SQL) and is
# created by ensure_schema() during open_db().  The helper below is kept for
# callers that operate on standalone connections (e.g. tests, external tools).


def ensure_runtime_table(conn: sqlite3.Connection) -> None:
    """Ensure the runtime_stats table exists.

    Delegates to the canonical schema in roam.db.schema so there is a single
    source of truth for the table definition.
    """
    from roam.db.schema import SCHEMA_SQL

    conn.executescript(SCHEMA_SQL)


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
        # Normalize path separators for comparison. ``files.path`` is
        # always stored with forward slashes (git-style POSIX path)
        # regardless of host OS, so we MUST match against the normalised
        # form. The second fallback uses the basename of the normalised
        # path so a trace file that carries an absolute system path
        # (``C:/repo/api.py`` or ``/home/user/repo/api.py``) still
        # matches a relative repo-rooted path stored in ``files.path``.
        norm = file_path.replace("\\", "/")
        rows = conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name = ? AND f.path LIKE ?",
            (function_name, f"%{norm}"),
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
        # Fallback: match on basename. Catches traces that record an
        # absolute filesystem path rather than a repo-relative path.
        if not rows:
            basename = norm.rsplit("/", 1)[-1]
            if basename and basename != norm:
                rows = conn.execute(
                    "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name = ? AND f.path LIKE ?",
                    (function_name, f"%/{basename}"),
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
    otel_db_system: str | None = None,
    otel_db_operation: str | None = None,
    otel_db_statement_type: str | None = None,
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
            "last_seen = ?, otel_db_system = ?, otel_db_operation = ?, "
            "otel_db_statement_type = ?, ingested_at = ? "
            "WHERE id = ?",
            (
                symbol_id,
                file_path,
                call_count,
                p50_latency_ms,
                p99_latency_ms,
                error_rate,
                last_seen,
                otel_db_system,
                otel_db_operation,
                otel_db_statement_type,
                now,
                existing[0],
            ),
        )
    else:
        conn.execute(
            "INSERT INTO runtime_stats "
            "(symbol_id, symbol_name, file_path, trace_source, call_count, "
            "p50_latency_ms, p99_latency_ms, error_rate, last_seen, "
            "otel_db_system, otel_db_operation, otel_db_statement_type, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol_id,
                symbol_name,
                file_path,
                trace_source,
                call_count,
                p50_latency_ms,
                p99_latency_ms,
                error_rate,
                last_seen,
                otel_db_system,
                otel_db_operation,
                otel_db_statement_type,
                now,
            ),
        )

    return {
        "symbol_name": symbol_name,
        "file_path": file_path,
        "symbol_id": symbol_id,
        "call_count": call_count,
        "p50_latency_ms": p50_latency_ms,
        "p99_latency_ms": p99_latency_ms,
        "error_rate": error_rate,
        "otel_db_system": otel_db_system,
        "otel_db_operation": otel_db_operation,
        "otel_db_statement_type": otel_db_statement_type,
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


def _attr_value(attr: dict) -> str | None:
    """Extract a scalar string value from an OTel attribute object."""
    value = attr.get("value")
    if not isinstance(value, dict):
        return str(value) if value is not None else None

    for key in (
        "stringValue",
        "string_value",
        "intValue",
        "int_value",
        "doubleValue",
        "double_value",
        "boolValue",
        "bool_value",
    ):
        if key in value and value[key] is not None:
            return str(value[key])
    return None


def _statement_type(statement: str) -> str | None:
    """Infer SQL operation type (SELECT/INSERT/UPDATE/...) from SQL text."""
    if not statement:
        return None
    token = statement.strip().split(" ", 1)[0].upper()
    allowed = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "UPSERT",
        "REPLACE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
    }
    return token if token in allowed else None


class _OtelSpanRollup(NamedTuple):
    call_count: int
    p50_latency_ms: float | None
    p99_latency_ms: float | None
    error_rate: float
    file_path: str | None
    db_system: str | None
    db_operation: str | None
    db_statement_type: str | None


class _ZipkinSpanRollup(NamedTuple):
    call_count: int
    p50_latency_ms: float | None
    p99_latency_ms: float | None
    error_rate: float


class _JaegerSpanRollup(NamedTuple):
    call_count: int
    p50_latency_ms: float | None
    p99_latency_ms: float | None
    error_rate: float


def _fold_otel_attr_for_stable_rollup(
    attr: dict,
    db_systems: set[str],
    db_operations: set[str],
    db_stmt_types: set[str],
    file_path: str | None,
) -> str | None:
    key = attr.get("key", "")
    if file_path is None and key in ("code.filepath", "code.function.file"):
        value = attr.get("value", {})
        if isinstance(value, dict):
            return value.get("stringValue", value.get("string_value"))
        return file_path

    val = _attr_value(attr)
    if not val:
        return file_path
    if key == "db.system":
        db_systems.add(val.lower())
    elif key in {"db.operation", "db.sql.operation", "db.mongodb.operation"}:
        db_operations.add(val.upper())
    elif key in {"db.statement", "db.query.text", "db.sql.text"}:
        statement_type = _statement_type(val)
        if statement_type:
            db_stmt_types.add(statement_type)
    return file_path


def _fold_otel_span_fields_for_stable_rollup(span: dict, durations_ms: list[float]) -> int:
    start = int(span.get("startTimeUnixNano", 0))
    end = int(span.get("endTimeUnixNano", 0))
    if start and end:
        durations_ms.append((end - start) / 1_000_000)

    status = span.get("status") or {}
    if isinstance(status, dict) and status.get("code") in _OTEL_ERROR_STATUS_CODES:
        return 1
    return 0


def _summarize_otel_group_for_stable_upsert(spans: list[dict]) -> _OtelSpanRollup:
    durations_ms: list[float] = []
    error_count = 0
    db_systems: set[str] = set()
    db_operations: set[str] = set()
    db_stmt_types: set[str] = set()
    file_path: str | None = None

    for span in spans:
        error_count += _fold_otel_span_fields_for_stable_rollup(span, durations_ms)
        attrs = span.get("attributes", [])
        for attr in attrs:
            file_path = _fold_otel_attr_for_stable_rollup(
                attr,
                db_systems,
                db_operations,
                db_stmt_types,
                file_path,
            )

    call_count = len(spans)
    db_statement_type = sorted(db_stmt_types)[0] if db_stmt_types else None
    return _OtelSpanRollup(
        call_count=call_count,
        p50_latency_ms=_percentile(durations_ms, 50) if durations_ms else None,
        p99_latency_ms=_percentile(durations_ms, 99) if durations_ms else None,
        error_rate=error_count / call_count if call_count > 0 else 0.0,
        file_path=file_path,
        db_system=sorted(db_systems)[0] if db_systems else None,
        db_operation=sorted(db_operations)[0] if db_operations else db_statement_type,
        db_statement_type=db_statement_type,
    )


def _summarize_zipkin_group_for_stable_upsert(spans: list[dict]) -> _ZipkinSpanRollup:
    """Roll up a list of Zipkin spans into per-operation metrics.

    Separates the numeric aggregation (durations, error rate, percentiles)
    from the Zipkin envelope parsing in ``ingest_zipkin_trace`` so each
    layer has a single responsibility.
    """
    durations_ms: list[float] = []
    error_count = 0
    for span in spans:
        duration_us = span.get("duration", 0)
        durations_ms.append(duration_us / 1000.0)
        tags = span.get("tags", {})
        if tags.get("error"):
            error_count += 1

    call_count = len(spans)
    return _ZipkinSpanRollup(
        call_count=call_count,
        p50_latency_ms=_percentile(durations_ms, 50) if durations_ms else None,
        p99_latency_ms=_percentile(durations_ms, 99) if durations_ms else None,
        error_rate=error_count / call_count if call_count > 0 else 0.0,
    )


def _summarize_jaeger_group_for_stable_upsert(spans: list[dict]) -> _JaegerSpanRollup:
    """Roll up Jaeger spans once the operation boundary is known."""
    durations_ms: list[float] = []
    error_count = 0
    for span in spans:
        duration_us = span.get("duration", 0)
        durations_ms.append(duration_us / 1000.0)
        tags = span.get("tags", [])
        for tag in tags:
            if tag.get("key") == "error" and tag.get("value") is True:
                error_count += 1
                break

    call_count = len(spans)
    return _JaegerSpanRollup(
        call_count=call_count,
        p50_latency_ms=_percentile(durations_ms, 50) if durations_ms else None,
        p99_latency_ms=_percentile(durations_ms, 99) if durations_ms else None,
        error_rate=error_count / call_count if call_count > 0 else 0.0,
    )


def _group_jaeger_spans_for_operation_rollups(data: dict) -> dict[str, list[dict]]:
    span_groups: dict[str, list[dict]] = {}
    traces = data.get("data", [data]) if isinstance(data.get("data"), list) else [data]
    for trace in traces:
        spans = trace.get("spans", [])
        for span in spans:
            name = span.get("operationName", "unknown")
            span_groups.setdefault(name, []).append(span)
    return span_groups


def _group_otel_spans_for_operation_rollups(data: dict) -> dict[str, list[dict]]:
    span_groups: dict[str, list[dict]] = {}
    resource_spans = data.get("resourceSpans", data.get("resource_spans", []))
    for rs in resource_spans:
        scope_spans = rs.get("scopeSpans", rs.get("scope_spans", []))
        for ss in scope_spans:
            spans = ss.get("spans", [])
            for span in spans:
                name = span.get("name", "unknown")
                span_groups.setdefault(name, []).append(span)
    return span_groups


# ---------------------------------------------------------------------------
# Ingesters
# ---------------------------------------------------------------------------


def ingest_generic_trace(
    conn: sqlite3.Connection,
    trace_path: str,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
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

    Raises ``ValueError`` when the root is not a list or an entry is not
    a mapping. Pattern-2 "make fallback chains loud" — silently
    skipping malformed input would let an OTel/Jaeger/Zipkin file
    routed here by mistake produce zero rows and a SUCCESS verdict.

    W599: generic stays loud on wrong-shape (ValueError) — that
    discipline pre-dates W599 and is intentional. The only post-parse
    silent-empty path is an *empty list* (``[]``), which is a
    legitimate cold-trace sentinel; the marker is informational.

    Emitted kind (closed enum):

      * ``trace_ingest_generic_empty:<path>`` — JSON parsed cleanly
        as an empty list (legitimate cold-trace sentinel; no entries).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(
            f"{trace_path}: generic trace must be a JSON list, "
            f"got {type(data).__name__}; use auto_detect_format() to "
            f"route to the right ingester"
        )

    if not data:
        _emit(f"trace_ingest_generic_empty:{trace_path}")
        return []

    results = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"{trace_path}: generic trace entry {i} must be an object, got {type(entry).__name__}")
        fn_name = entry.get("function", "")
        file_path = entry.get("file")
        call_count = entry.get("call_count", 0)
        p50 = entry.get("p50_ms")
        p99 = entry.get("p99_ms")
        err = entry.get("error_rate", 0.0)

        symbol_id = match_trace_to_symbol(conn, fn_name, file_path)
        result = _upsert_runtime_stat(
            conn,
            symbol_id,
            fn_name,
            file_path,
            "generic",
            call_count,
            p50,
            p99,
            err,
            None,
        )
        results.append(result)

    return results


def ingest_otel_trace(
    conn: sqlite3.Connection,
    trace_path: str,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Parse OpenTelemetry JSON trace format (OTLP JSON).

    Handles the standard OTLP JSON export structure with
    resourceSpans > scopeSpans > spans.

    W599: mirrors the W595 ``read_permit`` / W596 ``read_run_meta`` /
    W597 ``daemon_state`` / W598 ``_load_cache`` plumb — when
    *warnings_out* is supplied, the silent-empty wrong-shape sites
    each append one structured closed-enum marker so callers can tell
    "valid JSON but wrong format (misroute)" from "valid OTel JSON
    with zero spans (legitimate cold-trace dump)". The returned
    ``list[dict]`` shape is PRESERVED — the empty-list return is the
    caller contract. ``warnings_out=None`` (default) preserves the
    pre-W599 silent-empty behaviour.

    W978 first-hypothesis finding: ``open()`` and ``json.load()``
    already RAISE loudly (``OSError`` / ``JSONDecodeError``); the CLI
    bridge in ``cmd_ingest_trace`` catches both and emits a structured
    parse-error envelope. There is no silent-None read-failure path
    to plumb. The plumb here targets the *post-parse* silent-empty
    paths: valid JSON whose top-level shape is not OTel-flavoured.

    Marker shape mirrors W595's / W596's / W597's / W598's closed-enum
    vocabulary with a ``trace_ingest_otel_`` prefix so a caller
    threading the same bucket through multiple substrate read sites
    sees one uniform marker vocabulary.

    Emitted kinds (closed enum):

      * ``trace_ingest_otel_corrupt:<path>:WrongFormat`` — JSON parsed
        cleanly but the top-level shape is not an OTLP doc (missing
        both ``resourceSpans`` and ``resource_spans`` keys; or
        top-level is not a JSON object). This is a misroute signal:
        a Jaeger/Zipkin/generic file was handed to the OTel reader.
      * ``trace_ingest_otel_empty:<path>`` — JSON parsed cleanly,
        ``resourceSpans`` was present but contained zero spans. This
        is the legitimate cold-trace cold-start sentinel (trace agent
        started but no traffic yet); the marker is informational
        rather than an error.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or ("resourceSpans" not in data and "resource_spans" not in data):
        _emit(f"trace_ingest_otel_corrupt:{trace_path}:WrongFormat")
        return []

    span_groups = _group_otel_spans_for_operation_rollups(data)
    results = []
    for span_name, spans in span_groups.items():
        rollup = _summarize_otel_group_for_stable_upsert(spans)

        symbol_id = match_trace_to_symbol(conn, span_name, rollup.file_path)
        result = _upsert_runtime_stat(
            conn,
            symbol_id,
            span_name,
            rollup.file_path,
            "otel",
            rollup.call_count,
            rollup.p50_latency_ms,
            rollup.p99_latency_ms,
            rollup.error_rate,
            None,
            otel_db_system=rollup.db_system,
            otel_db_operation=rollup.db_operation,
            otel_db_statement_type=rollup.db_statement_type,
        )
        results.append(result)

    if not results:
        # Legitimate cold-trace sentinel: OTel doc parsed cleanly but
        # contained zero spans. Informational marker so an operator
        # threading ``warnings_out`` can distinguish "no spans ingested
        # because file was empty" from "no spans ingested because the
        # file matched a different format".
        _emit(f"trace_ingest_otel_empty:{trace_path}")
    return results


def ingest_jaeger_trace(
    conn: sqlite3.Connection,
    trace_path: str,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Parse Jaeger JSON format.

    Handles the standard Jaeger UI export structure with
    data > traces > spans.

    W599: mirrors the OTel sibling. Closed-enum markers:

      * ``trace_ingest_jaeger_corrupt:<path>:WrongFormat`` — JSON
        parsed cleanly but the top-level shape is not a Jaeger doc
        (top-level is not an object, or has no ``data``/``spans``
        key). Misroute signal.
      * ``trace_ingest_jaeger_empty:<path>`` — Jaeger doc parsed
        cleanly with zero spans (legitimate cold-trace sentinel).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or ("data" not in data and "spans" not in data):
        _emit(f"trace_ingest_jaeger_corrupt:{trace_path}:WrongFormat")
        return []

    span_groups = _group_jaeger_spans_for_operation_rollups(data)

    results = []
    for span_name, spans in span_groups.items():
        rollup = _summarize_jaeger_group_for_stable_upsert(spans)

        file_path = None
        symbol_id = match_trace_to_symbol(conn, span_name, file_path)
        result = _upsert_runtime_stat(
            conn,
            symbol_id,
            span_name,
            file_path,
            "jaeger",
            rollup.call_count,
            rollup.p50_latency_ms,
            rollup.p99_latency_ms,
            rollup.error_rate,
            None,
        )
        results.append(result)

    if not results:
        _emit(f"trace_ingest_jaeger_empty:{trace_path}")
    return results


def ingest_zipkin_trace(
    conn: sqlite3.Connection,
    trace_path: str,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Parse Zipkin JSON format.

    Zipkin exports as a flat list of spans.

    W599: mirrors the OTel/Jaeger siblings. Closed-enum markers:

      * ``trace_ingest_zipkin_corrupt:<path>:WrongFormat`` — JSON
        parsed cleanly but the top-level is neither a list nor a dict
        that looks like a Zipkin span (no ``traceId``/``id`` keys on
        the wrapped element). Misroute signal.
      * ``trace_ingest_zipkin_empty:<path>`` — top-level was an empty
        list (legitimate cold-trace sentinel).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    ensure_runtime_table(conn)
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Zipkin is a flat list of spans
    if isinstance(data, list):
        if not data:
            # Empty list — legitimate cold-trace sentinel.
            _emit(f"trace_ingest_zipkin_empty:{trace_path}")
            return []
    else:
        # Single-span dict — heuristic check that it actually looks
        # like a Zipkin span. The Zipkin span shape has both
        # ``traceId`` and ``id`` (auto_detect_format relies on the
        # same pair). If either is missing, it's a misroute (a
        # Jaeger/OTel/generic doc handed to the Zipkin reader).
        if not isinstance(data, dict) or "traceId" not in data or "id" not in data:
            _emit(f"trace_ingest_zipkin_corrupt:{trace_path}:WrongFormat")
            return []
        data = [data]

    span_groups: dict[str, list[dict]] = {}
    for span in data:
        name = span.get("name", "unknown")
        if name not in span_groups:
            span_groups[name] = []
        span_groups[name].append(span)

    results = []
    for span_name, spans in span_groups.items():
        rollup = _summarize_zipkin_group_for_stable_upsert(spans)

        file_path = None
        symbol_id = match_trace_to_symbol(conn, span_name, file_path)
        result = _upsert_runtime_stat(
            conn,
            symbol_id,
            span_name,
            file_path,
            "zipkin",
            rollup.call_count,
            rollup.p50_latency_ms,
            rollup.p99_latency_ms,
            rollup.error_rate,
            None,
        )
        results.append(result)

    return results


def _read_trace_json_for_auto_detection(trace_path: str) -> object:
    """Read a trace file while preserving the detector's ValueError boundary."""
    try:
        with open(trace_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise ValueError(f"{trace_path}: trace file not found") from exc
    except OSError as exc:
        raise ValueError(f"{trace_path}: cannot read trace file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{trace_path}: not a valid JSON document (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc


def _looks_like_otel_to_prefer_explicit_trace_schema(data: object) -> bool:
    return isinstance(data, dict) and ("resourceSpans" in data or "resource_spans" in data)


def _looks_like_jaeger_to_avoid_generic_misroute(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    if "spans" in data:
        return True

    inner = data.get("data")
    if not isinstance(inner, list) or not inner:
        return False
    return isinstance(inner[0], dict) and "spans" in inner[0]


def _first_list_trace_entry_for_legacy_formats(data: object) -> dict | None:
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    return first if isinstance(first, dict) else None


def _looks_like_zipkin_to_preserve_span_identity(data: object) -> bool:
    first = _first_list_trace_entry_for_legacy_formats(data)
    return first is not None and "traceId" in first and "id" in first


def _looks_like_generic_to_preserve_legacy_contract(data: object) -> bool:
    first = _first_list_trace_entry_for_legacy_formats(data)
    return first is not None and "function" in first


def _detect_format_that_preserves_specificity(data: object) -> str | None:
    if _looks_like_otel_to_prefer_explicit_trace_schema(data):
        return "otel"
    if _looks_like_jaeger_to_avoid_generic_misroute(data):
        return "jaeger"
    if _looks_like_zipkin_to_preserve_span_identity(data):
        return "zipkin"
    if _looks_like_generic_to_preserve_legacy_contract(data):
        return "generic"
    return None


def auto_detect_format(
    trace_path: str,
    *,
    warnings_out: WarningsOut = None,
) -> str:
    """Auto-detect trace format from JSON structure.

    Returns one of: "otel", "jaeger", "zipkin", "generic".

    Raises ``ValueError`` (NOT raw ``OSError`` / ``JSONDecodeError``) on
    a missing file or malformed JSON so the CLI bridge can produce a
    structured error envelope instead of a stack trace. Mirrors the
    "make fallback chains loud" rule — the underlying failure stays
    typed but the wrapping is uniform for the caller.

    W599: when no shape matches (dict without OTel/Jaeger keys, list
    of non-Zipkin/non-generic dicts, empty list, or non-collection
    root), the detector silently falls back to ``"generic"``. With
    *warnings_out* supplied, the silent fallback emits a closed-enum
    marker so a caller threading the bucket can disclose the misroute
    risk rather than letting a non-generic file produce zero rows
    under a SUCCESS verdict.

    Emitted kind (closed enum):

      * ``trace_ingest_auto_detect_fallback_generic:<path>`` — no
        format-specific shape matched; the detector defaulted to
        ``"generic"``. The string return is unchanged; the marker is
        informational. ``warnings_out=None`` (default) preserves the
        pre-W599 silent-fallback behaviour.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    data = _read_trace_json_for_auto_detection(trace_path)
    detected = _detect_format_that_preserves_specificity(data)
    if detected is not None:
        return detected

    _emit(f"trace_ingest_auto_detect_fallback_generic:{trace_path}")
    return "generic"
