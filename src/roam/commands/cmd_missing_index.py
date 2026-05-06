"""Detect database queries that filter or sort on columns lacking appropriate indexes.

Detection algorithm:

Step 1 - Extract index definitions from migration files:
  - Find files with 'migration' in their path and language = 'php'
  - Parse index declarations: $table->index([...]), ->unique([...]),
    ->index() chained on column defs, $table->primary(...)
  - Build a dict: table_name -> set of indexed column tuples

Step 2 - Extract query patterns from PHP source files:
  - Parse ->where('column_name', ...) in models/services/controllers
  - Parse ->whereIn('column_name', ...) patterns
  - Parse ->orderBy('column_name', ...) patterns
  - Associate with a table by resolving the model being queried

Step 3 - Cross-reference:
  - For each query column, check if it is covered by an index
  - For chained where patterns, check for a composite index
  - Assign confidence: high (paginated + no index), medium (orderBy or missing
    composite), low (column has a single index but used in multi-column filter)

Confidence levels:
  high   - WHERE on a column with no index at all, in a paginated query
  medium - OrderBy on non-indexed column, or WHERE without composite index
  low    - Column has an individual index but is part of a multi-column filter
"""

from __future__ import annotations

import re
from collections import defaultdict

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Regex patterns for PHP migration and query parsing
# ---------------------------------------------------------------------------

# Match table name from any of:
#   Schema::create('table_name', ...)
#   Schema::table('table_name', ...)
#   Schema::connection('payroll')->create('table_name', ...)
#   Schema::connection('payroll')->table('table_name', ...)
# redacted: the connection-chain form is the canonical Laravel
# multi-tenant migration pattern; without matching it, every per-schema
# index was invisible to the analyser.
_RE_SCHEMA_TABLE = re.compile(
    r"Schema\s*::\s*(?:connection\s*\([^)]*\)\s*->\s*)?(?:create|table)\s*\(\s*['\"]([^'\"]+)['\"]",
)

# M9 / M13 — normalise Laravel multi-tenant table prefixes.
# In tenant-per-schema setups the migration writes:
#   Schema::create("{$schema}.payroll_advances", ...)
# The captured "table" string is "{$schema}.payroll_advances" — but the
# downstream query asks for just "payroll_advances". Without normalisation
# the index map lookup misses every tenant table.
_RE_SCHEMA_PREFIX = re.compile(r"^\{?\$\w+\}?\.")


def _normalise_table_name(raw: str | None) -> str | None:
    """Strip Laravel `{$schema}.` / `$schema.` prefixes from a captured table name.

    Tenant-per-schema migrations interpolate the schema name into the
    table identifier; the analyser should compare on the bare table name
    so multi-tenant indexes don't appear as missing.
    """
    if raw is None:
        return None
    cleaned = _RE_SCHEMA_PREFIX.sub("", raw)
    return cleaned or None


# Match inline chained ->index() on a column definition, e.g.:
#   $table->string('email')->index()
# Captures: column_name
_RE_INLINE_INDEX = re.compile(
    r"\$table\s*->\s*\w+\s*\(\s*['\"]([^'\"]+)['\"][^)]*\)"
    r"(?:\s*->[^;]*?)?"
    r"\s*->\s*index\s*\(\s*\)",
)

# Match $table->primary('col') — single column primary key
_RE_PRIMARY_SINGLE = re.compile(
    r"\$table\s*->\s*primary\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
)

# Match $table->primary(['col1', 'col2']) — composite primary key
_RE_PRIMARY_COMPOSITE = re.compile(
    r"\$table\s*->\s*primary\s*\(\s*\[([^\]]+)\]\s*\)",
)

# Match $table->index('col') — single string argument
_RE_INDEX_SINGLE = re.compile(
    r"\$table\s*->\s*index\s*\(\s*['\"]([^'\"]+)['\"]\s*[,)]",
)

# Match $table->index(['col1','col2',...]) — array argument
_RE_INDEX_ARRAY = re.compile(
    r"\$table\s*->\s*(?:index|unique)\s*\(\s*\[([^\]]+)\]",
)

