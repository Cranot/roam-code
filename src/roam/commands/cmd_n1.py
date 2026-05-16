"""Detect implicit N+1 I/O patterns across ORM frameworks.

Unlike ``roam algo``'s explicit "I/O in loop" detector, this command finds
**implicit** N+1 patterns: computed properties on data classes that trigger
I/O (DB queries, HTTP calls) when the object is serialized in a collection.

Supported frameworks:
- Laravel/Eloquent: $appends accessors → relationship lazy loading
- Django: @property → related manager access
- Rails/ActiveRecord: methods → association access
- SQLAlchemy: @hybrid_property → relationship access
- JPA/Hibernate: @Transient → entity association access
- Generic: any collection-iterable class with I/O in computed properties

Detection algorithm:
1. Find "data classes" (ORM models, entities, DTOs)
2. Find computed/virtual properties on those classes
3. Trace call chains from properties to I/O operations
4. Check if the class appears in collection/pagination contexts
5. Check for eager-loading / prefetch configuration
6. Flag: property triggers I/O per-instance without batch loading
"""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.index.test_conventions import is_test_file as _is_canonical_test_file
from roam.output.confidence import (
    confidence_distribution,
    confidence_level_rank,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import json_envelope, loc, to_json

# W110: n1 is the fourth detector migrating onto the central findings
# registry (after `clones` in W95, `dead` in W99, and `complexity` in
# W102). The shape mirrors those — a stable detector version stamp and
# a deterministic ``finding_id_str`` so re-runs upsert instead of
# duplicating rows. Bump this when the confidence-derivation rule in
# :func:`_n1_classify` or the I/O-tracing predicates in
# :func:`analyze_n1` change meaningfully — both shape the registry
# row's ``claim`` / ``confidence``.
N1_DETECTOR_VERSION: str = "1.0.0"


def _n1_finding_id(
    model_qname: str,
    accessor_name: str,
    relationship: str,
    appended_attribute: str,
) -> str:
    """Stable, deterministic finding id for one N+1 finding.

    The (model_qname, accessor_name, relationship, appended_attribute)
    tuple uniquely identifies one N+1 pattern: "model X's accessor Y
    triggers lazy-load of relationship Z via appended attribute W".
    Re-running ``roam n1 --persist`` on unchanged source upserts the
    existing row rather than duplicating.

    We avoid keying on ``symbol_id`` because the n1 analyzer doesn't
    surface a stable subject_id for the accessor at the finding-build
    site (the dict carries ``accessor_name`` / ``model_name`` strings,
    not ids). Hashing the readable identifiers keeps the id stable
    across reindex cycles that re-mint symbol ids.
    """
    raw = f"{model_qname}|{accessor_name}|{relationship}|{appended_attribute}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"n1:pattern:{digest}"


def _resolve_accessor_subject_id(conn, finding: dict) -> int | None:
    """Resolve the accessor's ``symbols.id`` from the finding dict.

    The n1 finding shape carries the accessor's name + display location
    (``"file:line"``) rather than a numeric symbol id. We re-query
    ``symbols`` by name + line_start so registry rows can JOIN back to
    the canonical symbol table. Returns ``None`` when the accessor
    can't be resolved — :func:`emit_finding` tolerates a NULL
    subject_id by design.
    """
    accessor_name = finding.get("accessor_name") or ""
    accessor_location = finding.get("accessor_location") or ""
    if not accessor_name or ":" not in accessor_location:
        return None
    # ``loc()`` formats as ``path:line`` (may include extra suffixes on
    # some output paths — we take the LAST colon-separated token as the
    # candidate line number).
    parts = accessor_location.rsplit(":", 1)
    if len(parts) != 2:
        return None
    try:
        line_start = int(parts[1])
    except (TypeError, ValueError):
        return None
    try:
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.name = ? AND s.line_start = ? AND f.path = ? "
            "LIMIT 1",
            (accessor_name, line_start, parts[0]),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        # Fall back to a name-only match — handles cases where the
        # ``loc()`` format includes trailing decoration that defeats
        # exact path equality.
        try:
            row = conn.execute(
                "SELECT s.id FROM symbols s WHERE s.name = ? AND s.line_start = ? LIMIT 1",
                (accessor_name, line_start),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _emit_n1_findings(conn, findings: list[dict]) -> int:
    """Mirror each N+1 finding into the central findings registry.

    Returns the count of rows emitted (one per N+1 finding). Wrapped by
    the caller in try/except so a pre-W89 DB (no ``findings`` table)
    silently no-ops rather than crashing the standard read path.

    All n1 findings are STRUCTURAL by nature — they detect a graph
    pattern (an accessor whose outgoing edges trace into a relationship
    or query-builder method), then check that the model is referenced
    from a controller / service. Every emitted row gets
    ``CONFIDENCE_STRUCTURAL``. The high / medium / low gradation
    surfaced in the JSON envelope is a refactor-priority signal, not a
    detector-confidence signal, so it lives in the evidence payload
    rather than collapsing into the registry's confidence tier.
    """
    # Local imports keep the cost out of the read-only path — callers
    # without --persist never reach here, so the import only runs when
    # we're actually writing.
    from roam.db.findings import (
        CONFIDENCE_STRUCTURAL,
        FindingRecord,
        emit_finding,
    )

    emitted = 0
    for f in findings:
        model_name = f.get("model_name") or ""
        accessor_name = f.get("accessor_name") or ""
        relationship = f.get("relationship") or ""
        appended = f.get("appended_attribute") or ""
        if not (model_name and accessor_name and relationship):
            # Defensive: skip malformed rows so a missing key doesn't
            # poison the whole batch. analyze_n1 always populates these
            # today, but the registry write should stay tolerant.
            continue
        finding_id = _n1_finding_id(model_name, accessor_name, relationship, appended)
        subject_id = _resolve_accessor_subject_id(conn, f)
        evidence = {
            "model_name": model_name,
            "model_location": f.get("model_location"),
            "accessor_name": accessor_name,
            "accessor_location": f.get("accessor_location"),
            "appended_attribute": appended,
            "relationship": relationship,
            "io_type": f.get("io_type"),
            "eager_loaded": bool(f.get("eager_loaded")),
            "confidence_label": f.get("confidence"),
            "severity": f.get("severity"),
            "collection_contexts": f.get("collection_contexts") or [],
            "suggestion": f.get("suggestion"),
        }
        claim = (
            f"Implicit N+1: {model_name}.{accessor_name} triggers "
            f"{f.get('io_type') or 'I/O'} on relationship '{relationship}' "
            f"via appended attribute '{appended}'"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                # All n1 findings are structural — they detect a
                # deterministic graph pattern (accessor → relationship /
                # query-builder edge) plus an optional reference-context
                # signal. Manual review is for prioritisation, not for
                # questioning the detector's evidence.
                confidence=CONFIDENCE_STRUCTURAL,
                source_detector="n1",
                source_version=N1_DETECTOR_VERSION,
            ),
        )
        emitted += 1
    return emitted


# Sentinel used by helpers that accept pre-fetched bulk data. Distinct from
# ``None`` because ``None`` is a valid "caller pre-fetched and there's no
# match" answer; the sentinel means "caller did NOT pre-fetch — fall back
# to the per-model query".
_BULK_NOT_FETCHED = object()

# ---------------------------------------------------------------------------
# Framework detection helpers
# ---------------------------------------------------------------------------

# Patterns that identify ORM model classes by parent class or trait
_MODEL_PARENTS = {
    # Laravel / Eloquent / Django (both use bare "Model" — single membership)
    "Model",
    "Eloquent",
    "Authenticatable",
    "models.Model",
    # Rails / ActiveRecord
    "ApplicationRecord",
    "ActiveRecord::Base",
    # SQLAlchemy
    "Base",
    "DeclarativeBase",
    # JPA / Hibernate
    "Serializable",
    # TypeScript ORMs
    "BaseEntity",
    "Entity",
}

# Symbols that indicate "this class is a data/model class"
_MODEL_INDICATORS = {
    # Laravel
    "$fillable",
    "$guarded",
    "$casts",
    "$appends",
    "$table",
    "$hidden",
    # Django
    "objects",
    "Meta",
    "DoesNotExist",
    # Rails
    "has_many",
    "belongs_to",
    "has_one",
    "scope",
    # SQLAlchemy
    "__tablename__",
    "Column",
    "relationship",
    # JPA
    "@Entity",
    "@Table",
}

# Property/accessor patterns per framework
_ACCESSOR_PATTERNS = {
    # Laravel: get{Name}Attribute methods
    "laravel": re.compile(r"^get(\w+)Attribute$"),
    # Django: properties (detected by kind='property' or @property decorator)
    "django": re.compile(r".*"),  # any property on a Model
    # Rails: instance methods that access associations
    "rails": re.compile(r".*"),
}

# Relationship access patterns (method calls that trigger lazy loading)
_RELATIONSHIP_CALLS = {
    # Laravel Eloquent
    "hasMany",
    "hasOne",
    "belongsTo",
    "belongsToMany",
    "morphMany",
    "morphOne",
    "morphTo",
    "morphToMany",
    # Django ORM
    "all",
    "filter",
    "get",
    "first",
    "last",
    "count",
    "exists",
    "select_related",
    "prefetch_related",
    # Rails ActiveRecord
    "where",
    "find",
    "find_by",
    "pluck",
    # SQLAlchemy
    "query",
    "filter_by",
    # Generic DB
    "load",
    "fetch",
    "findOrFail",
}

# I/O operations that indicate a DB query or network call
_IO_INDICATORS = {
    # Database
    "query",
    "execute",
    "select",
    "where",
    "find",
    "findOrFail",
    "first",
    "get",
    "all",
    "count",
    "exists",
    "pluck",
    "save",
    "create",
    "update",
    "delete",
    # HTTP
    "fetch",
    "request",
    "post",
    "put",
    # File
    "fopen",
    "file_get_contents",
    "read",
    "readFile",
}

# Eager loading configuration patterns
_EAGER_LOAD_PATTERNS = {
    "with",
    "eagerLoad",
    "eager_load",
    "select_related",
    "prefetch_related",
    "includes",
    "eager_load_relations",
    "joinedload",
    "subqueryload",
    "JOIN FETCH",
}


def _is_test_path(path: str) -> bool:
    """Delegate to the canonical ``test_conventions.is_test_file`` detector.

    W6.2 consolidation: the canonical detector handles both filename
    patterns (Vitest / Vue SFC ``*.test.vue``, pytest ``test_*.py``,
    Go ``*_test.go``, ...) AND directory patterns (``tests/``,
    ``__tests__/``, ``spec/``, ...). No fallback needed.
    """
    return _is_canonical_test_file(path)


def _detect_framework(conn) -> str:
    """Detect the primary ORM framework from file languages and symbol names."""
    lang_counts = {}
    for r in conn.execute("SELECT language, COUNT(*) as cnt FROM files GROUP BY language").fetchall():
        lang_counts[r["language"]] = r["cnt"]

    # Check for framework-specific symbols (PHP parser stores without $ prefix)
    laravel_signals = conn.execute(
        "SELECT COUNT(*) as cnt FROM symbols WHERE name IN "
        "('fillable', 'appends', 'casts', 'guarded', "
        " '$fillable', '$appends', '$casts', '$guarded')"
    ).fetchone()["cnt"]

    django_signals = conn.execute(
        "SELECT COUNT(*) as cnt FROM symbols WHERE name IN ('objects', 'Meta', '__tablename__')"
    ).fetchone()["cnt"]

    if lang_counts.get("php", 0) > 10 and laravel_signals > 0:
        return "laravel"
    if lang_counts.get("python", 0) > 10 and django_signals > 0:
        return "django"
    if lang_counts.get("ruby", 0) > 10:
        return "rails"
    if lang_counts.get("java", 0) > 10:
        return "jpa"
    return "generic"


# ---------------------------------------------------------------------------
# Core detection: find model classes and their computed properties
# ---------------------------------------------------------------------------


def _find_model_classes(conn):
    """Find all ORM model / data classes in the codebase.

    Uses two strategies since some parsers (PHP) don't set parent_id:
    1. Classes with indicator children (via parent_id OR same-file line range)
    2. Classes in Model/Entity directories
    """
    model_classes = {}

    # Strategy 1a: Classes with children via parent_id
    rows = conn.execute(
        "SELECT DISTINCT parent.id, parent.name, parent.qualified_name, "
        "parent.kind, f.path as file_path, parent.file_id, "
        "parent.line_start, parent.line_end "
        "FROM symbols parent "
        "JOIN files f ON parent.file_id = f.id "
        "JOIN symbols child ON child.parent_id = parent.id "
        "WHERE parent.kind = 'class' "
        "AND child.name IN ('fillable', 'appends', 'casts', 'guarded', "
        "  'table', 'hidden', 'connection', "
        "  '$fillable', '$appends', '$casts', '$guarded', "
        "  '$table', '$hidden', '$connection', "
        "  '__tablename__', 'Meta') "
    ).fetchall()
    for r in rows:
        model_classes[r["id"]] = dict(r)

    # Strategy 1b: Classes with indicator properties in same file/line range
    # (for parsers like PHP that don't set parent_id)
    indicator_names = (
        "'fillable', 'appends', 'casts', 'guarded', 'table', 'hidden', "
        "'connection', '$fillable', '$appends', '$casts', '$guarded', "
        "'$table', '$hidden', '$connection', '__tablename__', 'Meta'"
    )
    classes = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, "
        "f.path as file_path, s.file_id, s.line_start, s.line_end "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class' AND s.line_end IS NOT NULL"
    ).fetchall()

    for cls in classes:
        if cls["id"] in model_classes:
            continue
        # Check if any indicator property exists in the same file within class line range
        indicator = conn.execute(
            f"SELECT 1 FROM symbols WHERE file_id = ? "
            f"AND kind = 'property' AND name IN ({indicator_names}) "
            f"AND line_start >= ? AND line_start <= ? LIMIT 1",
            (cls["file_id"], cls["line_start"], cls["line_end"] or 999999),
        ).fetchone()
        if indicator:
            model_classes[cls["id"]] = dict(cls)

    # Strategy 2: Classes in Model/Entity directories
    suffix_rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, "
        "f.path as file_path, s.file_id, s.line_start, s.line_end "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class' "
        "AND (f.path LIKE '%/Models/%' OR f.path LIKE '%/models/%' "
        "  OR f.path LIKE '%/Entity/%' OR f.path LIKE '%/entities/%')"
    ).fetchall()
    for r in suffix_rows:
        if r["id"] not in model_classes:
            model_classes[r["id"]] = dict(r)

    return model_classes


