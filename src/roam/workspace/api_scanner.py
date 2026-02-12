"""Cross-repo API edge detection: scan for REST endpoints and match them."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

# Regex to extract URL path from a source line
_URL_RE = re.compile(r"""[('"`](/[a-zA-Z0-9/_\-{}.]+)[)'"`]""")

# HTTP methods for frontend call detection
_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}

# Frontend API call patterns (method names that indicate HTTP calls)
_FRONTEND_CALL_NAMES = {
    "get", "post", "put", "delete", "patch",
    "fetch", "$fetch", "useFetch", "useLazyFetch",
    "request", "axios",
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


def scan_frontend_api_calls(repo_db_path: Path,
                            repo_root: Path) -> list[dict[str, Any]]:
    """Scan a frontend repo DB for API call sites.

    Looks for references to HTTP methods (get/post/etc.) and extracts
    URL patterns from the corresponding source lines.

    Returns a list of dicts: {symbol_id, url_pattern, http_method,
    file_path, line, symbol_name}
    """
    if not repo_db_path.exists():
        return []

    conn = sqlite3.connect(str(repo_db_path), timeout=30)
    conn.row_factory = sqlite3.Row

    results = []
    try:
        # Find references to HTTP method calls
        rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.line, e.kind, "
            "  s.name AS source_name, s.file_id AS source_file_id, "
            "  t.name AS target_name, "
            "  f.path AS file_path "
            "FROM edges e "
            "JOIN symbols s ON s.id = e.source_id "
            "JOIN symbols t ON t.id = e.target_id "
            "JOIN files f ON f.id = s.file_id "
            "WHERE LOWER(t.name) IN ({ph})".format(
                ph=",".join("?" for _ in _FRONTEND_CALL_NAMES)
            ),
            list(_FRONTEND_CALL_NAMES),
        ).fetchall()

        for row in rows:
            method = row["target_name"].lower()
            if method in ("fetch", "$fetch", "usefetch", "uselazyfetch",
                          "request", "axios"):
                http_method = None  # determined from context
            else:
                http_method = method.upper()

            line_num = row["line"]
            file_path = row["file_path"]

            # Read the source line to extract URL
            url = _extract_url_from_source(repo_root / file_path, line_num)
            if not url:
                continue

            # For fetch-like calls, try to infer method from context
            if http_method is None:
                http_method = _infer_method_from_context(
                    repo_root / file_path, line_num
                )

            results.append({
                "symbol_id": row["source_id"],
                "url_pattern": url,
                "http_method": http_method or "GET",
                "file_path": file_path,
                "line": line_num,
                "symbol_name": row["source_name"],
            })

        # Also scan for string literals that look like API paths in source
        # This catches patterns like: api.get('/kiniseis/save')
        file_rows = conn.execute(
            "SELECT id, path FROM files "
            "WHERE language IN ('typescript', 'javascript', 'vue', 'tsx', 'jsx')"
        ).fetchall()

        seen_urls = {(r["file_path"], r["line"]) for r in results}
        for file_row in file_rows:
            fpath = repo_root / file_row["path"]
            if not fpath.exists():
                continue
            calls = _scan_file_for_api_calls(fpath, file_row["path"])
            for call in calls:
                key = (call["file_path"], call["line"])
                if key not in seen_urls:
                    # Try to find the enclosing symbol
                    sym = conn.execute(
                        "SELECT id, name FROM symbols "
                        "WHERE file_id=? AND line_start<=? AND "
                        "(line_end>=? OR line_end IS NULL) "
                        "ORDER BY line_start DESC LIMIT 1",
                        (file_row["id"], call["line"], call["line"]),
                    ).fetchone()
                    call["symbol_id"] = sym["id"] if sym else 0
                    call["symbol_name"] = sym["name"] if sym else ""
                    results.append(call)
                    seen_urls.add(key)
    finally:
        conn.close()

    return results