# Match $table->unique('col') — single string argument
_RE_UNIQUE_SINGLE = re.compile(
    r"\$table\s*->\s*unique\s*\(\s*['\"]([^'\"]+)['\"]\s*[,)]",
)

# Match raw SQL CREATE INDEX ... ON table(col1, col2)
_RE_CREATE_INDEX_RAW = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"\w+\s+ON\s+(?:[\w.${}]+\.)?(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Match ->where('column', ...) — captures column name
_RE_WHERE = re.compile(
    r"->\s*where\s*\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]",
)

# Match ->whereIn('column', ...) — captures column name
_RE_WHERE_IN = re.compile(
    r"->\s*whereIn\s*\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]",
)

# Match ->orderBy('column', ...) — captures column name
_RE_ORDER_BY = re.compile(
    r"->\s*orderBy\s*\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]",
)

# Pagination indicators in the same method/file context
_RE_PAGINATE = re.compile(
    r"->\s*(?:paginate|simplePaginate|cursorPaginate)\s*\(",
)

# Model class reference: SomeModel::query(), SomeModel::where(), new SomeModel
_RE_MODEL_CALL = re.compile(
    r"(?:^|[^a-zA-Z0-9_\\])([A-Z][a-zA-Z0-9_]+)\s*::\s*(?:query|where|orderBy|with|all|find|paginate)",
)

# Scope method: public function scopeSomeName
_RE_SCOPE_METHOD = re.compile(
    r"public\s+function\s+scope([A-Z]\w*)\s*\(",
)

# Class declaration to infer table name
_RE_CLASS_DECL = re.compile(
    r"class\s+([A-Z][a-zA-Z0-9_]+)\s+extends",
)

# Explicit $table = 'table_name' property
_RE_TABLE_PROP = re.compile(
    r"\$table\s*=\s*['\"]([^'\"]+)['\"]",
)

