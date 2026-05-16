"""List all detected REST/GraphQL/gRPC endpoints with handlers.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because endpoints outputs are invocation-scoped route-surface
enumerations — not per-location violations. Editor consumers should use
the JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.index.test_conventions import is_test_file as _is_test_file
from roam.output.formatter import format_table, json_envelope, loc, to_json
from roam.output.structured_unknowns import structured_unknown_filter

# ---------------------------------------------------------------------------
# Framework detection patterns
# ---------------------------------------------------------------------------

# Python/Flask: @app.route('/path', methods=['GET'])
# Python/Flask: @app.get('/path'), @app.post('/path')
_FLASK_ROUTE_RE = re.compile(
    r"""@\s*\w+\s*\.\s*route\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*methods\s*=\s*\[([^\]]*)\])?\s*\)""",
)
_FLASK_METHOD_RE = re.compile(
    r"""@\s*\w+\s*\.\s*(get|post|put|patch|delete|head|options)\s*\(\s*['"]([^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# Python/FastAPI: @app.get('/path'), @router.post('/path')
# (same pattern as Flask method routes, covered by _FLASK_METHOD_RE)

# Python/Django: path('api/users', view_func)  or  url(r'^api/users', view_func)
_DJANGO_PATH_RE = re.compile(
    r"""(?:^|\b)(?:re_)?path\s*\(\s*r?['"]([^'"]+)['"]\s*,\s*(\w+(?:\.\w+)?)\s*""",
    re.MULTILINE,
)
_DJANGO_URL_RE = re.compile(
    r"""url\s*\(\s*r?['"]([^'"]+)['"]\s*,\s*(\w+(?:\.\w+)?)\s*""",
)

# Express.js: app.get('/path', handler), router.post('/path', handler)
_EXPRESS_RE = re.compile(
    r"""(?:app|router|server)\s*\.\s*(get|post|put|patch|delete|head|options|all|use)\s*\(\s*['"]([^'"]+)['"]\s*""",
    re.IGNORECASE,
)

# Go net/http: http.HandleFunc("/path", handler)
_GO_HANDLEFUNC_RE = re.compile(
    r"""(?:http\.)?HandleFunc\s*\(\s*["']([^"']+)["']\s*,\s*(\w+)""",
)

# Go Gin/Chi/gorilla: r.GET("/path", handler), r.POST(...)
_GO_ROUTER_RE = re.compile(
    r"""(?:\w+)\s*\.\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Handle|Any)\s*\(\s*["']([^"']+)["']\s*""",
)

# Java Spring: @GetMapping("/path"), @RequestMapping(value="/path")
# Allow empty path strings like @PostMapping("") — use [^"']* (zero or more)
_SPRING_MAPPING_RE = re.compile(
    r"""@\s*(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\s*\(\s*(?:value\s*=\s*)?["']([^"']*)["']""",
)

# Ruby/Rails: get '/path', to: 'controller#action'
_RAILS_RE = re.compile(
    r"""(?:^|\s)(get|post|put|patch|delete|match|resources?|namespace)\s+['"]([^'"]+)['"]""",
    re.MULTILINE | re.IGNORECASE,
)

# PHP/Laravel: Route::get('/path', ...), Route::post(...)
_LARAVEL_RE = re.compile(
    r"""Route\s*::\s*(get|post|put|patch|delete|options|head|any)\s*\(\s*['"]([^'"]+)['"]\s*""",
    re.IGNORECASE,
)

# Vue Router (vue-router 4 / Nuxt): { path: '/users', component: ... }
# Vue Router routes are declared as object literals inside createRouter({ routes: [...] }).
# Each route object exposes ``path: '...'`` and optionally ``name``, ``component``,
# ``redirect``. We match the path string and the next ``component:`` /
# ``redirect:`` identifier to surface a handler.
_VUE_ROUTER_PATH_RE = re.compile(
    r"""\{\s*(?:[^{}]*?,\s*)?path\s*:\s*['"]([^'"]+)['"]""",
)
_VUE_ROUTER_COMPONENT_RE = re.compile(
    r"""(?:component|redirect)\s*:\s*(?:\(\s*\)\s*=>\s*import\s*\(\s*['"]([^'"]+)['"]|['"]([^'"]+)['"]|(\w+))""",
)

# GraphQL: type Query { field(...): ... }
_GRAPHQL_QUERY_RE = re.compile(
    r"""type\s+(Query|Mutation|Subscription)\s*\{([^}]+)\}""",
    re.DOTALL,
)
_GRAPHQL_FIELD_RE = re.compile(
    r"""^\s+(\w+)\s*(?:\([^)]*\))?\s*:""",
    re.MULTILINE,
)

# gRPC: rpc MethodName(Request) returns (Response)
_GRPC_RPC_RE = re.compile(
    r"""rpc\s+(\w+)\s*\(\s*(\w+)\s*\)\s*returns\s*\(\s*(\w+)\s*\)""",
)
_GRPC_SERVICE_RE = re.compile(
    r"""service\s+(\w+)\s*\{""",
)


# ---------------------------------------------------------------------------
# HTTP method extraction helpers
# ---------------------------------------------------------------------------

_METHOD_MAP = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "head": "HEAD",
    "options": "OPTIONS",
    "all": "ANY",
    "any": "ANY",
    "use": "USE",
    "getmapping": "GET",
    "postmapping": "POST",
    "putmapping": "PUT",
    "patchmapping": "PATCH",
    "deletemapping": "DELETE",
    "requestmapping": "ANY",
    "Handle": "ANY",
    # Vue Router doesn't carry an HTTP verb; reported verbatim.
    "route": "ROUTE",
}

_SPRING_METHOD_MAP = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
    "RequestMapping": "ANY",
}


