"""REST API cross-language bridge: frontend HTTP calls <-> backend route definitions.

Resolves cross-references between:
- Frontend fetch/axios/jQuery calls to API endpoints
- Backend route definitions in Flask, Express, Go net/http, etc.
- Python HTTP client calls (requests, httpx) to backend routes
"""
from __future__ import annotations

import os
import re

from roam.bridges.base import LanguageBridge
from roam.bridges.registry import register_bridge


# Frontend extensions that may contain HTTP calls
_FRONTEND_EXTS = frozenset({".js", ".ts", ".jsx", ".tsx"})

# Backend extensions that may contain route definitions
_BACKEND_EXTS = frozenset({".py", ".go", ".java", ".rb", ".js", ".ts"})

# All extensions this bridge cares about (union)
_ALL_SOURCE_EXTS = _FRONTEND_EXTS
_ALL_TARGET_EXTS = _BACKEND_EXTS

# --- Frontend URL extraction patterns ---

# fetch('/api/users') or fetch("/api/users")
_FETCH_RE = re.compile(
    r'''fetch\s*\(\s*['"](/[^'"]+)['"]''',
)

# axios.get('/api/users'), axios.post('/api/orders'), etc.
_AXIOS_RE = re.compile(
    r'''axios\s*\.\s*(?:get|post|put|patch|delete|head|options)\s*\(\s*['"](/[^'"]+)['"]''',
    re.IGNORECASE,
)

# $.ajax({url: '/api/...'}) or $.get('/api/...') or $.post('/api/...')
_JQUERY_RE = re.compile(
    r'''\$\s*\.\s*(?:ajax|get|post|put|delete)\s*\(\s*(?:\{\s*url\s*:\s*)?['"](/[^'"]+)['"]''',
    re.IGNORECASE,
)

# Python: requests.get('/api/users'), httpx.get(...)
_PY_HTTP_RE = re.compile(
    r'''(?:requests|httpx|urllib\.request)\s*\.\s*(?:get|post|put|patch|delete|head|options|urlopen)\s*\(\s*['"](/[^'"]+)['"]''',
    re.IGNORECASE,
)

# --- Backend route definition patterns ---

# Python/Flask/FastAPI: @app.route('/api/users'), @router.get('/api/users')
_PY_ROUTE_RE = re.compile(
    r'''@\s*(?:\w+)\s*\.\s*(?:route|get|post|put|patch|delete|head|options|api_view)\s*\(\s*['"](/[^'"]+)['"]''',
)

# Express: app.get('/api/users', ...), router.post('/api/orders', ...)
_EXPRESS_RE = re.compile(
    r'''(?:app|router|server)\s*\.\s*(?:get|post|put|patch|delete|head|options|all|use)\s*\(\s*['"](/[^'"]+)['"]''',
)

# Go: http.HandleFunc("/api/users", ...), r.GET("/api/users", ...)
_GO_ROUTE_RE = re.compile(
    r'''(?:HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Group|Any)\s*\(\s*["'](/[^"']+)["']''',
)

# Java Spring: @GetMapping("/api/users"), @RequestMapping("/api/users")
_JAVA_ROUTE_RE = re.compile(
    r'''@\s*(?:GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\s*\(\s*(?:value\s*=\s*)?["'](/[^"']+)["']''',
)

# Ruby/Rails: get '/api/users', post '/api/orders'
_RUBY_ROUTE_RE = re.compile(
    r'''(?:get|post|put|patch|delete|match)\s+['"](/[^'"]+)['"]''',
)


