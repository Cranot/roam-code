"""Detect API endpoints that return more data than necessary.

Finds models with many fields that are fully serialized when only a few
fields are needed — the "over-fetch" anti-pattern that bloats API responses
with unused columns, wastes bandwidth, and leaks internal schema details.

Detection checks:

1. **Large models without $hidden** — Models with many $fillable fields
   (>threshold) that don't define ``$hidden`` to suppress serialized output.
   Every API response includes ALL fillable fields unless hidden or visible is set.

2. **Models without API Resources** — Models returned directly from controllers
   via ``response()->json($model)`` instead of through a Laravel API Resource
   that controls which fields are exposed.

3. **$fillable count vs $hidden count ratio** — If a model has 50 fillable
   fields and only hides 2, the API response is still 48 fields.

4. **Missing select() in queries** — Controllers/services that do
   ``Model::all()`` or ``Model::paginate()`` without ``->select(['f1', 'f2'])``
   to limit returned columns, especially on large models.

5. **3-state endpoint classification** — Per controller-method classification
   into BARE / GUARDED_RELATION / UNGUARDED_RELATION based on the eager-load
   shape. Surfaced in the JSON envelope under ``endpoint_findings`` and
   counted in ``summary.bare_count`` / ``guarded_relation_count`` /
   ``unguarded_relation_count``. Disambiguates the common case where a
   model is flagged but most of its endpoints already have partial
   ``with('rel:cols')`` guards in place.

Confidence levels (model-level):

- ``high``   — 30+ fillable fields, no $hidden, no $visible, direct controller return
- ``medium`` — 20+ fillable fields, minimal $hidden (< 3 fields hidden)
- ``low``    — 15+ fillable fields, no select() optimization in queries

Endpoint states (3-state, endpoint-level):

- ``BARE``                — Model::query()->paginate() without ->select() or Resource (H severity)
- ``UNGUARDED_RELATION``  — with('rel') without column selection (H severity)
- ``GUARDED_RELATION``    — with('rel:col1,col2,...') — partially constrained (L severity, advisory)

Supported frameworks: Laravel/Eloquent (PHP).
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to extract entries from PHP array literals like:
#   'field1', 'field2', "field3", ...
_ARRAY_STRING_RE = re.compile(r"""['"]([^'"]+)['"]""")

# Patterns that indicate a model field is being directly returned to the client
# without going through an API Resource.
_DIRECT_RETURN_PATTERNS = [
    # return $model;  /  return $record;  /  return $this->model;
    re.compile(r"\breturn\s+\$\w+\s*;"),
    # response()->json($model)  /  response()->json($record)
    re.compile(r"response\s*\(\s*\)\s*->\s*json\s*\(\s*\$\w+"),
    # ->json($model)
    re.compile(r"->\s*json\s*\(\s*\$\w+"),
]

# Patterns that indicate an API Resource wrapping (these are safe)
_RESOURCE_PATTERNS = [
    # new SomeResource($model)  /  SomeResource::collection(...)
    re.compile(r"\bnew\s+\w+Resource\s*\("),
    re.compile(r"\w+Resource\s*::\s*collection\s*\("),
    # JsonResource, AnonymousResourceCollection
    re.compile(r"\bJsonResource\b"),
]

# body-level signals that the method shapes its output
# before returning. When ANY of these appears anywhere in the method body,
# treat the method as field-shape-protected: don't flag direct returns of
# the model object itself, because the bytes that hit the wire have been
# filtered. Patterns cover Laravel API Resources, DTO objects, Eloquent
# field filters (`makeHidden` / `makeVisible` / `only` / `except`), the
# paginate->through() transform, and project-specific shapers like
# DynamicResourceController's `inheritModelFields()`.
_BODY_SHAPING_PATTERNS = [
    re.compile(r"\binheritModelFields\s*\("),
    re.compile(r"->\s*through\s*\(\s*(?:fn|function)"),
    re.compile(r"->\s*makeHidden\s*\("),
    re.compile(r"->\s*makeVisible\s*\("),
    re.compile(r"->\s*only\s*\(\s*\["),
    re.compile(r"->\s*except\s*\(\s*\["),
    re.compile(r"\bnew\s+\w*(?:Dto|DTO|Resource|Collection)\s*\("),
    # Match `EmployeeDto::fromModel(`, `OrderDTO::createFrom(`, etc. — the
    # method-name suffix is allowed to extend (`fromModel`, `fromArray`).
    re.compile(r"\b\w*(?:Dto|DTO)\s*::\s*(?:from|create|make|build)\w*\s*\("),
    re.compile(r"->\s*toArray\s*\(\s*\)"),
    re.compile(r"\$this\s*->\s*(?:shape|transform|format|present|serialize)\s*\("),
    # Method that just delegates to a parent (`return parent::index();`) —
    # trust the parent to shape, since we can't easily reach it here.
    re.compile(r"\breturn\s+parent\s*::\s*\w+\s*\("),
]

# select() call detection — indicates developer is intentionally limiting columns
_SELECT_CALL_RE = re.compile(r"->\s*select\s*\(")

# Unoptimized query patterns (no select)
_UNOPTIMIZED_QUERY_RE = re.compile(
    r"(?:Model::all\(\)|Model::paginate\(|"
    r"::\s*all\s*\(\s*\)|::\s*paginate\s*\(|"
    r"::\s*get\s*\(\s*\))",
)

# ---------------------------------------------------------------------------
# 3-state with(...) classification (BARE / GUARDED_RELATION / UNGUARDED_RELATION)
# ---------------------------------------------------------------------------
#
# These overlays operate at the endpoint (controller method) level — distinct
# from the model-level findings above. The dogfood lesson: 4 of 5 flagged
# "Employee leak" endpoints actually had `with('relation:cols')` partial
# guards already in place. Only 1 was a true full-relation dump. Existing
# model-level scoring (huge $fillable + no Resource) doesn't see this nuance.

# Each individual eager-load argument inside `with(...)`. We extract every
# 'relation' or 'relation:cols' string literal independently so a single
# with('a:cols', 'b') call is correctly classified as mixed (1 guarded + 1
# unguarded). The colon → column-selection convention is documented in
# Laravel's Eloquent docs as the "Eager Loading Specific Columns" feature.
_WITH_ARG_RE = re.compile(r"['\"](?P<rel>[A-Za-z_][\w.]*)(?P<colon>:)?(?P<cols>[\w,]*)['\"]")

# Top-level `with(` call — captures the args group only; we then scan inside.
_WITH_CALL_RE = re.compile(r"\bwith\s*\(\s*(?P<args>[^)]*)\)")

# Query-loading entry points that pull rows into memory (and hence into the
# JSON response if returned directly). These are the call sites we classify.
_LOAD_CALL_RE = re.compile(
    r"->\s*(?:paginate|simplePaginate|cursorPaginate|get|all|find|first|firstOrFail|findOrFail)\s*\("
)

# Detect `Model::query()` chains and `Model::paginate(` etc that originate
# in a class-static call. We need the model name for endpoint naming.
_MODEL_STATIC_RE = re.compile(r"\b(?P<model>[A-Z][A-Za-z0-9_]+)\s*::\s*(?:query|paginate|all|where|with|find|first|select)\s*\(")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    base = os.path.basename(p)
    if base.startswith("test_") or base.endswith("_test.php"):
        return True
    if "/tests/" in p or "/test/" in p or "/testing/" in p:
        return True
    return False


def _read_file_lines(abs_path) -> list[str]:
    """Read file lines safely, returning empty list on failure."""
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _extract_array_fields(source: str, property_name: str) -> list[str]:
    """Extract string values from a PHP class property array.

    Handles both ``$fillable = ['a', 'b']`` and ``$fillable = ["a", "b"]``
    across multiple lines.

    Returns list of field name strings found, or empty list if property absent.
    """
    # Match:  $fillable = [ ... ]  or  protected $fillable = [ ... ];
    # Uses a non-greedy match that stops at the closing bracket.
    pattern = re.compile(
        rf"\$\s*{re.escape(property_name)}\s*=\s*\[([^\]]*)\]",
        re.DOTALL,
    )
    m = pattern.search(source)
    if not m:
        return []
    return _ARRAY_STRING_RE.findall(m.group(1))


def _has_visible_property(source: str) -> bool:
    """Return True if the model defines $visible (whitelist — fully controlled)."""
    return bool(re.search(r"\$\s*visible\s*=\s*\[", source))


def _find_api_resource_for_model(conn, model_name: str) -> str | None:
    """Find an API Resource file matching this model name.

    Looks for files named <ModelName>Resource.php in the codebase.
    Returns the file path string, or None if not found.
    """
    resource_name = f"{model_name}Resource.php"
    rows = conn.execute(
        "SELECT path FROM files WHERE path LIKE ?",
        (f"%{resource_name}",),
    ).fetchall()
    for row in rows:
        p = row["path"].replace("\\", "/")
        if "Resource" in p or "resource" in p:
            return row["path"]
    # Also check for collection resources
    collection_name = f"{model_name}Collection.php"
    rows2 = conn.execute(
        "SELECT path FROM files WHERE path LIKE ?",
        (f"%{collection_name}",),
    ).fetchall()
    if rows2:
        return rows2[0]["path"]
    return None


def _count_resource_fields(root, resource_path: str) -> int | None:
    """Count fields exposed in a Laravel API Resource's ``toArray()`` method.

    Parses the ``return [...]`` array inside ``toArray()`` and counts
    ``'key' =>`` patterns.  Returns the field count, or ``None`` if the
    method cannot be parsed (e.g. dynamic or delegated responses).
    """
    abs_path = root / resource_path
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    m = re.search(r"function\s+toArray\s*\(", source)
    if not m:
        return None

    rest = source[m.end() :]
    return_match = re.search(r"return\s*\[", rest)
    if not return_match:
        return None

    bracket_pos = rest.index("[", return_match.start())
    depth = 0
    pos = bracket_pos
    while pos < len(rest):
        if rest[pos] == "[":
            depth += 1
        elif rest[pos] == "]":
            depth -= 1
            if depth == 0:
                break
        pos += 1

    array_body = rest[bracket_pos : pos + 1]

    # Each 'key' => represents one exposed field in the response
    return len(re.findall(r"""['\"][^'\"]+['\"]\s*=>""", array_body))


