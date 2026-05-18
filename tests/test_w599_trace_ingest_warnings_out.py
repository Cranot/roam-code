"""W599 — trace-ingest readers plumb ``warnings_out`` on silent-empty paths.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster. W593/W595 closed the permits cluster. W596 closed the
runs-ledger cluster (``read_run_meta`` + bonus ``read_run_events``).
W597 closed the runtime-daemon cluster (``daemon_state`` + bonus
``daemon_running``). W598 closed the pr-analyze-cache reader
(``_load_cache``). W599 closes the runtime trace-ingest substrate:
``ingest_otel_trace`` / ``ingest_jaeger_trace`` / ``ingest_zipkin_trace``
/ ``ingest_generic_trace`` / ``auto_detect_format`` previously had
silent-empty paths (valid JSON whose top-level shape didn't match the
target format, or matched but contained zero records) that were
indistinguishable from "successful ingest of an empty trace" to a
programmatic caller.

W978 first-hypothesis finding: the trace-ingest readers are already
LOUD on the read-failure axis. ``open()`` raises ``OSError``,
``json.load()`` raises ``JSONDecodeError`` — both propagate up to the
``cmd_ingest_trace`` CLI bridge which wraps them in a structured
parse-error envelope. There is no silent-None read-failure path
needing the W595/W596/W597/W598 closed-enum ``..._read_failed:`` /
``..._corrupt:JSONDecodeError`` markers. W599's plumb is therefore
narrower: it targets the *post-parse silent-empty* misroute paths
(wrong shape) and the legitimate cold-trace sentinels (empty
records).

Closed-enum kinds (per format):

  * OTel:
    - ``trace_ingest_otel_corrupt:<path>:WrongFormat`` — top-level
      shape lacks both ``resourceSpans``/``resource_spans`` keys, or
      is not a JSON object. Misroute signal.
    - ``trace_ingest_otel_empty:<path>`` — OTel doc parsed cleanly,
      zero spans (legitimate cold-trace sentinel).
  * Jaeger:
    - ``trace_ingest_jaeger_corrupt:<path>:WrongFormat`` — top-level
      shape lacks ``data``/``spans`` keys, or is not a JSON object.
    - ``trace_ingest_jaeger_empty:<path>`` — zero spans.
  * Zipkin:
    - ``trace_ingest_zipkin_corrupt:<path>:WrongFormat`` — top-level
      neither a list nor a Zipkin-span-shaped dict (no ``traceId`` +
      ``id`` pair on the wrapped element).
    - ``trace_ingest_zipkin_empty:<path>`` — top-level was an empty
      list.
  * Generic:
    - ``trace_ingest_generic_empty:<path>`` — top-level parsed as an
      empty list (legitimate cold-trace sentinel). Wrong-shape stays
      LOUD (raises ``ValueError``) — the pre-W599 discipline; W599
      preserves it.
  * Auto-detect:
    - ``trace_ingest_auto_detect_fallback_generic:<path>`` — no
      format-specific shape matched; the detector silently defaulted
      to ``"generic"``.

Intentional-absence decisions (W978 + "Make fallback chains loud"):

  * Wrong-shape paths emit a ``_corrupt:WrongFormat`` marker —
    misroute is an operator-grade signal (a Jaeger doc handed to the
    OTel reader will silently produce zero rows and a SUCCESS
    verdict; that is the silent-fallback failure mode the W599 plumb
    is designed to disclose).
  * Empty-records paths emit a ``_empty:`` marker — cold-trace dumps
    are common and legitimate, but a programmatic caller benefits
    from being able to distinguish "no spans because file was empty"
    from "no spans because file matched a different format". The
    marker is informational; callers can ignore the bucket if they
    only care about the misroute signal.
  * Missing-file / corrupt-JSON paths are NOT plumbed: those raise
    ``OSError`` / ``JSONDecodeError`` today and the CLI bridge in
    ``cmd_ingest_trace`` already produces structured parse-error
    envelopes. There is no silent-None path to plumb there.

Caller audit: only one live caller exists per format —
``cmd_ingest_trace.ingest_trace`` (cmd_ingest_trace.py:165). It
catches ``(json.JSONDecodeError, OSError, ValueError)`` and surfaces a
structured parse-error envelope via ``_emit_parse_error``. It does NOT
thread ``warnings_out``. W599 leaves the caller unchanged — the plumb
is read-only and additive.

The empty-list / wrong-shape return is PRESERVED on every drop path —
the existing caller contract (an empty list means "no spans to ingest")
is unchanged. ``warnings_out=None`` (default) preserves the pre-W599
silent-empty behaviour.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589 / W592 / W593 /
W595 / W596 / W597 / W598).
"""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.runtime.trace_ingest import (  # noqa: E402
    auto_detect_format,
    ensure_runtime_table,
    ingest_generic_trace,
    ingest_jaeger_trace,
    ingest_otel_trace,
    ingest_zipkin_trace,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """A minimal in-memory SQLite with the runtime_stats table."""
    conn = sqlite3.connect(":memory:")
    ensure_runtime_table(conn)
    return conn


@pytest.fixture
def otel_clean(tmp_path: Path) -> str:
    """A minimal but valid OTLP JSON trace doc with one span."""
    doc = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "handle",
                                "startTimeUnixNano": 1_000_000_000,
                                "endTimeUnixNano": 1_010_000_000,
                                "status": {"code": "OK"},
                                "attributes": [],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    p = tmp_path / "otel.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


@pytest.fixture
def jaeger_clean(tmp_path: Path) -> str:
    """A minimal but valid Jaeger JSON trace doc with one span."""
    doc = {
        "data": [
            {
                "spans": [
                    {
                        "operationName": "handle",
                        "duration": 1500,
                        "tags": [],
                    }
                ]
            }
        ]
    }
    p = tmp_path / "jaeger.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


@pytest.fixture
def zipkin_clean(tmp_path: Path) -> str:
    """A minimal but valid Zipkin JSON trace doc with one span."""
    doc = [
        {
            "traceId": "abc123",
            "id": "span1",
            "name": "handle",
            "duration": 1500,
            "tags": {},
        }
    ]
    p = tmp_path / "zipkin.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


@pytest.fixture
def generic_clean(tmp_path: Path) -> str:
    """A minimal but valid generic JSON trace doc with one entry."""
    doc = [
        {
            "function": "handle",
            "file": "api.py",
            "call_count": 100,
            "p50_ms": 10,
            "p99_ms": 100,
            "error_rate": 0.0,
        }
    ]
    p = tmp_path / "generic.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


# ===========================================================================
# (1) Happy paths — clean reads emit no warnings (every format)
# ===========================================================================


def test_clean_otel_emits_no_warning(db_conn, otel_clean) -> None:
    """A normal OTel read with one span appends nothing to ``warnings_out``."""
    warnings: list[str] = []
    results = ingest_otel_trace(db_conn, otel_clean, warnings_out=warnings)

    assert len(results) == 1, f"expected 1 span; got {len(results)}"
    assert warnings == [], f"clean ingest must not emit warnings; got {warnings!r}"


def test_clean_jaeger_emits_no_warning(db_conn, jaeger_clean) -> None:
    """A normal Jaeger read with one span emits no warning."""
    warnings: list[str] = []
    results = ingest_jaeger_trace(db_conn, jaeger_clean, warnings_out=warnings)

    assert len(results) == 1
    assert warnings == [], f"clean Jaeger ingest must not emit warnings; got {warnings!r}"


def test_clean_zipkin_emits_no_warning(db_conn, zipkin_clean) -> None:
    """A normal Zipkin read with one span emits no warning."""
    warnings: list[str] = []
    results = ingest_zipkin_trace(db_conn, zipkin_clean, warnings_out=warnings)

    assert len(results) == 1
    assert warnings == [], f"clean Zipkin ingest must not emit warnings; got {warnings!r}"


def test_clean_generic_emits_no_warning(db_conn, generic_clean) -> None:
    """A normal generic read with one entry emits no warning."""
    warnings: list[str] = []
    results = ingest_generic_trace(db_conn, generic_clean, warnings_out=warnings)

    assert len(results) == 1
    assert warnings == [], f"clean generic ingest must not emit warnings; got {warnings!r}"


# ===========================================================================
# (2) Wrong-format misroute — each reader emits ``_corrupt:WrongFormat``
# ===========================================================================


def test_wrong_format_jaeger_into_otel_emits_corrupt(db_conn, jaeger_clean) -> None:
    """A Jaeger doc handed to the OTel reader emits ``trace_ingest_otel_corrupt:...:WrongFormat``.

    Pattern 2 "silent fallback" — before W599, the OTel reader would
    silently return ``[]`` for a Jaeger doc (no ``resourceSpans`` key)
    and a SUCCESS verdict would be indistinguishable from a clean
    cold-trace ingest. The W599 plumb makes the misroute disclosable
    on the warnings bucket.
    """
    warnings: list[str] = []
    results = ingest_otel_trace(db_conn, jaeger_clean, warnings_out=warnings)

    assert results == [], "wrong-format ingest must return empty list (caller contract)"
    assert len(warnings) == 1, f"expected one WrongFormat warning; got {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("trace_ingest_otel_corrupt:"), msg
    assert "WrongFormat" in msg, msg


def test_wrong_format_otel_into_jaeger_emits_corrupt(db_conn, otel_clean) -> None:
    """An OTel doc handed to the Jaeger reader emits ``trace_ingest_jaeger_corrupt:...:WrongFormat``."""
    warnings: list[str] = []
    results = ingest_jaeger_trace(db_conn, otel_clean, warnings_out=warnings)

    assert results == []
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_jaeger_corrupt:"), msg
    assert "WrongFormat" in msg, msg


def test_wrong_format_otel_into_zipkin_emits_corrupt(db_conn, otel_clean) -> None:
    """An OTel doc (dict without traceId/id) handed to Zipkin emits ``trace_ingest_zipkin_corrupt:...:WrongFormat``."""
    warnings: list[str] = []
    results = ingest_zipkin_trace(db_conn, otel_clean, warnings_out=warnings)

    assert results == []
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_zipkin_corrupt:"), msg
    assert "WrongFormat" in msg, msg


# ===========================================================================
# (3) Empty-records cold-trace sentinel — each reader emits ``_empty:``
# ===========================================================================


def test_empty_otel_emits_empty_marker(db_conn, tmp_path) -> None:
    """An OTel doc with ``resourceSpans: []`` emits ``trace_ingest_otel_empty:``.

    Legitimate cold-trace sentinel: the OTel agent is running but no
    traffic has been received yet. The marker is informational so a
    programmatic caller can distinguish "zero spans because no
    traffic" from "zero spans because file was misrouted".
    """
    doc = {"resourceSpans": []}
    p = tmp_path / "otel_empty.json"
    p.write_text(json.dumps(doc), encoding="utf-8")

    warnings: list[str] = []
    results = ingest_otel_trace(db_conn, str(p), warnings_out=warnings)

    assert results == []
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_otel_empty:"), msg


def test_empty_jaeger_emits_empty_marker(db_conn, tmp_path) -> None:
    """A Jaeger doc with empty ``data`` emits ``trace_ingest_jaeger_empty:``."""
    doc = {"data": []}
    p = tmp_path / "jaeger_empty.json"
    p.write_text(json.dumps(doc), encoding="utf-8")

    warnings: list[str] = []
    results = ingest_jaeger_trace(db_conn, str(p), warnings_out=warnings)

    assert results == []
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_jaeger_empty:"), msg


def test_empty_zipkin_emits_empty_marker(db_conn, tmp_path) -> None:
    """An empty Zipkin list emits ``trace_ingest_zipkin_empty:``."""
    p = tmp_path / "zipkin_empty.json"
    p.write_text("[]", encoding="utf-8")

    warnings: list[str] = []
    results = ingest_zipkin_trace(db_conn, str(p), warnings_out=warnings)

    assert results == []
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_zipkin_empty:"), msg


def test_empty_generic_emits_empty_marker(db_conn, tmp_path) -> None:
    """An empty generic list emits ``trace_ingest_generic_empty:``.

    Generic stays LOUD on wrong-shape (raises ``ValueError``); the
    empty-list case is the only post-parse silent-empty path and
    surfaces via the warnings bucket as a legitimate cold-trace
    sentinel.
    """
    p = tmp_path / "generic_empty.json"
    p.write_text("[]", encoding="utf-8")

    warnings: list[str] = []
    results = ingest_generic_trace(db_conn, str(p), warnings_out=warnings)

    assert results == []
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_generic_empty:"), msg


# ===========================================================================
# (4) Generic wrong-shape stays LOUD (pre-W599 discipline preserved)
# ===========================================================================


def test_generic_wrong_shape_still_raises(db_conn, tmp_path) -> None:
    """Generic wrong-shape (non-list) STILL raises ``ValueError``.

    Pre-W599 discipline preserved: ``ingest_generic_trace`` is the one
    reader that raises on wrong-shape rather than silently returning
    ``[]``. The ``ValueError`` is the documented signal for "this is
    not a generic trace doc, use auto_detect_format to route". W599
    does NOT convert this into a silent-empty + warning — the
    pre-existing loud behaviour is the correct discipline.
    """
    p = tmp_path / "generic_wrong.json"
    p.write_text('{"not": "a list"}', encoding="utf-8")

    warnings: list[str] = []
    with pytest.raises(ValueError, match="must be a JSON list"):
        ingest_generic_trace(db_conn, str(p), warnings_out=warnings)


# ===========================================================================
# (5) OSError / JSONDecodeError still raise loudly (pre-W599 discipline)
# ===========================================================================


def test_oserror_still_raises_in_all_readers(db_conn, tmp_path) -> None:
    """Missing file still raises ``OSError`` (pre-W599 discipline preserved).

    W978 first-hypothesis finding: the trace-ingest readers are
    already LOUD on the read-failure axis. The CLI bridge in
    ``cmd_ingest_trace`` catches the ``OSError`` and produces a
    structured parse-error envelope. W599 does NOT convert this into
    a silent-None path with a ``..._read_failed:`` marker — there is
    no silent-None path to plumb.
    """
    missing = str(tmp_path / "does-not-exist.json")
    warnings: list[str] = []

    with pytest.raises(OSError):
        ingest_otel_trace(db_conn, missing, warnings_out=warnings)
    with pytest.raises(OSError):
        ingest_jaeger_trace(db_conn, missing, warnings_out=warnings)
    with pytest.raises(OSError):
        ingest_zipkin_trace(db_conn, missing, warnings_out=warnings)
    with pytest.raises(OSError):
        ingest_generic_trace(db_conn, missing, warnings_out=warnings)


def test_jsondecodeerror_still_raises(db_conn, tmp_path) -> None:
    """Corrupt JSON still raises ``JSONDecodeError`` — same discipline as W599 OSError finding.

    The CLI bridge handles this. W599 does NOT introduce silent-None
    for malformed JSON.
    """
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json", encoding="utf-8")

    warnings: list[str] = []
    with pytest.raises(json.JSONDecodeError):
        ingest_otel_trace(db_conn, str(p), warnings_out=warnings)


# ===========================================================================
# (6) auto_detect_format fallback to "generic" emits a marker
# ===========================================================================


def test_auto_detect_fallback_generic_emits_marker(tmp_path) -> None:
    """An unrecognised shape silently falls back to ``"generic"`` — now disclosed.

    Pattern 2 "silent fallback" — before W599, a doc that looked like
    none of OTel/Jaeger/Zipkin/generic-by-key shapes silently returned
    ``"generic"`` and the downstream ``ingest_generic_trace`` would
    then RAISE ``ValueError``. The fallback itself is silent though —
    W599 makes it disclosable on the warnings bucket.
    """
    # A JSON list of dicts with neither traceId/id (Zipkin) nor
    # function key (generic). Auto-detect falls back to generic.
    p = tmp_path / "ambiguous.json"
    p.write_text(json.dumps([{"unknown": "shape"}]), encoding="utf-8")

    warnings: list[str] = []
    fmt = auto_detect_format(str(p), warnings_out=warnings)

    assert fmt == "generic", f"expected fallback to 'generic'; got {fmt!r}"
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("trace_ingest_auto_detect_fallback_generic:"), msg


def test_auto_detect_recognized_emits_no_warning(otel_clean, jaeger_clean, zipkin_clean) -> None:
    """A recognised shape emits no fallback warning."""
    for path, expected in (
        (otel_clean, "otel"),
        (jaeger_clean, "jaeger"),
        (zipkin_clean, "zipkin"),
    ):
        warnings: list[str] = []
        fmt = auto_detect_format(path, warnings_out=warnings)
        assert fmt == expected, f"{path}: expected {expected}; got {fmt!r}"
        assert warnings == [], f"{path}: recognised shape must not warn; got {warnings!r}"


# ===========================================================================
# (7) Default warnings_out=None preserves silent behaviour, no crash
# ===========================================================================


def test_default_none_no_crash(db_conn, otel_clean, jaeger_clean, zipkin_clean, generic_clean) -> None:
    """Default ``warnings_out=None`` returns results cleanly with no crash.

    Existing callers (``cmd_ingest_trace.ingest_trace`` at
    cmd_ingest_trace.py:165 + the ``test_runtime.py`` suite) call the
    ingesters with no kwargs — they must NOT regress on any failure
    mode covered by the W599 plumb.
    """
    # All four happy-path readers, default-None, no warnings bucket.
    assert len(ingest_otel_trace(db_conn, otel_clean)) == 1
    assert len(ingest_jaeger_trace(db_conn, jaeger_clean)) == 1
    assert len(ingest_zipkin_trace(db_conn, zipkin_clean)) == 1
    assert len(ingest_generic_trace(db_conn, generic_clean)) == 1
    # auto_detect default-None.
    assert auto_detect_format(otel_clean) == "otel"


def test_default_none_empty_path_no_crash(db_conn, tmp_path) -> None:
    """Default-None on the empty-records silent-empty path returns ``[]`` without crash."""
    p = tmp_path / "otel_empty.json"
    p.write_text('{"resourceSpans": []}', encoding="utf-8")
    # No warnings bucket passed; the marker site is skipped silently.
    assert ingest_otel_trace(db_conn, str(p)) == []


def test_default_none_wrong_shape_no_crash(db_conn, jaeger_clean) -> None:
    """Default-None on the wrong-shape silent-empty path returns ``[]`` without crash."""
    # No warnings bucket; silent-empty preserved.
    assert ingest_otel_trace(db_conn, jaeger_clean) == []


# ===========================================================================
# (8) Per-format symmetry: all four readers + auto_detect share the kw-only signature
# ===========================================================================


def test_per_format_symmetry_signature() -> None:
    """AST-check all four ingesters + auto_detect declare ``warnings_out`` kw-only.

    Per-format symmetry guard. If a new format-specific reader is added
    later (e.g. ``ingest_protobuf_trace``), this test will not see it
    — but it pins the W599 contract for the four readers that exist
    today. The closed-enum vocabulary (``trace_ingest_<format>_``)
    establishes a discoverable prefix for future siblings.
    """
    src_path = repo_root() / "src" / "roam" / "runtime" / "trace_ingest.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    expected_fns = {
        "ingest_otel_trace",
        "ingest_jaeger_trace",
        "ingest_zipkin_trace",
        "ingest_generic_trace",
        "auto_detect_format",
    }
    found_fns: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in expected_fns:
            found_fns[node.name] = node
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in expected_fns:
            raise AssertionError(f"{node.name} became async — W599 must not change the synchronous-call contract")

    missing = expected_fns - set(found_fns.keys())
    assert not missing, f"expected to find {expected_fns}; missing {missing}"

    for name, node in found_fns.items():
        # No yields — readers must remain non-generator.
        for child in ast.walk(node):
            if isinstance(child, (ast.Yield, ast.YieldFrom)):
                raise AssertionError(f"{name} contains a yield — W599 must not turn an ingester into a generator")
        kwonly_names = [a.arg for a in node.args.kwonlyargs]
        assert "warnings_out" in kwonly_names, (
            f"{name} must declare ``warnings_out`` as a kw-only parameter (got kwonly={kwonly_names!r})"
        )


