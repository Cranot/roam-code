"""Find controller endpoints and routes missing authentication or authorization checks."""

from __future__ import annotations

import hashlib
import json as _json
import os
import re
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import format_table, json_envelope, loc, to_json

# W116 — auth-gaps is the fourth detector migrating onto the central
# findings registry (after `clones` in W95, `dead` in W99, and
# `complexity` in W102). The shape mirrors those — a stable detector
# version stamp + deterministic ``finding_id_str`` so re-runs upsert
# instead of duplicating rows. Bump when the route/controller analysis
# semantics or the kind→confidence-tier mapping changes meaningfully.
AUTH_GAPS_DETECTOR_VERSION: str = "1.0.0"

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
    r"""\$this\s*->\s*middleware\s*\(\s*\[?\s*['"]auth(?::sanctum)?['"]""",
    re.IGNORECASE,
)

# `class Foo extends Bar` → Bar is the parent class.
# Used to walk the inheritance chain so a child controller inherits the
# auth middleware its parent's __construct adds. Without this, every
# subclass of a base controller (DynamicResourceController, ApiController,
# etc.) gets falsely flagged as missing auth even when the base wires
# `$this->middleware('auth')` once.
_RE_CLASS_EXTENDS = re.compile(
    r"""\bclass\s+(\w+)\s+extends\s+(\\?[\w\\]+)""",
    re.IGNORECASE,
)

# Route-level middleware in routes files
_ROUTE_AUTH_MIDDLEWARE_RE = re.compile(
    r"""(?:->middleware\s*\(\s*['"]auth(?::sanctum)?['"]|
           middleware\s*\(\s*['"]auth(?::sanctum)?['"])""",
    re.IGNORECASE | re.VERBOSE,
)

# M10 — non-auth route guards. When a route has throttle/signed/verified/
# can/web middleware but no `auth:*`, the user's intent is "public but
# rate-limited" or "public but signed-URL-protected", not "missing auth".
# Detector should distinguish "no middleware AT ALL" (real gap) from
# "no auth-middleware but other guards present" (intentional public).
_NON_AUTH_GUARD_RE = re.compile(
    r"""->middleware\s*\(\s*\[?['"](?:
        throttle(?::[\w,]+)?|       # throttle:60,1
        signed|                       # signed-URL routes
        verified|                     # email-verified gate
        cache\.headers|               # response cache
        can:[\w.]+|                   # ACL-only (no auth)
        scope:[\w,]+                  # OAuth scope only
    )['"]""",
    re.IGNORECASE | re.VERBOSE,
)