def _check_controller_direct_returns(
    conn,
    model_name: str,
    root,
) -> list[dict]:
    """Check controller files for direct model returns (without API Resources).

    scope the check to method bodies that actually use the
    model. Real-world FP from a Vue 3 + Laravel codebase: `MyDataController` was flagged for
    over-fetching `LedgerAccount` because `LedgerAccount` was imported at
    the top of the file — but the controller actually returns AADE service
    DTOs (`return $aadeService->getDocs()`). Without method-body scoping
    every direct-return pattern in the file got attributed to every
    imported model.

    The fix: per controller method body, only flag direct returns when the
    body literally references the model name in a way that suggests it's
    being returned (Model::, new Model(, or Model used in select()/with()).
    Just having the import at the top isn't enough.
    """
    direct_returns = []

    controller_files = conn.execute(
        "SELECT path FROM files WHERE (path LIKE '%Controller%' OR path LIKE '%controller%') AND path LIKE '%.php'",
    ).fetchall()

    # M12 — match the model when used in a way that suggests it'll flow to the response.
    # Skip pure imports / type hints alone; they don't tell us what's returned.
    model_use_re = re.compile(
        rf"\b{re.escape(model_name)}\s*::"  # Model::find / Model::all / Model::query / Model::factory
        rf"|\bnew\s+{re.escape(model_name)}\s*\("  # new Model(
        rf"|->\s*{model_name.lower()}\s*(?:\(|->)"  # ->model() / ->modelRel
    )

    for row in controller_files:
        if _is_test_path(row["path"]):
            continue
        # Skip console commands and non-HTTP controllers
        p_lower = row["path"].replace("\\", "/").lower()
        if "/console/" in p_lower or "/commands/" in p_lower:
            continue
        # Only match files in HTTP controller directories
        if (
            "controller" in os.path.basename(row["path"]).lower()
            and "/http/" not in p_lower
            and "/controllers/" not in p_lower
        ):
            continue
        abs_path = root / row["path"]
        if not abs_path.is_file():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Only check controllers that reference this model
        if model_name not in content:
            continue

        # M12 — scope to method bodies that USE the model (not just import it).
        method_bodies = _extract_method_bodies_with_lines(content)
        for method in method_bodies:
            body = method["body"]
            # Skip method bodies that don't actually use the model
            if not model_use_re.search(body):
                continue
            # E5 — if the body shapes its output (Resource wrap, DTO,
            # makeHidden, inheritModelFields, parent:: delegation, etc.),
            # the bytes on the wire are already filtered; don't flag.
            if any(p.search(body) for p in _BODY_SHAPING_PATTERNS):
                continue
            body_lines = body.splitlines()
            for offset, line in enumerate(body_lines):
                line_no = method["start_line"] + offset
                # Skip if the line uses a Resource (safe pattern)
                if any(p.search(line) for p in _RESOURCE_PATTERNS):
                    continue
                # Flag direct returns
                for pattern in _DIRECT_RETURN_PATTERNS:
                    if pattern.search(line):
                        direct_returns.append(
                            {
                                "file": row["path"],
                                "line": line_no,
                                "location": loc(row["path"], line_no),
                                "snippet": line.strip()[:100],
                                "method": method["name"],
                            }
                        )
                        break  # one match per line is enough

    return direct_returns


