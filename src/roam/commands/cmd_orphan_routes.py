"""Find backend API routes that have no consumers in the codebase (dead endpoints).

W1227: SARIF is deliberately surfaced via the global ``--sarif`` flag.
cmd_orphan_routes emits per-route orphan findings as envelope items
(each carrying ``confidence`` / ``method`` / ``path`` / ``file`` /
``line`` / optional ``controller`` + ``action``) which the
:func:`roam.output.sarif.orphan_routes_to_sarif` projection maps onto a
single closed-enum rule id (``orphan-route``) with per-result level
banded by confidence (high + medium -> warning; low -> note). Dead
endpoints are real bugs (operational cost + attack surface), not just
hygiene — the defaultLevel is ``warning`` rather than ``note``. See
W1227 audit (Wave 15) + the SHIP path in
:mod:`tests.test_sarif_disclosure_coverage` (cmd_orphan_routes removed
from ``_KNOWN_MISSING``).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import format_table, json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Skip patterns — routes that are intentionally internal / infrastructure
# ---------------------------------------------------------------------------

_SKIP_PATH_PATTERNS = re.compile(
    r"telescope|horizon|pulse|sanctum|broadcasting|"
    r"health|healthcheck|health-check|"
    r"webhook|webhooks|stripe/|paypal/|"
    r"_debugbar|_ignition|nova-api",
    re.IGNORECASE,
)

_SKIP_NAMES = re.compile(
    r"^(login|logout|register|password|verify|email|two-factor|"
    r"sanctum|csrf|broadcasting|health)$",
    re.IGNORECASE,
)

# 9 frontend extensions scanned for route-name string literals: Vue SFC plus
# the 8 JS/TS module variants (.ts/.tsx/.js/.jsx + the 4 m{ts,js}/c{ts,js}
# ESM/CommonJS suffixes). Pairs with _BACKEND_EXTENSIONS below for the
# Laravel-route consumer/producer split.
_FRONTEND_EXTENSIONS = {
    ".vue",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mts",
    ".mjs",
    ".cts",
    ".cjs",
}

# 1 backend extension (.php) — cmd_orphan_routes is scoped to Laravel route
# discovery. Extending to other PHP-adjacent backends (Symfony, CodeIgniter)
# requires no extension change; extending to Django/Rails/Express does.
_BACKEND_EXTENSIONS = {".php"}

_BACKEND_TEST_PATTERNS = re.compile(
    r"[/\\](tests?|spec|feature|unit)[/\\]|Test\.php$|Spec\.php$",
    re.IGNORECASE,
)

_SEEDER_FACTORY_PATTERN = re.compile(
    r"[/\\](database|seeders?|factories)[/\\]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Route extraction from PHP route files
# ---------------------------------------------------------------------------

# Standard single-action routes:
#   Route::get('/path', [Controller::class, 'method'])
#   Route::post('/path', 'Controller@method')
#   Route::get('/path', fn => ...)   (closure — no controller)
_SINGLE_ROUTE_RE = re.compile(
    r"Route\s*::\s*(get|post|put|patch|delete|options|head|any)\s*\("
    r"\s*['\"]([^'\"]+)['\"]"
    r"(?:\s*,\s*"
    r"(?:"
    r"\[\s*([A-Za-z_\\]+)::class\s*,\s*['\"]([A-Za-z_]+)['\"]\s*\]"
    r"|['\"]([A-Za-z_\\]+)@([A-Za-z_]+)['\"]"
    r"))?"
    r"\s*\)",
    re.IGNORECASE,
)

# Resource / apiResource routes (these expand to multiple methods):
#   Route::resource('name', Controller::class)
#   Route::apiResource('name', Controller::class)
_RESOURCE_ROUTE_RE = re.compile(
    r"Route\s*::\s*(apiResource|resource)\s*\("
    r"\s*['\"]([^'\"]+)['\"]"
    r"\s*,\s*([A-Za-z_\\]+)::class",
    re.IGNORECASE,
)

# Route groups whose prefix composes child route paths:
#   Route::prefix('mydata')->group(function () { Route::post('/bulk-send', ...) })
#   -> the child route's REAL path is /mydata/bulk-send (the FE calls that, not /bulk-send)
# Matches ``prefix('X') ...optional ->name()/->middleware() chain... ->group(``. The prefix
# string is consumed by the quoted group so route params inside it (e.g. 'x/{id}/y') don't
# break parsing, and ``[^;{]`` stops the chain before the group body's opening brace.
_PREFIX_GROUP_RE = re.compile(
    r"prefix\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
    r"[^;{]*?"
    r"->\s*group\s*\(",
    re.IGNORECASE,
)

# Standard REST methods for apiResource (create/edit excluded)
_APIRESOURCE_METHODS = {
    "index": "GET",
    "store": "POST",
    "show": "GET",
    "update": "PUT",
    "destroy": "DELETE",
}

# Full resource adds create/edit
_RESOURCE_METHODS = {
    **_APIRESOURCE_METHODS,
    "create": "GET",
    "edit": "GET",
}

_HTTP_METHOD_MAP = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "options": "OPTIONS",
    "head": "HEAD",
    "any": "ANY",
}


def _scan_to_matching_brace(source: str, open_idx: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``open_idx``.

    String- and comment-aware: PHP route params (``{id}``) live inside quoted
    strings and must not be counted, so we skip ``'...'`` / ``"..."`` strings
    (with ``\\`` escapes) and ``//`` / ``#`` / ``/* */`` comments. Returns
    ``len(source)`` if unbalanced (treated as running to EOF).
    """
    depth = 0
    i = open_idx
    n = len(source)
    while i < n:
        c = source[i]
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "#":
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _prefix_scopes(source: str) -> list[tuple[str, int, int]]:
    """Return ``(prefix_segment, body_start, body_end)`` for each
    ``Route::prefix('X')->group(function () { ... })`` block, so routes nested
    inside inherit the composed prefix. Groups without an in-file brace body
    (arrow-fn or file-include groups) are skipped — their child paths cannot be
    composed from this file.
    """
    scopes: list[tuple[str, int, int]] = []
    for m in _PREFIX_GROUP_RE.finditer(source):
        seg = m.group(1).strip("/")
        if not seg:
            continue
        brace = source.find("{", m.end())
        if brace == -1:
            continue
        semi = source.find(";", m.end())
        if semi != -1 and semi < brace:
            continue
        end = _scan_to_matching_brace(source, brace)
        scopes.append((seg, brace, end))
    return scopes