# Authorization calls inside a method body
_AUTHORIZATION_RE = re.compile(
    r"""
    \$this\s*->\s*authorize\s*\(         # $this->authorize(
    | Gate\s*::\s*(?:allows|denies|check|authorize|inspect|any|none)\s*\(  # Gate::allows( / Gate::any(
    | \$request\s*->\s*user\s*\(\s*\)\s*->\s*can\s*\(             # $request->user()->can(
    | \$user\s*->\s*can\s*\(                                       # $user->can(
    | auth\s*\(\s*\)\s*->\s*user\s*\(\s*\)\s*->\s*can\s*\(       # auth()->user()->can(
    | \$this\s*->\s*authorizeResource\s*\(                         # $this->authorizeResource(
    | \$this\s*->\s*authorizeForUser\s*\(                          # $this->authorizeForUser(
    | Policy\s*::\s*authorize\s*\(                                 # Policy::authorize(
    | can\s*\(\s*['"]                                              # can('...')
    | cannot\s*\(\s*['"]                                           # cannot('...')
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Dogfood #6 — helper-method indirection. Controllers commonly wrap
# `$this->authorize()` in a project-specific helper (`authorizeIfPolicyExists`,
# `requireAuthorization`, ...) defined on a base controller. The intra-method
# regex above can't see through that helper, so every CRUD method that goes
# through it gets falsely flagged. Two compounding fixes:
#
#   (1) Allowlist of well-known helper names that, when called as
#       `$this->X(` / `static::X(` / `self::X(`, are treated as proof of
#       authorization without any further analysis. Covers the common Laravel
#       conventions and the dogfood-cited `authorizeIfPolicyExists`.
#
#   (2) One-level intra-class descent (see `_method_has_authorize`): when a
#       method calls `$this->someHelper()` AND `someHelper` is defined on
#       this class OR one of its ancestors (using the existing
#       `class_source_map`), we re-check `someHelper`'s body for the literal
#       authorize regex / allowlist hit.
#
# Both layers stay conservative: if a project ships an unfamiliar helper that
# isn't in the allowlist and isn't defined in any indexed PHP file (e.g.
# trait-only methods, magic __call), we still flag the calling method. This
# preserves the "false negative > false positive" bias the rest of the
# detector follows.
#
# Project-specific helpers can be added to a future `.roam-config.yml`
# (extension point not yet wired). For now, edit `_AUTHORIZE_HELPER_NAMES`
# below or rely on layer (2)'s descent to discover them automatically.
_AUTHORIZE_HELPER_NAMES = frozenset(
    {
        # Literal Laravel-baseline helpers (also caught by _AUTHORIZATION_RE)
        "authorize",
        "authorizeResource",
        "authorizeForUser",
        # Dogfood #6 — observed in real Laravel codebases
        "authorizeIfPolicyExists",
        "requireAuthorization",
        "requireAuth",
        "requireAuthorized",
        "mustBeAllowed",
        "mustAuthorize",
        "checkPolicy",
        "checkAuthorization",
        "checkAccess",
        "ensureAuthorized",
        "ensureCan",
        "guardAgainst",
        "abortUnlessAuthorized",
    }
)

# Match `$this->name(` / `static::name(` / `self::name(` / `parent::name(`.
# Used both to detect helper-name matches and to enumerate intra-class call
# targets for the one-level descent.
_RE_SELF_CALL = re.compile(
    r"""(?:\$this\s*->|static\s*::|self\s*::|parent\s*::)\s*(\w+)\s*\(""",
)

# Prefix forms that almost always indicate an authorize helper, regardless of
# the exact name. Examples observed in production Laravel codebases:
#   $this->authorizeFoo(...)   -> "authorize"-prefixed
#   $this->gateFoo(...)        -> "gate"-prefixed
# Conservative: only matches when the prefix is followed by another
# capital letter / digit / underscore (so plain `authorize(` stays in the
# explicit regex, not here).
_RE_AUTHORIZE_PREFIX_HELPER = re.compile(
    r"""(?:\$this\s*->|static\s*::|self\s*::|parent\s*::)\s*(?:authorize|gate)[A-Z0-9_]\w*\s*\(""",
)


def _body_has_inline_authorization(body: str) -> bool:
    """Cheap, regex-only authorization check on a single method body.

    Mirrors what `_AUTHORIZATION_RE` did before dogfood #6 — kept as a
    standalone helper so the one-level-descent path (which has already
    spent the lookup cost) can reuse it on a helper's body without paying
    the full helper-resolution price a second time.
    """
    if _AUTHORIZATION_RE.search(body):
        return True
    if _RE_AUTHORIZE_PREFIX_HELPER.search(body):
        return True
    # Allowlist match — `$this->authorizeIfPolicyExists(`, etc.
    for m in _RE_SELF_CALL.finditer(body):
        if m.group(1) in _AUTHORIZE_HELPER_NAMES:
            return True
    return False


_HELPER_DESCENT_MAX_DEPTH = 2  # W36.10: bumped from 1 -> 2 to cover 2-deep
# wrapper chains (AdminController extends
# ResourceController extends BaseController where
# the authorize call lives 2 hops up). Do NOT
# bump beyond 2 — the dogfood corpus only
# justifies depth-1; depth-2 is a conservative
# extension. Depth-3+ risks FP via spurious
# `authorize` matches in unrelated ancestor
# methods of deep framework hierarchies.


def _method_has_authorize(
    body: str,
    own_class_methods: dict[str, str] | None = None,
    class_source_map: dict[str, str] | None = None,
    source: str | None = None,
    _depth: int = _HELPER_DESCENT_MAX_DEPTH,
) -> bool:
    """Decide whether *body* should count as authorized.

    Three layers, evaluated cheapest-first:

      1. Inline literal authorize calls / allowlist helpers / prefix-helpers
         (`_body_has_inline_authorization`).
      2. Intra-class descent: for each `$this->X(` in *body* that resolves to
         a method defined ON THIS CLASS, re-run the check on X's body. This
         catches the dogfood-cited `BaseResourceController::authorizeIfPolicyExists`
         pattern when the helper happens to live in the same file.
      3. Ancestor descent: same as (2) but the helper is resolved on a parent
         class via the existing `class_source_map` + `_RE_CLASS_EXTENDS`
         walker. Capped at the same 3-ancestor depth as the constructor-
         middleware walker — Laravel projects rarely go deeper.

    Depth is hard-capped at ``_HELPER_DESCENT_MAX_DEPTH`` descent layers
    (W36.10 — bumped from 1 to 2). The dogfood corpus reported depth-1
    covers ~95% of real-world cases; depth-2 lifts that to handle the
    common 2-deep wrapper chain (``AdminController -> ResourceController
    -> BaseController``). Deeper recursion buys little and risks cycles
    plus false positives from unrelated `authorize` calls in deep
    framework hierarchies.
    """
    if _body_has_inline_authorization(body):
        return True
    if own_class_methods is None and class_source_map is None:
        return False
    if _depth <= 0:
        return False

    # Collect `$this->X(` call targets in this method body.
    self_call_targets: list[str] = []
    for m in _RE_SELF_CALL.finditer(body):
        target = m.group(1)
        # Layer 1 already covered allowlist hits; skip them to avoid wasted
        # work but stay correct if a project overrides one of these names.
        if target in _AUTHORIZE_HELPER_NAMES:
            return True
        self_call_targets.append(target)

    if not self_call_targets:
        return False

    # Layer 2 — same-class helpers. Recurse so a 2-deep chain
    # (caller -> helperA -> helperB[auth]) resolves at _depth=2.
    if own_class_methods:
        for target in self_call_targets:
            helper_body = own_class_methods.get(target)
            if helper_body is None:
                continue
            if _method_has_authorize(
                helper_body,
                own_class_methods=own_class_methods,
                class_source_map=class_source_map,
                source=source,
                _depth=_depth - 1,
            ):
                return True

    # Layer 3 — ancestor helpers via class_source_map. Recurse so a 2-deep
    # ancestor wrapper chain (helper -> deeper helper[auth]) resolves.
    if class_source_map and source:
        ancestor_methods = _collect_ancestor_methods(source, class_source_map)
        for target in self_call_targets:
            helper_body = ancestor_methods.get(target)
            if helper_body is None:
                continue
            if _method_has_authorize(
                helper_body,
                own_class_methods=own_class_methods,
                class_source_map=class_source_map,
                source=source,
                _depth=_depth - 1,
            ):
                return True

    return False


def _collect_ancestor_methods(
    source: str,
    class_source_map: dict[str, str],
    depth: int = 3,
) -> dict[str, str]:
    """Return a {method_name: body} map gathered from *source*'s ancestors.

    Mirrors `_ancestor_has_constructor_auth`'s walking strategy but collects
    every public/protected method body it can find. Capped at the same
    3-ancestor depth so a long Laravel framework chain doesn't dominate.
    """
    methods: dict[str, str] = {}
    visited: set[str] = set()
    current_source: str | None = source
    while current_source is not None and depth > 0:
        extends_match = _RE_CLASS_EXTENDS.search(current_source)
        if not extends_match:
            break
        parent_fqn = extends_match.group(2)
        parent_short = parent_fqn.replace("\\", "/").rsplit("/", 1)[-1]
        if parent_short in visited:
            break
        visited.add(parent_short)
        parent_source = class_source_map.get(parent_short)
        if parent_source is None:
            break
        for m in _PROTECTED_OR_PUBLIC_METHOD_RE.finditer(parent_source):
            name = m.group(1)
            methods.setdefault(name, _extract_method_body_at(parent_source, m.start()))
        current_source = parent_source
        depth -= 1
    return methods


def _extract_method_body_at(source: str, start_offset: int) -> str:
    """Return the textual body of the method whose declaration begins at *start_offset*.

    Walks character-by-character until brace-depth returns to 0. Returns an
    empty string if the body never opens (interface methods, abstract
    methods, syntax errors).
    """
    text = source[start_offset:]
    brace_depth = 0
    found_open = False
    out: list[str] = []
    for ch in text:
        out.append(ch)
        if ch == "{":
            brace_depth += 1
            found_open = True
        elif ch == "}":
            brace_depth -= 1
            if found_open and brace_depth == 0:
                return "".join(out)
    return "".join(out)


# Matches `public function X(` / `protected function X(` / `private function X(`.
# Used to collect ancestor-class method bodies for one-level intra-class descent.
_PROTECTED_OR_PUBLIC_METHOD_RE = re.compile(
    r"""\b(?:public|protected|private)\s+(?:static\s+)?function\s+(\w+)\s*\(""",
)

# M11 — tenant-scoped query patterns. When a controller method scopes its
# resource to the current tenant / office / user (Laravel multi-tenant
# pattern), the route-level auth + tenant scope IS the authorization.
# Real-world FP from a Vue 3 + Laravel codebase: ~115 controller methods flagged because
# none called $this->authorize() — but every one used officeScoped() /
# multiTenant() / Resource::for(...) which implicitly enforce auth.
_TENANT_SCOPE_RE = re.compile(
    r"""
    ->\s*officeScoped\s*\(            # ->officeScoped()
    | ->\s*multiTenant\s*\(           # ->multiTenant()
    | ->\s*tenantScoped\s*\(          # ->tenantScoped()
    | ->\s*forTenant\s*\(             # ->forTenant(
    | ->\s*forUser\s*\(               # ->forUser(  (Laravel Spatie pattern)
    | ::\s*for\s*\(\s*\$               # Resource::for($user, ...) / Spatie pattern
    | ->\s*scopeOwnedBy\s*\(          # ->scopeOwnedBy(
    | ->\s*belongsToCurrentUser\s*\(  # custom Eloquent scope
    | ->\s*currentTeam\s*\(           # team-scoped
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

# Controller references in route files — used to cross-reference route-level auth
# with controller-level analysis.  Matches:
#   UserController::class       (group 1)
#   'UserController@method'     (group 2)
_RE_CONTROLLER_REF = re.compile(
    r"""(\w+Controller)\s*::class|['"](\w+Controller)@""",
)

# ServiceProvider patterns — routes registered in boot() with middleware
# e.g.:  Route::middleware(['auth:sanctum'])->group(function () { ... });
#        Route::middleware('auth:sanctum')->group(function () { ... });
# within a class that extends ServiceProvider
_RE_SERVICE_PROVIDER_CLASS = re.compile(
    r"class\s+(\w+)\s+extends\s+ServiceProvider",
)
_RE_PROVIDER_ROUTE_MIDDLEWARE = re.compile(
    r"""Route\s*::\s*middleware\s*\(\s*(?:\[?\s*['"]auth(?::sanctum)?['"]\s*\]?)""",
    re.IGNORECASE,
)


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


def _analyze_route_file(file_path: str, source: str) -> tuple[list[dict], set[str]]:
    """Parse a routes file and return routes outside an auth middleware group.

    Returns ``(findings, protected_controllers)`` where *protected_controllers*
    is a set of controller class names (e.g. ``'UserController'``) that appear
    inside an auth-middleware-protected group.  This set is used later to
    suppress false-positive controller findings.

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
    protected_controllers: set[str] = set()
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
            if re.search(r"\]\s*\)\s*->\s*group\s*\(", line) or re.search(r"->\s*group\s*\(", line):
                accumulated = "\n".join(middleware_accumulator)
                has_auth = bool(
                    re.search(
                        r"""['"]auth(?::sanctum)?['"]""",
                        accumulated,
                        re.IGNORECASE,
                    )
                )
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
        if re.search(r"Route\s*::\s*middleware\s*\(\s*\[", line, re.IGNORECASE) and not re.search(
            r"->\s*group\s*\(", line
        ):
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
        if (
            re.search(
                r"Route\s*::\s*(?:prefix|name|domain)\s*\([^)]*\)\s*->\s*group\s*\(",
                line,
                re.IGNORECASE,
            )
            or re.search(r"Route\s*::\s*group\s*\(", line, re.IGNORECASE)
        ) and opens > closes:
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
            # Collect controller names referenced inside protected groups
            # so we can suppress false-positive controller findings later.
            for cm in _RE_CONTROLLER_REF.finditer(line):
                name = cm.group(1) or cm.group(2)
                if name:
                    protected_controllers.add(name)
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

        # M10 — non-auth guards (throttle / signed / verified / can / scope).
        # When present, the route is intentionally public-but-protected.
        # Demote confidence + add evidence note rather than reporting as
        # a high-confidence "missing auth" finding.
        non_auth_guard = bool(_NON_AUTH_GUARD_RE.search(line))

        if non_auth_guard:
            findings.append(
                {
                    "type": "route",
                    "confidence": "low",
                    "verb": verb,
                    "path": path_str,
                    "file": file_path,
                    "line": lineno,
                    "fix": "Verify intent: this route has non-auth guards (throttle / signed / verified / can / scope) but no auth:* — looks like an intentional public-but-protected endpoint",
                    "non_auth_guard_present": True,
                }
            )
        else:
            findings.append(
                {
                    "type": "route",
                    "confidence": "high",
                    "verb": verb,
                    "path": path_str,
                    "file": file_path,
                    "line": lineno,
                    "fix": "Add ->middleware('auth:sanctum') or move inside auth group",
                }
            )

    return findings, protected_controllers


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

            methods.append(
                {
                    "name": method_name,
                    "start_line": start_line,
                    "body": "\n".join(body_lines),
                }
            )
            i = j + 1
        else:
            i += 1

    return methods


def _collect_all_methods(source: str) -> dict[str, str]:
    """Return ``{method_name: body}`` for every method in *source*.

    Unlike ``_extract_method_bodies`` (which only looks at lines that *start*
    with ``public function``), this captures public/protected/private and
    static variants — needed for dogfood #6's one-level intra-class
    descent, since the wrapped authorize-helper is typically ``protected``.
    """
    methods: dict[str, str] = {}
    for m in _PROTECTED_OR_PUBLIC_METHOD_RE.finditer(source):
        name = m.group(1)
        # Skip duplicates so the FIRST definition wins (PHP doesn't allow
        # overload anyway, and trait-merging is out of scope).
        if name in methods:
            continue
        methods[name] = _extract_method_body_at(source, m.start())
    return methods


# E2 — class-source map: short-class-name -> source. Built once per
# `auth-gaps` invocation by walking every controller file in the project
# so the inheritance walker has O(1) lookup instead of re-reading files.


def _build_class_source_map(file_paths: list[str], project_root) -> dict[str, str]:
    """Index ``class X`` declarations across PHP files for inheritance lookup."""
    out: dict[str, str] = {}
    for db_path in file_paths:
        abs_path = _resolve_path(project_root, db_path)
        source = _read_source(abs_path)
        if source is None:
            continue
        # A file may declare multiple classes; index each. Strip leading
        # backslash for fully-qualified-name forms (`\App\Foo`).
        for match in re.finditer(r"\bclass\s+(\w+)\s*[\{\s(]", source):
            cls = match.group(1)
            out.setdefault(cls, source)
    return out


def _ancestor_has_constructor_auth(
    source: str,
    class_source_map: dict[str, str],
    depth: int = 3,
) -> bool:
    """E2 — walk the `extends` chain looking for `$this->middleware('auth')`.

    Caps at ``depth`` ancestors to avoid pathological cycles or framework
    base classes (Illuminate\\Routing\\Controller, etc.) that aren't in the
    project source map. Returns True at the first ancestor that wires auth.
    """
    if not class_source_map or depth <= 0:
        return False
    extends_match = _RE_CLASS_EXTENDS.search(source)
    if not extends_match:
        return False
    parent_fqn = extends_match.group(2)
    # Strip leading backslash + namespace prefix; we keyed the map by short name.
    parent_short = parent_fqn.replace("\\", "/").rsplit("/", 1)[-1]
    parent_source = class_source_map.get(parent_short)
    if parent_source is None:
        return False
    if _CONSTRUCTOR_MIDDLEWARE_RE.search(parent_source):
        return True
    # Recurse — handle 2+ level inheritance (BaseController extends ApiController).
    return _ancestor_has_constructor_auth(parent_source, class_source_map, depth - 1)


def _analyze_controller_file(
    file_path: str,
    source: str,
    route_protected_controllers: set[str] | None = None,
    class_source_map: dict[str, str] | None = None,
) -> list[dict]:
    """Analyze a PHP controller for missing authorization checks.

    When *route_protected_controllers* is provided, controllers whose class
    name appears in that set are considered auth-middleware-protected at the
    route level.  This suppresses ``high`` → ``medium`` for CRUD methods
    (authentication already handled) and skips read-method findings entirely.

    Confidence levels:
    - ``high``:   CRUD method with no route-level auth, no constructor
                  middleware, and no authorization call in method body
    - ``medium``: CRUD method without explicit Gate/Policy/authorize check
                  (route or constructor middleware provides authentication)
    - ``low``:    Read method without authorization (may be intentionally public)
    """
    base = os.path.basename(file_path)

    # Skip auth-related controllers
    if _SKIP_CONTROLLER_PATTERNS.search(base):
        return []

    class_name = _controller_class_name(base)
    is_route_protected = route_protected_controllers is not None and class_name in route_protected_controllers

    findings = []

    # Check for class-level auth middleware in constructor.
    # also walk the extends chain so a controller that
    # inherits from DynamicResourceController / ApiController / etc. picks
    # up the parent's `$this->middleware('auth')` registration.
    has_constructor_auth = bool(_CONSTRUCTOR_MIDDLEWARE_RE.search(source))
    if not has_constructor_auth and class_source_map:
        has_constructor_auth = _ancestor_has_constructor_auth(source, class_source_map)

    methods = _extract_method_bodies(source)
    # Dogfood #6 — pre-build a {name: body} map of ALL methods (public,
    # protected, private) on this class so the one-level descent can find
    # same-class helpers like `protected function authorizeIfPolicyExists`.
    own_class_methods = _collect_all_methods(source)
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

        # Check for explicit authorization call in the method body. Dogfood
        # #6: extend beyond a single regex by also (a) recognising a small
        # allowlist of common helper names, (b) descending one level into
        # `$this->X(` helpers defined on this class or its ancestors.
        has_auth_check = _method_has_authorize(
            body,
            own_class_methods=own_class_methods,
            class_source_map=class_source_map,
            source=source,
        )

        # M11 — tenant-scoped query patterns count as authorization-equivalent.
        # When a method is route-auth-protected AND scopes its query to the
        # current tenant/user (officeScoped / multiTenant / Resource::for(...)),
        # the route auth + tenant scope is the authorization layer. Demote
        # what would have been a high/medium "missing $this->authorize()"
        # finding to a "review" rather than dropping silently.
        has_tenant_scope = bool(_TENANT_SCOPE_RE.search(body))

        if has_auth_check:
            continue

        if is_crud:
            if not has_constructor_auth and not is_route_protected:
                confidence = "high"
                reason = "CRUD method with no auth middleware and no authorization call"
                fix = "Add $this->authorize() or Gate::allows() inside the method"
            else:
                confidence = "medium"
                if is_route_protected and not has_constructor_auth:
                    reason = "CRUD method without object-level authorization (route auth exists)"
                else:
                    reason = "CRUD method without explicit authorization (has constructor middleware)"
                fix = "Add $this->authorize('action', $model) for object-level authorization"
                # M11 — tenant-scoped CRUD = route auth + tenant scope = layered
                # authorization. Downgrade and explain.
                if has_tenant_scope:
                    confidence = "low"
                    reason = (
                        "CRUD method has route auth + tenant-scope (officeScoped / multiTenant / "
                        "Resource::for) — likely already authorized at the right layer; "
                        "verify policy intent for object-level guards"
                    )
        else:
            # Read method behind route or constructor auth — skip
            if is_route_protected or has_constructor_auth:
                continue
            # M11 — even without route auth, tenant-scope is a meaningful guard
            # for read methods. Drop confidence to "low" with a note.
            if has_tenant_scope:
                continue
            confidence = "low"
            reason = "Read method without authorization (may be intentionally public)"
            fix = "Add $this->authorize('view', $model) if access should be restricted"

        # `matched_patterns` lists the structural signals
        # that contributed to this finding's confidence. Mirrors the math
        # detector's evidence shape so consumers can render WHY uniformly.
        matched_patterns: list[str] = []
        matched_patterns.append("CRUD action" if is_crud else "read action")
        if has_constructor_auth:
            matched_patterns.append("auth middleware in constructor (or ancestor)")
        if is_route_protected:
            matched_patterns.append("auth middleware at route level")
        if has_tenant_scope:
            matched_patterns.append("tenant-scoped query (officeScoped / multiTenant / Resource::for)")
        if not has_auth_check:
            matched_patterns.append("no $this->authorize() / Gate / Policy call in method body")
        findings.append(
            {
                "type": "controller",
                "confidence": confidence,
                "controller": class_name,
                "method": name,
                "file": file_path,
                "line": line,
                "reason": reason,
                "fix": fix,
                "matched_patterns": matched_patterns,
            }
        )

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


def _find_service_provider_files(conn) -> list[str]:
    """Return file paths for ServiceProvider files from the index."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE (path LIKE '%Provider%' OR path LIKE '%provider%') "
        "  AND (path LIKE '%.php' OR language = 'php') "
        "ORDER BY path"
    ).fetchall()
    return [r["path"] for r in rows]


def _analyze_service_provider(file_path: str, source: str) -> set[str]:
    """Scan a ServiceProvider file for controllers registered inside auth middleware groups.

    Returns a set of controller class names (e.g. ``'OrderController'``) that are
    referenced inside ``Route::middleware(['auth:sanctum'])->group(...)`` blocks
    within a ServiceProvider ``boot()`` method.
    """
    protected_controllers: set[str] = set()

    # Quick check: file must extend ServiceProvider and contain Route::middleware auth
    if not _RE_SERVICE_PROVIDER_CLASS.search(source):
        return protected_controllers
    if not _RE_PROVIDER_ROUTE_MIDDLEWARE.search(source):
        return protected_controllers

    # Track brace depth to detect controllers inside auth middleware groups.
    # Strategy: when we see Route::middleware('auth...')->group(, mark that depth
    # as auth-protected and collect controller refs until the group closes.
    lines = source.splitlines()
    brace_depth = 0
    auth_depth_stack: list[tuple[int, bool]] = []  # (depth_at_open, is_auth)

    for line in lines:
        opens, closes = _count_braces(line)

        # Check for auth middleware group opener
        if _RE_PROVIDER_ROUTE_MIDDLEWARE.search(line) and re.search(r"->\s*group\s*\(", line):
            # Process closes first
            for _ in range(closes):
                brace_depth -= 1
                if brace_depth < 0:
                    brace_depth = 0
                if auth_depth_stack and auth_depth_stack[-1][0] == brace_depth:
                    auth_depth_stack.pop()
            auth_depth_stack.append((brace_depth, True))
            brace_depth += opens
            continue

        # Process closing braces
        for _ in range(closes):
            brace_depth -= 1
            if brace_depth < 0:
                brace_depth = 0
            if auth_depth_stack and auth_depth_stack[-1][0] == brace_depth:
                auth_depth_stack.pop()

        # Check if currently inside auth group
        currently_protected = any(auth for _, auth in auth_depth_stack)

        brace_depth += opens

        if currently_protected:
            for cm in _RE_CONTROLLER_REF.finditer(line):
                name = cm.group(1) or cm.group(2)
                if name:
                    protected_controllers.add(name)

    return protected_controllers


# ---------------------------------------------------------------------------
# Findings-registry emit (W116 — fourth detector migrating onto the
# central A4 findings registry, after clones / dead / complexity).
# ---------------------------------------------------------------------------


def _auth_gap_finding_kind(f: dict) -> str:
    """Classify an auth-gap into one of three semantic ``kind`` buckets.

    The detector emits two output ``type`` values (``route`` /
    ``controller``); the kind below carries the additional information
    the confidence-tier mapping needs:

      - ``direct-unauthenticated-handler``: a route that sits outside
        every auth middleware group AND has no inline auth middleware.
        Deterministic from the route file's brace-depth analysis →
        ``static_analysis`` tier.
      - ``helper-indirection``: a controller method without a literal
        ``$this->authorize`` call, where same-class or ancestor-class
        helper descent was attempted but did NOT clear the gap. The
        detector ran a graph traversal (class_source_map +
        ``_collect_ancestor_methods``) to land here → ``structural``.
      - ``name-based``: weaker signals — route low-confidence findings
        gated on ``_NON_AUTH_GUARD_RE`` (throttle / signed / verified
        naming) and controller read methods (action name heuristic) /
        tenant-scope demotions. Pattern-on-name only → ``heuristic``.
    """
    ftype = f.get("type")
    confidence = f.get("confidence")
    if ftype == "route":
        # Routes inside the middleware brace tree are excluded earlier;
        # findings that survive are by construction "no auth middleware
        # on this route". The low-confidence variant is the
        # non_auth_guard_present pattern — a naming-based heuristic.
        if f.get("non_auth_guard_present"):
            return "name-based"
        return "direct-unauthenticated-handler"
    # type == "controller"
    if confidence == "high":
        # CRUD method, no constructor middleware (including ancestor
        # walk), no route-level protection, no inline / descent-resolved
        # authorize. Helper descent already ran and returned False.
        return "helper-indirection"
    if confidence == "medium":
        # Route or constructor middleware exists; missing object-level
        # authorization. Still a graph-walked decision (the ancestor
        # walker resolved the constructor auth), but the gap itself is
        # at the object level rather than the handler level.
        return "helper-indirection"
    # confidence == "low" — read methods or tenant-scope demotions.
    return "name-based"


def _auth_gap_confidence_tier(kind: str) -> str:
    """Map an auth-gap ``kind`` to a registry confidence tier.

    See the dataclass-level docstring on ``FindingRecord`` for the four
    accepted tiers. The mapping below is deliberately conservative —
    name-based signals stay at the heuristic floor so an agent
    consuming ``roam findings list`` doesn't over-trust them.
    """
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_STRUCTURAL,
    )

    if kind == "direct-unauthenticated-handler":
        return CONFIDENCE_STATIC_ANALYSIS
    if kind == "helper-indirection":
        return CONFIDENCE_STRUCTURAL
    # name-based / unknown — pattern-on-name only.
    return CONFIDENCE_HEURISTIC


def _auth_gap_finding_id(file_path: str, kind: str, subject: str, line: int) -> str:
    """Stable, deterministic finding id for one auth-gap.

    The (kind, file, subject, line) tuple re-identifies the same gap
    across runs — kind keeps the namespace clean when a single file
    grows both a route-level and a controller-level finding, and the
    subject (route path for routes, ``Controller::method`` for
    controllers) folds in the route verb / method name. Re-running
    ``roam auth-gaps --persist`` upserts via the SHA-1 prefix.
    """
    raw = f"{kind}:{file_path}:{subject}:{line}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"auth-gaps:{kind}:{digest}"


def _to_db_relative(file_path: str, project_root) -> str:
    """Best-effort conversion of an absolute path back to the DB-relative
    form stored in ``files.path``. Mirrors how ``_resolve_path`` builds
    the absolute path so the inverse is a simple relpath against the
    project root. Falls back to ``file_path`` unchanged on failure.
    """
    try:
        rel = os.path.relpath(file_path, str(project_root))
        # Normalise Windows back-slashes — the indexer stores forward
        # slashes regardless of platform.
        return rel.replace(os.sep, "/")
    except ValueError:
        return file_path


def _resolve_controller_symbol_id(
    conn: sqlite3.Connection,
    file_path: str,
    method_name: str,
    line: int,
    project_root,
) -> int | None:
    """Look up ``symbols.id`` for a (file, method, line) triple.

    Used by the findings-registry emit path so controller findings can
    JOIN back to ``symbols``. Returns ``None`` when the symbol can't be
    resolved (anonymous method, mismatched indexer state, pre-W89
    schema). Mirrors the resolution strategy in
    :func:`roam.graph.clone_detect._resolve_symbol_id`.
    """
    db_path = _to_db_relative(file_path, project_root)
    try:
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? AND s.line_start = ? "
            "LIMIT 1",
            (db_path, method_name, line),
        ).fetchone()
        if row is not None:
            return int(row[0])
        # Indexer's reported line_start can drift a few rows past the
        # `public function X(` declaration our regex anchors on. Fall
        # back to name-only on the same file, picking the closest line.
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? "
            "ORDER BY ABS(COALESCE(s.line_start, 0) - ?) "
            "LIMIT 1",
            (db_path, method_name, line),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        # Pre-W89 schema or symbols table absent — caller's defensive
        # try/except will keep the standard auth-gaps output flowing.
        return None


def _emit_auth_gaps_findings(
    conn: sqlite3.Connection,
    findings: list[dict],
    source_version: str,
    project_root=None,
) -> None:
    """Mirror each auth-gap into the central findings registry.

    ``findings`` is the combined ``route_findings + controller_findings``
    list the detector builds for the JSON envelope (same shape — the
    dict keys are the contract). The detector-specific text/JSON output
    is unchanged; the registry rows are the denormalised cross-detector
    surface that ``roam findings list --detector auth-gaps`` reads.

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard read path.
    """
    # Local import to keep the cost out of the read-only fast path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    for f in findings:
        ftype = f.get("type") or ""
        file_path = f.get("file") or ""
        line = int(f.get("line") or 0)
        kind = _auth_gap_finding_kind(f)
        tier = _auth_gap_confidence_tier(kind)

        if ftype == "route":
            verb = f.get("verb") or ""
            path = f.get("path") or ""
            subject = f"{verb} {path}".strip()
            subject_id: int | None = None
            claim = (
                f"Auth gap: route {verb} {path} ({file_path}:{line}) — kind={kind}, confidence={f.get('confidence')}"
            )
            evidence = {
                "type": "route",
                "kind": kind,
                "verb": verb,
                "path": path,
                "file": file_path,
                "line": line,
                "confidence": f.get("confidence"),
                "fix": f.get("fix"),
                "non_auth_guard_present": bool(f.get("non_auth_guard_present", False)),
            }
        else:
            # type == "controller"
            controller = f.get("controller") or ""
            method = f.get("method") or ""
            subject = f"{controller}::{method}"
            if project_root is not None and method and file_path:
                subject_id = _resolve_controller_symbol_id(conn, file_path, method, line, project_root)
            else:
                subject_id = None
            claim = (
                f"Auth gap: controller {controller}::{method} "
                f"({file_path}:{line}) — kind={kind}, "
                f"confidence={f.get('confidence')}"
            )
            evidence = {
                "type": "controller",
                "kind": kind,
                "controller": controller,
                "method": method,
                "file": file_path,
                "line": line,
                "confidence": f.get("confidence"),
                "reason": f.get("reason"),
                "fix": f.get("fix"),
                "matched_patterns": list(f.get("matched_patterns") or []),
            }

        finding_id_str = _auth_gap_finding_id(file_path, kind, subject, line)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id_str,
                subject_kind="symbol" if subject_id is not None else "endpoint",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=tier,
                source_detector="auth-gaps",
                source_version=source_version,
            ),
        )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="auth-gaps",
    category="reports",
    summary="Find endpoints missing authentication or authorization checks",
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
@click.command("auth-gaps")
@click.option("--limit", "-n", default=50, show_default=True, help="Max findings to display")
@click.option(
    "--routes-only",
    "routes_only",
    is_flag=True,
    help="Only check route files, skip controller analysis",
)
@click.option(
    "--controllers-only",
    "controllers_only",
    is_flag=True,
    help="Only check controller files, skip route analysis",
)
@click.option(
    "--min-confidence",
    "min_confidence",
    # W1005-followup-D: widened from 3-tier {high, medium, low} to the W547
    # canonical 7-tier so agents can pass any of {critical, error, high,
    # warning, medium, low, info} and have the floor compared via
    # ``severity_rank()`` from ``roam.output._severity``. The detector emits
    # only {high, medium, low} (the CVSS 3-tier) but the Choice accepts the
    # full canonical vocabulary so canonical-aware agents can pass any tier.
    # A user-passed ``--min-confidence critical`` keeps no findings because
    # nothing ranks above ``high`` (rank 4 via severity_rank).
    type=click.Choice(
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    default="low",
    show_default=True,
    help=(
        "Minimum confidence floor. Uses the canonical W547 7-tier ordering "
        "(critical > error == high > warning > medium > low > info). Detector "
        "emits high/medium/low today; canonical aliases rank via the same "
        "severity_rank() comparator."
    ),
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each auth-gap into the central findings registry "
        "(``roam findings list --detector auth-gaps``). Detector-specific "
        "output is unchanged; the registry rows are the denormalised "
        "cross-detector surface."
    ),
)
@click.pass_context
def auth_gaps_cmd(ctx, limit, routes_only, controllers_only, min_confidence, persist):
    """Find endpoints missing authentication or authorization checks.

    Analyses PHP controller files and route definitions to detect:

    \b
    1. Routes outside an auth middleware group (routes/api.php, routes/web.php)
    2. Controller CRUD methods without $this->authorize() / Gate::allows() checks
    3. Controller read methods without any authorization call (low confidence)

    Unlike ``coverage-gaps`` (which checks call-graph reachability from entry
    points to gate symbols), this command performs PHP/Laravel-specific source
    analysis — parsing route group nesting, constructor middleware, and
    ServiceProvider registrations to find unprotected endpoints.

    Confidence levels:
      high   - Route outside any auth group AND no controller-level auth
      medium - CRUD method missing explicit object-level authorization
      low    - Read method without authorization (may be intentionally public)
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    ensure_index()

    # W1005-followup-D: canonical severity_rank() floor — higher = worse.
    # Pre-W1005-followup-D the floor went through ``confidence_level_rank``
    # (W596); post-fix it goes through ``severity_rank`` so the same canonical
    # comparator answers ``--min-confidence`` and ``--min-severity`` floors
    # across every command (Pattern 3a cross-command parameter vocabulary).
    # Detector emits {high, medium, low} (CVSS 3-tier) while Choice accepts
    # the full canonical 7-tier — see Choice docstring above for the asymmetry.
    min_conf_rank = severity_rank(min_confidence)

    project_root = find_project_root()
    all_findings: list[dict] = []

    # W607-CM -- substrate-boundary plumbing for cmd_auth_gaps.
    # ``_run_check_cm`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cm_warnings_out`` rather than
    # crashing the auth-gaps detector outright (W116 foundational
    # detector; W815 sealed the empty-corpus Pattern-2 guard with the
    # explicit zero-count verdict but did NOT install substrate
    # isolation -- this wave adds it). Marker family
    # ``auth_gaps_<phase>_failed:<exc_class>:<detail>``. Substrates
    # wrapped:
    #
    #   * find_route_files            -- route file discovery
    #   * find_service_provider_files -- provider file discovery
    #   * find_controller_files       -- controller file discovery
    #   * analyze_route_file          -- per-route-file analysis (W815 empty guard)
    #   * analyze_service_provider    -- per-provider-file analysis
    #   * build_class_source_map      -- E2 inheritance-lookup map build
    #   * analyze_controller_file     -- per-controller-file analysis
    #                                    (W140 helper indirection + W36.10
    #                                    depth-2 ancestor descent live here)
    #   * apply_confidence_filter     -- W1005-followup-D severity floor
    #   * aggregate_by_confidence     -- histogram
    #   * emit_findings               -- W116 findings-registry mirror
    #                                    (sqlite3.OperationalError silent
    #                                    no-op preserved for pre-W89 DB)
    #   * serialize_to_sarif          -- W1195 SARIF projection
    _w607cm_warnings_out: list[str] = []

    def _run_check_cm(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CM marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``auth_gaps_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607cm_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cm_warnings_out.append(f"auth_gaps_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-ED -- ADDITIVE aggregation-phase plumbing for cmd_auth_gaps.
    # Layered on top of the W607-CM substrate-CALL layer. The two
    # buckets are merged at envelope-emit time so consumers see the
    # full degradation lineage. The phase names (score_classify /
    # compute_predicate / compute_verdict / serialize_envelope) are
    # disjoint from the W607-CM substrate phases above (route_files /
    # analyze_route_file / ...). Marker family
    # ``auth_gaps_<phase>_failed:<exc_class>:<detail>`` is shared.
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_ed(...)`` call:
    #   1. f-string verdict floor: NEVER re-interpolate the same values
    #      that tripped the closure inside the ``default=`` floor.
    #   2. kwarg-default eagerness: ``default=`` must be a literal
    #      constant, never a computed expression.
    #   3. json.dumps(default=str) sentinel: the serialize_envelope
    #      floor must be JSON-serializable with the standard encoder.
    #   4. phase-name collision: verified above against CM's phases.
    #   5. len() at kwarg-bind: move len() INSIDE the closure, never at
    #      the ``_run_check_ed(...)`` call site.
    #   6. unguarded len()/if on poisoned object: the floor MUST be a
    #      concrete dict/str/None, never a sentinel that may
    #      __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    _w607ed_warnings_out: list[str] = []

    def _run_check_ed(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-ED marker emission.

        Mirror of ``_run_check_cm`` shape (same
        ``auth_gaps_<phase>_failed:`` marker family) but writes into
        ``_w607ed_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits. W607-DW finding pin: the
        return statement is verbatim ``return default`` (NOT
        ``return default if default is not None else {}``) so the floor
        is a literal pass-through.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ed_warnings_out.append(f"auth_gaps_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # --- Route file analysis ---
        # Also collects controller names inside auth-protected groups so we
        # can suppress false-positive controller findings later.
        route_protected_controllers: set[str] = set()
        if not controllers_only:
            # W607-CM: ``find_route_files`` substrate -- raise in the
            # path-LIKE SQL query degrades to [] so the empty-state
            # envelope still composes.
            route_files = _run_check_cm(
                "find_route_files",
                _find_route_files,
                conn,
                default=[],
            )
            if route_files is None:
                route_files = []
            for db_path in route_files:
                abs_path = _resolve_path(project_root, db_path)
                source = _read_source(abs_path)
                if source is None:
                    continue
                # W607-CM: ``analyze_route_file`` substrate -- per-file
                # brace-depth analyser. A raise on ONE route file must
                # not torpedo other route files (per-framework isolation),
                # so this is wrapped inside the per-file loop. Default
                # is ``([], set())`` so the protected-controllers set
                # stays correct even when one file fails.
                pair = _run_check_cm(
                    "analyze_route_file",
                    _analyze_route_file,
                    abs_path,
                    source,
                    default=([], set()),
                )
                if pair is None:
                    pair = ([], set())
                findings, protected = pair
                all_findings.extend(findings)
                route_protected_controllers.update(protected)

        # --- ServiceProvider analysis ---
        # Routes can also be registered in ServiceProvider::boot() methods
        # with Route::middleware(['auth:sanctum'])->group(...).  Controllers
        # referenced inside those groups should be treated as protected.
        if not controllers_only:
            # W607-CM: ``find_service_provider_files`` substrate -- raise
            # in the path-LIKE SQL query degrades to [] so the empty-state
            # envelope still composes.
            provider_files = _run_check_cm(
                "find_service_provider_files",
                _find_service_provider_files,
                conn,
                default=[],
            )
            if provider_files is None:
                provider_files = []
            for db_path in provider_files:
                abs_path = _resolve_path(project_root, db_path)
                source = _read_source(abs_path)
                if source is None:
                    continue
                # W607-CM: ``analyze_service_provider`` substrate -- one
                # provider raising must not torpedo siblings. Default
                # is the empty set so route_protected_controllers stays
                # well-formed.
                provider_protected = _run_check_cm(
                    "analyze_service_provider",
                    _analyze_service_provider,
                    abs_path,
                    source,
                    default=set(),
                )
                if provider_protected is None:
                    provider_protected = set()
                route_protected_controllers.update(provider_protected)

        # --- Controller file analysis ---
        if not routes_only:
            # W607-CM: ``find_controller_files`` substrate -- raise in
            # the path-LIKE SQL query degrades to [] so the empty-state
            # envelope still composes.
            controller_files = _run_check_cm(
                "find_controller_files",
                _find_controller_files,
                conn,
                default=[],
            )
            if controller_files is None:
                controller_files = []
            # E2 — build the project-wide class-source map once so the
            # inheritance walker can resolve `extends` parents in O(1).
            # W607-CM: ``build_class_source_map`` substrate -- raise in
            # the cross-file walker degrades to {} so per-controller
            # analysis still runs (W36.10 ancestor descent silently
            # bypasses with no ancestor source).
            class_source_map = _run_check_cm(
                "build_class_source_map",
                _build_class_source_map,
                controller_files,
                project_root,
                default={},
            )
            if class_source_map is None:
                class_source_map = {}
            for db_path in controller_files:
                abs_path = _resolve_path(project_root, db_path)
                source = _read_source(abs_path)
                if source is None:
                    continue
                # W607-CM: ``analyze_controller_file`` substrate -- W140
                # helper-indirection + W36.10 depth-2 ancestor descent
                # live here. Per-file isolation so one controller raising
                # does not torpedo siblings (matches the per-framework
                # isolation discipline).
                findings = _run_check_cm(
                    "analyze_controller_file",
                    _analyze_controller_file,
                    abs_path,
                    source,
                    route_protected_controllers,
                    class_source_map=class_source_map,
                    default=[],
                )
                if findings is None:
                    findings = []
                all_findings.extend(findings)

        # --- W116: mirror auth-gaps into the central findings registry.
        # Runs ONLY with --persist. The persisted set is the unfiltered
        # union of route + controller findings — the registry mirrors
        # the full detector output regardless of the --min-confidence
        # display filter, so consumers reading
        # ``roam findings list --detector auth-gaps`` see every gap
        # the detector actually found.
        # W607-CM: ``emit_findings`` substrate boundary. The pre-W89
        # schema path (sqlite3.OperationalError on missing ``findings``
        # table) is the EXPECTED degraded path -- the try/except below
        # maintains the W116 silent no-op contract for that case.
        # Generic exceptions surface via the
        # ``auth_gaps_emit_findings_failed:<exc>:<detail>`` marker.
        if persist:
            try:
                _emit_auth_gaps_findings(
                    conn,
                    all_findings,
                    source_version=AUTH_GAPS_DETECTOR_VERSION,
                    project_root=project_root,
                )
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully so the standard auth-gaps output
                # still ships. Surface lineage so a non-expected variant
                # (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_auth_gaps:emit_findings", _exc)
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-CM disclosure
                _w607cm_warnings_out.append(f"auth_gaps_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

    # Apply confidence floor via canonical severity_rank() (W1005-followup-D).
    # Floor semantic: keep finding when ``severity_rank(f.confidence) >= floor``.
    # Unknown labels collapse to rank -1 and sort below every defined tier — so
    # ``--min-confidence info`` (rank 0) drops them, matching the W531
    # CI-safety lesson (a typo never accidentally promotes a finding).
    # W607-CM: ``apply_confidence_filter`` substrate -- raise in the
    # comprehension (e.g. severity_rank raising on a malformed finding
    # dict) degrades to the unfiltered list so a single bad row doesn't
    # wipe the findings list.
    def _apply_confidence_filter():
        return [f for f in all_findings if severity_rank(f["confidence"]) >= min_conf_rank]

    _filtered = _run_check_cm(
        "apply_confidence_filter",
        _apply_confidence_filter,
        default=all_findings,
    )
    if _filtered is not None:
        all_findings = _filtered

    # Sort: high first, then medium, then low; within tier by file.
    # W607-CM: ``sort_findings`` substrate -- raise in the sort key
    # (e.g. malformed finding dict missing ``confidence`` / ``file``)
    # degrades to the unsorted list so the envelope still composes.
    def _sort_findings():
        all_findings.sort(
            key=lambda f: (
                -confidence_level_rank(f["confidence"], fallback=-1),
                f["file"],
                f.get("line", 0),
            )
        )
        return all_findings

    _run_check_cm("sort_findings", _sort_findings, default=None)

    # Counts by confidence
    # W607-CM: ``aggregate_by_confidence`` substrate -- histogram
    # construction. Degrades to (0, 0, 0) on raise so the verdict
    # composer still produces a coherent string.
    def _aggregate_by_confidence():
        return (
            sum(1 for f in all_findings if f["confidence"] == "high"),
            sum(1 for f in all_findings if f["confidence"] == "medium"),
            sum(1 for f in all_findings if f["confidence"] == "low"),
        )

    counts = _run_check_cm(
        "aggregate_by_confidence",
        _aggregate_by_confidence,
        default=(0, 0, 0),
    )
    if counts is None:
        counts = (0, 0, 0)
    n_high, n_medium, n_low = counts
    total = len(all_findings)

    # Split by type for display
    route_findings = [f for f in all_findings if f["type"] == "route"]
    ctrl_findings = [f for f in all_findings if f["type"] == "controller"]

    # --- SARIF output ---
    # W1195: SARIF projection mirrors the three confidence tiers used
    # by the findings-registry emit path
    # (``_auth_gap_confidence_tier``). Three closed-enum rule ids —
    # ``auth-gaps/direct-unauthenticated-handler`` (error),
    # ``auth-gaps/helper-indirection`` (warning), and
    # ``auth-gaps/name-based`` (note) — so a CI gate keyed off
    # ``level: error`` only blocks on deterministic findings, not
    # heuristic name-matches.
    if sarif_mode:
        # W607-CM: SARIF projection substrate -- a raise in the
        # SARIF writer used to crash the auth-gaps command on the CI
        # integration path; now degrades silently to None with a
        # marker, and the function returns early (matches pre-W607-CM
        # semantics that SARIF mode short-circuits).
        def _emit_sarif():
            from roam.output.sarif import auth_gaps_to_sarif, write_sarif

            sarif = auth_gaps_to_sarif(all_findings)
            click.echo(write_sarif(sarif))

        _run_check_cm("serialize_to_sarif", _emit_sarif, default=None)
        return

    # --- JSON output ---
    if json_mode:
        verdict_floor = f"{total} auth gap(s) found"
        # W21.7 LAW 4: suppress zero-severity rows from the auto-derived
        # ``agent_contract.facts`` so we don't ship noise like
        # ``"0 high findings"`` / ``"0 medium findings"`` /
        # ``"0 low findings"``. Build the contract explicitly: verdict +
        # only the non-zero severity buckets. When everything is zero the
        # facts list contains only the verdict ("0 auth gap(s) found").
        explicit_facts = [verdict_floor]
        for sev, n in (("high", n_high), ("medium", n_medium), ("low", n_low)):
            if n > 0:
                explicit_facts.append(f"{n} {sev}-severity auth gaps")

        # W607-ED -- score_classify boundary. Buckets the run by total
        # auth-gap count into a state label:
        #   * NO_AUTH_GAPS  -- total == 0
        #   * LIGHT         -- 0 < total <= 3
        #   * MODERATE      -- 3 < total <= 10
        #   * HEAVY         -- total > 10
        #   * DEGRADED      -- floor on raise
        # W978 5th-discipline: ``total`` passed as a raw int; counting /
        # iteration lives INSIDE the closure (no len() at kwarg-bind).
        def _score_classify_run(_total):
            if _total == 0:
                _state_label = "NO_AUTH_GAPS"
            elif _total <= 3:
                _state_label = "LIGHT"
            elif _total <= 10:
                _state_label = "MODERATE"
            else:
                _state_label = "HEAVY"
            return {"state": _state_label, "scanned": _total}

        _score_dict = _run_check_ed(
            "score_classify",
            _score_classify_run,
            total,
            default={"state": "DEGRADED", "scanned": 0},
        )

        # W607-ED -- compute_predicate boundary. Rollup metrics dict
        # surfacing aggregate dimensions (total_count / by_kind /
        # files_affected / endpoints_affected) so a downstream refactor
        # of the rollup logic surfaces a marker rather than crashing.
        # W978 5th-discipline: ``all_findings`` passed as a raw arg;
        # counting / iteration lives INSIDE the closure.
        def _compute_predicate_fields(_findings):
            _by_kind: dict[str, int] = {}
            _files: set[str] = set()
            _endpoints: set[str] = set()
            for _f in _findings:
                # Auth-gap findings carry ``type`` (route|controller) and
                # ``reason`` (e.g. helper_indirection / ancestor_descent /
                # missing_authorize). Compose a stable key per finding:
                # ``<type>:<reason>`` so by_kind is a meaningful 2D rollup.
                _t = _f.get("type") or "auth_gap"
                _r = _f.get("reason") or _t
                _k = f"{_t}:{_r}"
                _by_kind[_k] = _by_kind.get(_k, 0) + 1
                _file = _f.get("file") or ""
                if _file:
                    _files.add(_file)
                # Endpoint identity: route uses verb+path, controller
                # uses class+method
                if _t == "route":
                    _ep = f"{_f.get('verb', '?')} {_f.get('path', '?')}"
                else:
                    _ep = f"{_f.get('controller', '?')}::{_f.get('name', '?')}"
                _endpoints.add(_ep)
            return {
                "total_count": len(_findings),
                "by_kind": dict(_by_kind),
                "files_affected": len(_files),
                "endpoints_affected": len(_endpoints),
            }

        _pred_fields = _run_check_ed(
            "compute_predicate",
            _compute_predicate_fields,
            all_findings,
            default={
                "total_count": 0,
                "by_kind": {},
                "files_affected": 0,
                "endpoints_affected": 0,
            },
        )

        # W607-ED -- compute_verdict boundary. Wraps the verdict string
        # assembly so a downstream f-string refactor surfaces a marker
        # rather than crashing the envelope. Literal "auth_gaps
        # completed" floor (LAW 6 still holds: the line works
        # standalone).
        #
        # W978 1st-discipline: the floor MUST NOT re-interpolate the
        # same values that tripped the closure. W978 2nd-discipline:
        # ``default=`` is a literal constant.
        def _build_verdict_str(_verdict_floor):
            return _verdict_floor

        verdict = _run_check_ed(
            "compute_verdict",
            _build_verdict_str,
            verdict_floor,
            default="auth_gaps completed",
        )

        # W607-CM + W607-ED: any substrate marker OR aggregation marker
        # flips ``partial_success: True`` so a degraded envelope is NOT
        # mistaken for a clean "0 auth gaps" verdict (Pattern-2
        # silent-fallback guard). W815 already validates that the
        # empty-corpus path explicitly names the zero-count outcome --
        # the W607-CM/ED flip extends that guard to the degraded paths.
        _combined_warnings = list(_w607cm_warnings_out) + list(_w607ed_warnings_out)
        summary_block: dict = {
            "verdict": verdict,
            "total": total,
            "high": n_high,
            "medium": n_medium,
            "low": n_low,
            "route_gaps": len(route_findings),
            "controller_gaps": len(ctrl_findings),
            # W607-ED: surface score_classify result so consumers can
            # read the run state without re-deriving from raw counts.
            # W978 7th-discipline: bare ``_score_dict["state"]`` (floor
            # dict guarantees the key) -- NOT ``.get(..., expensive)``.
            "run_state": _score_dict["state"],
            # W607-ED: surface compute_predicate rollup on the envelope
            # so consumers can read the aggregate dimensions without
            # rebuilding from the raw list. W978 7th-discipline: bare
            # key lookups.
            "by_kind": _pred_fields["by_kind"],
            "files_affected": _pred_fields["files_affected"],
            "endpoints_affected": _pred_fields["endpoints_affected"],
        }
        envelope_kwargs: dict = {
            "summary": summary_block,
            "agent_contract": {"facts": explicit_facts},
            "route_gaps": route_findings[:limit],
            "controller_gaps": ctrl_findings[:limit],
        }
        # W607-CM + W607-ED: mirror combined substrate + aggregation
        # markers into BOTH the top-level envelope ``warnings_out``
        # AND ``summary.warnings_out`` so MCP consumers see disclosure
        # regardless of which surface they read.
        if _combined_warnings:
            summary_block["partial_success"] = True
            summary_block["warnings_out"] = list(_combined_warnings)
            envelope_kwargs["warnings_out"] = list(_combined_warnings)

        # W607-ED -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("auth-gaps", ...)`` would otherwise
        # crash AFTER all substrate + aggregation signals were already
        # gathered. Floor to a minimal envelope stub so consumers still
        # receive a parseable JSON object with the marker attached + the
        # canonical command name. W978 6th-discipline: floor is a
        # concrete dict, not a sentinel that may __len__-raise downstream.
        _envelope_floor: dict = {
            "command": "auth-gaps",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": "auth_gaps completed",
                "partial_success": True,
                "warnings_out": list(_combined_warnings),
            },
            "warnings_out": list(_combined_warnings),
        }
        envelope = _run_check_ed(
            "serialize_envelope",
            json_envelope,
            "auth-gaps",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-ED -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``auth_gaps_serialize_envelope_failed:`` marker was appended to
        # ``_w607ed_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output. Clean path ->
        # envelope is the real json_envelope return value, no rebuild.
        if envelope is _envelope_floor and _w607ed_warnings_out:
            _combined_warnings = list(_w607cm_warnings_out) + list(_w607ed_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
            _envelope_floor["warnings_out"] = list(_combined_warnings)
            envelope = _envelope_floor
        click.echo(to_json(envelope))
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
            rows.append(
                [
                    f"[{f['confidence']}]",
                    f"{f['verb']}  {f['path']}",
                    loc(f["file"], f["line"]),
                    f["fix"],
                ]
            )
        click.echo(
            format_table(
                ["Conf", "Route", "Location", "Fix"],
                rows,
                budget=limit,
            )
        )
        click.echo()
    else:
        click.echo("Routes without auth middleware: (none found)\n")

    # --- Controllers section ---
    if ctrl_findings:
        click.echo(f"Controllers without authorization ({len(ctrl_findings)}):")
        # when many findings cluster on a few controllers
        # (e.g. ~115 methods of DynamicResourceController-derived classes),
        # a per-controller rollup makes triage radically faster than skimming
        # 100+ detail rows. Threshold: only show rollup when >= 10 findings.
        if len(ctrl_findings) >= 10:
            from collections import Counter

            by_ctrl = Counter(f.get("controller", "?") for f in ctrl_findings)
            top = by_ctrl.most_common(5)
            click.echo("  Top by controller:")
            for ctrl, n in top:
                click.echo(f"    {ctrl:<40s} {n} method(s)")
            tail = len(by_ctrl) - 5
            if tail > 0:
                click.echo(f"    ...and {tail} more controller(s)")
            click.echo()
        rows = []
        for f in ctrl_findings[:limit]:
            rows.append(
                [
                    f"[{f['confidence']}]",
                    f"{f['controller']}::{f['method']}",
                    loc(f["file"], f["line"]),
                    f["fix"],
                ]
            )
        click.echo(
            format_table(
                ["Conf", "Symbol", "Location", "Fix"],
                rows,
                budget=limit,
            )
        )
        click.echo()
    else:
        click.echo("Controllers without authorization: (none found)\n")

    if total == 0:
        click.echo("No auth gaps detected.")
    elif n_high == 0:
        click.echo("No high-confidence gaps found. Review medium/low findings manually.")
