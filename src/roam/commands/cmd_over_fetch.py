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

import hashlib
import json as _json
import os
import re
import sqlite3
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import json_envelope, loc, to_json

# W114 (W93 follow-up): over-fetch is the seventh detector migrating onto
# the central findings registry (after ``clones`` in W95, ``dead`` in
# W99, ``complexity`` in W102, ``smells`` in W109, ``n1`` in W110, and
# ``missing-index`` in W111). The shape mirrors those — a stable
# detector version stamp and a deterministic ``finding_id_str`` so
# re-runs upsert instead of duplicating rows. Bump this when the
# model-level confidence ladder, the 3-state endpoint classification,
# or the body-shaping skip rules change meaningfully (the registry's
# ``claim`` and confidence tier are derived from those).
OVER_FETCH_DETECTOR_VERSION: str = "1.0.0"


# W114 — per-state confidence-tier mapping for the 3-state endpoint
# classification.
#
# The endpoint detector relies on regex pattern matching scoped to PHP
# method bodies (via ``_extract_method_bodies_with_lines``) plus a
# graph-backed file query for the controller set. That places it in the
# "structural" tier of the registry's vocabulary: graph evidence backs
# the scoping, but the leak verdict itself is pattern-based.
#
#  - ``BARE``               -> ``structural``: confirmed leak — a load
#       call (paginate/get/all) without ``->select()`` or Resource
#       wrapping. Structural evidence: method body parsed for the chain.
#  - ``UNGUARDED_RELATION`` -> ``structural``: confirmed leak — eager
#       ``with('rel')`` without column selection. Same evidence class as
#       BARE (regex on structured method bodies).
#  - ``GUARDED_RELATION``   -> ``heuristic``: advisory — partial fix
#       already applied via ``with('rel:cols')``. The leak is mostly
#       closed; the row is surfaced so reviewers can decide whether to
#       harden the relation further. Lowest tier keeps the signal-to-
#       noise ratio for cross-detector consumers reasonable.
_ENDPOINT_STATE_TO_CONFIDENCE: dict[str, str] = {
    "BARE": "structural",
    "UNGUARDED_RELATION": "structural",
    "GUARDED_RELATION": "heuristic",
}


# W114 — per-confidence-bucket tier mapping for the model-level findings.
#
# These mirror the discipline used by W99 ``dead``: graph-backed signals
# land at ``structural``, threshold-only signals at ``heuristic``. We
# intentionally don't promote anything here to ``static_analysis``
# because over-fetch is regex+threshold driven — there is no taint or
# dataflow proof that a given column actually reaches the wire.
#
#  - ``high``   -> ``structural``: graph-backed (the controller scan
#       confirmed at least one direct-return site for this model) on top
#       of the 30+ fillable threshold and absent Resource wrapper.
#  - ``medium`` -> ``heuristic``: threshold-only (20+ fillable, < 3
#       hidden) without a confirmed controller-side leak.
#  - ``low``    -> ``heuristic``: threshold-only (15+ fillable, missing
#       ``->select()``) — the weakest of the three signals.
_MODEL_CONFIDENCE_TO_TIER: dict[str, str] = {
    "high": "structural",
    "medium": "heuristic",
    "low": "heuristic",
}


def _over_fetch_model_finding_id(model_name: str, file_path: str) -> str:
    """Stable, deterministic finding id for one model-level over-fetch hit.

    The (model_name, file_path) pair re-identifies the same model across
    runs. We fold both fields into the digest so two models with the
    same class name in different namespaces (a common Laravel pattern)
    get distinct ids.
    """
    raw = f"{model_name}:{file_path}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"over-fetch:model:{digest}"


def _over_fetch_endpoint_finding_id(controller: str, method: str, file_path: str, state: str) -> str:
    """Stable, deterministic finding id for one endpoint-level hit.

    The (controller, method, file_path, state) tuple keys the finding —
    folding ``state`` in means a method that flips from GUARDED to
    UNGUARDED across runs upserts as a fresh row rather than colliding
    on the old advisory entry. The old row stays in place until the
    next persist sweep clears it (via upsert miss); that's the same
    contract every other detector uses for state transitions.
    """
    raw = f"{controller}:{method}:{file_path}:{state}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"over-fetch:endpoint:{digest}"


