"""Cross-repo API edge detection: scan for REST endpoints and match them."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Regex to extract URL path from a source line
_URL_RE = re.compile(r"""[('"`](/[a-zA-Z0-9/_\-{}.]+)[)'"`]""")

# HTTP methods for frontend call detection
_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}

# Frontend API call patterns (method names that indicate HTTP calls)
_FRONTEND_CALL_NAMES = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "fetch",
    "$fetch",
    "useFetch",
    "useLazyFetch",
    "request",
    "axios",
}

# Backend route definition patterns per framework
_BACKEND_ROUTE_RE = re.compile(
    r"""Route\s*::\s*(get|post|put|delete|patch|any|match|resource|apiResource)"""
    r"""\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Express/Fastify style: router.get('/path', ...) or app.post('/path', ...)
_EXPRESS_ROUTE_RE = re.compile(
    r"""(?:router|app|server)\s*\.\s*(get|post|put|delete|patch|all)"""
    r"""\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# FastAPI/Flask style: @app.get('/path') or @router.post('/path')
_PYTHON_ROUTE_RE = re.compile(
    r"""@\s*(?:app|router)\s*\.\s*(get|post|put|delete|patch)"""
    r"""\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

_RouteRecordBuilder = Callable[[re.Match[str], str, int], dict[str, Any]]


@dataclass(frozen=True)
class _BackendRouteLookup:
    routes: list[dict[str, Any]]
    exact_route_ids: dict[str, set[int]]
    route_ids_by_length: dict[int, set[int]]
    route_ids_by_segment: dict[tuple[int, str], set[int]]
    route_ids_by_prefix: dict[str, set[int]]
    route_ids_by_method: dict[str, set[int]]
    methodless_route_ids: set[int]


def scan_frontend_api_calls(repo_db_path: Path, repo_root: Path) -> list[dict[str, Any]]:
    """Scan a frontend repo DB for API call sites.

    Looks for references to HTTP methods (get/post/etc.) and extracts
    URL patterns from the corresponding source lines.

    **Issue #19 guard**: ``app.get('/x', handler)`` / ``router.post(...)`` /
    ``Route::get(...)`` / ``@app.get(...)`` are route *definitions*, not
    client-side API calls — even if the file lives in a repo tagged as
    ``frontend``. Polyglot monorepos with both UI and server code in the
    same repo would otherwise generate false-positive cross-repo edges.
    Source lines that match any of the backend route regexes (or whose
    receiver is one of ``{app, router, server}``) are skipped.

    Returns a list of dicts: {symbol_id, url_pattern, http_method,
    file_path, line, symbol_name}
    """
    if not repo_db_path.exists():
        return []

    conn = sqlite3.connect(str(repo_db_path), timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        results = _scan_edge_calls(conn, repo_root)
        _scan_regex_supplement(conn, repo_root, results)
    finally:
        conn.close()

    return results


def _edge_row_to_call(row: sqlite3.Row, repo_root: Path) -> dict[str, Any] | None:
    """Convert one HTTP-method edge row into an API-call dict, or None to skip."""
    method = row["target_name"].lower()
    if method in ("fetch", "$fetch", "usefetch", "uselazyfetch", "request", "axios"):
        http_method = None  # determined from context
    else:
        http_method = method.upper()

    line_num = row["line"]
    file_path = row["file_path"]

    # Issue #19 guard: skip route-definition syntax that looks
    # like a client call.
    if _looks_like_route_definition(repo_root / file_path, line_num):
        return None

    # Read the source line to extract URL
    url = _extract_url_from_source(repo_root / file_path, line_num)
    if not url:
        return None

    # For fetch-like calls, try to infer method from context
    if http_method is None:
        http_method = _infer_method_from_context(repo_root / file_path, line_num)

    return {
        "symbol_id": row["source_id"],
        "url_pattern": url,
        "http_method": http_method or "GET",
        "file_path": file_path,
        "line": line_num,
        "symbol_name": row["source_name"],
    }


def _scan_edge_calls(conn: sqlite3.Connection, repo_root: Path) -> list[dict[str, Any]]:
    """Find API calls via edges whose target is an HTTP-method-named symbol."""
    rows = conn.execute(
        "SELECT e.source_id, e.target_id, e.line, e.kind, "
        "  s.name AS source_name, s.file_id AS source_file_id, "
        "  t.name AS target_name, "
        "  f.path AS file_path "
        "FROM edges e "
        "JOIN symbols s ON s.id = e.source_id "
        "JOIN symbols t ON t.id = e.target_id "
        "JOIN files f ON f.id = s.file_id "
        "WHERE LOWER(t.name) IN ({ph})".format(ph=",".join("?" for _ in _FRONTEND_CALL_NAMES)),
        list(_FRONTEND_CALL_NAMES),
    ).fetchall()

    results = []
    for row in rows:
        call = _edge_row_to_call(row, repo_root)
        if call is not None:
            results.append(call)
    return results


def _scan_regex_supplement(conn: sqlite3.Connection, repo_root: Path, results: list[dict[str, Any]]) -> None:
    """Regex-scan frontend files for API-path literals the edge scan missed.

    Catches patterns like ``api.get('/transactions/save')``. Appends new
    calls to *results* in place, deduplicating by (file_path, line).
    """
    file_rows = conn.execute(
        "SELECT id, path FROM files WHERE language IN ('typescript', 'javascript', 'vue', 'tsx', 'jsx')"
    ).fetchall()

    seen_urls = {(r["file_path"], r["line"]) for r in results}
    for file_row in file_rows:
        fpath = repo_root / file_row["path"]
        if not fpath.exists():
            continue
        _append_new_file_calls(conn, file_row, fpath, seen_urls, results)


def _append_new_file_calls(
    conn: sqlite3.Connection,
    file_row: sqlite3.Row,
    fpath: Path,
    seen_urls: set[tuple[str, int]],
    results: list[dict[str, Any]],
) -> None:
    """Append one file's regex-detected calls not already in *seen_urls*."""
    for call in _scan_file_for_api_calls(fpath, file_row["path"]):
        key = (call["file_path"], call["line"])
        if key in seen_urls:
            continue
        sym = _enclosing_symbol(conn, file_row["id"], call["line"])
        call["symbol_id"] = sym["id"] if sym else 0
        call["symbol_name"] = sym["name"] if sym else ""
        results.append(call)
        seen_urls.add(key)


def _enclosing_symbol(conn: sqlite3.Connection, file_id: int, line: int) -> sqlite3.Row | None:
    """Find the innermost symbol containing *line* in the given file."""
    return conn.execute(
        "SELECT id, name FROM symbols "
        "WHERE file_id=? AND line_start<=? AND "
        "(line_end>=? OR line_end IS NULL) "
        "ORDER BY line_start DESC LIMIT 1",
        (file_id, line, line),
    ).fetchone()


def scan_backend_routes(repo_db_path: Path, repo_root: Path) -> list[dict[str, Any]]:
    """Scan a backend repo for route definitions.

    Supports Laravel (Route::get), Express (router.get), and
    FastAPI (@app.get) patterns.

    Returns a list of dicts: {symbol_id, url_pattern, http_method,
    file_path, line, symbol_name}
    """
    if not repo_db_path.exists():
        return []

    conn = sqlite3.connect(str(repo_db_path), timeout=30)
    conn.row_factory = sqlite3.Row

    results = []
    try:
        # Scan all PHP, Python, JS/TS files for route definitions
        file_rows = conn.execute(
            "SELECT id, path, language FROM files WHERE language IN ('php', 'python', 'javascript', 'typescript')"
        ).fetchall()

        for file_row in file_rows:
            fpath = repo_root / file_row["path"]
            if not fpath.exists():
                continue
            routes = _scan_file_for_routes(fpath, file_row["path"])
            for route in routes:
                # Find the handler symbol
                sym = _enclosing_symbol(conn, file_row["id"], route["line"])
                route["symbol_id"] = sym["id"] if sym else 0
                route["symbol_name"] = sym["name"] if sym else ""
                results.append(route)
    finally:
        conn.close()

    return results


def match_api_endpoints(
    frontend_calls: list[dict[str, Any]],
    backend_routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match frontend API calls to backend route definitions.

    Normalizes URL patterns and matches by path + HTTP method.

    Returns a list of matched pairs: {frontend: {...}, backend: {...},
    url_pattern, http_method, score}
    """
    backend_lookup = _backend_route_lookup_for_locality(backend_routes)

    matches = []
    for call in frontend_calls:
        matches.extend(_matches_preserving_method_fidelity(call, backend_lookup))

    return _dedupe_endpoint_matches_by_best_score(matches)


def _matches_preserving_method_fidelity(
    call: dict[str, Any], backend_lookup: _BackendRouteLookup
) -> list[dict[str, Any]]:
    """Score only route-local candidates that pass the method compatibility gate."""
    normalized = _normalize_url(call["url_pattern"])
    path_candidate_ids = _route_candidate_ids_without_backend_scan(normalized, backend_lookup)
    candidate_ids = _route_ids_that_preserve_method_fidelity(call, path_candidate_ids, backend_lookup)

    matches: list[dict[str, Any]] = []
    for route_id in sorted(candidate_ids):
        candidate = backend_lookup.routes[route_id]
        score = _match_score(
            call["url_pattern"],
            candidate["url_pattern"],
            call.get("http_method"),
            candidate.get("http_method"),
        )
        matches.append(
            {
                "frontend": call,
                "backend": candidate,
                "url_pattern": call["url_pattern"],
                "http_method": call.get("http_method", candidate.get("http_method", "")),
                "score": score,
            }
        )
    return matches


def _dedupe_endpoint_matches_by_best_score(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-scoring endpoint match for each frontend/backend pair."""
    seen: dict[tuple, dict] = {}
    for m in matches:
        key = (
            m["frontend"]["file_path"],
            m["frontend"]["line"],
            m["backend"]["file_path"],
            m["backend"]["line"],
        )
        if key not in seen or m["score"] > seen[key]["score"]:
            seen[key] = m

    return sorted(seen.values(), key=lambda m: m["score"], reverse=True)


def _call_keys_with_backend_evidence(matches: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Return frontend call keys that already have at least one route match."""
    matched_call_keys: set[tuple[str, int]] = set()
    for match in matches:
        frontend = match.get("frontend", {})
        key = (frontend.get("file_path", ""), frontend.get("line", 0))
        matched_call_keys.add(key)
    return matched_call_keys


def _reason_preserving_route_locality(
    call: dict[str, Any],
    normalized_url: str,
    call_method: str,
    backend_lookup: _BackendRouteLookup,
) -> tuple[str, str]:
    """Classify an unmatched call without scanning every backend route."""
    path_candidates = _route_candidates_without_backend_scan(normalized_url, backend_lookup)
    if path_candidates:
        backend_methods = sorted(
            {(r.get("http_method") or "").upper() for r in path_candidates if r.get("http_method")}
        )
        method_list = ", ".join(backend_methods) if backend_methods else "?"
        return (
            "method_mismatch",
            f"backend route `{call['url_pattern']}` exists but accepts {method_list}, not {call_method or '?'}",
        )

    segs = _url_segments(normalized_url)
    sample = _same_prefix_route_for_path_shape_mismatch(segs, backend_lookup)
    if sample is not None:
        prefix_key = "/" + segs[0]
        return (
            "path_variable_mismatch",
            f"no backend route matches `{call['url_pattern']}` for method "
            f"{call_method or '?'}; closest sibling under `{prefix_key}` "
            f"is `{sample['url_pattern']}`",
        )

    return (
        "unknown_path",
        f"no backend route matches `{call['url_pattern']}` for method {call_method or '?'}",
    )


def find_unmatched_calls(
    frontend_calls: list[dict[str, Any]],
    backend_routes: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return frontend calls that did not match any backend route.

    Each entry is a dict with keys ``frontend_file``, ``url``, ``method``,
    ``symbol_name``, ``line``, and ``reason``. The ``reason`` field
    classifies *why* the call is unmatched, drawn from a closed enum:

    - ``"method_mismatch"`` — URL path matches a backend route but the
      HTTP method differs.
    - ``"unknown_path"`` — URL path does not match any backend route,
      and no backend route shares the same prefix (top URL segment).
    - ``"path_variable_mismatch"`` — Path's leading segment matches at
      least one backend route, but no full path (including variable
      slots) lines up.

    Unmatched is computed against the *matched-pair* list rather than
    re-running the matcher, so the input/output contract of
    :func:`match_api_endpoints` is unchanged.
    """
    if not frontend_calls:
        return []

    matched_call_keys = _call_keys_with_backend_evidence(matches)
    backend_lookup = _backend_route_lookup_for_locality(backend_routes)

    unmatched: list[dict[str, Any]] = []
    for call in frontend_calls:
        key = (call.get("file_path", ""), call.get("line", 0))
        if key in matched_call_keys:
            continue

        normalized = _normalize_url(call["url_pattern"])
        call_method = (call.get("http_method") or "").upper()

        reason, reason_detail = _reason_preserving_route_locality(
            call,
            normalized,
            call_method,
            backend_lookup,
        )

        unmatched.append(
            {
                "frontend_file": call.get("file_path", ""),
                "url": call["url_pattern"],
                "method": call_method or "",
                "symbol_name": call.get("symbol_name", ""),
                "line": call.get("line", 0),
                "reason": reason,
                "reason_detail": reason_detail,
            }
        )

    # Stable ordering: by file then line.
    unmatched.sort(key=lambda u: (u["frontend_file"], u["line"]))
    return unmatched


def build_cross_repo_edges(
    ws_conn: sqlite3.Connection,
    frontend_repo_id: int,
    backend_repo_id: int,
    matched: list[dict[str, Any]],
) -> int:
    """Store matched API endpoints as cross-repo edges.

    Returns the number of edges stored.
    """
    count = 0
    for m in matched:
        fe = m["frontend"]
        be = m["backend"]

        # Store route symbols for frontend call
        ws_conn.execute(
            "INSERT INTO ws_route_symbols "
            "(repo_id, symbol_id, url_pattern, http_method, kind, "
            " file_path, line, symbol_name) "
            "VALUES (?, ?, ?, ?, 'api_call', ?, ?, ?)",
            (
                frontend_repo_id,
                fe["symbol_id"],
                fe["url_pattern"],
                m.get("http_method", ""),
                fe["file_path"],
                fe["line"],
                fe["symbol_name"],
            ),
        )

        # Store route symbols for backend route
        ws_conn.execute(
            "INSERT INTO ws_route_symbols "
            "(repo_id, symbol_id, url_pattern, http_method, kind, "
            " file_path, line, symbol_name) "
            "VALUES (?, ?, ?, ?, 'route_definition', ?, ?, ?)",
            (
                backend_repo_id,
                be["symbol_id"],
                be["url_pattern"],
                m.get("http_method", ""),
                be["file_path"],
                be["line"],
                be["symbol_name"],
            ),
        )

        # Store cross-repo edge
        metadata = json.dumps(
            {
                "url_pattern": m["url_pattern"],
                "http_method": m.get("http_method", ""),
                "score": m["score"],
            }
        )
        ws_conn.execute(
            "INSERT INTO ws_cross_edges "
            "(source_repo_id, source_symbol_id, target_repo_id, "
            " target_symbol_id, kind, metadata) "
            "VALUES (?, ?, ?, ?, 'api_call', ?)",
            (frontend_repo_id, fe["symbol_id"], backend_repo_id, be["symbol_id"], metadata),
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_ROUTE_HANDLER_RECEIVERS = ("app", "router", "server", "fastify", "express")
# Note: ``api`` is intentionally NOT here — ``api.get('/users')`` is the
# canonical client-call shape. Server frameworks consistently use one of
# the above receiver names.


def _url_segments(normalized_url: str) -> list[str]:
    return [segment for segment in normalized_url.split("/") if segment]


def _backend_route_lookup_for_locality(backend_routes: list[dict[str, Any]]) -> _BackendRouteLookup:
    """Index route shape once so fuzzy matching stays local to set lookups."""
    routes = list(backend_routes)
    exact_route_ids: dict[str, set[int]] = {}
    route_ids_by_length: dict[int, set[int]] = {}
    route_ids_by_segment: dict[tuple[int, str], set[int]] = {}
    route_ids_by_prefix: dict[str, set[int]] = {}
    route_ids_by_method: dict[str, set[int]] = {}
    methodless_route_ids: set[int] = set()

    for route_id, route in enumerate(routes):
        normalized = _normalize_url(route["url_pattern"])
        segments = _url_segments(normalized)
        method = (route.get("http_method") or "").upper()
        exact_route_ids.setdefault(normalized, set()).add(route_id)
        route_ids_by_length.setdefault(len(segments), set()).add(route_id)
        if segments:
            route_ids_by_prefix.setdefault(segments[0], set()).add(route_id)
        for position, segment in enumerate(segments):
            route_ids_by_segment.setdefault((position, segment), set()).add(route_id)
        if method:
            route_ids_by_method.setdefault(method, set()).add(route_id)
        else:
            methodless_route_ids.add(route_id)

    return _BackendRouteLookup(
        routes=routes,
        exact_route_ids=exact_route_ids,
        route_ids_by_length=route_ids_by_length,
        route_ids_by_segment=route_ids_by_segment,
        route_ids_by_prefix=route_ids_by_prefix,
        route_ids_by_method=route_ids_by_method,
        methodless_route_ids=methodless_route_ids,
    )


def _route_candidate_ids_without_backend_scan(
    normalized_url: str, backend_lookup: _BackendRouteLookup
) -> set[int]:
    """Preserve fuzzy URL coverage without comparing against every route."""
    exact_ids = backend_lookup.exact_route_ids.get(normalized_url, set())
    if exact_ids:
        return set(exact_ids)

    segments = _url_segments(normalized_url)
    candidate_ids = set(backend_lookup.route_ids_by_length.get(len(segments), set()))
    for position, segment in enumerate(segments):
        if segment == "[*]":
            continue
        segment_matches = backend_lookup.route_ids_by_segment.get((position, segment), set())
        wildcard_matches = backend_lookup.route_ids_by_segment.get((position, "[*]"), set())
        candidate_ids &= segment_matches | wildcard_matches
        if not candidate_ids:
            return set()

    return candidate_ids


def _route_candidates_without_backend_scan(
    normalized_url: str, backend_lookup: _BackendRouteLookup
) -> list[dict[str, Any]]:
    """Preserve fuzzy URL coverage without comparing against every route."""
    route_ids = _route_candidate_ids_without_backend_scan(normalized_url, backend_lookup)
    return [backend_lookup.routes[route_id] for route_id in sorted(route_ids)]


def _route_ids_that_preserve_method_fidelity(
    call: dict[str, Any],
    path_candidate_ids: set[int],
    backend_lookup: _BackendRouteLookup,
) -> set[int]:
    """Keep route-local candidates compatible with the call's HTTP method."""
    call_method = (call.get("http_method") or "").upper()
    if not call_method:
        return path_candidate_ids

    method_route_ids = set(backend_lookup.route_ids_by_method.get(call_method, set()))
    method_route_ids.update(backend_lookup.methodless_route_ids)
    return path_candidate_ids & method_route_ids


def _same_prefix_route_for_path_shape_mismatch(
    segments: list[str], backend_lookup: _BackendRouteLookup
) -> dict[str, Any] | None:
    """Find a sibling route that explains path-shape mismatches without scanning."""
    if not segments:
        return None
    route_ids = backend_lookup.route_ids_by_prefix.get(segments[0])
    if not route_ids:
        return None
    return backend_lookup.routes[min(route_ids)]


def _looks_like_route_definition(file_path: Path, line_num: int | None) -> bool:
    """Return True when the source line at *line_num* defines a server route.

    Mirrors the checks in :func:`_scan_file_for_routes` but at a single
    line, so the per-edge frontend scanner can short-circuit before
    creating a false-positive API call. Conservative — when in doubt
    we keep the edge (returns False) so we don't suppress real calls.
    """
    if line_num is None:
        return False
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line_num <= 0 or line_num > len(text):
            return False
        line = text[line_num - 1]
    except (OSError, UnicodeDecodeError):
        return False

    if _EXPRESS_ROUTE_RE.search(line):
        return True
    if _PYTHON_ROUTE_RE.search(line):
        return True
    if _BACKEND_ROUTE_RE.search(line):
        return True

    # Receiver-name heuristic: `app.get(...)` / `router.post(...)` etc.
    # The body before the dot is the receiver. We're conservative and
    # only flag well-known server receivers.
    receiver_match = re.search(
        r"\b(" + "|".join(_ROUTE_HANDLER_RECEIVERS) + r")\s*\.\s*"
        r"(get|post|put|delete|patch)\s*\(",
        line,
        re.IGNORECASE,
    )
    if receiver_match:
        # Server receivers commonly take a 2nd argument that's a callable.
        # The presence of `, function` / `, async function` / `, () =>`
        # / `, async () =>` / `, handler` tightens the precision.
        if re.search(r",\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>|[a-zA-Z_]\w*)\s*[){]", line):
            return True
        # Even without a callable in the same line (multi-line handlers),
        # a receiver of `app|router|server` plus a path argument is a
        # strong route signal.
        return True

    return False


def _extract_url_from_source(file_path: Path, line_num: int | None) -> str | None:
    """Read a source line and extract a URL pattern."""
    if line_num is None:
        return None
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line_num <= 0 or line_num > len(lines):
            return None
        line = lines[line_num - 1]
        m = _URL_RE.search(line)
        return m.group(1) if m else None
    except (OSError, UnicodeDecodeError):
        return None


def _infer_method_from_context(file_path: Path, line_num: int | None) -> str | None:
    """Try to infer HTTP method from surrounding lines."""
    if line_num is None:
        return None
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, (line_num or 1) - 3)
        end = min(len(lines), (line_num or 1) + 2)
        context = " ".join(lines[start:end]).lower()
        for method in ("post", "put", "delete", "patch"):
            if f"method: '{method}'" in context or f'method: "{method}"' in context:
                return method.upper()
            if f"method:{method}" in context:
                return method.upper()
        return None
    except (OSError, UnicodeDecodeError):
        return None


def _scan_file_for_api_calls(file_path: Path, rel_path: str) -> list[dict[str, Any]]:
    """Scan a source file for API call patterns via regex."""
    results = []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return results

    api_call_re = re.compile(
        r"""(?:api|axios|http|client|\$fetch|useFetch|useLazyFetch|fetch)"""
        r"""\s*\.\s*(get|post|put|delete|patch)\s*\(""",
        re.IGNORECASE,
    )

    for i, line in enumerate(lines, 1):
        m = api_call_re.search(line)
        if not m:
            continue
        http_method = m.group(1).upper()
        url_m = _URL_RE.search(line[m.end() - 1 :])
        if url_m:
            results.append(
                {
                    "url_pattern": url_m.group(1),
                    "http_method": http_method,
                    "file_path": rel_path,
                    "line": i,
                    "symbol_id": 0,
                    "symbol_name": "",
                }
            )

    return results


def _route_record(url_pattern: str, http_method: str, rel_path: str, line_num: int) -> dict[str, Any]:
    return {
        "url_pattern": url_pattern,
        "http_method": http_method,
        "file_path": rel_path,
        "line": line_num,
    }


def _backend_route_record(match: re.Match[str], rel_path: str, line_num: int) -> dict[str, Any]:
    method_raw = match.group(1).lower()
    if method_raw in ("resource", "apiresource"):
        http_method = "RESOURCE"
    elif method_raw in ("any", "match"):
        http_method = "ANY"
    else:
        http_method = method_raw.upper()
    return _route_record(match.group(2), http_method, rel_path, line_num)


def _express_route_record(match: re.Match[str], rel_path: str, line_num: int) -> dict[str, Any]:
    method_raw = match.group(1).lower()
    http_method = "ANY" if method_raw == "all" else method_raw.upper()
    return _route_record(match.group(2), http_method, rel_path, line_num)


def _python_route_record(match: re.Match[str], rel_path: str, line_num: int) -> dict[str, Any]:
    return _route_record(match.group(2), match.group(1).upper(), rel_path, line_num)


_ROUTE_MATCHERS: tuple[tuple[re.Pattern[str], _RouteRecordBuilder], ...] = (
    (_BACKEND_ROUTE_RE, _backend_route_record),
    (_EXPRESS_ROUTE_RE, _express_route_record),
    (_PYTHON_ROUTE_RE, _python_route_record),
)


def _route_record_from_line(line: str, rel_path: str, line_num: int) -> dict[str, Any] | None:
    for route_re, record_builder in _ROUTE_MATCHERS:
        match = route_re.search(line)
        if match is None:
            continue
        return record_builder(match, rel_path, line_num)
    return None


def _scan_file_for_routes(file_path: Path, rel_path: str) -> list[dict[str, Any]]:
    """Scan a source file for route definition patterns."""
    results = []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return results

    for i, line in enumerate(lines, 1):
        route = _route_record_from_line(line, rel_path, i)
        if route is not None:
            results.append(route)

    return results


def _normalize_url(url: str) -> str:
    """Normalize a URL pattern for matching.

    Strips common API prefixes and converts parameter placeholders
    to a common form.
    """
    # Strip leading /api/ or /api/v1/ etc.
    normalized = re.sub(r"^/api(?:/v\d+)?", "", url)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    # Convert {param}, ${param}, :param to [*]
    normalized = re.sub(r"\$?\{[^}]+\}", "[*]", normalized)
    normalized = re.sub(r"/:([a-zA-Z_]\w*)", "/[*]", normalized)
    # Strip trailing slash
    normalized = normalized.rstrip("/") or "/"
    return normalized.lower()


def _urls_equivalent(a: str, b: str) -> bool:
    """Check if two normalized URLs are equivalent."""
    if a == b:
        return True
    # Split into segments and compare
    seg_a = [s for s in a.split("/") if s]
    seg_b = [s for s in b.split("/") if s]
    if len(seg_a) != len(seg_b):
        return False
    for sa, sb in zip(seg_a, seg_b):
        if sa == sb:
            continue
        if sa == "[*]" or sb == "[*]":
            continue
        return False
    return True


def _match_score(fe_url: str, be_url: str, fe_method: str | None, be_method: str | None) -> float:
    """Score the quality of a URL match (0.0 - 1.0)."""
    score = 0.5  # base score for a match

    # Exact URL match bonus
    if _normalize_url(fe_url) == _normalize_url(be_url):
        score += 0.3

    # Method match bonus
    if fe_method and be_method:
        if fe_method.upper() == be_method.upper():
            score += 0.2
        elif be_method.upper() in ("ANY", "RESOURCE"):
            score += 0.1

    # Segment count similarity
    fe_segs = [s for s in fe_url.split("/") if s]
    be_segs = [s for s in be_url.split("/") if s]
    if len(fe_segs) == len(be_segs):
        score += 0.05

    return min(score, 1.0)
