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

import hashlib
import json as _json
import re
import sqlite3
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.confidence import (
    confidence_distribution,
    confidence_level_rank,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import json_envelope, loc, to_json

# W111 — missing-index is the fourth detector migrating onto the central
# findings registry (after `clones` (W95), `dead` (W99), and `complexity`
# (W102)). The shape mirrors those — a stable detector version stamp and
# a deterministic ``finding_id_str`` so re-runs upsert instead of
# duplicating rows. Bump this when the confidence tier mapping or the
# pattern_type set changes meaningfully — those are what the registry
# row's confidence / claim are derived from.
MISSING_INDEX_DETECTOR_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# Regex patterns for PHP migration and query parsing
# ---------------------------------------------------------------------------

# Match table name from any of:
#   Schema::create('table_name', ...)
#   Schema::table('table_name', ...)
#   Schema::connection('payroll')->create('table_name', ...)
#   Schema::connection('payroll')->table('table_name', ...)
# the connection-chain form is the canonical Laravel
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

# Match a range/inequality where: ->where('col', '<op>', value) where <op> is
# one of >, <, >=, <=, !=, <> — captures column name. Equality where calls
# `->where('col', value)` or `->where('col', '=', value)` are NOT matched here.
_RE_WHERE_RANGE = re.compile(
    r"->\s*where\s*\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]"
    r"\s*,\s*['\"](?:>=|<=|!=|<>|>|<)['\"]",
)

# Match range-flavoured Eloquent helpers (always treated as range predicates):
#   ->whereBetween('col', ...), ->whereNotBetween('col', ...),
#   ->whereDate('col', ...), ->whereYear/Month/Day/Time('col', ...),
#   ->whereJsonContains('col', ...), ->whereJsonLength('col', ...)
_RE_WHERE_RANGE_HELPER = re.compile(
    r"->\s*(?:whereBetween|whereNotBetween|whereDate|whereDay|whereMonth|whereYear|whereTime"
    r"|whereJsonContains|whereJsonLength)\s*\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]",
)

# Match ->when($x, fn(...) => ...) or ->when($x, function(...) { ... }) —
# captures the start of the closure-arg list so we can scan its body.
_RE_WHEN = re.compile(r"->\s*when\s*\(")

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

    on a Vue 3 + Laravel codebase, models like ``PayrollAdvance`` set
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


# ---------------------------------------------------------------------------
# Predicate classification — distinguishes unconditional / conditional / range
# / sort columns so the composite-index recommendation can order columns by
# index efficacy (leading equality first, then range, then sort).
#
# The 212-eval dogfood showed that the previous regex-find ordering put
# ``->when($var, ...)`` filters BEFORE eager-resolved ``whereIn`` calls in the
# suggested composite, which is wrong: an index can only seek on its leading
# columns when those columns are ALWAYS supplied. Conditional `->when()`
# predicates can be absent at runtime, so they must trail the unconditional
# columns. Range predicates likewise stop the engine from using subsequent
# index columns for further equality seeks, so they trail equality. ORDER BY
# columns sit last so the index can stream the sort.
# ---------------------------------------------------------------------------

# Predicate classification labels (mirror the indexing semantics above).
_PRED_UNCONDITIONAL = "unconditional"  # always-applied equality (or whereIn)
_PRED_CONDITIONAL = "conditional"  # inside a ->when() / if-guarded closure
_PRED_RANGE = "range"  # whereBetween / whereDate / where col > x
_PRED_SORT = "sort"  # orderBy
# Per-classification rank (lower = better leading-column candidate).
_PRED_RANK = {
    _PRED_UNCONDITIONAL: 0,
    _PRED_CONDITIONAL: 1,
    _PRED_RANGE: 2,
    _PRED_SORT: 3,
}


def _find_when_ranges(body: str) -> list[tuple[int, int]]:
    """Return [(start, end), ...] for each ``->when(...)`` closure body.

    Each range covers the inside of the ``->when(`` arg list (from the byte
    after the opening paren to the byte before its matching close paren).
    Any predicate match whose position falls inside one of these ranges is
    classified as *conditional* — its column might not be applied at runtime
    so it cannot anchor a composite index.

    The scanner balances parens (and skips over strings) so nested
    ``->when()`` chains, ternary arguments, and quoted parens don't confuse
    the boundary tracking.
    """
    ranges: list[tuple[int, int]] = []
    for wm in _RE_WHEN.finditer(body):
        # Position of the '(' immediately after 'when'.
        open_pos = body.rfind("(", wm.start(), wm.end())
        if open_pos == -1:
            continue
        depth = 1
        i = open_pos + 1
        n = len(body)
        in_str: str | None = None
        while i < n and depth > 0:
            ch = body[i]
            if in_str is not None:
                # Skip escaped chars; close on matching quote.
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            else:
                if ch == "'" or ch == '"':
                    in_str = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        ranges.append((open_pos + 1, i))
                        break
            i += 1
    return ranges


def _pos_in_any_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """True if ``pos`` falls inside any (start, end) range."""
    return any(start <= pos < end for (start, end) in ranges)


class _ClassifiedPredicate:
    """One predicate (column reference) plus its classification + source pos.

    The position is preserved so the ranker can stably break ties by
    appearance order within the method body — keeps tests predictable and
    makes ``column_ordering`` rationale reproducible.
    """

    __slots__ = ("column", "classification", "pos")

    def __init__(self, column: str, classification: str, pos: int):
        self.column = column
        self.classification = classification
        self.pos = pos


def _classify_predicates(body: str) -> list[_ClassifiedPredicate]:
    """Scan a method body and classify every WHERE / ORDER BY predicate.

    Heuristic (line-based, NOT a full PHP parser):
      1. Find every ``->when(...)`` arg-list range (closure-balanced parens).
      2. For each ``->where``, ``->whereIn``, ``->orderBy``, ``->whereBetween``,
         ``->whereDate``, etc., look up its source position.
      3. If the position is inside any ``->when()`` range → conditional.
      4. Else if it matches a range helper or 3-arg operator-where → range.
      5. Else if it's an orderBy → sort.
      6. Else → unconditional equality (the strongest leading-column signal).

    Edge cases (accept some false positives — the heuristic is strictly
    better than ranking everything as conditional):
      - Nested ``->when()`` chains: outer + inner ranges both register; any
        position inside either is conditional (still correct).
      - Ternary inside a where value: ``where('a', $x ?: $y)`` does not
        appear inside a ``->when()`` range so it stays unconditional — that's
        the correct classification (the predicate IS always applied).
      - Range operator inside a ``->when()``: caught as conditional first
        (when() takes priority — runtime conditionality dominates).
    """
    when_ranges = _find_when_ranges(body)
    preds: list[_ClassifiedPredicate] = []
    consumed: set[int] = set()

    # Range helpers FIRST so a ``->whereBetween`` isn't double-classified
    # as a plain equality where. Operator-where (``->where('col', '>', x)``)
    # next, so it can claim its position before the looser _RE_WHERE.
    _collect_range_helper(body, when_ranges, consumed, preds)
    _collect_range_operator(body, when_ranges, consumed, preds)
    _collect_plain_where(body, when_ranges, consumed, preds)
    _collect_where_in(body, when_ranges, preds)
    _collect_order_by(body, when_ranges, preds)

    return preds


def _collect_range_helper(
    body: str,
    when_ranges: list[tuple[int, int]],
    consumed: set[int],
    preds: list[_ClassifiedPredicate],
) -> None:
    """Append RANGE (or CONDITIONAL) predicates for ``->whereBetween`` etc."""
    for m in _RE_WHERE_RANGE_HELPER.finditer(body):
        pos = m.start()
        consumed.add(pos)
        cls = _PRED_CONDITIONAL if _pos_in_any_range(pos, when_ranges) else _PRED_RANGE
        preds.append(_ClassifiedPredicate(m.group(1).split(".")[-1], cls, pos))


def _collect_range_operator(
    body: str,
    when_ranges: list[tuple[int, int]],
    consumed: set[int],
    preds: list[_ClassifiedPredicate],
) -> None:
    """Append RANGE (or CONDITIONAL) predicates for 3-arg operator-where."""
    for m in _RE_WHERE_RANGE.finditer(body):
        pos = m.start()
        consumed.add(pos)
        cls = _PRED_CONDITIONAL if _pos_in_any_range(pos, when_ranges) else _PRED_RANGE
        preds.append(_ClassifiedPredicate(m.group(1).split(".")[-1], cls, pos))


def _collect_plain_where(
    body: str,
    when_ranges: list[tuple[int, int]],
    consumed: set[int],
    preds: list[_ClassifiedPredicate],
) -> None:
    """Append UNCONDITIONAL (or CONDITIONAL) plain ``->where`` predicates.

    Skips positions already consumed by range helpers / operator-where,
    since _RE_WHERE matches their prefix too.
    """
    for m in _RE_WHERE.finditer(body):
        pos = m.start()
        if pos in consumed:
            continue
        cls = _PRED_CONDITIONAL if _pos_in_any_range(pos, when_ranges) else _PRED_UNCONDITIONAL
        preds.append(_ClassifiedPredicate(m.group(1).split(".")[-1], cls, pos))


def _collect_where_in(
    body: str,
    when_ranges: list[tuple[int, int]],
    preds: list[_ClassifiedPredicate],
) -> None:
    """Append UNCONDITIONAL (or CONDITIONAL) ``->whereIn`` predicates.

    whereIn is eager-resolved equality (the array is materialised at call
    time, so it's ALWAYS applied unless wrapped in ->when()).
    """
    for m in _RE_WHERE_IN.finditer(body):
        pos = m.start()
        cls = _PRED_CONDITIONAL if _pos_in_any_range(pos, when_ranges) else _PRED_UNCONDITIONAL
        preds.append(_ClassifiedPredicate(m.group(1).split(".")[-1], cls, pos))


def _collect_order_by(
    body: str,
    when_ranges: list[tuple[int, int]],
    preds: list[_ClassifiedPredicate],
) -> None:
    """Append SORT (or CONDITIONAL) predicates for ``->orderBy``."""
    for m in _RE_ORDER_BY.finditer(body):
        pos = m.start()
        cls = _PRED_CONDITIONAL if _pos_in_any_range(pos, when_ranges) else _PRED_SORT
        preds.append(_ClassifiedPredicate(m.group(1).split(".")[-1], cls, pos))


def _rank_columns_for_index(
    preds: list[_ClassifiedPredicate],
    keep_cols: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Order columns for a composite index recommendation.

    Returns ``[(column, classification), ...]`` ordered as:
      1. unconditional equality (leading-column candidates)
      2. conditional equality
      3. range predicates (break index efficiency for trailing columns)
      4. sort columns (orderBy)

    Ties within a class are broken by FIRST appearance in source. Duplicates
    are merged on column name, KEEPING THE STRONGEST classification (lowest
    rank wins) — important when the same column is used both inside and
    outside a ``->when()``.

    ``keep_cols`` (optional) restricts the output to columns we want to
    include (e.g. those that aren't already covered by an index).
    """
    # Merge duplicates: column name → (best_rank, classification, first_pos).
    best: dict[str, tuple[int, str, int]] = {}
    for p in preds:
        if keep_cols is not None and p.column not in keep_cols:
            continue
        rank = _PRED_RANK.get(p.classification, 9)
        existing = best.get(p.column)
        if existing is None or rank < existing[0]:
            best[p.column] = (rank, p.classification, p.pos)
        elif rank == existing[0] and p.pos < existing[2]:
            # Same rank, earlier position — keep earliest source position.
            best[p.column] = (rank, p.classification, p.pos)

    ordered = sorted(best.items(), key=lambda item: (item[1][0], item[1][2]))
    return [(col, info[1]) for (col, info) in ordered]


# Human-readable rationale text per classification (used in finding output).
_CLASSIFICATION_RATIONALE = {
    _PRED_UNCONDITIONAL: "unconditional equality (always applied — leading-column candidate)",
    _PRED_CONDITIONAL: "conditional (inside ->when() — may be absent at runtime)",
    _PRED_RANGE: "range predicate (breaks index efficacy for trailing equality)",
    _PRED_SORT: "sort column (orderBy — index can stream the sort)",
}


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
        "predicates",
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
        predicates: list[_ClassifiedPredicate] | None = None,
    ):
        self.file_path = file_path
        self.line_no = line_no
        self.table = table
        self.where_cols = where_cols
        self.orderby_cols = orderby_cols
        self.has_paginate = has_paginate
        self.kind = kind  # 'scope', 'service', 'controller', 'generic'
        self.predicates = predicates or []


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
    emit. Each predicate is classified (unconditional / conditional / range /
    sort) so the composite-index ranker can order columns correctly.
    """
    has_paginate = bool(_RE_PAGINATE.search(body))
    predicates = _classify_predicates(body)
    if not predicates:
        return None

    # Preserve the legacy ``where_cols`` / ``orderby_cols`` shape for the
    # downstream checks that don't yet consume the classified list. We keep
    # the columns in FIRST-APPEARANCE order (sorted by source position) so
    # legacy text rendering stays stable.
    where_pred = [p for p in predicates if p.classification != _PRED_SORT]
    sort_pred = [p for p in predicates if p.classification == _PRED_SORT]
    where_cols = list(dict.fromkeys(p.column for p in sorted(where_pred, key=lambda p: p.pos)))
    orderby_cols = list(dict.fromkeys(p.column for p in sorted(sort_pred, key=lambda p: p.pos)))

    return _QueryPattern(
        file_path=rel_path,
        line_no=line_no,
        table=table,
        where_cols=where_cols,
        orderby_cols=orderby_cols,
        has_paginate=has_paginate,
        kind=kind,
        predicates=predicates,
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
    """Multi-column WHERE (and optional ORDER BY) without a covering composite.

    Column ordering for the suggested composite index is computed from the
    predicate classification on the originating pattern, NOT from the order
    columns happen to appear in regex results. The classes are ranked:

      unconditional equality → conditional equality → range → sort

    This matches B-tree index semantics — the engine can only seek on
    leading equality columns that are guaranteed to be present in every
    invocation. Columns that may be omitted (``->when()``) or that break
    further equality seeks (ranges) must trail.

    Confidence calibration:
      high   — paginated query AND at least one unconditional column is
               present (we are sure the index will be used).
      medium — paginated WITHOUT any unconditional column (less certain),
               or any non-paginated query.
      low    — only used by the orderby_composite path, not here.
    """
    if not pat.table:
        return None

    # ORDER BY columns that aren't already covered by index — fold them into
    # the composite recommendation so it satisfies filter + sort.
    sort_cols_to_consider = [c for c in pat.orderby_cols if c not in _SKIP_COLUMNS and not _is_low_cardinality(c)]
    all_candidate_cols = set(query_cols) | set(sort_cols_to_consider)
    if not all_candidate_cols:
        return None

    if _composite_covered(list(all_candidate_cols), indexed):
        return None

    # Use the classified predicates to rank — strongest leading-column
    # candidates (unconditional) first, sort columns last.
    ranked = _rank_columns_for_index(pat.predicates, keep_cols=all_candidate_cols)
    if not ranked:
        # No classified predicates somehow — fall back to source order.
        ranked = [(c, _PRED_UNCONDITIONAL) for c in query_cols]
        ranked += [(c, _PRED_SORT) for c in sort_cols_to_consider if c not in {col for col, _ in ranked}]

    ordered_cols = [c for (c, _cls) in ranked]
    dedup_key = ("composite", pat.table, tuple(sorted(ordered_cols)), pat.file_path)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)

    missing_individual = [c for c in ordered_cols if not _column_has_any_index(c, indexed)]
    has_unconditional = any(cls == _PRED_UNCONDITIONAL for (_c, cls) in ranked)
    if pat.has_paginate and has_unconditional:
        confidence = "high"
    elif pat.has_paginate:
        confidence = "medium"
    else:
        confidence = "medium"

    fix_cols = ", ".join(ordered_cols)
    suggestion = f"Add composite index on ({fix_cols})"
    if missing_individual:
        suggestion += " — also missing individual indexes for: " + ", ".join(missing_individual)

    # Build per-column rationale so consumers see WHY each column is in its
    # position (external dogfood feedback specifically flagged this gap).
    column_ordering = [
        {
            "column": col,
            "classification": cls,
            "rationale": _CLASSIFICATION_RATIONALE.get(cls, cls),
        }
        for (col, cls) in ranked
    ]
    ranking_explanation = "leading-column priority: " + " > ".join(f"{col} ({cls})" for (col, cls) in ranked)

    return {
        "confidence": confidence,
        "table": pat.table,
        "columns": ordered_cols,
        "issue": f"no composite index covering ({fix_cols})",
        "query_location": loc(pat.file_path, pat.line_no),
        "query_kind": pat.kind,
        "has_paginate": pat.has_paginate,
        "pattern_type": "composite_where",
        "suggestion": suggestion,
        "missing_individual": missing_individual,
        "column_ordering": column_ordering,
        "ranking_explanation": ranking_explanation,
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
    filter+sort columns.

    Composite column ordering uses the predicate classification (the same
    ranking validated against a real Vue/TS dogfood codebase) so the sort
    column trails the equality columns and conditional/range predicates
    are positioned correctly within the index.
    """
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

    # Rank with classification — sort columns will naturally trail.
    keep = set(non_skip_where) | {col}
    ranked = _rank_columns_for_index(pat.predicates, keep_cols=keep)
    if not ranked:
        ranked = [(c, _PRED_UNCONDITIONAL) for c in non_skip_where] + [(col, _PRED_SORT)]
    ordered_cols = [c for (c, _cls) in ranked]
    fix_cols = ", ".join(ordered_cols)

    column_ordering = [
        {
            "column": c,
            "classification": cls,
            "rationale": _CLASSIFICATION_RATIONALE.get(cls, cls),
        }
        for (c, cls) in ranked
    ]
    ranking_explanation = "leading-column priority: " + " > ".join(f"{c} ({cls})" for (c, cls) in ranked)

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
        "column_ordering": column_ordering,
        "ranking_explanation": ranking_explanation,
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
        # The composite path folds in trailing orderBy columns so the
        # suggestion is a single index that satisfies filter + sort — see
        # the Pattern 2 case from external dogfood feedback. Track which
        # orderBy columns got folded so the per-column orderby analysis
        # below doesn't double-fire.
        query_cols = [c for c in pat.where_cols if c not in _SKIP_COLUMNS]
        sort_cols_folded: set[str] = set()
        if len(query_cols) >= 2:
            f = _check_composite_where(pat, query_cols, indexed, seen)
            if f:
                findings.append(f)
                sort_cols_folded = {c for c in f.get("columns", []) if c in pat.orderby_cols}
        elif len(query_cols) == 1:
            f = _check_single_where(pat, query_cols[0], indexed, seen)
            if f:
                findings.append(f)

        # ORDER-BY analysis (per column). Skip any column that was already
        # rolled into the composite_where finding above.
        order_cols = [c for c in pat.orderby_cols if c not in _SKIP_COLUMNS and c not in sort_cols_folded]
        for col in order_cols:
            f = _check_orderby_unindexed(pat, col, indexed, seen) or _check_orderby_composite(pat, col, indexed, seen)
            if f:
                findings.append(f)

    # W596: canonical confidence-LEVEL rank — negate for high-first sort.
    findings.sort(key=lambda f: -confidence_level_rank(f["confidence"], fallback=-1))
    return findings


# ---------------------------------------------------------------------------
# R22 — confidence classifier for missing-index findings.
#
# The analyser already labels each finding with a high/medium/low based
# on pattern unambiguity:
#   high   — WHERE on a known unindexed column in a paginated query
#            (unambiguous: paginated → bounded result set means filtering
#            is happening; missing index → guaranteed table scan).
#   medium — JOIN/ORDER BY without composite, or WHERE without paginate
#            (recognisable pattern but slightly weaker signal).
#   low    — heuristic / composite-index nuance where the column already
#            has *an* index just not the optimal one.
#
# We re-use the existing label and surface the specific pattern_type so
# consumers know WHY it landed in each bucket without re-deriving.
# ---------------------------------------------------------------------------


def _missing_index_classify(finding: dict) -> tuple[str, str]:
    """Map a missing-index finding to a (confidence, reason) tuple."""
    conf = (finding.get("confidence") or "medium").lower()
    if conf not in ("high", "medium", "low"):
        conf = "medium"
    pattern_type = finding.get("pattern_type", "?")
    paginate = finding.get("has_paginate")
    if conf == "high":
        reason = f"{pattern_type} on a paginated query (unambiguous missing index)"
    elif conf == "medium":
        if paginate:
            reason = f"{pattern_type} on a paginated query without composite coverage"
        else:
            reason = f"{pattern_type} pattern recognised; not paginated"
    else:
        reason = f"{pattern_type} — column has an index but not the optimal composite"
    return conf, reason


# ---------------------------------------------------------------------------
# W111 — emit missing-index findings into the central findings registry.
#
# Confidence tier mapping (mirrors the dead-detector W99 pattern of
# mapping per-kind verdicts onto W90 registry tiers):
#
#   detector confidence "high"   → CONFIDENCE_STATIC_ANALYSIS
#       Paginated query on an unindexed column with an unconditional
#       equality predicate. The detector cross-references the migration
#       file's index map against a regex-classified WHERE/whereIn that is
#       ALWAYS applied — strongest signal it can produce.
#
#   detector confidence "medium" → CONFIDENCE_STRUCTURAL
#       Recognised pattern (orderBy on non-indexed column, or paginated
#       WHERE without an unconditional predicate). The structural shape
#       is intact but the indexing claim is less certain.
#
#   detector confidence "low"    → CONFIDENCE_HEURISTIC
#       Column has SOME index but not the optimal composite (orderby_composite
#       path). Purely a "you could do better" heuristic — no proof of
#       a missing seek-key.
#
# This mapping is deterministic on the detector's pre-computed
# ``confidence`` field, so the emitter doesn't re-derive — it just
# translates the vocabulary.
# ---------------------------------------------------------------------------


def _missing_index_confidence_tier(detector_confidence: str) -> str:
    """Map the detector's high/medium/low to a W90 registry confidence tier."""
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_STRUCTURAL,
    )

    conf = (detector_confidence or "medium").lower()
    if conf == "high":
        return CONFIDENCE_STATIC_ANALYSIS
    if conf == "medium":
        return CONFIDENCE_STRUCTURAL
    # "low" or unknown → pure heuristic ("col has an index, just not the optimal one").
    return CONFIDENCE_HEURISTIC


def _missing_index_finding_id(
    table: str | None,
    columns: tuple[str, ...],
    pattern_type: str,
    file_path: str,
    line_no: int,
) -> str:
    """Stable, deterministic finding id for one missing-index finding.

    Re-identification key:
      (table, sorted-columns, pattern_type, file_path, line_no)

    ``line_no`` is included so the same query-shape appearing on two
    different lines in the same file is two findings, not one. Columns
    are sorted so a regex order-jitter doesn't mint a fresh id (the
    column_ordering rationale is on the evidence_json, not the id).

    Re-running ``roam missing-index --persist`` on the same input
    upserts the existing row rather than duplicating.
    """
    cols_part = ",".join(sorted(columns or ()))
    raw = f"{table or ''}:{cols_part}:{pattern_type}:{file_path}:{line_no}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"missing-index:query:{digest}"


def _parse_query_location(query_location: str) -> tuple[str, int]:
    """Split a ``file:line`` location string into (path, line_no).

    The ``loc()`` formatter renders ``path:line`` (or ``path`` when there's
    no line); fall back to (raw, 0) when no colon is found. The line
    number is used in the finding_id and only needs to be deterministic —
    we don't require it to be authoritative.
    """
    if not query_location:
        return "", 0
    # Use rsplit so Windows ``C:\foo:42`` still gives us the trailing line.
    parts = query_location.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], int(parts[1])
    return query_location, 0


def _emit_missing_index_findings(
    conn,
    findings_unfiltered: list[dict],
) -> None:
    """Mirror each missing-index finding into the central findings registry.

    ``findings_unfiltered`` is the full ``_build_findings`` result BEFORE
    confidence/table filters are applied — re-running with a narrower
    --confidence filter must not truncate what lands in the registry,
    same discipline as W102 complexity persist.

    ``subject_kind`` is ``"file"`` (resolved to a ``files.id`` when the
    query location maps to an indexed file). The detector emits at
    query-pattern granularity, so there's no natural ``symbols.id`` to
    point at — file-level grounding is the strongest subject anchor
    available without a real PHP AST.

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard missing-index command.
    """
    # Local import to keep the cost out of the readonly path — callers
    # without --persist never reach here, so the import only runs when
    # we're actually writing.
    from roam.db.findings import FindingRecord, emit_finding

    for f in findings_unfiltered:
        detector_conf = f.get("confidence") or "medium"
        table = f.get("table")
        columns = tuple(f.get("columns") or ())
        pattern_type = f.get("pattern_type") or "unknown"
        query_location = f.get("query_location") or ""
        file_path, line_no = _parse_query_location(query_location)

        # Resolve subject_id to a files.id when the path matches an indexed
        # file. Not every finding has a resolvable file (e.g. when the
        # path normalisation differs from what the indexer recorded), so
        # the lookup is best-effort.
        subject_id: int | None = None
        if file_path:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
            if row is not None:
                subject_id = int(row[0])

        finding_id = _missing_index_finding_id(table, columns, pattern_type, file_path, line_no)

        # Keep the evidence payload small (< 4 KB per the W90 contract).
        # Drop verbose nested rationale dicts if present — the consumer
        # can re-derive them by re-running the detector.
        evidence = {
            "table": table,
            "columns": list(columns),
            "issue": f.get("issue"),
            "query_location": query_location,
            "query_kind": f.get("query_kind"),
            "has_paginate": bool(f.get("has_paginate")),
            "pattern_type": pattern_type,
            "suggestion": f.get("suggestion"),
            "missing_individual": f.get("missing_individual") or [],
            "detector_confidence": detector_conf,
        }
        # Per-column ordering rationale is only present on composite findings;
        # keep it for composite kinds since it's the actionable "WHY this column
        # order" payload that the W36.3 wave added.
        if f.get("column_ordering"):
            evidence["column_ordering"] = f["column_ordering"]

        cols_part = " + ".join(columns) if columns else "?"
        claim = (
            f"Missing index ({pattern_type}) on "
            f"{(table or '?')}.{cols_part} at {query_location} — "
            f"{f.get('suggestion') or 'no suggestion'}"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="file",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=_missing_index_confidence_tier(detector_conf),
                source_detector="missing-index",
                source_version=MISSING_INDEX_DETECTOR_VERSION,
            ),
        )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="missing-index",
    category="health",
    summary="Detect queries that filter or sort on columns without indexes",
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
@click.command("missing-index")
@click.option("--limit", "-n", default=50, help="Max findings to show")
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
    # Semantic change: equality → floor (pre-fix kept findings with EXACTLY
    # that confidence; post-fix keeps findings AT OR ABOVE that rank).
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
    "--table",
    "table_filter",
    default=None,
    help="Limit results to a specific table name",
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror missing-index findings into the central findings registry — "
        "visible via ``roam findings list --detector missing-index``. The "
        "detector-specific output (text / JSON) is unchanged; the registry "
        "rows are the denormalised cross-detector surface. Persisted rows "
        "ignore --limit / --confidence / --table display filters so re-running "
        "with a narrower view doesn't truncate the registry."
    ),
)
@click.pass_context
def missing_index_cmd(ctx, limit, confidence_filter, table_filter, persist):
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
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    ensure_index()

    root = find_project_root()

    # W607-CI -- substrate-boundary plumbing for cmd_missing_index.
    # ``_run_check_ci`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607ci_warnings_out`` rather than
    # crashing the missing-index detector outright (W111 foundational
    # detector; W807 sealed the empty-corpus smoke with explicit
    # ``no_migrations`` state but did NOT install substrate isolation
    # -- this wave adds it). Marker family
    # ``missing_index_<phase>_failed:<exc_class>:<detail>``. Substrates
    # wrapped:
    #
    #   * parse_migration_indexes   -- Step 2 index definitions
    #   * parse_query_patterns      -- Step 3 query enumeration
    #   * build_model_table_overrides -- M9 cross-file override index
    #   * build_findings            -- Step 4 cross-reference +
    #                                  W18.4 unconditional-predicate
    #                                  flagging + W36.3 ordering
    #   * apply_confidence_filter   -- W1005-followup-D floor
    #   * apply_table_filter        -- --table display filter
    #   * aggregate_by_confidence   -- histogram
    #   * emit_findings             -- W111 findings-registry mirror
    #                                  (sqlite3.OperationalError silent
    #                                  no-op preserved for pre-W89 DB)
    #   * serialize_to_sarif        -- SARIF projection
    _w607ci_warnings_out: list[str] = []

    def _run_check_ci(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CI marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``missing_index_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ci_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ci_warnings_out.append(f"missing_index_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DX -- additive aggregation-phase plumbing for cmd_missing_index.
    # Layered ON TOP of the W607-CI substrate-CALL plumbing above; both
    # share the canonical ``missing_index_*`` marker family and the
    # ``missing_index_<phase>_failed:<exc_class>:<detail>`` shape contract.
    # The two bucket sources are merged at envelope-emit time into the
    # single ``warnings_out`` mirror so consumers see the full degradation
    # lineage. Aggregation phases wrapped (sibling pattern to cmd_n1's
    # W607-DQ + cmd_over_fetch's W607-DT, closing the ORM-detector 3-way
    # at the aggregation layer):
    #
    #   * score_classify     -- buckets the run by missing_index_count into
    #                          NO_MISSING_INDEX / MI_LIGHT / MI_MODERATE /
    #                          MI_HEAVY / DEGRADED
    #   * compute_predicate  -- rollup metrics dict (total_count, by_kind
    #                          (unconditional_predicate / unbounded_join /
    #                          etc.), files_affected, hottest_models)
    #   * compute_verdict    -- single-line verdict string (LAW 6 floor:
    #                          "missing_index completed")
    #   * serialize_envelope -- json_envelope("missing-index", ...) projection
    #
    # The 4 aggregation phase names DO NOT collide with the 9 W607-CI
    # substrate phase names (parse_migration_indexes / parse_query_patterns
    # / build_model_table_overrides / build_findings / apply_confidence_filter
    # / apply_table_filter / aggregate_by_confidence / emit_findings /
    # serialize_to_sarif).
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_dx(...)`` call:
    #   1. f-string verdict floor: NEVER re-interpolate the same values
    #      that tripped the closure inside the ``default=`` floor.
    #   2. kwarg-default eagerness: ``default=`` must be a literal
    #      constant, never a computed expression.
    #   3. json.dumps(default=str) sentinel: the serialize_envelope
    #      floor must be JSON-serializable with the standard encoder.
    #   4. phase-name collision: verified above against CI's 9 phases.
    #   5. len() at kwarg-bind: move len() INSIDE the closure, never at
    #      the ``_run_check_dx(...)`` call site.
    #   6. unguarded len()/if on poisoned object: the floor MUST be a
    #      concrete dict/str/None, never a sentinel that may
    #      __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    _w607dx_warnings_out: list[str] = []

    def _run_check_dx(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DX marker emission.

        Mirror of ``_run_check_ci`` shape (same
        ``missing_index_<phase>_failed:`` marker family) but writes into
        ``_w607dx_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits. W607-DW finding pin: the
        return statement is verbatim ``return default`` (NOT
        ``default if default is not None else {}``) so the floor is a
        literal pass-through.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dx_warnings_out.append(f"missing_index_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
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
        # W607-CI: ``build_model_table_overrides`` substrate -- raise in
        # the cross-file walker degrades to {} so per-class snake_case
        # fallback still runs.
        global _MODEL_TABLE_OVERRIDES
        _MODEL_TABLE_OVERRIDES = _run_check_ci(
            "build_model_table_overrides",
            _build_model_table_overrides,
            root,
            source_paths,
            default={},
        )
        if _MODEL_TABLE_OVERRIDES is None:
            _MODEL_TABLE_OVERRIDES = {}

        # Step 2: Parse index definitions
        # W607-CI: ``parse_migration_indexes`` substrate -- raise in
        # migration regex parsing degrades to {} so the empty-state
        # ``no_migrations`` branch still composes cleanly.
        table_indexes = _run_check_ci(
            "parse_migration_indexes",
            _parse_migration_indexes,
            root,
            migration_paths,
            default={},
        )
        if table_indexes is None:
            table_indexes = {}
        total_indexes = sum(len(v) for v in table_indexes.values())
        total_tables = len(table_indexes)

        # Step 3: Parse query patterns
        # W607-CI: ``parse_query_patterns`` substrate -- raise in the
        # PHP source-file parser (W18.4 unconditional-predicate
        # classification lives here) degrades to [] so the empty-state
        # envelope still composes.
        query_patterns = _run_check_ci(
            "parse_query_patterns",
            _parse_query_patterns,
            root,
            source_paths,
            default=[],
        )
        if query_patterns is None:
            query_patterns = []

        # Step 4: Cross-reference
        # W607-CI: ``build_findings`` substrate -- the cross-reference
        # phase that applies W18.4 unconditional-predicate detection +
        # W36.3 unconditional-first ordering. A raise here degrades to
        # [] so the rest of the envelope (counts, verdict, registry
        # mirror) still composes.
        findings = _run_check_ci(
            "build_findings",
            _build_findings,
            query_patterns,
            table_indexes,
            default=[],
        )
        if findings is None:
            findings = []

        # W111 — mirror findings into the central registry BEFORE applying
        # display filters. Re-running with --confidence high must not
        # truncate the persisted set — the registry documents what the
        # detector actually found, not what the current invocation chose
        # to render. Matches the W102 complexity --persist discipline.
        # W607-CI: ``emit_findings`` substrate boundary. The pre-W89
        # schema path (sqlite3.OperationalError on missing ``findings``
        # table) is the EXPECTED degraded path -- the try/except below
        # maintains the W111 silent no-op contract for that case.
        # Generic exceptions surface via the
        # ``missing_index_emit_findings_failed:<exc>:<detail>`` marker.
        if persist:
            try:
                _emit_missing_index_findings(conn, findings)
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_missing_index:emit_findings", _exc)
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-CI disclosure
                _w607ci_warnings_out.append(
                    f"missing_index_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}"
                )

        # Apply filters — W1005-followup-D: equality → floor via canonical
        # severity_rank(). Detector emits {high, medium, low}; the Click
        # Choice accepts the full W547 7-tier. Floor keeps a finding when
        # ``severity_rank(f.confidence) >= severity_rank(confidence_filter)``.
        # W607-CI: ``apply_confidence_filter`` substrate -- raise in
        # severity_rank() degrades to the unfiltered list so a single
        # bad confidence string doesn't wipe the findings list.
        if confidence_filter:

            def _apply_confidence_filter():
                _floor_rank = severity_rank(confidence_filter)
                return [f for f in findings if severity_rank(f["confidence"]) >= _floor_rank]

            _filtered = _run_check_ci(
                "apply_confidence_filter",
                _apply_confidence_filter,
                default=findings,
            )
            if _filtered is not None:
                findings = _filtered
        # W607-CI: ``apply_table_filter`` substrate -- raise in the
        # comprehension (e.g. malformed finding dict) degrades to the
        # unfiltered list.
        if table_filter:

            def _apply_table_filter():
                return [f for f in findings if f.get("table") == table_filter]

            _filtered = _run_check_ci(
                "apply_table_filter",
                _apply_table_filter,
                default=findings,
            )
            if _filtered is not None:
                findings = _filtered

        # Count before truncation
        total_findings = len(findings)

        # W607-CI: ``aggregate_by_confidence`` substrate -- histogram
        # construction. Degrades to an empty defaultdict on raise so
        # the verdict composer still produces a coherent string.
        def _aggregate_by_confidence():
            agg: defaultdict[str, int] = defaultdict(int)
            for f in findings:
                agg[f["confidence"]] += 1
            return agg

        by_confidence = _run_check_ci(
            "aggregate_by_confidence",
            _aggregate_by_confidence,
            default=defaultdict(int),
        )
        if by_confidence is None:
            by_confidence = defaultdict(int)

        truncated = total_findings > limit
        findings = findings[:limit]

        # Build verdict string
        high_n = by_confidence.get("high", 0)
        medium_n = by_confidence.get("medium", 0)
        low_n = by_confidence.get("low", 0)

        # Fix E (Pattern 2: silent fallbacks) — distinguish "0 missing
        # indexes found after scanning N migrations" from "0 migrations
        # scanned" (no migration files at all). The previous code reported
        # "No missing indexes detected" in BOTH cases, which silently hid
        # the no-input scenario from consumers.
        state = "scanned"
        partial_success = False
        if len(migration_paths) == 0:
            state = "no_migrations"
            partial_success = True
            verdict_floor_str = (
                "no migrations scanned (no PHP migration files found; "
                "missing-index detection requires Laravel-style migration files)"
            )
        elif total_findings == 0:
            verdict_floor_str = "No missing indexes detected"
        else:
            parts = []
            if high_n:
                parts.append(f"{high_n} high")
            if medium_n:
                parts.append(f"{medium_n} medium")
            if low_n:
                parts.append(f"{low_n} low")
            verdict_floor_str = (
                f"{total_findings} potential missing index"
                f"{'es' if total_findings != 1 else ''} found"
                f" ({', '.join(parts)})"
            )

        # W607-DX -- score_classify boundary. Buckets the run by total
        # missing-index findings into a state label:
        #   * NO_MISSING_INDEX -- total_findings == 0
        #   * MI_LIGHT         -- 0 < total_findings <= 3
        #   * MI_MODERATE      -- 3 < total_findings <= 10
        #   * MI_HEAVY         -- total_findings > 10
        #   * DEGRADED         -- floor on raise
        # W978 5th-discipline: ``total_findings`` passed as a raw int;
        # counting / iteration lives INSIDE the closure (no len() at
        # kwarg-bind).
        def _score_classify_run(_total):
            if _total == 0:
                _state_label = "NO_MISSING_INDEX"
            elif _total <= 3:
                _state_label = "MI_LIGHT"
            elif _total <= 10:
                _state_label = "MI_MODERATE"
            else:
                _state_label = "MI_HEAVY"
            return {"state": _state_label, "scanned": _total}

        _score_dict = _run_check_dx(
            "score_classify",
            _score_classify_run,
            total_findings,
            default={"state": "DEGRADED", "scanned": 0},
        )

        # W607-DX -- compute_predicate boundary. Rollup metrics dict
        # surfacing aggregate dimensions (total_count / by_kind /
        # files_affected / hottest_models) so a downstream refactor of
        # the rollup logic surfaces a marker rather than crashing. W978
        # 5th-discipline: ``findings`` passed as a raw arg; counting /
        # iteration lives INSIDE the closure.
        def _compute_predicate_fields(_findings):
            _by_kind: defaultdict[str, int] = defaultdict(int)
            _files: set[str] = set()
            _model_counts: defaultdict[str, int] = defaultdict(int)
            for _f in _findings:
                # Missing-index findings carry an ``issue`` slug (e.g.
                # ``unconditional_predicate`` / ``unbounded_join``) on
                # the W18.4 + W36.3 classification path. Fall back to a
                # bucket-collapsed key when absent.
                _k = _f.get("issue") or _f.get("reason") or "missing_index"
                _by_kind[_k] += 1
                _loc = _f.get("query_location") or ""
                if _loc:
                    _file = _loc.rsplit(":", 1)[0] if ":" in _loc else _loc
                    if _file:
                        _files.add(_file)
                _table = _f.get("table") or ""
                if _table:
                    _model_counts[_table] += 1
            # hottest_models: top 3 (table, count) tuples by count desc
            _hottest = sorted(_model_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            return {
                "total_count": len(_findings),
                "by_kind": dict(_by_kind),
                "files_affected": len(_files),
                "hottest_models": [{"table": _t, "count": _c} for _t, _c in _hottest],
            }

        _pred_fields = _run_check_dx(
            "compute_predicate",
            _compute_predicate_fields,
            findings,
            default={
                "total_count": 0,
                "by_kind": {},
                "files_affected": 0,
                "hottest_models": [],
            },
        )

        # W607-DX -- compute_verdict boundary. Wraps the verdict string
        # assembly so a downstream f-string refactor surfaces a marker
        # rather than crashing the envelope. Literal "missing_index
        # completed" floor (LAW 6 still holds: the line works
        # standalone).
        #
        # W978 1st-discipline: the floor MUST NOT re-interpolate the
        # same values that tripped the closure. W978 2nd-discipline:
        # ``default=`` is a literal constant.
        def _build_verdict_str(_verdict_floor):
            return _verdict_floor

        verdict = _run_check_dx(
            "compute_verdict",
            _build_verdict_str,
            verdict_floor_str,
            default="missing_index completed",
        )

        index_summary = f"Indexes found: {total_indexes} across {total_tables} tables"
        migrations_summary = f"Migrations scanned: {len(migration_paths)} | Source files scanned: {len(source_paths)}"

        # --- SARIF output (W1217) ---
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1217. The SARIF projection mirrors
        # the displayed slice — `findings` here has already been
        # filtered (--confidence / --table) and truncated to --limit,
        # so a CI gate sees the same evidence the human / agent sees.
        if sarif_mode:
            # W607-CI: SARIF projection substrate -- a raise in the
            # SARIF writer used to crash the missing-index command on
            # the CI integration path; now degrades silently to None
            # with a marker, and the function returns early (matches
            # pre-W607-CI semantics that SARIF mode short-circuits).
            def _emit_sarif():
                from roam.output.sarif import missing_index_to_sarif, write_sarif

                click.echo(write_sarif(missing_index_to_sarif(findings)))

            _run_check_ci("serialize_to_sarif", _emit_sarif, default=None)
            return

        # --- JSON output ---
        if json_mode:
            # R22: wrap each finding in {value, confidence, reason}.
            # Consumers that previously read findings[i]["table"] must
            # now read findings[i]["value"]["table"] plus
            # findings[i]["confidence"] / findings[i]["reason"].
            finding_triples = wrap_findings(findings, classifier=_missing_index_classify)
            distribution = confidence_distribution(finding_triples)
            wrapped_verdict = verdict_with_high_count(verdict, distribution)
            # W607-CI + W607-DX: any substrate-CALL OR aggregation-phase
            # marker flips partial_success: True so a degraded envelope
            # is NOT mistaken for a clean "no missing indexes" verdict
            # (Pattern-2 silent-fallback guard). The pre-W607-CI
            # ``no_migrations`` partial_success semantic is preserved
            # on the happy path.
            _combined_warnings = list(_w607ci_warnings_out) + list(_w607dx_warnings_out)
            partial_success_w607ci = partial_success or bool(_combined_warnings)
            summary_block = {
                "verdict": wrapped_verdict,
                "state": state,
                "partial_success": partial_success_w607ci,
                "total": total_findings,
                "by_confidence": dict(by_confidence),
                "indexes_found": total_indexes,
                "tables_with_indexes": total_tables,
                "migrations_scanned": len(migration_paths),
                "source_files_scanned": len(source_paths),
                "truncated": truncated,
                "confidence_filter": confidence_filter,
                "table_filter": table_filter,
                "findings_confidence_distribution": distribution,
                # W607-DX: surface score_classify result on the envelope
                # so consumers can read the run state without re-deriving
                # from raw counts. W978 7th-discipline anchor: bare
                # ``_score_dict["state"]`` lookup (floor dict guarantees
                # the key) -- NOT ``.get("state", expensive_default)``.
                "run_state": _score_dict["state"],
                # W607-DX: surface compute_predicate rollup on the
                # envelope so consumers can read the aggregate
                # dimensions without rebuilding from the raw list.
                # W978 7th-discipline anchor: bare key lookups.
                "by_kind": _pred_fields["by_kind"],
                "files_affected": _pred_fields["files_affected"],
                "hottest_models": _pred_fields["hottest_models"],
            }
            envelope_kwargs: dict = {
                "summary": summary_block,
                "findings": finding_triples,
            }
            # W607-CI + W607-DX: mirror combined substrate-CALL +
            # aggregation-phase markers into BOTH the top-level envelope
            # ``warnings_out`` AND ``summary.warnings_out`` so MCP
            # consumers see disclosure regardless of which surface they
            # read.
            if _combined_warnings:
                summary_block["warnings_out"] = list(_combined_warnings)
                envelope_kwargs["warnings_out"] = list(_combined_warnings)

            # W607-DX -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("missing-index", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. W978
            # 6th-discipline: floor is a concrete dict, not a sentinel
            # that may __len__-raise downstream.
            _envelope_floor: dict = {
                "command": "missing-index",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": wrapped_verdict,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings),
                },
                "warnings_out": list(_combined_warnings),
            }
            envelope = _run_check_dx(
                "serialize_envelope",
                json_envelope,
                "missing-index",
                default=_envelope_floor,
                **envelope_kwargs,
            )
            # W607-DX -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``missing_index_serialize_envelope_failed:`` marker was
            # appended to ``_w607dx_warnings_out`` and the floor stub
            # carries only the pre-raise combined list. Rebuild the
            # floor stub's warnings_out so the new marker reaches the
            # JSON output. Clean path -> envelope is the real
            # json_envelope return value, no rebuild.
            if envelope is _envelope_floor and _w607dx_warnings_out:
                _combined_warnings = list(_w607ci_warnings_out) + list(_w607dx_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
                _envelope_floor["warnings_out"] = list(_combined_warnings)
                envelope = _envelope_floor
            click.echo(to_json(envelope))
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