def _bulk_fetch_appends_symbols(conn, model_ids, models):
    """Bulk-fetch ``$appends`` / ``appends`` symbols for a list of model IDs.

    Replaces the per-model 1-2 queries in :func:`_find_appends_properties`.
    Issues two batched_in queries (parent_id-linked + file-range fallback)
    plus a small companion fetch for file_id resolution. The total is
    constant in ``len(model_ids)`` rather than 2N.

    Returns a dict mapping ``model_id`` → appends-symbol row dict (or
    ``None`` when no appends declaration was found for that model).
    """
    from roam.db.connection import batched_in

    out: dict[int, dict | None] = {int(mid): None for mid in model_ids}
    if not model_ids:
        return out

    # Strategy 1: parent_id-linked appends
    for r in batched_in(
        conn,
        "SELECT s.parent_id, s.id, s.default_value, s.line_start, s.line_end, "
        "f.path as file_path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.parent_id IN ({ph}) AND s.name IN ('appends', '$appends')",
        list(model_ids),
    ):
        out[int(r["parent_id"])] = {
            "id": r["id"],
            "default_value": r["default_value"],
            "line_start": r["line_start"],
            "line_end": r["line_end"],
            "file_path": r["file_path"],
        }

    # Strategy 2: file-range fallback for models without parent_id-linked
    # appends. Bulk-fetch model file_ids, then bulk-fetch all
    # appends-named property symbols in those files, then match per-model
    # by line range in Python.
    missing = [mid for mid, v in out.items() if v is None]
    if not missing:
        return out

    file_id_by_model: dict[int, int] = {}
    for r in batched_in(
        conn,
        "SELECT id, file_id FROM symbols WHERE id IN ({ph})",
        missing,
    ):
        file_id_by_model[int(r["id"])] = int(r["file_id"])

    file_ids = list({fid for fid in file_id_by_model.values()})
    if not file_ids:
        return out

    cands_by_file: dict[int, list] = {}
    for r in batched_in(
        conn,
        "SELECT s.id, s.file_id, s.default_value, s.line_start, s.line_end, "
        "f.path as file_path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.file_id IN ({ph}) AND s.name IN ('appends', '$appends') "
        "AND s.kind = 'property'",
        file_ids,
    ):
        cands_by_file.setdefault(int(r["file_id"]), []).append(r)

    for mid in missing:
        fid = file_id_by_model.get(mid)
        if fid is None:
            continue
        info = models.get(mid) or {}
        ls = info.get("line_start", 0)
        le = info.get("line_end") or 999999
        for cand in cands_by_file.get(fid, []):
            if ls <= cand["line_start"] <= le:
                out[mid] = {
                    "id": cand["id"],
                    "default_value": cand["default_value"],
                    "line_start": cand["line_start"],
                    "line_end": cand["line_end"],
                    "file_path": cand["file_path"],
                }
                break

    return out