# M12 helper — extract method bodies with line numbers for scoped scans.
def _extract_method_bodies_with_lines(source: str) -> list[dict]:
    """Pull out PHP class method bodies with start_line + name.

    Returns list of {name, start_line, body}. Mirrors the more elaborate
    helper used by auth-gaps; intentionally local so the over-fetch fix
    doesn't depend on a cross-module import.
    """
    out: list[dict] = []
    method_re = re.compile(r"(?:public|protected|private)\s+function\s+(\w+)\s*\(")
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        m = method_re.search(lines[i])
        if not m:
            i += 1
            continue
        method_name = m.group(1)
        start_line = i + 1  # 1-based
        # Walk to the closing brace
        brace_depth = 0
        body_lines: list[str] = []
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
        out.append({"name": method_name, "start_line": start_line, "body": "\n".join(body_lines)})
        i = j + 1
    return out


def _check_missing_select(
    conn,
    model_name: str,
    root,
) -> list[dict]:
    """Check controller/service files for queries without select().

    Returns list of locations where Model::all() or paginate() is used
    without a preceding ->select() call to limit columns.
    """
    missing_select = []

    # Look in controllers and services
    files = conn.execute(
        "SELECT path FROM files "
        "WHERE path LIKE '%.php' "
        "AND (path LIKE '%Controller%' OR path LIKE '%controller%' "
        "  OR path LIKE '%Service%' OR path LIKE '%service%' "
        "  OR path LIKE '%Repository%' OR path LIKE '%repository%')",
    ).fetchall()

    # Pattern to match Model::query(), Model::all(), Model::paginate() etc.
    # We build model-specific patterns
    model_query_re = re.compile(
        rf"{re.escape(model_name)}\s*::\s*(?:all|paginate|get|query|where|with)\s*\(",
    )

    for row in files:
        if _is_test_path(row["path"]):
            continue
        abs_path = root / row["path"]
        if not abs_path.is_file():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if model_name not in content:
            continue

        lines = content.splitlines()
        for line_no, line in enumerate(lines, start=1):
            if not model_query_re.search(line):
                continue
            # Check if this line or the surrounding ±5 lines contain ->select(
            context_start = max(0, line_no - 6)
            context_end = min(len(lines), line_no + 5)
            context_block = "\n".join(lines[context_start:context_end])
            if _SELECT_CALL_RE.search(context_block):
                continue  # select() is nearby — OK
            missing_select.append(
                {
                    "file": row["path"],
                    "line": line_no,
                    "location": loc(row["path"], line_no),
                    "snippet": line.strip()[:100],
                }
            )

    return missing_select