def _norm_method(raw: str) -> str:
    return _METHOD_MAP.get(raw.lower(), raw.upper())


def _line_of(source: str, match_start: int) -> int:
    """Return 1-based line number for a byte offset in source."""
    return source[:match_start].count("\n") + 1


# ---------------------------------------------------------------------------
# Per-language scanners
# ---------------------------------------------------------------------------


def _scan_python(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan Python source for Flask/FastAPI/Django route definitions."""
    endpoints = []

    # Flask/FastAPI @app.route(...)
    for m in _FLASK_ROUTE_RE.finditer(source):
        path = m.group(1)
        methods_raw = m.group(2) or ""
        methods = (
            [w.strip().strip("'\"").upper() for w in methods_raw.split(",") if w.strip().strip("'\"")]
            if methods_raw
            else ["GET"]
        )
        line = _line_of(source, m.start())
        handler = _next_function_name(source, m.end())
        for method in methods:
            endpoints.append(
                {
                    "method": method,
                    "path": path,
                    "handler": handler,
                    "file": rel_path,
                    "line": line,
                    "framework": _detect_python_framework(source),
                }
            )

    # Flask/FastAPI shorthand: @app.get('/path')
    for m in _FLASK_METHOD_RE.finditer(source):
        method = m.group(1).upper()
        path = m.group(2)
        line = _line_of(source, m.start())
        handler = _next_function_name(source, m.end())
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": handler,
                "file": rel_path,
                "line": line,
                "framework": _detect_python_framework(source),
            }
        )

    # Django path() / url()
    for pattern_re in (_DJANGO_PATH_RE, _DJANGO_URL_RE):
        for m in pattern_re.finditer(source):
            path = m.group(1)
            handler = m.group(2)
            line = _line_of(source, m.start())
            # Only report if it looks like an actual URL pattern
            if "/" in path or path.startswith("^") or path.startswith(r"\b"):
                endpoints.append(
                    {
                        "method": "ANY",
                        "path": path if path.startswith("/") else "/" + path.lstrip("^").rstrip("$"),
                        "handler": handler,
                        "file": rel_path,
                        "line": line,
                        "framework": "django",
                    }
                )

    return endpoints


def _detect_python_framework(source: str) -> str:
    """Detect Python web framework from import statements."""
    if "fastapi" in source.lower() and ("FastAPI" in source or "APIRouter" in source):
        return "fastapi"
    if "flask" in source.lower() and ("Flask" in source or "Blueprint" in source):
        return "flask"
    if "django" in source.lower():
        return "django"
    if "@app.route" in source or "@router." in source:
        return "flask/fastapi"
    return "python"


def _scan_javascript_typescript(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan JS/TS source for Express route definitions."""
    endpoints = []
    for m in _EXPRESS_RE.finditer(source):
        method = _norm_method(m.group(1))
        path = m.group(2)
        line = _line_of(source, m.start())
        handler = _extract_express_handler(source, m.end())
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": handler,
                "file": rel_path,
                "line": line,
                "framework": _detect_js_framework(source),
            }
        )
    return endpoints