def _find_appends_properties(conn, model_id, model_info, *, bulk_appends_sym=_BULK_NOT_FETCHED):
    """Find $appends array entries for a Laravel model.

    Returns list of appended attribute names (e.g., ['full_name', 'is_admin']).
    Handles both parent_id-linked and flat (same-file line range) symbol structures.
    If default_value is not captured by the parser, reads from source file.

    When ``bulk_appends_sym`` is provided (any value other than the
    sentinel — including ``None`` for "no appends found"), the helper
    skips the per-model lookups and uses the pre-fetched row directly.
    ``analyze_n1`` threads the bulk-fetched dict through to keep total
    query count constant in the model count.
    """
    if bulk_appends_sym is _BULK_NOT_FETCHED:
        # Try parent_id first
        appends_sym = conn.execute(
            "SELECT s.id, s.default_value, s.line_start, s.line_end, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.parent_id = ? AND s.name IN ('appends', '$appends')",
            (model_id,),
        ).fetchone()

        # Fallback: same file, within class line range
        if not appends_sym:
            appends_sym = conn.execute(
                "SELECT s.id, s.default_value, s.line_start, s.line_end, f.path as file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.file_id = (SELECT file_id FROM symbols WHERE id = ?) "
                "AND s.name IN ('appends', '$appends') "
                "AND s.kind = 'property' "
                "AND s.line_start >= ? AND s.line_start <= ?",
                (model_id, model_info["line_start"], model_info.get("line_end") or 999999),
            ).fetchone()
    else:
        appends_sym = bulk_appends_sym

    if not appends_sym:
        return []

    # If default_value is captured, use it
    if appends_sym["default_value"]:
        return re.findall(r"['\"](\w+)['\"]", appends_sym["default_value"])

    # Otherwise read from source file
    file_path = appends_sym["file_path"]
    line_start = appends_sym["line_start"]
    line_end = appends_sym["line_end"] or line_start + 20

    # Resolve to absolute path from project root
    from roam.db.connection import find_project_root

    root = find_project_root()
    abs_path = root / file_path if root else None

    if abs_path and abs_path.is_file():
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            # Extract the appends array from source (line_start is 1-indexed)
            snippet = "".join(lines[max(0, line_start - 1) : line_end])
            return re.findall(r"['\"](\w+)['\"]", snippet)
        except OSError:
            pass

    return []


def _bulk_fetch_methods_with_locations(conn, model_ids):
    """Bulk-fetch method symbols with full location columns for a list of
    model IDs. Returns dict ``{model_id: [method_row_dict, ...]}``.

    The base ``analyze_n1`` model-methods cache only carries
    ``(id, name, kind, parent_id)``. ``_find_accessor_methods`` needs
    ``qualified_name``, ``file_path``, ``line_start``, ``line_end`` too.
    Rather than two bulk fetches, ``analyze_n1`` calls this once and
    derives the lighter view from it.
    """
    from roam.db.connection import batched_in

    out: dict[int, list] = {int(mid): [] for mid in model_ids}
    if not model_ids:
        return out
    for r in batched_in(
        conn,
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.parent_id, "
        "f.path as file_path, s.line_start, s.line_end "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.parent_id IN ({ph}) AND s.kind = 'method'",
        list(model_ids),
    ):
        out.setdefault(int(r["parent_id"]), []).append(dict(r))
    return out


def _bulk_fetch_methods_by_file(conn, file_ids):
    """Bulk-fetch every ``kind='method'`` symbol across a set of files.

    Returns ``{file_id: [{"id", "name", "kind", "line_start"}, ...]}``
    sorted by ``line_start`` per file. Used by the candidate-filter
    fallback in :func:`analyze_n1` when a model's methods aren't
    parent_id-linked (common in PHP) — pre-fetching everything in a
    single batched ``IN`` query eliminates the per-model file-range
    SELECT that defeated the surrounding bulk-fetch work.

    Edge cases: empty ``file_ids`` → ``{}``. Files with no methods
    simply don't appear in the result; the caller treats a missing key
    as "no methods for this model" — same semantics as the old
    per-model SELECT returning ``[]``.
    """
    from roam.db.connection import batched_in

    out: dict[int, list[dict]] = {}
    if not file_ids:
        return out
    # De-dup before batching — multiple models can share one file_id.
    unique_ids = list({int(fid) for fid in file_ids if fid is not None})
    if not unique_ids:
        return out
    for r in batched_in(
        conn,
        "SELECT s.file_id, s.id, s.name, s.kind, s.line_start FROM symbols s "
        "WHERE s.kind = 'method' AND s.file_id IN ({ph}) "
        "ORDER BY s.file_id, s.line_start",
        unique_ids,
    ):
        out.setdefault(int(r["file_id"]), []).append(
            {"id": r["id"], "name": r["name"], "kind": r["kind"], "line_start": r["line_start"]}
        )
    return out