# ---------------------------------------------------------------------------
# Endpoint-level 3-state classification
# ---------------------------------------------------------------------------


def _classify_with_args(args_text: str) -> tuple[list[dict], list[dict]]:
    """Parse a `with(...)` arg list and split into guarded / unguarded entries.

    Returns (guarded, unguarded) where each entry is
    {"relation": str, "cols": list[str], "raw": str}.

    Examples:
        with('user:id,name')         -> guarded=[{rel:user,cols:[id,name]}], unguarded=[]
        with('user')                 -> guarded=[], unguarded=[{rel:user,cols:[]}]
        with('a:id', 'b')            -> guarded=[{rel:a}], unguarded=[{rel:b}]
        with(['user' => fn (...)])   -> {} (closure callbacks ignored — could be guarded inside)
    """
    guarded: list[dict] = []
    unguarded: list[dict] = []
    for m in _WITH_ARG_RE.finditer(args_text):
        rel = m.group("rel")
        # Skip anything that looks like a query method passed as the relation arg
        # (e.g. 'orderBy', 'limit') — relations are typically lowercase nouns
        # with optional dotted paths. We accept any identifier-shaped string;
        # false positives here just produce extra advisory entries.
        cols = m.group("cols") or ""
        if m.group("colon") and cols:
            guarded.append(
                {
                    "relation": rel,
                    "cols": [c.strip() for c in cols.split(",") if c.strip()],
                    "raw": f"{rel}:{cols}",
                }
            )
        else:
            unguarded.append({"relation": rel, "cols": [], "raw": rel})
    return guarded, unguarded


def _endpoint_state_for_body(
    body: str,
) -> tuple[str | None, dict]:
    """Determine the 3-state classification for a single method body.

    Returns (state, details) where state is one of:
      - "BARE"                — query loads data without column select, no with(:cols)
      - "GUARDED_RELATION"    — body has at least one with('rel:cols') call
      - "UNGUARDED_RELATION"  — body has with('rel') without column selection
      - None                  — no loading query, or response is shaped (Resource/DTO)

    Precedence when both guarded and unguarded relations co-exist in the same
    body: report as UNGUARDED_RELATION (the unguarded relation is the leak,
    even if a sibling relation is partially guarded). The guarded sibling is
    surfaced in `details.guarded` so reviewers see the existing partial fix.
    """
    # Bail early if the body doesn't load anything we'd serialize back to
    # the wire — no point classifying internal logic / setters.
    if not _LOAD_CALL_RE.search(body) and not _MODEL_STATIC_RE.search(body):
        return None, {}

    # If the body shapes its output through a Resource/DTO/makeHidden/etc,
    # the leak is filtered before the wire. Don't flag.
    # _BODY_SHAPING_PATTERNS catches `new XResource(`, DTOs, makeHidden, etc.
    # _RESOURCE_PATTERNS catches `XResource::collection(` and bare JsonResource
    # references, which is what most Laravel apps use for list endpoints.
    if any(p.search(body) for p in _BODY_SHAPING_PATTERNS):
        return None, {}
    if any(p.search(body) for p in _RESOURCE_PATTERNS):
        return None, {}

    # Walk all `with(...)` calls and accumulate guarded + unguarded relations.
    all_guarded: list[dict] = []
    all_unguarded: list[dict] = []
    for wm in _WITH_CALL_RE.finditer(body):
        g, u = _classify_with_args(wm.group("args"))
        all_guarded.extend(g)
        all_unguarded.extend(u)

    has_select = bool(_SELECT_CALL_RE.search(body))

    # Detect the BARE model surface: a load (paginate/get/all/etc) that does
    # NOT chain ->select() and does NOT have a guarded with(:cols) covering
    # the main model. The presence of `with('rel:cols')` only guards the
    # eager-loaded relation, NOT the primary model. So a body with
    # `Model::query()->with('rel:cols')->paginate()` is still BARE for Model.
    bare_main_model = bool(_LOAD_CALL_RE.search(body)) and not has_select

    details = {
        "guarded": all_guarded,
        "unguarded": all_unguarded,
        "has_select": has_select,
        "bare_main_model": bare_main_model,
    }

    # Precedence: BARE > UNGUARDED_RELATION > GUARDED_RELATION
    # Why this order: the user dogfood's exact pain point — agents need to
    # know the worst surviving leak in this endpoint. A BARE main model is
    # the strongest leak (all columns). UNGUARDED_RELATION dumps a full
    # relation. GUARDED_RELATION means partial fix already applied → low.
    #
    # BUT: if there are ZERO with(...) calls at all, we don't claim BARE
    # purely on the load — the existing model-level scoring already covers
    # plain `Model::paginate()` via direct-return analysis. The new states
    # exist to disambiguate eager-load patterns. So:
    #   - load + no with()        → return None (model-level rules handle it)
    #   - load + with(:cols) only → GUARDED_RELATION
    #   - load + with() only      → UNGUARDED_RELATION
    #   - load + mixed            → UNGUARDED_RELATION (worst wins)
    if not all_guarded and not all_unguarded:
        # Pure BARE Model::paginate() — surface here too so the endpoint-level
        # view is complete. Severity remains H per the spec.
        if bare_main_model:
            return "BARE", details
        return None, {}

    if all_unguarded:
        return "UNGUARDED_RELATION", details
    if all_guarded:
        return "GUARDED_RELATION", details
    return None, {}