# Method declaration pattern (PHP visibility + function)
_RE_METHOD_DECL = re.compile(
    r"(?:public|protected|private)\s+function\s+(\w+)\s*\(",
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _is_migration_path(path: str) -> bool:
    """Return True if this path looks like a database migration file."""
    p = path.replace("\\", "/").lower()
    return "migration" in p and p.endswith(".php")


def _is_query_source_path(path: str) -> bool:
    """Return True if this path is a model/service/controller/repository."""
    p = path.replace("\\", "/").lower()
    if "migration" in p or "test" in p or "vendor" in p:
        return False
    keywords = ("model", "service", "controller", "repository", "scope", "query")
    return (p.endswith(".php") and any(k in p for k in keywords)) or p.endswith(".php")


def _extract_string_list(raw: str) -> list[str]:
    """Extract all quoted strings from an array-like raw fragment."""
    return [m.group(1) for m in re.finditer(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", raw)]


# M9 — project-wide override index. Populated once per `roam missing-index`
# run by walking all model files and parsing `protected $table = '...'`.
# `_class_to_table` consults this BEFORE applying the snake_case fallback.
# Module-level so the parsing pass can populate it without changing every
# call site's signature.
_MODEL_TABLE_OVERRIDES: dict[str, str] = {}


def _build_model_table_overrides(root, model_paths: list[str]) -> dict[str, str]:
    """Walk model files; build {ClassName: explicit_table_name}.

    redacted: on redacted, models like ``PayrollAdvance`` set
    ``$table = 'payroll_advances'`` (not the Eloquent default
    ``payroll_advances`` from the class name). Without this index, queries
    against ``PayrollAdvance::where(...)`` were attributed to the table
    ``payroll_advances`` (correct accidentally) — but for models named
    ``Advance`` with ``$table = 'payroll_advances'`` the inference was wrong.
    """
    overrides: dict[str, str] = {}
    for rel in model_paths:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Find each class declaration in the file + the nearest $table.
        for class_match in _RE_CLASS_DECL.finditer(content):
            class_name = class_match.group(1)
            # Look forward in the class body for a $table = '...' definition.
            class_body_start = class_match.end()
            # Stop at the next class declaration (multi-class files are rare in PHP)
            next_cls = _RE_CLASS_DECL.search(content, class_body_start)
            class_body_end = next_cls.start() if next_cls else len(content)
            class_body = content[class_body_start:class_body_end]
            tbl_match = _RE_TABLE_PROP.search(class_body)
            if tbl_match:
                overrides[class_name] = tbl_match.group(1)
    return overrides


def _class_to_table(class_name: str) -> str:
    """Convert a StudlyCase class name to a snake_case plural table name.

    M9: consults the cross-file `_MODEL_TABLE_OVERRIDES` index first; falls
    back to snake_case-plural derivation when the model has no explicit
    ``$table`` property.

    Strips common suffixes (Controller, Service, Repository) before converting,
    since these are not model names.

    Examples:
      User              -> users
      BlogPost          -> blog_posts
      Category          -> categories  (y -> ies)
      OrderItem         -> order_items
      ProductController -> products  (strips Controller suffix)
    """
    # M9: explicit override wins.
    if class_name in _MODEL_TABLE_OVERRIDES:
        return _MODEL_TABLE_OVERRIDES[class_name]
    # Strip non-model suffixes before converting
    for suffix in ("Controller", "Service", "Repository", "Scope", "Factory"):
        if class_name.endswith(suffix) and len(class_name) > len(suffix):
            class_name = class_name[: -len(suffix)]
            break

    # Insert underscores before uppercase letters
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", class_name)
    snake = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1).lower()

    # Simple English pluralisation
    if snake.endswith("y") and len(snake) >= 2 and snake[-2] not in "aeiou":
        return snake[:-1] + "ies"
    if snake.endswith("fe"):
        return snake[:-2] + "ves"
    if snake.endswith("f") and not snake.endswith("ff"):
        return snake[:-1] + "ves"
    if (
        snake.endswith("s")
        or snake.endswith("x")
        or snake.endswith("z")
        or snake.endswith("sh")
        or snake.endswith("ch")
    ):
        return snake + "es"
    return snake + "s"


# ---------------------------------------------------------------------------
# Step 1: Parse migration files for index definitions
# ---------------------------------------------------------------------------


def _add_composite(indexes: set[tuple[str, ...]], cols: tuple[str, ...]) -> None:
    """Register a composite index plus each member as a single-col sub-index."""
    if not cols:
        return
    indexes.add(cols)
    for c in cols:
        indexes.add((c,))


def _extract_schema_block_indexes(block: str, indexes: set[tuple[str, ...]]) -> None:
    """Scan one Schema::create/table closure body for the six index shapes."""
    for m in _RE_INDEX_ARRAY.finditer(block):
        _add_composite(indexes, tuple(_extract_string_list(m.group(1))))
    for m in _RE_INDEX_SINGLE.finditer(block):
        indexes.add((m.group(1),))
    for m in _RE_UNIQUE_SINGLE.finditer(block):
        indexes.add((m.group(1),))
    for m in _RE_PRIMARY_SINGLE.finditer(block):
        indexes.add((m.group(1),))
    for m in _RE_PRIMARY_COMPOSITE.finditer(block):
        _add_composite(indexes, tuple(_extract_string_list(m.group(1))))
    for m in _RE_INLINE_INDEX.finditer(block):
        indexes.add((m.group(1),))


def _extract_raw_create_index(
    content: str,
    table_indexes: dict[str, set[tuple[str, ...]]],
) -> None:
    """Pattern 7: raw ``CREATE INDEX ... ON table(...)`` outside Schema blocks."""
    for m in _RE_CREATE_INDEX_RAW.finditer(content):
        tbl = m.group(1).rsplit(".", 1)[-1].strip("{}\"' $")
        cols = tuple(_extract_string_list(m.group(2)))
        if not cols:
            cols = tuple(c.strip() for c in m.group(2).split(",") if c.strip())
        _add_composite(table_indexes[tbl], cols)


def _parse_migration_indexes(root, migration_paths: list[str]) -> dict[str, set[tuple[str, ...]]]:
    """Read migration files and build table -> set-of-indexed-column-tuples.

    Each entry in the returned set is a tuple of column names that share an
    index, e.g.:
      ('id',)                       -- single-column index
      ('user_id', 'created_at')     -- composite index
    """
    table_indexes: dict[str, set[tuple[str, ...]]] = defaultdict(set)

    for rel_path in migration_paths:
        abs_path = root / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        schema_matches = list(_RE_SCHEMA_TABLE.finditer(content))
        for i, sm in enumerate(schema_matches):
            raw_table = sm.group(1)
            table_name = raw_table.rsplit(".", 1)[-1].strip("{}\"' $")
            block_start = sm.end()
            block_end = schema_matches[i + 1].start() if i + 1 < len(schema_matches) else len(content)
            block = content[block_start:block_end]
            # Eloquent tables always have an ``id`` primary key.
            table_indexes[table_name].add(("id",))
            _extract_schema_block_indexes(block, table_indexes[table_name])

        _extract_raw_create_index(content, table_indexes)

    return dict(table_indexes)


# ---------------------------------------------------------------------------
# Step 2: Parse query files for WHERE / ORDER BY patterns
# ---------------------------------------------------------------------------


def _infer_table_from_context(content: str, match_pos: int) -> str | None:
    """Try to infer the table name from surrounding code context.

    Looks backwards from *match_pos* for:
      1. An explicit $table = 'name' property
      2. A class declaration (converts to snake_case plural)
      3. A Model::where('...') call that names a model class

    To avoid cross-model attribution (e.g. attributing a query on Model B
    to Model A because Model A appears earlier in the same file), the search
    window is truncated at the nearest statement boundary (``;``).  We look
    back at most 2000 chars but stop at the last ``;`` that is NOT immediately
    followed by a method-chain continuation (``->``) — this preserves chains
    like ``Model::where(...)->orderBy(...)`` while cutting off prior statements.
    """
    raw_window = content[max(0, match_pos - 2000) : match_pos]

    # Truncate at the nearest statement boundary (`;') that is not followed
    # by a chain continuation ('->').  We scan from the end backwards and
    # stop at the first ';' whose subsequent non-whitespace is NOT '->'.
    truncated_window = raw_window
    search_area = raw_window
    for i in range(len(search_area) - 1, -1, -1):
        if search_area[i] == ";":
            # Check if text after the semicolon starts a chain continuation
            rest = search_area[i + 1 :].lstrip()
            if rest.startswith("->"):
                # This semicolon is inside a chained expression context;
                # keep looking further back.
                continue
            # Found a real statement boundary — use only content after it
            truncated_window = search_area[i + 1 :]
            break

    window = truncated_window

    # Explicit $table property
    m = list(_RE_TABLE_PROP.finditer(window))
    if m:
        return m[-1].group(1)

    # Class name → snake_case table
    m2 = list(_RE_CLASS_DECL.finditer(window))
    if m2:
        return _class_to_table(m2[-1].group(1))

    # Model::query / Model::where calls
    m3 = list(_RE_MODEL_CALL.finditer(window))
    if m3:
        return _class_to_table(m3[-1].group(1))

    # If truncation removed all context, fall back to the full window
    # for class declarations and $table properties (they are file-level).
    if window != raw_window:
        m_full = list(_RE_TABLE_PROP.finditer(raw_window))
        if m_full:
            return m_full[-1].group(1)
        m2_full = list(_RE_CLASS_DECL.finditer(raw_window))
        if m2_full:
            return _class_to_table(m2_full[-1].group(1))

    return None


class _QueryPattern:
    """Represents a detected query pattern in source code."""

    __slots__ = (
        "file_path",
        "line_no",
        "table",
        "where_cols",
        "orderby_cols",
        "has_paginate",
        "kind",
    )

    def __init__(
        self,
        file_path: str,
        line_no: int,
        table: str | None,
        where_cols: list[str],
        orderby_cols: list[str],
        has_paginate: bool,
        kind: str,
    ):
        self.file_path = file_path
        self.line_no = line_no
        self.table = table
        self.where_cols = where_cols
        self.orderby_cols = orderby_cols
        self.has_paginate = has_paginate
        self.kind = kind  # 'scope', 'service', 'controller', 'generic'


def _line_offsets(content: str) -> list[int]:
    """Pre-compute character offsets for the start of each line."""
    offsets = [0]
    for ln in content.splitlines():
        offsets.append(offsets[-1] + len(ln) + 1)
    return offsets


def _line_no_for_pos(line_offsets: list[int], pos: int) -> int:
    """Binary-search the line number (1-based) for a character position."""
    lo, hi = 0, len(line_offsets) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if line_offsets[mid] <= pos < line_offsets[mid + 1]:
            return mid + 1
        if pos < line_offsets[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo + 1


def _extract_brace_body(content: str, start_search_pos: int) -> str | None:
    """From a search position, find the next ``{`` and return the substring
    up to the matching ``}``. Returns None if no opening brace was found."""
    brace_start = content.find("{", start_search_pos)
    if brace_start == -1:
        return None
    depth = 0
    pos = brace_start
    while pos < len(content):
        if content[pos] == "{":
            depth += 1
        elif content[pos] == "}":
            depth -= 1
            if depth == 0:
                break
        pos += 1
    return content[brace_start : pos + 1]


def _file_kind_from_path(rel_path: str) -> str:
    """Bucket the path into a Laravel-ish layer name for confidence scoring."""
    p_lower = rel_path.replace("\\", "/").lower()
    if "service" in p_lower:
        return "service"
    if "controller" in p_lower:
        return "controller"
    if "scope" in p_lower or "model" in p_lower:
        return "model"
    return "generic"


def _query_pattern_from_body(
    body: str,
    rel_path: str,
    line_no: int,
    table: str | None,
    kind: str,
) -> _QueryPattern | None:
    """Scan a function/scope body for WHERE/ORDER BY/paginate patterns.

    Returns a populated ``_QueryPattern`` or None when there's nothing to
    emit.
    """
    where_cols = [m.group(1) for m in _RE_WHERE.finditer(body)]
    where_cols += [m.group(1) for m in _RE_WHERE_IN.finditer(body)]
    orderby_cols = [m.group(1) for m in _RE_ORDER_BY.finditer(body)]
    has_paginate = bool(_RE_PAGINATE.search(body))
    where_cols = [c.split(".")[-1] for c in where_cols]
    orderby_cols = [c.split(".")[-1] for c in orderby_cols]
    if not (where_cols or orderby_cols):
        return None
    return _QueryPattern(
        file_path=rel_path,
        line_no=line_no,
        table=table,
        where_cols=list(dict.fromkeys(where_cols)),
        orderby_cols=list(dict.fromkeys(orderby_cols)),
        has_paginate=has_paginate,
        kind=kind,
    )


def _parse_query_patterns(root, source_paths: list[str]) -> list[_QueryPattern]:
    """Read PHP source files and extract WHERE / ORDER BY patterns with context."""
    patterns: list[_QueryPattern] = []

    for rel_path in source_paths:
        abs_path = root / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        file_kind = _file_kind_from_path(rel_path)
        offsets = _line_offsets(content)

        # Scope methods (Laravel ``scope*`` query helpers).
        for scope_match in _RE_SCOPE_METHOD.finditer(content):
            scope_body = _extract_brace_body(content, scope_match.end())
            if scope_body is None:
                continue
            pat = _query_pattern_from_body(
                scope_body,
                rel_path,
                _line_no_for_pos(offsets, scope_match.start()),
                _infer_table_from_context(content, scope_match.start()),
                "scope",
            )
            if pat is not None:
                patterns.append(pat)

        # Regular method bodies (skip scope methods — already handled above).
        for mm in _RE_METHOD_DECL.finditer(content):
            if mm.group(1).startswith("scope"):
                continue
            body = _extract_brace_body(content, mm.end())
            if body is None:
                continue
            pat = _query_pattern_from_body(
                body,
                rel_path,
                _line_no_for_pos(offsets, mm.start()),
                _infer_table_from_context(content, mm.start()),
                file_kind,
            )
            if pat is not None:
                patterns.append(pat)

    return patterns


# ---------------------------------------------------------------------------
# Step 3: Cross-reference patterns against known indexes
# ---------------------------------------------------------------------------

# Columns that are nearly always indexed or not worth flagging
_SKIP_COLUMNS = frozenset(
    {
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "deleted_at",
    }
)

# Columns typically low-cardinality / not worth indexing individually
_LOW_CARDINALITY_HINTS = frozenset(
    {
        "status",
        "type",
        "flag",
        "active",
        "enabled",
        "is_",
    }
)


def _is_low_cardinality(col: str) -> bool:
    return any(col.startswith(hint) or col == hint for hint in _LOW_CARDINALITY_HINTS)


def _column_has_any_index(col: str, indexed_tuples: set[tuple[str, ...]]) -> bool:
    """Return True if *col* appears in any index tuple for this table."""
    return any(col in tup for tup in indexed_tuples)


def _composite_covered(cols: list[str], indexed_tuples: set[tuple[str, ...]]) -> bool:
    """Return True if there is an index whose columns are a superset of *cols*."""
    col_set = set(cols)
    for tup in indexed_tuples:
        if col_set.issubset(set(tup)):
            return True
    return False


def _check_composite_where(pat, query_cols, indexed, seen) -> dict | None:
    """Multi-column WHERE without a covering composite index."""
    if not (pat.table and not _composite_covered(query_cols, indexed)):
        return None
    dedup_key = ("composite", pat.table, tuple(sorted(query_cols)), pat.file_path)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    missing_individual = [c for c in query_cols if not _column_has_any_index(c, indexed)]
    confidence = "high" if pat.has_paginate else "medium"
    fix_cols = ", ".join(query_cols)
    suggestion = f"Add composite index on ({fix_cols})"
    if missing_individual:
        suggestion += " — also missing individual indexes for: " + ", ".join(missing_individual)
    return {
        "confidence": confidence,
        "table": pat.table,
        "columns": query_cols,
        "issue": f"no composite index covering ({fix_cols})",
        "query_location": loc(pat.file_path, pat.line_no),
        "query_kind": pat.kind,
        "has_paginate": pat.has_paginate,
        "pattern_type": "composite_where",
        "suggestion": suggestion,
        "missing_individual": missing_individual,
    }


def _check_single_where(pat, col, indexed, seen) -> dict | None:
    """Single-column WHERE on an unindexed, non-low-cardinality column."""
    if _is_low_cardinality(col):
        return None
    if not (pat.table and not _column_has_any_index(col, indexed)):
        return None
    dedup_key = ("single_where", pat.table, col, pat.file_path)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    confidence = "high" if pat.has_paginate else "medium"
    return {
        "confidence": confidence,
        "table": pat.table,
        "columns": [col],
        "issue": f"no index on {col}",
        "query_location": loc(pat.file_path, pat.line_no),
        "query_kind": pat.kind,
        "has_paginate": pat.has_paginate,
        "pattern_type": "single_where",
        "suggestion": f"Add index on {col}, or a composite index starting with {col}",
        "missing_individual": [col],
    }


def _check_orderby_unindexed(pat, col, indexed, seen) -> dict | None:
    """ORDER BY on a non-indexed, non-low-cardinality column."""
    if _is_low_cardinality(col):
        return None
    if not (pat.table and not _column_has_any_index(col, indexed)):
        return None
    dedup_key = ("orderby", pat.table, col, pat.file_path)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    confidence = "high" if pat.has_paginate else "medium"
    suggestion = f"Add index on {col}"
    if pat.where_cols:
        suggestion += f", or composite index starting with a filter column + {col}"
    return {
        "confidence": confidence,
        "table": pat.table,
        "columns": [col],
        "issue": f"orderBy on non-indexed column {col}",
        "query_location": loc(pat.file_path, pat.line_no),
        "query_kind": pat.kind,
        "has_paginate": pat.has_paginate,
        "pattern_type": "orderby",
        "suggestion": suggestion,
        "missing_individual": [col],
    }


def _check_orderby_composite(pat, col, indexed, seen) -> dict | None:
    """ORDER BY column has an individual index but no composite covering
    filter+sort columns."""
    if _is_low_cardinality(col):
        return None
    if not (pat.table and _column_has_any_index(col, indexed) and pat.where_cols):
        return None
    non_skip_where = [c for c in pat.where_cols if c not in _SKIP_COLUMNS]
    if not (non_skip_where and not _composite_covered(non_skip_where + [col], indexed)):
        return None
    dedup_key = ("orderby_composite", pat.table, col, pat.file_path)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    fix_cols = ", ".join(non_skip_where + [col])
    return {
        "confidence": "low",
        "table": pat.table,
        "columns": [col],
        "issue": f"{col} has an index but no composite index covering filter+sort ({fix_cols})",
        "query_location": loc(pat.file_path, pat.line_no),
        "query_kind": pat.kind,
        "has_paginate": pat.has_paginate,
        "pattern_type": "orderby_with_where",
        "suggestion": f"Consider a composite index on ({fix_cols})",
        "missing_individual": [],
    }


def _build_findings(
    patterns: list[_QueryPattern],
    table_indexes: dict[str, set[tuple[str, ...]]],
) -> list[dict]:
    """Cross-reference query patterns with known indexes and build finding dicts."""
    findings: list[dict] = []
    seen: set[tuple] = set()

    for pat in patterns:
        table = pat.table
        indexed = table_indexes.get(table, set()) if table else set()

        # WHERE-clause analysis (composite vs single).
        query_cols = [c for c in pat.where_cols if c not in _SKIP_COLUMNS]
        if len(query_cols) >= 2:
            f = _check_composite_where(pat, query_cols, indexed, seen)
            if f:
                findings.append(f)
        elif len(query_cols) == 1:
            f = _check_single_where(pat, query_cols[0], indexed, seen)
            if f:
                findings.append(f)

        # ORDER-BY analysis (per column).
        order_cols = [c for c in pat.orderby_cols if c not in _SKIP_COLUMNS]
        for col in order_cols:
            f = _check_orderby_unindexed(pat, col, indexed, seen) or _check_orderby_composite(pat, col, indexed, seen)
            if f:
                findings.append(f)

    _order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: _order.get(f["confidence"], 9))
    return findings


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("missing-index")
@click.option("--limit", "-n", default=50, help="Max findings to show")
@click.option(
    "--confidence",
    "confidence_filter",
    default=None,
    type=click.Choice(["high", "medium", "low"], case_sensitive=False),
    help="Filter by confidence level",
)
@click.option(
    "--table",
    "table_filter",
    default=None,
    help="Limit results to a specific table name",
)
@click.pass_context
def missing_index_cmd(ctx, limit, confidence_filter, table_filter):
    """Detect queries that filter or sort on columns without indexes.

    Reads migration files to learn which columns are indexed, then scans
    models, services, and controllers for ->where() / ->orderBy() calls on
    columns that have no index. Unlike ``migration-safety`` (which finds
    non-idempotent DDL in migrations) and ``n1`` (which detects ORM N+1
    lazy-load patterns), this command cross-references migration index
    definitions against query patterns to find columns queried without indexes.

    Confidence levels:

    \b
      high   WHERE on unindexed column in a paginated query
      medium orderBy on non-indexed column, or WHERE without composite index
      low    column has an individual index but is used in multi-column filter

    \b
    Examples:
        roam missing-index                      # Full scan (default: 50 results)
        roam missing-index --confidence high    # High-confidence only
        roam missing-index --table orders        # One table
        roam missing-index -n 100              # More results
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    root = find_project_root()

    with open_db(readonly=True) as conn:
        # Fetch all PHP file paths from the index
        all_php = conn.execute("SELECT path FROM files WHERE language = 'php'").fetchall()
        all_php_paths = [r["path"] for r in all_php]

        # Step 1: Separate migration files from query source files
        migration_paths = [p for p in all_php_paths if _is_migration_path(p)]
        source_paths = [
            p for p in all_php_paths if not _is_migration_path(p) and ("vendor" not in p.replace("\\", "/").lower())
        ]

        # M9 — Step 0: build cross-file model→$table override index BEFORE
        # parsing queries, so _class_to_table consults it.
        global _MODEL_TABLE_OVERRIDES
        _MODEL_TABLE_OVERRIDES = _build_model_table_overrides(root, source_paths)

        # Step 2: Parse index definitions
        table_indexes = _parse_migration_indexes(root, migration_paths)
        total_indexes = sum(len(v) for v in table_indexes.values())
        total_tables = len(table_indexes)

        # Step 3: Parse query patterns
        query_patterns = _parse_query_patterns(root, source_paths)

        # Step 4: Cross-reference
        findings = _build_findings(query_patterns, table_indexes)

        # Apply filters
        if confidence_filter:
            findings = [f for f in findings if f["confidence"] == confidence_filter]
        if table_filter:
            findings = [f for f in findings if f.get("table") == table_filter]

        # Count before truncation
        total_findings = len(findings)
        by_confidence: dict[str, int] = defaultdict(int)
        for f in findings:
            by_confidence[f["confidence"]] += 1

        truncated = total_findings > limit
        findings = findings[:limit]

        # Build verdict string
        high_n = by_confidence.get("high", 0)
        medium_n = by_confidence.get("medium", 0)
        low_n = by_confidence.get("low", 0)

        if total_findings == 0:
            verdict = "No missing indexes detected"
        else:
            parts = []
            if high_n:
                parts.append(f"{high_n} high")
            if medium_n:
                parts.append(f"{medium_n} medium")
            if low_n:
                parts.append(f"{low_n} low")
            verdict = (
                f"{total_findings} potential missing index"
                f"{'es' if total_findings != 1 else ''} found"
                f" ({', '.join(parts)})"
            )

        index_summary = f"Indexes found: {total_indexes} across {total_tables} tables"
        migrations_summary = f"Migrations scanned: {len(migration_paths)} | Source files scanned: {len(source_paths)}"

        # --- JSON output ---
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "missing-index",
                        summary={
                            "verdict": verdict,
                            "total": total_findings,
                            "by_confidence": dict(by_confidence),
                            "indexes_found": total_indexes,
                            "tables_with_indexes": total_tables,
                            "migrations_scanned": len(migration_paths),
                            "source_files_scanned": len(source_paths),
                            "truncated": truncated,
                            "confidence_filter": confidence_filter,
                            "table_filter": table_filter,
                        },
                        findings=findings,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"\n{index_summary}")
        click.echo(migrations_summary)

        if not findings:
            return

        click.echo()

        # Group by table for readability
        by_table: dict[str, list[dict]] = defaultdict(list)
        no_table: list[dict] = []
        for f in findings:
            if f.get("table"):
                by_table[f["table"]].append(f)
            else:
                no_table.append(f)

        def _render_finding(f: dict) -> None:
            conf = f["confidence"]
            cols = " + ".join(f["columns"])
            table_str = f"  [{conf}]  {f['table']}.{cols}" if f.get("table") else f"  [{conf}]  {cols}"
            click.echo(table_str)
            click.echo(f"          Issue: {f['issue']}")
            click.echo(f"          Query: {f['query_location']}")
            if f.get("has_paginate"):
                click.echo("          (paginated query — high impact)")
            click.echo(f"          Fix: {f['suggestion']}")

        for table_name in sorted(by_table.keys()):
            table_findings = by_table[table_name]
            click.echo(f"Table: {table_name}")
            for f in table_findings:
                _render_finding(f)
                click.echo()

        for f in no_table:
            _render_finding(f)
            click.echo()

        if truncated:
            click.echo(f"  (showing {limit} of {total_findings} findings — use --limit to see more)")
