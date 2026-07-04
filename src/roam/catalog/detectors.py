"""Algorithm detection: query DB signals to find suboptimal patterns.

Each detector has signature ``(conn) -> list[dict]`` and returns findings
with fields: task_id, detected_way, suggested_way, symbol_id, symbol_name,
kind, location, confidence, reason.

Design principles (informed by research):
- Structural AST patterns (loop depth, accumulator, subscript) are more
  reliable than function-name matching alone.
- Name matching is a confidence booster, not a gate.
- Idiom-only improvements (same Big-O) get ``low`` confidence.
- Genuine complexity-class improvements get ``medium`` or ``high``.
- Suppress known false-positive patterns (grid traversal, intentional polling).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sqlite3
from typing import Any, Callable, Iterable, Mapping

from roam.catalog._shared import is_test_path as _shared_is_test_path
from roam.catalog._shared import loc as _loc
from roam.catalog.tasks import best_way
from roam.catalog.versions import detector_version as _detector_version_for_task
from roam.db.edge_kinds import CALL_EDGE_KINDS
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
)
from roam.languages import JS_FAMILY_LANGUAGES
from roam.observability import log_swallowed
from roam.output._severity import severity_rank

# W1037: public surface declaration. New detector functions added below
# follow the ``detect_*`` naming convention and should be appended to the
# alphabetical list below at the same time as their ``@algorithm_detector``
# decoration. Constants and helpers prefixed with a leading underscore
# (``_DETECTOR_REGISTRY``, ``_DETECTOR_METADATA``, ``_finding``, ...) are
# intentionally OMITTED — tests that need them import by explicit name
# (``__all__`` only affects ``from foo import *``).
__all__ = [
    # Registry + decorator (public API).
    "algorithm_detector",
    "detector",
    "list_detector_names",
    "list_detector_surface",
    "list_registered_detectors",
    "run_detectors",
    # Framework-profile API.
    "list_framework_profiles",
    "autodetect_framework_profile",
    "set_active_framework_profile",
    # Query-cost vocabulary (closed enum, named constants).
    "QUERY_COST_LOW",
    "QUERY_COST_MEDIUM",
    "QUERY_COST_HIGH",
    # Detector functions (alphabetical).
    "detect_async_blocking_sleep",
    "detect_branching_recursion",
    "detect_broad_except_swallow",
    "detect_busy_wait",
    "detect_dangerous_eval",
    "detect_io_in_loop",
    "detect_linear_search",
    "detect_list_prepend",
    "detect_loop_invariant_call",
    "detect_loop_lookup",
    "detect_manual_gcd",
    "detect_manual_power",
    "detect_manual_sort",
    "detect_matrix_mult",
    "detect_naive_fibonacci",
    "detect_nested_lookup",
    "detect_quadratic_string",
    "detect_regex_in_loop",
    "detect_serial_await_loop",
    "detect_sort_to_select",
    "detect_string_reverse",
    "detect_unremoved_event_listener",
    "detect_useeffect_missing_deps",
]

log = logging.getLogger(__name__)

# Inline-bound SQL fragment for JS-family language filters. Catalog
# detectors that target the TS / JS / Vue / Svelte ecosystem use this
# instead of hand-written `'javascript', 'typescript', ...` tuples so
# the .vue / .svelte SFCs aren't silently dropped from anti-pattern
# matching. ``files.language`` stores 'vue' / 'svelte' for SFCs, even
# though their ``<script>`` blocks are parsed and indexed as TS/JS.
_JS_FAMILY_SQL_TUPLE = "(" + ", ".join(f"'{lang}'" for lang in JS_FAMILY_LANGUAGES) + ")"


# ---------------------------------------------------------------------------
# A3 — Detector registry + @algorithm_detector decorator
# ---------------------------------------------------------------------------
#
# Replaces the implicit-by-tuple registration in ``_MATH_DETECTORS`` with an
# opt-in metadata registry. Decorating a detector with
# ``@algorithm_detector(...)``
# records its declared task, language coverage, confidence basis, and query
# cost so ``roam math --list-detectors / --only / --exclude`` can act on
# the metadata without re-deriving it elsewhere.
#
# This is additive: detectors that haven't been decorated still run via
# ``_MATH_DETECTORS``. Future PRs migrate the long tail; this wave seeds
# the substrate with the highest-leverage detectors.

# Allowed metadata vocabularies. Restricted enums catch typos at import
# time rather than letting bogus strings leak into JSON envelopes.
#
# W911: the confidence-tier values are imported from ``roam.db.findings``
# (the canonical source of truth) rather than re-defined here. The
# frozenset is just a derived view that adds membership-test semantics
# for the ``@algorithm_detector`` decorator. A drift-guard test in
# ``tests/test_w911_confidence_tier_parity.py`` asserts the set has not
# diverged from the four canonical constants.
_CONFIDENCE_BASES = frozenset(
    {
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STRUCTURAL,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_RUNTIME,
    }
)

# W915: query-cost vocabulary lifted to module-level named constants so the
# closed-enum shape matches ``_CONFIDENCE_BASES`` (W911). Same drift-guard
# rationale: a typo in a decorator's ``query_cost=`` argument should fail
# at import time against a named constant rather than against a bare
# literal that anyone can re-introduce elsewhere.
QUERY_COST_LOW = "low"
QUERY_COST_MEDIUM = "medium"
QUERY_COST_HIGH = "high"
_QUERY_COSTS = frozenset({QUERY_COST_LOW, QUERY_COST_MEDIUM, QUERY_COST_HIGH})

_DETECTOR_REGISTRY: dict[str, dict[str, Any]] = {}


def algorithm_detector(
    *,
    task_id: str,
    languages: tuple[str, ...] = (),
    confidence_basis: str = "heuristic",
    query_cost: str = QUERY_COST_LOW,
    version: str = "1.0.0",
) -> Callable[[Callable[..., list[dict]]], Callable[..., list[dict]]]:
    """Register an algorithm-catalog detector with metadata.

    Parameters
    ----------
    task_id : str
        Algorithm catalog task this detector targets (see ``catalog/tasks.py``).
    languages : tuple of str
        Language names this detector applies to. Empty tuple means
        language-agnostic.
    confidence_basis : str
        One of ``heuristic``, ``structural``, ``static_analysis``, ``runtime``.
    query_cost : str
        One of ``low``, ``medium``, ``high`` — rough indicator of DB load.
    version : str
        Detector version string (bump on behavior changes).
    """
    if confidence_basis not in _CONFIDENCE_BASES:
        raise ValueError(f"confidence_basis must be one of {sorted(_CONFIDENCE_BASES)}, got {confidence_basis!r}")
    if query_cost not in _QUERY_COSTS:
        raise ValueError(f"query_cost must be one of {sorted(_QUERY_COSTS)}, got {query_cost!r}")

    def wrap(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
        _DETECTOR_REGISTRY[fn.__name__] = {
            "name": fn.__name__,
            "task_id": task_id,
            "languages": tuple(languages),
            "confidence_basis": confidence_basis,
            "query_cost": query_cost,
            "version": version,
            "function": fn,
        }
        return fn

    return wrap


# Backward-compatible alias for external imports. The implementation uses the
# domain-specific name above so this module no longer defines a ``detector``
# function that collides with ``roam.catalog.registry.detector``.
detector = algorithm_detector


def list_registered_detectors() -> list[dict[str, Any]]:
    """Return registry entries (excluding the callable) for inspection."""
    return [{k: v for k, v in entry.items() if k != "function"} for entry in _DETECTOR_REGISTRY.values()]


def list_detector_surface() -> list[dict[str, Any]]:
    """Return every built-in detector visible through ``roam algo``.

    ``list_registered_detectors()`` is intentionally narrow: it reports
    detectors declared with the ``@algorithm_detector`` decorator in this
    module.
    The runtime surface is wider because ``run_detectors()`` also loads
    Python-specific idiom detectors from ``python_idioms.py``. This helper
    mirrors the runtime surface so CLI discovery and ``--only``/``--exclude``
    filtering do not hide detectors that already run by default.
    """
    entries: list[dict[str, Any]] = []
    for entry in list_registered_detectors():
        row = dict(entry)
        row["source"] = "catalog"
        entries.append(row)

    try:
        from roam.catalog.python_idioms import PYTHON_IDIOM_DETECTORS
        from roam.catalog.versions import detector_version

        for task_id, _way_id, detect_fn in PYTHON_IDIOM_DETECTORS:
            entries.append(
                {
                    "name": getattr(detect_fn, "__name__", ""),
                    "task_id": task_id,
                    "languages": ("python",),
                    "confidence_basis": CONFIDENCE_HEURISTIC,
                    "query_cost": QUERY_COST_LOW,
                    "version": detector_version(task_id),
                    "source": "python_idioms",
                }
            )
    except ImportError as exc:
        # Optional-module guard: python_idioms absence just yields fewer
        # surface entries — logged loud rather than silently swallowed.
        log_swallowed("detectors.surface.python_idioms_import", exc)

    try:
        from roam.catalog.js_idioms import JS_IDIOM_DETECTORS
        from roam.catalog.versions import detector_version

        for task_id, _way_id, detect_fn in JS_IDIOM_DETECTORS:
            entries.append(
                {
                    "name": getattr(detect_fn, "__name__", ""),
                    "task_id": task_id,
                    "languages": ("javascript", "typescript"),
                    "confidence_basis": CONFIDENCE_HEURISTIC,
                    "query_cost": QUERY_COST_LOW,
                    "version": detector_version(task_id),
                    "source": "js_idioms",
                }
            )
    except ImportError as exc:
        # Optional-module guard: js_idioms absence just yields fewer surface
        # entries — logged loud rather than silently swallowed.
        log_swallowed("detectors.surface.js_idioms_import", exc)

    return entries


def list_detector_names() -> set[str]:
    """Return filterable detector function names for ``--only`` / ``--exclude``."""
    return {e["name"] for e in list_detector_surface() if e.get("name")}


def _is_test_path(path: str) -> bool:
    # when --include-tests was set on the CLI, force
    # this to return False so every detector stops filtering test paths.
    if _INCLUDE_TESTS_OVERRIDE:
        return False
    # W873: delegate to the canonical catalog-layer detector. The shared
    # helper is strictly a superset of the prior in-file rules (adds
    # ``__tests__/``, ``spec/``, ``testing/``, ``conftest.py``, plus
    # Go/Rust/PHP/JS/TS test-suffix coverage).
    return _shared_is_test_path(path)


def _finding(
    task_id: str,
    detected_way: str,
    sym: sqlite3.Row,
    reason: str,
    confidence: str = "medium",
    *,
    evidence: Mapping[str, Any] | None = None,
    fix: str | Mapping[str, Any] | None = None,
    match_line: int | None = None,
    snippet: str | None = None,
    matched_patterns: Iterable[str] | None = None,
) -> dict:
    """Build a finding dict.

    W875: NOT consolidated with ``roam.catalog.smells._finding`` —
    the two share only 3 of ~14 union field names
    (``symbol_name``, ``kind``, ``location``). This helper produces
    the algorithm-catalog shape (task_id/detected_way/suggested_way/
    symbol_id/symbol_line/confidence/reason + optional evidence/fix)
    from a sqlite3.Row ``sym`` and integrates with
    ``tasks.best_way()``. ``smells._finding`` produces a fixed 8-key
    structural-smell envelope from plain strings + numbers. Hoisting
    a shared base would replace ~20 lines of clear domain-specific
    helpers with ~30 lines of base + wrappers + union-typed kwargs —
    net negative. See the matching comment in ``smells.py``.


    when ``match_line`` is supplied, the finding's
    ``location`` field points at the exact AST node where the pattern
    matched (e.g. the line containing the .sort() call) — not the
    enclosing function declaration. The function-start line is
    preserved as ``symbol_line`` so callers needing both have access.

    when ``snippet`` is also supplied alongside
    ``match_line``, evidence gets a ``context_lines`` block — ±2 lines
    around the match site, with the matching line flagged. Cuts FP
    triage time from "open the file" to "skim the JSON".

    ``matched_patterns`` lists the named sub-patterns
    that contributed to the verdict (e.g. ``["nested-loop", "sort+slice"]``
    for sort-to-select). Surfaces in evidence so users can see WHY a
    finding fired without grepping the detector source.

    W932 (W925 follow-up): ``evidence`` is annotated as
    ``Mapping[str, Any] | None``. Audited all 4 ``evidence=`` keyword
    call-sites in this module — every one passes a dict literal; there
    are no positional or non-dict callers (the ``*`` makes ``evidence``
    keyword-only). The runtime defensive branch below
    (``not isinstance(evidence, dict)``) is kept as belt-and-braces for
    forward compatibility with future plugin detectors that may pass
    non-Mapping shapes, but the static type stays narrow so misuse
    surfaces at mypy time rather than at runtime as a silent rewrap.
    """
    bw = best_way(task_id)
    sym_line = sym["line_start"]
    actual_line = match_line if match_line is not None else sym_line
    finding = {
        "task_id": task_id,
        "detected_way": detected_way,
        "suggested_way": bw["id"] if bw else "",
        "symbol_id": sym["id"],
        "symbol_name": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "location": _loc(sym["file_path"], actual_line),
        "symbol_line": sym_line,
        "confidence": confidence,
        "reason": reason,
    }
    # W924: stamp the per-task_id detector_version from
    # ``roam.catalog.versions.detector_version`` (the canonical lookup —
    # falls back to DEFAULT_VERSION="1.0.0" when the task_id is not in
    # ``DETECTOR_VERSION_OVERRIDES``). Sibling helpers
    # (``clones_cross_layer._make_finding``, ``parallel_hierarchy._finding``,
    # ``smells.make_smell_finding``) already stamp ``detector_version``;
    # this closes the asymmetry for the 30+ algorithm-catalog detectors
    # routed through ``_finding``. The lookup never returns ``None``, so
    # the key is always present — but kept inside the explicit assignment
    # (rather than baked into the literal above) so a future caller can
    # override via post-mutation without confusion.
    dv = _detector_version_for_task(task_id)
    if dv is not None:
        finding["detector_version"] = dv
    # D1 — context lines (cheap to compute, optional based on snippet availability)
    context_lines: list[dict] = []
    if snippet is not None and match_line is not None:
        context_lines = _extract_context_lines(snippet, match_line, sym_line)

    if evidence is None:
        evidence = {}
    elif not isinstance(evidence, dict):
        evidence = {"raw_evidence": evidence}
    if context_lines:
        evidence = dict(evidence)
        evidence["context_lines"] = context_lines
    # matched_patterns is the explainability hook. Always copy the
    # input list so detector mutations don't leak into shared state.
    if matched_patterns:
        evidence = dict(evidence) if not context_lines else evidence
        evidence["matched_patterns"] = list(matched_patterns)

    if evidence:
        finding["evidence"] = evidence
    if fix:
        finding["fix"] = fix
    return finding


def _extract_context_lines(
    snippet: str,
    match_line: int | None,
    sym_line_start: int | None,
    *,
    radius: int = 2,
) -> list[dict]:
    """D1 — Pull ±radius lines around the match site as evidence.

    User feedback (3.b): "Each finding should show the exact lines that
    triggered the detector (5-line context, not just the function name).
    Makes 'is this a FP?' a 10-second skim instead of a 5-minute investigation."

    Returns a list of {"line": absolute_line_no, "text": line} dicts.
    Empty list when snippet/lines unavailable. Truncates to 80 chars per
    line so big function bodies don't bloat the JSON envelope.
    """
    if not snippet or sym_line_start is None or match_line is None:
        return []
    lines = snippet.splitlines()
    if not lines:
        return []
    # Convert match_line back to a 0-based offset within the snippet.
    match_offset = match_line - sym_line_start
    if match_offset < 0 or match_offset >= len(lines):
        return []
    start = max(0, match_offset - radius)
    end = min(len(lines), match_offset + radius + 1)
    out: list[dict] = []
    for i in range(start, end):
        out.append(
            {
                "line": sym_line_start + i,
                "text": lines[i].rstrip()[:80],
                "is_match": i == match_offset,
            }
        )
    return out


def _find_match_line(snippet: str, pattern, sym_line_start: int | None) -> int | None:
    """Walk the snippet line by line to find the first line matching ``pattern``.

    Returns the absolute line number (sym_line_start + offset). When
    snippet doesn't contain the match, returns sym_line_start unchanged
    so callers can blindly substitute.

    Used by sort-to-select / IO-in-loop / regex-in-loop detectors to
    pin findings at the exact match site.
    """
    if not snippet or sym_line_start is None:
        return sym_line_start
    for offset, line in enumerate(snippet.splitlines()):
        if pattern.search(line):
            return sym_line_start + offset
    return sym_line_start


def _js_source_findings_preserving_evidence_lines(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    detected_way: str,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
    reason_for_first_match: Callable[[str], str],
    confidence: str,
) -> list[dict]:
    """Run JS-family source-pattern detectors through one evidence-line path."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND f.language IN " + _JS_FAMILY_SQL_TUPLE + ""
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue
        matched: list[str] = []
        first_pos: int | None = None
        for pattern, label in patterns:
            match = pattern.search(snippet)
            if match is None:
                continue
            matched.append(label)
            if first_pos is None or match.start() < first_pos:
                first_pos = match.start()
        if not matched:
            continue
        match_line = (r["line_start"] or 1) + snippet[: first_pos or 0].count("\n")
        results.append(
            _finding(
                task_id,
                detected_way,
                r,
                reason_for_first_match(matched[0]),
                confidence,
                match_line=match_line,
                snippet=snippet,
                matched_patterns=matched,
            )
        )
    return results


# M4 — recognise DEV-only / DEBUG-only gates so production-impact
# detectors don't fire on code that's stripped from production builds.
# Real-world examples (from the prior round of FPs):
#   - if (import.meta.env.DEV) { ... heavy diagnostics ... }
#   - if (process.env.NODE_ENV !== 'production') { ... }
#   - if (__DEV__) { ... }
#   - if (DEBUG) { ... }
#   - console.assert(...)  — short-circuits in production
_DEV_GATE_RE = re.compile(
    # No trailing \b — alternatives ending in `'production'` or `__DEV__` have
    # non-word chars after them, so the global `\b` would never fire there.
    r"\bif\s*\([^)]*?\b(?:"
    r"import\.meta\.env\.(?:DEV\b|MODE\s*[!=]==?\s*['\"]production['\"])|"
    r"process\.env\.NODE_ENV\s*[!=]==?\s*['\"]production['\"]|"
    r"__DEV__|"
    r"DEBUG\b"
    r")",
    re.IGNORECASE,
)
_CONSOLE_ASSERT_RE = re.compile(r"\bconsole\.assert\s*\(")


def _is_dev_only_block(snippet: str, match_line_offset: int | None = None) -> bool:
    """Heuristic: does the snippet (or the lines around the match) sit inside
    a DEV-only conditional?

    Conservative matcher: returns True only when an obvious DEV gate is
    visible *before* the match line in the snippet. Won't catch every
    case (e.g. a flag set in a parent function), but catches the common
    Vue 3 / React / Next.js / Vite patterns where heavy diagnostics live
    behind import.meta.env.DEV.
    """
    if not snippet:
        return False
    if _DEV_GATE_RE.search(snippet) or _CONSOLE_ASSERT_RE.search(snippet):
        return True
    return False


def _find_first_keyword_line(snippet: str, keywords: tuple[str, ...], sym_line_start: int | None) -> int | None:
    """First line containing any of the keywords (case-insensitive substring).

    Cheaper than regex; used for self-call detection in branching recursion.
    """
    if not snippet or sym_line_start is None:
        return sym_line_start
    lows = [k.lower() for k in keywords]
    for offset, line in enumerate(snippet.splitlines()):
        ll = line.lower()
        if any(k in ll for k in lows):
            return sym_line_start + offset
    return sym_line_start


def _row_value(row, key, default=None):
    """Safely read a sqlite row key with a fallback."""
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def _json_list(value) -> list[str]:
    """Parse a JSON-encoded list of strings; return [] on malformed input."""
    if not value:
        return []
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str):
            out.append(item)
    return out


@functools.lru_cache(maxsize=4096)
def _call_leaf(name: str) -> str:
    """Return the final method/function token from a qualified call name."""
    if not name:
        return ""
    return name.rsplit(".", 1)[-1]


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# single-process file-line cache. The 23 algorithm
# detectors each call `_read_symbol_source` for every loop-bearing
# symbol. On roam-code itself that's 4989 reads and ~1.7s of wall
# time; many are exact-duplicate (path, line_start, line_end) tuples
# from sibling detectors over the same row. Caching the file content
# once per path (not per slice) lets repeat slices hit memory.
#
# bounded with FIFO eviction. Without a cap a single
# `roam math` on a 50K-file monorepo would hold every loop-bearing file's
# full source in memory simultaneously. Eviction by insertion order
# (Python dict invariant) keeps the cap honest with O(1) overhead.
_FILE_LINES_CACHE: dict[tuple[str, float], list[str]] = {}
_FILE_LINES_CACHE_MAX = 4096  # entries; ~10MB at a typical 2.5KB/file avg