_SEVERITY_BY_STATE = {
    "BARE": "H",
    "UNGUARDED_RELATION": "H",
    "GUARDED_RELATION": "L",
}


def _recommendation_for_state(state: str, details: dict) -> str:
    if state == "BARE":
        return (
            "Bare model load — add ->select(['col1','col2',...]) or wrap "
            "in an API Resource to control output columns."
        )
    if state == "UNGUARDED_RELATION":
        rels = ", ".join(u["relation"] for u in details.get("unguarded", []) or [])
        return (
            f"Add column selection: with('{rels.split(',')[0].strip()}:id,name,...') "
            "or wrap the loaded relation in a Resource. Currently dumps all "
            "relation columns."
        )
    if state == "GUARDED_RELATION":
        return (
            "Already partially guarded; consider full API Resource wrapper "
            "for stronger contract."
        )
    return ""


def _evidence_for_state(state: str, details: dict) -> str:
    if state == "GUARDED_RELATION":
        first = (details.get("guarded") or [{}])[0]
        if first:
            return f"with('{first['raw']}')"
    if state == "UNGUARDED_RELATION":
        first = (details.get("unguarded") or [{}])[0]
        if first:
            return f"with('{first['raw']}')"
    if state == "BARE":
        return "paginate()/get()/all() without ->select() or Resource"
    return ""


