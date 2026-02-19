"""Find backend API routes that have no consumers in the codebase (dead endpoints)."""

from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import click

from roam.db.connection import find_project_root, open_db
from roam.output.formatter import loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


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

# Frontend file extensions to search
_FRONTEND_EXTENSIONS = {
    ".vue", ".ts", ".tsx", ".js", ".jsx",
    ".mts", ".mjs", ".cts", ".cjs",
}

# Backend-only file patterns (tests, seeders, etc.)
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

# Route groups with prefix:
#   Route::prefix('prefix')->group(...)
_PREFIX_RE = re.compile(
    r"Route\s*::\s*(?:[^;]*?->)?\s*prefix\s*\(\s*['\"]([^'\"]+)['\"]",
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
    "get": "GET", "post": "POST", "put": "PUT",
    "patch": "PATCH", "delete": "DELETE", "options": "OPTIONS",
    "head": "HEAD", "any": "ANY",
}


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
    lines = source.splitlines()

    # Track active prefix stack (very simplified — handles one level)
    # We do a first pass to collect prefix blocks, then match routes.
    # For the purposes of orphan detection the path segment matching is
    # loose, so prefix tracking is best-effort.
    current_prefix = ""
    prefix_match = _PREFIX_RE.search(source)
    if prefix_match:
        current_prefix = prefix_match.group(1).strip("/")

    def _line_of(match):
        """Return 1-based line number for a regex match in 'source'."""
        return source[: match.start()].count("\n") + 1

    # --- Single-action routes ---
    for m in _SINGLE_ROUTE_RE.finditer(source):
        http_verb = _HTTP_METHOD_MAP.get(m.group(1).lower(), m.group(1).upper())
        raw_path = m.group(2)

        # Normalise path — make it start with /
        if not raw_path.startswith("/"):
            raw_path = "/" + raw_path

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

        routes.append({
            "method": http_verb,
            "path": raw_path,
            "controller": controller,
            "action": action,
            "file": str(file_path),
            "line": _line_of(m),
        })

    # --- Resource / apiResource routes ---
    for m in _RESOURCE_ROUTE_RE.finditer(source):
        is_api = m.group(1).lower() == "apiresource"
        resource_name = m.group(2).strip("/")
        controller = m.group(3).split("\\")[-1]
        method_map = _APIRESOURCE_METHODS if is_api else _RESOURCE_METHODS
        line = _line_of(m)

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

            routes.append({
                "method": http_verb,
                "path": path,
                "controller": controller,
                "action": action,
                "file": str(file_path),
                "line": line,
            })

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

def _search_with_git_grep(pattern: str, project_root: Path,
                          extra_args: list[str] | None = None) -> list[str]:
    """Run git grep and return matching file paths (unique)."""
    cmd = ["git", "grep", "-l", "-I", "--no-color", "-F", pattern]
    if extra_args:
        cmd.extend(extra_args)
    try:
        result = subprocess.run(
            cmd, cwd=str(project_root),
            capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="replace",
        )
        if result.returncode <= 1:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return []


def _search_with_git_grep_regex(pattern: str, project_root: Path) -> list[str]:
    """Run git grep with regex and return matching file paths (unique)."""
    cmd = ["git", "grep", "-l", "-I", "--no-color", "-E", pattern]
    try:
        result = subprocess.run(
            cmd, cwd=str(project_root),
            capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="replace",
        )
        if result.returncode <= 1:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return []


def _search_in_files(segment: str, project_root: Path,
                     file_paths: list[str]) -> list[str]:
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

def _extract_controller_public_methods(project_root: Path,
                                       controller_name: str) -> list[str]:
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

        # Extract public function names
        method_re = re.compile(
            r"public\s+function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.IGNORECASE,
        )
        methods = method_re.findall(source)
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