# File-scope for catalog detectors (set by run_detectors when scope_file_ids is
# given). When set, `_read_symbol_source` returns "" for any path NOT in the
# set, so every source-reading detector skips out-of-scope files without a file
# read or regex pass. This is a pure perf optimization: run_detectors already
# post-filters catalog findings to the scope, so an out-of-scope finding would
# be discarded anyway — skipping the read just avoids computing it.
_DETECTOR_SCOPE_PATHS: set[str] | None = None


def _file_lines_cached(path: str) -> list[str]:
    # key by (path, mtime). Without the mtime guard,
    # a relative path like "svc.py" could collide across test fixtures
    # that monkeypatch.chdir into different temp dirs but write the same
    # filename. Cache survives within one process but never returns stale
    # bytes after the file has been rewritten.
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    cache_key = (path, mtime)
    cached = _FILE_LINES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    if len(_FILE_LINES_CACHE) >= _FILE_LINES_CACHE_MAX:
        # Evict oldest insertion — Python 3.7+ dicts preserve insertion
        # order, so popitem(last=False)-equivalent is `del first key`.
        first_key = next(iter(_FILE_LINES_CACHE))
        del _FILE_LINES_CACHE[first_key]
    _FILE_LINES_CACHE[cache_key] = lines
    return lines


def _read_symbol_source(path: str, line_start: int | None, line_end: int | None) -> str:
    """Best-effort source slice for a symbol location.

    Reuses an in-memory line cache so sibling detectors don't re-read
    the same file. Cache lives for the duration of the process; safe
    because `roam math` runs detectors against a snapshotted index.

    When a detector file-scope is active (``_DETECTOR_SCOPE_PATHS``), an
    out-of-scope path short-circuits to "" so the caller skips it without a
    file read or regex pass — see the module-global's docstring.
    """
    if _DETECTOR_SCOPE_PATHS is not None and path not in _DETECTOR_SCOPE_PATHS:
        return ""
    lines = _file_lines_cached(path)
    if not lines:
        return ""
    if line_start is None or line_end is None:
        return "\n".join(lines)
    ls = max(1, int(line_start))
    le = max(ls, int(line_end))
    return "\n".join(lines[ls - 1 : le])


def _iter_loop_calls(row) -> list[str]:
    """Return combined loop call names (leaf + qualified)."""
    calls = _json_list(_row_value(row, "calls_in_loops", ""))
    qcalls = _json_list(_row_value(row, "calls_in_loops_qualified", ""))
    return _dedupe(calls + qcalls)


def _call_in(calls: list[str], targets: set[str]) -> list[str]:
    """Match calls against targets by exact qualified name or leaf token."""
    hits: list[str] = []
    for c in calls:
        if c in targets or _call_leaf(c) in targets:
            hits.append(c)
    return _dedupe(hits)


# Note: call target names are extracted as the last identifier in member
# expressions (e.g. re.compile -> "compile", re.match -> "match").
_REGEX_COMPILE_CALLS = {"compile", "Compile", "MustCompile"}
_REGEX_CONVENIENCE_CALLS = {
    "match",
    "search",
    "findall",
    "sub",
    "split",
    "fullmatch",
    "finditer",
    "matches",
    "Replace",
    "ReplaceAll",
    "Find",
    "FindAll",
    "MatchString",
}

# Module-level regex prefixes — calls like `re.match()` recompile each time,
# but `compiled_pattern.match()` does not. Only flag the former.
_REGEX_MODULE_PREFIXES = ("re.", "regexp.", "regex.", "Pattern.")


def _regex_module_convenience_calls(row) -> list[str]:
    """Return loop calls that use module-level regex convenience APIs."""
    qcalls = _json_list(_row_value(row, "calls_in_loops_qualified", ""))
    return [
        c
        for c in qcalls
        if c.startswith(_REGEX_MODULE_PREFIXES) and _call_leaf(c) in _REGEX_CONVENIENCE_CALLS
    ]


def _regex_loop_finding(row) -> dict | None:
    """Build the regex-in-loop finding for one loop-bearing symbol row."""
    compile_calls = _call_in(_iter_loop_calls(row), _REGEX_COMPILE_CALLS)
    if compile_calls:
        return _finding(
            "regex-in-loop",
            "compile-per-iter",
            row,
            f"Regex compilation via {', '.join(compile_calls[:2])} inside loop",
            "high",
        )

    module_convenience = _regex_module_convenience_calls(row)
    if not module_convenience:
        return None
    return _finding(
        "regex-in-loop",
        "compile-per-iter",
        row,
        f"Regex call ({', '.join(module_convenience[:2])}) inside loop (recompiles per iteration)",
        "high",
    )


_FRAMEWORK_IO_PACKS = {
    "python": [
        {
            "framework": "django-orm",
            "receiver_hints": {"objects", "queryset"},
            "leaves": {"get", "filter", "exclude", "all", "count", "exists"},
            "confidence": "high",
            "fix": ("Batch ORM fetches with `id__in` and add `select_related()/prefetch_related()`."),
        },
        {
            "framework": "sqlalchemy",
            "receiver_hints": {"session"},
            "leaves": {"execute", "query", "scalars", "get"},
            "confidence": "high",
            "fix": ("Use one `IN` query and eager loading (`selectinload`/`joinedload`) before the loop."),
        },
        {
            "framework": "python-http-client",
            "receiver_hints": {"requests", "httpx", "aiohttp", "client", "session"},
            "leaves": {"get", "post", "put", "delete", "patch", "request"},
            "confidence": "medium",
            "fix": "Use a bulk endpoint or bounded async batches (e.g., gather + semaphore).",
        },
    ],
    "ruby": [
        {
            "framework": "rails-active-record",
            "receiver_hints": {"activerecord", "relation", "model"},
            "leaves": {"find", "find_by", "where", "pluck"},
            "confidence": "high",
            "fix": "Preload associations with `.includes`/`.preload` and fetch in bulk.",
        },
    ],
    "php": [
        {
            "framework": "laravel-eloquent",
            "receiver_hints": {"db", "model", "eloquent", "query"},
            "leaves": {"find", "where", "first", "get", "value"},
            "confidence": "high",
            "fix": "Use `Model::whereIn(...)` or eager load with `with(...)`.",
        },
    ],
    "javascript": [
        {
            "framework": "node-orm",
            "receiver_hints": {"prisma", "sequelize", "knex", "db"},
            "leaves": {"findmany", "findunique", "findone", "query"},
            "confidence": "high",
            "fix": "Replace per-item ORM calls with one batched query.",
        },
        {
            "framework": "http-client",
            "receiver_hints": {"axios", "fetch", "http", "client"},
            "leaves": {"get", "post", "request"},
            "confidence": "medium",
            "fix": "Use a bulk endpoint or bounded parallel batch requests.",
        },
        {
            "framework": "graphql-client",
            "receiver_hints": {"graphql", "apollo", "urql", "client"},
            "leaves": {"query", "mutate", "request"},
            "confidence": "medium",
            "fix": "Batch GraphQL operations or use persisted/bulk queries.",
        },
    ],
    "typescript": [
        {
            "framework": "node-orm",
            "receiver_hints": {"prisma", "sequelize", "knex", "db"},
            "leaves": {"findmany", "findunique", "findone", "query"},
            "confidence": "high",
            "fix": "Replace per-item ORM calls with one batched query.",
        },
        {
            "framework": "http-client",
            "receiver_hints": {"axios", "fetch", "http", "client"},
            "leaves": {"get", "post", "request"},
            "confidence": "medium",
            "fix": "Use a bulk endpoint or bounded parallel batch requests.",
        },
        {
            "framework": "graphql-client",
            "receiver_hints": {"graphql", "apollo", "urql", "client"},
            "leaves": {"query", "mutate", "request"},
            "confidence": "medium",
            "fix": "Batch GraphQL operations or use persisted/bulk queries.",
        },
    ],
    "java": [
        {
            "framework": "jpa-hibernate",
            "receiver_hints": {"repository", "entitymanager", "jdbc", "template"},
            "leaves": {"findbyid", "findall", "query", "execute", "select"},
            "confidence": "high",
            "fix": ("Preload rows with one repository/JPA query (`IN (...)` + fetch join)."),
        },
    ],
}


def _framework_packs(language: str | None) -> list[dict]:
    if not language:
        return []
    return _FRAMEWORK_IO_PACKS.get(language.lower(), [])


_IO_GUARD_HINTS = {
    "python": {
        "select_related(",
        "prefetch_related(",
        "joinedload(",
        "selectinload(",
        "in_(",
        "__in",
        "executemany(",
    },
    "ruby": {
        ".includes(",
        ".preload(",
        ".eager_load(",
    },
    "php": {
        "->with(",
        "wherein(",
        "loadmissing(",
    },
    "javascript": {
        "findmany(",
        "include:",
        " in: ",
    },
    "typescript": {
        "findmany(",
        "include:",
        " in: ",
    },
    "java": {
        "join fetch",
        "findallbyid(",
        " in (",
    },
}


def _guard_hints_from_source(language: str | None, snippet: str) -> list[str]:
    if not language or not snippet:
        return []
    tokens = _IO_GUARD_HINTS.get((language or "").lower(), set())
    if not tokens:
        return []
    lower = snippet.lower()
    hits: list[str] = []
    for tok in tokens:
        if tok in lower:
            hits.append(tok)
    return _dedupe(hits)


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


_RE_FIRE_AND_FORGET_TASK = re.compile(
    r"^\s*(?:asyncio\.)?create_task\s*\(",
    re.MULTILINE,
)
_RE_STORED_TASK = re.compile(
    r"^\s*(?:\w+\s*[+\-*/]?=\s*|\w+\s*\.\s*append\s*\(\s*|\w+\s*\.\s*add\s*\(\s*|return\s+|await\s+)"
    r"(?:asyncio\.)?create_task\s*\(",
    re.MULTILINE,
)
_RE_SPREAD_ACC = re.compile(
    r"\b(\w+)\s*=\s*\[\s*\.\.\.\s*\1\s*,",  # name = [...name,
)
_RE_SPREAD_OBJ_ACC = re.compile(
    r"\b(\w+)\s*=\s*\{\s*\.\.\.\s*\1\s*[,}]",  # name = {...name,
)
_RE_REDUCE_SPREAD = re.compile(
    r"\.\s*reduce\s*\(\s*\(\s*(\w+)[^)]*\)\s*=>\s*\[\s*\.\.\.\s*\1\s*,",
)
_RE_REDUCE_SPREAD_OBJ = re.compile(
    r"\.\s*reduce\s*\(\s*\(\s*(\w+)[^)]*\)\s*=>\s*\{\s*\.\.\.\s*\1\s*[,}]",
)