def analyze_endpoint_states(conn, root) -> list[dict]:
    """Scan all controller methods and classify each into BARE / GUARDED / UNGUARDED.

    Returns a list of endpoint findings, each:
        {
          endpoint: "ControllerName@method",
          file, line, state, severity, evidence, recommendation,
          details: {guarded:[...], unguarded:[...], has_select, bare_main_model}
        }

    The returned list is sorted with H severity first.
    """
    findings: list[dict] = []

    controller_files = conn.execute(
        "SELECT path FROM files WHERE (path LIKE '%Controller%' OR path LIKE '%controller%') "
        "AND path LIKE '%.php'",
    ).fetchall()

    for row in controller_files:
        path = row["path"]
        if _is_test_path(path):
            continue
        p_lower = path.replace("\\", "/").lower()
        if "/console/" in p_lower or "/commands/" in p_lower:
            continue
        if (
            "controller" in os.path.basename(path).lower()
            and "/http/" not in p_lower
            and "/controllers/" not in p_lower
        ):
            continue

        abs_path = root / path
        if not abs_path.is_file():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Derive controller class name from filename (e.g. AdvanceController.php → AdvanceController)
        controller_name = os.path.basename(path).rsplit(".", 1)[0]

        for method in _extract_method_bodies_with_lines(content):
            body = method["body"]
            state, details = _endpoint_state_for_body(body)
            if state is None:
                continue
            severity = _SEVERITY_BY_STATE.get(state, "M")
            findings.append(
                {
                    "endpoint": f"{controller_name}@{method['name']}",
                    "controller": controller_name,
                    "method": method["name"],
                    "file": path,
                    "line": method["start_line"],
                    "location": loc(path, method["start_line"]),
                    "state": state,
                    "severity": severity,
                    "evidence": _evidence_for_state(state, details),
                    "recommendation": _recommendation_for_state(state, details),
                    "details": details,
                }
            )

    # H first, then L; within a severity bucket preserve file/line stability.
    _sev_order = {"H": 0, "M": 1, "L": 2}
    findings.sort(key=lambda f: (_sev_order.get(f["severity"], 9), f["file"], f["line"]))
    return findings


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def _find_model_files(conn) -> list[dict]:
    """Find PHP model files by path and presence of $fillable."""
    rows = conn.execute(
        "SELECT f.id as file_id, f.path, s.id as class_id, "
        "s.name as class_name, s.line_start, s.line_end "
        "FROM files f "
        "JOIN symbols s ON s.file_id = f.id "
        "WHERE s.kind = 'class' "
        "AND f.path LIKE '%.php' "
        "AND ("
        "  f.path LIKE '%/Models/%' "
        "  OR f.path LIKE '%\\\\Models\\\\%' "
        "  OR f.path LIKE '%/Model/%' "
        ") "
        "ORDER BY f.path",
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_over_fetch(conn, threshold: int, limit: int) -> list[dict]:
    """Run the over-fetch detection analysis.

    Returns list of finding dicts sorted by severity (high → medium → low).
    """
    root = find_project_root()
    findings = []

    model_files = _find_model_files(conn)

    for model_info in model_files:
        if _is_test_path(model_info["path"]):
            continue

        abs_path = root / model_info["path"]
        if not abs_path.is_file():
            continue

        source = ""
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Extract $fillable fields
        fillable = _extract_array_fields(source, "fillable")
        fillable_count = len(fillable)

        # Skip models with fewer fields than the minimum threshold
        if fillable_count < threshold:
            continue

        # Extract $hidden fields
        hidden = _extract_array_fields(source, "hidden")
        hidden_count = len(hidden)

        # Check for $visible (whitelist — strong protection)
        has_visible = _has_visible_property(source)

        # Check for a matching API Resource
        resource_path = _find_api_resource_for_model(conn, model_info["class_name"])
        has_resource = resource_path is not None

        # If a Resource exists, check how many fields it actually exposes.
        # When the Resource returns significantly fewer fields than the model
        # has, the output is well-filtered and the finding can be skipped.
        resource_field_count = None
        if has_resource and resource_path:
            resource_field_count = _count_resource_fields(root, resource_path)
            if resource_field_count is not None and resource_field_count < fillable_count * 0.5:
                continue  # Resource properly filters output

        # Effective exposed fields = fillable minus hidden (when no $visible)
        exposed_count = fillable_count - hidden_count if not has_visible else 0

        # -------------------------------------------------------------------
        # Determine confidence level
        # -------------------------------------------------------------------
        confidence = None
        reasons = []
        suggestions = []

        if has_visible:
            # $visible whitelist is the strictest protection — skip
            continue

        if fillable_count >= 30 and hidden_count == 0 and not has_resource:
            confidence = "high"
            reasons.append(f"Serializes {fillable_count} fields per item in list APIs")
            if not has_resource:
                reasons.append("No API Resource found to control output")
            # when the model is *huge* (>=50 fields) and
            # has no shaping at all, lead with a concrete artefact suggestion
            # because the right fix is unambiguous: scaffold a Resource.
            if fillable_count >= 50:
                resource_class = f"{model_info['class_name']}Resource"
                suggestions.append(
                    f"BIG MODEL ({fillable_count} fields) — create app/Http/Resources/{resource_class}.php\n"
                    f"               with `public function toArray() {{ return [...]; }}` listing only\n"
                    f"               the fields the API needs. Then return\n"
                    f"               `{resource_class}::collection({model_info['class_name']}::query()->paginate())`\n"
                    f"               from the controller. ~80% bandwidth savings on list endpoints."
                )
            else:
                suggestions.append(
                    f"Add $hidden for unused fields, or use ->select() in queries,\n"
                    f"               or create a {model_info['class_name']}Resource "
                    f"to control output\n"
                    f"               Note: $hidden/$visible also hides fields from edit endpoints.\n"
                    f"               For CRUD apps, prefer API Resources for response shaping."
                )
        elif fillable_count >= 20 and hidden_count < 3:
            confidence = "medium"
            reasons.append(
                f"{fillable_count} fillable fields, only {hidden_count} hidden — {exposed_count} fields exposed"
            )
            if not has_resource:
                suggestions.append(
                    f"Consider $hidden or a {model_info['class_name']}Resource "
                    f"for large list responses\n"
                    f"               Note: $hidden/$visible also hides fields from edit endpoints.\n"
                    f"               For CRUD apps, prefer API Resources for response shaping."
                )
            else:
                suggestions.append(f"Verify {resource_path} limits fields for list vs detail views")
        elif fillable_count >= threshold:
            confidence = "low"
            reasons.append(f"{fillable_count} fillable fields without select() optimization")
            suggestions.append("Use ->select(['field1', 'field2']) in list queries to limit columns")

        if confidence is None:
            continue

        # -------------------------------------------------------------------
        # Controller analysis: direct returns + missing select()
        # -------------------------------------------------------------------
        direct_returns = []
        missing_selects = []

        # Only do file I/O for medium+ threshold findings to stay fast
        if confidence in ("high", "medium"):
            direct_returns = _check_controller_direct_returns(conn, model_info["class_name"], root)
            # Upgrade to high if direct returns found and we were medium
            if direct_returns and confidence == "medium":
                confidence = "high"
                reasons.append(f"Model returned directly from controller ({len(direct_returns)} location(s))")

        if confidence == "low":
            missing_selects = _check_missing_select(conn, model_info["class_name"], root)
            if not missing_selects:
                # No bad query patterns — downgrade / skip low-confidence
                continue

        # `matched_patterns` mirrors what _io_emit_finding
        # does for math: a structured list of the signals that fired so
        # downstream surfaces (pr-comment-render, dashboards) can render
        # WHY without parsing prose `reasons`. Stays additive — the
        # `reasons` field keeps its human-readable shape.
        matched_patterns = []
        if exposed_count > 0:
            matched_patterns.append(f"exposed_fields={exposed_count}")
        if not has_resource:
            matched_patterns.append("no API Resource wrapper")
        if direct_returns:
            matched_patterns.append(f"direct model return ({len(direct_returns)} site(s))")
        if missing_selects:
            matched_patterns.append(f"unselected query ({len(missing_selects)} site(s))")
        if has_visible:
            matched_patterns.append("$visible whitelist (signal: bounded)")
        findings.append(
            {
                "model_name": model_info["class_name"],
                "model_path": model_info["path"],
                "model_location": loc(model_info["path"], model_info["line_start"]),
                "fillable_count": fillable_count,
                "hidden_count": hidden_count,
                "exposed_count": exposed_count,
                "has_visible": has_visible,
                "has_resource": has_resource,
                "resource_path": resource_path,
                "confidence": confidence,
                "reasons": reasons,
                "matched_patterns": matched_patterns,
                "suggestions": suggestions,
                "direct_returns": direct_returns[:5],  # Cap to avoid noise
                "missing_selects": missing_selects[:5],
            }
        )

    # Sort: high → medium → low, then by exposed_count descending
    _conf_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (_conf_order.get(f["confidence"], 9), -f["exposed_count"]))

    return findings[:limit]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="over-fetch",
    category="health",
    summary="Detect models that serialize more fields than necessary in API responses",
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
@click.command("over-fetch")
@click.option(
    "--threshold",
    "-t",
    default=20,
    show_default=True,
    help="Minimum number of $fillable fields to flag a model",
)
@click.option(
    "--limit",
    "-n",
    default=30,
    show_default=True,
    help="Maximum number of findings to display",
)
@click.option(
    "--leaks-only/--no-leaks-only",
    "leaks_only",
    default=None,
    help=(
        "Show only BARE and UNGUARDED_RELATION findings "
        "(omit GUARDED_RELATION advisory). Default off, but `--ci` "
        "implies --leaks-only; pass --no-leaks-only to override."
    ),
)
@click.pass_context
def over_fetch_cmd(ctx, threshold, limit, leaks_only):
    """Detect models that serialize more fields than necessary in API responses.

    Finds large models ($fillable) without $hidden/$visible field filtering,
    controllers that return models directly without API Resources, and
    queries missing ->select() to limit fetched columns.

    Unlike ``api-drift`` (which compares backend field names against
    frontend TypeScript types) and ``orphan-routes`` (which finds dead
    endpoints), this command focuses on data volume and field exposure.

    Confidence levels:

    \b
      high   — 30+ fillable fields, no $hidden/$visible, no API Resource
      medium — 20+ fillable fields, minimal $hidden (< 3 fields)
      low    — 15+ fillable fields, missing ->select() in list queries

    \b
    Examples:
        roam over-fetch                  # Scan with default threshold (20 fields)
        roam over-fetch --threshold 30   # Only flag very large models
        roam over-fetch --limit 10       # Show top 10 findings only
        roam --json over-fetch           # JSON output
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # W21.6 --ci composition: under --ci, default leaks_only=True so the CI
    # gate fails only on real leaks (BARE/UNGUARDED). leaks_only is a
    # tri-state Click option (--leaks-only/--no-leaks-only/None=unset);
    # explicit user flags ALWAYS win over the --ci inference (LAW 11).
    ci_mode = ctx.obj.get("ci_mode", False) if ctx.obj else False
    if leaks_only is None:
        leaks_only = bool(ci_mode)
    ensure_index()

    with open_db(readonly=True) as conn:
        findings = analyze_over_fetch(conn, threshold=threshold, limit=limit)
        endpoint_findings = analyze_endpoint_states(conn, find_project_root())

    # -------------------------------------------------------------------
    # Tally by confidence (model-level) and by state (endpoint-level)
    # -------------------------------------------------------------------
    by_confidence: dict[str, int] = defaultdict(int)
    for f in findings:
        by_confidence[f["confidence"]] += 1

    total = len(findings)
    high_count = by_confidence.get("high", 0)
    medium_count = by_confidence.get("medium", 0)
    low_count = by_confidence.get("low", 0)

    conf_parts = []
    if high_count:
        conf_parts.append(f"{high_count} high")
    if medium_count:
        conf_parts.append(f"{medium_count} medium")
    if low_count:
        conf_parts.append(f"{low_count} low")
    conf_str = ", ".join(conf_parts) if conf_parts else "none"

    # 3-state endpoint tallies — computed from the FULL classification, not
    # the post-filter list. This is intentional: SUMMARY tells the truth;
    # the FINDINGS list is what gets filtered by --leaks-only.
    bare_count = sum(1 for e in endpoint_findings if e["state"] == "BARE")
    guarded_relation_count = sum(1 for e in endpoint_findings if e["state"] == "GUARDED_RELATION")
    unguarded_relation_count = sum(1 for e in endpoint_findings if e["state"] == "UNGUARDED_RELATION")
    endpoint_total = bare_count + guarded_relation_count + unguarded_relation_count

    # Apply --leaks-only filter to the findings LIST only (summary counts
    # preserved above). The flag is a presentation filter; detection is
    # identical with or without it.
    if leaks_only:
        endpoint_findings = [
            e for e in endpoint_findings if e["state"] in {"BARE", "UNGUARDED_RELATION"}
        ]

    # Endpoint verdict — concrete-noun anchored (LAW 4). Names the worst
    # endpoint by file:line where possible so agents see WHICH leak to fix.
    real_leaks = bare_count + unguarded_relation_count
    if endpoint_total:
        ep_parts: list[str] = []
        if bare_count:
            ep_parts.append(f"{bare_count} BARE leak{'s' if bare_count != 1 else ''}")
        if guarded_relation_count and not leaks_only:
            ep_parts.append(
                f"{guarded_relation_count} GUARDED_RELATION (already partial)"
            )
        if unguarded_relation_count:
            ep_parts.append(
                f"{unguarded_relation_count} UNGUARDED_RELATION "
                f"({'real leak' if unguarded_relation_count == 1 else 'real leaks'})"
            )
        if leaks_only and guarded_relation_count:
            # Explicit suppression note so the verdict makes clear WHY the
            # GUARDED count isn't in the leak list. Positive vocabulary per
            # CONSTRAINT 7 — name what's surviving (real leaks), then state
            # the suppression as a closing clause.
            leak_clause = ", ".join(ep_parts) if ep_parts else "0 real leaks"
            endpoint_verdict = (
                f"{real_leaks} real leak{'s' if real_leaks != 1 else ''} "
                f"({leak_clause}) — {guarded_relation_count} guarded-relation "
                f"finding{'s' if guarded_relation_count != 1 else ''} suppressed via --leaks-only"
            )
        else:
            endpoint_verdict = ", ".join(ep_parts)
    else:
        endpoint_verdict = ""

    if total or endpoint_total:
        head_bits: list[str] = []
        if total:
            head_bits.append(
                f"{total} over-fetch pattern{'s' if total != 1 else ''} ({conf_str})"
            )
        if endpoint_verdict:
            head_bits.append(endpoint_verdict)
        verdict = "; ".join(head_bits)
    else:
        verdict = "No over-fetch patterns detected"

    # -------------------------------------------------------------------
    # JSON output
    # -------------------------------------------------------------------
    if json_mode:
        # `endpoint_findings` is the 3-state classification surface — keep
        # it separate from model-level `findings` so existing consumers
        # don't break. The summary carries the headline counts so agents
        # can read the verdict line and skip the full envelope.
        partial_success = bare_count > 0 or unguarded_relation_count > 0
        click.echo(
            to_json(
                json_envelope(
                    "over-fetch",
                    summary={
                        "verdict": verdict,
                        "total": total,
                        "threshold": threshold,
                        "by_confidence": dict(by_confidence),
                        # 3-state endpoint classification counts — these
                        # reflect the FULL classification regardless of
                        # --leaks-only; the flag only filters the findings
                        # list. Summary always tells the truth.
                        "bare_count": bare_count,
                        "guarded_relation_count": guarded_relation_count,
                        "unguarded_relation_count": unguarded_relation_count,
                        "endpoint_total": endpoint_total,
                        "real_leak_count": real_leaks,
                        "state": "ok" if real_leaks == 0 else "leak",
                        "partial_success": partial_success,
                        "leaks_only": leaks_only,
                    },
                    findings=findings,
                    endpoint_findings=endpoint_findings,
                )
            )
        )
        return

    # -------------------------------------------------------------------
    # Text output
    # -------------------------------------------------------------------
    click.echo(f"VERDICT: {verdict}")

    # Endpoint-level 3-state block — printed BEFORE the model-level findings
    # because it names concrete endpoints by file:line and is what the
    # caller usually wants to act on first (CONSTRAINT 12 — executability).
    if endpoint_findings:
        click.echo()
        click.echo("Endpoint classification (BARE / GUARDED_RELATION / UNGUARDED_RELATION):")
        for ep in endpoint_findings:
            click.echo(
                f"  [{ep['severity']}] {ep['state']:<20} {ep['endpoint']}  {ep['location']}"
            )
            if ep.get("evidence"):
                click.echo(f"          Evidence: {ep['evidence']}")
            if ep.get("recommendation"):
                click.echo(f"          Fix: {ep['recommendation']}")

    if not findings:
        return

    click.echo()
    click.echo("Large models without field filtering:")

    for f in findings:
        conf = f["confidence"]
        model = f["model_name"]
        fillable = f["fillable_count"]
        hidden = f["hidden_count"]
        model_loc = f["model_location"]

        # Header line: [confidence]  ModelName (N fillable, M hidden)  path:line
        click.echo(f"  [{conf}]  {model} ({fillable} fillable, {hidden} hidden)  {model_loc}")

        # Reason lines
        for reason in f["reasons"]:
            click.echo(f"          {reason}")
        # surface matched_patterns when present so the text
        # surface mirrors the JSON shape and reviewers see WHY in one line.
        patterns = f.get("matched_patterns") or []
        if patterns:
            click.echo(f"          Matched: {', '.join(str(p) for p in patterns[:5])}")

        # API Resource status
        if f["has_resource"]:
            click.echo(f"          Resource: {f['resource_path']}")

        # Direct return locations (for high-confidence)
        if f["direct_returns"]:
            click.echo(f"          Direct controller returns ({len(f['direct_returns'])} location(s)):")
            for dr in f["direct_returns"][:3]:
                click.echo(f"            {dr['location']}  {dr['snippet']}")

        # Missing select locations (for low-confidence)
        if f["missing_selects"]:
            click.echo(f"          Queries without ->select() ({len(f['missing_selects'])} location(s)):")
            for ms in f["missing_selects"][:3]:
                click.echo(f"            {ms['location']}  {ms['snippet']}")

        # Fix suggestion(s)
        for suggestion in f["suggestions"]:
            click.echo(f"          Fix: {suggestion}")

        click.echo()