def _compose_path(prefix_segs: list[str], raw_path: str) -> str:
    """Join active group prefixes with a child route path into one ``/``-path.

    Handles multi-segment prefixes (``'vat/f2'``) and route params
    (``'{usagePeriod}'``); returns ``/`` for an empty composition.
    """
    parts: list[str] = []
    for seg in prefix_segs:
        parts.extend(p for p in seg.strip("/").split("/") if p)
    for p in raw_path.strip("/").split("/"):
        if p:
            parts.append(p)
    return "/" + "/".join(parts) if parts else "/"


def _extract_routes_from_file(file_path: Path) -> list[dict]:
    """Parse a PHP route file and extract route definitions.

    Returns a list of dicts:
        {method, path, controller, action, file, line}
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    routes = []

    # Prefix-group scopes: routes nested in Route::prefix('x')->group(){...} inherit 'x'
    # (composed onto the child path) so the FE caller of /x/child is found, not /child.
    scopes = _prefix_scopes(source)

    def _line_of(match):
        """Return 1-based line number for a regex match in 'source'."""
        return source[: match.start()].count("\n") + 1

    def _active_prefix_segments(pos: int) -> list[str]:
        """Prefix segments of every group scope containing 'pos', outermost first."""
        active = [(s, seg) for (seg, s, e) in scopes if s <= pos <= e]
        active.sort(key=lambda t: t[0])
        return [seg for _, seg in active]

    # --- Single-action routes ---
    for m in _SINGLE_ROUTE_RE.finditer(source):
        http_verb = _HTTP_METHOD_MAP.get(m.group(1).lower(), m.group(1).upper())
        raw_path = m.group(2)

        # Compose the enclosing group prefix stack onto the child route path.
        path = _compose_path(_active_prefix_segments(m.start()), raw_path)

        # Controller and action
        if m.group(3):  # [Controller::class, 'method'] form
            controller = m.group(3).split("\\")[-1]  # short name
            action = m.group(4)
        elif m.group(5):  # 'Controller@method' form
            controller = m.group(5).split("\\")[-1]
            action = m.group(6)
        else:
            controller = None  # closure-based route
            action = None

        routes.append(
            {
                "method": http_verb,
                "path": path,
                "controller": controller,
                "action": action,
                "file": str(file_path),
                "line": _line_of(m),
            }
        )

    # --- Resource / apiResource routes ---
    for m in _RESOURCE_ROUTE_RE.finditer(source):
        is_api = m.group(1).lower() == "apiresource"
        resource_name = m.group(2).strip("/")
        controller = m.group(3).split("\\")[-1]
        method_map = _APIRESOURCE_METHODS if is_api else _RESOURCE_METHODS
        line = _line_of(m)
        prefix_segs = _active_prefix_segments(m.start())

        for action, http_verb in method_map.items():
            if action == "index":
                path = f"/{resource_name}"
            elif action == "store":
                path = f"/{resource_name}"
            elif action == "create":
                path = f"/{resource_name}/create"
            elif action == "show":
                path = f"/{resource_name}/{{id}}"
            elif action == "edit":
                path = f"/{resource_name}/{{id}}/edit"
            elif action in ("update", "destroy"):
                path = f"/{resource_name}/{{id}}"
            else:
                path = f"/{resource_name}"

            path = _compose_path(prefix_segs, path)
            routes.append(
                {
                    "method": http_verb,
                    "path": path,
                    "controller": controller,
                    "action": action,
                    "file": str(file_path),
                    "line": line,
                }
            )

    return routes


def _find_route_files(project_root: Path) -> list[Path]:
    """Locate PHP route files in the project."""
    candidates = []
    routes_dir = project_root / "routes"
    if routes_dir.is_dir():
        for php_file in routes_dir.glob("*.php"):
            candidates.append(php_file)

    # Also check common alternate locations
    for alt in ["app/routes", "src/routes"]:
        alt_dir = project_root / alt
        if alt_dir.is_dir():
            for php_file in alt_dir.glob("*.php"):
                candidates.append(php_file)

    return candidates


def _should_skip_route(route: dict) -> bool:
    """Return True if this route should be excluded from orphan analysis."""
    path = route["path"]
    # Skip infra / telescope / horizon etc.
    if _SKIP_PATH_PATTERNS.search(path):
        return True
    # Skip pure auth routes by first segment
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    first = segments[0] if segments else ""
    if _SKIP_NAMES.match(first):
        return True
    return False


# ---------------------------------------------------------------------------
# Path segment extraction
# ---------------------------------------------------------------------------


def _path_segments(route_path: str) -> list[str]:
    """Extract meaningful segments from a route path for searching.

    '/api/orders/{id}/items' → ['orders', 'items']
    Drops the 'api' prefix (too generic) and parameter placeholders.
    """
    parts = route_path.strip("/").split("/")
    segments = []
    for p in parts:
        if p.startswith("{") or p in ("api", "v1", "v2", "v3", ""):
            continue
        segments.append(p)
    return segments


# ---------------------------------------------------------------------------
# Codebase searching
# ---------------------------------------------------------------------------


def _search_with_git_grep(pattern: str, project_root: Path, extra_args: list[str] | None = None) -> list[str]:
    """Run git grep and return matching file paths (unique)."""
    cmd = ["git", "grep", "-l", "-I", "--no-color", "-F", pattern]
    if extra_args:
        cmd.extend(extra_args)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode <= 1:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as _exc:
        # A git-grep failure silently yields no matches — surface lineage
        # so a missed route reference has a discoverable cause.
        from roam.observability import log_swallowed

        log_swallowed("cmd_orphan_routes:search_with_git_grep", _exc)
    return []


def _search_in_files(segment: str, project_root: Path, file_paths: list[str]) -> list[str]:
    """Fallback: search for segment in a list of files by reading them."""
    matches = []
    pattern = re.compile(re.escape(segment), re.IGNORECASE)
    for rel_path in file_paths:
        full = project_root / rel_path
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                matches.append(rel_path)
        except OSError:
            continue
    return matches


def _classify_match_files(matching_files: list[str]) -> dict:
    """Classify a list of matching file paths.

    Returns:
        {
          'frontend': [...],   # .vue, .ts, .js etc.
          'backend_test': [...],  # PHP test/spec files
          'backend_other': [...], # seeders, factories, other PHP
          'docs': [...],          # .md etc.
        }
    """
    result: dict[str, list] = {
        "frontend": [],
        "backend_test": [],
        "backend_other": [],
        "docs": [],
    }
    for f in matching_files:
        norm = f.replace("\\", "/")
        ext = os.path.splitext(f)[1].lower()
        if ext in _FRONTEND_EXTENSIONS:
            result["frontend"].append(f)
        elif ext in _BACKEND_EXTENSIONS:
            if _BACKEND_TEST_PATTERNS.search(norm):
                result["backend_test"].append(f)
            else:
                result["backend_other"].append(f)
        elif ext in {".md", ".rst", ".txt"}:
            result["docs"].append(f)
        # else: ignore binary, lock files, etc.
    return result


def _determine_confidence(classified: dict) -> str:
    """Compute orphan confidence from classified match files.

    high:   no references at all (outside route files + controller)
    medium: referenced in backend-only files (tests/seeders) but not frontend
    low:    referenced in docs/comments only
    """
    if classified["frontend"]:
        return "used"  # has a consumer — not orphan

    if not any(classified.values()):
        return "high"

    if classified["backend_test"] or classified["backend_other"]:
        return "medium"

    if classified["docs"]:
        return "low"

    return "high"


# ---------------------------------------------------------------------------
# Controller method analysis
# ---------------------------------------------------------------------------

_RE_PUBLIC_METHOD = re.compile(
    r"public\s+function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.IGNORECASE,
)


def _extract_controller_public_methods(project_root: Path, controller_name: str) -> list[str]:
    """Find public methods in a controller PHP file.

    Returns a list of method names.
    """
    if not controller_name:
        return []

    # Search for the controller file
    matches = _search_with_git_grep(
        f"class {controller_name}",
        project_root,
        ["--", "*.php"],
    )

    for rel_path in matches:
        full = project_root / rel_path
        try:
            source = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        methods = _RE_PUBLIC_METHOD.findall(source)
        # Filter out magic methods
        return [m for m in methods if not m.startswith("__")]

    return []


def _find_routed_actions(routes: list[dict]) -> dict[str, set]:
    """Build a map of controller → set of routed action names."""
    routed: dict[str, set] = defaultdict(set)
    for r in routes:
        if r["controller"] and r["action"]:
            routed[r["controller"]].add(r["action"])
    return routed


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def _analyse_orphan_routes(project_root: Path, conn, limit: int) -> dict:
    """Run the full orphan routes analysis.

    Returns a dict with:
        routes_total, routes_with_consumers, orphans (list), unrouted_methods (list)
    """
    # Step 1: Locate and parse route files
    route_files = _find_route_files(project_root)
    all_routes: list[dict] = []
    for rf in route_files:
        all_routes.extend(_extract_routes_from_file(rf))

    # Filter skippable routes
    all_routes = [r for r in all_routes if not _should_skip_route(r)]

    # Also get the set of all indexed file paths from the DB (for fallback search)
    indexed_files = [row["path"] for row in conn.execute("SELECT path FROM files").fetchall()]

    # Determine which route files and controller files to exclude from consumer search
    route_file_prefixes = set()
    for rf in route_files:
        try:
            rel = str(rf.relative_to(project_root)).replace("\\", "/")
        except ValueError:
            rel = str(rf)
        route_file_prefixes.add(rel)

    orphans: list[dict] = []
    routes_with_consumers = 0

    for route in all_routes:
        segments = _path_segments(route["path"])
        if not segments:
            # Path has no meaningful segments (e.g. '/' or '/api')
            routes_with_consumers += 1
            continue

        # Collect all files that reference any segment of this route
        all_matching: list[str] = []

        for segment in segments:
            if len(segment) < 3:
                # Too short (e.g. 'id') — too noisy to search
                continue

            # Primary: git grep (fastest)
            found = _search_with_git_grep(segment, project_root)
            if not found:
                # Fallback: manual search in indexed files
                found = _search_in_files(segment, project_root, indexed_files)

            all_matching.extend(found)

        # Deduplicate
        all_matching = list(set(all_matching))

        # Remove the route files themselves and the controller file from matches.
        # `_ctrl` and `_route_files` are bound as defaults so the closure
        # captures THIS iteration's values (per-loop B023 fix).
        controller_name = route.get("controller") or ""

        def _is_self_reference(
            file_path: str,
            _ctrl: str = controller_name,
            _route_files: set = route_file_prefixes,
        ) -> bool:
            norm = file_path.replace("\\", "/")
            if norm in _route_files:
                return True
            if _ctrl and _ctrl.lower() in norm.lower():
                if "controller" in norm.lower() or "http" in norm.lower():
                    return True
            return False

        consumer_files = [f for f in all_matching if not _is_self_reference(f)]

        classified = _classify_match_files(consumer_files)
        confidence = _determine_confidence(classified)

        if confidence == "used":
            routes_with_consumers += 1
            continue

        # It's an orphan candidate
        orphan = {
            "confidence": confidence,
            "method": route["method"],
            "path": route["path"],
            "controller": route["controller"],
            "action": route["action"],
            "file": route["file"],
            "line": route["line"],
            "found_in": {
                "backend_test": classified["backend_test"][:3],
                "backend_other": classified["backend_other"][:3],
                "docs": classified["docs"][:3],
            },
        }
        orphans.append(orphan)

    # Sort: high → medium → low (W596: canonical rank, negated for ascending order).
    orphans.sort(key=lambda o: -confidence_level_rank(o["confidence"], fallback=-1))

    if limit:
        orphans = orphans[:limit]

    # Step 2: Find controller methods that aren't mapped to any route
    routed_actions = _find_routed_actions(all_routes)
    unrouted_methods: list[dict] = []

    seen_controllers: set[str] = set()
    for route in all_routes:
        ctrl = route.get("controller")
        if not ctrl or ctrl in seen_controllers:
            continue
        seen_controllers.add(ctrl)

        all_methods = _extract_controller_public_methods(project_root, ctrl)
        mapped = routed_actions.get(ctrl, set())
        for method in all_methods:
            if method not in mapped:
                # Also skip common Laravel lifecycle / base methods
                if method in {
                    "callAction",
                    "middleware",
                    "getMiddleware",
                    "authorize",
                    "authorizeResource",
                    "authorizeForUser",
                    "dispatch",
                    "dispatchNow",
                    "validate",
                    "validateWithBag",
                }:
                    continue
                unrouted_methods.append(
                    {
                        "controller": ctrl,
                        "method": method,
                    }
                )

    return {
        "routes_total": len(all_routes),
        "routes_with_consumers": routes_with_consumers,
        "orphans": orphans,
        "unrouted_methods": unrouted_methods,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="orphan-routes",
    category="reports",
    summary="Find backend API routes that have no frontend consumers (dead endpoints)",
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
@click.command("orphan-routes")
@click.option("--limit", "-n", default=50, show_default=True, help="Maximum number of orphan findings to show")
@click.option(
    "--confidence",
    "confidence_filter",
    default=None,
    # W1005-followup-D: widened from 3-tier {high, medium, low} to the W547
    # canonical 7-tier so agents can pass any of {critical, error, high,
    # warning, medium, low, info} and have the floor compared via
    # ``severity_rank()`` from ``roam.output._severity``. The detector emits
    # only {high, medium, low} (the CVSS 3-tier) but the Choice accepts the
    # full canonical vocabulary so canonical-aware agents can pass any tier.
    # Semantic change: equality → floor (pre-fix kept orphans with EXACTLY
    # that confidence; post-fix keeps orphans AT OR ABOVE that rank).
    type=click.Choice(
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    help=(
        "Minimum confidence floor. Uses the canonical W547 7-tier ordering "
        "(critical > error == high > warning > medium > low > info). Detector "
        "emits high/medium/low today; canonical aliases rank via the same "
        "severity_rank() comparator."
    ),
)
@click.option(
    "--no-unrouted",
    "skip_unrouted",
    is_flag=True,
    default=False,
    help="Skip controller method analysis (faster)",
)
@click.pass_context
def orphan_routes_cmd(ctx, limit, confidence_filter, skip_unrouted):
    """Find backend API routes that have no frontend consumers (dead endpoints).

    Parses routes/api.php and routes/web.php, extracts route definitions,
    then searches the codebase for references to each route's path segments.

    Unlike ``api-drift`` (which compares PHP model fields against TypeScript
    type definitions) and ``over-fetch`` (which detects models exposing too
    many fields), this command finds routes with zero frontend callers.

    Confidence levels:

    \b
      high:   path segment not found in any file outside route files / controller
      medium: path found only in backend files (tests, seeders) — not frontend
      low:    path found only in docs or comments

    Skips infrastructure routes (telescope, horizon, health checks, webhooks).
    Also reports public controller methods that are not mapped to any route.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        result = _analyse_orphan_routes(project_root, conn, limit)

    orphans = result["orphans"]
    unrouted = result["unrouted_methods"]
    routes_total = result["routes_total"]
    routes_with_consumers = result["routes_with_consumers"]

    # Apply confidence floor — W1005-followup-D: equality → floor via
    # canonical severity_rank(). Detector emits {high, medium, low}; the
    # Click Choice accepts the full W547 7-tier. Floor keeps an orphan when
    # ``severity_rank(o.confidence) >= severity_rank(confidence_filter)``.
    if confidence_filter:
        _floor_rank = severity_rank(confidence_filter)
        orphans = [o for o in orphans if severity_rank(o["confidence"]) >= _floor_rank]

    # --- SARIF output (W1227) -------------------------------------------
    # SARIF surfaces the closed-enum confidence rule catalogue
    # (single rule: orphan-route) even on a clean / no-orphans scan so
    # CI consumers see the rule vocabulary regardless of whether any
    # finding fired. The ``used`` bucket is filtered upstream by
    # ``orphan_routes_to_sarif`` (not actionable — has a frontend
    # consumer).
    if sarif_mode:
        from roam.output.sarif import orphan_routes_to_sarif, write_sarif

        click.echo(write_sarif(orphan_routes_to_sarif(orphans)))
        return

    n_high = sum(1 for o in orphans if o["confidence"] == "high")
    n_medium = sum(1 for o in orphans if o["confidence"] == "medium")
    n_low = sum(1 for o in orphans if o["confidence"] == "low")

    if routes_total == 0:
        msg = "No routes found. Ensure routes/api.php or routes/web.php exist and the roam index is up to date."
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "orphan-routes",
                        summary={"verdict": "no routes found", "error": msg},
                    )
                )
            )
        else:
            click.echo(f"VERDICT: no routes found — {msg}")
        return

    # --- JSON output ---
    if json_mode:
        clean_orphans = []
        for o in orphans:
            entry = {
                "confidence": o["confidence"],
                "method": o["method"],
                "path": o["path"],
                "location": loc(o["file"], o["line"]),
            }
            if o["controller"]:
                entry["controller"] = o["controller"]
                entry["action"] = o["action"]
            refs = {k: v for k, v in o["found_in"].items() if v}
            if refs:
                entry["referenced_in"] = refs
            clean_orphans.append(entry)

        clean_unrouted = (
            [] if skip_unrouted else [{"controller": u["controller"], "method": u["method"]} for u in unrouted]
        )

        click.echo(
            to_json(
                json_envelope(
                    "orphan-routes",
                    summary={
                        "verdict": f"{len(orphans)} orphan routes found "
                        f"({n_high} high, {n_medium} medium, {n_low} low)",
                        "routes_total": routes_total,
                        "routes_with_consumers": routes_with_consumers,
                        "orphans_found": len(orphans),
                        "high": n_high,
                        "medium": n_medium,
                        "low": n_low,
                        "unrouted_controller_methods": len(clean_unrouted),
                    },
                    orphans=clean_orphans,
                    unrouted_methods=clean_unrouted,
                )
            )
        )
        return

    # --- Text output ---
    click.echo("=== Orphan Routes ===\n")
    click.echo(f"VERDICT: {len(orphans)} orphan routes found ({n_high} high, {n_medium} medium, {n_low} low)")
    click.echo()
    click.echo(f"  Routes defined:        {routes_total}")
    click.echo(f"  Routes with consumers: {routes_with_consumers}")
    click.echo(f"  Potentially orphaned:  {len(orphans)}")
    click.echo()

    if not orphans:
        click.echo("  All routes appear to have consumers — no orphans detected.")
    else:
        for o in orphans:
            conf_label = f"[{o['confidence']}]"
            controller_str = ""
            if o["controller"] and o["action"]:
                controller_str = f"\n          Controller: {o['controller']}::{o['action']}"
            elif o["controller"]:
                controller_str = f"\n          Controller: {o['controller']}"

            location_str = loc(o["file"], o["line"])

            refs = {k: v for k, v in o["found_in"].items() if v}
            if refs:
                ref_parts = []
                for kind, files in refs.items():
                    label = {
                        "backend_test": "tests",
                        "backend_other": "backend",
                        "docs": "docs",
                    }.get(kind, kind)
                    ref_parts.append(f"{label}: {', '.join(files[:2])}")
                ref_str = f"\n          Only referenced in: {'; '.join(ref_parts)}"
            else:
                ref_str = "\n          No references found in codebase"

            click.echo(f"  {conf_label:<10} {o['method']} {o['path']}  {location_str}{controller_str}{ref_str}")
            click.echo()

    if not skip_unrouted and unrouted:
        click.echo(f"-- Controller methods with no route mapping ({len(unrouted)}) --\n")
        rows = []
        for u in unrouted[:30]:
            rows.append([u["controller"], u["method"]])
        click.echo(
            format_table(
                ["Controller", "Method"],
                rows,
                budget=30,
            )
        )
        if len(unrouted) > 30:
            click.echo(f"  (+{len(unrouted) - 30} more)")
        click.echo()