def _find_accessor_methods(conn, model_id, model_info, appended_names, *, bulk_methods=_BULK_NOT_FETCHED):
    """Find accessor methods for appended attributes.

    For Laravel: get{StudlyName}Attribute methods.
    Returns list of (accessor_symbol_row, appended_name) tuples.
    Handles both parent_id-linked and flat symbol structures.

    When ``bulk_methods`` is provided (the pre-fetched method list for
    this model from :func:`_bulk_fetch_methods_with_locations`), the
    per-model SELECTs are skipped. The fallback file-range query stays
    on the per-model path because it only triggers when parent_id
    linkage is absent — rare enough that caching it isn't worth the
    extra plumbing.
    """
    accessors = []

    if bulk_methods is not _BULK_NOT_FETCHED:
        methods = list(bulk_methods or [])
    else:
        # Get all methods in this class (try parent_id first, then line range)
        methods = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, "
            "f.path as file_path, s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.parent_id = ? AND s.kind = 'method'",
            (model_id,),
        ).fetchall()

    if not methods:
        # Fallback: same file, within class line range
        methods = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, "
            "f.path as file_path, s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.file_id = (SELECT file_id FROM symbols WHERE id = ?) "
            "AND s.kind = 'method' "
            "AND s.line_start >= ? AND s.line_start <= ?",
            (model_id, model_info["line_start"], model_info.get("line_end") or 999999),
        ).fetchall()

    # Build lookup: snake_case appended name → StudlyCase accessor name
    for attr_name in appended_names:
        # Convert snake_case to StudlyCase: "full_name" → "FullName"
        studly = "".join(part.capitalize() for part in attr_name.split("_"))
        accessor_name = f"get{studly}Attribute"

        for m in methods:
            if m["name"] == accessor_name:
                accessors.append((dict(m), attr_name))
                break

    return accessors


_RELATIONSHIP_METHODS = {
    "hasMany",
    "hasOne",
    "belongsTo",
    "belongsToMany",
    "morphMany",
    "morphOne",
    "morphTo",
    "morphToMany",
    "hasManyThrough",
}
_RELATIONSHIP_DEFINITION_CALLS = (
    "hasMany",
    "hasOne",
    "belongsTo",
    "belongsToMany",
    "morphMany",
    "morphOne",
    "morphTo",
    "morphToMany",
    "hasManyThrough",
    "hasOneThrough",
)
_QUERY_BUILDER_METHODS = (
    "first",
    "get",
    "exists",
    "count",
    "pluck",
    "find",
    "findOrFail",
    "all",
    "orderBy",
    "where",
)
_QUERY_BUILDER_CHAIN_PATTERNS = (
    "->first()",
    "->get()",
    "->exists()",
    "->count()",
    "->pluck()",
)
_THIS_ACCESS_SKIP_METHODS = {
    "relationLoaded",
    "getAttribute",
    "setAttribute",
    "getKey",
    "toArray",
    "toJson",
}


def _bulk_fetch_accessor_edge_traces(conn, accessor_ids):
    """Pre-fetch outgoing edges for a batch of accessor IDs *and* the
    sub-edge target names of every callee that's a method.

    Replaces the per-accessor 1 + N queries inside
    :func:`_trace_io_via_edges`. Returns a 2-tuple of dicts:

    * ``callees_by_accessor[int(accessor_id)]`` → list of edge-target row
      dicts (id, name, qualified_name, kind, file_path, edge_kind).
    * ``sub_callee_names[int(callee_id)]`` → set of target-symbol names
      reached from that callee.

    Only the first dict is keyed by accessor; the second is keyed by
    callee_id because the same callee can be shared across accessors.
    """
    from roam.db.connection import batched_in

    callees_by_accessor: dict[int, list[dict]] = {int(aid): [] for aid in accessor_ids}
    sub_callee_names: dict[int, set[str]] = {}
    if not accessor_ids:
        return callees_by_accessor, sub_callee_names

    callee_method_ids: set[int] = set()
    for r in batched_in(
        conn,
        "SELECT e.source_id, t.id, t.name, t.qualified_name, t.kind, "
        "f.path as file_path, e.kind as edge_kind "
        "FROM edges e JOIN symbols t ON e.target_id = t.id "
        "JOIN files f ON t.file_id = f.id "
        "WHERE e.source_id IN ({ph})",
        list(accessor_ids),
    ):
        callees_by_accessor.setdefault(int(r["source_id"]), []).append(dict(r))
        if r["kind"] == "method":
            callee_method_ids.add(int(r["id"]))

    if callee_method_ids:
        for cid in callee_method_ids:
            sub_callee_names.setdefault(cid, set())
        for r in batched_in(
            conn,
            "SELECT e.source_id, t.name FROM edges e JOIN symbols t ON e.target_id = t.id WHERE e.source_id IN ({ph})",
            list(callee_method_ids),
        ):
            sub_callee_names.setdefault(int(r["source_id"]), set()).add(r["name"])

    return callees_by_accessor, sub_callee_names


def _trace_io_via_edges(
    conn, accessor_id, model_method_names, *, bulk_callees=_BULK_NOT_FETCHED, bulk_sub_names=_BULK_NOT_FETCHED
):
    """Strategy 1: walk outgoing edges from the accessor to look for
    relationship-defining methods or query-builder methods.

    When ``bulk_callees`` and ``bulk_sub_names`` are provided (from
    :func:`_bulk_fetch_accessor_edge_traces`), the helper consults
    in-memory dicts instead of issuing per-accessor queries.
    """
    io_chains: list[tuple[str, str]] = []
    if bulk_callees is _BULK_NOT_FETCHED:
        callees = conn.execute(
            "SELECT t.id, t.name, t.qualified_name, t.kind, "
            "f.path as file_path, e.kind as edge_kind "
            "FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "JOIN files f ON t.file_id = f.id "
            "WHERE e.source_id = ?",
            (accessor_id,),
        ).fetchall()
    else:
        callees = bulk_callees or []

    for callee in callees:
        name = callee["name"]
        if name in model_method_names and callee["kind"] == "method":
            if bulk_sub_names is _BULK_NOT_FETCHED:
                sub_callees = conn.execute(
                    "SELECT t.name FROM edges e JOIN symbols t ON e.target_id = t.id WHERE e.source_id = ?",
                    (callee["id"],),
                ).fetchall()
                sub_names = {r["name"] for r in sub_callees}
            else:
                sub_names = bulk_sub_names.get(int(callee["id"]), set())
            rel_methods = sub_names & _RELATIONSHIP_METHODS
            if rel_methods:
                io_chains.append((name, f"relationship ({', '.join(rel_methods)})"))
                continue

        if name in _QUERY_BUILDER_METHODS:
            io_chains.append((name, "query builder"))
    return io_chains


def _classify_method_body(method_snippet: str) -> str | None:
    """Decide whether a method body looks like a relationship definition or
    a query-builder chain. Returns the matching io_type label or None."""
    if any(rc in method_snippet for rc in _RELATIONSHIP_DEFINITION_CALLS):
        return "lazy-load relationship"
    if any(qb in method_snippet for qb in _QUERY_BUILDER_CHAIN_PATTERNS):
        return "query builder"
    return None