def _detect_js_framework(source: str) -> str:
    """Detect JS/TS web framework."""
    src = source.lower()
    if "express" in src:
        return "express"
    if "hapi" in src or "hapi/hapi" in src:
        return "hapi"
    if "koa" in src:
        return "koa"
    if "fastify" in src:
        return "fastify"
    if "next/" in src or "next/app" in src:
        return "nextjs"
    return "javascript"


def _scan_vue_router(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan a JS/TS/Vue file for Vue Router route table entries.

    Recognises ``createRouter({ routes: [...] })`` declarations and any
    ``{ path: '...', component: ... }`` literals inside the routes array.
    Vue Router is method-agnostic (it routes browser navigation, not HTTP
    verbs), so every entry is reported as method ``ROUTE``.

    Triggers only when the source mentions ``createRouter`` /
    ``vue-router`` / ``defineRouter`` — keeps the scan from misfiring on
    arbitrary object literals containing a ``path`` key (e.g. webpack
    aliases).
    """
    # Only scan files that look like Vue Router config — the same
    # path-literal pattern appears in plain object literals (webpack
    # aliases, Next.js config), and we don't want false positives.
    if not (
        "createRouter" in source
        or "vue-router" in source
        or "defineRouter" in source
        or "useRouter" in source
        and "routes" in source
    ):
        return []

    endpoints: list[dict] = []
    for m in _VUE_ROUTER_PATH_RE.finditer(source):
        path = m.group(1)
        line = _line_of(source, m.start())
        # Look forward up to ~300 chars for the matching component / redirect.
        tail = source[m.end() : m.end() + 400]
        handler = ""
        cm = _VUE_ROUTER_COMPONENT_RE.search(tail)
        if cm:
            handler = cm.group(1) or cm.group(2) or cm.group(3) or ""
        endpoints.append(
            {
                "method": "ROUTE",
                "path": path,
                "handler": handler,
                "file": rel_path,
                "line": line,
                "framework": "vue-router",
            }
        )
    return endpoints


def _scan_javascript_typescript_with_vue_router(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Wrapper: scan JS/TS source for both Express AND Vue Router routes.

    Vue Router config files commonly live in ``router/index.ts`` /
    ``src/router.js``, not in ``.vue`` SFCs. Folding both scanners into
    one extension-map slot lets ``endpoints`` discover Vue Router
    declarations without a separate registration per extension.
    """
    endpoints = _scan_javascript_typescript(source, file_path, rel_path)
    endpoints.extend(_scan_vue_router(source, file_path, rel_path))
    return endpoints


def _scan_vue_sfc(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan a Vue SFC for routes declared inside ``<script setup>`` /
    ``<script>`` blocks.

    Strips the SFC down to its script content before reusing the JS/TS
    scanners — Vue Router config sometimes lives inline in
    ``App.vue`` / ``router.vue`` instead of a dedicated TS file. We
    fall through to the same scanners ``.ts`` files use; the regexes
    don't care about the surrounding ``<template>`` / ``<style>``
    blocks but stripping them avoids matching path-like strings inside
    template attributes.
    """
    script_blocks = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", source, re.DOTALL | re.IGNORECASE)
    if not script_blocks:
        return []
    joined = "\n".join(script_blocks)
    # Reuse the JS/TS scanner so Express-style and Vue-Router-style
    # endpoints inside SFC scripts are both picked up.
    return _scan_javascript_typescript_with_vue_router(joined, file_path, rel_path)


def _scan_go(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan Go source for net/http and router route definitions."""
    endpoints = []
    for m in _GO_HANDLEFUNC_RE.finditer(source):
        path = m.group(1)
        handler = m.group(2)
        line = _line_of(source, m.start())
        endpoints.append(
            {
                "method": "ANY",
                "path": path,
                "handler": handler,
                "file": rel_path,
                "line": line,
                "framework": "net/http",
            }
        )
    for m in _GO_ROUTER_RE.finditer(source):
        method = _norm_method(m.group(1))
        path = m.group(2)
        line = _line_of(source, m.start())
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": _extract_go_handler(source, m.end()),
                "file": rel_path,
                "line": line,
                "framework": _detect_go_framework(source),
            }
        )
    return endpoints