def _analyse_orphan_routes(project_root: Path,
                           conn,
                           limit: int) -> dict:
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

        # Remove the route files themselves and the controller file from matches
        controller_name = route.get("controller") or ""
        controller_snake = re.sub(r'([A-Z])', r'_\1', controller_name).lower().lstrip("_")

        def _is_self_reference(file_path: str) -> bool:
            norm = file_path.replace("\\", "/")
            # Exclude the route definition files
            if norm in route_file_prefixes:
                return True
            # Exclude the controller file itself
            if controller_name and controller_name.lower() in norm.lower():
                # Check it actually looks like a controller path
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

    # Sort: high → medium → low
    _confidence_order = {"high": 0, "medium": 1, "low": 2}
    orphans.sort(key=lambda o: _confidence_order.get(o["confidence"], 9))

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
                    "callAction", "middleware", "getMiddleware",
                    "authorize", "authorizeResource", "authorizeForUser",
                    "dispatch", "dispatchNow", "validate", "validateWithBag",
                }:
                    continue
                unrouted_methods.append({
                    "controller": ctrl,
                    "method": method,
                })

    return {
        "routes_total": len(all_routes),
        "routes_with_consumers": routes_with_consumers,
        "orphans": orphans,
        "unrouted_methods": unrouted_methods,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("orphan-routes")
@click.option("--limit", "-n", default=50, show_default=True,
              help="Maximum number of orphan findings to show")
@click.option("--confidence", "confidence_filter", default=None,
              type=click.Choice(["high", "medium", "low"], case_sensitive=False),
              help="Only show orphans of a specific confidence level")
@click.option("--no-unrouted", "skip_unrouted", is_flag=True, default=False,
              help="Skip controller method analysis (faster)")
@click.pass_context
def orphan_routes_cmd(ctx, limit, confidence_filter, skip_unrouted):
    """Find backend API routes that have no frontend consumers (dead endpoints).

    Parses routes/api.php and routes/web.php, extracts route definitions,
    then searches the codebase for references to each route's path segments.

    Confidence levels:

    \b
      high:   path segment not found in any file outside route files / controller
      medium: path found only in backend files (tests, seeders) — not frontend
      low:    path found only in docs or comments

    Skips infrastructure routes (telescope, horizon, health checks, webhooks).
    Also reports public controller methods that are not mapped to any route.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        result = _analyse_orphan_routes(project_root, conn, limit)

    orphans = result["orphans"]
    unrouted = result["unrouted_methods"]
    routes_total = result["routes_total"]
    routes_with_consumers = result["routes_with_consumers"]

    # Apply confidence filter if requested
    if confidence_filter:
        orphans = [o for o in orphans if o["confidence"] == confidence_filter.lower()]

    n_high = sum(1 for o in orphans if o["confidence"] == "high")
    n_medium = sum(1 for o in orphans if o["confidence"] == "medium")
    n_low = sum(1 for o in orphans if o["confidence"] == "low")

    if routes_total == 0:
        msg = (
            "No routes found. Ensure routes/api.php or routes/web.php exist "
            "and the roam index is up to date."
        )
        if json_mode:
            click.echo(to_json(json_envelope("orphan-routes",
                summary={"verdict": "no routes found", "error": msg},
            )))
        else:
            click.echo(msg)
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

        clean_unrouted = [] if skip_unrouted else [
            {"controller": u["controller"], "method": u["method"]}
            for u in unrouted
        ]

        click.echo(to_json(json_envelope("orphan-routes",
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
        )))
        return

    # --- Text output ---
    click.echo("=== Orphan Routes ===\n")
    click.echo(
        f"VERDICT: {len(orphans)} orphan routes found "
        f"({n_high} high, {n_medium} medium, {n_low} low)"
    )
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

            click.echo(
                f"  {conf_label:<10} {o['method']} {o['path']}  {location_str}"
                f"{controller_str}"
                f"{ref_str}"
            )
            click.echo()

    if not skip_unrouted and unrouted:
        click.echo(f"-- Controller methods with no route mapping ({len(unrouted)}) --\n")
        rows = []
        for u in unrouted[:30]:
            rows.append([u["controller"], u["method"]])
        click.echo(format_table(
            ["Controller", "Method"],
            rows,
            budget=30,
        ))
        if len(unrouted) > 30:
            click.echo(f"  (+{len(unrouted) - 30} more)")
        click.echo()