def _trace_io_via_source(conn, accessor_info, model_methods, model_method_names):
    """Strategy 2: read the accessor source and pattern-match
    ``$this->relationName`` accesses (needed for PHP where property access
    doesn't generate edges)."""
    io_chains: list[tuple[str, str]] = []
    file_path = accessor_info.get("file_path", "")
    line_start = accessor_info.get("line_start", 0)
    line_end = accessor_info.get("line_end") or line_start + 30

    from roam.db.connection import find_project_root

    root = find_project_root()
    abs_path = root / file_path if root else None
    if not (abs_path and abs_path.is_file()):
        return io_chains

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return io_chains

    snippet = "".join(lines[max(0, line_start - 1) : line_end])
    this_accesses = re.findall(r"\$this->(\w+?)(?:\s*->|\s*\?->|(?:\(\))\s*->|\s*;|\s*\))", snippet)
    this_method_calls = re.findall(r"\$this->(\w+)\(\)", snippet)

    methods_by_name = {m["name"]: m for m in model_methods}
    for accessed in set(this_accesses + this_method_calls):
        if accessed in _THIS_ACCESS_SKIP_METHODS:
            continue
        if accessed not in model_method_names:
            continue
        method_sym = methods_by_name.get(accessed)
        if not method_sym:
            continue
        m_sym_full = conn.execute(
            "SELECT s.line_start, s.line_end FROM symbols s WHERE s.id = ?",
            (method_sym["id"],),
        ).fetchone()
        if not m_sym_full:
            continue
        m_start = max(0, m_sym_full["line_start"] - 1)
        m_end = m_sym_full["line_end"] or m_start + 15
        method_snippet = "".join(lines[m_start:m_end])
        io_type = _classify_method_body(method_snippet)
        if io_type:
            io_chains.append((accessed, io_type))
    return io_chains


def _trace_accessor_io(
    conn, accessor_id, accessor_info, model_methods, *, bulk_callees=_BULK_NOT_FETCHED, bulk_sub_names=_BULK_NOT_FETCHED
):
    """Trace an accessor method to see if it triggers I/O.

    Uses two strategies:
    1. Edge-based: follow outgoing edges to relationship/query methods
    2. Source-based: pattern-match $this->relation in accessor source code
       (needed for PHP where property access doesn't generate edges)

    Returns list of (relationship_name, io_type) tuples found.

    ``bulk_callees`` / ``bulk_sub_names`` are forwarded to
    :func:`_trace_io_via_edges` for batched execution; the source-based
    fallback only reads the accessor's own file so it doesn't benefit
    from bulk fetching.
    """
    model_method_names = {m["name"] for m in model_methods}
    io_chains = _trace_io_via_edges(
        conn,
        accessor_id,
        model_method_names,
        bulk_callees=bulk_callees,
        bulk_sub_names=bulk_sub_names,
    )
    if not io_chains:
        io_chains = _trace_io_via_source(conn, accessor_info, model_methods, model_method_names)
    return io_chains


def _build_controller_cache(conn) -> dict[str, str]:
    """Read every Laravel ``*Controller*.php`` file once and return
    ``{path: content}`` for ``_find_eager_loads`` to query in-memory.

    Pre-fix, ``_find_eager_loads`` issued the query AND read each
    controller from disk per model — for a 100-model app with 50
    controllers, that's 5000 ``read_text()`` calls (audit B2). Building
    the cache once at the top of ``analyze_n1`` collapses that to 50.
    """
    from roam.db.connection import find_project_root

    cache: dict[str, str] = {}
    root = find_project_root()
    if root is None:
        return cache
    rows = conn.execute(
        "SELECT f.path FROM files f WHERE f.path LIKE '%Controller%' AND f.path LIKE '%.php'"
    ).fetchall()
    for row in rows:
        rel = row["path"]
        abs_path = root / rel
        if not abs_path.is_file():
            continue
        try:
            cache[rel] = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return cache


def _bulk_fetch_with_symbols(conn, models):
    """Pre-loop bulk fetch: single SELECT for every ``with`` / ``$with``
    property symbol in the codebase, then match each one back to its
    owning model by ``file_id``.

    Pre-fix, :func:`_find_eager_loads` issued one
    ``WHERE name IN ('with', '$with') AND f.path LIKE '%{model_name}.php'``
    SELECT per model — N queries on a Laravel app with N models.
    Building one ``{model_id: with_sym_row | None}`` dict up-front
    collapses that to a single query (B3 / W80 follow-up).

    Returns ``{int(model_id): with_sym_dict | None}`` where the row
    dict contains ``default_value``, ``line_start``, ``line_end``,
    ``file_path``. Models without a matching ``$with`` map to ``None``
    so callers can distinguish "fetched and absent" from "not fetched".
    """
    out: dict[int, dict | None] = {int(mid): None for mid in models}
    if not models:
        return out

    file_id_to_model: dict[int, int] = {}
    for mid, minfo in models.items():
        fid = minfo.get("file_id")
        if fid is not None:
            # If two models share a file (rare — separate classes in
            # one PHP file), the last one wins; the historical
            # per-model query used a file-path LIKE that also had this
            # ambiguity, so the bulk path preserves the same behavior.
            file_id_to_model[int(fid)] = int(mid)

    if not file_id_to_model:
        return out

    rows = conn.execute(
        "SELECT s.file_id, s.default_value, s.line_start, s.line_end, "
        "f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name IN ('with', '$with') AND s.kind = 'property'"
    ).fetchall()
    for r in rows:
        mid = file_id_to_model.get(int(r["file_id"]))
        if mid is None:
            continue
        # First match wins per model — fetchone() semantics from the
        # old per-model query.
        if out.get(mid) is None:
            out[mid] = {
                "default_value": r["default_value"],
                "line_start": r["line_start"],
                "line_end": r["line_end"],
                "file_path": r["file_path"],
            }
    return out