def scan_backend_routes(repo_db_path: Path,
                        repo_root: Path) -> list[dict[str, Any]]:
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
            "SELECT id, path, language FROM files "
            "WHERE language IN ('php', 'python', 'javascript', 'typescript')"
        ).fetchall()

        for file_row in file_rows:
            fpath = repo_root / file_row["path"]
            if not fpath.exists():
                continue
            routes = _scan_file_for_routes(fpath, file_row["path"])
            for route in routes:
                # Find the handler symbol
                sym = conn.execute(
                    "SELECT id, name FROM symbols "
                    "WHERE file_id=? AND line_start<=? AND "
                    "(line_end>=? OR line_end IS NULL) "
                    "ORDER BY line_start DESC LIMIT 1",
                    (file_row["id"], route["line"], route["line"]),
                ).fetchone()
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
    # Build backend lookup: normalized_url -> list of routes
    backend_by_url: dict[str, list[dict[str, Any]]] = {}
    for route in backend_routes:
        normalized = _normalize_url(route["url_pattern"])
        backend_by_url.setdefault(normalized, []).append(route)

    matches = []
    for call in frontend_calls:
        normalized = _normalize_url(call["url_pattern"])
        candidates = backend_by_url.get(normalized, [])

        if not candidates:
            # Try prefix match for parameterized routes
            candidates = _fuzzy_url_match(normalized, backend_by_url)

        for candidate in candidates:
            # Method match (if both specify a method)
            method_match = (
                not call.get("http_method")
                or not candidate.get("http_method")
                or call["http_method"].upper() == candidate["http_method"].upper()
            )
            if not method_match:
                continue

            score = _match_score(call["url_pattern"], candidate["url_pattern"],
                                 call.get("http_method"), candidate.get("http_method"))
            matches.append({
                "frontend": call,
                "backend": candidate,
                "url_pattern": call["url_pattern"],
                "http_method": call.get("http_method", candidate.get("http_method", "")),
                "score": score,
            })

    # Deduplicate: keep best score per (frontend_call, backend_route) pair
    seen: dict[tuple, dict] = {}
    for m in matches:
        key = (m["frontend"]["file_path"], m["frontend"]["line"],
               m["backend"]["file_path"], m["backend"]["line"])
        if key not in seen or m["score"] > seen[key]["score"]:
            seen[key] = m

    return sorted(seen.values(), key=lambda m: m["score"], reverse=True)


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
            (frontend_repo_id, fe["symbol_id"], fe["url_pattern"],
             m.get("http_method", ""), fe["file_path"], fe["line"],
             fe["symbol_name"]),
        )

        # Store route symbols for backend route
        ws_conn.execute(
            "INSERT INTO ws_route_symbols "
            "(repo_id, symbol_id, url_pattern, http_method, kind, "
            " file_path, line, symbol_name) "
            "VALUES (?, ?, ?, ?, 'route_definition', ?, ?, ?)",
            (backend_repo_id, be["symbol_id"], be["url_pattern"],
             m.get("http_method", ""), be["file_path"], be["line"],
             be["symbol_name"]),
        )

        # Store cross-repo edge
        metadata = json.dumps({
            "url_pattern": m["url_pattern"],
            "http_method": m.get("http_method", ""),
            "score": m["score"],
        })
        ws_conn.execute(
            "INSERT INTO ws_cross_edges "
            "(source_repo_id, source_symbol_id, target_repo_id, "
            " target_symbol_id, kind, metadata) "
            "VALUES (?, ?, ?, ?, 'api_call', ?)",
            (frontend_repo_id, fe["symbol_id"],
             backend_repo_id, be["symbol_id"], metadata),
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _scan_file_for_api_calls(file_path: Path,
                              rel_path: str) -> list[dict[str, Any]]:
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
        url_m = _URL_RE.search(line[m.end() - 1:])
        if url_m:
            results.append({
                "url_pattern": url_m.group(1),
                "http_method": http_method,
                "file_path": rel_path,
                "line": i,
                "symbol_id": 0,
                "symbol_name": "",
            })

    return results


def _scan_file_for_routes(file_path: Path,
                           rel_path: str) -> list[dict[str, Any]]:
    """Scan a source file for route definition patterns."""
    results = []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return results

    for i, line in enumerate(lines, 1):
        # Laravel Route::get/post/...
        m = _BACKEND_ROUTE_RE.search(line)
        if m:
            method_raw = m.group(1).lower()
            # resource/apiResource map to multiple methods
            if method_raw in ("resource", "apiresource"):
                http_method = "RESOURCE"
            elif method_raw in ("any", "match"):
                http_method = "ANY"
            else:
                http_method = method_raw.upper()
            results.append({
                "url_pattern": m.group(2),
                "http_method": http_method,
                "file_path": rel_path,
                "line": i,
            })
            continue

        # Express/Fastify
        m = _EXPRESS_ROUTE_RE.search(line)
        if m:
            method_raw = m.group(1).lower()
            http_method = "ANY" if method_raw == "all" else method_raw.upper()
            results.append({
                "url_pattern": m.group(2),
                "http_method": http_method,
                "file_path": rel_path,
                "line": i,
            })
            continue

        # FastAPI/Flask
        m = _PYTHON_ROUTE_RE.search(line)
        if m:
            results.append({
                "url_pattern": m.group(2),
                "http_method": m.group(1).upper(),
                "file_path": rel_path,
                "line": i,
            })

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


def _fuzzy_url_match(normalized_url: str,
                      backend_by_url: dict[str, list]) -> list[dict[str, Any]]:
    """Try to match a frontend URL against backend routes with some fuzziness."""
    # Try with/without /api prefix
    candidates = []
    for prefix in ("", "/api", "/api/v1"):
        alt = prefix + normalized_url
        alt = alt.rstrip("/") or "/"
        if alt in backend_by_url:
            candidates.extend(backend_by_url[alt])

    # Also try the original URL as a backend route key
    for backend_url, routes in backend_by_url.items():
        if _urls_equivalent(normalized_url, backend_url):
            candidates.extend(routes)

    return candidates


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


def _match_score(fe_url: str, be_url: str,
                 fe_method: str | None, be_method: str | None) -> float:
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
