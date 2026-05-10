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

import os
import re
from collections import defaultdict

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, loc, to_json

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
    p = path.replace("\\", "/").lower()
    base = os.path.basename(p)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    if "tests/" in p or "test/" in p or "__tests__/" in p or "spec/" in p:
        return True
    return False


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


def _find_appends_properties(conn, model_id, model_info):
    """Find $appends array entries for a Laravel model.

    Returns list of appended attribute names (e.g., ['full_name', 'is_admin']).
    Handles both parent_id-linked and flat (same-file line range) symbol structures.
    If default_value is not captured by the parser, reads from source file.
    """
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


def _find_accessor_methods(conn, model_id, model_info, appended_names):
    """Find accessor methods for appended attributes.

    For Laravel: get{StudlyName}Attribute methods.
    Returns list of (accessor_symbol_row, appended_name) tuples.
    Handles both parent_id-linked and flat symbol structures.
    """
    accessors = []

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


def _trace_io_via_edges(conn, accessor_id, model_method_names):
    """Strategy 1: walk outgoing edges from the accessor to look for
    relationship-defining methods or query-builder methods."""
    io_chains: list[tuple[str, str]] = []
    callees = conn.execute(
        "SELECT t.id, t.name, t.qualified_name, t.kind, "
        "f.path as file_path, e.kind as edge_kind "
        "FROM edges e "
        "JOIN symbols t ON e.target_id = t.id "
        "JOIN files f ON t.file_id = f.id "
        "WHERE e.source_id = ?",
        (accessor_id,),
    ).fetchall()

    for callee in callees:
        name = callee["name"]
        if name in model_method_names and callee["kind"] == "method":
            sub_callees = conn.execute(
                "SELECT t.name FROM edges e JOIN symbols t ON e.target_id = t.id WHERE e.source_id = ?",
                (callee["id"],),
            ).fetchall()
            sub_names = {r["name"] for r in sub_callees}
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


def _trace_accessor_io(conn, accessor_id, accessor_info, model_methods):
    """Trace an accessor method to see if it triggers I/O.

    Uses two strategies:
    1. Edge-based: follow outgoing edges to relationship/query methods
    2. Source-based: pattern-match $this->relation in accessor source code
       (needed for PHP where property access doesn't generate edges)

    Returns list of (relationship_name, io_type) tuples found.
    """
    model_method_names = {m["name"] for m in model_methods}
    io_chains = _trace_io_via_edges(conn, accessor_id, model_method_names)
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


def _find_eager_loads(conn, model_name, controller_cache: dict[str, str] | None = None):
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

    Returns set of relationship names that are eager loaded.
    """
    eager_loaded = set()

    from roam.db.connection import find_project_root

    root = find_project_root()

    # --- 1. Check $with property on the model ---
    with_sym = conn.execute(
        "SELECT s.default_value, s.line_start, s.line_end, f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name IN ('with', '$with') AND s.kind = 'property' "
        "AND f.path LIKE ?",
        (f"%{model_name}.php",),
    ).fetchone()

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
    config_files = conn.execute(
        "SELECT f.path FROM files f WHERE f.path LIKE '%config/resources.php'   OR f.path LIKE '%config/resources/%'"
    ).fetchall()

    # Convert model class name to resource key pattern
    # e.g., "Post" → look for Post::class or 'posts' near eagerLoad
    model_lower = model_name.lower()

    for cf in config_files:
        if not root:
            continue
        abs_path = root / cf["path"]
        if not abs_path.is_file():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
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
        except OSError:
            pass

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


def _find_collection_contexts(conn, model_id, model_name):
    """Check if a model is used in collection/pagination contexts.

    Returns list of locations where the model is paginated or collected.
    """
    contexts = []

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

    # Pre-loop bulk fetch: get every method whose parent is one of the
    # model symbols we're about to iterate. The previous per-model query
    # made this an N+1 (1 query per model, flagged by `roam math` running
    # against this file). Batched IN-clause turns N model queries into
    # one. The file-range fallback below stays per-model since it's
    # rarely-triggered (only when a model lacks parent_id-linked methods).
    from roam.db.connection import batched_in

    model_ids = list(models.keys())
    method_rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, s.parent_id FROM symbols s WHERE s.parent_id IN ({ph}) AND s.kind = 'method'",
        model_ids,
    )
    methods_by_model: dict[int, list] = {}
    for row in method_rows:
        methods_by_model.setdefault(int(row["parent_id"]), []).append(row)

    # Pre-loop file-cache: ``_find_eager_loads`` reads every Laravel
    # controller (*.php with ``Controller`` in the path) per model.
    # On a 100-model × 50-controller app that's 5000 read_text calls.
    # Build the cache once here; per-model lookups become dict scans.
    controller_cache = _build_controller_cache(conn)

    for model_id, model_info in models.items():
        if _is_test_path(model_info["file_path"]):
            continue

        model_name = model_info["name"]

        # Get all methods on this model (for relationship detection).
        # Lookup pre-fetched results first; fall back to file-range
        # match for models without parent_id-linked methods.
        model_methods = methods_by_model.get(model_id, [])
        if not model_methods:
            model_methods = conn.execute(
                "SELECT s.id, s.name, s.kind FROM symbols s "
                "WHERE s.file_id = ? AND s.kind = 'method' "
                "AND s.line_start >= ? AND s.line_start <= ?",
                (
                    model_info.get("file_id")
                    or conn.execute("SELECT file_id FROM symbols WHERE id = ?", (model_id,)).fetchone()["file_id"],
                    model_info["line_start"],
                    model_info.get("line_end") or 999999,
                ),
            ).fetchall()

        # Step 1: Find $appends / virtual properties
        appended = _find_appends_properties(conn, model_id, model_info)
        if not appended:
            continue

        # Step 2: Find accessor methods for each appended attribute
        accessors = _find_accessor_methods(conn, model_id, model_info, appended)
        if not accessors:
            continue

        # Step 3: Find what's already eager loaded
        eager_loaded = _find_eager_loads(conn, model_name, controller_cache=controller_cache)

        # Step 4: Find collection contexts
        collection_ctxs = _find_collection_contexts(conn, model_id, model_name)

        # Step 5: For each accessor, trace I/O chains
        for accessor_info, attr_name in accessors:
            io_chains = _trace_accessor_io(conn, accessor_info["id"], accessor_info, model_methods)

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
@click.pass_context
def n1_cmd(ctx, confidence_filter, limit, verbose):
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
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        findings, framework = analyze_n1(conn, confidence_filter)

        # Sort: high first
        _conf_order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: _conf_order.get(f["confidence"], 9))

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

        # --- JSON output ---
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "n1",
                        summary={
                            "verdict": verdict,
                            "total": total,
                            "framework": framework,
                            "by_confidence": dict(by_confidence),
                            "truncated": truncated,
                        },
                        findings=findings,
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
