"""Find controller endpoints and routes missing authentication or authorization checks."""
from __future__ import annotations

import os
import re
from collections import defaultdict

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Skip patterns — controllers / routes that are intentionally public
# ---------------------------------------------------------------------------

_SKIP_CONTROLLER_PATTERNS = re.compile(
    r"Auth|Login|Register|Password|Logout|Sanctum|Csrf|Health|Webhook|Ping|Status",
    re.IGNORECASE,
)

_SKIP_ROUTE_PATTERNS = re.compile(
    r"""
    # Health / monitoring
    /health | /ping | /status | /up |
    # Public webhook receivers
    /webhook | /hooks? |
    # Public API / docs
    /docs | /swagger | /openapi | /api-docs |
    # Auth endpoints themselves
    /login | /logout | /register | /forgot | /reset | /verify |
    /oauth | /sanctum | /csrf |
    # Public assets
    \.(jpg|jpeg|png|gif|svg|ico|css|js|woff|woff2|ttf|map)$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Regex matching a public-marker comment on the same line or the preceding line
_PUBLIC_COMMENT = re.compile(r"#\s*(public|no.?auth|unauthenticated|open)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Auth patterns
# ---------------------------------------------------------------------------

# Class-level middleware in constructor
_CONSTRUCTOR_MIDDLEWARE_RE = re.compile(
    r"""\$this\s*->\s*middleware\s*\(\s*['"]auth(?::sanctum)?['"]""",
    re.IGNORECASE,
)

# Route-level middleware in routes files
_ROUTE_AUTH_MIDDLEWARE_RE = re.compile(
    r"""(?:->middleware\s*\(\s*['"]auth(?::sanctum)?['"]|
           middleware\s*\(\s*['"]auth(?::sanctum)?['"])""",
    re.IGNORECASE | re.VERBOSE,
)

# Authorization calls inside a method body
_AUTHORIZATION_RE = re.compile(
    r"""
    \$this\s*->\s*authorize\s*\(         # $this->authorize(
    | Gate\s*::\s*(?:allows|denies|check|authorize|inspect)\s*\(  # Gate::allows(
    | \$request\s*->\s*user\s*\(\s*\)\s*->\s*can\s*\(             # $request->user()->can(
    | \$user\s*->\s*can\s*\(                                       # $user->can(
    | \$this\s*->\s*authorizeResource\s*\(                         # $this->authorizeResource(
    | Policy\s*::\s*authorize\s*\(                                 # Policy::authorize(
    | can\s*\(\s*['"]                                              # can('...')
    | cannot\s*\(\s*['"]                                           # cannot('...')
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Route group open: Route::middleware('auth')->group(  or  middleware(['auth'])->group(
_ROUTE_GROUP_OPEN_RE = re.compile(
    r"""Route\s*::\s*(?:middleware|prefix|group).*?middleware\s*\(\s*\[?['"]auth(?::sanctum)?['"]
    | Route\s*::\s*middleware\s*\(\s*\[?['"]auth(?::sanctum)?['"]""",
    re.IGNORECASE | re.VERBOSE,
)

# Route group alternative: middleware(['auth:sanctum', ...])
_ROUTE_GROUP_MIDDLEWARE_RE = re.compile(
    r"""->middleware\s*\(\s*(?:\[['"]auth(?::sanctum)?['"]|\s*['"]auth(?::sanctum)?['"]\s*\))""",
    re.IGNORECASE | re.VERBOSE,
)

# Explicit route definitions (verb + path)
_ROUTE_DEFINITION_RE = re.compile(
    r"""Route\s*::\s*(get|post|put|patch|delete|any|match|resource|apiResource)\s*
        \(\s*(['"][^'"]*['"])""",
    re.IGNORECASE | re.VERBOSE,
)

# CRUD action names that warrant authorization checks
_CRUD_ACTIONS = frozenset({"store", "update", "destroy", "create", "delete", "edit"})
_READ_ACTIONS = frozenset({"index", "show", "list", "view", "get"})

# Public controller method pattern (PHP public function X)
_PUBLIC_METHOD_RE = re.compile(
    r"""^\s*public\s+function\s+(\w+)\s*\(""",
    re.MULTILINE,
)

# Constructor method (exclude from reporting — it's where class-level middleware goes)
_CONSTRUCTOR_RE = re.compile(r"^__construct$")


# ---------------------------------------------------------------------------
# Source reading helpers
# ---------------------------------------------------------------------------

def _read_source(file_path: str) -> str | None:
    """Read source file as text, returning None on failure."""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _resolve_path(project_root, db_path: str) -> str:
    """Resolve a DB-relative path to an absolute filesystem path."""
    p = os.path.join(str(project_root), db_path)
    return os.path.normpath(p)


def _count_braces(line: str) -> tuple[int, int]:
    """Count ``{`` and ``}`` that appear outside quoted strings.

    Route files contain URL parameters like ``{id}`` inside string literals.
    Naively counting all braces would mis-track brace depth (e.g.
    ``Route::get('/users/{id}', function () {`` has 2 naive opens but only
    1 real open).
    """
    opens = 0
    closes = 0
    in_string: str | None = None  # None, "'", or '"'
    prev_backslash = False
    for ch in line:
        if prev_backslash:
            prev_backslash = False
            continue
        if ch == "\\":
            prev_backslash = True
            continue
        if in_string is not None:
            if ch == in_string:
                in_string = None
            continue
        if ch == "'":
            in_string = "'"
        elif ch == '"':
            in_string = '"'
        elif ch == "{":
            opens += 1
        elif ch == "}":
            closes += 1
    return opens, closes


# ---------------------------------------------------------------------------
# Route file analysis
# ---------------------------------------------------------------------------

def _analyze_route_file(file_path: str, source: str) -> list[dict]:
    """Parse a routes file and return routes outside an auth middleware group.

    Strategy:
    - Track ``brace_depth`` (every ``{`` and ``}`` in the file).
    - Maintain an ``auth_depth_stack`` of ``(depth_at_open, is_auth)`` tuples.
      Each entry records the brace_depth *before* a Route group's ``{`` was
      counted, so we know at which depth to pop when the matching ``}`` closes.
    - Non-group braces (closures, if-statements, array literals) change
      ``brace_depth`` but never push/pop the stack, so they can't accidentally
      remove auth protection.
    - Handles multi-line middleware arrays like::

        Route::middleware([
            'auth:sanctum',
            SomeMiddleware::class,
        ])->group(function () {
    """
    findings: list[dict] = []
    lines = source.splitlines()

    # (depth_at_open, is_auth) — depth_at_open is brace_depth right before
    # the group's opening ``{``.  We pop when a ``}`` returns us to that depth.
    auth_depth_stack: list[tuple[int, bool]] = []
    brace_depth = 0

    # Multi-line middleware accumulator
    middleware_accumulator: list[str] | None = None

    def _process_closes(n: int) -> None:
        """Decrement brace_depth for *n* closing braces, popping group entries
        whose recorded depth matches."""
        nonlocal brace_depth
        for _ in range(n):
            brace_depth -= 1
            if brace_depth < 0:
                brace_depth = 0
            if auth_depth_stack and auth_depth_stack[-1][0] == brace_depth:
                auth_depth_stack.pop()

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line
        opens, closes = _count_braces(line)

        # --- Multi-line middleware accumulation ---
        if middleware_accumulator is not None:
            middleware_accumulator.append(line)
            if re.search(r'\]\s*\)\s*->\s*group\s*\(', line) or \
               re.search(r'->\s*group\s*\(', line):
                accumulated = "\n".join(middleware_accumulator)
                has_auth = bool(re.search(
                    r"""['"]auth(?::sanctum)?['"]""",
                    accumulated,
                    re.IGNORECASE,
                ))
                middleware_accumulator = None
                _process_closes(closes)
                auth_depth_stack.append((brace_depth, has_auth))
                brace_depth += opens
                continue
            # Still accumulating — track braces but no group push
            _process_closes(closes)
            brace_depth += opens
            continue

        # --- Check for multi-line middleware start ---
        if re.search(r'Route\s*::\s*middleware\s*\(\s*\[', line, re.IGNORECASE) and \
           not re.search(r'->\s*group\s*\(', line):
            middleware_accumulator = [line]
            _process_closes(closes)
            brace_depth += opens
            continue

        # --- Single-line: auth middleware group opener ---
        if _ROUTE_GROUP_OPEN_RE.search(line) or _ROUTE_GROUP_MIDDLEWARE_RE.search(line):
            _process_closes(closes)
            auth_depth_stack.append((brace_depth, True))
            brace_depth += opens
            continue

        # --- Single-line: non-auth group (prefix/name/domain) ---
        if (re.search(r"Route\s*::\s*(?:prefix|name|domain)\s*\([^)]*\)\s*->\s*group\s*\(", line, re.IGNORECASE) or
                re.search(r"Route\s*::\s*group\s*\(", line, re.IGNORECASE)) and opens > closes:
            _process_closes(closes)
            auth_depth_stack.append((brace_depth, False))
            brace_depth += opens
            continue

        # --- Regular line (not a group opener) ---
        _process_closes(closes)

        # Check protection AFTER closes, BEFORE opens
        currently_protected = any(auth for _, auth in auth_depth_stack)

        brace_depth += opens

        if currently_protected:
            continue

        # Check for a route definition on this line
        m = _ROUTE_DEFINITION_RE.search(line)
        if not m:
            continue

        verb = m.group(1).upper()
        path_str = m.group(2).strip("'\"")

        # Skip routes explicitly marked as public / health / webhook
        if _SKIP_ROUTE_PATTERNS.search(path_str):
            continue

        # Check for public-marker comment on this line or the line above
        prev_line = lines[lineno - 2] if lineno >= 2 else ""
        if _PUBLIC_COMMENT.search(line) or _PUBLIC_COMMENT.search(prev_line):
            continue

        # Also skip if there's an inline ->middleware('auth') directly on this line
        if _ROUTE_AUTH_MIDDLEWARE_RE.search(line):
            continue

        findings.append({
            "type": "route",
            "confidence": "high",
            "verb": verb,
            "path": path_str,
            "file": file_path,
            "line": lineno,
            "fix": "Add ->middleware('auth:sanctum') or move inside auth group",
        })

    return findings


# ---------------------------------------------------------------------------
# Controller file analysis
# ---------------------------------------------------------------------------

def _extract_method_bodies(source: str) -> list[dict]:
    """Extract public method name and body from PHP controller source.

    Returns list of {name, start_line, body} dicts.
    Each body is the text of the method from opening to closing brace.
    """
    methods = []
    lines = source.splitlines()

    i = 0
    while i < len(lines):
        m = _PUBLIC_METHOD_RE.match(lines[i])
        if m:
            method_name = m.group(1)
            start_line = i + 1  # 1-based

            # Find opening brace
            brace_depth = 0
            body_lines = []
            j = i
            found_open = False
            while j < len(lines):
                seg = lines[j]
                for ch in seg:
                    if ch == "{":
                        brace_depth += 1
                        found_open = True
                    elif ch == "}":
                        brace_depth -= 1
                body_lines.append(seg)
                if found_open and brace_depth == 0:
                    break
                j += 1

            methods.append({
                "name": method_name,
                "start_line": start_line,
                "body": "\n".join(body_lines),
            })
            i = j + 1
        else:
            i += 1

    return methods


def _analyze_controller_file(file_path: str, source: str) -> list[dict]:
    """Analyze a PHP controller for missing authorization checks.

    Confidence levels:
    - ``high``:   CRUD method (store/update/destroy) without auth in constructor
                  AND without any authorization call in method body
    - ``medium``: CRUD method without explicit Gate/Policy/authorize check
                  (but controller has constructor auth middleware — route-level protection)
    - ``low``:    Read method (index/show) without authorization
    """
    base = os.path.basename(file_path)

    # Skip auth-related controllers
    if _SKIP_CONTROLLER_PATTERNS.search(base):
        return []

    findings = []

    # Check for class-level auth middleware in constructor
    has_constructor_auth = bool(_CONSTRUCTOR_MIDDLEWARE_RE.search(source))

    methods = _extract_method_bodies(source)
    for method in methods:
        name = method["name"]
        body = method["body"]
        line = method["start_line"]

        # Skip constructor and magic methods
        if _CONSTRUCTOR_RE.match(name) or name.startswith("__"):
            continue

        # Determine action category
        name_lower = name.lower()
        is_crud = any(action in name_lower for action in _CRUD_ACTIONS)
        is_read = any(action in name_lower for action in _READ_ACTIONS)

        if not is_crud and not is_read:
            continue

        # Check for explicit authorization call in the method body
        has_auth_check = bool(_AUTHORIZATION_RE.search(body))

        if has_auth_check:
            continue

        if is_crud:
            if not has_constructor_auth:
                confidence = "high"
                reason = "CRUD method with no constructor middleware and no authorization call"
                fix = "Add $this->authorize() or Gate::allows() inside the method"
            else:
                confidence = "medium"
                reason = "CRUD method without explicit authorization (has constructor middleware)"
                fix = "Add $this->authorize('action', $model) for object-level authorization"
        else:
            # Read method
            confidence = "low"
            reason = "Read method without authorization (may be intentionally public)"
            fix = "Add $this->authorize('view', $model) if access should be restricted"

        findings.append({
            "type": "controller",
            "confidence": confidence,
            "controller": _controller_class_name(base),
            "method": name,
            "file": file_path,
            "line": line,
            "reason": reason,
            "fix": fix,
        })

    return findings


def _controller_class_name(basename: str) -> str:
    """Derive class name from file basename (strip .php extension)."""
    return os.path.splitext(basename)[0]


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _find_route_files(conn) -> list[str]:
    """Return file paths for route definition files from the index."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE path LIKE '%routes/api%' "
        "   OR path LIKE '%routes/web%' "
        "   OR path LIKE '%routes/api.php' "
        "   OR path LIKE '%routes/web.php' "
        "   OR path LIKE '%/routes/%' "
        "ORDER BY path"
    ).fetchall()
    return [r["path"] for r in rows]


def _find_controller_files(conn) -> list[str]:
    """Return file paths for PHP controller files from the index."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE (path LIKE '%Controller%' OR path LIKE '%controller%') "
        "  AND (path LIKE '%.php' OR language = 'php') "
        "ORDER BY path"
    ).fetchall()
    return [r["path"] for r in rows]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("auth-gaps")
@click.option("--limit", "-n", default=50, show_default=True, help="Max findings to display")
@click.option("--routes-only", "routes_only", is_flag=True,
              help="Only check route files, skip controller analysis")
@click.option("--controllers-only", "controllers_only", is_flag=True,
              help="Only check controller files, skip route analysis")
@click.option("--min-confidence", "min_confidence",
              type=click.Choice(["high", "medium", "low"], case_sensitive=False),
              default="low", show_default=True,
              help="Minimum confidence level to report")
@click.pass_context
def auth_gaps_cmd(ctx, limit, routes_only, controllers_only, min_confidence):
    """Find endpoints missing authentication or authorization checks.

    Analyses PHP controller files and route definitions to detect:

    \b
    1. Routes outside an auth middleware group (routes/api.php, routes/web.php)
    2. Controller CRUD methods without $this->authorize() / Gate::allows() checks
    3. Controller read methods without any authorization call (low confidence)

    Confidence levels:
      high   - Route outside any auth group AND no controller-level auth
      medium - CRUD method missing explicit object-level authorization
      low    - Read method without authorization (may be intentionally public)
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    _CONF_ORDER = {"high": 0, "medium": 1, "low": 2}
    min_conf_rank = _CONF_ORDER[min_confidence.lower()]

    project_root = find_project_root()
    all_findings: list[dict] = []

    with open_db(readonly=True) as conn:
        # --- Route file analysis ---
        if not controllers_only:
            route_files = _find_route_files(conn)
            for db_path in route_files:
                abs_path = _resolve_path(project_root, db_path)
                source = _read_source(abs_path)
                if source is None:
                    continue
                findings = _analyze_route_file(abs_path, source)
                all_findings.extend(findings)

        # --- Controller file analysis ---
        if not routes_only:
            controller_files = _find_controller_files(conn)
            for db_path in controller_files:
                abs_path = _resolve_path(project_root, db_path)
                source = _read_source(abs_path)
                if source is None:
                    continue
                findings = _analyze_controller_file(abs_path, source)
                all_findings.extend(findings)

    # Apply confidence filter
    all_findings = [
        f for f in all_findings
        if _CONF_ORDER.get(f["confidence"], 99) <= min_conf_rank
    ]

    # Sort: high first, then medium, then low; within tier by file
    all_findings.sort(key=lambda f: (
        _CONF_ORDER.get(f["confidence"], 99),
        f["file"],
        f.get("line", 0),
    ))

    # Counts by confidence
    n_high = sum(1 for f in all_findings if f["confidence"] == "high")
    n_medium = sum(1 for f in all_findings if f["confidence"] == "medium")
    n_low = sum(1 for f in all_findings if f["confidence"] == "low")
    total = len(all_findings)

    # Split by type for display
    route_findings = [f for f in all_findings if f["type"] == "route"]
    ctrl_findings = [f for f in all_findings if f["type"] == "controller"]

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "auth-gaps",
            summary={
                "verdict": f"{total} auth gap(s) found",
                "total": total,
                "high": n_high,
                "medium": n_medium,
                "low": n_low,
                "route_gaps": len(route_findings),
                "controller_gaps": len(ctrl_findings),
            },
            route_gaps=route_findings[:limit],
            controller_gaps=ctrl_findings[:limit],
        )))
        return

    # --- Text output ---
    click.echo("=== Auth Gaps ===\n")

    verdict = f"{total} auth gap(s) found"
    if total:
        parts = []
        if n_high:
            parts.append(f"{n_high} high")
        if n_medium:
            parts.append(f"{n_medium} medium")
        if n_low:
            parts.append(f"{n_low} low")
        verdict += f"  ({', '.join(parts)})"
    click.echo(f"VERDICT: {verdict}\n")

    # --- Routes section ---
    if route_findings:
        click.echo(f"Routes without auth middleware ({len(route_findings)}):")
        rows = []
        for f in route_findings[:limit]:
            rows.append([
                f"[{f['confidence']}]",
                f"{f['verb']}  {f['path']}",
                loc(f["file"], f["line"]),
                f["fix"],
            ])
        click.echo(format_table(
            ["Conf", "Route", "Location", "Fix"],
            rows,
            budget=limit,
        ))
        click.echo()
    else:
        click.echo("Routes without auth middleware: (none found)\n")

    # --- Controllers section ---
    if ctrl_findings:
        click.echo(f"Controllers without authorization ({len(ctrl_findings)}):")
        rows = []
        for f in ctrl_findings[:limit]:
            rows.append([
                f"[{f['confidence']}]",
                f"{f['controller']}::{f['method']}",
                loc(f["file"], f["line"]),
                f["fix"],
            ])
        click.echo(format_table(
            ["Conf", "Symbol", "Location", "Fix"],
            rows,
            budget=limit,
        ))
        click.echo()
    else:
        click.echo("Controllers without authorization: (none found)\n")

    if total == 0:
        click.echo("No auth gaps detected.")
    elif n_high == 0:
        click.echo("No high-confidence gaps found. Review medium/low findings manually.")