def _resolve_over_fetch_subject_id(
    conn: sqlite3.Connection,
    file_path: str,
    symbol_name: str,
    line_start: int | None,
) -> int | None:
    """Best-effort lookup of ``symbols.id`` for a (file, name, line) triple.

    Mirrors ``cmd_smells._resolve_smell_subject_id`` and
    ``clone_detect._resolve_symbol_id`` — same fallback ladder
    (exact match by line_start, then nearest-line by name). Returns
    ``None`` when nothing matches; the findings registry permits NULL
    subject_id by design (file/edge/commit findings).
    """
    try:
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? AND s.line_start = ? "
            "LIMIT 1",
            (file_path, symbol_name, line_start),
        ).fetchone()
        if row is not None:
            return int(row[0])
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? "
            "ORDER BY ABS(COALESCE(s.line_start, 0) - ?) "
            "LIMIT 1",
            (file_path, symbol_name, line_start or 0),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        # Pre-W89 schema or symbols table absent — fall back to NULL.
        return None


def _emit_over_fetch_findings(
    conn: sqlite3.Connection,
    findings_data: list[dict],
    endpoint_findings_data: list[dict],
    source_version: str,
) -> int:
    """Mirror over-fetch results into the central findings registry.

    Emits TWO kinds of finding rows:

    * One row per model-level finding (``findings`` list) — keyed by
      ``over-fetch:model:<digest>``. ``subject_kind="symbol"`` when the
      model class resolves in ``symbols``; ``"file"`` otherwise.
    * One row per endpoint-level finding (``endpoint_findings`` list,
      3-state classification) — keyed by
      ``over-fetch:endpoint:<digest>``. ``subject_kind="symbol"`` when
      the controller method resolves; ``"file"`` otherwise.

    Returns the total count of finding rows written. Caller is
    responsible for opening ``conn`` writable; emit_finding does not
    commit (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard over-fetch command path.
    """
    # Local import keeps the cost out of the read-only path — callers
    # without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0

    # ---- Model-level findings ------------------------------------------
    for f in findings_data:
        model_name = f.get("model_name") or ""
        file_path = f.get("model_path") or ""
        if not model_name or not file_path:
            continue
        # Model classes are stored as ``class`` symbols by the PHP
        # extractor — best-effort lookup so the registry row can JOIN
        # back to ``symbols``.
        line_hint: int | None = None
        model_location = f.get("model_location") or ""
        if model_location and ":" in model_location:
            try:
                line_hint = int(model_location.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                line_hint = None
        subject_id = _resolve_over_fetch_subject_id(conn, file_path, model_name, line_hint)
        finding_id = _over_fetch_model_finding_id(model_name, file_path)
        confidence_bucket = f.get("confidence") or "low"
        tier = _MODEL_CONFIDENCE_TO_TIER.get(confidence_bucket, "heuristic")
        evidence = {
            "kind": "model",
            "model_name": model_name,
            "model_path": file_path,
            "model_location": model_location,
            "fillable_count": f.get("fillable_count"),
            "hidden_count": f.get("hidden_count"),
            "exposed_count": f.get("exposed_count"),
            "has_visible": f.get("has_visible"),
            "has_resource": f.get("has_resource"),
            "resource_path": f.get("resource_path"),
            "confidence_bucket": confidence_bucket,
            "reasons": f.get("reasons") or [],
            "matched_patterns": f.get("matched_patterns") or [],
            "direct_return_count": len(f.get("direct_returns") or []),
            "missing_select_count": len(f.get("missing_selects") or []),
        }
        fillable = f.get("fillable_count")
        hidden = f.get("hidden_count")
        claim = f"Over-fetch ({confidence_bucket}): {model_name} ({file_path}) — {fillable} fillable, {hidden} hidden"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol" if subject_id is not None else "file",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=tier,
                source_detector="over-fetch",
                source_version=source_version,
            ),
        )
        written += 1

    # ---- Endpoint-level findings (3-state classification) --------------
    for ep in endpoint_findings_data:
        controller = ep.get("controller") or ""
        method_name = ep.get("method") or ""
        file_path = ep.get("file") or ""
        state = ep.get("state") or ""
        if not (controller and method_name and file_path and state):
            continue
        line_start = ep.get("line")
        subject_id = _resolve_over_fetch_subject_id(conn, file_path, method_name, line_start)
        finding_id = _over_fetch_endpoint_finding_id(controller, method_name, file_path, state)
        tier = _ENDPOINT_STATE_TO_CONFIDENCE.get(state, "heuristic")
        # Strip the nested ``details.guarded`` / ``details.unguarded``
        # dict-of-dicts down to flat counts so the evidence payload
        # stays small (< 4KB per the registry contract).
        details = ep.get("details") or {}
        guarded_list = details.get("guarded") or []
        unguarded_list = details.get("unguarded") or []
        evidence = {
            "kind": "endpoint",
            "endpoint": ep.get("endpoint"),
            "controller": controller,
            "method": method_name,
            "file": file_path,
            "line": line_start,
            "location": ep.get("location"),
            "state": state,
            "severity": ep.get("severity"),
            "evidence_text": ep.get("evidence"),
            "recommendation": ep.get("recommendation"),
            "guarded_relation_count": len(guarded_list),
            "unguarded_relation_count": len(unguarded_list),
            "has_select": bool(details.get("has_select")),
            "bare_main_model": bool(details.get("bare_main_model")),
        }
        claim = (
            f"Over-fetch endpoint: {ep.get('endpoint') or controller + '@' + method_name} "
            f"({file_path}:{line_start}) — state={state} (severity {ep.get('severity')})"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol" if subject_id is not None else "file",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=tier,
                source_detector="over-fetch",
                source_version=source_version,
            ),
        )
        written += 1

    return written


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
_MODEL_STATIC_RE = re.compile(
    r"\b(?P<model>[A-Z][A-Za-z0-9_]+)\s*::\s*(?:query|paginate|all|where|with|find|first|select)\s*\("
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# W886: delegate test-path detection to the canonical commands-layer
# helper (W873 audit). The local PHP-flavoured variant only recognised
# ``test_*`` / ``*_test.php`` basenames + ``tests/`` / ``test/`` /
# ``testing/`` directory components — the canonical helper covers all
# of those plus PHP camelCase ``*Test.php`` (per
# ``test_conventions.DEFAULT_TEST_PATTERNS``), ``__tests__/``,
# ``spec/``, and ~16 other cross-language conventions. Behaviour
# strictly widens; the 5 call-sites only ever SKIP test paths, so
# matching MORE conventions can only improve precision (fewer test
# files leaking into over-fetch findings), never regress.
from roam.commands.changed_files import is_test_file as _is_test_path


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


# Position-based string/comment-aware delimiter matching: shared implementation
# (php_source_scan) so per-detector copies can't drift. The shared version also
# handles PHP heredocs/nowdocs — an unpaired apostrophe in a heredoc body (SQL
# `-- don't`, and this repo's Services DO hold heredoc SQL) poisoned a
# quote-aware scanner into string state — and PHP 8 `#[...]` attributes.
# A `]`/`}` buried in a doc comment or quoted literal (a route path
# '/users/{id}', an array key 'Item [beta]') cannot prematurely balance the
# count and truncate the matched span.
from roam.commands.php_source_scan import matching_delim_end as _matching_delim_end


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
    # Walk the array extent with a string/comment-aware matcher so a
    # ``[``/``]`` inside a string literal or comment within the toArray()
    # return can't prematurely balance the bracket count and truncate the
    # array body (same brace-in-comment defect fixed in auth-gaps).
    end_pos = _matching_delim_end(rest, bracket_pos, "[", "]")

    array_body = rest[bracket_pos : end_pos + 1]

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


# Per-line CODE-only (opens, closes) of {/}: shared implementation
# (php_source_scan) — see the note on _matching_delim_end above; both now
# also survive heredocs/nowdocs and PHP 8 attributes.
from roam.commands.php_source_scan import brace_deltas as _brace_deltas


def _extract_method_bodies_with_lines(source: str) -> list[dict]:
    """Pull out PHP class method bodies with start_line + name.

    Returns list of {name, start_line, body}. Mirrors the more elaborate
    helper used by auth-gaps; intentionally local so the over-fetch fix
    doesn't depend on a cross-module import.
    """
    out: list[dict] = []
    method_re = re.compile(r"(?:public|protected|private)\s+function\s+(\w+)\s*\(")
    lines = source.splitlines()
    # Code-only brace deltas (skip braces in strings/comments/backticks,
    # cross-line aware) so a `{id}` in a route-path string or a doc comment
    # can't shift where the method body's closing `}` is located. Aligned
    # by index with ``lines`` (both derive from ``source.splitlines()``).
    deltas = _brace_deltas(source)
    i = 0
    while i < len(lines):
        m = method_re.search(lines[i])
        if not m:
            i += 1
            continue
        method_name = m.group(1)
        start_line = i + 1  # 1-based
        # Walk to the closing brace using per-line code-only deltas. The
        # end-of-line check matches the original char loop's semantics
        # (brace depth was only inspected after each full line).
        brace_depth = 0
        body_lines: list[str] = []
        j = i
        found_open = False
        while j < len(lines):
            seg = lines[j]
            opens, closes = deltas[j]
            if opens:
                found_open = True
            brace_depth += opens - closes
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
            "Bare model load — add ->select(['col1','col2',...]) or wrap in an API Resource to control output columns."
        )
    if state == "UNGUARDED_RELATION":
        rels = ", ".join(u["relation"] for u in details.get("unguarded", []) or [])
        return (
            f"Add column selection: with('{rels.split(',')[0].strip()}:id,name,...') "
            "or wrap the loaded relation in a Resource. Currently dumps all "
            "relation columns."
        )
    if state == "GUARDED_RELATION":
        return "Already partially guarded; consider full API Resource wrapper for stronger contract."
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
        "SELECT path FROM files WHERE (path LIKE '%Controller%' OR path LIKE '%controller%') AND path LIKE '%.php'",
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

    # Sort: high → medium → low, then by exposed_count descending.
    # W596: canonical confidence-LEVEL rank — negate for high-first sort.
    findings.sort(key=lambda f: (-confidence_level_rank(f["confidence"], fallback=-1), -f["exposed_count"]))

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
    side_effect=True,
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
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each over-fetch hit (model-level + 3-state endpoint) "
        "into the central findings registry (``roam findings list "
        "--detector over-fetch``). Detector-specific output is "
        "unchanged; the registry rows are the denormalised cross-detector "
        "surface. Persisted set ignores --leaks-only display filtering — "
        "every classification (including GUARDED_RELATION advisories) is "
        "written so re-running with --leaks-only doesn't truncate the "
        "registry."
    ),
)
@click.pass_context
def over_fetch_cmd(ctx, threshold, limit, leaks_only, persist):
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
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    # W21.6 --ci composition: under --ci, default leaks_only=True so the CI
    # gate fails only on real leaks (BARE/UNGUARDED). leaks_only is a
    # tri-state Click option (--leaks-only/--no-leaks-only/None=unset);
    # explicit user flags ALWAYS win over the --ci inference (LAW 11).
    ci_mode = ctx.obj.get("ci_mode", False) if ctx.obj else False
    if leaks_only is None:
        leaks_only = bool(ci_mode)
    ensure_index()

    # W607-CE -- substrate-boundary plumbing for cmd_over_fetch.
    # ``_run_check_ce`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607ce_warnings_out`` rather than
    # crashing the over-fetch detector outright (W114 foundational
    # detector; W809 sealed the empty-corpus smoke but did NOT install
    # substrate isolation -- this wave adds it). Marker family
    # ``over_fetch_<phase>_failed:<exc_class>:<detail>``. Substrates
    # wrapped:
    #
    #   * analyze_over_fetch        -- core model-level detection
    #   * analyze_endpoint_states   -- 3-state endpoint classification
    #                                  (W84 BARE/GUARDED/UNGUARDED)
    #   * find_model_files          -- empty-state model counter
    #   * symbol_count_query        -- empty-state symbol-table COUNT
    #   * emit_findings             -- W114 findings-registry mirror
    #                                  (sqlite3.OperationalError silent
    #                                  no-op preserved for pre-W89 DB)
    #   * serialize_to_sarif        -- SARIF projection
    #   * aggregate_by_confidence   -- model-level by-confidence histogram
    #   * aggregate_by_state        -- endpoint 3-state tallies
    #   * apply_leaks_only_filter   -- W21.6 --leaks-only filter
    #   * compute_endpoint_verdict  -- endpoint-verdict composition
    _w607ce_warnings_out: list[str] = []

    def _run_check_ce(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CE marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an ``over_fetch_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ce_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ce_warnings_out.append(f"over_fetch_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DT -- additive aggregation-phase plumbing for cmd_over_fetch.
    # Layered ON TOP of the W607-CE substrate-CALL plumbing above; both
    # share the canonical ``over_fetch_*`` marker family and the
    # ``over_fetch_<phase>_failed:<exc_class>:<detail>`` shape contract.
    # The two bucket sources are merged at envelope-emit time into the
    # single ``warnings_out`` mirror so consumers see the full degradation
    # lineage. Aggregation phases wrapped (sibling pattern to cmd_n1's
    # W607-DQ):
    #
    #   * score_classify     -- buckets the run by over-fetch + leak
    #                          counts into NO_OVER_FETCH / OF_LIGHT /
    #                          OF_MODERATE / OF_HEAVY / DEGRADED
    #   * compute_predicate  -- rollup metrics dict (total_count, by_kind,
    #                          files_affected, bare_count, real_leaks)
    #   * compute_verdict    -- single-line verdict string (LAW 6 floor:
    #                          "over_fetch completed")
    #   * serialize_envelope -- json_envelope("over-fetch", ...) projection
    #
    # The 4 aggregation phase names DO NOT collide with the 10 W607-CE
    # substrate phase names (analyze_over_fetch / analyze_endpoint_states
    # / find_model_files / symbol_count_query / emit_findings /
    # serialize_to_sarif / aggregate_by_confidence / aggregate_by_state /
    # apply_leaks_only_filter / compute_endpoint_verdict).
    # ``compute_verdict`` is deliberately distinct from CE's
    # ``compute_endpoint_verdict`` so an agent can tell which layer raised.
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_dt(...)`` call:
    #   1. f-string verdict floor: NEVER re-interpolate the same values
    #      that tripped the closure inside the ``default=`` floor.
    #   2. kwarg-default eagerness: ``default=`` must be a literal
    #      constant, never a computed expression.
    #   3. json.dumps(default=str) sentinel: the serialize_envelope
    #      floor must be JSON-serializable with the standard encoder.
    #   4. phase-name collision: verified above against CE's 10 phases.
    #   5. len() at kwarg-bind: move len() INSIDE the closure, never at
    #      the ``_run_check_dt(...)`` call site.
    #   6. unguarded len()/if on poisoned object: the floor MUST be a
    #      concrete dict/str/None, never a sentinel that may
    #      __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    _w607dt_warnings_out: list[str] = []

    def _run_check_dt(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DT marker emission.

        Mirror of ``_run_check_ce`` shape (same
        ``over_fetch_<phase>_failed:`` marker family) but writes into
        ``_w607dt_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dt_warnings_out.append(f"over_fetch_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # W607-CE: ``analyze_over_fetch`` is the core model-level
        # detection. A raise inside per-model regex parsing or
        # controller-direct-return analysis used to crash the over-fetch
        # detector outright; now degrades to [] so the empty-state
        # envelope still emits with the marker AND
        # ``partial_success: True``.
        findings = _run_check_ce(
            "analyze_over_fetch",
            analyze_over_fetch,
            conn,
            threshold,
            limit,
            default=[],
        )
        if findings is None:
            findings = []

        # W607-CE: ``analyze_endpoint_states`` is the 3-state endpoint
        # classification (W84 BARE/GUARDED_RELATION/UNGUARDED_RELATION).
        # A raise inside the method-body extractor or with(...) classifier
        # used to crash the over-fetch detector; now degrades to [] with
        # a marker so the model-level findings still surface.
        endpoint_findings = _run_check_ce(
            "analyze_endpoint_states",
            analyze_endpoint_states,
            conn,
            find_project_root(),
            default=[],
        )
        if endpoint_findings is None:
            endpoint_findings = []

        # W805 (Pattern 2: silent fallbacks) — over-fetch only fires on
        # PHP/Laravel model files (path LIKE '%/Models/%'). On a
        # Python/JS/Go codebase, _find_model_files returns 0 and BOTH
        # finding lists come back empty. The historical verdict
        # "No over-fetch patterns detected" was indistinguishable from
        # "scan ran against a Laravel app and the models are clean".
        # Capture the empty-input state so the empty-corpus branch
        # below can name it explicitly.
        # W607-CE: wrap both empty-state probes so a raise in
        # _find_model_files / the symbols COUNT query degrades to 0 and
        # surfaces a marker rather than crashing the envelope.
        _model_files_result = _run_check_ce("find_model_files", _find_model_files, conn, default=[])
        _w805_model_files_count = len(_model_files_result or [])

        def _symbol_count():
            return conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

        _w805_symbol_count = _run_check_ce("symbol_count_query", _symbol_count, default=0)
        if _w805_symbol_count is None:
            _w805_symbol_count = 0

        # --- W114: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is independent of
        # the --leaks-only display filter — we emit every classification
        # (including GUARDED_RELATION advisories) so the registry stays
        # comprehensive regardless of how a particular invocation slices
        # the view. Matches the W109 smells / W111 missing-index persist
        # discipline.
        # W607-CE: ``_emit_over_fetch_findings`` substrate boundary. The
        # pre-W89 schema path (sqlite3.OperationalError on missing
        # ``findings`` table) is the EXPECTED degraded path -- the
        # try/except below maintains the W114 silent no-op contract for
        # that case. Generic exceptions surface via the
        # ``over_fetch_emit_findings_failed:<exc>:<detail>`` marker.
        if persist:
            try:
                _emit_over_fetch_findings(
                    conn,
                    findings,
                    endpoint_findings,
                    OVER_FETCH_DETECTOR_VERSION,
                )
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_over_fetch:emit_findings", _exc)
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-CE disclosure
                _w607ce_warnings_out.append(f"over_fetch_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

    # -------------------------------------------------------------------
    # Tally by confidence (model-level) and by state (endpoint-level)
    # -------------------------------------------------------------------
    # W607-CE: ``aggregate_by_confidence`` substrate -- model-level
    # by-confidence histogram. Degrades to an empty defaultdict on raise
    # so the envelope still composes with conf_str="none".
    def _aggregate_by_confidence():
        agg: defaultdict[str, int] = defaultdict(int)
        for f in findings:
            agg[f["confidence"]] += 1
        return agg

    by_confidence = _run_check_ce(
        "aggregate_by_confidence",
        _aggregate_by_confidence,
        default=defaultdict(int),
    )
    if by_confidence is None:
        by_confidence = defaultdict(int)

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
    # W607-CE: ``aggregate_by_state`` substrate -- 3-state endpoint
    # tallies. Degrades to (0, 0, 0) on raise so the envelope still
    # composes with empty endpoint counts. Hard distinction from the
    # model-level confidence histogram: this substrate operates on the
    # endpoint classification list, not the findings list.
    def _aggregate_by_state():
        bare = sum(1 for e in endpoint_findings if e["state"] == "BARE")
        guarded = sum(1 for e in endpoint_findings if e["state"] == "GUARDED_RELATION")
        unguarded = sum(1 for e in endpoint_findings if e["state"] == "UNGUARDED_RELATION")
        return (bare, guarded, unguarded)

    _state_tally = _run_check_ce("aggregate_by_state", _aggregate_by_state, default=(0, 0, 0))
    if _state_tally is None:
        _state_tally = (0, 0, 0)
    bare_count, guarded_relation_count, unguarded_relation_count = _state_tally
    endpoint_total = bare_count + guarded_relation_count + unguarded_relation_count

    # Apply --leaks-only filter to the findings LIST only (summary counts
    # preserved above). The flag is a presentation filter; detection is
    # identical with or without it.
    # W607-CE: ``apply_leaks_only_filter`` substrate -- W21.6 filter
    # isolation. Degrades to the pre-filter endpoint list so a raise in
    # the comprehension does NOT silently drop endpoint findings -- the
    # marker fires and the envelope still surfaces the full classification.
    if leaks_only:

        def _apply_leaks_only_filter():
            return [e for e in endpoint_findings if e["state"] in {"BARE", "UNGUARDED_RELATION"}]

        _filtered = _run_check_ce(
            "apply_leaks_only_filter",
            _apply_leaks_only_filter,
            default=endpoint_findings,
        )
        if _filtered is not None:
            endpoint_findings = _filtered

    # Endpoint verdict — concrete-noun anchored (LAW 4). Names the worst
    # endpoint by file:line where possible so agents see WHICH leak to fix.
    # W607-CE: ``compute_endpoint_verdict`` substrate -- verdict
    # composition. Degrades to "" on raise so the empty-endpoint branch
    # below still composes a coherent envelope.
    real_leaks = bare_count + unguarded_relation_count

    def _compute_endpoint_verdict():
        if not endpoint_total:
            return ""
        ep_parts: list[str] = []
        if bare_count:
            ep_parts.append(f"{bare_count} BARE leak{'s' if bare_count != 1 else ''}")
        if guarded_relation_count and not leaks_only:
            ep_parts.append(f"{guarded_relation_count} GUARDED_RELATION (already partial)")
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
            return (
                f"{real_leaks} real leak{'s' if real_leaks != 1 else ''} "
                f"({leak_clause}) — {guarded_relation_count} guarded-relation "
                f"finding{'s' if guarded_relation_count != 1 else ''} suppressed via --leaks-only"
            )
        return ", ".join(ep_parts)

    endpoint_verdict = _run_check_ce("compute_endpoint_verdict", _compute_endpoint_verdict, default="")
    if endpoint_verdict is None:
        endpoint_verdict = ""

    # W805 (Pattern 2: silent fallbacks) — distinguish detector-ran-clean
    # from detector-had-no-input. The empty-state classification is set
    # only when no model-level AND no endpoint-level findings exist,
    # because either lane producing a finding proves the detector did
    # meaningful work.
    w805_empty_state: str | None = None
    if not total and not endpoint_total:
        if _w805_symbol_count == 0:
            w805_empty_state = "empty_corpus"
        elif _w805_model_files_count == 0:
            w805_empty_state = "no_php_models"

    # W607-DT -- score_classify boundary. Buckets the run by total
    # over-fetch findings + real endpoint leaks into a state label:
    #   * NO_OVER_FETCH  -- total == 0 AND real_leaks == 0
    #   * OF_LIGHT       -- 0 < (total + real_leaks) <= 3
    #   * OF_MODERATE    -- 3 < (total + real_leaks) <= 10
    #   * OF_HEAVY       -- (total + real_leaks) > 10
    #   * DEGRADED       -- floor on raise
    # W978 5th-discipline: ``total`` / ``real_leaks`` passed as raw
    # args; counting / iteration lives INSIDE the closure (no len() at
    # kwarg-bind).
    def _score_classify_run(_total, _real_leaks):
        _combined = _total + _real_leaks
        if _combined == 0:
            _state = "NO_OVER_FETCH"
        elif _combined <= 3:
            _state = "OF_LIGHT"
        elif _combined <= 10:
            _state = "OF_MODERATE"
        else:
            _state = "OF_HEAVY"
        return {"state": _state, "scanned": _combined}

    _score_dict = _run_check_dt(
        "score_classify",
        _score_classify_run,
        total,
        real_leaks,
        default={"state": "DEGRADED", "scanned": 0},
    )

    # W607-DT -- compute_predicate boundary. Rollup metrics dict
    # surfacing aggregate dimensions (total_count / by_kind /
    # files_affected / bare_count / real_leaks) so a downstream refactor
    # of the rollup logic surfaces a marker rather than crashing. W978
    # 5th-discipline: ``findings`` + ``endpoint_findings`` passed as
    # raw args; counting / iteration lives INSIDE the closure.
    def _compute_predicate_fields(_findings, _endpoint_findings):
        _by_kind: defaultdict[str, int] = defaultdict(int)
        _files: set[str] = set()
        for _f in _findings:
            # Over-fetch model-level findings are classified by reason
            # bucket ("missing_hidden" / "no_resource" / "direct_return"
            # / "missing_select"); collapse to "model_level" if absent.
            _k = _f.get("kind") or _f.get("io_type") or "model_level"
            _by_kind[_k] += 1
            _loc = _f.get("model_location") or ""
            if _loc:
                _file = _loc.rsplit(":", 1)[0] if ":" in _loc else _loc
                if _file:
                    _files.add(_file)
        for _ep in _endpoint_findings:
            _state_key = _ep.get("state") or "unknown_state"
            _by_kind[_state_key] += 1
            _loc = _ep.get("location") or ""
            if _loc:
                _file = _loc.rsplit(":", 1)[0] if ":" in _loc else _loc
                if _file:
                    _files.add(_file)
        return {
            "total_count": len(_findings),
            "by_kind": dict(_by_kind),
            "files_affected": len(_files),
        }

    _pred_fields = _run_check_dt(
        "compute_predicate",
        _compute_predicate_fields,
        findings,
        endpoint_findings,
        default={
            "total_count": 0,
            "by_kind": {},
            "files_affected": 0,
        },
    )

    # W607-DT -- compute_verdict boundary. Wraps the verdict string
    # assembly so a downstream f-string refactor (non-int totals from
    # a vocabulary refactor, or a __format__-raising sentinel) surfaces
    # a marker rather than crashing the envelope. Literal
    # "over_fetch completed" floor (LAW 6 still holds: the line works
    # standalone).
    #
    # W978 1st-discipline: the floor MUST NOT re-interpolate the same
    # values that tripped the closure. W978 2nd-discipline: ``default=``
    # is a literal constant.
    def _build_verdict_str(_total, _endpoint_total, _conf_str, _endpoint_verdict, _empty_state):
        if _total or _endpoint_total:
            head_bits: list[str] = []
            if _total:
                head_bits.append(f"{_total} over-fetch pattern{'s' if _total != 1 else ''} ({_conf_str})")
            if _endpoint_verdict:
                head_bits.append(_endpoint_verdict)
            return "; ".join(head_bits)
        if _empty_state == "empty_corpus":
            return (
                "no symbols to analyze (corpus empty; "
                "run `roam index --force` to populate the graph before over-fetch detection)"
            )
        if _empty_state == "no_php_models":
            return (
                "no PHP/Laravel model files found (over-fetch detection requires "
                "files under Models/ or Model/; detector ran but had no input to analyze)"
            )
        return "No over-fetch patterns detected"

    verdict = _run_check_dt(
        "compute_verdict",
        _build_verdict_str,
        total,
        endpoint_total,
        conf_str,
        endpoint_verdict,
        w805_empty_state,
        default="over_fetch completed",
    )

    # -------------------------------------------------------------------
    # SARIF output (W1219)
    # -------------------------------------------------------------------
    # Branches BEFORE json/text so the pre-existing paths stay
    # byte-identical to pre-W1219. The SARIF projection feeds the
    # POST-`--leaks-only` filtered endpoint list — so a CI gate sees the
    # same surviving leaks the human / agent sees. Model-level findings
    # are NOT subject to --leaks-only filtering (that flag scopes only
    # the 3-state endpoint classification per the W21.6 contract).
    if sarif_mode:
        # W607-CE: SARIF projection substrate -- a raise in the SARIF
        # writer used to crash the over-fetch command on the CI
        # integration path; now degrades silently to None with a marker,
        # and the function returns early (matches pre-W607-CE semantics
        # that SARIF mode short-circuits).
        def _emit_sarif():
            from roam.output.sarif import over_fetch_to_sarif, write_sarif

            # Combine model-level + endpoint-level findings into one list
            # so the SARIF projector can branch on the ``state`` field
            # (present on endpoint findings only).
            combined: list[dict] = list(findings) + list(endpoint_findings)
            click.echo(write_sarif(over_fetch_to_sarif(combined)))

        _run_check_ce("serialize_to_sarif", _emit_sarif, default=None)
        return

    # -------------------------------------------------------------------
    # JSON output
    # -------------------------------------------------------------------
    if json_mode:
        # `endpoint_findings` is the 3-state classification surface — keep
        # it separate from model-level `findings` so existing consumers
        # don't break. The summary carries the headline counts so agents
        # can read the verdict line and skip the full envelope.
        # Pre-existing partial_success semantic (leaks present) is
        # preserved for back-compat. W805 added detector-input-state
        # disclosure via the `detector_state` field for the
        # empty-corpus / no-PHP-models cases — partial_success stays
        # False on those branches because 0 real leaks is genuinely
        # healthy (W809 pins this contract: empty corpus → 0 real
        # leaks → partial_success MUST be False). The legacy `state`
        # ("ok"/"leak") is preserved unchanged.
        # W607-CE + W607-DT: any substrate-CALL OR aggregation-phase
        # marker flips partial_success: True so a degraded-empty
        # envelope is NOT mistaken for a clean "no over-fetch patterns"
        # verdict. The pre-W607-CE leak-or-no-leak semantics still
        # drive partial_success on the happy path.
        _combined_warnings = list(_w607ce_warnings_out) + list(_w607dt_warnings_out)
        partial_success = bare_count > 0 or unguarded_relation_count > 0 or bool(_combined_warnings)
        summary_block = {
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
            "detector_state": w805_empty_state or "scanned",
            "partial_success": partial_success,
            "leaks_only": leaks_only,
            "model_files_scanned": _w805_model_files_count,
            # W607-DT: surface score_classify result on the envelope so
            # consumers can read the run state without re-deriving from
            # raw counts. W978 7th-discipline anchor: bare
            # ``_score_dict["state"]`` lookup (floor dict guarantees the
            # key) -- NOT ``.get("state", expensive_default)``.
            "run_state": _score_dict["state"],
            # W607-DT: surface compute_predicate rollup on the envelope
            # so consumers can read the aggregate dimensions without
            # rebuilding from the raw lists. W978 7th-discipline anchor:
            # bare key lookups.
            "by_kind": _pred_fields["by_kind"],
            "files_affected": _pred_fields["files_affected"],
        }
        envelope_kwargs: dict = {
            "summary": summary_block,
            "findings": findings,
            "endpoint_findings": endpoint_findings,
        }
        # W607-CE + W607-DT: mirror combined substrate-CALL + aggregation-
        # phase markers into BOTH the top-level envelope ``warnings_out``
        # AND ``summary.warnings_out`` so MCP consumers see disclosure
        # regardless of which surface they read.
        if _combined_warnings:
            summary_block["warnings_out"] = list(_combined_warnings)
            envelope_kwargs["warnings_out"] = list(_combined_warnings)

        # W607-DT -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("over-fetch", ...)`` would otherwise
        # crash AFTER all substrate + aggregation signals were already
        # gathered. Floor to a minimal envelope stub so consumers still
        # receive a parseable JSON object with the marker attached + the
        # canonical command name. W978 6th-discipline: floor is a
        # concrete dict, not a sentinel that may __len__-raise downstream.
        _envelope_floor: dict = {
            "command": "over-fetch",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings),
            },
            "warnings_out": list(_combined_warnings),
        }
        envelope = _run_check_dt(
            "serialize_envelope",
            json_envelope,
            "over-fetch",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-DT -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``over_fetch_serialize_envelope_failed:`` marker was appended
        # to ``_w607dt_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output. Clean path ->
        # envelope is the real json_envelope return value, no rebuild.
        if envelope is _envelope_floor and _w607dt_warnings_out:
            _combined_warnings = list(_w607ce_warnings_out) + list(_w607dt_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
            _envelope_floor["warnings_out"] = list(_combined_warnings)
            envelope = _envelope_floor
        click.echo(to_json(envelope))
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
            click.echo(f"  [{ep['severity']}] {ep['state']:<20} {ep['endpoint']}  {ep['location']}")
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