def _detect_go_framework(source: str) -> str:
    src = source.lower()
    if "gin-gonic" in src or '"github.com/gin-gonic/gin"' in src:
        return "gin"
    if "chi" in src and "go-chi" in src:
        return "chi"
    if "gorilla/mux" in src:
        return "gorilla/mux"
    if "echo" in src and "labstack" in src:
        return "echo"
    if "fiber" in src and "gofiber" in src:
        return "fiber"
    return "go"


def _scan_java(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan Java source for Spring route definitions."""
    endpoints = []

    # Look for class-level @RequestMapping to use as prefix
    class_prefix = ""
    class_rm = re.search(
        r"""@RequestMapping\s*\(\s*(?:value\s*=\s*)?["']([^"']+)["']""",
        source[: source.find("class ") + 500] if "class " in source else source[:500],
    )
    if class_rm:
        class_prefix = class_rm.group(1).rstrip("/")

    for m in _SPRING_MAPPING_RE.finditer(source):
        ann = m.group(1)
        path_segment = m.group(2)
        method = _SPRING_METHOD_MAP.get(ann, "ANY")
        full_path = class_prefix + "/" + path_segment.lstrip("/") if class_prefix else path_segment
        if not full_path.startswith("/"):
            full_path = "/" + full_path
        line = _line_of(source, m.start())
        handler = _next_java_method(source, m.end())
        endpoints.append(
            {
                "method": method,
                "path": full_path,
                "handler": handler,
                "file": rel_path,
                "line": line,
                "framework": "spring",
            }
        )
    return endpoints


def _scan_ruby(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan Ruby source for Rails route definitions."""
    endpoints = []
    for m in _RAILS_RE.finditer(source):
        verb = m.group(1).lower()
        path = m.group(2)
        if not path.startswith("/"):
            path = "/" + path
        line = _line_of(source, m.start())
        method = {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
            "match": "ANY",
        }.get(verb, "RESOURCE")
        if verb in ("resources", "resource", "namespace"):
            method = "RESOURCE"
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": "",
                "file": rel_path,
                "line": line,
                "framework": "rails",
            }
        )
    return endpoints