# ===========================================================================
# (9) Caller-side audit: cmd_ingest_trace caller is unmodified
# ===========================================================================


def test_callers_unmodified() -> None:
    """AST-check that the CLI caller in cmd_ingest_trace.py is unchanged.

    The single live caller of the four ingesters is
    ``cmd_ingest_trace.ingest_trace`` at cmd_ingest_trace.py:165 which
    invokes ``ingester(conn, path)`` with positional args only. W599
    is read-only and additive — the caller does NOT thread
    ``warnings_out``. The audit confirms by AST:

      * the call site at cmd_ingest_trace.py invokes the resolved
        ingester via positional args only (no ``warnings_out`` kwarg).

    A future refactor can opt the caller into threading the bucket;
    this test pins the current "audit-only, caller unmodified"
    contract.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_ingest_trace.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    # Find the ``ingester(conn, path)`` call by walking for any call
    # whose target name is ``ingester`` (the local rebind).
    ingester_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            if name == "ingester":
                ingester_calls.append(node)

    assert len(ingester_calls) >= 1, (
        f"expected at least one ``ingester(...)`` call in cmd_ingest_trace.py; found {len(ingester_calls)}"
    )
    for call in ingester_calls:
        kwarg_names = [kw.arg for kw in call.keywords if kw.arg is not None]
        assert "warnings_out" not in kwarg_names, (
            f"caller at cmd_ingest_trace.py:{call.lineno} now threads warnings_out; "
            f"W599 was audit-only — update this test if intentionally opted in."
        )


def test_auto_detect_caller_unmodified() -> None:
    """The ``auto_detect_format(trace_file)`` call site is unchanged.

    ``cmd_ingest_trace.ingest_trace`` calls ``auto_detect_format`` at
    cmd_ingest_trace.py:137 and catches the wrapped ``ValueError``.
    W599's plumb is additive — the caller does not thread the bucket.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_ingest_trace.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "auto_detect_format":
                calls.append(node)

    assert len(calls) >= 1, f"expected at least one auto_detect_format call in cmd_ingest_trace.py; found {len(calls)}"
    for call in calls:
        kwarg_names = [kw.arg for kw in call.keywords if kw.arg is not None]
        assert "warnings_out" not in kwarg_names, (
            f"caller at cmd_ingest_trace.py:{call.lineno} now threads warnings_out; "
            f"W599 was audit-only — update this test if intentionally opted in."
        )


# ===========================================================================
# (10) match_trace_to_symbol / _upsert_runtime_stat untouched — read-only scope
# ===========================================================================


def test_unrelated_helpers_untouched() -> None:
    """W599 is read-only on the four ingesters + auto_detect. The matchers
    and upsert helpers (``match_trace_to_symbol``, ``_upsert_runtime_stat``,
    ``ensure_runtime_table``, ``_percentile``, ``_attr_value``,
    ``_statement_type``) are NOT plumbed — they don't have silent-empty
    behaviour at the file-read boundary.
    """
    src_path = repo_root() / "src" / "roam" / "runtime" / "trace_ingest.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    untouched = {
        "match_trace_to_symbol",
        "_upsert_runtime_stat",
        "ensure_runtime_table",
        "_percentile",
        "_attr_value",
        "_statement_type",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in untouched:
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" not in kwonly_names, (
                f"{node.name} must NOT thread warnings_out — W599 scope is the "
                f"file-read ingesters only; helper plumb is a separate wave"
            )