class RestApiBridge(LanguageBridge):
    """Bridge between frontend HTTP calls and backend route definitions."""

    @property
    def name(self) -> str:
        return "rest-api"

    @property
    def source_extensions(self) -> frozenset[str]:
        return _ALL_SOURCE_EXTS

    @property
    def target_extensions(self) -> frozenset[str]:
        return _ALL_TARGET_EXTS

    def detect(self, file_paths: list[str]) -> bool:
        """Detect if project has both frontend and backend files."""
        has_frontend = False
        has_backend = False
        for fp in file_paths:
            ext = os.path.splitext(fp)[1].lower()
            if ext in _FRONTEND_EXTS:
                has_frontend = True
            if ext in {".py", ".go", ".java", ".rb"}:
                has_backend = True
            if has_frontend and has_backend:
                return True
        return False

    def resolve(self, source_path: str, source_symbols: list[dict],
                target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve frontend HTTP calls to backend route definitions.

        This bridge works differently from symbol-based bridges:
        it scans file contents (via symbol signatures/docstrings or raw names)
        to find URL patterns, then matches them across languages.

        For simplicity with the existing bridge interface (which provides symbols,
        not raw file content), we extract URL-like strings from symbol names,
        signatures, and qualified names.
        """
        edges: list[dict] = []

        # Extract URLs from source symbols (frontend calls)
        source_urls = self._extract_urls_from_symbols(source_symbols, mode="client")

        if not source_urls:
            return edges

        # Extract route definitions from target files
        for tpath, tsymbols in target_files.items():
            target_urls = self._extract_urls_from_symbols(tsymbols, mode="server")
            if not target_urls:
                continue

            # Match URLs
            for src_url, src_sym_name in source_urls:
                for tgt_url, tgt_sym_name in target_urls:
                    if self._urls_match(src_url, tgt_url):
                        edges.append({
                            "source": src_sym_name,
                            "target": tgt_sym_name,
                            "kind": "x-lang",
                            "bridge": self.name,
                            "mechanism": "url-match",
                            "url": src_url,
                            "confidence": 0.8,
                        })

        return edges

    def _extract_urls_from_symbols(self, symbols: list[dict],
                                    mode: str) -> list[tuple[str, str]]:
        """Extract URL strings from symbol metadata.

        Returns list of (url, symbol_qualified_name) tuples.
        """
        results: list[tuple[str, str]] = []

        for sym in symbols:
            name = sym.get("name", "")
            qname = sym.get("qualified_name", name)
            sig = sym.get("signature", "") or ""
            doc = sym.get("docstring", "") or ""

            # Combine all text fields for scanning
            text = f"{name} {sig} {doc}"

            urls: list[str] = []
            if mode == "client":
                urls.extend(m.group(1) for m in _FETCH_RE.finditer(text))
                urls.extend(m.group(1) for m in _AXIOS_RE.finditer(text))
                urls.extend(m.group(1) for m in _JQUERY_RE.finditer(text))
                urls.extend(m.group(1) for m in _PY_HTTP_RE.finditer(text))
            else:
                urls.extend(m.group(1) for m in _PY_ROUTE_RE.finditer(text))
                urls.extend(m.group(1) for m in _EXPRESS_RE.finditer(text))
                urls.extend(m.group(1) for m in _GO_ROUTE_RE.finditer(text))
                urls.extend(m.group(1) for m in _JAVA_ROUTE_RE.finditer(text))
                urls.extend(m.group(1) for m in _RUBY_ROUTE_RE.finditer(text))

            for url in urls:
                results.append((url, qname))

        return results

    def _urls_match(self, client_url: str, server_url: str) -> bool:
        """Check if a client URL matches a server route.

        Supports exact match and parameterized routes:
        - /api/users matches /api/users
        - /api/users/123 matches /api/users/<id> or /api/users/:id or /api/users/{id}
        """
        if client_url == server_url:
            return True

        # Normalize: strip trailing slashes
        c = client_url.rstrip("/")
        s = server_url.rstrip("/")
        if c == s:
            return True

        # Check prefix match for parameterized routes
        # Convert server route params to regex
        # :param, <param>, {param} -> wildcard
        param_re = re.sub(r'[:<{]\w+[>}]?', r'[^/]+', s)
        if re.fullmatch(param_re, c):
            return True

        return False


# Auto-register on import
register_bridge(RestApiBridge())