def _scan_php(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan PHP source for Laravel route definitions."""
    endpoints = []
    for m in _LARAVEL_RE.finditer(source):
        method = _norm_method(m.group(1))
        path = m.group(2)
        if not path.startswith("/"):
            path = "/" + path
        line = _line_of(source, m.start())
        handler = _extract_laravel_handler(source, m.end())
        endpoints.append(
            {
                "method": method,
                "path": path,
                "handler": handler,
                "file": rel_path,
                "line": line,
                "framework": "laravel",
            }
        )
    return endpoints


def _scan_graphql(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan GraphQL schema files for Query/Mutation/Subscription fields."""
    endpoints = []
    for type_match in _GRAPHQL_QUERY_RE.finditer(source):
        type_name = type_match.group(1)  # Query, Mutation, Subscription
        body = type_match.group(2)
        line = _line_of(source, type_match.start())
        method_map = {
            "Query": "QUERY",
            "Mutation": "MUTATION",
            "Subscription": "SUBSCRIPTION",
        }
        gql_method = method_map.get(type_name, "QUERY")
        for field_match in _GRAPHQL_FIELD_RE.finditer(body):
            field_name = field_match.group(1)
            field_line = line + body[: field_match.start()].count("\n")
            endpoints.append(
                {
                    "method": gql_method,
                    "path": field_name,
                    "handler": field_name,
                    "file": rel_path,
                    "line": field_line,
                    "framework": "graphql",
                }
            )
    return endpoints


def _scan_proto(source: str, file_path: str, rel_path: str) -> list[dict]:
    """Scan protobuf files for gRPC service definitions."""
    endpoints = []
    current_service = ""
    for m in _GRPC_SERVICE_RE.finditer(source):
        current_service = m.group(1)

    for m in _GRPC_RPC_RE.finditer(source):
        rpc_name = m.group(1)
        line = _line_of(source, m.start())
        endpoints.append(
            {
                "method": "RPC",
                "path": f"{current_service}/{rpc_name}" if current_service else rpc_name,
                "handler": rpc_name,
                "file": rel_path,
                "line": line,
                "framework": "grpc",
            }
        )
    return endpoints


# ---------------------------------------------------------------------------
# Handler name extraction helpers
# ---------------------------------------------------------------------------


def _next_function_name(source: str, after_pos: int) -> str:
    """Extract the function name following a decorator (the next 'def name')."""
    snippet = source[after_pos : after_pos + 300]
    m = re.search(r"def\s+(\w+)\s*\(", snippet)
    if m:
        return m.group(1)
    return ""


def _extract_express_handler(source: str, after_pos: int) -> str:
    """Extract the handler function name from an Express route call."""
    snippet = source[after_pos : after_pos + 200]
    # Try: , handlerName) or , handlerName, ...)
    m = re.search(r",\s*(\w+)\s*(?:,|\))", snippet)
    if m:
        name = m.group(1)
        if name not in ("req", "res", "next", "true", "false", "null", "undefined"):
            return name
    # Try async (req, res) => ... or function(req, res) ...
    m2 = re.search(r",\s*(?:async\s+)?(?:function\s+(\w+)|(\w+)\s*=>|function\s*\()", snippet)
    if m2:
        return m2.group(1) or "(anonymous)"
    return ""


def _extract_go_handler(source: str, after_pos: int) -> str:
    """Extract the handler function name from a Go router call."""
    snippet = source[after_pos : after_pos + 150]
    m = re.search(r",\s*(\w+(?:\.\w+)?)\s*(?:,|\))", snippet)
    if m:
        return m.group(1)
    return ""


def _next_java_method(source: str, after_pos: int) -> str:
    """Extract the Java method name following a Spring mapping annotation."""
    snippet = source[after_pos : after_pos + 400]
    # Match: public ResponseEntity<...> methodName(...) or public String methodName(...)
    m = re.search(
        r"(?:public|protected|private)?\s+\w[\w<>, ]*\s+(\w+)\s*\(",
        snippet,
    )
    if m:
        name = m.group(1)
        # Skip constructor-like names
        if not name[0].isupper():
            return name
    return ""


def _extract_laravel_handler(source: str, after_pos: int) -> str:
    """Extract handler from Laravel route: closure or [Controller::class, 'method']."""
    snippet = source[after_pos : after_pos + 300]
    # Array-style: [Controller::class, 'method']
    m = re.search(r'\[\s*(\w+)::class\s*,\s*[\'"](\w+)[\'"]\s*\]', snippet)
    if m:
        return f"{m.group(1)}@{m.group(2)}"
    # String-style: 'Controller@method'
    m2 = re.search(r"['\"](\w+@\w+)['\"]", snippet)
    if m2:
        return m2.group(1)
    # Invokable: Controller::class
    m3 = re.search(r"\[\s*(\w+)::class\s*\]", snippet)
    if m3:
        return f"{m3.group(1)}@__invoke"
    return ""


# ---------------------------------------------------------------------------
# Extension-to-scanner mapping
# ---------------------------------------------------------------------------

_EXT_SCANNER = {
    ".py": _scan_python,
    ".js": _scan_javascript_typescript_with_vue_router,
    ".ts": _scan_javascript_typescript_with_vue_router,
    ".jsx": _scan_javascript_typescript_with_vue_router,
    ".tsx": _scan_javascript_typescript_with_vue_router,
    ".mjs": _scan_javascript_typescript_with_vue_router,
    ".cjs": _scan_javascript_typescript_with_vue_router,
    ".vue": _scan_vue_sfc,
    ".java": _scan_java,
    ".rb": _scan_ruby,
    ".php": _scan_php,
    ".go": _scan_go,
    ".graphql": _scan_graphql,
    ".gql": _scan_graphql,
    ".proto": _scan_proto,
}

# Files that are unlikely to define routes (skip to speed up scan)
_SKIP_PATH_PATTERNS = re.compile(
    r"[/\\](?:node_modules|\.git|__pycache__|\.tox|venv|"
    r"vendor|dist|build|\.roam|migrations|static|assets)[/\\]",
    re.IGNORECASE,
)

# Note: test-file detection used to live here as a private
# ``_TEST_PATH_PATTERNS`` regex. W6.2 consolidated test detection onto
# the canonical ``test_conventions.is_test_file`` facade above.


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------


def _scan_file(full_path: Path, rel_path: str) -> list[dict]:
    """Scan a single file for endpoint definitions. Returns a list of endpoint dicts."""
    ext = full_path.suffix.lower()
    scanner = _EXT_SCANNER.get(ext)
    if scanner is None:
        return []

    try:
        source = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    return scanner(source, str(full_path), rel_path)


def _collect_endpoints(project_root: Path, file_paths: list[str], include_tests: bool = False) -> list[dict]:
    """Scan all indexed files for endpoint definitions.

    Args:
        project_root: Root directory for resolving relative paths.
        file_paths: List of relative file paths from the DB.
        include_tests: If True, also scan test files.

    Returns:
        List of endpoint dicts sorted by framework then path.
    """
    all_endpoints: list[dict] = []
    supported_exts = set(_EXT_SCANNER.keys())

    for rel_path in file_paths:
        # Skip unsupported extensions early
        ext = os.path.splitext(rel_path)[1].lower()
        if ext not in supported_exts:
            continue

        # Skip build/vendor dirs
        norm = rel_path.replace("\\", "/")
        if _SKIP_PATH_PATTERNS.search("/" + norm):
            continue

        # Skip test files unless requested. Uses the canonical
        # test_conventions.is_test_file detector (W6.2) — recognises
        # every supported test convention including Vitest / Vue SFC.
        if not include_tests and _is_test_file(norm):
            continue

        full_path = project_root / rel_path
        endpoints = _scan_file(full_path, rel_path)
        all_endpoints.extend(endpoints)

    # Deduplicate: same (method, path, file, line) — can happen with multi-match
    seen: set[tuple] = set()
    unique: list[dict] = []
    for ep in all_endpoints:
        key = (ep["method"], ep["path"], ep["file"], ep["line"])
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    # Sort: framework → method → path
    unique.sort(key=lambda e: (e["framework"], e["method"], e["path"]))
    return unique


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="endpoints",
    category="exploration",
    summary="List all detected REST/GraphQL/gRPC endpoints with handlers",
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
@click.command("endpoints")
@click.option(
    "--framework",
    "-f",
    default=None,
    help="Filter to a specific framework (e.g. flask, express, django)",
)
@click.option("--method", "-m", "http_method", default=None, help="Filter by HTTP method (GET, POST, etc.)")
@click.option("--include-tests", is_flag=True, default=False, help="Include endpoints defined in test files")
@click.option(
    "--group-by",
    default="framework",
    type=click.Choice(["framework", "file", "method"], case_sensitive=False),
    help="Group output by: framework (default), file, or method",
)
@click.pass_context
def endpoints(ctx, framework, http_method, include_tests, group_by):
    """List all detected REST/GraphQL/gRPC endpoints with handlers.

    Unlike ``auth-gaps`` (which checks for missing auth guards on routes),
    this command catalogs all route definitions across 12 frameworks.

    Scans indexed source files for route definitions from Flask, FastAPI,
    Django, Express, Spring, Rails, Laravel, Go net/http, Vue Router,
    GraphQL schemas, and gRPC .proto files.

    Outputs HTTP method, URL path, handler function, file, and line.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        file_rows = conn.execute("SELECT path FROM files").fetchall()
        file_paths = [r["path"] for r in file_rows]

    all_endpoints = _collect_endpoints(project_root, file_paths, include_tests)

    # W1069 (sibling of W1063/W1068): capture the observed-framework
    # superset BEFORE filtering so the unknown_framework_filter
    # disclosure can enumerate every framework the corpus actually
    # exposes. Apply the same lowercase substring contract as the
    # filter itself (``fw_lower in e["framework"].lower()``).
    observed_frameworks = sorted({e["framework"] for e in all_endpoints})
    framework_matched_corpus = True
    if framework:
        fw_lower = framework.lower()
        framework_matched_corpus = any(fw_lower in fw.lower() for fw in observed_frameworks)

    # W1075 (drive-by sibling of W1069): capture the observed-method
    # superset BEFORE filtering so the unknown_method_filter disclosure
    # can enumerate every HTTP method the corpus actually exposes. Apply
    # the same upper-case equality contract as the filter itself
    # (``e["method"] == meth_upper``).
    observed_methods = sorted({e["method"] for e in all_endpoints})
    method_matched_corpus = True
    if http_method:
        meth_upper = http_method.upper()
        method_matched_corpus = meth_upper in observed_methods

    # Apply filters
    if framework:
        fw_lower = framework.lower()
        all_endpoints = [e for e in all_endpoints if fw_lower in e["framework"].lower()]
    if http_method:
        meth_upper = http_method.upper()
        all_endpoints = [e for e in all_endpoints if e["method"] == meth_upper]

    n = len(all_endpoints)

    # Collect distinct frameworks
    frameworks_found = sorted(set(e["framework"] for e in all_endpoints))
    n_frameworks = len(frameworks_found)

    # W1069: when ``--framework`` was supplied AND its substring matches
    # NO framework in the observed superset, emit the explicit
    # unknown_framework_filter envelope (Pattern-1D: silent-success on
    # degraded filter resolution otherwise renders this as a generic
    # "no endpoints matching the given filters" — indistinguishable from
    # a valid filter that just happens to have zero hits).
    # W1080: delegated to the shared ``structured_unknown_filter`` helper.
    # Note the helper's membership test is exact, while the
    # surrounding filter is substring-match — at this point in the
    # control flow ``framework_matched_corpus`` is False, so the
    # substring miss already rules out exact membership too, and the
    # helper is guaranteed to return a fragment.
    if framework and not framework_matched_corpus:
        frag = structured_unknown_filter(
            requested=framework,
            known=observed_frameworks,
            state="unknown_framework_filter",
            requested_field="requested_framework",
            known_field="observed_frameworks",
            fact_anchor="frameworks",
            did_you_mean_omit_when_empty=True,
            requested_disclosure_verb="substring not in observed",
            known_disclosure_label="observed frameworks",
        )
        assert frag is not None  # substring miss guarantees membership miss
        close = frag.get("did_you_mean", [])
        verdict_unknown = (
            f"unknown framework filter {framework!r} ({len(observed_frameworks)} observed){frag['verdict_suffix']}"
        )
        if json_mode:
            summary_payload: dict[str, object] = {
                "verdict": verdict_unknown,
                "partial_success": frag["partial_success"],
                "state": frag["state"],
                "count": 0,
                "frameworks": [],
                "framework_count": 0,
                "requested_framework": frag["requested_framework"],
                "observed_frameworks": frag["observed_frameworks"],
            }
            # W1082: helper now controls presence + second-fact wording
            # via the W1081 kwargs (did_you_mean_omit_when_empty +
            # requested_disclosure_verb + known_disclosure_label). No
            # post-helper splice or fact patch needed; LAW 4 anchor
            # ``frameworks`` preserved on every fact terminal.
            if "did_you_mean" in frag:
                summary_payload["did_you_mean"] = frag["did_you_mean"]
            click.echo(
                to_json(
                    json_envelope(
                        "endpoints",
                        summary=summary_payload,
                        agent_contract={
                            "facts": frag["facts"],
                            "next_commands": ["roam endpoints"],
                        },
                        budget=token_budget,
                        endpoints=[],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict_unknown}")
        if close:
            quoted = " or ".join(f"'{m}'" for m in close)
            click.echo(f"Did you mean: {quoted}?")
        if observed_frameworks:
            click.echo("Observed frameworks: " + ", ".join(observed_frameworks))
        else:
            click.echo("No frameworks observed in the indexed corpus.")
        return

    # W1075: mirror of the W1069 unknown_framework_filter block for the
    # ``--method`` filter. When ``--method`` was supplied AND its
    # upper-cased value is NOT in the observed-method superset, emit the
    # explicit unknown_method_filter envelope. Otherwise Pattern-1D
    # silent-success renders as a generic "no endpoints detected matching
    # the given filters" — indistinguishable from a valid method that just
    # happens to have zero occurrences in the corpus.
    if http_method and not method_matched_corpus:
        meth_upper = http_method.upper()
        # W1082: migrated to the shared ``structured_unknown_filter`` helper
        # using the W1081 kwargs (did_you_mean_omit_when_empty +
        # requested_disclosure_verb + known_disclosure_label). The helper's
        # second-fact wording matches the pre-migration string
        # ``"'GARBAGE' not in observed methods"`` exactly; LAW 4 anchor
        # ``methods`` preserved on every fact terminal.
        frag_m = structured_unknown_filter(
            requested=meth_upper,
            known=observed_methods,
            state="unknown_method_filter",
            requested_field="requested_method",
            known_field="observed_methods",
            fact_anchor="methods",
            did_you_mean_omit_when_empty=True,
            requested_disclosure_verb="not in observed",
            known_disclosure_label="observed methods",
        )
        assert frag_m is not None  # not in superset guarantees membership miss
        close_m = frag_m.get("did_you_mean", [])
        verdict_unknown_method = (
            f"unknown method filter {meth_upper!r} ({len(observed_methods)} observed){frag_m['verdict_suffix']}"
        )
        if json_mode:
            summary_payload_m: dict[str, object] = {
                "verdict": verdict_unknown_method,
                "partial_success": frag_m["partial_success"],
                "state": frag_m["state"],
                "count": 0,
                "frameworks": [],
                "framework_count": 0,
                "requested_method": frag_m["requested_method"],
                "observed_methods": frag_m["observed_methods"],
            }
            if "did_you_mean" in frag_m:
                summary_payload_m["did_you_mean"] = frag_m["did_you_mean"]
            click.echo(
                to_json(
                    json_envelope(
                        "endpoints",
                        summary=summary_payload_m,
                        agent_contract={
                            "facts": frag_m["facts"],
                            "next_commands": ["roam endpoints"],
                        },
                        budget=token_budget,
                        endpoints=[],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict_unknown_method}")
        if close_m:
            quoted_m = " or ".join(f"'{m}'" for m in close_m)
            click.echo(f"Did you mean: {quoted_m}?")
        if observed_methods:
            click.echo("Observed methods: " + ", ".join(observed_methods))
        else:
            click.echo("No methods observed in the indexed corpus.")
        return

    # Build verdict
    if n == 0:
        verdict = "no endpoints detected"
        if framework or http_method:
            verdict += " matching the given filters"
    else:
        fw_label = f"1 framework ({frameworks_found[0]})" if n_frameworks == 1 else f"{n_frameworks} frameworks"
        verdict = f"found {n} endpoint{'s' if n != 1 else ''} across {fw_label}"

    if json_mode:
        json_endpoints = [
            {
                "method": e["method"],
                "path": e["path"],
                "handler": e["handler"],
                "file": e["file"],
                "line": e["line"],
                "framework": e["framework"],
            }
            for e in all_endpoints
        ]
        click.echo(
            to_json(
                json_envelope(
                    "endpoints",
                    summary={
                        "verdict": verdict,
                        "count": n,
                        "frameworks": frameworks_found,
                        "framework_count": n_frameworks,
                    },
                    # LAW 4 (W17.3): bare ``count`` / ``framework_count``
                    # keys auto-derive to awkward facts; pin explicit
                    # concrete-noun facts here.
                    agent_contract={
                        "facts": [
                            f"endpoints scan found {n} endpoint(s) across {n_frameworks} framework(s)",
                            f"endpoints scan frameworks: {', '.join(frameworks_found) or '(none)'}",
                        ],
                    },
                    budget=token_budget,
                    endpoints=json_endpoints,
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}\n")

    if n == 0:
        click.echo("  No endpoint definitions found in indexed files.")
        click.echo()
        click.echo("  Supported frameworks: Flask, FastAPI, Django, Express, Spring,")
        click.echo("  Rails, Laravel, Go net/http, Vue Router, GraphQL (.graphql), gRPC (.proto)")
        return

    # Group endpoints
    if group_by == "framework":
        groups: dict[str, list[dict]] = defaultdict(list)
        for ep in all_endpoints:
            groups[ep["framework"]].append(ep)
    elif group_by == "file":
        groups = defaultdict(list)
        for ep in all_endpoints:
            groups[ep["file"]].append(ep)
    else:  # method
        groups = defaultdict(list)
        for ep in all_endpoints:
            groups[ep["method"]].append(ep)

    for group_key in sorted(groups):
        group_eps = groups[group_key]
        click.echo(f"=== {group_key} ({len(group_eps)}) ===")
        rows = []
        for ep in group_eps:
            rows.append(
                [
                    ep["method"],
                    ep["path"],
                    ep["handler"] or "-",
                    loc(ep["file"], ep["line"]),
                ]
            )
        click.echo(
            format_table(
                ["Method", "Path", "Handler", "Location"],
                rows,
                budget=50,
            )
        )
        click.echo()

    click.echo(f"Total: {n} endpoint{'s' if n != 1 else ''} across {', '.join(frameworks_found)}")