def _build_resource_config_cache(conn) -> list[str]:
    """Pre-loop bulk fetch: read every Laravel resource-config file
    once and return the list of file contents.

    Pre-fix, :func:`_find_eager_loads` re-issued the
    ``LIKE '%config/resources.php'`` SELECT AND re-read every matching
    file from disk on every model. On a 100-model app with 5 resource
    configs that's 500 reads of the SAME files. The cached contents
    are model-name-independent (the per-model filter happens inside
    the eagerLoad-match loop), so one shared list serves all models.

    Returns a list of file-content strings. Files whose ``read_text``
    fails are silently skipped.
    """
    from roam.db.connection import find_project_root

    contents: list[str] = []
    root = find_project_root()
    if root is None:
        return contents
    rows = conn.execute(
        "SELECT f.path FROM files f WHERE f.path LIKE '%config/resources.php' OR f.path LIKE '%config/resources/%'"
    ).fetchall()
    for row in rows:
        abs_path = root / row["path"]
        if not abs_path.is_file():
            continue
        try:
            contents.append(abs_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return contents


def _find_eager_loads(
    conn,
    model_name,
    controller_cache: dict[str, str] | None = None,
    *,
    bulk_with_sym=_BULK_NOT_FETCHED,
    resource_config_contents: list[str] | None = None,
    model_id: int | None = None,
):
    """Find eager loading configuration for a model.

    Checks:
    1. Model $with property (auto-eager-load on every query)
    2. Resource config files (Laravel: ->eagerLoad([...]) in config/resources.php)
    3. Controller ::with() calls

    For sources where the parser doesn't capture method chains as symbols,
    falls back to reading source files and pattern matching.

    Parameters
    ----------
    controller_cache:
        Pre-built ``{path: content}`` map from
        :func:`_build_controller_cache`. When provided, the per-call
        directory scan + per-controller ``read_text`` is skipped. Pass
        ``None`` (default) for ad-hoc invocations; ``analyze_n1`` builds
        the cache once and threads it through every per-model call.
    bulk_with_sym:
        Pre-fetched ``$with`` symbol row (or ``None`` for "fetched and
        absent") from :func:`_bulk_fetch_with_symbols`. When provided,
        the per-model SELECT in step 1 is skipped. The model_id-based
        index is the bulk path; ad-hoc callers without a model_id can
        still trigger the legacy ``LIKE '%{model_name}.php'`` query by
        leaving this as the sentinel.
    resource_config_contents:
        Pre-read list of resource-config file contents from
        :func:`_build_resource_config_cache`. When provided, the
        per-model SQL scan + ``read_text`` for resource configs is
        skipped. The eagerLoad-match logic still runs per-model since
        the model name is part of the heuristic.
    model_id:
        Used together with ``bulk_with_sym`` to look up the bulk
        symbol — the bulk index keys by model id, not name. Optional
        when ``bulk_with_sym`` is the sentinel.

    Returns set of relationship names that are eager loaded.
    """
    eager_loaded = set()

    from roam.db.connection import find_project_root

    root = find_project_root()

    # --- 1. Check $with property on the model ---
    if bulk_with_sym is _BULK_NOT_FETCHED:
        with_sym = conn.execute(
            "SELECT s.default_value, s.line_start, s.line_end, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.name IN ('with', '$with') AND s.kind = 'property' "
            "AND f.path LIKE ?",
            (f"%{model_name}.php",),
        ).fetchone()
    else:
        with_sym = bulk_with_sym

    if with_sym:
        if with_sym["default_value"]:
            eager_loaded.update(re.findall(r"['\"](\w+)['\"]", with_sym["default_value"]))
        elif root:
            # Read from source
            abs_path = root / with_sym["file_path"]
            if abs_path.is_file():
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                    start = max(0, with_sym["line_start"] - 1)
                    end = with_sym["line_end"] or start + 10
                    snippet = "".join(lines[start:end])
                    eager_loaded.update(re.findall(r"['\"](\w+)['\"]", snippet))
                except OSError:
                    pass

    # --- 2. Check resource config files for eagerLoad ---
    # Convert model class name to resource key pattern
    # e.g., "Post" → look for Post::class or 'posts' near eagerLoad
    model_lower = model_name.lower()

    if resource_config_contents is not None:
        config_contents_iter = resource_config_contents
    else:
        config_contents_iter = _iter_resource_config_contents(conn, root)

    for content in config_contents_iter:
        # Find eagerLoad([...]) calls and extract relationship names
        # Pattern: ->eagerLoad(['rel1', 'rel2', ...])
        for match in re.finditer(
            r"->eagerLoad\(\s*\[(.*?)\]\s*\)",
            content,
            re.DOTALL,
        ):
            # Check if this eagerLoad is near the model reference
            # Look backwards from match for the model class name
            start = max(0, match.start() - 500)
            context = content[start : match.end()]
            if model_name in context or f"{model_name}::class" in context or model_lower in context.lower():
                rels = re.findall(r"['\"](\w+)['\"]", match.group(1))
                eager_loaded.update(rels)

    # --- 3. Check controller with() calls ---
    # Look for Model::with(['rel']) or ->with(['rel']) near model references.
    # Source contents come from either the pre-built cache (fast path,
    # used by ``analyze_n1``) or a per-call directory scan + read_text
    # (slow path, for ad-hoc callers without a cache).
    if controller_cache is not None:
        contents_iter = controller_cache.values()
    else:
        contents_iter = _iter_controller_contents(conn, root)

    for content in contents_iter:
        eager_loaded.update(_extract_with_calls(content, model_name))

    return eager_loaded


def _iter_resource_config_contents(conn, root):
    """Stream resource-config contents on demand — fallback when
    :func:`_find_eager_loads` is called without a pre-built
    ``resource_config_contents`` list (ad-hoc callers).
    """
    if root is None:
        return
    rows = conn.execute(
        "SELECT f.path FROM files f WHERE f.path LIKE '%config/resources.php' OR f.path LIKE '%config/resources/%'"
    ).fetchall()
    for row in rows:
        abs_path = root / row["path"]
        if not abs_path.is_file():
            continue
        try:
            yield abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue


def _iter_controller_contents(conn, root):
    """Stream controller-file contents on demand — fallback when no
    pre-built cache is available. Skips files whose ``read_text`` fails.
    """
    if root is None:
        return
    rows = conn.execute(
        "SELECT f.path FROM files f WHERE f.path LIKE '%Controller%' AND f.path LIKE '%.php'"
    ).fetchall()
    for cf in rows:
        abs_path = root / cf["path"]
        if not abs_path.is_file():
            continue
        try:
            yield abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue


def _extract_with_calls(content: str, model_name: str) -> set[str]:
    """Pull relationship names out of ``Model::with([...])`` calls in
    a single piece of PHP source. Returns the set of names found (may
    be empty).
    """
    if model_name not in content:
        return set()
    rels: set[str] = set()
    for match in re.finditer(
        rf"{model_name}::with\(\s*\[(.*?)\]\s*\)",
        content,
        re.DOTALL,
    ):
        rels.update(re.findall(r"['\"](\w+)['\"]", match.group(1)))
    return rels


def _bulk_fetch_incoming_refs(conn, model_ids):
    """Pre-fetch incoming references (edges where target ∈ model_ids).

    Replaces the per-model query in :func:`_find_collection_contexts`.
    Returns dict ``{model_id: [ref_row_dict, ...]}``.
    """
    from roam.db.connection import batched_in

    out: dict[int, list[dict]] = {int(mid): [] for mid in model_ids}
    if not model_ids:
        return out
    for r in batched_in(
        conn,
        "SELECT e.target_id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, e.kind as edge_kind "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id IN ({ph})",
        list(model_ids),
    ):
        out.setdefault(int(r["target_id"]), []).append(dict(r))
    return out


def _find_collection_contexts(conn, model_id, model_name, *, bulk_refs=_BULK_NOT_FETCHED):
    """Check if a model is used in collection/pagination contexts.

    Returns list of locations where the model is paginated or collected.
    When ``bulk_refs`` is provided (the pre-fetched incoming-ref list
    for this model from :func:`_bulk_fetch_incoming_refs`), the
    per-model query is skipped.
    """
    contexts = []

    if bulk_refs is _BULK_NOT_FETCHED:
        # Find references to this model class
        refs = conn.execute(
            "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, e.kind as edge_kind "
            "FROM edges e "
            "JOIN symbols s ON e.source_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE e.target_id = ?",
            (model_id,),
        ).fetchall()
    else:
        refs = bulk_refs or []

    for ref in refs:
        path_lower = ref["file_path"].replace("\\", "/").lower()
        # Controller files are collection contexts
        if "controller" in path_lower or "resource" in path_lower:
            contexts.append(
                {
                    "location": loc(ref["file_path"], ref["line_start"]),
                    "type": "controller",
                    "symbol": ref["qualified_name"] or ref["name"],
                }
            )
        # Service files with pagination
        if "service" in path_lower:
            contexts.append(
                {
                    "location": loc(ref["file_path"], ref["line_start"]),
                    "type": "service",
                    "symbol": ref["qualified_name"] or ref["name"],
                }
            )

    return contexts


# ---------------------------------------------------------------------------
# Main analysis: run all detection passes
# ---------------------------------------------------------------------------


def analyze_n1(conn, confidence_filter=None):
    """Run full N+1 implicit I/O analysis.

    Returns list of finding dicts with:
    - model_name, model_location
    - accessor_name, accessor_location
    - appended_attribute
    - relationship_chain (what I/O it triggers)
    - eager_loaded (bool — is this relationship pre-loaded?)
    - collection_contexts (where the model is used in collections)
    - confidence, severity
    - suggestion
    """
    framework = _detect_framework(conn)
    findings = []

    models = _find_model_classes(conn)
    if not models:
        return findings, framework

    # Pre-loop bulk fetches collapse what used to be ~5 N+1 sites (one
    # per helper × N models) into a constant number of batched_in
    # queries. Each helper accepts the pre-fetched data through a
    # keyword arg (``bulk_*``) and falls back to its per-model query
    # when called ad-hoc by other code paths.
    model_ids = list(models.keys())

    # methods (with full location columns — needed by _find_accessor_methods
    # AND for the lighter ``model_method_names`` lookup below)
    methods_by_model = _bulk_fetch_methods_with_locations(conn, model_ids)

    # W86 follow-up (B3.5): pre-fetch every method symbol in any model file
    # so the candidate-filter fallback below can resolve methods by file_id
    # in memory instead of running a per-model file-range SELECT. The
    # parent_id-linked bulk fetch above misses PHP models (parser doesn't
    # set parent_id), so on Laravel apps the fallback used to fire for
    # every model and defeat the surrounding bulk work.
    model_file_ids = [int(mi["file_id"]) for mi in models.values() if mi.get("file_id") is not None]
    # Resolve file_id for any model that arrived without one (defensive —
    # ``_find_model_classes`` selects file_id, but consumers could in
    # principle pass a model dict missing it). Single batched IN query.
    missing_fid_model_ids = [int(mid) for mid, mi in models.items() if mi.get("file_id") is None]
    if missing_fid_model_ids:
        from roam.db.connection import batched_in

        for r in batched_in(
            conn,
            "SELECT id, file_id FROM symbols WHERE id IN ({ph})",
            missing_fid_model_ids,
        ):
            mid = int(r["id"])
            fid = r["file_id"]
            if fid is not None and mid in models:
                models[mid]["file_id"] = int(fid)
                model_file_ids.append(int(fid))
    methods_by_file = _bulk_fetch_methods_by_file(conn, model_file_ids)

    # $appends symbols (replaces _find_appends_properties N+1)
    appends_by_model = _bulk_fetch_appends_symbols(conn, model_ids, models)

    # incoming refs to each model (replaces _find_collection_contexts N+1)
    incoming_refs_by_model = _bulk_fetch_incoming_refs(conn, model_ids)

    # Pre-loop file-cache: ``_find_eager_loads`` reads every Laravel
    # controller (*.php with ``Controller`` in the path) per model.
    # On a 100-model × 50-controller app that's 5000 read_text calls.
    # Build the cache once here; per-model lookups become dict scans.
    controller_cache = _build_controller_cache(conn)

    # B3: bulk fetch every ``$with`` property symbol once instead of
    # one per-model ``LIKE '%{Model}.php'`` SELECT. Key by model id.
    with_sym_by_model = _bulk_fetch_with_symbols(conn, models)

    # B3: read every Laravel resource-config file once instead of
    # per-model. Contents are model-name-independent so a single
    # shared list serves all models.
    resource_config_contents = _build_resource_config_cache(conn)

    # First pass: filter to (model_id, model_info, accessors, model_methods)
    # tuples we'll actually trace. We need this to know all accessor IDs
    # before bulk-fetching their outgoing edges.
    candidates: list[tuple[int, dict, list, list]] = []
    for model_id, model_info in models.items():
        if _is_test_path(model_info["file_path"]):
            continue

        # Get all methods on this model (for relationship detection).
        model_methods = methods_by_model.get(model_id, [])
        if not model_methods:
            # File-range fallback for models without parent_id-linked
            # methods (PHP, primarily). Uses the pre-fetched
            # ``methods_by_file`` map so this stays a constant-cost
            # in-memory filter instead of one SELECT per gap-model.
            fid = model_info.get("file_id")
            line_start = model_info.get("line_start") or 0
            line_end = model_info.get("line_end") or 999999
            if fid is not None:
                model_methods = [
                    m for m in methods_by_file.get(int(fid), []) if line_start <= m["line_start"] <= line_end
                ]
            else:
                # ``model_file_ids`` resolution above should have filled
                # this in; preserve the legacy per-model query as a last
                # resort so we don't silently drop methods.
                row = conn.execute("SELECT file_id FROM symbols WHERE id = ?", (model_id,)).fetchone()
                fallback_fid = row["file_id"] if row else None
                if fallback_fid is not None:
                    model_methods = conn.execute(
                        "SELECT s.id, s.name, s.kind, s.line_start FROM symbols s "
                        "WHERE s.file_id = ? AND s.kind = 'method' "
                        "AND s.line_start >= ? AND s.line_start <= ?",
                        (fallback_fid, line_start, line_end),
                    ).fetchall()

        # Step 1: Find $appends / virtual properties (uses bulk-fetched row)
        appended = _find_appends_properties(
            conn,
            model_id,
            model_info,
            bulk_appends_sym=appends_by_model.get(model_id),
        )
        if not appended:
            continue

        # Step 2: Find accessor methods (uses bulk-fetched methods)
        accessors = _find_accessor_methods(
            conn,
            model_id,
            model_info,
            appended,
            bulk_methods=methods_by_model.get(model_id),
        )
        if not accessors:
            continue

        candidates.append((model_id, model_info, accessors, model_methods))

    # Second pre-fetch: now that we know every accessor we'll trace,
    # bulk-fetch their outgoing edges + sub-edges in two batched_in calls.
    accessor_ids = [a[0]["id"] for _, _, accs, _ in candidates for a in accs]
    callees_by_accessor, sub_callee_names = _bulk_fetch_accessor_edge_traces(conn, accessor_ids)

    for model_id, model_info, accessors, model_methods in candidates:
        model_name = model_info["name"]

        # Step 3: Find what's already eager loaded (uses bulk-fetched
        # ``$with`` symbol + cached resource-config contents)
        eager_loaded = _find_eager_loads(
            conn,
            model_name,
            controller_cache=controller_cache,
            bulk_with_sym=with_sym_by_model.get(model_id),
            resource_config_contents=resource_config_contents,
            model_id=model_id,
        )

        # Step 4: Find collection contexts (uses bulk-fetched refs)
        collection_ctxs = _find_collection_contexts(
            conn,
            model_id,
            model_name,
            bulk_refs=incoming_refs_by_model.get(model_id),
        )

        # Step 5: For each accessor, trace I/O chains (uses bulk edge maps)
        for accessor_info, attr_name in accessors:
            aid = int(accessor_info["id"])
            io_chains = _trace_accessor_io(
                conn,
                aid,
                accessor_info,
                model_methods,
                bulk_callees=callees_by_accessor.get(aid, []),
                bulk_sub_names=sub_callee_names,
            )

            if not io_chains:
                continue

            # Check if the relationships found are eager loaded
            for rel_name, io_type in io_chains:
                is_eager = rel_name in eager_loaded

                if is_eager:
                    continue  # This one is handled, skip it

                # Determine confidence
                confidence = "medium"
                if collection_ctxs:
                    confidence = "high"  # Used in collection context = definitely N+1
                if not collection_ctxs:
                    confidence = "low"  # No collection context found = might be OK

                # Determine severity based on likely query count
                severity = "per-item query on serialization"

                suggestion = _build_suggestion(framework, model_name, rel_name, attr_name, io_type)

                findings.append(
                    {
                        "model_name": model_info["qualified_name"] or model_name,
                        "model_location": loc(model_info["file_path"], model_info["line_start"]),
                        "accessor_name": accessor_info["name"],
                        "accessor_location": loc(accessor_info["file_path"], accessor_info["line_start"]),
                        "appended_attribute": attr_name,
                        "relationship": rel_name,
                        "io_type": io_type,
                        "eager_loaded": False,
                        "confidence": confidence,
                        "severity": severity,
                        "collection_contexts": collection_ctxs[:3],  # Top 3
                        "suggestion": suggestion,
                    }
                )

    # Apply confidence filter
    if confidence_filter:
        findings = [f for f in findings if f["confidence"] == confidence_filter]

    return findings, framework


# R22 — confidence classifier for N+1 findings.
#
# analyze_n1 already assigns a high/medium/low to each finding based on
# whether the model is used in a collection/pagination context (the
# functional analogue of "ORM call inside a loop"). We surface that
# label verbatim in the triple and explain in the reason which signal
# drove it so consumers don't need to re-derive the rule.
#
#   high   — model used in a collection / pagination context → almost
#            certainly N+1 on serialization (loop-depth analogue).
#   medium — no clear collection context detected but the I/O type is a
#            relationship lazy-load (sometimes still problematic).
#   low    — heuristic match without strong supporting signal.
def _n1_classify(finding: dict) -> tuple[str, str]:
    """Map an N+1 finding to a (confidence, reason) tuple."""
    conf = (finding.get("confidence") or "medium").lower()
    if conf not in ("high", "medium", "low"):
        conf = "medium"
    io_type = finding.get("io_type", "?")
    ctxs = finding.get("collection_contexts") or []
    if conf == "high":
        reason = f"used in {len(ctxs)} collection context(s); I/O type {io_type}"
    elif conf == "medium":
        reason = f"I/O type {io_type}; no strong collection-context evidence"
    else:
        reason = f"heuristic match (I/O type {io_type}); manual review needed"
    return conf, reason


def _build_suggestion(framework, model_name, rel_name, attr_name, io_type):
    """Build a framework-specific fix suggestion."""
    if framework == "laravel":
        return (
            f"Add '{rel_name}' to eagerLoad in config/resources.php, "
            f"or add '{rel_name}' to $with on {model_name}, "
            f"or use ::with('{rel_name}') in the controller query"
        )
    if framework == "django":
        return f"Add .select_related('{rel_name}') or .prefetch_related('{rel_name}') to the QuerySet"
    if framework == "rails":
        return f"Add .includes(:{rel_name}) or .eager_load(:{rel_name}) to the ActiveRecord query"
    return f"Pre-load '{rel_name}' data before iterating the collection to avoid per-item I/O"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="n1",
    category="health",
    summary="Detect implicit N+1 I/O patterns in ORM models",
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
@click.command("n1")
@click.option(
    "--confidence",
    "confidence_filter",
    default=None,
    type=click.Choice(["high", "medium", "low"], case_sensitive=False),
    help="Filter by confidence level",
)
@click.option("--limit", "-n", default=30, help="Max findings to show")
@click.option("--verbose", "-v", is_flag=True, help="Show I/O trace chains")
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each N+1 pattern into the central findings registry "
        "(``roam findings list --detector n1``). The detector-specific "
        "output is unchanged; the registry rows are the denormalised "
        "cross-detector surface. Re-running with the same source upserts "
        "in place (no duplicates)."
    ),
)
@click.pass_context
def n1_cmd(ctx, confidence_filter, limit, verbose, persist):
    """Detect implicit N+1 I/O patterns in ORM models.

    Finds computed properties on model classes that trigger database
    queries when the model is serialized in a collection (pagination,
    API responses, etc.). Supports Laravel/Eloquent, Django, Rails,
    SQLAlchemy, and JPA.

    Unlike ``algo`` (which detects explicit I/O-in-loop patterns from AST
    shapes), this command finds implicit N+1 queries hidden inside ORM
    serialization -- e.g. Laravel $appends accessors that trigger lazy-load
    SQL on every item.

    \b
    Examples:
        roam n1                    # Full scan
        roam n1 --confidence high  # Only high-confidence findings
        roam n1 -v                 # Show I/O trace details

    See also ``algo`` (explicit I/O-in-loop AST shapes),
    ``over-fetch`` (overly broad SELECT * patterns), and ``hotspots``
    (runtime evidence to confirm the suspicion).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    ensure_index()

    with open_db(readonly=not persist) as conn:
        findings, framework = analyze_n1(conn, confidence_filter)

        # --- W110: mirror findings into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is the FULL finding
        # list (independent of the --limit display slice) so re-running
        # with a smaller --limit doesn't truncate the registry. The
        # detector-specific output (text / JSON) below is unchanged.
        # Wrapped so a pre-W89 DB (no ``findings`` table) silently
        # no-ops rather than crashing the standard read path.
        if persist:
            try:
                _emit_n1_findings(conn, findings)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # Sort: high first (W596: canonical rank, negated for ascending order).
        findings.sort(key=lambda f: -confidence_level_rank(f["confidence"], fallback=-1))

        # Apply limit
        truncated = len(findings) > limit
        findings = findings[:limit]

        # Confidence counts
        by_confidence = defaultdict(int)
        for f in findings:
            by_confidence[f["confidence"]] += 1

        total = len(findings)
        conf_parts = [f"{by_confidence[c]} {c}" for c in ("high", "medium", "low") if by_confidence.get(c)]
        conf_str = ", ".join(conf_parts) if conf_parts else "none"

        verdict = (
            f"{total} implicit N+1 pattern{'s' if total != 1 else ''} found ({conf_str})"
            if total
            else "No implicit N+1 patterns detected"
        )

        # --- SARIF output (W1208) ---
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1208. The SARIF projection mirrors the
        # displayed slice — `findings` here has already been sorted by
        # confidence (high first) and truncated to `--limit`, so a CI
        # gate sees the same evidence the human / agent sees.
        if sarif_mode:
            from roam.output.sarif import n1_to_sarif, write_sarif

            click.echo(write_sarif(n1_to_sarif(findings)))
            return

        # --- JSON output ---
        if json_mode:
            # R22: wrap each finding in {value, confidence, reason}.
            # Consumers that previously read findings[i]["model_name"]
            # must now read findings[i]["value"]["model_name"] plus
            # findings[i]["confidence"] / findings[i]["reason"].
            finding_triples = wrap_findings(findings, classifier=_n1_classify)
            distribution = confidence_distribution(finding_triples)
            wrapped_verdict = verdict_with_high_count(verdict, distribution)
            click.echo(
                to_json(
                    json_envelope(
                        "n1",
                        summary={
                            "verdict": wrapped_verdict,
                            "total": total,
                            "framework": framework,
                            "by_confidence": dict(by_confidence),
                            "truncated": truncated,
                            "findings_confidence_distribution": distribution,
                        },
                        findings=finding_triples,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        if framework != "generic":
            click.echo(f"Framework: {framework}")
        if not findings:
            return

        click.echo()

        # Group by model
        by_model = defaultdict(list)
        for f in findings:
            by_model[f["model_name"]].append(f)

        for model_name, model_findings in by_model.items():
            model_loc = model_findings[0]["model_location"]
            click.echo(f"{model_name}  {model_loc}")

            for f in model_findings:
                conf = f["confidence"]
                attr = f["appended_attribute"]
                rel = f["relationship"]
                accessor = f["accessor_name"]
                acc_loc = f["accessor_location"]

                click.echo(f"  [{conf}]  ${attr}")
                click.echo(f"        Accessor: {accessor}  {acc_loc}")
                click.echo(f"        Triggers: {rel} ({f['io_type']})")
                click.echo(f"        Fix: {f['suggestion']}")

                if verbose and f["collection_contexts"]:
                    click.echo("        Used in:")
                    for ctx_info in f["collection_contexts"]:
                        click.echo(f"          {ctx_info['type']}: {ctx_info['location']}")

            click.echo()

        if truncated:
            click.echo(f"  (showing {limit} of more findings, use --limit to see more)")