@algorithm_detector(
    task_id="sorting",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_manual_sort(conn: sqlite3.Connection) -> list[dict]:
    """Symbols named *sort* with nested loops, comparisons, and subscript
    access (swap pattern).  No call to built-in sort."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_nested_loops, ms.calls_in_loops, "
        "ms.loop_with_compare, ms.subscript_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%sort%' OR s.name LIKE '%Sort%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"sort", "sorted", "Arrays.sort", "Collections.sort", "qsort", "std::sort"}):
            continue
        # Subscript access in loops strengthens the signal (swap pattern)
        conf = "high" if r["subscript_in_loops"] else "medium"
        results.append(
            _finding(
                "sorting",
                "manual-sort",
                r,
                "Nested loops with comparisons in sort-named function",
                conf,
            )
        )
    return results


@algorithm_detector(
    task_id="search-sorted",
    languages=(),
    confidence_basis="heuristic",
    query_cost=QUERY_COST_LOW,
)
def detect_linear_search(conn: sqlite3.Connection) -> list[dict]:
    """Functions explicitly named *sorted*/*search_sorted* that loop with
    comparisons but don't call bisect/binarySearch.

    Downgraded to low confidence because we cannot verify from AST alone
    that the data being searched is actually sorted.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_compare, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%search\\_sorted%' ESCAPE '\\' OR s.name LIKE '%searchSorted%' "
        "  OR s.name LIKE '%find\\_sorted%' ESCAPE '\\' OR s.name LIKE '%findSorted%' "
        "  OR s.name LIKE '%find\\_in\\_sorted%' ESCAPE '\\' OR s.name LIKE '%in\\_sorted%' ESCAPE '\\' "
        "  OR s.name LIKE '%linear\\_search%' ESCAPE '\\' OR s.name LIKE '%linearSearch%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(
            calls,
            {
                "bisect",
                "bisect_left",
                "bisect_right",
                "binarySearch",
                "binary_search",
                "lower_bound",
                "upper_bound",
            },
        ):
            continue
        results.append(
            _finding(
                "search-sorted",
                "linear-scan",
                r,
                "Linear scan in function that implies sorted data",
                "low",
            )
        )
    return results


@algorithm_detector(
    task_id="manual-power",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_manual_power(conn: sqlite3.Connection) -> list[dict]:
    """Loop-based exponentiation in power/exponent-named functions."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_multiplication, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%pow%' OR s.name LIKE '%Pow%' "
            "  OR s.name LIKE '%power%' OR s.name LIKE '%Power%' "
            "  OR s.name LIKE '%exp%' OR s.name LIKE '%Exponent%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1 "
            "AND ms.loop_with_multiplication = 1"
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, 0 as loop_with_multiplication, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%pow%' OR s.name LIKE '%Pow%' "
            "  OR s.name LIKE '%power%' OR s.name LIKE '%Power%' "
            "  OR s.name LIKE '%exp%' OR s.name LIKE '%Exponent%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1 "
            "AND ms.loop_with_accumulator = 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"pow", "Math.pow", "std::pow", "math.pow", "BigInteger.modPow"}):
            continue
        conf = "high" if _row_value(r, "loop_with_multiplication", 0) else "medium"
        results.append(
            _finding(
                "manual-power",
                "loop-multiply",
                r,
                "Loop multiplication used for exponentiation",
                conf,
            )
        )
    return results


@algorithm_detector(
    task_id="manual-gcd",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_manual_gcd(conn: sqlite3.Connection) -> list[dict]:
    """Manual GCD loops where built-in/standard helpers are available."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_modulo, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%gcd%' OR s.name LIKE '%GCD%' "
            "  OR s.name LIKE '%hcf%' OR s.name LIKE '%gcf%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, 0 as loop_with_modulo, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%gcd%' OR s.name LIKE '%GCD%' "
            "  OR s.name LIKE '%hcf%' OR s.name LIKE '%gcf%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"gcd", "math.gcd", "std::gcd", "BigInteger.gcd"}):
            continue
        conf = "medium"
        if _row_value(r, "loop_with_modulo", 0):
            conf = "high"
        results.append(
            _finding(
                "manual-gcd",
                "manual-gcd",
                r,
                "Manual GCD loop can be replaced with standard gcd helper",
                conf,
            )
        )
    return results


@algorithm_detector(
    task_id="string-reverse",
    languages=(),
    confidence_basis="heuristic",
    query_cost=QUERY_COST_LOW,
)
def detect_string_reverse(conn: sqlite3.Connection) -> list[dict]:
    """Manual reversal loops in reverse-named functions."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_accumulator, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%reverse%' OR s.name LIKE '%Reverse%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_accumulator, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%reverse%' OR s.name LIKE '%Reverse%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"reverse", "reversed", "std::reverse", "StringBuilder.reverse", "strrev"}):
            continue
        results.append(
            _finding(
                "string-reverse",
                "manual-reverse",
                r,
                "Manual character-loop reversal in reverse-named function",
                "low",
            )
        )
    return results


@algorithm_detector(
    task_id="matrix-mult",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_matrix_mult(conn: sqlite3.Connection) -> list[dict]:
    """Naive triple-loop matrix multiplication patterns."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.subscript_in_loops, "
            "ms.loop_with_multiplication, ms.loop_with_accumulator, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%matrix%' OR s.name LIKE '%matmul%' "
            "  OR s.name LIKE '%multiply\\_matrix%' ESCAPE '\\' OR s.name LIKE '%dot%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 3 "
            "AND ms.subscript_in_loops = 1"
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.subscript_in_loops, "
            "0 as loop_with_multiplication, ms.loop_with_accumulator, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%matrix%' OR s.name LIKE '%matmul%' "
            "  OR s.name LIKE '%multiply\\_matrix%' ESCAPE '\\' OR s.name LIKE '%dot%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 3 "
            "AND ms.subscript_in_loops = 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"dot", "matmul", "gemm", "dgemm", "numpy.dot", "np.matmul"}):
            continue
        conf = (
            "high"
            if (_row_value(r, "loop_with_multiplication", 0) and _row_value(r, "loop_with_accumulator", 0))
            else "medium"
        )
        results.append(
            _finding(
                "matrix-mult",
                "naive-triple",
                r,
                "Naive matrix multiplication via nested loops",
                conf,
            )
        )
    return results


@algorithm_detector(
    task_id="fibonacci",
    languages=(),
    confidence_basis="heuristic",
    query_cost=QUERY_COST_LOW,
)
def detect_naive_fibonacci(conn: sqlite3.Connection) -> list[dict]:
    """Recursive functions named *fib* without memoization.

    O(2^n) -> O(n) — one of the strongest algorithmic improvements.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_self_call "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%fib%' OR s.name LIKE '%Fib%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.has_self_call >= 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Check if there's a memoization decorator (edge to lru_cache/cache)
        memo_edge = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ? "
            "AND t.name IN ('lru_cache', 'cache', 'memoize', 'memo', "
            "               'functools.lru_cache', 'functools.cache') "
            "LIMIT 1",
            (r["id"],),
        ).fetchone()
        if memo_edge:
            continue
        results.append(
            _finding(
                "fibonacci",
                "naive-recursive",
                r,
                "Recursive fibonacci without memoization (exponential blowup)",
                "high",
            )
        )
    return results


_BOUNDED_NESTED_NAMES = {
    # Grid / matrix traversal.
    "matrix",
    "grid",
    "board",
    "pixel",
    "cell",
    "permut",
    "combin",
    "cartesian",
    "product",
    "transpose",
    "rotate",
    "convolv",
    # Diff / overlap / range-intersection.
    "changed_symbols",
    "overlap",
    "intersect",
    "diff",
    "hunk",
    "region",
    "interval",
    "range_overlap",
    # Graph clustering / labelling.
    "label_cluster",
    "cluster_label",
    "cluster_assignment",
    "cluster_match",
    # Co-change / coupling.
    "cochange",
    "co_change",
    "coupling_pair",
    "pairwise",
    # Detector dispatch / rule application.
    "run_detector",
    "apply_rule",
    "for_each_detector",
    "dispatch_rule",
    # Rendering / formatting.
    "format_table",
    "format_grid",
    "render_table",
    "render_grid",
    "render_matrix",
    "print_table",
    "print_grid",
    "emit_table",
    "draw_grid",
    "draw_table",
    "tabulate",
}


def _is_bounded_nested_lookup_row(row) -> bool:
    name_lower = (row["name"] or "").lower()
    qname_lower = (row["qualified_name"] or "").lower() if "qualified_name" in row.keys() else ""
    return any(kw in name_lower or kw in qname_lower for kw in _BOUNDED_NESTED_NAMES)


@algorithm_detector(
    task_id="nested-lookup",
    languages=("python", "javascript", "typescript", "php", "ruby", "go", "java"),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_nested_lookup(conn: sqlite3.Connection) -> list[dict]:
    """Nested loops that match the hash-joinable lookup fingerprint.

    The historical triplet (nested_loops + subscript_in_loops + loop_compare)
    flagged ~85% false positives on PHP because both streaming output
    (``foreach rows × cols`` CSV emission) and matrix traversal share the
    same surface signals. The 2026-05 dogfood quantified this and named
    the discriminator:

    A real O(n*m) hash-joinable lookup contains BOTH

      1. an equality comparison on per-iteration keys
         (``$a->id === $b->id`` / ``a['k'] == b['k']``), AND
      2. an accumulator write gated by that equality
         (``$matched[$t->id] = $e`` / ``results.append((a, b))``).

    The new ``loop_eq_with_dependent_write`` math-signal captures that
    structural fingerprint at index time (single AST walk). This detector
    now requires the signal, on top of the historical triplet. Suppresses
    known FP names (matrix / grid / format_table / co-change / …) on top.

    Note: legacy DBs (indexed before the W36 sprint) will have
    ``loop_eq_with_dependent_write = 0`` for every row and report zero
    findings. A re-index repopulates the column; this is the same
    behaviour the schema-versioned ``index_manifest`` already advertises
    to bundle/doctor consumers.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_nested_loops, ms.subscript_in_loops, "
        "ms.loop_with_compare, ms.loop_eq_with_dependent_write "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.subscript_in_loops = 1 "
        "AND ms.loop_with_compare = 1 "
        "AND ms.loop_eq_with_dependent_write = 1 "
        "AND sm.cognitive_complexity >= 8"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]) or _is_bounded_nested_lookup_row(r):
            continue
        results.append(
            _finding(
                "nested-lookup",
                "nested-iteration",
                r,
                "Nested loops with subscript access and comparisons (potential O(n*m))",
                "medium",
            )
        )
    return results


_POLL_NAMES = {
    "poll",
    "retry",
    "health_check",
    "healthcheck",
    "monitor",
    "wait_for",
    "wait_until",
    "watchdog",
    "ping",
    "heartbeat",
    "keepalive",
    "backoff",
    "_loop",
    "watch_",
    "watcher",
}
_SPIN_THRESHOLD_SECONDS = 1.0
_RE_SLEEP_ARG = re.compile(
    r"\b(?:time\.sleep|asyncio\.sleep|Thread\.sleep|usleep|Sleep)\s*\(\s*"
    r"(?:[A-Z][A-Z_]*|(?P<num>\d+(?:\.\d+)?))",
)


def _max_sleep_arg_seconds(snippet: str) -> float | None:
    """Return the largest literal sleep argument in seconds, or None."""
    max_seen: float | None = None
    for m in _RE_SLEEP_ARG.finditer(snippet or ""):
        num = m.group("num")
        if num is None:
            return None
        val = float(num)
        if max_seen is None or val > max_seen:
            max_seen = val
    return max_seen


def _is_operator_paced_sleep(row) -> bool:
    snippet = _read_symbol_source(
        row["file_path"],
        _row_value(row, "line_start", None),
        _row_value(row, "line_end", None),
    )
    max_sleep = _max_sleep_arg_seconds(snippet)
    return max_sleep is not None and max_sleep >= _SPIN_THRESHOLD_SECONDS


def _is_busy_wait_candidate(row) -> bool:
    if _is_test_path(row["file_path"]):
        return False
    calls = _iter_loop_calls(row)
    sleep_calls = {"sleep", "time.sleep", "Thread.sleep", "usleep", "nanosleep", "Sleep"}
    if not _call_in(calls, sleep_calls):
        return False
    name_lower = (row["name"] or "").lower()
    if any(kw in name_lower for kw in _POLL_NAMES):
        return False
    return not _is_operator_paced_sleep(row)


@algorithm_detector(
    task_id="busy-wait",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_busy_wait(conn: sqlite3.Connection) -> list[dict]:
    """Loops that call sleep — polling / busy-wait pattern.

    Suppresses intentional polling: functions named *poll*, *retry*,
    *health_check*, *monitor*, *wait_for* — these are legitimate patterns.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    results = []
    for r in rows:
        if not _is_busy_wait_candidate(r):
            continue
        results.append(
            _finding(
                "busy-wait",
                "sleep-loop",
                r,
                "sleep() called inside a loop (busy-wait pattern)",
                "high",
            )
        )
    return results


# ---------------------------------------------------------------------------
# New detectors: patterns identified by research
# ---------------------------------------------------------------------------


@algorithm_detector(
    task_id="regex-in-loop",
    languages=("python", "javascript", "typescript", "ruby"),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_regex_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """Regex compilation inside a loop — recompiles on every iteration.

    O(n*p) wasted compilation when O(p + n*m) is achievable by compiling
    once outside the loop.  Applies to all languages with regex engines.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        finding = _regex_loop_finding(r)
        if finding:
            results.append(finding)
    return results


_IO_HIGH_EXACT = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.patch",
    "urllib.request.urlopen",
    "session.execute",
    "session.query",
    "cursor.execute",
    "http.Get",
    "http.Post",
    # Node.js sync FS calls — these block the event loop
    # AND are dramatically slower than the async equivalents when iterated
    # in a loop. Each one is a syscall round-trip per iteration.
    "fs.readFileSync",
    "fs.writeFileSync",
    "fs.appendFileSync",
    "fs.existsSync",
    "fs.statSync",
    "fs.lstatSync",
    "fs.readdirSync",
    "fs.unlinkSync",
    "fs.mkdirSync",
    "fs.rmSync",
    "fs.copyFileSync",
}
_IO_HIGH_EXACT_LOWER = {c.lower() for c in _IO_HIGH_EXACT}
_IO_HIGH_LEAF = {
    "execute",
    "executemany",
    "query",
    "urlopen",
    # U3 — leaf-only matching catches `readFileSync(...)` even when the
    # `fs.` receiver is aliased (`const { readFileSync } = require('fs')`).
    "readfilesync",
    "writefilesync",
    "existssync",
    "statsync",
    "readdirsync",
}
_IO_AMBIGUOUS_BARE = {"query", "find", "get"}
_IO_MEDIUM_LEAF = {"fetchone", "fetchall", "fetchmany", "fetch", "find", "get", "open"}
_IO_RECEIVER_HINTS = {
    "session",
    "cursor",
    "db",
    "conn",
    "connection",
    "repo",
    "repository",
    "queryset",
    "client",
    "api",
    "http",
    "requests",
    "urllib",
    # Node.js fs receivers + popular wrappers.
    "fs",
    "fspromises",
    "fsasync",
    "fsextra",
    "fileutil",
}
# M3 expansion: when a call is IN this set OR matches one of the IN_MEMORY_LEAVES
# attached to a recognised receiver, treat as an in-memory cache read NOT I/O.
# Real-world FP that drove the expansion: roam math flagged
# `queryClient.getQueryData` inside a TanStack Query factory as N+1
# round-trips when those are sync cache reads.
_IN_MEMORY_EXACT = {
    "queryclient.setquerydata",
    "queryclient.getquerydata",
    "queryclient.getqueriesdata",
    "queryclient.setqueriesdata",
    "queryclient.invalidatequeries",
    "queryclient.removequeries",
    "queryclient.cancelqueries",
    "queryclient.refetchqueries",
    "qc.setquerydata",
    "qc.getquerydata",
    "qc.getqueriesdata",
    "qc.setqueriesdata",
    "qc.invalidatequeries",
    "redux.dispatch",
    "store.dispatch",
    # SWR + RTK Query + Apollo cache equivalents
    "mutate",  # SWR's mutate() (cache-only)
    "cache.read",
    "cache.write",
    "cache.evict",
    "cache.modify",  # Apollo
    "client.readquery",  # Apollo
    "client.writequery",  # Apollo
    "client.cache.evict",  # Apollo
}
_IN_MEMORY_LEAVES = {
    "setquerydata",
    "getquerydata",
    "setqueriesdata",
    "getqueriesdata",
    "invalidatequeries",
    "removequeries",
    "cancelqueries",
    "refetchqueries",
    "dispatch",
    "setstate",
    # M3 — generic Map/Set/WeakMap operations are NOT I/O even in loops.
    # Without these, `mapInst.get(k)` inside a loop falsely fires as N+1.
    "has",
    "set",
    "delete",
    "clear",
    "peek",
    # SWR + Apollo
    "readquery",
    "writequery",
    "writefragment",
    "readfragment",
    "evict",
    "modify",
}
_IN_MEMORY_RECEIVER_HINTS = {
    "queryclient",
    "qc",
    "store",
    "redux",
    "cache",
    "state",
    "router",
    # M3 expansion: more cache-library + native-collection receivers
    "map",
    "set",
    "weakmap",
    "weakset",
    "dict",
    "lookup",
    "registry",
    "client",  # Apollo client.cache.evict
    "session",
    "pinia",  # Vue 3 store
}
_IO_WRAPPER_NAMES = {
    "batch",
    "bulk",
    "migrate",
    "seed",
    "import",
    "export",
    "sync_all",
    "backfill",
    # Multi-database / multi-repo workspace patterns: each iteration
    # opens a *different* DB file, so the per-iter query cannot be
    # batched in SQL. Threadpool / async is the proper fix shape, not
    # WHERE IN (...). Suppress so users aren't told to "use bulk query"
    # on a fundamentally architectural per-database loop.
    "cross_repo",
    "per_repo",
    "across_repos",
    "multi_db",
    "federated",
    "for_each_repo",
}

# D body-level signals that the loop iterates CHUNKS, not
# individual items. When any of these patterns appear in the function body,
# the inner I/O calls operate on a batch (WHERE IN (...) form), not per-item,
# so it's not N+1. Caught a self-FP on roam's own `_symbol_context` which
# uses `for chunk in _chunked(symbol_ids):` then `WHERE symbol_id IN (...)`.
_BATCH_ITERATION_PATTERNS = re.compile(
    r"\bfor\s+\w+\s+in\s+(?:_?chunked|_?batched|chunks_of|batched_in|batch_iter|grouper)\b"
    r"|\bfor\s+\w+\s+in\s+range\s*\(\s*\d*\s*,\s*[^,]+,\s*(?:chunk|batch|page)_?size\b"
    r"|\bIN\s*\(\s*\{[^}]*\}\s*\)"  # f"WHERE id IN ({ph})" style
)


def _has_batch_iteration(snippet: str) -> bool:
    """Return True if the loop iterates chunks/batches rather than items."""
    if not snippet:
        return False
    return bool(_BATCH_ITERATION_PATTERNS.search(snippet))


@functools.lru_cache(maxsize=4096)
def _io_receiver_hint(call: str) -> str:
    if "." not in call:
        return ""
    return call.rsplit(".", 1)[0].lower()


def _io_receiver_is_ioish(call: str) -> bool:
    recv = _io_receiver_hint(call)
    if not recv:
        return False
    return any(h in recv for h in _IO_RECEIVER_HINTS)


# ---- D3: Framework profiles -----------------------------------------------
# Opt-in extras layered on top of the built-in cache allowlists when the user
# passes ``--framework FRAMEWORK`` to a detector command. Keeps defaults safe
# for arbitrary repos while letting users self-classify framework-specific
# helpers without forking the codebase.
_FRAMEWORK_PROFILES: dict[str, dict[str, set[str]]] = {
    # Django ORM-aware allowlist. `prefetch_related`,
    # `select_related`, `annotate`, `values_list`, `iterator` all belong
    # to QuerySet but are CHEAP cache/dispatch operations, not new I/O
    # per call. Without this, naive scans flag idiomatic Django code
    # (e.g. `for q in QuerySet.iterator(): ...`) as N+1.
    "django": {
        "in_memory_exact": {
            "queryset.iterator",
            "queryset.values_list",
            "queryset.values",
            "queryset.annotate",
            "queryset.prefetch_related",
            "queryset.select_related",
            "manager.iterator",
            "cache.get",
            "cache.set",
            "cache.delete",
        },
        "in_memory_leaves": {
            "iterator",
            "values_list",
            "annotate",
            "prefetch_related",
            "select_related",
            "only",
            "defer",
            "exists",
            "count",  # count is ONE query, not per-item
        },
        "in_memory_receivers": {
            "queryset",
            "qs",
            "manager",
            "cache",
            "objects",
        },
    },
    # Rails / ActiveRecord allowlist. Same shape:
    # `includes`, `joins`, `pluck`, `find_each`, `scope` are framework
    # primitives that don't trigger per-call I/O.
    "rails": {
        "in_memory_exact": {
            "rails.cache.read",
            "rails.cache.write",
            "rails.cache.delete",
        },
        "in_memory_leaves": {
            "includes",
            "joins",
            "preload",
            "eager_load",
            "pluck",
            "find_each",
            "in_batches",
            "scope",
            "where_values_hash",
        },
        "in_memory_receivers": {
            "scope",
            "relation",
            "association",
            "rails.cache",
            "active_support",
        },
    },
    # NestJS DI cache helpers (CacheModule, ConfigService
    # decorators) and TypeORM repository helpers that are not per-item I/O.
    "nestjs": {
        "in_memory_exact": {
            "configservice.get",
            "cachemanager.get",
            "cachemanager.set",
            "cachemanager.del",
        },
        "in_memory_leaves": {
            "createquerybuilder",
            "leftjoinandselect",
            "innerjoinandselect",
            "addselect",
            "addorderby",
            "addgroupby",
        },
        "in_memory_receivers": {
            "configservice",
            "cachemanager",
            "querybuilder",
            "qb",
            "repository",
        },
    },
    "vue3-tanstack": {
        # Vue 3 + TanStack Vue Query patterns observed in a Vue 3 + Laravel codebase FP batch.
        "in_memory_exact": {
            "queryclient.fetchquery",
            "queryclient.ensurequerydata",
            "queryclient.prefetchquery",
        },
        "in_memory_leaves": {
            "fetchquery",
            "ensurequerydata",
            "prefetchquery",
            "$reset",
            "$patch",
        },
        "in_memory_receivers": {
            "$query",
            "usequery",
            "usemutation",
            "useinfinitequery",
            "vuequery",
        },
    },
    "laravel-multitenant": {
        # stancl/tenancy + Laravel multi-DB patterns.
        "in_memory_exact": {
            "tenant.context",
            "tenancy.initialize",
        },
        "in_memory_leaves": {
            "throughtenant",
            "scopetenant",
            "viausertenant",
            "fortenant",
        },
        "in_memory_receivers": {
            "tenantmanager",
            "tenantservice",
            "tenantscoped",
            "tenancy",
        },
    },
}

_ACTIVE_FRAMEWORK_PROFILE: dict[str, set[str]] | None = None

# module-level flag for --include-tests. When True,
# `_is_test_path` returns False so detectors stop filtering out tests.
# Reset alongside the framework profile in run_detectors finally-block.
_INCLUDE_TESTS_OVERRIDE: bool = False


def list_framework_profiles() -> list[str]:
    """Return the names of bundled framework profiles."""
    return sorted(_FRAMEWORK_PROFILES.keys())


def _read_project_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Missing/unreadable/malformed project JSON is expected during
        # framework profiling — callers treat None as "no such manifest".
        return None
    return data if isinstance(data, dict) else None


def _read_project_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _package_dependencies(pkg: Mapping[str, Any]) -> dict[str, Any]:
    return {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}


def _detect_package_profile(cwd: str) -> str | None:
    pkg = _read_project_json(os.path.join(cwd, "package.json"))
    if not pkg:
        return None
    deps = _package_dependencies(pkg)
    if "@nestjs/core" in deps or "@nestjs/common" in deps:
        return "nestjs"
    vue_ver = str(deps.get("vue", ""))
    tanstack = "@tanstack/vue-query" in deps or "@tanstack/query-core" in deps
    if tanstack and vue_ver.startswith(("^3", "3", "~3")):
        return "vue3-tanstack"
    return None


def _detect_composer_profile(cwd: str) -> str | None:
    composer = _read_project_json(os.path.join(cwd, "composer.json"))
    if not composer:
        return None
    require = composer.get("require") or {}
    tenancy = "stancl/tenancy" in require or "spatie/laravel-multitenancy" in require
    if "laravel/framework" in require and tenancy:
        return "laravel-multitenant"
    return None


def _detect_python_profile(cwd: str) -> str | None:
    req_text = _read_project_text(os.path.join(cwd, "requirements.txt")).lower()
    pyp_text = _read_project_text(os.path.join(cwd, "pyproject.toml")).lower()
    if "django" in req_text or '"django' in pyp_text or "'django" in pyp_text:
        return "django"
    return None


def _detect_plugin_framework_profile(cwd: str) -> str | None:
    try:
        from pathlib import Path

        from roam.plugins import get_plugin_framework_detectors
    except Exception as err:
        log.warning(
            "framework-detector plugin discovery failed: %s: %s",
            type(err).__name__,
            err,
        )
        return None

    root = Path(cwd)
    for detect in get_plugin_framework_detectors():
        try:
            result = detect(root)
        except Exception as err:
            log.warning(
                "framework detector plugin %r raised %s: %s",
                getattr(detect, "__qualname__", repr(detect)),
                type(err).__name__,
                err,
            )
            continue
        if result:
            return str(result)
    return None


def autodetect_framework_profile() -> str | None:
    """sniff package.json / composer.json for known stacks.

    Inspects ``package.json`` and ``composer.json`` from the project root
    (current working directory) for the dependency signals that map onto
    a bundled profile:

    * ``vue3-tanstack`` — package.json depends on ``vue@3.x`` AND on
      ``@tanstack/vue-query`` (or ``@tanstack/query-core``).
    * ``laravel-multitenant`` — composer.json depends on ``laravel/framework``
      AND on ``stancl/tenancy`` or ``spatie/laravel-multitenancy``.

    Returns the profile name or None when no signal matches. Designed so
    a missing/unreadable manifest is silently None — never raises.
    """
    cwd = os.getcwd()
    for detect in (_detect_package_profile, _detect_composer_profile, _detect_python_profile):
        result = detect(cwd)
        if result:
            return result

    # Ruby/Rails detection lives in the ``roam-plugin-rails`` plugin
    # under ``dev/example-plugin/roam_plugin_rails`` (W28.2 Path A
    # clean-cut extraction). Core no longer ships a built-in
    # ``Gemfile`` check — load the plugin to restore detection::
    #
    #     PYTHONPATH=dev/example-plugin \
    #     ROAM_PLUGIN_MODULES=roam_plugin_rails \
    #     roam <subcommand>
    #
    # The ``rails`` *profile* (in-memory I/O allowlist for N+1) still
    # lives in ``_FRAMEWORK_PROFILES`` so ``--framework rails`` keeps
    # working even when the detector plugin is absent.

    # Plugin-contributed framework detectors. Built-ins win first.
    return _detect_plugin_framework_profile(cwd)


def set_active_framework_profile(name: str | None) -> dict[str, set[str]] | None:
    """Activate a framework profile for the current process.

    Returns the resolved profile dict, or ``None`` if the name is unknown.
    Callers wrap in try/finally to restore the previous profile.
    """
    global _ACTIVE_FRAMEWORK_PROFILE
    if not name:
        _ACTIVE_FRAMEWORK_PROFILE = None
        return None
    profile = _FRAMEWORK_PROFILES.get(name.lower())
    _ACTIVE_FRAMEWORK_PROFILE = profile
    return profile


def _framework_extras(key: str) -> set[str]:
    if not _ACTIVE_FRAMEWORK_PROFILE:
        return set()
    return _ACTIVE_FRAMEWORK_PROFILE.get(key) or set()


# `_io_is_known_in_memory_call` is hot: 12,226 calls
# during a single `roam math` on roam-code. The merged-set construction
# (`_IN_MEMORY_EXACT | _framework_extras(...)`) was running per-call.
# Cache results per (call, framework_id) so repeat call names short-
# circuit. The framework_id changes when the active profile changes,
# so cache keys naturally invalidate via the profile-state tuple.
_IN_MEMORY_CALL_CACHE: dict[tuple[str, int], bool] = {}


def _active_framework_id() -> int:
    return id(_ACTIVE_FRAMEWORK_PROFILE)


def _io_is_known_in_memory_call(call: str) -> bool:
    cache_key = (call, _active_framework_id())
    cached = _IN_MEMORY_CALL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    lower_c = call.lower()
    leaf = _call_leaf(lower_c)
    recv = _io_receiver_hint(lower_c)
    exact = _IN_MEMORY_EXACT | _framework_extras("in_memory_exact")
    leaves = _IN_MEMORY_LEAVES | _framework_extras("in_memory_leaves")
    receivers = _IN_MEMORY_RECEIVER_HINTS | _framework_extras("in_memory_receivers")
    if lower_c in exact:
        result = True
    elif leaf in leaves and any(h in recv for h in receivers):
        result = True
    else:
        result = False
    _IN_MEMORY_CALL_CACHE[cache_key] = result
    return result


def _is_call_awaited_in_snippet(call: str, snippet: str) -> bool:
    """D2 — cheap proxy for "Promise<T> vs T" return type.

    User suggested checking the call's actual return type to distinguish
    cache reads (sync, T) from I/O (async, Promise<T>). Without full type
    resolution, the next-best signal is: is the call preceded by ``await``
    in the function body? If yes, the call returns a Promise — meaningful
    I/O. If no, it's likely a sync cache read even when the leaf name
    overlaps a cache library identifier.

    Conservative match: looks for ``await <maybe-receiver>.leaf(`` on
    any snippet line. Misses cases where the awaited call is named via
    a variable; catches the common ``await fetchUser(id)`` pattern.
    """
    if not snippet or not call:
        return False
    leaf = _call_leaf(call) or call
    if not leaf:
        return False
    pattern = re.compile(rf"\bawait\s+(?:[\w$.]+\.)?{re.escape(leaf)}\s*\(")
    return bool(pattern.search(snippet))


# `_io_match_framework_pack` is invoked per-call inside
# `detect_io_in_loop` (12,226× on roam-code). The pack-set lookup is
# deterministic in (call, language) so cache by that tuple. The cache
# is reset alongside the file/in-memory caches at run_detectors entry.
_FRAMEWORK_PACK_CACHE: dict[tuple[str, str], dict | None] = {}


def _lower_string_set(values: Iterable[Any]) -> set[str]:
    return {str(v).lower() for v in values}


def _io_pack_matches(pack: dict, lower_call: str, leaf: str, receiver: str) -> bool:
    leaves = _lower_string_set(pack.get("leaves", set()))
    receiver_hints = _lower_string_set(pack.get("receiver_hints", set()))
    if lower_call in _lower_string_set(pack.get("exact", set())):
        return True
    if leaf not in leaves:
        return False
    return not receiver_hints or any(hint in receiver for hint in receiver_hints)


def _io_match_framework_pack(call: str, language: str | None) -> dict | None:
    cache_key = (call, language or "")
    if cache_key in _FRAMEWORK_PACK_CACHE:
        return _FRAMEWORK_PACK_CACHE[cache_key]
    leaf = _call_leaf(call).lower()
    recv = _io_receiver_hint(call)
    lower_c = call.lower()
    result: dict | None = None
    for pack in _framework_packs(language):
        if _io_pack_matches(pack, lower_c, leaf, recv):
            result = pack
            break
    _FRAMEWORK_PACK_CACHE[cache_key] = result
    return result


def _classify_known_in_memory_call(c: str, snippet: str | None) -> tuple[str | None, dict | None] | None:
    if not _io_is_known_in_memory_call(c):
        return None
    if snippet and _is_call_awaited_in_snippet(c, snippet):
        return "medium", None
    return None, None


def _classify_framework_io_call(c: str, language: str | None) -> tuple[str | None, dict | None] | None:
    pack = _io_match_framework_pack(c, language)
    if not pack:
        return None
    level = "high" if pack.get("confidence") == "high" else "medium"
    return level, pack


def _classify_named_io_call(c: str) -> str | None:
    lower_c = c.lower()
    leaf = _call_leaf(c).lower()
    if lower_c in _IO_HIGH_EXACT_LOWER:
        return "high"
    if leaf in _IO_HIGH_LEAF and _io_receiver_is_ioish(c):
        return "high"
    if leaf in {"get", "post", "put", "delete", "patch", "request"}:
        recv = _io_receiver_hint(c)
        if "requests" in recv or recv.endswith("http"):
            return "high"
    if leaf in _IO_MEDIUM_LEAF and _io_receiver_is_ioish(c):
        return "medium"
    if leaf == "open":
        return "medium"
    return None


def _is_local_ambiguous_helper(conn, r, leaf: str) -> bool:
    local_helper = conn.execute(
        "SELECT 1 FROM edges e "
        "JOIN symbols t ON e.target_id = t.id "
        "WHERE e.source_id = ? "
        "AND lower(t.name) = ? "
        "AND t.file_id = ? "
        "LIMIT 1",
        (r["id"], leaf, r["file_id"]),
    ).fetchone()
    return bool(local_helper)


def _classify_ambiguous_bare_call(c: str, conn, r) -> str | None:
    leaf = _call_leaf(c).lower()
    if "." in c or leaf not in _IO_AMBIGUOUS_BARE:
        return None
    if _is_local_ambiguous_helper(conn, r, leaf):
        return None
    return "ambiguous"


def _io_classify_call(
    c: str,
    language: str | None,
    conn,
    r,
    *,
    snippet: str | None = None,
) -> tuple[str | None, dict | None]:
    """Classify a single call inside a loop.

    Returns ``(level, framework_pack)`` where level is ``"high"``,
    ``"medium"``, ``"ambiguous"``, or None for in-memory / local-helper /
    non-I/O calls.

    when ``snippet`` is supplied, the cache allowlist
    is OVERRIDDEN if the call appears with ``await`` in the snippet.
    Reason: ``await cache.fetch(k)`` is a real I/O round trip even
    though the leaf matches a cache identifier; user feedback called
    out the Promise<T> vs T distinction explicitly.
    """
    # D2: if call name matches cache allowlist BUT is awaited in the body,
    # the await says it really IS asynchronous I/O — escalate to medium.
    memory_result = _classify_known_in_memory_call(c, snippet)
    if memory_result is not None:
        return memory_result
    framework_result = _classify_framework_io_call(c, language)
    if framework_result is not None:
        return framework_result
    named_level = _classify_named_io_call(c)
    if named_level:
        return named_level, None
    ambiguous_level = _classify_ambiguous_bare_call(c, conn, r)
    if ambiguous_level:
        return ambiguous_level, None
    return None, None


def _io_derived_match_line(
    match_line: int | None,
    snippet: str | None,
    calls: list[str],
    row,
) -> int | None:
    if match_line is not None or not snippet or not calls:
        return match_line
    first_call = calls[0]
    leaf = _call_leaf(first_call) or first_call
    if not leaf:
        return match_line
    for offset, line in enumerate(snippet.splitlines()):
        if leaf in line:
            return (row["line_start"] or 0) + offset
    return match_line


def _io_common_evidence_extras(dev_gated: bool) -> dict:
    if not dev_gated:
        return {}
    return {
        "dev_gated": True,
        "dev_gated_note": (
            "loop body sits inside a DEV-only conditional (import.meta.env.DEV / __DEV__ / "
            "process.env.NODE_ENV); production-stripped, so the N+1 cost is not paid in prod"
        ),
    }


def _io_matched_patterns(
    high_calls: list[str],
    medium_calls: list[str],
    ambiguous_calls: list[str],
    frameworks: set[str],
    guard_applies: bool,
    dev_gated: bool,
) -> list[str]:
    patterns: list[str] = []
    if high_calls:
        patterns.append(f"high-confidence I/O leaves ({len(high_calls)})")
    if medium_calls:
        patterns.append(f"medium-confidence I/O leaves ({len(medium_calls)})")
    if ambiguous_calls:
        patterns.append(f"ambiguous bare calls ({len(ambiguous_calls)})")
    if frameworks:
        patterns.append(f"framework pack: {', '.join(sorted(frameworks))}")
    if guard_applies:
        patterns.append("eager/batch guard nearby (confidence demoted)")
    if dev_gated:
        patterns.append("DEV-only gate (confidence demoted)")
    return patterns


def _io_level_reason_and_confidence(
    level: str,
    high_calls: list[str],
    medium_calls: list[str],
    ambiguous_calls: list[str],
    frameworks: set[str],
    guard_hints: list[str],
    guard_applies: bool,
) -> tuple[str, str]:
    calls_by_level = {
        "high": high_calls,
        "medium": medium_calls,
        "ambiguous": ambiguous_calls,
    }
    reason_calls = _dedupe(calls_by_level.get(level, ambiguous_calls))[:2]
    reason_suffix = f"; frameworks: {', '.join(sorted(frameworks))}" if frameworks else ""
    if level in {"high", "medium"} and guard_applies:
        reason_suffix += f"; eager/batch guards: {', '.join(guard_hints[:2])}"
    if level == "high":
        reason = f"I/O call ({', '.join(reason_calls)}) inside loop (N+1 pattern){reason_suffix}"
    elif level == "medium":
        reason = f"I/O-like call ({', '.join(reason_calls)}) inside loop (may be N+1){reason_suffix}"
    else:
        reason = f"Ambiguous bare call ({', '.join(reason_calls)}) inside loop (possible I/O, review manually)"
    confidence = {"high": "high", "medium": "medium"}.get(level, "low")
    if level in {"high", "medium"} and guard_applies:
        confidence = _lower_confidence(confidence)
    return reason, confidence


def _io_evidence(
    level: str,
    evidence_io_calls: list[str],
    ambiguous_calls: list[str],
    frameworks: set[str],
    guard_hints: list[str],
    common_extras: dict,
    suppress_hint: str,
) -> dict:
    evidence = {
        "io_calls": evidence_io_calls,
        "frameworks": sorted(frameworks),
        "guard_hints": guard_hints,
        "to_suppress": suppress_hint,
        **common_extras,
    }
    if level == "ambiguous":
        evidence["ambiguous_io_calls"] = _dedupe(ambiguous_calls)[:4]
        evidence["ambiguous_io_only"] = True
    return evidence


def _io_emit_finding(
    level: str,
    high_calls: list[str],
    medium_calls: list[str],
    ambiguous_calls: list[str],
    frameworks: set[str],
    fixes: set[str],
    guard_hints: list[str],
    guard_applies: bool,
    evidence_io_calls: list[str],
    r,
    *,
    dev_gated: bool = False,
    match_line: int | None = None,
    snippet: str | None = None,
) -> dict:
    """Build the finding dict for one of high/medium/ambiguous result branches.

    M1 (match_line): pin location at the first I/O call site in the snippet
    rather than the function declaration.
    M4 (dev_gated): note when the body sits inside a DEV gate.
    M6 (suppress hint): every emitted finding carries `to_suppress` evidence.
    """
    fix_text = "; ".join(sorted(fixes)) if fixes else None
    # M1: try to find the line of the first I/O call inside the snippet.
    all_calls = high_calls + medium_calls + ambiguous_calls
    derived_match_line = _io_derived_match_line(match_line, snippet, all_calls, r)
    common_evidence_extras = _io_common_evidence_extras(dev_gated)
    suppress_hint = (
        "wrap the loop in a batch/eager guard (e.g. `with()` or `map()`+`Promise.all`), OR "
        "add `# roam: ignore-math[io-in-loop]` on the function line if the call is intentional"
    )
    # assemble matched_patterns once for all branches.
    # Surfaces in `evidence.matched_patterns` so users see which classifier
    # branches contributed (high-leaf / framework-pack / ambiguous-bare /
    # dev-gated / batch-iteration). Quiet (empty list) when no signal.
    matched_patterns = _io_matched_patterns(
        high_calls,
        medium_calls,
        ambiguous_calls,
        frameworks,
        guard_applies,
        dev_gated,
    )
    reason, confidence = _io_level_reason_and_confidence(
        level,
        high_calls,
        medium_calls,
        ambiguous_calls,
        frameworks,
        guard_hints,
        guard_applies,
    )
    finding = _finding(
        "io-in-loop",
        "loop-query",
        r,
        reason,
        confidence,
        evidence=_io_evidence(
            level,
            evidence_io_calls,
            ambiguous_calls,
            frameworks,
            guard_hints,
            common_evidence_extras,
            suppress_hint,
        ),
        fix=fix_text,
        match_line=derived_match_line,
        snippet=snippet,
        matched_patterns=matched_patterns,
    )
    if level == "ambiguous":
        finding["precision"] = "low"
    return finding


@algorithm_detector(
    task_id="io-in-loop",
    languages=("python", "javascript", "typescript", "php", "ruby", "go", "java", "csharp"),
    confidence_basis="structural",
    query_cost=QUERY_COST_HIGH,
)
def detect_io_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """Database query, HTTP request, or file I/O inside a loop — N+1 pattern.

    One of the most impactful performance anti-patterns in web applications.
    Each iteration incurs a full I/O round trip.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "f.language as language, s.line_start, s.line_end, ms.loop_depth, ms.calls_in_loops, "
            "ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "f.language as language, s.line_start, s.line_end, ms.loop_depth, ms.calls_in_loops, "
            "'' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        language = _row_value(r, "language", "")
        snippet = _read_symbol_source(
            r["file_path"],
            _row_value(r, "line_start", None),
            _row_value(r, "line_end", None),
        )
        guard_hints = _guard_hints_from_source(language, snippet)
        calls = _iter_loop_calls(r)
        if not calls:
            continue

        high_calls, medium_calls, ambiguous_calls, frameworks, fixes = _classify_loop_call_buckets(
            calls, language, conn, r, snippet
        )

        if not high_calls and not medium_calls and not ambiguous_calls:
            continue

        name_lower = (r["name"] or "").lower()
        if any(kw in name_lower for kw in _IO_WRAPPER_NAMES):
            continue

        # D body-level: skip if the loop iterates chunks
        # / batches rather than items. Catches the canonical `for chunk in
        # _chunked(ids):` pattern where the inner query is WHERE IN (...).
        if _has_batch_iteration(snippet):
            continue

        # M4: DEV-gated bodies get demoted (not dropped) — a real
        # production-bound issue could still exist outside the gate, but it
        # sinks to the bottom of the verdict list.
        dev_gated = _is_dev_only_block(snippet)
        guard_applies = bool(frameworks and guard_hints)
        evidence_io_calls = _dedupe(high_calls + medium_calls + ambiguous_calls)[:6]
        level = _demote_dev_gated_level(_pick_io_level(high_calls, medium_calls), dev_gated)
        results.append(
            _io_emit_finding(
                level,
                high_calls,
                medium_calls,
                ambiguous_calls,
                frameworks,
                fixes,
                guard_hints,
                guard_applies,
                evidence_io_calls,
                r,
                dev_gated=dev_gated,
                snippet=snippet,
            )
        )
    return results


def _classify_loop_call_buckets(
    calls: list[str],
    language: str,
    conn: sqlite3.Connection,
    r,
    snippet: str,
) -> tuple[list[str], list[str], list[str], set[str], set[str]]:
    """Bin each loop call into tier buckets and collect framework/fix sets."""
    high_calls: list[str] = []
    medium_calls: list[str] = []
    ambiguous_calls: list[str] = []
    frameworks: set[str] = set()
    fixes: set[str] = set()
    for c in calls:
        level, pack = _io_classify_call(c, language, conn, r, snippet=snippet)
        if pack:
            frameworks.add(pack["framework"])
            if pack.get("fix"):
                fixes.add(pack["fix"])
        if level == "high":
            high_calls.append(c)
        elif level == "medium":
            medium_calls.append(c)
        elif level == "ambiguous":
            ambiguous_calls.append(c)
    return high_calls, medium_calls, ambiguous_calls, frameworks, fixes


def _pick_io_level(high_calls: list[str], medium_calls: list[str]) -> str:
    """Pick the dominant tier level from the per-tier bucket lists."""
    if high_calls:
        return "high"
    if medium_calls:
        return "medium"
    return "ambiguous"


def _demote_dev_gated_level(level: str, dev_gated: bool) -> str:
    """Demote one tier when the loop body is DEV-gated (production stripped)."""
    if not dev_gated:
        return level
    if level == "high":
        return "medium"
    if level == "medium":
        return "ambiguous"
    return level


_LIST_PREPEND_SHIFT_OPS = {"insert", "unshift", "shift"}
_LIST_PREPEND_DEQUE_OPS = {"popleft", "appendleft", "extendleft"}
_LIST_PREPEND_FRONT_OPS = _LIST_PREPEND_SHIFT_OPS | _LIST_PREPEND_DEQUE_OPS


def _list_prepend_finding_for_row(r: sqlite3.Row) -> dict | None:
    calls = _iter_loop_calls(r)
    front_calls = _call_in(calls, _LIST_PREPEND_FRONT_OPS)
    if front_calls and all(_call_leaf(c) in _LIST_PREPEND_DEQUE_OPS for c in front_calls):
        return None

    if _row_value(r, "front_ops_in_loop", None) == 1:
        return _finding(
            "list-prepend",
            "insert-front",
            r,
            "Front insert/remove inside loop (O(n) shift per operation)",
            "high",
        )

    if not _call_in(calls, _LIST_PREPEND_SHIFT_OPS):
        return None

    return _finding(
        "list-prepend",
        "insert-front",
        r,
        "Potential front insert/remove inside loop",
        "medium",
    )


@algorithm_detector(
    task_id="list-prepend",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_list_prepend(conn: sqlite3.Connection) -> list[dict]:
    """insert(0, x), unshift(), or pop(0) inside a loop — O(n) per op
    due to array shifting, O(n^2) total."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.front_ops_in_loop, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.front_ops_in_loop = 1"
        ).fetchall()
    except sqlite3.Error:
        # Fallback for older indexes without front_ops_in_loop.
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        finding = _list_prepend_finding_for_row(r)
        if finding:
            results.append(finding)
    return results


@algorithm_detector(
    task_id="sort-to-select",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_HIGH,
)
def detect_sort_to_select(conn: sqlite3.Connection) -> list[dict]:
    """Calling sort on a collection and then only using the first/last element.

    O(n log n) sort when min()/max() is O(n), or heapq.nsmallest is O(n log k).
    Detection: function calls sort AND has subscript access, but does NOT
    iterate the full sorted result.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, s.line_end "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()

    sorted_index_re = re.compile(r"\bsorted\s*\([^)]*\)\s*\[\s*(?:-?\d+|:\s*[^]\n]+)\s*\]")
    inplace_sort_index_re = re.compile(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*sort\s*\([^)]*\).*?"
        r"\b\1\s*\[\s*(?:-?\d+|:\s*[^]\n]+)\s*\]",
        re.DOTALL,
    )
    generic_sort_index_re = re.compile(
        r"\bsort(?:ed)?\s*\([^)]*\).*?\[\s*(?:-?\d+|:\s*[^]\n]+)\s*\]",
        re.DOTALL,
    )
    # M1: per-line sort detector for pinpointing the match line
    sort_call_line_re = re.compile(r"\bsort(?:ed)?\s*\(")

    # M5 — fallback false-positive guard. Skip when the sort result is also
    # iterated/returned in full (sorted then map/forEach/return — display order,
    # not a min/max selection).
    # The check is conservative: if we see ANY iteration of the sort target
    # alongside the index access, demote the finding rather than skip outright.
    iteration_after_sort_re = re.compile(r"\bsort(?:ed)?\s*\(.*?\b(map|forEach|filter|reduce|return)\b", re.DOTALL)

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue

        match_line = _find_match_line(snippet, sort_call_line_re, r["line_start"])
        sort_iterated = bool(iteration_after_sort_re.search(snippet))

        # Strong patterns: sorted(...)[0], sorted(... )[:k], arr.sort(); arr[0]
        if sorted_index_re.search(snippet) or inplace_sort_index_re.search(snippet):
            # M5: when the sort result is also iterated, downgrade — the
            # subscript may be incidental (e.g. logging the first item of a
            # display-ordered list).
            confidence = "medium" if sort_iterated else "high"
            reason = "Sort used only for first/last/top-k selection"
            if sort_iterated:
                reason += " (note: result is also iterated — may be incidental subscript)"
            results.append(
                _finding(
                    "sort-to-select",
                    "full-sort",
                    r,
                    reason,
                    confidence,
                    match_line=match_line,
                    snippet=snippet,
                )
            )
            continue

        # Fallback pattern for other languages (sort(...) then index/slice).
        if generic_sort_index_re.search(snippet):
            confidence = "low" if sort_iterated else "medium"
            reason = "Potential full sort followed by index/slice selection"
            if sort_iterated:
                reason += " (note: result is also iterated — may be incidental subscript)"
            results.append(
                _finding(
                    "sort-to-select",
                    "full-sort",
                    r,
                    reason,
                    confidence,
                    match_line=match_line,
                    snippet=snippet,
                )
            )
    return results


# JS/TS-specific: serial-await inside a for-of loop is a
# Promise.all opportunity. Each `await` round-trips before the next call
# starts, so 100 awaits = 100x the latency of one. The fix is
# `await Promise.all(items.map(item => fetch(item)))`. Distinct from
# io-in-loop because the per-item call may not match a known I/O leaf
# (custom async helpers); the `await` itself is the signal.
_RE_FOR_OF = re.compile(
    r"\bfor\s*(?:await\s*)?\s*\(\s*(?:const|let|var)\s+\w+\s+of\s+",
)
_RE_AWAIT_IN_BODY = re.compile(r"\bawait\s+[\w$.]+\s*\(")


@algorithm_detector(
    task_id="serial-await-loop",
    languages=("javascript", "typescript"),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_serial_await_loop(conn: sqlite3.Connection) -> list[dict]:
    """`for (... of ...) { await x() }` — serial-await N+1 (Promise.all opportunity).

    Only fires for JS/TS. Two signals:
    1. The function body contains a `for ... of ...` (or `for await ... of`) header.
    2. The body has at least one `await call()` line.
    Skips when body uses `Promise.all`/`Promise.allSettled` (already batched).
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
        "f.language AS language, s.line_start, s.line_end, ms.loop_depth "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND f.language IN " + _JS_FAMILY_SQL_TUPLE + ""
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(
            r["file_path"],
            _row_value(r, "line_start", None),
            _row_value(r, "line_end", None),
        )
        if not snippet:
            continue
        if not _RE_FOR_OF.search(snippet):
            continue
        if not _RE_AWAIT_IN_BODY.search(snippet):
            continue
        # Already-batched: Promise.all / Promise.allSettled in the body.
        if "Promise.all" in snippet:
            continue
        # Skip benign awaited identifiers that aren't I/O (await timeout(0),
        # await Promise.resolve, await new Promise(...)). Conservative: only
        # fire when the awaited callee is a method/function name on a value,
        # not a literal Promise constructor call.
        if not re.search(r"\bawait\s+(?!new\s+Promise|Promise\.|setTimeout|setImmediate)[\w$.]+\s*\(", snippet):
            continue
        results.append(
            _finding(
                "serial-await-loop",
                "for-of-await",
                r,
                "for-of loop with serial `await` — each iteration waits for the previous "
                "(use Promise.all for parallel I/O)",
                "high",
                snippet=snippet,
                matched_patterns=["for-of header", "await-call in body", "no Promise.all batch"],
            )
        )
    return results


# Python-specific: `time.sleep()` inside an async function
# blocks the event loop. The async cousin is `asyncio.sleep` (or trio.sleep).
# This is one of the most common asyncio bugs and dramatically degrades
# request throughput in async web servers (FastAPI, aiohttp, Sanic).
@algorithm_detector(
    task_id="async-blocking-sleep",
    languages=("python", "javascript", "typescript"),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_async_blocking_sleep(conn: sqlite3.Connection) -> list[dict]:
    """`time.sleep()` (or other blocking calls) inside an async function.

    Async functions must use ``await asyncio.sleep(n)`` instead — otherwise
    the entire event loop stalls. Same applies to other blocking I/O calls
    (``requests.get``, ``urllib.urlopen``, sync DB drivers).

    Conservative: only fires for Python (the bug shape is language-specific)
    and only when ``is_async`` is set on the symbol row.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end, ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND s.is_async = 1 "
            "AND f.language = 'python'"
        ).fetchall()
    except sqlite3.Error:
        return []

    _BLOCKING_CALLS = {
        "time.sleep",
        "sleep",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.patch",
        "urllib.request.urlopen",
        "urlopen",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_output",
    }
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Combine loop calls with body scan: blocking calls anywhere in the
        # async body (not just inside loops) are still bugs.
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"]) or ""
        loop_calls = _iter_loop_calls(r)
        body_blocking: list[str] = []
        for call in loop_calls:
            if call.lower() in _BLOCKING_CALLS or _call_leaf(call).lower() in {"sleep", "urlopen"}:
                body_blocking.append(call)
        # Cheap snippet sweep to catch out-of-loop occurrences.
        if not body_blocking:
            for needle in ("time.sleep(", "requests.get(", "requests.post(", "urlopen(", "subprocess.run("):
                if needle in snippet:
                    body_blocking.append(needle.rstrip("("))
                    break
        if not body_blocking:
            continue
        # The await heuristic — if the snippet already awaits the same leaf,
        # it's the async cousin (await asyncio.sleep), so skip.
        if "await asyncio.sleep" in snippet or "await trio.sleep" in snippet:
            # Could still have a stray `time.sleep` on another line; only
            # skip when no blocking-call needles remained after that filter.
            if not any(c.startswith("time.sleep") or c == "time.sleep" for c in body_blocking):
                continue
        results.append(
            _finding(
                "async-blocking-sleep",
                "blocking-call-in-async",
                r,
                f"Blocking call ({', '.join(body_blocking[:2])}) inside async function — blocks the event loop",
                "high",
                snippet=snippet,
                matched_patterns=[
                    "is_async = 1",
                    f"blocking calls: {', '.join(body_blocking[:3])}",
                ],
            )
        )
    return results


# Python: `except Exception:` / `except BaseException:`
# without `raise` is the canonical "swallowing bug" pattern. Catches
# KeyboardInterrupt, MemoryError, SystemExit silently. The fix is to
# narrow the exception type or always re-raise after logging.
_RE_BROAD_EXCEPT = re.compile(
    r"^\s*except\s+(?:Exception|BaseException)\s*(?:as\s+\w+\s*)?:",
    re.MULTILINE,
)
_RE_RERAISE = re.compile(r"^\s*raise(?:\s|$)", re.MULTILINE)


@algorithm_detector(
    task_id="broad-except-swallow",
    languages=("python",),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_broad_except_swallow(conn: sqlite3.Connection) -> list[dict]:
    """Python: `except Exception:` block that silently swallows the error.

    Fires when the body has NO `raise` statement — meaning the error is
    caught, possibly logged, and the program continues as if nothing
    happened. That hides real bugs and masks operational failures.

    Skip cases:
    - Re-raise present (intentional log-and-rethrow)
    - Function name suggests it's an error-recovery wrapper
      (`safe_*`, `_try_*`, `with_default`, `silent_*`).
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND f.language = 'python'"
        ).fetchall()
    except sqlite3.Error:
        return []

    _RECOVERY_PREFIXES = (
        "safe_",
        "_safe_",
        "try_",
        "_try_",
        "with_default_",
        "silent_",
        "_silent_",
        "noexcept_",
    )
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        name_lower = (r["name"] or "").lower()
        if any(name_lower.startswith(p) for p in _RECOVERY_PREFIXES):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue
        broad_match = _RE_BROAD_EXCEPT.search(snippet)
        if not broad_match:
            continue
        # If the function body re-raises somewhere AFTER the broad except,
        # treat it as intentional log-and-rethrow.
        post = snippet[broad_match.end() :]
        if _RE_RERAISE.search(post):
            continue
        # Find the matched line number for precise location reporting.
        line_offset = snippet[: broad_match.start()].count("\n")
        match_line = (r["line_start"] or 1) + line_offset
        results.append(
            _finding(
                "broad-except-swallow",
                "swallow-exception",
                r,
                "`except Exception:` without re-raise — silently swallows bugs",
                "medium",
                match_line=match_line,
                snippet=snippet,
                matched_patterns=[
                    "broad except clause",
                    "no re-raise in body",
                    "function name not in recovery prefix list",
                ],
            )
        )
    return results


# React: `useEffect(() => { ... })` without a dependency
# array runs on EVERY render. Almost always a bug — the dev forgot the
# second argument. The fix is `useEffect(() => { ... }, [deps])` or
# `useEffect(() => { ... }, [])` for mount-only.
_RE_USEEFFECT_NO_DEPS = re.compile(
    r"\buseEffect\s*\(\s*(?:async\s+)?(?:\(\s*\)|function\s*\(\s*\))\s*=>\s*\{[^}]*?\}\s*\)",
    re.DOTALL,
)
_RE_USEEFFECT_WITH_DEPS = re.compile(
    r"\buseEffect\s*\(\s*[^,]+,\s*\[",
)


@algorithm_detector(
    task_id="useeffect-missing-deps",
    languages=("javascript", "typescript"),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_useeffect_missing_deps(conn: sqlite3.Connection) -> list[dict]:
    """React: `useEffect(() => {...})` without a dependency array."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND f.language IN " + _JS_FAMILY_SQL_TUPLE + ""
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet or "useEffect" not in snippet:
            continue
        # Skip if every useEffect call also has a deps array.
        no_deps = _RE_USEEFFECT_NO_DEPS.search(snippet)
        if not no_deps:
            continue
        # Verify the SAME call doesn't have a deps array (regex catches the
        # no-deps shape but a more elaborate body could trick it).
        # Conservative: only fire when there's NO useEffect-with-deps in body.
        if _RE_USEEFFECT_WITH_DEPS.search(snippet):
            # Mixed bag — at least one useEffect has deps, can't reliably
            # tell which one is the problem without a real parser. Skip.
            continue
        line_offset = snippet[: no_deps.start()].count("\n")
        match_line = (r["line_start"] or 1) + line_offset
        results.append(
            _finding(
                "useeffect-missing-deps",
                "no-deps-array",
                r,
                "useEffect without dependency array — runs on every render",
                "high",
                match_line=match_line,
                snippet=snippet,
                matched_patterns=["useEffect call", "no second-arg dep array"],
            )
        )
    return results


# `eval()` / `exec()` / `new Function()` / `setTimeout(string)`
# in production source. These are arbitrary code execution sinks if the
# input is user-derived. Even when "safe" (literal string), they trip
# CSP rules and bundler optimisations. Suppress when test path.
_RE_EVAL_CALLS = re.compile(
    r"\b(?:eval|exec|execfile|compile)\s*\("
    r"|\bnew\s+Function\s*\("
    r"|\bsetTimeout\s*\(\s*['\"]"
    r"|\bsetInterval\s*\(\s*['\"]",
)
# Declaration-line skip: `function exec(...)` / `def exec(...)` defines a
# user function named like a sink — it is a definition, not a call site.
_RE_EVAL_DECL_LINE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def)\s+(?:eval|exec|execfile|compile)\b")
# Receiver-qualified shell-exec that IS a genuine sink even via a dot —
# keep these firing through the `.exec(` receiver guard below.
_RE_SHELL_EXEC_RECEIVER = re.compile(r"(?:child_process|cp)\s*\.\s*exec", re.IGNORECASE)


def _dangerous_eval_match_is_executable_code(snippet: str, match: re.Match[str]) -> bool:
    """Return true when the regex hit sits outside comments and string text."""
    line_start = snippet.rfind("\n", 0, match.start()) + 1
    line_end = snippet.find("\n", match.start())
    line = snippet[line_start : (line_end if line_end != -1 else len(snippet))]
    match_column = match.start() - line_start
    quote: str | None = None
    escaped = False
    i = 0
    while i < match_column:
        ch = line[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if quote:
            if ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if line.startswith(("//", "/*"), i) or ch == "#":
            return False
        if ch in {"'", '"', "`"}:
            quote = ch
        i += 1
    return quote is None


def _is_dangerous_eval_false_positive(snippet: str, match: re.Match[str]) -> bool:
    """Keep dangerous-eval findings on executable sinks, not safe APIs or text."""
    if not _dangerous_eval_match_is_executable_code(snippet, match):
        return True
    # Skip ast.literal_eval (safe), regex.compile (different "compile"
    # — won't actually match because it ends in `.compile(` with a dot
    # before, so prefix isn't word-boundary — but be defensive).
    if "literal_eval" in snippet[max(0, match.start() - 20) : match.end()]:
        return True
    if ".compile(" in snippet[max(0, match.start() - 5) : match.end() + 1]:
        return True
    # `<regex>.exec(x)` / `<str>.exec(x)` is the standard JS/TS regex API,
    # NOT a code-injection sink — symmetric to the `.compile(` guard above.
    # Keep genuine shell-exec receivers (`child_process.exec`, `cp.exec`)
    # firing; suppress every other dotted `.exec(`.
    exec_window = snippet[max(0, match.start() - 30) : match.end() + 1]
    if ".exec(" in snippet[max(0, match.start() - 5) : match.end() + 1] and not (
        _RE_SHELL_EXEC_RECEIVER.search(exec_window)
    ):
        return True
    # Skip declaration lines: `function exec(...)` / `def exec(...)` is a
    # definition of a sink-named wrapper, not a call to a dynamic-exec sink.
    line_start = snippet.rfind("\n", 0, match.start()) + 1
    line_end = snippet.find("\n", match.start())
    match_line_text = snippet[line_start : (line_end if line_end != -1 else len(snippet))]
    return bool(_RE_EVAL_DECL_LINE.match(match_line_text))


@algorithm_detector(
    task_id="dangerous-eval",
    languages=("python", "javascript", "typescript", "php", "ruby"),
    confidence_basis="static_analysis",
    query_cost=QUERY_COST_LOW,
)
def detect_dangerous_eval(conn: sqlite3.Connection) -> list[dict]:
    """Detect `eval`, `exec`, `new Function(...)`, `setTimeout(string)` — code-injection sinks."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "f.language AS language, s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method')"
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Skip non-source roles when we can tell.
        path = (r["file_path"] or "").replace("\\", "/").lower()
        if "/migration" in path or "/script" in path or "/cli" in path:
            # CLI / migration scripts often legitimately use exec/eval.
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue
        m = next(
            (
                candidate
                for candidate in _RE_EVAL_CALLS.finditer(snippet)
                if not _is_dangerous_eval_false_positive(snippet, candidate)
            ),
            None,
        )
        if m is None:
            continue
        line_offset = snippet[: m.start()].count("\n")
        match_line = (r["line_start"] or 1) + line_offset
        called = m.group(0).rstrip("(")
        results.append(
            _finding(
                "dangerous-eval",
                "eval-or-exec",
                r,
                f"Dangerous dynamic execution sink ({called}) — code-injection risk if input is user-derived",
                "high",
                match_line=match_line,
                snippet=snippet,
                matched_patterns=[f"call: {called}", "not in test/migration/script path"],
            )
        )
    return results


# JS/TS DOM listener leak: `addEventListener` without
# a paired `removeEventListener` keeps references alive after the
# component unmounts. Detect ONLY when the function looks like a
# component lifecycle (useEffect / componentDidMount / connectedCallback /
# constructor) and addEventListener appears without remove.
_RE_ADD_LISTENER = re.compile(r"\baddEventListener\s*\(")
_RE_REMOVE_LISTENER = re.compile(r"\bremoveEventListener\s*\(")
_RE_LIFECYCLE = re.compile(
    r"\b(?:useEffect|componentDidMount|componentWillMount|connectedCallback|constructor)\b",
)


@algorithm_detector(
    task_id="unremoved-event-listener",
    languages=JS_FAMILY_LANGUAGES,
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_unremoved_event_listener(conn: sqlite3.Connection) -> list[dict]:
    """JS/TS: `addEventListener` in a lifecycle without paired `removeEventListener`."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND f.language IN " + _JS_FAMILY_SQL_TUPLE + ""
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue
        if not _RE_ADD_LISTENER.search(snippet):
            continue
        # Only fire for lifecycle-ish bodies — outside of components,
        # listeners are often global and intentionally never removed.
        if not _RE_LIFECYCLE.search(snippet):
            continue
        # Already paired: presence of removeEventListener anywhere in body.
        if _RE_REMOVE_LISTENER.search(snippet):
            continue
        # `useEffect` should also return a cleanup function. Check for one.
        if "useEffect" in snippet and re.search(r"return\s+(?:\(\s*\)|function)", snippet):
            continue
        m = _RE_ADD_LISTENER.search(snippet)
        line_offset = snippet[: m.start()].count("\n")
        match_line = (r["line_start"] or 1) + line_offset
        results.append(
            _finding(
                "unremoved-event-listener",
                "no-cleanup",
                r,
                "addEventListener in component lifecycle without removeEventListener — memory leak",
                "high",
                match_line=match_line,
                snippet=snippet,
                matched_patterns=[
                    "addEventListener call",
                    "lifecycle context (useEffect / componentDidMount / etc.)",
                    "no paired removeEventListener",
                ],
            )
        )
    return results


@algorithm_detector(
    task_id="loop-lookup",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_loop_lookup(conn: sqlite3.Connection) -> list[dict]:
    """.index(), .indexOf(), .contains(), .includes() called inside a loop.

    Each call is O(m) linear scan on the lookup collection, total O(n*m).
    Pre-building a set gives O(1) per lookup, O(n+m) total.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_lookup_calls, ms.calls_in_loops, "
            "ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, '' as loop_lookup_calls, ms.calls_in_loops, "
            "'' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    _LOOKUP_CALLS = {
        "index",
        "indexOf",
        "lastIndexOf",
        "contains",
        "includes",
        "Contains",
        "IndexOf",
    }

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        lookup_calls = _json_list(_row_value(r, "loop_lookup_calls", ""))
        if lookup_calls:
            results.append(
                _finding(
                    "loop-lookup",
                    "method-scan",
                    r,
                    f"Linear lookup ({', '.join(lookup_calls[:2])}) called on invariant collection",
                    "high",
                )
            )
            continue
        # Fallback for older indexes: conservative matching only.
        calls = _iter_loop_calls(r)
        fallback_hits = _call_in(calls, _LOOKUP_CALLS)
        if fallback_hits:
            results.append(
                _finding(
                    "loop-lookup",
                    "method-scan",
                    r,
                    f"Linear lookup ({', '.join(fallback_hits[:2])}) called inside loop",
                    "low",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Tier 2 detectors: enhanced signals
# ---------------------------------------------------------------------------


@algorithm_detector(
    task_id="branching-recursion",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_branching_recursion(conn: sqlite3.Connection) -> list[dict]:
    """Functions with 2+ self-call sites and no memoization.

    Generalizes fibonacci to any branching recursion: tree traversals,
    divide-and-conquer, DP problems.  O(2^n) -> O(n) with memoization.
    """
    # self_call_count column may not exist in older DBs — fall back safely
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "f.language as language, s.line_start, s.line_end, ms.self_call_count "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.self_call_count >= 2"
        ).fetchall()
    except sqlite3.Error:
        return []

    results = []

    # M2 fix: detect the SECOND form of depth guard — early-return when
    # depth EXCEEDS limit ("if depth > 10 return"). The original regex only
    # recognised "depth < limit ⇒ continue" patterns; the negation form was
    # silently mis-flagged as O(2^n). Real-world FP: deepEqual flagged
    # despite line+2 having `if (depth > 10) return false`.
    # pre-compile the depth-guard / memo-collection
    # regexes once. Previously each `re.search(literal, snippet)` call
    # rebuilt the pattern object via the implicit re._cache (capped at
    # 512 entries), which can evict on a busy run. Hoisting them keeps
    # the patterns hot.
    _SPLIT_LENGTH_GUARD = re.compile(r"\.split\s*\([^)]*\)\s*\.\s*length\s*(?:<|<=)\s*\d+")
    _LEN_SPLIT_GUARD = re.compile(r"len\s*\(\s*[^)]*\.split\s*\([^)]*\)\s*\)\s*(?:<|<=)\s*\d+")
    _DEPTH_BELOW_GUARD = re.compile(
        r"\b(?:depth|level|budget|remaining|hops|currentDepth|current_depth|max_depth|maxDepth)\b"
        r"\s*(?:<|<=)\s*(?:\d+|maxDepth|max_depth|MAX_DEPTH|max_recursion|MAX_RECURSION)",
    )
    _DEPTH_EXCEEDS_GUARD = re.compile(
        r"\b(?:depth|level|budget|remaining|hops|recursion_count|recursionDepth)\b"
        r"\s*(?:>|>=|<|<=)\s*(?:\d+|maxDepth|max_depth|MAX_DEPTH|max_recursion|MAX_RECURSION|0)\s*\)?\s*"
        r"\s*[:{]?\s*(?:return|raise|throw|break)",
    )
    _DEC_THEN_CHECK_GUARD = re.compile(
        r"--?\s*\b(?:depth|budget|remaining|hops)\b\s*[<>]=?\s*\d+\s*\)?\s*[:{]?\s*"
        r"(?:return|raise|throw|break)",
    )
    _MAXDEPTH_VAR_GUARD = re.compile(
        r"\b(?:maxDepth|max_depth)\b\s*(?:>|>=)\s*\b(?:depth|level|currentDepth|current_depth)\b",
    )
    _MEMO_COLLECTION_GENERIC = re.compile(r"\b(?:Set|Map|WeakSet|WeakMap)\s*<")
    _MEMO_COLLECTION_LITERAL = re.compile(r"\bnew\s+(?:Set|Map|WeakSet|WeakMap)\b")
    _MEMO_VAR_NAME = re.compile(r"\b(?:visited|seen|memo|cache|memoised|memoized)\b\s*[:=]")
    _MEMO_DECORATOR = re.compile(r"@(?:lru_cache|cache|memoize|memoise|functools\.lru_cache)\b")

    def _has_explicit_depth_guard(language: str | None, snippet: str) -> bool:
        """Return True for bounded-recursion guards that cap traversal depth."""
        if not snippet:
            return False
        if _SPLIT_LENGTH_GUARD.search(snippet):  # JS/TS: path.split('.').length < 5
            return True
        if _LEN_SPLIT_GUARD.search(snippet):  # Python: len(path.split(".")) < 5
            return True
        if _DEPTH_BELOW_GUARD.search(snippet):  # Form 1: depth < limit
            return True
        # M2 — Form 2: early-return if depth/level/budget EXCEEDS limit.
        if _DEPTH_EXCEEDS_GUARD.search(snippet):
            return True
        if _DEC_THEN_CHECK_GUARD.search(snippet):  # `if (--budget <= 0) return`
            return True
        if language and language.lower() in {"javascript", "typescript"} and _MAXDEPTH_VAR_GUARD.search(snippet):
            return True
        return False

    # M2 — Set/Map/WeakSet/WeakMap parameter or local: signals the function
    # already implements its own memoization / cycle-tracking, so the
    # branching-recursion warning would be a FP.
    def _has_memo_collection(snippet: str) -> bool:
        if not snippet:
            return False
        if _MEMO_COLLECTION_GENERIC.search(snippet):  # TS generic
            return True
        if _MEMO_COLLECTION_LITERAL.search(snippet):  # JS literal
            return True
        if _MEMO_VAR_NAME.search(snippet):
            return True
        if _MEMO_DECORATOR.search(snippet):
            return True
        return False

    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Skip fibonacci — already covered by detect_naive_fibonacci
        name_lower = (r["name"] or "").lower()
        if "fib" in name_lower:
            continue
        # Skip tree/AST walkers — recursive traversal of children is
        # intentional and doesn't have overlapping subproblems
        _WALKER_NAMES = {
            "walk",
            "visit",
            "traverse",
            "search",
            "scan",
            "crawl",
            "descend",
            "recurse",
            "dfs",
            "bfs",
        }
        if any(kw in name_lower for kw in _WALKER_NAMES):
            continue
        # Check for memoization edge
        memo_edge = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ? "
            "AND t.name IN ('lru_cache', 'cache', 'memoize', 'memo', "
            "               'functools.lru_cache', 'functools.cache') "
            "LIMIT 1",
            (r["id"],),
        ).fetchone()
        if memo_edge:
            continue
        snippet = _read_symbol_source(
            r["file_path"],
            _row_value(r, "line_start", None),
            _row_value(r, "line_end", None),
        )
        if _has_explicit_depth_guard(_row_value(r, "language", ""), snippet):
            continue
        if _has_memo_collection(snippet):
            # M2: function carries its own Set/Map/WeakSet — already memoised.
            continue
        # M1: pin the location at the first self-call line if we can find it,
        # not the function declaration. The detector flagged because
        # self_call_count >= 2; the first occurrence of `name(` (or shorthand
        # `name(`) inside the body is the most informative anchor.
        match_line = _find_first_keyword_line(snippet, (r["name"] + "(",), r["line_start"])
        results.append(
            _finding(
                "branching-recursion",
                "naive-branching",
                r,
                f"Branching recursion ({r['self_call_count']} self-calls) without memoization",
                "high",
                evidence={
                    "self_call_count": r["self_call_count"],
                    "guard_check": "no depth/budget guard found in body, no memo Set/Map detected",
                    "to_suppress": (
                        "add `if (depth > N) return` early-return guard, OR pass a "
                        "Set/Map/WeakSet for memoisation/cycle-tracking, OR add "
                        "`# roam: ignore-math[branching-recursion]` to the function line"
                    ),
                },
                match_line=match_line,
                snippet=snippet,
            )
        )
    return results


@algorithm_detector(
    task_id="quadratic-string",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_LOW,
)
def detect_quadratic_string(conn: sqlite3.Connection) -> list[dict]:
    """String concatenation via += inside a loop — O(n^2) due to
    immutable string reallocation in Python/Java/Go.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.str_concat_in_loop "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.str_concat_in_loop = 1"
        ).fetchall()
    except sqlite3.Error:
        return []

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        results.append(
            _finding(
                "quadratic-string",
                "augment-concat",
                r,
                "String += in loop (O(n^2) due to immutable reallocation)",
                "high",
            )
        )
    return results


_RE_DEFER_IN_LOOP_BODY = re.compile(
    r"\b(?:for|range)\b[^{]*\{[^}]*\bdefer\b\s+[\w$.]+",
    re.DOTALL,
)

_RE_FILTER_FIND = re.compile(
    r"\.\s*filter\s*\([^)]*\)\s*\.\s*find\s*\(",
)

_RE_FILTER_LENGTH_BOOL = re.compile(
    r"\.\s*filter\s*\([^)]*\)\s*\.\s*length\s*(?:>\s*0|>=\s*1|!==?\s*0)",
)

_RE_MAP_FIND = re.compile(
    r"\.\s*map\s*\([^)]*\)\s*\.\s*find\s*\(",
)


@algorithm_detector(
    task_id="loop-invariant-call",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_loop_invariant_call(conn: sqlite3.Connection) -> list[dict]:
    """Calls inside loops whose arguments don't reference the loop variable.

    These can be hoisted before the loop to avoid repeated computation.
    Suppresses common intentional per-iteration calls (logging, metrics, etc.).
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_invariant_calls "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_invariant_calls IS NOT NULL "
            "AND ms.loop_invariant_calls != '[]'"
        ).fetchall()
    except sqlite3.Error:
        return []

    # Calls that are intentionally per-iteration (suppress)
    _INTENTIONAL_CALLS = {
        # Logging / output
        "print",
        "log",
        "debug",
        "info",
        "warn",
        "warning",
        "error",
        # Collection mutation
        "append",
        "add",
        "push",
        "extend",
        "write",
        "send",
        "get",
        "values",
        "items",
        "keys",
        "update",
        "pop",
        "remove",
        "insert",
        "setdefault",
        "discard",
        # String methods (inherently per-item)
        "startswith",
        "endswith",
        "replace",
        "format",
        "strip",
        "split",
        "join",
        "lower",
        "upper",
        "lstrip",
        "rstrip",
        "encode",
        "decode",
        "ljust",
        "rjust",
        "center",
        "zfill",
        # Event / tracking
        "emit",
        "track",
        "record",
        "increment",
        "decrement",
        # Iteration helpers / builtins
        "enumerate",
        "zip",
        "range",
        "sorted",
        "reversed",
        "list",
        "dict",
        "set",
        "tuple",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "type",
        # Math / comparison builtins (per-item reductions)
        "max",
        "min",
        "sum",
        "abs",
        "round",
        "pow",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        # File / IO
        "resolve",
        "execute",
        "fetchone",
        "fetchall",
        "read_text",
        "read_bytes",
        "open",
        # Control flow
        "sleep",
        "yield",
    }

    # heavyweight calls that are especially worth
    # hoisting (parsing serialised data is O(n^2) over loop iterations
    # when the input doesn't change). When ANY of these fire, escalate
    # confidence to "high".
    _HEAVYWEIGHT_PARSE_LEAVES = {
        "loads",  # json.loads, yaml.load (sometimes), pickle.loads
        "load",  # yaml.load, pickle.load
        "parse",  # JSON.parse, dateutil.parser.parse
        "parsestring",  # JSON.parseString
        "fromstring",  # ET.fromstring (XML)
        "deserialize",
        "decode",
        "compile",  # re.compile inside loop
    }
    _HEAVYWEIGHT_PARSE_RECEIVERS = {
        "json",
        "yaml",
        "pickle",
        "toml",
        "xml",
        "msgpack",
        "cbor",
        "dateutil",
    }

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        inv_calls = json.loads(r["loop_invariant_calls"]) if r["loop_invariant_calls"] else []
        # Filter out intentional per-iteration calls
        flagged = []
        heavyweight_hits = []
        for c in inv_calls:
            call_full = (c or "").lower()
            call_leaf = _call_leaf(c).lower()
            if call_full in _INTENTIONAL_CALLS or call_leaf in _INTENTIONAL_CALLS:
                continue
            flagged.append(c)
            # V3 — escalate when the call is a parse/deserialize on a
            # known serialisation receiver (json.loads, JSON.parse, etc.).
            recv = _io_receiver_hint(c)
            if call_leaf in _HEAVYWEIGHT_PARSE_LEAVES and any(h in recv for h in _HEAVYWEIGHT_PARSE_RECEIVERS):
                heavyweight_hits.append(c)
        flagged = _dedupe(flagged)
        if not flagged:
            continue
        confidence = "high" if heavyweight_hits else "medium"
        matched_patterns = ["hoistable call(s) detected"]
        if heavyweight_hits:
            matched_patterns.append(f"heavyweight parse: {', '.join(heavyweight_hits[:2])}")
        results.append(
            _finding(
                "loop-invariant-call",
                "repeated-call",
                r,
                f"Loop-invariant call ({', '.join(flagged[:3])}) can be hoisted before loop"
                + (f" — heavyweight parse ({', '.join(heavyweight_hits[:2])})" if heavyweight_hits else ""),
                confidence,
                matched_patterns=matched_patterns,
            )
        )
    return results


@detector(
    task_id="membership",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_list_membership(conn: sqlite3.Connection) -> list[dict]:
    """Nested loops with equality comparisons — structural pattern for
    O(n^2) membership testing regardless of function name.

    Note on the LIKE patterns: ``_`` is a single-char wildcard in SQL
    LIKE, so ``LIKE '%in_%'`` matches *any* identifier with "in" followed
    by another char (``find_x``, ``intent``, ``something_else`` — all
    spurious hits). We use ``ESCAPE '\\'`` and double-write the literal
    ``\\_`` so we only match the intended idiomatic prefixes
    (``has_x``, ``is_in_y``, ``contains_z``).
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_with_compare, ms.subscript_in_loops, "
        "ms.has_nested_loops, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.loop_with_compare = 1 "
        "AND ms.subscript_in_loops = 1 "
        "AND (s.name LIKE '%contain%' OR s.name LIKE '%member%' "
        "  OR s.name LIKE '%exist%' OR s.name LIKE '%has\\_%' ESCAPE '\\' "
        "  OR s.name LIKE '%in\\_%' ESCAPE '\\' OR s.name LIKE '%check%' "
        "  OR s.name LIKE '%includes%' OR s.name LIKE '%Includes%' "
        "  OR s.name LIKE '%lookup%' OR s.name LIKE '%match%')"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        results.append(
            _finding(
                "membership",
                "list-scan",
                r,
                "Nested loops with comparisons for membership check",
                "medium",
            )
        )
    return results


@detector(
    task_id="unique",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_manual_dedup(conn: sqlite3.Connection) -> list[dict]:
    """Nested loops in dedup/unique-named functions without set usage."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_nested_loops, ms.loop_with_compare, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%dedup%' OR s.name LIKE '%unique%' "
        "  OR s.name LIKE '%Dedup%' OR s.name LIKE '%Unique%' "
        "  OR s.name LIKE '%distinct%' OR s.name LIKE '%Distinct%' "
        "  OR s.name LIKE '%remove\\_dup%' ESCAPE '\\' OR s.name LIKE '%removeDup%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Negative check: skip if they already use set/hash
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"set", "Set", "HashSet"}):
            continue
        results.append(
            _finding(
                "unique",
                "nested-dedup",
                r,
                "Nested loops with comparisons in dedup function",
                "high",
            )
        )
    return results


@detector(
    task_id="accumulation",
    languages=(),
    confidence_basis="heuristic",
    query_cost=QUERY_COST_LOW,
)
def detect_manual_accumulation(conn: sqlite3.Connection) -> list[dict]:
    """Loops with accumulator in sum/total-named functions.

    Same Big-O (both O(n)) — idiom improvement, flagged at low confidence.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_accumulator, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%\\_sum%' ESCAPE '\\' OR s.name LIKE '%\\_total%' ESCAPE '\\' "
        "  OR s.name LIKE '%Sum%' OR s.name LIKE '%Total%' "
        "  OR s.name LIKE '%accumulate%' OR s.name LIKE '%Accumulate%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_accumulator = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"sum", "reduce", "aggregate", "prod"}):
            continue
        results.append(
            _finding(
                "accumulation",
                "manual-sum",
                r,
                "Loop with accumulator in sum/total function (idiomatic improvement)",
                "low",
            )
        )
    return results


@detector(
    task_id="groupby",
    languages=(),
    confidence_basis="heuristic",
    query_cost=QUERY_COST_LOW,
)
def detect_manual_groupby(conn: sqlite3.Connection) -> list[dict]:
    """Loops in group/categorize-named functions without defaultdict/groupby.

    Same Big-O (both O(n)) — idiom improvement.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_accumulator, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%group%' OR s.name LIKE '%Group%' "
        "  OR s.name LIKE '%bucket%' OR s.name LIKE '%Bucket%' "
        "  OR s.name LIKE '%partition%' OR s.name LIKE '%Partition%' "
        "  OR s.name LIKE '%categorize%' OR s.name LIKE '%Categorize%' "
        "  OR s.name LIKE '%classify%' OR s.name LIKE '%Classify%' "
        "  OR s.name LIKE '%bin\\_by%' ESCAPE '\\' OR s.name LIKE '%key\\_by%' ESCAPE '\\' "
        "  OR s.name LIKE '%index\\_by%' ESCAPE '\\') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"groupby", "group_by", "defaultdict", "setdefault", "groupingBy", "Collectors"}):
            continue
        results.append(
            _finding(
                "groupby",
                "manual-check",
                r,
                "Manual loop in group-by function (idiomatic improvement)",
                "low",
            )
        )
    return results


@detector(
    task_id="async-nested-run",
    languages=("python",),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_async_nested_run(conn: sqlite3.Connection) -> list[dict]:
    """``asyncio.run()`` invoked inside another async function.

    ``asyncio.run`` creates a new event loop. If one is already running
    (which it is, inside an async function), this raises ``RuntimeError:
    asyncio.run() cannot be called from a running event loop``. The fix
    is to ``await`` the coroutine directly.

    Python only, fires only when the host is async.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND s.is_async = 1 "
            "AND f.language = 'python'"
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"]) or ""
        if "asyncio.run(" not in snippet:
            continue
        # Heuristic: must be `asyncio.run(` (not `await asyncio.run(`); the
        # latter is also wrong but caught by other linters more reliably.
        match_count = snippet.count("asyncio.run(")
        if match_count == 0:
            continue
        results.append(
            _finding(
                "async-nested-run",
                "asyncio-run-in-async",
                r,
                "asyncio.run() invoked inside an async function — raises RuntimeError at runtime (loop already running)",
                "high",
                snippet=snippet,
                matched_patterns=[
                    "is_async = 1",
                    f"asyncio.run( occurrences: {match_count}",
                ],
            )
        )
        results[-1]["fix"] = "Replace `asyncio.run(coro())` with `await coro()` — you're already inside an event loop."
    return results


@detector(
    task_id="chained-collection-walk",
    languages=JS_FAMILY_LANGUAGES,
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_chained_collection_walks(conn: sqlite3.Connection) -> list[dict]:
    """JS/TS: `.filter().find()` and friends are 2-pass; one-pass equivalents exist."""
    return _js_source_findings_preserving_evidence_lines(
        conn,
        task_id="chained-collection-walk",
        detected_way="two-pass-walk",
        patterns=(
            (_RE_FILTER_FIND, "filter().find() — two passes → .find(x => predA(x) && predB(x))"),
            (_RE_FILTER_LENGTH_BOOL, "filter().length — full walk for boolean → .some(x => predA(x))"),
            (_RE_MAP_FIND, "map().find() — full transform before search → .find() then .map() of one item"),
        ),
        reason_for_first_match=lambda label: (
            f"Chained collection walk ({label.split(' →', 1)[0]}) — single-pass form available"
        ),
        confidence="medium",
    )


@detector(
    task_id="defer-in-loop",
    languages=("go",),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_defer_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """Go: `defer` inside a `for`/`range` loop accumulates instead of firing per-iteration."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "f.language AS language, s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND f.language = 'go'"
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue
        m = _RE_DEFER_IN_LOOP_BODY.search(snippet)
        if not m:
            continue
        # Find the line of the defer keyword (not the for keyword) for precise location.
        defer_pos = snippet.find("defer ", m.start())
        if defer_pos == -1:
            defer_pos = m.start()
        line_offset = snippet[:defer_pos].count("\n")
        match_line = (r["line_start"] or 1) + line_offset
        results.append(
            _finding(
                "defer-in-loop",
                "loop-defer",
                r,
                "`defer` inside loop — fires when function returns, not when iteration ends "
                "(extract loop body to a helper, or close explicitly)",
                "high",
                match_line=match_line,
                snippet=snippet,
                matched_patterns=["for/range loop", "defer inside loop body"],
            )
        )
    return results


@algorithm_detector(
    task_id="async-fire-and-forget-task",
    languages=("python",),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_async_fire_and_forget(conn: sqlite3.Connection) -> list[dict]:
    """``asyncio.create_task(...)`` whose return value is discarded.

    Background tasks that aren't held in a long-lived reference get
    garbage-collected before they finish. Python 3.11+ explicitly warns
    about this footgun. The fix is to store the task somewhere that
    survives until ``await`` time, or just ``await`` it directly.

    Conservative: Python only, only fires when the line clearly creates
    a task without storing it.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path AS file_path, "
            "s.line_start, s.line_end "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND f.language = 'python'"
        ).fetchall()
    except sqlite3.Error:
        return []
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"]) or ""
        if "create_task" not in snippet:
            continue
        # Subtract stored-task lines from total create_task occurrences
        total = len(_RE_FIRE_AND_FORGET_TASK.findall(snippet))
        stored = len(_RE_STORED_TASK.findall(snippet))
        leaked = total - stored
        if leaked <= 0:
            continue
        results.append(
            _finding(
                "async-fire-and-forget-task",
                "leaked-asyncio-task",
                r,
                f"{leaked} asyncio.create_task call(s) whose return value isn't stored — gc may discard the task before it completes",
                "high",
                snippet=snippet,
                matched_patterns=[
                    f"create_task occurrences: {total}",
                    f"stored: {stored}, leaked: {leaked}",
                ],
            )
        )
        results[-1]["fix"] = (
            "Store the task: `tasks.append(asyncio.create_task(coro()))` and await it later, or `await asyncio.create_task(coro())` directly."
        )
    return results


@algorithm_detector(
    task_id="max-min",
    languages=(),
    confidence_basis="heuristic",
    query_cost=QUERY_COST_LOW,
)
def detect_manual_maxmin(conn: sqlite3.Connection) -> list[dict]:
    """Loops with comparisons in max/min-named functions.

    Same Big-O (both O(n)) — this is an idiom improvement, flagged at low
    confidence.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_compare, "
        "ms.loop_with_accumulator, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%find\\_max%' ESCAPE '\\' OR s.name LIKE '%find\\_min%' ESCAPE '\\' "
        "  OR s.name LIKE '%findMax%' OR s.name LIKE '%findMin%' "
        "  OR s.name LIKE '%get\\_max%' ESCAPE '\\' OR s.name LIKE '%get\\_min%' ESCAPE '\\' "
        "  OR s.name LIKE '%getMax%' OR s.name LIKE '%getMin%' "
        "  OR s.name LIKE '%find\\_largest%' ESCAPE '\\' OR s.name LIKE '%find\\_smallest%' ESCAPE '\\' "
        "  OR s.name LIKE '%findLargest%' OR s.name LIKE '%findSmallest%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"max", "min", "Math.max", "Math.min", "Collections.max", "Collections.min"}):
            continue
        results.append(
            _finding(
                "max-min",
                "manual-loop",
                r,
                "Manual loop with comparisons in max/min function (idiomatic improvement)",
                "low",
            )
        )
    return results


@algorithm_detector(
    task_id="spread-accumulator",
    languages=JS_FAMILY_LANGUAGES,
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_spread_accumulator(conn: sqlite3.Connection) -> list[dict]:
    """JS/TS: `acc = [...acc, x]` or `.reduce((acc, x) => [...acc, x])` is O(n²)."""
    return _js_source_findings_preserving_evidence_lines(
        conn,
        task_id="spread-accumulator",
        detected_way="spread-rebind",
        patterns=(
            (_RE_REDUCE_SPREAD, "reduce array spread accumulator"),
            (_RE_REDUCE_SPREAD_OBJ, "reduce object spread accumulator"),
            (_RE_SPREAD_ACC, "in-place array spread re-bind"),
            (_RE_SPREAD_OBJ_ACC, "in-place object spread re-bind"),
        ),
        reason_for_first_match=lambda label: f"Spread accumulator ({label}) is O(n^2) — use .push() / Object.assign()",
        confidence="high",
    )


@algorithm_detector(
    task_id="string-concat",
    languages=(),
    confidence_basis="structural",
    query_cost=QUERY_COST_MEDIUM,
)
def detect_string_concat_loop(conn: sqlite3.Connection) -> list[dict]:
    """Loops with accumulation patterns and string-related call hints.

    Relies primarily on the structural pattern (loop + accumulator) combined
    with calls to string methods (append/concat) or string-building name hints.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops, ms.loop_with_accumulator "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_accumulator = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        # Structural signal: calls to string concat/append methods
        has_concat_call = bool(_call_in(calls, {"concat", "strcat", "append", "push"}))
        # Name signal: function name suggests string building
        name_lower = (r["name"] or "").lower()
        has_name_hint = any(
            kw in name_lower
            for kw in (
                "concat",
                "build_str",
                "build_string",
                "format",
                "render",
                "serialize",
                "to_string",
                "tostring",
                "stringify",
                "to_csv",
                "to_html",
                "to_xml",
                "generate_report",
                "join",
            )
        )
        if has_concat_call or has_name_hint:
            results.append(
                _finding(
                    "string-concat",
                    "loop-concat",
                    r,
                    "Loop accumulation in string-building function",
                    "medium",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------

_CONFIDENCE_ORDER = ["low", "medium", "high"]

_DETECTOR_METADATA = {
    "sorting": {"precision": "high", "impact": "medium", "tags": ["ordering", "nested-loop"]},
    "search-sorted": {"precision": "low", "impact": "medium", "tags": ["search", "sorted-data"]},
    "membership": {"precision": "medium", "impact": "high", "tags": ["collections", "n2"]},
    "string-concat": {"precision": "medium", "impact": "medium", "tags": ["string", "quadratic"]},
    "unique": {"precision": "medium", "impact": "high", "tags": ["dedup", "n2"]},
    "max-min": {"precision": "medium", "impact": "low", "tags": ["idiom"]},
    "accumulation": {"precision": "medium", "impact": "low", "tags": ["idiom"]},
    "manual-power": {"precision": "high", "impact": "medium", "tags": ["math"]},
    "manual-gcd": {"precision": "high", "impact": "low", "tags": ["math"]},
    "fibonacci": {"precision": "high", "impact": "high", "tags": ["recursion", "exponential"]},
    "nested-lookup": {"precision": "medium", "impact": "high", "tags": ["join", "nxm"]},
    "groupby": {"precision": "medium", "impact": "low", "tags": ["idiom"]},
    "string-reverse": {"precision": "low", "impact": "low", "tags": ["idiom"]},
    "matrix-mult": {"precision": "high", "impact": "high", "tags": ["math", "triple-loop"]},
    "busy-wait": {"precision": "high", "impact": "high", "tags": ["concurrency"]},
    "regex-in-loop": {"precision": "high", "impact": "high", "tags": ["string", "regex"]},
    "io-in-loop": {"precision": "high", "impact": "high", "tags": ["io", "n+1"]},
    "list-prepend": {"precision": "high", "impact": "high", "tags": ["collections", "front-shift"]},
    "sort-to-select": {"precision": "high", "impact": "medium", "tags": ["ordering"]},
    "loop-lookup": {"precision": "high", "impact": "high", "tags": ["collections", "lookup"]},
    "branching-recursion": {"precision": "high", "impact": "high", "tags": ["recursion", "dp"]},
    "quadratic-string": {"precision": "high", "impact": "high", "tags": ["string", "quadratic"]},
    "loop-invariant-call": {"precision": "medium", "impact": "medium", "tags": ["hoisting"]},
    # W913 backfill: rows for previously-silent fallback detectors.
    # Async / event-loop:
    "async-blocking-sleep": {"precision": "high", "impact": "high", "tags": ["async", "blocking"]},
    "serial-await-loop": {"precision": "high", "impact": "medium", "tags": ["async", "performance"]},
    # Error handling:
    "broad-except-swallow": {"precision": "high", "impact": "high", "tags": ["error-handling", "anti-pattern"]},
    # Security:
    "dangerous-eval": {"precision": "high", "impact": "high", "tags": ["security", "code-injection"]},
    # React / DOM:
    "unremoved-event-listener": {"precision": "high", "impact": "medium", "tags": ["javascript", "memory-leak"]},
    "useeffect-missing-deps": {"precision": "high", "impact": "high", "tags": ["react", "hooks", "stale-closure"]},
}


def _detector_meta(task_id: str) -> dict:
    return _DETECTOR_METADATA.get(
        task_id,
        {"precision": "medium", "impact": "medium", "tags": []},
    )


def _boost_confidence(confidence: str) -> str:
    """Raise confidence by one level."""
    idx = _CONFIDENCE_ORDER.index(confidence) if confidence in _CONFIDENCE_ORDER else 1
    return _CONFIDENCE_ORDER[min(idx + 1, len(_CONFIDENCE_ORDER) - 1)]


def _lower_confidence(confidence: str) -> str:
    """Lower confidence by one level."""
    idx = _CONFIDENCE_ORDER.index(confidence) if confidence in _CONFIDENCE_ORDER else 1
    return _CONFIDENCE_ORDER[max(idx - 1, 0)]


_SIGNAL_COLUMNS = [
    ("has_nested_loops", "nested_loops"),
    ("subscript_in_loops", "subscript_in_loops"),
    ("loop_with_compare", "loop_compare"),
    ("loop_with_accumulator", "loop_accumulator"),
    ("loop_with_multiplication", "loop_multiplication"),
    ("loop_with_modulo", "loop_modulo"),
    ("str_concat_in_loop", "string_concat"),
    ("front_ops_in_loop", "front_ops"),
]

_PROFILE_BALANCED = "balanced"
_PROFILE_STRICT = "strict"
_PROFILE_AGGRESSIVE = "aggressive"
_VALID_PROFILES = {
    _PROFILE_BALANCED,
    _PROFILE_STRICT,
    _PROFILE_AGGRESSIVE,
}


def _chunked(values: list[int], size: int = 500):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _empty_symbol_context() -> dict:
    """Default evidence context consumed by calibration and evidence builders."""
    return {
        "signals": [],
        "loop_depth": 0,
        "loop_bound_small": 0,
        "caller_count": 0,
        "cognitive_complexity": 0.0,
        "cyclomatic_density": 0.0,
        "line_count": 0,
        "complexity_zscore": 0.0,
        "runtime_call_count": 0,
        "runtime_p99_latency_ms": None,
        "runtime_error_rate": 0.0,
        "runtime_otel_db_system": None,
        "runtime_otel_db_operation": None,
        "runtime_otel_db_statement_type": None,
    }


def _complexity_baseline(conn) -> tuple[float, float]:
    """Repo-wide cognitive-complexity baseline (mean, std) for z-score scoring.

    Differential scoring signal: computed over the whole ``symbol_metrics``
    table (NOT scoped to the candidate set) so high-complexity findings score
    as outliers. Returns (0.0, 0.0) when the table is missing on a legacy DB
    or holds no rows.
    """
    try:
        row = conn.execute(
            "SELECT AVG(COALESCE(cognitive_complexity, 0)) AS avg_cc, "
            "AVG(COALESCE(cognitive_complexity, 0) * COALESCE(cognitive_complexity, 0)) AS avg_sq_cc "
            "FROM symbol_metrics"
        ).fetchone()
        if row and row["avg_cc"] is not None:
            mean = float(row["avg_cc"] or 0.0)
            avg_sq = float(row["avg_sq_cc"] or 0.0)
            variance = max(0.0, avg_sq - (mean * mean))
            return mean, variance**0.5
    except sqlite3.Error as exc:
        log.warning(
            "complexity baseline skipped for _symbol_context: %s",
            exc,
        )
    return 0.0, 0.0


def _load_static_signals(conn, symbol_ids: list[int], context: dict[int, dict]) -> None:
    """Populate loop_depth / loop_bound_small / signals from math_signals."""
    for chunk in _chunked(symbol_ids):
        ph = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT symbol_id, loop_depth, loop_bound_small, "
            f"has_nested_loops, subscript_in_loops, loop_with_compare, "
            f"loop_with_accumulator, loop_with_multiplication, loop_with_modulo, "
            f"str_concat_in_loop, front_ops_in_loop "
            f"FROM math_signals WHERE symbol_id IN ({ph})",
            chunk,
        ).fetchall()
        for r in rows:
            sid = r["symbol_id"]
            if sid not in context:
                continue
            ctx = context[sid]
            ctx["loop_depth"] = int(r["loop_depth"] or 0)
            ctx["loop_bound_small"] = int(r["loop_bound_small"] or 0)
            signals: list[str] = []
            for col, label in _SIGNAL_COLUMNS:
                if r[col]:
                    signals.append(label)
            ctx["signals"] = signals


def _load_complexity_metrics(
    conn,
    symbol_ids: list[int],
    context: dict[int, dict],
    complexity_mean: float,
    complexity_std: float,
) -> None:
    """Populate cognitive_complexity / cyclomatic_density / line_count / z-score."""
    for chunk in _chunked(symbol_ids):
        ph = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT symbol_id, cognitive_complexity, cyclomatic_density, line_count "
            f"FROM symbol_metrics WHERE symbol_id IN ({ph})",
            chunk,
        ).fetchall()
        for r in rows:
            sid = r["symbol_id"]
            if sid not in context:
                continue
            cc = float(r["cognitive_complexity"] or 0.0)
            density = float(r["cyclomatic_density"] or 0.0)
            line_count = int(r["line_count"] or 0)
            ctx = context[sid]
            ctx["cognitive_complexity"] = cc
            ctx["cyclomatic_density"] = density
            ctx["line_count"] = line_count
            if complexity_std > 0:
                ctx["complexity_zscore"] = (cc - complexity_mean) / complexity_std


def _load_caller_counts(conn, symbol_ids: list[int], context: dict[int, dict]) -> None:
    """Populate caller_count from pure call edges.

    W512: edge-kind vocabulary lives in roam.db.edge_kinds — pure call edges
    only here, callers of a function in the algorithm-context catalog.
    """
    call_kind_ph = ", ".join("?" for _ in CALL_EDGE_KINDS)
    for chunk in _chunked(symbol_ids):
        ph = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT target_id, COUNT(*) AS cnt "
            f"FROM edges WHERE kind IN ({call_kind_ph}) "
            f"AND target_id IN ({ph}) "
            f"GROUP BY target_id",
            (*CALL_EDGE_KINDS, *chunk),
        ).fetchall()
        for r in rows:
            sid = r["target_id"]
            if sid in context:
                context[sid]["caller_count"] = int(r["cnt"] or 0)


def _load_runtime_traces(conn, symbol_ids: list[int], context: dict[int, dict]) -> None:
    """Populate runtime_* OTel enrichment from runtime_stats.

    Optional table for legacy DB compatibility; absence is expected and
    logged, not raised (see the W661/W679 notes on the handler below).
    """
    try:
        for chunk in _chunked(symbol_ids):
            ph = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT symbol_id, SUM(call_count) AS total_calls, "
                f"MAX(p99_latency_ms) AS p99_ms, MAX(error_rate) AS max_err, "
                f"MAX(otel_db_system) AS db_system, "
                f"MAX(otel_db_operation) AS db_operation, "
                f"MAX(otel_db_statement_type) AS db_statement_type "
                f"FROM runtime_stats "
                f"WHERE symbol_id IN ({ph}) "
                f"GROUP BY symbol_id",
                chunk,
            ).fetchall()
            for r in rows:
                sid = r["symbol_id"]
                if sid not in context:
                    continue
                context[sid]["runtime_call_count"] = int(r["total_calls"] or 0)
                context[sid]["runtime_p99_latency_ms"] = r["p99_ms"]
                context[sid]["runtime_error_rate"] = float(r["max_err"] or 0.0)
                context[sid]["runtime_otel_db_system"] = r["db_system"]
                context[sid]["runtime_otel_db_operation"] = r["db_operation"]
                context[sid]["runtime_otel_db_statement_type"] = r["db_statement_type"]
    except (sqlite3.Error, KeyError, TypeError) as exc:
        # W679: narrowed from `except Exception` per the W662 allowlist plan
        # ("re-audit to narrow to (sqlite3.Error, KeyError) once the runtime
        # schema stabilises"). The handler covers three legitimate-skip
        # classes for the optional OTel runtime-enrichment merge:
        #   * sqlite3.Error — `runtime_stats` is an optional table on legacy
        #     DBs (see surrounding docstring "optional table for legacy DB
        #     compatibility"); OperationalError on a missing table /
        #     missing OTel column is the expected absence signal.
        #   * KeyError — Row mapping access (`r["db_system"]` etc.) raises
        #     KeyError if a query selects fewer columns than expected (e.g.
        #     a partial-migration DB without the otel_* columns).
        #   * TypeError — the int(...) / float(...) coercions on
        #     total_calls / max_err raise TypeError if the column carries a
        #     non-numeric blob (legacy ingest variants).
        # Programmer-class errors (NameError / ImportError / AttributeError)
        # propagate per W531 fail-loud + W653 incident. The expected absence
        # is logged at warning so the skip stays observable to operators
        # (W661 fail-soft-with-logging discipline), rather than silent.
        log.warning(
            "OTel runtime enrichment skipped for %d symbols in _symbol_context: %s",
            len(symbol_ids),
            exc,
        )


def _symbol_context(conn, symbol_ids: list[int]) -> dict[int, dict]:
    """Load calibration + evidence context for findings in bulk.

    Thin orchestrator over five single-purpose loaders; each mutates
    ``context`` in place. The returned dict is keyed by symbol_id and carries
    every key the calibrate / evidence / score consumers read, defaulting to
    zero / None when the underlying row is absent.
    """
    context: dict[int, dict] = {sid: _empty_symbol_context() for sid in symbol_ids}
    if not symbol_ids:
        return context

    complexity_mean, complexity_std = _complexity_baseline(conn)
    _load_static_signals(conn, symbol_ids, context)
    _load_complexity_metrics(conn, symbol_ids, context, complexity_mean, complexity_std)
    _load_caller_counts(conn, symbol_ids, context)
    _load_runtime_traces(conn, symbol_ids, context)
    return context


def _merge_evidence(existing: dict | None, enriched: dict) -> dict:
    if not existing:
        return enriched
    merged = dict(existing)
    for k, v in enriched.items():
        if k not in merged:
            merged[k] = v
            continue
        current = merged[k]
        if isinstance(current, list) and isinstance(v, list):
            merged[k] = _dedupe([*current, *v])
        elif isinstance(current, dict) and isinstance(v, dict):
            tmp = dict(current)
            tmp.update(v)
            merged[k] = tmp
        else:
            merged[k] = v
    return merged


def _build_evidence(context: dict) -> dict:
    evidence = {
        "signal_count": len(context.get("signals", [])),
        "signals": context.get("signals", []),
        "loop_depth": int(context.get("loop_depth", 0) or 0),
        "loop_bound_small": bool(context.get("loop_bound_small", 0)),
        "caller_count": int(context.get("caller_count", 0) or 0),
    }
    cognitive = float(context.get("cognitive_complexity", 0.0) or 0.0)
    density = float(context.get("cyclomatic_density", 0.0) or 0.0)
    line_count = int(context.get("line_count", 0) or 0)
    zscore = float(context.get("complexity_zscore", 0.0) or 0.0)
    if cognitive > 0 or density > 0 or line_count > 0:
        evidence["complexity"] = {
            "cognitive": cognitive,
            "cyclomatic_density": density,
            "line_count": line_count,
            "zscore": round(zscore, 3),
        }
    runtime_calls = int(context.get("runtime_call_count", 0) or 0)
    runtime_p99 = context.get("runtime_p99_latency_ms")
    runtime_error = float(context.get("runtime_error_rate", 0.0) or 0.0)
    runtime_db_system = context.get("runtime_otel_db_system")
    runtime_db_operation = context.get("runtime_otel_db_operation")
    runtime_db_statement_type = context.get("runtime_otel_db_statement_type")
    if runtime_calls > 0 or runtime_p99 is not None:
        evidence["runtime"] = {
            "call_count": runtime_calls,
            "p99_latency_ms": runtime_p99,
            "error_rate": runtime_error,
            "db_system": runtime_db_system,
            "db_operation": runtime_db_operation,
            "db_statement_type": runtime_db_statement_type,
        }
    return evidence


def _build_evidence_path(finding: dict, context: dict) -> list[str]:
    path = [f"Observed pattern: {finding['reason']}"]
    signals = context.get("signals", [])
    if signals:
        path.append(f"Static evidence: {', '.join(signals[:4])}")
    cognitive = float(context.get("cognitive_complexity", 0.0) or 0.0)
    zscore = float(context.get("complexity_zscore", 0.0) or 0.0)
    if cognitive >= 12 or zscore >= 1.0:
        path.append(
            "Complexity pressure: cognitive={:.1f}, zscore={:.2f}".format(
                cognitive,
                zscore,
            )
        )
    runtime_calls = int(context.get("runtime_call_count", 0) or 0)
    runtime_p99 = context.get("runtime_p99_latency_ms")
    runtime_db_system = context.get("runtime_otel_db_system")
    runtime_db_operation = context.get("runtime_otel_db_operation") or context.get("runtime_otel_db_statement_type")
    if runtime_calls > 0:
        if runtime_p99 is not None:
            path.append(f"Runtime impact: {runtime_calls} calls, p99 {runtime_p99:.0f}ms")
        else:
            path.append(f"Runtime impact: {runtime_calls} calls")
        if runtime_db_system or runtime_db_operation:
            path.append(
                "Runtime semantics: db_system={} db_operation={}".format(
                    runtime_db_system or "n/a",
                    runtime_db_operation or "n/a",
                )
            )
    path.append(f"Recommendation: replace `{finding['detected_way']}` with `{finding['suggested_way']}`.")
    return path


def _impact_score(finding: dict, context: dict) -> float:
    """Rank finding urgency using static + runtime evidence."""
    confidence_base = {"high": 60.0, "medium": 35.0, "low": 15.0}
    impact_weight = {"high": 12.0, "medium": 6.0, "low": 2.0}

    score = confidence_base.get(finding.get("confidence", "medium"), 30.0)
    score += impact_weight.get(finding.get("impact", "medium"), 5.0)

    loop_depth = int(context.get("loop_depth", 0) or 0)
    score += min(12.0, loop_depth * 2.0)

    caller_count = int(context.get("caller_count", 0) or 0)
    score += min(10.0, caller_count * 1.5)

    # Differential complexity pressure (relative to repository baseline).
    cognitive = float(context.get("cognitive_complexity", 0.0) or 0.0)
    density = float(context.get("cyclomatic_density", 0.0) or 0.0)
    zscore = float(context.get("complexity_zscore", 0.0) or 0.0)
    if cognitive > 0:
        score += min(7.0, cognitive / 6.0)
    if density > 0:
        score += min(4.0, density * 8.0)
    if zscore > 0:
        score += min(8.0, zscore * 2.5)

    runtime_calls = int(context.get("runtime_call_count", 0) or 0)
    runtime_p99 = context.get("runtime_p99_latency_ms")
    runtime_error = float(context.get("runtime_error_rate", 0.0) or 0.0)
    runtime_db_system = context.get("runtime_otel_db_system")
    runtime_db_operation = (
        context.get("runtime_otel_db_operation") or context.get("runtime_otel_db_statement_type") or ""
    )
    runtime_db_operation = str(runtime_db_operation).upper()
    runtime_multiplier = 0.65
    if runtime_db_system:
        runtime_multiplier = 1.0
        if runtime_db_operation in {"INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "REPLACE"}:
            runtime_multiplier = 1.25
        elif runtime_db_operation in {"SELECT", "READ", "QUERY", "GET"}:
            runtime_multiplier = 1.0
        else:
            runtime_multiplier = 0.9
    if runtime_calls:
        score += min(20.0, (len(str(max(1, runtime_calls))) - 1) * 5.0) * runtime_multiplier
    if runtime_p99 is not None:
        latency_mult = 1.1 if runtime_db_system else 0.9
        score += min(15.0, float(runtime_p99) / 40.0) * latency_mult
    if runtime_error > 0:
        score += min(8.0, runtime_error * 100.0)
    if runtime_db_system:
        score += 2.5
    if runtime_db_operation in {"INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "REPLACE"}:
        score += 3.5

    if context.get("loop_bound_small", 0):
        score -= 8.0

    return round(max(0.0, score), 2)


def _impact_band(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _calibrate_finding(finding: dict, context: dict | None) -> dict:
    """Adjust confidence using static and runtime context."""
    ctx = context or {}
    caller_count = int(ctx.get("caller_count", 0) or 0)
    loop_bound_small = bool(ctx.get("loop_bound_small", 0))
    signal_count = len(ctx.get("signals", []))
    runtime_calls = int(ctx.get("runtime_call_count", 0) or 0)
    runtime_p99 = ctx.get("runtime_p99_latency_ms")
    runtime_error = float(ctx.get("runtime_error_rate", 0.0) or 0.0)
    runtime_db_system = ctx.get("runtime_otel_db_system")
    runtime_db_operation = ctx.get("runtime_otel_db_operation") or ctx.get("runtime_otel_db_statement_type") or ""
    runtime_db_operation = str(runtime_db_operation).upper()
    precision = (finding.get("precision") or "medium").lower()
    evidence = finding.get("evidence", {}) if isinstance(finding.get("evidence"), dict) else {}
    ambiguous_io_only = bool(finding.get("task_id") == "io-in-loop" and evidence.get("ambiguous_io_only"))
    caller_threshold = 5
    if precision == "medium":
        caller_threshold = 8
    elif precision == "low":
        caller_threshold = 12

    if (not ambiguous_io_only) and caller_count >= caller_threshold and (precision == "high" or signal_count >= 2):
        finding["confidence"] = _boost_confidence(finding["confidence"])
        finding["reason"] += f" (hot: {caller_count} callers)"
    elif caller_count == 0 and runtime_calls == 0:
        finding["confidence"] = _lower_confidence(finding["confidence"])

    runtime_call_threshold = 1500 if not runtime_db_system else 700
    runtime_hot = (
        runtime_calls >= runtime_call_threshold
        or (runtime_p99 is not None and runtime_p99 >= 300)
        or runtime_error >= 0.02
    )
    if runtime_hot:
        finding["confidence"] = _boost_confidence(finding["confidence"])
        runtime_very_hot = (
            runtime_calls >= 5000 or (runtime_p99 is not None and runtime_p99 >= 800) or runtime_error >= 0.05
        )
        if runtime_very_hot:
            finding["confidence"] = _boost_confidence(finding["confidence"])
        rt_bits = []
        if runtime_calls:
            rt_bits.append(f"{runtime_calls} calls")
        if runtime_p99 is not None:
            rt_bits.append(f"p99 {runtime_p99:.0f}ms")
        if runtime_error > 0:
            rt_bits.append(f"err {runtime_error * 100:.1f}%")
        if runtime_db_system:
            rt_bits.append(
                "db {}{}".format(
                    runtime_db_system,
                    f" {runtime_db_operation}" if runtime_db_operation else "",
                )
            )
        if rt_bits:
            finding["reason"] += f" (runtime: {', '.join(rt_bits)})"

    if loop_bound_small:
        finding["confidence"] = _lower_confidence(finding["confidence"])
        finding["reason"] += " (bounded loop)"

    # M8 — confidence calibration floor.
    # Categories where the FP-fix is heuristic-only (no AST-level proof)
    # cap at 'medium' regardless of caller-count or runtime boost. Real-world
    # calibration on a Vue 3 + Laravel codebase showed "high confidence" branching-recursion
    # was 0/1 true positive; same pattern likely on sort-then-subscript when
    # the result is also iterated.
    _MEDIUM_FLOOR_TASKS = {"branching-recursion", "sort-to-select"}
    if finding.get("task_id") in _MEDIUM_FLOOR_TASKS and finding.get("confidence") == "high":
        # Only floor when there's no strong runtime signal — runtime-hot code
        # earns the high-confidence boost.
        if not runtime_hot:
            finding["confidence"] = "medium"
            evidence_dict = finding.setdefault("evidence", {}) if isinstance(finding.get("evidence"), dict) else {}
            if isinstance(evidence_dict, dict):
                evidence_dict["calibration_floor"] = (
                    "category capped at 'medium' — heuristic-only detection has high FP rate; "
                    "use --confidence high to skip these"
                )

    return finding


def _has_strong_runtime(evidence: dict) -> bool:
    runtime = evidence.get("runtime") if isinstance(evidence, dict) else None
    if not isinstance(runtime, dict):
        return False
    calls = int(runtime.get("call_count", 0) or 0)
    p99 = runtime.get("p99_latency_ms")
    return calls >= 200 or (p99 is not None and p99 >= 200)


def _apply_profile(findings: list[dict], profile: str) -> list[dict]:
    if profile == _PROFILE_AGGRESSIVE:
        for f in findings:
            evidence = f.get("evidence", {})
            if f.get("confidence") == "low" and (
                int(evidence.get("signal_count", 0) or 0) >= 2 or _has_strong_runtime(evidence)
            ):
                f["confidence"] = "medium"
        return findings

    if profile == _PROFILE_STRICT:
        strict_findings: list[dict] = []
        for f in findings:
            if f.get("confidence") == "low":
                continue
            precision = (f.get("precision") or "medium").lower()
            evidence = f.get("evidence", {})
            runtime_strong = _has_strong_runtime(evidence)
            if precision == "low" and not runtime_strong and f.get("confidence") != "high":
                continue
            if f.get("confidence") == "high":
                strict_findings.append(f)
                continue
            if int(evidence.get("signal_count", 0) or 0) >= 2 or runtime_strong:
                strict_findings.append(f)
        return strict_findings

    return findings


# ---------------------------------------------------------------------------
# Detector registry
# ---------------------------------------------------------------------------

_MATH_DETECTORS = [
    ("sorting", "manual-sort", detect_manual_sort),
    ("search-sorted", "linear-scan", detect_linear_search),
    ("manual-power", "loop-multiply", detect_manual_power),
    ("manual-gcd", "manual-gcd", detect_manual_gcd),
    ("fibonacci", "naive-recursive", detect_naive_fibonacci),
    ("nested-lookup", "nested-iteration", detect_nested_lookup),
    ("string-reverse", "manual-reverse", detect_string_reverse),
    ("matrix-mult", "naive-triple", detect_matrix_mult),
    ("busy-wait", "sleep-loop", detect_busy_wait),
    ("regex-in-loop", "compile-per-iter", detect_regex_in_loop),
    ("io-in-loop", "loop-query", detect_io_in_loop),
    ("serial-await-loop", "for-of-await", detect_serial_await_loop),
    ("async-blocking-sleep", "blocking-call-in-async", detect_async_blocking_sleep),
    ("broad-except-swallow", "swallow-exception", detect_broad_except_swallow),
    ("useeffect-missing-deps", "no-deps-array", detect_useeffect_missing_deps),
    ("dangerous-eval", "eval-or-exec", detect_dangerous_eval),
    ("unremoved-event-listener", "no-cleanup", detect_unremoved_event_listener),
    ("list-prepend", "insert-front", detect_list_prepend),
    ("sort-to-select", "full-sort", detect_sort_to_select),
    ("loop-lookup", "method-scan", detect_loop_lookup),
    ("branching-recursion", "naive-branching", detect_branching_recursion),
    ("quadratic-string", "augment-concat", detect_quadratic_string),
    ("loop-invariant-call", "repeated-call", detect_loop_invariant_call),
    # The 11 entries below were dropped when a dead-code autopilot removed
    # their detector functions; the restores (aa263622 and siblings) brought
    # back the function bodies + @algorithm_detector decorators but not this
    # dispatch wiring, leaving them registry-visible yet never executed
    # (Pattern-1D silent no-op, caught by test_w1057_math_unknown_detectors).
    # (task_id, way_id) tuples recovered verbatim from the pre-removal tree.
    ("membership", "list-scan", detect_list_membership),
    ("string-concat", "loop-concat", detect_string_concat_loop),
    ("unique", "nested-dedup", detect_manual_dedup),
    ("max-min", "manual-loop", detect_manual_maxmin),
    ("accumulation", "manual-sum", detect_manual_accumulation),
    ("groupby", "manual-check", detect_manual_groupby),
    ("async-fire-and-forget-task", "leaked-asyncio-task", detect_async_fire_and_forget),
    ("async-nested-run", "asyncio-run-in-async", detect_async_nested_run),
    ("spread-accumulator", "spread-rebind", detect_spread_accumulator),
    ("defer-in-loop", "loop-defer", detect_defer_in_loop),
    ("chained-collection-walk", "two-pass-walk", detect_chained_collection_walks),
]


def _iter_registered_detectors():
    """Yield built-in detectors plus plugin-contributed detectors."""
    for det in _MATH_DETECTORS:
        yield det

    # Python pivot v12.4 — language-specific idiom detectors. Wrapped
    # in try/except so a regex bug in one detector can't block the
    # algorithm pass.
    try:
        from roam.catalog.python_idioms import PYTHON_IDIOM_DETECTORS

        for det in PYTHON_IDIOM_DETECTORS:
            yield det
    except ImportError as exc:
        # Optional-module guard: python_idioms absence just yields the
        # built-in set — logged loud (mirrors js_idioms below) rather
        # than silently swallowed.
        log_swallowed("detectors.iter.python_idioms_import", exc)

    # JS/TS sibling pack — same isolation rationale as python_idioms.
    try:
        from roam.catalog.js_idioms import JS_IDIOM_DETECTORS

        for det in JS_IDIOM_DETECTORS:
            yield det
    except ImportError as exc:
        # Optional-module guard: js_idioms absence just yields the built-in
        # set — logged loud rather than silently swallowed.
        log_swallowed("detectors.iter.js_idioms_import", exc)

    try:
        from roam.plugins import get_plugin_detectors

        for task_id, way_id, detect_fn in get_plugin_detectors():
            if callable(detect_fn):
                yield (task_id, way_id, detect_fn)
    except Exception as exc:  # noqa: BLE001 -- plugin code may raise anything; isolate it
        # Plugin loading errors should not impact built-in detection.
        log_swallowed("detectors.iter.plugin_detectors", exc)
        return


def _apply_detector_scopes(conn, scope_ids):
    """Apply the three per-run file scopes for ``run_detectors``.

    Returns ``(idiom_reset, js_idiom_reset, catalog_scope_applied)`` — the
    first two are the already-imported ``set_*_scope`` callables (captured so
    the caller's ``finally`` can reset them with ``None``), the third flags
    whether ``_DETECTOR_SCOPE_PATHS`` was set and must be cleared.
    """
    global _DETECTOR_SCOPE_PATHS
    idiom_reset = None
    js_idiom_reset = None
    catalog_scope_applied = False
    try:
        from roam.catalog.python_idioms import set_idiom_scope

        set_idiom_scope(scope_ids)
        idiom_reset = set_idiom_scope  # captured for the caller's finally
    except Exception as exc:  # noqa: BLE001 -- optional Python scope should not abort detector runs
        # Optional Python scope should not abort detector runs; unscoped detection is the fallback.
        log.warning("run_detectors: could not apply idiom scope: %s", exc, exc_info=True)
    # The JS pack keeps its own module-global scope; apply it the same way.
    try:
        from roam.catalog.js_idioms import set_js_idiom_scope

        set_js_idiom_scope(scope_ids)
        js_idiom_reset = set_js_idiom_scope  # captured for the caller's finally
    except Exception as exc:  # noqa: BLE001 -- optional JS scope should not abort detector runs
        # Optional JS scope should not abort detector runs; unscoped detection is the fallback.
        log.warning("run_detectors: could not apply js idiom scope: %s", exc, exc_info=True)
    # Resolve scope file-ids to paths so the catalog detectors' source-read
    # chokepoint (`_read_symbol_source`) can skip out-of-scope files.
    try:
        from roam.db.connection import batched_in as _bi

        paths = {r["path"] for r in _bi(conn, "SELECT path FROM files WHERE id IN ({ph})", list(scope_ids))}
        _DETECTOR_SCOPE_PATHS = paths
        catalog_scope_applied = True
    except Exception as exc:  # noqa: BLE001 -- scope filter is an optimization; unscoped detection is the safe fallback
        log.warning("run_detectors: could not apply catalog scope: %s", exc, exc_info=True)
    return idiom_reset, js_idiom_reset, catalog_scope_applied


def _execute_detectors(conn, detector_entries, task_filter, only_set, exclude_set):
    """Run the detector loop for ``run_detectors``.

    Returns ``(findings, failed_detectors, executed, executed_tasks)``.
    Exception discipline (W661): programmer-class errors raise, sqlite-class
    errors log + record + continue, anything else records + continues.
    """
    findings = []
    failed_detectors = []
    executed = 0
    executed_tasks: list[str] = []
    for task_id, _way_id, detect_fn in detector_entries:
        if task_filter and task_id != task_filter:
            continue
        # A3 — --only / --exclude filter on detector function names.
        # W1316 extends the filter from decorator-registered detectors
        # to the full runtime surface (built-in catalog + Python idioms),
        # so a detector shown by `roam algo --list-detectors` can always
        # be selected directly.
        fn_name = getattr(detect_fn, "__name__", "")
        if only_set:
            if fn_name not in only_set:
                continue
        elif fn_name in exclude_set:
            continue
        executed += 1
        executed_tasks.append(task_id)
        try:
            hits = detect_fn(conn)
        except (NameError, ImportError, AttributeError, TypeError) as err:
            # W661: programmer-class bug (missing import, wrong attribute,
            # signature drift) — fail-loud per W531 + CLAUDE.md Pattern-2
            # discipline. Mirrors W653 in smells.run_all_detectors(). The
            # W639 smoke test catches these at test time, but the production
            # loop must also surface the bug class to operators rather than
            # silently dropping the detector into the `failed_detectors`
            # bucket where it gets buried in `meta`.
            raise RuntimeError(
                f"algo detector {detect_fn.__name__} "
                f"(task_id={task_id}) crashed with programmer error: "
                f"{type(err).__name__}: {err}"
            ) from err
        except sqlite3.Error as exc:
            # W661: per-detector DB error (missing table, bad query against
            # the live schema) is a data-class issue — log + continue so
            # the remaining detectors still produce findings the operator
            # can act on. Preserves the existing `failed_detectors` meta
            # contract for sqlite-class failures.
            log.warning(
                "algo detector %s (task_id=%s) failed with sqlite error: %s",
                detect_fn.__name__,
                task_id,
                exc,
            )
            failed_detectors.append(
                {
                    "task_id": task_id,
                    "detector": detect_fn.__name__,
                    "error": str(exc),
                }
            )
            continue
        except Exception as exc:
            # W661: anything else (OS errors, third-party plugin
            # bugs we don't want to crash the run on) keeps the
            # legacy behaviour — record in `failed_detectors`,
            # continue. Narrower programmer-class + data-class
            # branches above already handle the bug classes we
            # know how to triage.
            failed_detectors.append(
                {
                    "task_id": task_id,
                    "detector": detect_fn.__name__,
                    "error": str(exc),
                }
            )
            continue
        dmeta = _detector_meta(task_id)
        for h in hits:
            h.setdefault("precision", dmeta["precision"])
            h.setdefault("impact", dmeta["impact"])
            h.setdefault("tags", list(dmeta["tags"]))
        findings.extend(hits)
    return findings, failed_detectors, executed, executed_tasks


def run_detectors(
    conn,
    task_filter=None,
    confidence_filter=None,
    *,
    profile="balanced",
    return_meta=False,
    framework=None,
    include_tests=False,
    only=None,
    exclude=None,
    scope_file_ids=None,
):
    """Run all detectors and return combined findings.

    Parameters
    ----------
    conn : sqlite3.Connection
    task_filter : str or None
        If set, only run the detector for this task_id.
    confidence_filter : str or None
        If set, keep only findings with this confidence level.
    profile : str
        Precision profile: ``balanced`` (default), ``strict``, ``aggressive``.
    return_meta : bool
        When True, returns ``(findings, meta)`` where ``meta`` contains
        detector execution diagnostics (totals + failures).
    framework : str or None
        D3 — opt-in framework profile name (e.g. ``vue3-tanstack``,
        ``laravel-multitenant``). Layers extra cache-allowlist entries on
        top of the defaults for the duration of this call. Unknown names
        are tolerated (defaults apply, and ``meta['framework_unknown']``
        flags the miss).
    only : iterable of str or None
        A3 — restrict to these decorated-detector names (matches
        ``_DETECTOR_REGISTRY`` keys). Non-registered detectors are always
        skipped under ``--only``. Use ``roam math --list-detectors`` to
        see candidates.
    exclude : iterable of str or None
        A3 — drop these decorated-detector names. Non-registered detectors
        are unaffected. ``only`` wins over ``exclude`` when both name the
        same detector.
    scope_file_ids : iterable of int or None
        Restrict the run to symbols/files in this file-id set. The dominant
        cost (measured: ~70% of a project-wide run on roam-code) is the
        python-idiom detectors' full-text regex scan of EVERY Python file;
        a scope collapses that to the changed files via ``set_idiom_scope``.
        The decorated catalog detectors still query the whole index, but
        their findings are filtered to the scope before enrichment. Callers
        that already know the changed fileset (e.g. ``roam adversarial``)
        should pass it. ``None`` = whole project (unchanged behaviour).

    Returns list of finding dicts, or ``(findings, meta)`` when
    ``return_meta=True``.
    """
    global _ACTIVE_FRAMEWORK_PROFILE, _INCLUDE_TESTS_OVERRIDE, _DETECTOR_SCOPE_PATHS
    # S2/S3/ caches scoped to a single `run_detectors`
    # invocation. Without this reset, fixture tests that rewrite the
    # same path between runs would see stale content.
    _FILE_LINES_CACHE.clear()
    _IN_MEMORY_CALL_CACHE.clear()
    _FRAMEWORK_PACK_CACHE.clear()
    # X14 — toggle test-path inclusion for the duration of this call.
    previous_include_tests = _INCLUDE_TESTS_OVERRIDE
    _INCLUDE_TESTS_OVERRIDE = bool(include_tests)
    framework_unknown: str | None = None
    previous_profile = _ACTIVE_FRAMEWORK_PROFILE
    framework_active: str | None = None
    if framework:
        resolved = set_active_framework_profile(framework)
        if resolved is None:
            framework_unknown = framework
            set_active_framework_profile(None)
        else:
            framework_active = framework.lower()
    only_set = {n for n in (only or ()) if n}
    exclude_set = {n for n in (exclude or ()) if n} - only_set
    detector_entries = list(_iter_registered_detectors())
    known_detector_names = {getattr(fn, "__name__", "") for _task_id, _way_id, fn in detector_entries}
    # W1057 (Pattern 1D + Pattern 2): diff user-supplied --only/--exclude against
    # the registry-derived authoritative detector-name set so unknown names
    # don't silently filter-to-zero. Mirrors the framework_unknown precedent
    # at L4719-4725. Sorted for deterministic envelope hashing. Empty lists on
    # the happy path keep the meta envelope byte-identical to pre-W1057.
    only_unknown = sorted(only_set - known_detector_names) if only_set else []
    exclude_unknown = sorted(exclude_set - known_detector_names) if exclude_set else []
    # File-scoping (the biggest single run_detectors lever). The python-idiom
    # detectors regex-scan every Python file's full text — measured ~70% of a
    # project-wide run. Restricting `_python_files` via set_idiom_scope collapses
    # that to the changed files. Reset in the finally so the module-global scope
    # never leaks into a later unscoped run.
    scope_ids = {int(f) for f in scope_file_ids} if scope_file_ids is not None else None
    _idiom_scope_reset = None  # bound to set_idiom_scope once it's applied
    _js_idiom_scope_reset = None  # bound to set_js_idiom_scope once applied
    _catalog_scope_applied = False
    if scope_ids is not None:
        _idiom_scope_reset, _js_idiom_scope_reset, _catalog_scope_applied = _apply_detector_scopes(conn, scope_ids)
    try:
        findings, failed_detectors, executed, executed_tasks = _execute_detectors(
            conn, detector_entries, task_filter, only_set, exclude_set
        )

        # Catalog detectors query the whole index, so scope their findings by
        # file_id here (idiom detectors are already scoped at the source via
        # set_idiom_scope). Filtering before enrichment also keeps the costly
        # _symbol_context / _calibrate / _build_evidence passes off out-of-scope
        # findings. One batched symbol->file lookup; no per-finding round-trips.
        if scope_ids is not None and findings:
            from roam.db.connection import batched_in as _batched_in

            sids = _dedupe([f["symbol_id"] for f in findings if f.get("symbol_id")])
            sym_file: dict = {}
            if sids:
                for r in _batched_in(conn, "SELECT id, file_id FROM symbols WHERE id IN ({ph})", sids):
                    sym_file[r["id"]] = r["file_id"]
            findings = [f for f in findings if sym_file.get(f.get("symbol_id")) in scope_ids]

        profile_key = (profile or _PROFILE_BALANCED).lower()
        if profile_key not in _VALID_PROFILES:
            profile_key = _PROFILE_BALANCED

        symbol_ids = _dedupe([f["symbol_id"] for f in findings if f.get("symbol_id")])
        context_map = _symbol_context(conn, symbol_ids)

        # Apply calibration and enrich findings with semantic evidence.
        for f in findings:
            ctx = context_map.get(f["symbol_id"], {})
            _calibrate_finding(f, ctx)
            enriched_evidence = _build_evidence(ctx)
            f["evidence"] = _merge_evidence(f.get("evidence"), enriched_evidence)
            f["evidence_path"] = _build_evidence_path(f, ctx)
            score = _impact_score(f, ctx)
            f["impact_score"] = score
            f["impact_band"] = _impact_band(score)

        pre_profile_count = len(findings)
        findings = _apply_profile(findings, profile_key)
        profile_filtered = pre_profile_count - len(findings)

        if confidence_filter:
            # W1005-followup-D: equality → floor semantic via canonical
            # severity_rank(). Detectors emit {high, medium, low}; the Click
            # Choice on ``--confidence`` (cmd_math) accepts the full W547
            # 7-tier so agents can pass any canonical token. Floor keeps a
            # finding when ``severity_rank(f.confidence) >= severity_rank(
            # confidence_filter)``. Equality was the pre-W1005-followup-D
            # semantic — kept only EXACTLY that tier; floor keeps that tier
            # AND everything ranked above it.
            _floor_rank = severity_rank(confidence_filter)
            findings = [f for f in findings if severity_rank(f["confidence"]) >= _floor_rank]

        if return_meta:
            meta = {
                "detectors_executed": executed,
                "detectors_failed": len(failed_detectors),
                "failed_detectors": failed_detectors,
                "profile": profile_key,
                "profile_filtered": profile_filtered,
                "detector_metadata": {task_id: _detector_meta(task_id) for task_id in executed_tasks},
                "framework": framework_active,
                "framework_unknown": framework_unknown,
            }
            # W1057: surface unknown --only/--exclude names ONLY when the
            # caller supplied them. Default path (neither flag set) emits no
            # new keys → byte-identical to pre-W1057 envelopes.
            if only_set:
                meta["only_unknown"] = only_unknown
            if exclude_set:
                meta["exclude_unknown"] = exclude_unknown
            return findings, meta

        return findings
    finally:
        # Always restore the prior active profile so a single run can't leak
        # into subsequent invocations or other commands sharing the process.
        _ACTIVE_FRAMEWORK_PROFILE = previous_profile
        _INCLUDE_TESTS_OVERRIDE = previous_include_tests
        # Reset the module-global idiom scope so it can't narrow a later run.
        # `_idiom_scope_reset` is the already-imported set_idiom_scope (captured
        # at apply-time); calling it with None is a trivial global assignment
        # that can't raise, so no guard is needed in the finally.
        if _idiom_scope_reset is not None:
            _idiom_scope_reset(None)
        if _js_idiom_scope_reset is not None:
            _js_idiom_scope_reset(None)
        # Reset the catalog source-read scope for the same reason.
        if _catalog_scope_applied:
            _DETECTOR_SCOPE_PATHS = None
