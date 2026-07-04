"""Code smell detection: query DB signals to find structural anti-patterns.

Each detector has signature ``(conn) -> list[dict]`` and returns findings
with fields: smell_id, severity, symbol_name, kind, location, metric_value,
threshold, description.

Severity levels:
- critical: High-impact structural issue that should be refactored
- warning: Moderate issue worth investigating
- info: Minor concern or code style observation

24 deterministic detectors querying the SQLite index. Most are pure DB
queries; ``detect_empty_catch``, ``detect_switch_statement``,
``detect_comment_density``, and a few others also read source files
referenced in the ``files`` table to walk their AST or scan comment
lines (the indexer does not extract every AST node or comment span
into queryable tables).

Three detectors live in their own modules to keep this file from
ballooning past the size already needed for the in-file detectors:
``detect_parallel_hierarchy`` (``roam.catalog.parallel_hierarchy``),
``detect_cross_layer_clones`` (``roam.catalog.clones_cross_layer``,
W856), and ``detect_type_switch`` (``roam.catalog.type_switch``,
W852). All three are imported here and registered via direct
``detector(...)(fn)`` calls near the bottom of this module so the
@detector decorator stays the single source of truth for the
registry. ``ALL_DETECTORS`` is a derived view over
``roam.catalog.registry.all_detectors()`` as of W941 -- the decorator
is the canonical registration point, not this list.
"""

from __future__ import annotations

import ast
import functools
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

from roam.catalog._shared import find_indexed_source_root as _find_indexed_source_root
from roam.catalog._shared import is_test_path as _is_test_path
from roam.catalog._shared import loc as _loc
from roam.catalog._shared import make_smell_finding as _finding
from roam.catalog.clones_cross_layer import detect_cross_layer_clones
from roam.catalog.parallel_hierarchy import detect_parallel_hierarchy
from roam.catalog.registry import all_detectors, detector, freeze_registry
from roam.catalog.type_switch import detect_type_switch
from roam.db.connection import batched_in
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
)
from roam.output._severity import severity_rank

log = logging.getLogger(__name__)

_PARAM_OPENERS = frozenset(("(", "[", "<", "{"))
_PARAM_CLOSERS = frozenset((")", "]", ">", "}"))
_IGNORED_PARAM_NAMES = frozenset(("", "self", "cls"))


# W1037: pin the OBSERVABLE module surface. New detectors are
# auto-included in ``ALL_DETECTORS`` via the @detector decorator (W941
# made the list a derived view over ``registry.all_detectors()``), so
# the public smell-detector surface is the derived view itself plus the
# top-level entry points (``run_all_detectors`` /
# ``file_health_scores``). The 21 in-file ``detect_*`` symbols are
# named individually because external test suites import them by name
# (``test_smells.py``, ``test_w853_speculative_generality.py``, ...).
__all__ = [
    "ALL_DETECTORS",
    "run_all_detectors",
    "file_health_scores",
    # Detector functions (alphabetical).
    "detect_boolean_parameter",
    "detect_brain_method",
    "detect_comment_density",
    "detect_data_clumps",
    "detect_dead_params",
    "detect_deep_nesting",
    "detect_duplicate_conditionals",
    "detect_empty_catch",
    "detect_feature_envy",
    "detect_god_class",
    "detect_large_class",
    "detect_long_params",
    "detect_low_cohesion",
    "detect_magic_numbers",
    "detect_message_chain",
    "detect_primitive_obsession",
    "detect_refused_bequest",
    "detect_shotgun_surgery",
    "detect_speculative_generality",
    "detect_switch_statement",
    "detect_temporal_coupling",
]


# W875 / W923: The canonical structural-smell envelope builder lives
# in ``roam.catalog._shared.make_smell_finding`` (aliased at import-
# time as ``_finding`` above). It is INTENTIONALLY NOT consolidated
# with the similarly-named ``_finding`` in ``roam.catalog.detectors``.
# The two share only 3 of ~14 union field names (``symbol_name``,
# ``kind``, ``location``) — a 21% Jaccard overlap on key sets. They
# produce semantically distinct envelopes:
#
#   - ``smells._finding`` (now == ``_shared.make_smell_finding``) is
#     the canonical STRUCTURAL-SMELL shape (fixed 8-key dict:
#     smell_id/severity/symbol_name/kind/location/metric_value/
#     threshold/description). Extended by
#     ``clones_cross_layer._make_finding`` and
#     ``parallel_hierarchy._finding`` with evidence/confidence/
#     detector_version via the same builder's kwargs. Callers pass
#     plain strings + numbers.
#   - ``detectors._finding`` is the ALGORITHM-CATALOG shape
#     (task_id/detected_way/suggested_way/symbol_id/symbol_line/
#     confidence/reason + optional evidence/fix). Callers pass a
#     sqlite3.Row ``sym`` and the helper derives ``symbol_name`` /
#     ``kind`` / ``location`` from it AND integrates with
#     ``tasks.best_way()``.
#
# Hoisting a shared base between the two FAMILIES would require
# either (a) collapsing both call-site contracts to the union (each
# detector then carries 14 unused fields) or (b) a thin base + two
# wrappers that's larger than the duplication it would replace.
# Both are net-negative. The W856 cross-layer-clone detector likely
# flagged this on the shared ``def _finding`` name — false positive
# at the consolidation layer.


def _signature_param_body(signature: str | None) -> str:
    """Return the balanced text inside the first parameter list."""
    if not signature:
        return ""
    start = signature.find("(")
    if start == -1:
        return ""
    depth = 0
    for idx, ch in enumerate(signature[start:], start=start):
        if ch in _PARAM_OPENERS:
            depth = depth + 1
        elif ch in _PARAM_CLOSERS:
            if depth == 0:
                continue
            depth = depth - 1
            if depth == 0:
                return signature[start + 1 : idx].strip()
    return ""


def _iter_top_level_param_parts(params_str: str) -> Iterator[str]:
    """Yield comma-delimited parameter parts without splitting nested commas."""
    depth = 0
    part_start = 0
    for idx, ch in enumerate(params_str):
        if ch in _PARAM_OPENERS:
            depth = depth + 1
        elif ch in _PARAM_CLOSERS and depth > 0:
            depth = depth - 1
        elif ch == "," and depth == 0:
            yield params_str[part_start:idx].strip()
            part_start = idx + 1
    tail = params_str[part_start:].strip()
    if tail:
        yield tail


def _signature_param_name(param: str) -> str:
    return param.split(":", 1)[0].split("=", 1)[0].strip().lower()


def _parse_param_count(signature: str | None) -> int:
    """Count parameters from a signature string, excluding self/cls."""
    params_str = _signature_param_body(signature)
    if not params_str:
        return 0
    return sum(
        1
        for param in _iter_top_level_param_parts(params_str)
        if _signature_param_name(param) not in _IGNORED_PARAM_NAMES
    )


# ---------------------------------------------------------------------------
# W1301 — shared per-run AST cache.
#
# Three Python-only detectors (``detect_magic_numbers``,
# ``detect_boolean_parameter``, ``detect_switch_statement``) each used to
# independently ``read_text()`` + ``ast.parse()`` every Python file in the
# index. On roam-code (~1667 Python files) one ``ast.parse`` pass costs
# ~8.7s; three detectors redoing it independently is ~26s -- the dominant
# cost of ``roam smells`` (profiled: ~51s total, ~28s of it triple-parse).
#
# ``_read_and_parse`` parses each file exactly once and memoises the
# resulting ``ast.Module`` against an ``(abs_path, mtime_ns, size)`` key.
# The mtime+size component makes the cache self-invalidating: an edited
# file re-parses, so a long-lived process (the MCP server reusing one
# Python interpreter across many ``roam smells`` calls) never serves a
# stale tree. The cache is bounded (``maxsize``) so it cannot grow without
# limit on a very large monorepo; the LRU eviction simply re-parses an
# evicted file on next access (correctness-neutral, perf-graceful).
#
# Output-identical guarantee: every consumer still receives the SAME
# ``ast.Module`` it would have parsed itself (``ast.parse`` is pure for a
# fixed source string), so the finding set is byte-identical -- this is a
# work-deduplication refactor, not a behavioural change.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=4096)
def _parse_source_cached(_abs_path: str, _mtime_ns: int, _size: int) -> ast.Module | None:
    """Read + ``ast.parse`` one file, memoised on (path, mtime_ns, size).

    Returns the parsed ``ast.Module`` or ``None`` when the file cannot be
    read or fails to parse (``SyntaxError`` / ``OSError`` / ``ValueError``).
    The ``_mtime_ns`` + ``_size`` cache-key components self-invalidate on
    any file edit so a reused interpreter never serves a stale tree.
    """
    try:
        with open(_abs_path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except (OSError, ValueError) as exc:
        # Loud-fallback lineage: disclose WHICH file was skipped so an
        # operator chasing missing AST-detector findings can see the read
        # failure rather than a silent gap.
        log.debug("_parse_source_cached: read failed for %r: %s", _abs_path, exc)
        return None
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        log.debug("_parse_source_cached: parse failed for %r: %s", _abs_path, exc)
        return None


def _read_and_parse(workspace, rel_path: str) -> ast.Module | None:
    """Return the cached ``ast.Module`` for ``workspace / rel_path``.

    Thin wrapper that stats the file (cheap) to build the cache key, then
    delegates to :func:`_parse_source_cached`. Returns ``None`` on any
    read/stat/parse failure so callers keep their existing skip-on-None
    control flow. Shared by the three Python-only AST detectors so each
    file is parsed at most once per run.
    """
    abs_path = workspace / rel_path
    try:
        st = abs_path.stat()
    except OSError as exc:
        log.debug("_read_and_parse: stat failed for %r: %s", abs_path, exc)
        return None
    return _parse_source_cached(str(abs_path), st.st_mtime_ns, st.st_size)


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


# Tier: static_analysis — deterministic AST/CFG metric thresholds
# (cognitive_complexity + LOC). Same input -> same score, no name patterns.
@detector("brain-method", confidence=CONFIDENCE_STATIC_ANALYSIS)
def detect_brain_method(conn: sqlite3.Connection) -> list[dict]:
    """Functions with complexity > 60 AND > 100 LOC."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.line_end, f.path as file_path, "
        "sm.cognitive_complexity "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND sm.cognitive_complexity > 60 "
        "AND (s.line_end - s.line_start) > 100"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        line_count = (r["line_end"] or 0) - (r["line_start"] or 0)
        results.append(
            _finding(
                "brain-method",
                "critical",
                r["name"],
                r["kind"],
                loc_str,
                r["cognitive_complexity"],
                60,
                f"Brain method: complexity {r['cognitive_complexity']:.0f}, {line_count} LOC",
            )
        )
    return results


# Tier: static_analysis — deterministic AST metric (nesting_depth) over a
# fixed threshold; no heuristic or name match involved.
@detector("deep-nesting", confidence=CONFIDENCE_STATIC_ANALYSIS)
def detect_deep_nesting(conn: sqlite3.Connection) -> list[dict]:
    """Symbols with nesting depth > 4."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, f.path as file_path, "
        "sm.nesting_depth "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE sm.nesting_depth > 4 "
        "AND s.kind IN ('function', 'method')"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(
            _finding(
                "deep-nesting",
                "warning",
                r["name"],
                r["kind"],
                loc_str,
                r["nesting_depth"],
                4,
                f"Deep nesting: depth {r['nesting_depth']}",
            )
        )
    return results


# Tier: static_analysis — deterministic parameter-count parsing of the
# stored signature; self/cls excluded; threshold predicate, no FP-prone signal.
@detector("long-params", confidence=CONFIDENCE_STATIC_ANALYSIS)
def detect_long_params(conn: sqlite3.Connection) -> list[dict]:
    """Functions with > 5 parameters (excluding self/cls)."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.signature IS NOT NULL "
        "AND s.signature != ''"
    ).fetchall()
    results = []
    for r in rows:
        count = _parse_param_count(r["signature"])
        if count > 5:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(
                _finding(
                    "long-params",
                    "warning",
                    r["name"],
                    r["kind"],
                    loc_str,
                    count,
                    5,
                    f"Long parameter list: {count} params",
                )
            )
    return results


# Tier: static_analysis — class-LOC + method-count, both deterministic
# AST-derived metrics. Threshold-only predicate, no heuristic signal.
@detector("large-class", confidence=CONFIDENCE_STATIC_ANALYSIS)
def detect_large_class(conn: sqlite3.Connection) -> list[dict]:
    """Classes with > 500 LOC AND > 20 methods."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, s.file_id, "
        "f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class' "
        "AND (s.line_end - s.line_start) > 500"
    ).fetchall()
    # Bulk-fetch method spans for the candidate classes' files ONCE,
    # replacing the per-class ``SELECT COUNT(*)`` N+1 query (which also
    # re-derived file_id via a subquery per row). The per-class line-range
    # containment runs in Python below; a method with a NULL line_start /
    # line_end is excluded, mirroring the old SQL's falsy NULL comparison.
    from collections import defaultdict

    methods_by_file: dict[int, list[tuple[int | None, int | None]]] = defaultdict(list)
    candidate_file_ids = sorted({r["file_id"] for r in rows})
    if candidate_file_ids:
        for m in batched_in(
            conn,
            "SELECT file_id, line_start, line_end FROM symbols WHERE file_id IN ({ph}) AND kind = 'method'",
            candidate_file_ids,
        ):
            methods_by_file[m["file_id"]].append((m["line_start"], m["line_end"]))
    results = []
    for r in rows:
        ls, le = r["line_start"] or 0, r["line_end"] or 0
        method_count = sum(
            1
            for (m_ls, m_le) in methods_by_file.get(r["file_id"], [])
            if m_ls is not None and m_le is not None and m_ls >= ls and m_le <= le
        )
        if method_count > 20:
            loc_str = _loc(r["file_path"], r["line_start"])
            line_count = (r["line_end"] or 0) - (r["line_start"] or 0)
            results.append(
                _finding(
                    "large-class",
                    "critical",
                    r["name"],
                    r["kind"],
                    loc_str,
                    line_count,
                    500,
                    f"Large class: {line_count} LOC, {method_count} methods",
                )
            )
    return results


# Tier: structural — combines method-count and class-LOC from the
# symbols/files graph; predicate is a threshold over graph-extracted shape,
# not a name match. Higher signal than ``large-class`` but still a threshold,
# so it lands on the structural tier rather than static_analysis.
def _count_methods_in_class(conn: sqlite3.Connection, file_id: int, line_start: int, line_end: int) -> int:
    """Count methods nested inside a class's line span."""
    return conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE file_id = ? AND kind = 'method' AND line_start >= ? AND line_end <= ?",
        (file_id, line_start, line_end),
    ).fetchone()[0]


def _format_god_class_parts(method_count: int, line_count: int) -> list[str]:
    """Build the human-readable fragments for whichever thresholds tripped."""
    parts = []
    if method_count > 30:
        parts.append(f"{method_count} methods")
    if line_count > 1000:
        parts.append(f"{line_count} LOC")
    return parts


def _build_god_class_finding(r, method_count: int, line_count: int) -> dict:
    """Assemble a god-class finding dict from the row + computed counts."""
    loc_str = _loc(r["file_path"], r["line_start"])
    metric = max(method_count, line_count)
    threshold = 30 if method_count > 30 else 1000
    parts = _format_god_class_parts(method_count, line_count)
    return _finding(
        "god-class",
        "critical",
        r["name"],
        r["kind"],
        loc_str,
        metric,
        threshold,
        f"God class: {', '.join(parts)}",
    )


@detector("god-class", confidence=CONFIDENCE_STRUCTURAL)
def detect_god_class(conn: sqlite3.Connection) -> list[dict]:
    """Classes with > 30 methods OR > 1000 LOC."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "f.path as file_path, s.file_id "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class'"
    ).fetchall()
    results = []
    for r in rows:
        line_count = (r["line_end"] or 0) - (r["line_start"] or 0)
        method_count = _count_methods_in_class(conn, r["file_id"], r["line_start"] or 0, r["line_end"] or 0)
        if method_count > 30 or line_count > 1000:
            results.append(_build_god_class_finding(r, method_count, line_count))
    return results


# Tier: structural — walks the call-graph edges of each function and checks
# the cross-file ratio. The signal is graph topology (target file_ids vs
# source file_id), not name pattern, so the tier is structural.
#
# W1280 FP-reduction ((internal memo)): the pre-W1280
# detector was a pure cross-FILE outbound-edge-ratio heuristic. A 24-sample
# measured ~88% FP / 0% true-positive — 76% of hits were in tests/ and 82%
# of the src hits were Click command modules / emit_/build_/collect_/_section_
# orchestrators that NECESSARILY reference many modules. Two tunings (the
# memo's preferred (1)+(2)) restore the classic feature-envy signal
# (a method using ONE foreign unit's members more than its own):
#   (1) skip test-role files + orchestrator/assembler-named functions, and
#   (2) require the external refs to be CONCENTRATED on a single foreign
#       file (true envy) rather than spread across many (orchestration).
# The min-edges floor (4) and external ratio (0.5) are intentionally left
# unchanged — that was the memo's lowest-priority option (3), not preferred.

# Functions whose external-ref breadth is structural, not envy: assemblers
# and orchestrators reference many modules by design. Matches a leading
# (optional ``_``) assembler verb prefix OR a ``_findings`` suffix OR a
# ``test_`` prefix (defence-in-depth alongside the file-role test skip).
_FEATURE_ENVY_ORCHESTRATOR_RE = re.compile(
    r"^(?:_?(?:emit|build|render|collect|assemble|section)_|test_|_section_)|_findings$"
)
# Of an envy candidate's external refs, the single most-referenced foreign
# file must carry at least this share. A function spreading its external
# refs across many files (max_single/external below this) is orchestration,
# not envy; one that hammers a single foreign file's members IS envy.
_FEATURE_ENVY_MIN_DOMINANT_FOREIGN_SHARE = 0.5


@detector("feature-envy", confidence=CONFIDENCE_STRUCTURAL)
def detect_feature_envy(conn: sqlite3.Connection) -> list[dict]:
    """Functions whose external refs are concentrated on ONE foreign file.

    Fires when > 50% of a function's >=4 outbound edges target other files
    AND those external refs are dominated by a single foreign file (true
    feature envy). Skips test-role files and orchestrator/assembler-named
    functions (emit_/build_/render_/collect_/assemble_/section_/_findings),
    whose cross-file breadth is by-design coupling, not envy (W1280).
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.file_id, "
        "f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()
    # Bulk-fetch every outbound edge ONCE and bucket by source_id, replacing
    # the per-symbol ``WHERE e.source_id = ?`` N+1 query. The in-memory bucket
    # is keyed by source symbol id; iteration order over ``rows`` below is
    # unchanged so the emitted findings stay byte-identical.
    from collections import defaultdict

    edges_by_source: dict[int, list[tuple[int, int]]] = defaultdict(list)
    edge_rows = conn.execute(
        "SELECT e.source_id, e.target_id, t.file_id as target_file_id FROM edges e JOIN symbols t ON e.target_id = t.id"
    ).fetchall()
    for e in edge_rows:
        edges_by_source[e["source_id"]].append((e["target_id"], e["target_file_id"]))
    results = []
    for r in rows:
        # (1) Skip test-role files + orchestrator/assembler-named functions.
        if _is_test_path(r["file_path"]):
            continue
        if _FEATURE_ENVY_ORCHESTRATOR_RE.search(r["name"] or ""):
            continue
        edges = edges_by_source.get(r["id"], [])
        total = len(edges)
        if total < 4:
            continue
        external = sum(1 for (_tid, tfid) in edges if tfid != r["file_id"])
        ratio = external / total
        if ratio <= 0.5:
            continue
        # (2) Concentration gate: the external refs must be dominated by one
        # foreign file. Spread-across-many-files is orchestration, not envy.
        foreign_counts = Counter(tfid for (_tid, tfid) in edges if tfid != r["file_id"])
        max_foreign = max(foreign_counts.values())
        dominant_share = max_foreign / external
        if dominant_share < _FEATURE_ENVY_MIN_DOMINANT_FOREIGN_SHARE:
            continue
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(
            _finding(
                "feature-envy",
                "warning",
                r["name"],
                r["kind"],
                loc_str,
                round(ratio * 100, 1),
                50,
                f"Feature envy: {external}/{total} refs ({ratio:.0%}) external, "
                f"{max_foreign} to one foreign file ({dominant_share:.0%} of external)",
            )
        )
    return results


# Tier: structural — caller-FILE scatter over the call-graph edges
# (distinct source files of incoming edges) gated on git co-change
# coherence, not a name heuristic. The signal is graph + git topology, so
# the tier stays structural.
#
# W1287 RE-IMPLEMENTATION ((internal memo)): the pre-W1287
# detector fired on ``graph_metrics.in_degree > 7`` — pure INBOUND
# popularity. An 18-sample dogfood measured ~100% FP / 0 TP: the top hits
# were the codebase's BEST shared symbols (conftest fixtures invoke_cli/
# cli_runner, helpers open_db/json_envelope/to_json, dataclass field
# EvidenceArtifact.path), 69% in tests/. High inbound reference count is
# good factoring — the OPPOSITE of the smell. The module comment at the
# ``message-chain`` registration confirms message-chain is the out_degree
# axis, so the in_degree impl was the wrong axis entirely. W1287 replaced
# it with a distinct-non-test-caller-FILE scatter count.
#
# W1300 COHERENCE GATE (the memo's fix-1 SECOND clause, restored): W1287
# shipped the scatter axis but DROPPED the memo's "require the callees to
# be a coherent change-set" requirement. Re-measurement confirmed the
# predicted residual FP: at scatter>=12 the 27 roam-code rows were ALL
# well-factored hubs (to_json/open_db/json_envelope/ensure_index/
# find_project_root) — scatter alone cannot tell "one symbol forces
# scattered edits" (surgery) apart from "one symbol is reused everywhere"
# (centralization); both score high scatter. The discriminator is git
# co-change COHERENCE among the caller files themselves: a genuine
# shotgun-surgery symbol's scattered caller files co-evolve in shared
# commits (a real ripple); a reuse hub's caller files each touched the
# utility once and never co-change with each other. Measured on roam-code:
# hubs cluster at 5-9% pairwise coherence, genuine change-clusters at
# 19-48% — a clean bimodal split, cut at ``_SHOTGUN_MIN_COHERENCE`` (0.15).
# The gate is GRACEFUL: when ``git_cochange`` is empty (no git history, or
# a unit-test fixture that does not populate it) it degrades to the W1287
# scatter-only behaviour so fixtures + no-history repos keep firing.
# A well-factored repo SHOULD still report ~zero rows. The kind stays
# registered (retiring it triggers count-drift / registry ripple). Tune
# ``_SHOTGUN_MIN_CALLER_FILES`` / ``_SHOTGUN_MIN_COHERENCE`` UP, never
# down, if a future corpus re-introduces FPs.
_SHOTGUN_MIN_CALLER_FILES = 12
# Minimum pairwise co-change coherence among a candidate's distinct caller
# files for the scatter to count as a genuine change-ripple rather than a
# centralization artifact. 0.15 sits in the empty band between the
# roam-code reuse-hub cluster (<=0.12) and the genuine-cluster band
# (>=0.19). Only applied when git_cochange has rows (graceful degrade).
_SHOTGUN_MIN_COHERENCE = 0.15


def _load_shotgun_callers_by_target(conn: sqlite3.Connection) -> dict[int, list[tuple[int | None, str]]]:
    """Bucket incoming caller files once so scatter decisions avoid N+1 SQL."""
    caller_rows = conn.execute(
        "SELECT e.target_id AS tid, "
        "COALESCE(e.source_file_id, ss.file_id) AS cf, "
        "cf2.path AS cpath "
        "FROM edges e "
        "JOIN symbols ss ON e.source_id = ss.id "
        "JOIN files cf2 ON cf2.id = COALESCE(e.source_file_id, ss.file_id) "
        "ORDER BY e.target_id"
    ).fetchall()
    return {
        tid: [(row["cf"], row["cpath"]) for row in rows]
        for tid, rows in groupby(caller_rows, key=lambda row: row["tid"])
    }


def _load_shotgun_cochange_evidence(conn: sqlite3.Connection) -> tuple[dict[int, set[int]], bool]:
    """Load the coherence evidence that separates reuse hubs from change ripples."""
    cochange_adj: dict[int, set[int]] = defaultdict(set)
    try:
        cochange_rows = conn.execute(
            "SELECT file_id_a, file_id_b, cochange_count FROM git_cochange WHERE cochange_count >= 2"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}, False

    for ca, cb, _cc in cochange_rows:
        cochange_adj[ca].add(cb)
        cochange_adj[cb].add(ca)
    return dict(cochange_adj), bool(cochange_rows)


def _can_symbol_change_ripple(row: sqlite3.Row) -> bool:
    """Accept symbols whose wide caller scatter is not by-design surface area."""
    if _is_test_path(row["file_path"]):
        return False
    dec = row["decorators"] or ""
    if "@property" in dec or "@cached_property" in dec:
        return False
    line_start, line_end = row["line_start"], row["line_end"]
    if line_start is not None and line_end is not None and (line_end - line_start) <= 2:
        return False
    return True


def _caller_files_that_would_ripple(
    row: sqlite3.Row,
    callers_by_target: dict[int, list[tuple[int | None, str]]],
) -> tuple[set[int], set[str]]:
    """Return distinct non-test caller files where a target edit can ripple."""
    caller_file_ids: set[int] = set()
    caller_file_paths: set[str] = set()
    for caller_file_id, caller_path in callers_by_target.get(row["id"], ()):
        if caller_file_id is None:
            continue
        if caller_file_id == row["file_id"] or _is_test_path(caller_path):
            continue
        caller_file_ids.add(caller_file_id)
        caller_file_paths.add(caller_path)
    return caller_file_ids, caller_file_paths


def _pairwise_cochange_coherence(
    caller_file_ids: set[int],
    cochange_adj: dict[int, set[int]],
) -> float:
    """Measure whether scattered callers evolve together as one change ripple."""
    file_ids = sorted(caller_file_ids)
    total_pairs = 0
    coupled_pairs = 0
    for left_idx, left_id in enumerate(file_ids):
        adjacent = cochange_adj.get(left_id, ())
        for right_id in file_ids[left_idx + 1 :]:
            total_pairs += 1
            if right_id in adjacent:
                coupled_pairs += 1
    return (coupled_pairs / total_pairs) if total_pairs else 0.0


def _build_shotgun_surgery_finding(row: sqlite3.Row, scatter: int, coherence: float) -> dict:
    """Render the one finding only after scatter and coherence both survive."""
    loc_str = _loc(row["file_path"], row["line_start"])
    return _finding(
        "shotgun-surgery",
        "warning",
        row["name"],
        row["kind"],
        loc_str,
        scatter,
        _SHOTGUN_MIN_CALLER_FILES,
        f"Shotgun surgery: referenced from {scatter} distinct non-test files "
        f"that co-evolve ({coherence:.0%} pairwise co-change coherence); "
        f"a change ripples across all of them",
    )


def _shotgun_surgery_finding_when_callers_cohere(
    row: sqlite3.Row,
    callers_by_target: dict[int, list[tuple[int | None, str]]],
    cochange_adj: dict[int, set[int]],
    cochange_present: bool,
) -> dict | None:
    """Keep scatter-only reuse hubs from masquerading as shotgun surgery."""
    if not _can_symbol_change_ripple(row):
        return None

    caller_file_ids, caller_file_paths = _caller_files_that_would_ripple(row, callers_by_target)
    scatter = len(caller_file_paths)
    if scatter < _SHOTGUN_MIN_CALLER_FILES:
        return None

    coherence = 1.0  # default when the gate cannot apply (no git data)
    if cochange_present:
        coherence = _pairwise_cochange_coherence(caller_file_ids, cochange_adj)
        if coherence < _SHOTGUN_MIN_COHERENCE:
            return None

    return _build_shotgun_surgery_finding(row, scatter, coherence)


@detector("shotgun-surgery", confidence=CONFIDENCE_STRUCTURAL)
def detect_shotgun_surgery(conn: sqlite3.Connection) -> list[dict]:
    """Symbols whose change ripples across many CO-EVOLVING caller files.

    Fires when a function/method is referenced (incoming call/use edges)
    from at least ``_SHOTGUN_MIN_CALLER_FILES`` (12) DISTINCT non-test
    files that are NOT the symbol's own file (Fowler's file-SCATTER axis)
    AND those caller files form a coherent change-cluster — their pairwise
    git co-change coherence is at least ``_SHOTGUN_MIN_COHERENCE`` (0.15).
    The coherence gate is the discriminator that separates a genuine
    change-ripple from a well-factored reuse hub (``to_json`` / ``open_db``
    are referenced everywhere but their callers do NOT co-evolve): scatter
    alone is centralization, scatter + coherence is shotgun surgery.

    Excludes test-role target files, ``@property`` / dataclass-field
    symbols, and trivial 1-3 line accessors (mirrors the W1280 feature-envy
    exclusion style). The coherence gate degrades gracefully — when
    ``git_cochange`` has no rows (no git history, or a unit-test fixture),
    the detector falls back to the W1287 scatter-only predicate. See the
    W1287 / W1300 comment above for why inbound popularity alone is NOT
    this smell.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, s.file_id, "
        "s.decorators, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()
    # Bulk-fetch every incoming edge and the file co-change graph once.
    # Scatter and coherence are then evaluated per symbol in pure helpers.
    callers_by_target = _load_shotgun_callers_by_target(conn)
    cochange_adj, cochange_present = _load_shotgun_cochange_evidence(conn)
    candidate_findings = (
        _shotgun_surgery_finding_when_callers_cohere(
            row,
            callers_by_target,
            cochange_adj,
            cochange_present,
        )
        for row in rows
    )
    return [finding for finding in candidate_findings if finding is not None]


# Tier: heuristic — groups by the sorted top-3 param NAMES across signatures.
# Name-dependent pattern (two functions can share param names without
# carrying the same concept) -> heuristic tier surfaces the FP risk.
@detector("data-clumps", confidence=CONFIDENCE_HEURISTIC)
def detect_data_clumps(conn: sqlite3.Connection) -> list[dict]:
    """3+ params repeated across 3+ functions (group by sorted first-3 param names)."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.signature IS NOT NULL "
        "AND s.signature != ''"
    ).fetchall()

    # Build param-group map
    from collections import defaultdict

    param_groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        params_str = _signature_param_body(r["signature"])
        if not params_str:
            continue
        names = [
            name
            for p in _iter_top_level_param_parts(params_str)
            if (name := _signature_param_name(p)) not in _IGNORED_PARAM_NAMES
        ]
        if len(names) >= 3:
            key = ",".join(sorted(names[:3]))
            param_groups[key].append(r)

    results = []
    seen_groups: set[str] = set()
    for key, funcs in param_groups.items():
        if len(funcs) >= 3 and key not in seen_groups:
            seen_groups.add(key)
            # Report one finding per clump using the first function
            r = funcs[0]
            loc_str = _loc(r["file_path"], r["line_start"])
            func_names = [f["name"] for f in funcs[:5]]
            results.append(
                _finding(
                    "data-clumps",
                    "info",
                    r["name"],
                    r["kind"],
                    loc_str,
                    len(funcs),
                    3,
                    f"Data clump: params ({key}) repeated in {len(funcs)} functions: {', '.join(func_names)}",
                )
            )
    return results


# W163: names whose "many params, low complexity" shape is the
# DEFINITION of the symbol, not a smell. Per W149 dogfood audit, ~40 %
# of dead-params findings were on constructors / lifecycle hooks where
# the body legitimately just stores params to ``self`` (or is empty
# because the implementation is auto-generated). Filtering these out
# at the detector level keeps the smell honest.
#
# Coverage:
#   * ``__init__``        — Python constructors.
#   * ``__post_init__``   — dataclass post-init hook.
#   * ``__new__``         — Python factory constructors.
#   * ``setUp`` / ``tearDown`` / ``setup_*`` / ``teardown_*`` —
#     pytest + unittest lifecycle hooks; canonically receive fixtures
#     and store them to self or to module-level state.
_DEAD_PARAMS_EXEMPT_NAMES: frozenset[str] = frozenset(
    {
        "__init__",
        "__post_init__",
        "__new__",
        "setUp",
        "tearDown",
    }
)

# Prefix-based exemptions (pytest's ``setup_method``, ``setup_class``,
# ``setup_function``, plus the ``teardown_*`` mirrors). Kept as a small
# tuple for ``str.startswith()`` rather than a frozenset because the
# match is a prefix, not equality.
_DEAD_PARAMS_EXEMPT_PREFIXES: tuple[str, ...] = ("setup_", "teardown_")


def _is_dead_params_exempt(symbol_name: str, parent_decorators: str | None) -> bool:
    """Decide whether to suppress a dead-params finding for this symbol.

    Suppresses when:
      1. ``symbol_name`` is a constructor / dataclass / lifecycle dunder
         in ``_DEAD_PARAMS_EXEMPT_NAMES``.
      2. ``symbol_name`` starts with ``setup_`` / ``teardown_`` (pytest
         + unittest fixture lifecycle).
      3. The enclosing class is decorated with ``@dataclass`` — the
         method body is auto-generated by the decorator, so "many
         params, low complexity" is by construction, not a smell.

    The dataclass check is symbol-table-based: ``decorators`` on the
    parent class is the comma-joined output of the Python extractor
    (``@dataclass`` or ``@dataclass(frozen=True)`` …), so a simple
    substring match against ``dataclass`` is sufficient without any
    re-parsing.
    """
    if symbol_name in _DEAD_PARAMS_EXEMPT_NAMES:
        return True
    if any(symbol_name.startswith(p) for p in _DEAD_PARAMS_EXEMPT_PREFIXES):
        return True
    if parent_decorators and "dataclass" in parent_decorators:
        return True
    return False


# Tier: static_analysis — joins param-count parsing with the cognitive-
# complexity AST metric. Deterministic per-symbol predicate; constructors
# / lifecycle hooks / dataclass methods are filtered upstream via
# ``_is_dead_params_exempt`` so the residual FP rate is low.
@detector("dead-params", confidence=CONFIDENCE_STATIC_ANALYSIS)
def detect_dead_params(conn: sqlite3.Connection) -> list[dict]:
    """Functions with 4+ params but complexity <= 1 (likely unused params).

    W163: skip constructors (``__init__`` / ``__new__``), dataclass
    auto-generated methods (parent class decorated with ``@dataclass``,
    plus ``__post_init__``), and pytest/unittest lifecycle hooks
    (``setUp`` / ``tearDown`` / ``setup_*`` / ``teardown_*``). For
    these shapes "many params, low complexity" is the definition of
    the symbol, not a code smell — see W149 dogfood audit.

    The decorator-on-parent-class arm of the rule reads
    ``symbols.decorators`` via a LEFT JOIN on ``parent_id``. Pre-v9
    indexes (and the hand-rolled minimal schema used by some unit
    tests) lack that column; the query falls back transparently to a
    name-only exemption check in that case.
    """
    try:
        rows = conn.execute(
            "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path, "
            "sm.cognitive_complexity, p.decorators as parent_decorators "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
            "LEFT JOIN symbols p ON p.id = s.parent_id "
            "WHERE s.kind IN ('function', 'method') "
            "AND sm.cognitive_complexity <= 1 "
            "AND s.signature IS NOT NULL "
            "AND s.signature != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        # ``symbols.decorators`` absent (pre-v9 index, or test fixture
        # with a stripped-down schema). Run the legacy query and treat
        # parent_decorators as NULL for every row — name-based
        # exemptions still fire, dataclass detection silently degrades
        # to a no-op.
        rows = conn.execute(
            "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path, "
            "sm.cognitive_complexity, NULL as parent_decorators "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND sm.cognitive_complexity <= 1 "
            "AND s.signature IS NOT NULL "
            "AND s.signature != ''"
        ).fetchall()
    results = []
    for r in rows:
        if _is_dead_params_exempt(r["name"], r["parent_decorators"]):
            continue
        count = _parse_param_count(r["signature"])
        if count >= 4:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(
                _finding(
                    "dead-params",
                    "info",
                    r["name"],
                    r["kind"],
                    loc_str,
                    count,
                    4,
                    f"Dead params: {count} params but complexity {r['cognitive_complexity']:.0f}",
                )
            )
    return results


# ---------------------------------------------------------------------------
# W370 — empty-catch detector
#
# Definition: a try/except (Python) or try/catch (other langs) block where
# the handler body is trivially empty (``pass`` / ``...`` / empty block /
# comment-only / single log-call / single ``return None``). This is the
# highest-signal AI-rot pattern per the 2025 research papers (per the
# W368 detector-competitive audit). AI-generated code commonly emits these
# to "satisfy" exception-handling without actually handling the failure.
#
# Re-raise (``raise`` / ``throw``) is NOT empty-catch — the handler
# propagates the error up, which is meaningful.
# Recovery code (assignments, function calls other than logging) is NOT
# empty-catch — the handler did something useful.
#
# The indexer does not extract exception_handler bodies into a queryable
# table (only ``except_clause`` / ``catch_clause`` AST nodes — used for
# complexity scoring, not persisted). So this detector reads source files
# referenced in the ``files`` table and applies per-language regex.
# ---------------------------------------------------------------------------

# Trivial single-statement bodies (matched against the body text after
# stripping comments + whitespace). A handler counts as empty-catch when
# the body matches one of these AFTER excluding re-raise lines.
_TRIVIAL_BODY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*pass\s*$"),
    re.compile(r"^\s*\.\.\.\s*$"),
    re.compile(r"^\s*$"),  # truly empty body
    re.compile(r"^\s*(?:return|return\s+None|return\s+null|return\s+undefined)\s*;?\s*$"),
    # single log call: print(...) / console.log(...) / logger.X(...) /
    # log.X(...) / System.out.println(...). Anchored so we don't match a
    # call with chained behaviour.
    re.compile(
        r"^\s*(?:print|console\.(?:log|error|warn|info|debug)|"
        r"(?:log|logger|logging|fmt|sys\.stderr|sys\.stdout)\.[A-Za-z_][A-Za-z0-9_]*|"
        r"System\.(?:out|err)\.println)\s*\([^;{}]*\)\s*;?\s*$"
    ),
)

# Re-raise / throw — lines that mean "I did NOT swallow the error".
_RERAISE_LINE = re.compile(r"^\s*(?:raise|throw)\b")

# Locate Python ``except ...:`` headers in a single pass with their
# column offsets — we need the indent level to find the body's end.
_PY_EXCEPT_HEADER = re.compile(r"^([ \t]*)except\b[^\n:]*:\s*(?:#.*)?$", re.MULTILINE)

# Locate ``catch (...)`` openings for brace languages (JS/TS/Java/C#/Kotlin/Swift).
# The ``(?<!\.)`` guard avoids Promise ``.catch(...)`` fallbacks like
# ``response.json().catch(() => ({}))``.
_BRACE_CATCH_HEADER = re.compile(r"(?<![A-Za-z0-9_.])catch\s*(?:\([^)]*\)\s*)?\{", re.MULTILINE)

# Languages whose catch syntax is brace-delimited.
_BRACE_LANGS: frozenset[str] = frozenset({"javascript", "typescript", "java", "c_sharp", "kotlin", "swift", "scala"})


def _strip_comments_and_blanks(body: str, lang: str) -> str:
    """Remove comments + blank lines from a handler body.

    Used as the "is this body trivial?" preprocessor. Multi-line block
    comments are stripped first so a body of just ``/* TODO */`` reduces
    to empty.
    """
    if not body:
        return ""
    # Strip block comments first (brace langs).
    if lang in _BRACE_LANGS:
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    # Strip line comments per language.
    if lang == "python":
        body = re.sub(r"#.*$", "", body, flags=re.MULTILINE)
    elif lang in _BRACE_LANGS or lang == "go":
        body = re.sub(r"//.*$", "", body, flags=re.MULTILINE)
    elif lang == "ruby":
        body = re.sub(r"#.*$", "", body, flags=re.MULTILINE)
    # Drop blank lines.
    lines = [ln for ln in body.splitlines() if ln.strip()]
    return "\n".join(lines)


def _is_trivial_body(body: str, lang: str) -> bool:
    """Decide whether *body* counts as an empty-catch handler.

    Returns True when (after stripping comments + blank lines):
      * body is empty, OR
      * body is exactly one of the trivial statements (pass / ... /
        return / single log call), AND
      * body contains NO ``raise`` / ``throw`` lines (re-raise excludes).

    Multi-statement bodies are not trivial. A single log call followed
    by ``raise`` is re-raise, not empty-catch.
    """
    cleaned = _strip_comments_and_blanks(body, lang)
    if not cleaned:
        return True
    # Re-raise guard: any line that looks like ``raise`` / ``throw`` ->
    # not empty-catch.
    for line in cleaned.splitlines():
        if _RERAISE_LINE.match(line):
            return False
    # Single-statement check.
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    single = lines[0]
    return any(p.match(single) for p in _TRIVIAL_BODY_PATTERNS)


def _extract_python_handlers(source: str) -> list[tuple[int, str]]:
    """Yield (line_number, body_text) per ``except`` block in Python source.

    Body extraction follows the indent rule: the body runs from the line
    after the ``except:`` header until the next line whose indent is
    ``<=`` the header's indent (or end of file).
    """
    lines = source.split("\n")
    handlers: list[tuple[int, str]] = []
    for m in _PY_EXCEPT_HEADER.finditer(source):
        header_indent = m.group(1)
        header_start = m.start()
        header_line_no = source.count("\n", 0, header_start) + 1
        # Find body lines.
        body_lines: list[str] = []
        # Same-line body: ``except E: pass`` style.
        same_line_tail = lines[header_line_no - 1].split(":", 1)[-1].strip()
        # Strip trailing comment on the header line itself.
        same_line_tail = re.sub(r"#.*$", "", same_line_tail).strip()
        if same_line_tail:
            body_lines.append(same_line_tail)
        # Next-line indented body.
        i = header_line_no  # 0-based index of next line
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                body_lines.append(line)
                i += 1
                continue
            # Indent of this line.
            line_indent = line[: len(line) - len(line.lstrip(" \t"))]
            # Body must be indented MORE than the header.
            if len(line_indent) > len(header_indent):
                body_lines.append(line)
                i += 1
            else:
                break
        body = "\n".join(body_lines)
        handlers.append((header_line_no, body))
    return handlers


def _extract_brace_handlers(source: str) -> list[tuple[int, str]]:
    """Yield (line_number, body_text) per ``catch (...) { ... }`` block.

    Body extraction uses brace-balancing starting from the ``{`` matched
    by the catch header. Strings + nested braces are tracked so the body
    is the literal text between the matched ``{`` and its closing ``}``.
    """
    handlers: list[tuple[int, str]] = []
    for m in _BRACE_CATCH_HEADER.finditer(source):
        open_brace_pos = m.end() - 1  # the matched ``{``
        line_no = source.count("\n", 0, m.start()) + 1
        # Brace-balance to find the matching close.
        depth = 0
        i = open_brace_pos
        in_string: str | None = None
        in_line_comment = False
        in_block_comment = False
        end = -1
        while i < len(source):
            ch = source[i]
            nxt = source[i + 1] if i + 1 < len(source) else ""
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
            elif in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 1
            elif in_string is not None:
                if ch == "\\":
                    i += 1  # skip escape
                elif ch == in_string:
                    in_string = None
            else:
                if ch == "/" and nxt == "/":
                    in_line_comment = True
                    i += 1
                elif ch == "/" and nxt == "*":
                    in_block_comment = True
                    i += 1
                elif ch in ('"', "'", "`"):
                    in_string = ch
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            i += 1
        if end < 0:
            continue
        body = source[open_brace_pos + 1 : end]
        handlers.append((line_no, body))
    return handlers


def _enclosing_symbol(conn: sqlite3.Connection, file_id: int, line: int) -> tuple[str, str, int]:
    """Return (symbol_name, kind, line_start) for the enclosing function.

    Falls back to (``"<module>"``, ``"file"``, ``line``) when no enclosing
    function/method is found (top-level try/except).
    """
    row = conn.execute(
        "SELECT name, kind, line_start FROM symbols "
        "WHERE file_id = ? AND kind IN ('function', 'method') "
        "AND line_start <= ? AND COALESCE(line_end, line_start) >= ? "
        "ORDER BY line_start DESC LIMIT 1",
        (file_id, line, line),
    ).fetchone()
    if row is not None:
        return row["name"], row["kind"], int(row["line_start"] or line)
    return "<module>", "file", line


# Tier: static_analysis — predicate is a deterministic closed list of
# trivial-body signatures (``pass`` / ``...`` / empty block / single
# print|log call / bare return). Regex on source, but the body-shape
# enumeration is fixed; re-raise (``raise`` / ``throw``) is explicitly
# excluded so the FP rate stays low.
@detector("empty-catch", confidence=CONFIDENCE_STATIC_ANALYSIS)
def detect_empty_catch(conn: sqlite3.Connection) -> list[dict]:
    """Detect exception handlers with trivial / empty bodies.

    W370. Reads source files referenced in the ``files`` table, locates
    each ``except``/``catch`` block, and flags handlers whose body is
    one of:
      * ``pass`` (Python), ``...`` (Python ellipsis stub)
      * empty block ``{}`` (JS/TS/Java/C#/Kotlin/Swift/Scala)
      * comment-only body
      * single ``print(...)`` / ``console.log(...)`` /
        ``logger.X(...)`` / ``log.X(...)`` call (logging without recovery)
      * single ``return`` / ``return None`` / ``return null``

    Re-raise (``raise`` / ``throw``) is excluded — the handler propagates
    the error. Multi-statement bodies + recovery code (assignments,
    non-log calls) are also excluded.
    """
    results: list[dict] = []
    try:
        files = conn.execute(
            "SELECT id, path, language FROM files "
            "WHERE language IN ('python', 'javascript', 'typescript', "
            "'java', 'c_sharp', 'kotlin', 'swift', 'scala', 'ruby', 'go')"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    workspace = _find_indexed_source_root()

    for f in files:
        file_id = f["id"]
        rel_path = f["path"]
        lang = f["language"]
        # Best-effort source read. Skip files we can't open (deleted,
        # binary, permission errors).
        try:
            source = (workspace / rel_path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue

        if lang == "python":
            handlers = _extract_python_handlers(source)
        elif lang in _BRACE_LANGS:
            handlers = _extract_brace_handlers(source)
        else:
            # Go / Ruby: covered by ``vibe-check`` but not flagged here
            # (idiomatic patterns vary too much per project — high FP).
            continue

        for line_no, body in handlers:
            if not _is_trivial_body(body, lang):
                continue
            symbol_name, kind, _line_start = _enclosing_symbol(conn, file_id, line_no)
            results.append(
                _finding(
                    "empty-catch",
                    "warning",
                    symbol_name,
                    kind,
                    _loc(rel_path, line_no),
                    1,
                    0,
                    f"Empty exception handler at {rel_path}:{line_no} (body has no recovery and does not re-raise)",
                )
            )
    return results


# Tier: structural — counts intra-class edges via ``edges.source_id``/
# ``target_id`` joins; predicate is "internal edges < methods/2" over the
# call graph, not a name match.
@detector("low-cohesion", confidence=CONFIDENCE_STRUCTURAL)
def detect_low_cohesion(conn: sqlite3.Connection) -> list[dict]:
    """Classes with 5+ methods but fewer than methods/2 internal edges."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "f.path as file_path, s.file_id "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class'"
    ).fetchall()
    # Bulk-fetch every method ONCE, bucketed by file_id, replacing the
    # per-class ``WHERE file_id = ? AND line_start >= ? AND line_end <= ?``
    # query. The per-class line-range filter runs in Python below.
    from collections import defaultdict

    methods_by_file: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    method_rows = conn.execute("SELECT id, file_id, line_start, line_end FROM symbols WHERE kind = 'method'").fetchall()
    for m in method_rows:
        methods_by_file[m["file_id"]].append((m["id"], m["line_start"], m["line_end"]))
    # Bulk-fetch every method->method edge ONCE, bucketed by source method id,
    # replacing the per-class ``source_id IN (...) AND target_id IN (...)``
    # query. Each entry maps a method source id to its method target ids.
    method_id_set = {m[0] for ms in methods_by_file.values() for m in ms}
    method_edges_by_source: dict[int, list[int]] = defaultdict(list)
    edge_rows = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
    for e in edge_rows:
        sid, tid = e["source_id"], e["target_id"]
        if sid in method_id_set and tid in method_id_set:
            method_edges_by_source[sid].append(tid)
    results = []
    for r in rows:
        # Count methods within the class line range. The class bounds use
        # ``COALESCE(... , 0)`` to mirror the old query's ``line_start or 0``
        # binding; a method with a NULL ``line_start``/``line_end`` is
        # excluded — a NULL comparison in the old SQL evaluates falsy.
        ls, le = r["line_start"] or 0, r["line_end"] or 0
        methods = [
            mid
            for (mid, m_ls, m_le) in methods_by_file.get(r["file_id"], [])
            if m_ls is not None and m_le is not None and m_ls >= ls and m_le <= le
        ]
        method_count = len(methods)
        if method_count < 5:
            continue
        method_ids = methods
        if not method_ids:
            continue
        # Count internal edges between methods of this class
        class_method_set = set(method_ids)
        internal_edges = sum(
            1 for sid in method_ids for tid in method_edges_by_source.get(sid, []) if tid in class_method_set
        )
        threshold = method_count // 2
        if internal_edges < threshold:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(
                _finding(
                    "low-cohesion",
                    "warning",
                    r["name"],
                    r["kind"],
                    loc_str,
                    internal_edges,
                    threshold,
                    f"Low cohesion: {method_count} methods but only {internal_edges} internal edges "
                    f"(threshold: {threshold})",
                )
            )
    return results


# Tier: structural — out_degree comes from precomputed graph_metrics;
# threshold over graph topology, no name signal. (Historically labelled the
# "outgoing-axis mirror of shotgun-surgery"; that framing predated the W1287
# shotgun-surgery re-implementation onto distinct-caller-FILE scatter, so the
# mirror note is dropped — message-chain is an out_degree-per-symbol count,
# shotgun-surgery is now a distinct-caller-file-scatter count; different axes.)
@detector("message-chain", confidence=CONFIDENCE_STRUCTURAL)
def detect_message_chain(conn: sqlite3.Connection) -> list[dict]:
    """Functions with out_degree > 10 in graph_metrics."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, f.path as file_path, "
        "gm.out_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON gm.symbol_id = s.id "
        "WHERE gm.out_degree > 10 "
        "AND s.kind IN ('function', 'method')"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(
            _finding(
                "message-chain",
                "info",
                r["name"],
                r["kind"],
                loc_str,
                r["out_degree"],
                10,
                f"Message chain: {r['out_degree']} outgoing calls",
            )
        )
    return results


# ---------------------------------------------------------------------------
# W370c — refused-bequest detector
#
# Definition: a subclass that inherits from a parent but overrides >= 2 of the
# parent's methods with a trivial body (``pass`` / ``return None`` / single
# ``raise NotImplementedError``). The class "refuses the bequest" from its
# parent -- the inheritance relationship exists structurally but most of what
# the parent provides is either thrown away or rejected outright. Classic
# Fowler smell; typically indicates the wrong inheritance choice.
#
# Re-use ``_TRIVIAL_BODY_PATTERNS`` + ``_strip_comments_and_blanks`` from
# the W370 empty-catch detector for body classification, plus the
# explicit ``raise NotImplementedError`` shape that's the canonical refusal
# pattern.
#
# Severity: warning (structural). Threshold: >= 2 trivial overrides per child.
# Lower bound chosen to keep FP rate manageable -- a single trivial override
# is often a legitimate "intentionally do nothing" hook (e.g. ``setUp``).
# ---------------------------------------------------------------------------

# Refusal-specific trivial bodies layered on top of ``_TRIVIAL_BODY_PATTERNS``.
# ``raise NotImplementedError`` / ``raise NotImplementedError(...)`` / Java/C#
# ``throw new UnsupportedOperationException(...)`` are the canonical "I am
# explicitly refusing this method" signatures.
_REFUSAL_RAISE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*raise\s+NotImplementedError\s*(?:\(.*\))?\s*$"),
    re.compile(r"^\s*throw\s+new\s+(?:UnsupportedOperationException|NotImplementedException)\s*\(.*\)\s*;?\s*$"),
)


def _is_refusal_body(body: str, lang: str) -> bool:
    """Return True when *body* counts as a refused-bequest override.

    Trivial bodies from ``_TRIVIAL_BODY_PATTERNS`` (pass / ... / return / empty)
    AND the explicit refusal-raise shapes both qualify. Multi-statement bodies
    are not refusals -- a method that does any real work, even ahead of a
    final ``raise NotImplementedError``, is not refusing the bequest.
    """
    cleaned = _strip_comments_and_blanks(body, lang)
    if not cleaned:
        return True
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    single = lines[0]
    if any(p.match(single) for p in _TRIVIAL_BODY_PATTERNS):
        return True
    return any(p.match(single) for p in _REFUSAL_RAISE_PATTERNS)


def _extract_method_body(source: str, line_start: int, line_end: int, lang: str) -> str:
    """Slice a method body out of *source* between line_start..line_end.

    Best-effort: returns the indented body lines for Python (after the header
    line), or the brace-delimited body for brace languages. The exact-syntax
    nuance does not matter -- the body just needs to be classifiable by
    ``_is_refusal_body``, which already collapses whitespace + comments.
    """
    if not source or line_start <= 0 or line_end < line_start:
        return ""
    lines = source.split("\n")
    if line_end > len(lines):
        line_end = len(lines)
    body_lines = lines[line_start:line_end]  # skip the header line itself
    return "\n".join(body_lines)


def _fetch_refused_bequest_edges(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Pull (child_class, parent_class) inheritance pairs from the graph.

    Limits to in-repo parents (``target_id`` resolves to a known symbol).
    Unions the canonical inheritance kinds via the shared helper so plugin
    extractors emitting ``implements`` / ``uses_trait`` (Rust impl-Trait,
    PHP traits) reach this detector too (W543-followup-C: pre-migration
    filter was bare ``e.kind = 'inherits'``). Returns ``[]`` on a
    missing-table OperationalError (un-indexed workspace).
    """
    from roam.db.edge_kinds import inheritance_in_clause

    try:
        return conn.execute(
            "SELECT s_child.id AS child_id, s_child.name AS child_name, "
            "s_child.line_start AS child_line_start, s_child.line_end AS child_line_end, "
            "s_child.file_id AS child_file_id, f_child.path AS child_path, "
            "f_child.language AS child_lang, "
            "s_parent.id AS parent_id, s_parent.name AS parent_name "
            "FROM edges e "
            "JOIN symbols s_child ON e.source_id = s_child.id "
            "JOIN symbols s_parent ON e.target_id = s_parent.id "
            "JOIN files f_child ON s_child.file_id = f_child.id "
            f"WHERE {inheritance_in_clause('e.kind')} "
            "AND s_child.kind = 'class' "
            "AND s_parent.kind = 'class'"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _prefetch_refused_bequest_methods_by_file(
    conn: sqlite3.Connection, child_file_ids: list[int]
) -> dict[int, list[sqlite3.Row]]:
    """Bulk-fetch all methods for the child files, grouped by ``file_id``.

    Ordered by id (rowid) so the later in-Python line-range containment
    filter yields rows in the same order the original per-file SELECT
    (no ORDER BY -> rowid order) returned them. W370c perf: hoists this
    out of the per-edge loop to kill an N+1 pattern.
    """
    methods_by_file: dict[int, list[sqlite3.Row]] = {}
    try:
        rows = batched_in(
            conn,
            "SELECT id, name, line_start, line_end, file_id FROM symbols "
            "WHERE kind = 'method' AND file_id IN ({ph}) ORDER BY id",
            child_file_ids,
        )
        for m in rows:
            methods_by_file.setdefault(m["file_id"], []).append(m)
    except sqlite3.OperationalError:
        pass
    return methods_by_file


def _prefetch_refused_bequest_parent_names(
    conn: sqlite3.Connection, parent_ids: list[int]
) -> dict[int, set[str]]:
    """Bulk-fetch parent method names grouped by ``parent_id``.

    These are the override-membership sets. W370c perf: hoisted out of the
    per-edge loop.
    """
    parent_names_by_id: dict[int, set[str]] = {}
    try:
        rows = batched_in(
            conn,
            "SELECT parent_id, name FROM symbols "
            "WHERE kind = 'method' AND parent_id IN ({ph})",
            parent_ids,
        )
        for r in rows:
            if r["name"]:
                parent_names_by_id.setdefault(r["parent_id"], set()).add(r["name"])
    except sqlite3.OperationalError:
        pass
    return parent_names_by_id


def _load_refused_bequest_source(
    workspace: Path, child_path: str, child_file_id: int, source_cache: dict[int, str | None]
) -> str | None:
    """Read a child class's source once, caching by file id.

    Returns the cached text, or ``None`` if the file cannot be read so the
    caller skips the row. A single child class typically has many methods
    checked, so the cache avoids re-reading per method.
    """
    if child_file_id not in source_cache:
        try:
            source_cache[child_file_id] = (workspace / child_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except (OSError, ValueError):
            source_cache[child_file_id] = None
    return source_cache[child_file_id]


def _refused_bequest_child_methods_in_range(
    file_methods: list[sqlite3.Row], c_start: int, c_end: int
) -> list[sqlite3.Row]:
    """Filter pre-fetched methods to those contained in the child class body.

    Line-range containment is the universal signal -- ``parent_id`` is not
    reliably set for methods across all languages. Mirrors the original SQL
    predicate (``line_start >= c_start AND COALESCE(line_end, line_start)
    <= c_end``).
    """
    return [
        m
        for m in file_methods
        if m["line_start"] is not None
        and m["line_start"] >= c_start
        and (m["line_end"] if m["line_end"] is not None else m["line_start"]) <= c_end
    ]


def _refused_bequest_trivial_overrides(
    child_methods: list[sqlite3.Row],
    source: str,
    child_lang: str,
    parent_method_names: set[str],
) -> list[tuple[str, int]]:
    """Count child methods overriding a parent method with a trivial body.

    When the parent has known methods, only name-matched overrides count.
    When the parent has no known methods (e.g. an un-indexed stdlib base),
    every trivial child method is a refusal candidate -- noisier but still
    useful, since the smell really is "this class declares mostly do-nothing
    methods".
    """
    require_override = bool(parent_method_names)
    trivial_overrides: list[tuple[str, int]] = []
    for m in child_methods:
        mname = m["name"]
        if require_override and mname not in parent_method_names:
            continue
        m_start = int(m["line_start"] or 0)
        m_end = int(m["line_end"] or m_start)
        body = _extract_method_body(source, m_start, m_end, child_lang)
        if _is_refusal_body(body, child_lang):
            trivial_overrides.append((mname, m_start))
    return trivial_overrides


def _refused_bequest_finding(
    row: sqlite3.Row, trivial_overrides: list[tuple[str, int]]
) -> dict | None:
    """Build a refused-bequest finding when ``>= 2`` trivial overrides, else ``None``."""
    if len(trivial_overrides) < 2:
        return None
    child_path = row["child_path"]
    child_line = int(row["child_line_start"] or 1)
    loc_str = _loc(child_path, child_line)
    method_parts = [f"{name}()" for name, _ in trivial_overrides[:5]]
    if len(trivial_overrides) > 5:
        method_parts.append(f"+{len(trivial_overrides) - 5} more")
    method_summary = ", ".join(method_parts)
    return _finding(
        "refused-bequest",
        "warning",
        row["child_name"],
        "class",
        loc_str,
        len(trivial_overrides),
        2,
        (
            f"Refused bequest: {row['child_name']} overrides "
            f"{len(trivial_overrides)} {row['parent_name']} method"
            f"{'s' if len(trivial_overrides) != 1 else ''} "
            f"with trivial body ({method_summary})"
        ),
    )


# Tier: structural — walks ``edges.kind='inherits'`` to find (child, parent)
# class pairs and inspects override-body shape. The signal is graph (inherits
# edge) + AST (trivial-body match) combined; the threshold (>= 2 trivial
# overrides) avoids the legitimate single-hook case, keeping the FP rate
# inside the structural tier rather than dropping to heuristic.
@detector("refused-bequest", confidence=CONFIDENCE_STRUCTURAL)
def detect_refused_bequest(conn: sqlite3.Connection) -> list[dict]:
    """Detect subclasses that override >= 2 parent methods with trivial bodies.

    W370c. Walks ``edges.kind='inherits'`` to find (child, parent) class
    pairs, then for each pair counts how many of the child's methods share
    a name with a parent method AND have a trivial body
    (``pass`` / ``return None`` / ``...`` / ``raise NotImplementedError``).
    Threshold: >= 2 trivial overrides per child. Single trivial overrides
    are legitimate "intentionally do nothing" hooks and not flagged.

    Languages: same set as ``detect_empty_catch`` (Python + brace languages).
    Source files are read from the workspace -- the indexer does not extract
    method bodies into a queryable table.
    """
    results: list[dict] = []
    inherits = _fetch_refused_bequest_edges(conn)
    if not inherits:
        return results

    workspace = _find_indexed_source_root()
    source_cache: dict[int, str | None] = {}

    # W370c perf: bulk pre-fetch the two per-edge SELECTs (child methods by
    # file/line-range, parent method names by parent_id) instead of the
    # original N+1 pattern (2 serial round-trips per inherits edge). The
    # per-edge logic and emitted findings (and order) are byte-identical to
    # the in-loop version.
    child_file_ids = sorted({row["child_file_id"] for row in inherits})
    parent_ids = sorted({row["parent_id"] for row in inherits})
    methods_by_file = _prefetch_refused_bequest_methods_by_file(conn, child_file_ids)
    parent_names_by_id = _prefetch_refused_bequest_parent_names(conn, parent_ids)

    for row in inherits:
        child_lang = row["child_lang"]
        # Limit to languages where we can reasonably parse a method body.
        if child_lang != "python" and child_lang not in _BRACE_LANGS:
            continue

        source = _load_refused_bequest_source(
            workspace, row["child_path"], row["child_file_id"], source_cache
        )
        if not source:
            continue

        c_start = int(row["child_line_start"] or 0)
        c_end = int(row["child_line_end"] or c_start)
        child_methods = _refused_bequest_child_methods_in_range(
            methods_by_file.get(row["child_file_id"], ()), c_start, c_end
        )

        trivial_overrides = _refused_bequest_trivial_overrides(
            child_methods,
            source,
            child_lang,
            parent_names_by_id.get(row["parent_id"], set()),
        )
        finding = _refused_bequest_finding(row, trivial_overrides)
        if finding is not None:
            results.append(finding)
    return results


# ---------------------------------------------------------------------------
# W370c — primitive-obsession detector
#
# Definition: a function/method with >= 4 parameters where the OVERWHELMING
# majority (>= 75%) are bare primitive types (``int`` / ``str`` / ``float`` /
# ``bool`` / ``bytes``) or Optional wrappers around primitives. The smell
# shape: a "data clump"-adjacent pattern where the function is passing around
# tuples of primitives instead of a named type.
#
# Polyadic value parameters (`id: int, name: str, age: int, ...`) on
# constructors are exempt -- by definition the constructor primitives become
# the attributes of a value object.
#
# Severity: info (heuristic). Confidence tier: heuristic (parses signature
# strings, not AST; some legitimate primitive-heavy APIs trip it).
# ---------------------------------------------------------------------------

# Bare primitive type spellings we recognise. Tier 1: Python builtins +
# common stdlib spellings. Tier 2: Optional[<primitive>] / <primitive> | None
# patterns. Tier 3: foreign-language primitives (JS/TS/Java/C#/Go) so the
# detector isn't Python-only.
_PRIMITIVE_TYPE_NAMES: frozenset[str] = frozenset(
    {
        # Python
        "int",
        "str",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "complex",
        "none",
        "nonetype",
        # JS / TS
        "string",
        "number",
        "boolean",
        "bigint",
        "symbol",
        "undefined",
        "null",
        # Java / C# / Kotlin / Swift / Scala
        "integer",
        "long",
        "short",
        "byte",
        "char",
        "character",
        "double",
        "decimal",
        "object",
        # Go
        "int8",
        "int16",
        "int32",
        "int64",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "rune",
        "float32",
        "float64",
    }
)


def _is_primitive_annotation(annotation: str | None) -> bool:
    """Return True when *annotation* names a bare primitive type.

    Recognises:
      * Bare primitive names (``int``, ``str``, ``bool``, …)
      * Optional / nullable wrappers: ``Optional[str]``, ``str | None``
      * Default-value-only params with no annotation are NOT primitive --
        the type is unknown, so we don't count them either way.

    Returns False for collection types (``list[str]``, ``dict[str, int]``,
    ``tuple[int, str]``) -- those count as compound types because they
    package the primitives. The smell is about LOOSE primitives being
    passed around individually, not about lists of primitives.
    """
    if not annotation:
        return False
    s = annotation.strip()
    if not s:
        return False
    # Strip outer parens / brackets we don't need.
    s = s.strip("()").strip()
    # Optional[X] -> X
    m = re.match(r"^Optional\s*\[\s*(.+?)\s*\]\s*$", s)
    if m:
        return _is_primitive_annotation(m.group(1))
    # X | None / None | X -> X
    if "|" in s:
        parts = [p.strip() for p in s.split("|")]
        non_none = [p for p in parts if p.lower() not in ("none", "nonetype", "null")]
        if len(non_none) == 1:
            return _is_primitive_annotation(non_none[0])
        # Union of multiple non-None types: only primitive if EVERY arm is
        # primitive (e.g. ``int | float``). Mixed unions like ``int | str``
        # are still primitive obsession -- the caller still has to know
        # which one. Be lenient here.
        return all(_is_primitive_annotation(p) for p in non_none)
    # Collection types are NOT primitive -- they package the data.
    lower = s.lower()
    if lower.startswith(
        (
            "list[",
            "list ",
            "tuple[",
            "tuple ",
            "dict[",
            "dict ",
            "set[",
            "set ",
            "frozenset[",
            "iterable[",
            "sequence[",
            "mapping[",
            "callable[",
            "type[",
            "list<",
            "array<",
            "map<",
            "set<",
        )
    ):
        return False
    # Bare primitive name match (case-insensitive).
    # Strip any trailing generic / nullable markers that survived.
    bare = re.split(r"[\[\<\s]", lower, maxsplit=1)[0].rstrip("?")
    return bare in _PRIMITIVE_TYPE_NAMES


def _split_signature_params(signature: str) -> list[str]:
    """Split a signature into raw param strings, handling nested brackets.

    Mirrors ``_parse_param_count``'s logic but returns the raw strings so
    each can be type-classified. Excludes self / cls.
    """
    params_str = _signature_param_body(signature)
    if not params_str:
        return []
    return [
        param
        for param in _iter_top_level_param_parts(params_str)
        if _signature_param_name(param) not in _IGNORED_PARAM_NAMES
    ]


def _extract_param_annotation(param: str) -> str | None:
    """Pull the type annotation out of a single param spec.

    Handles ``name: int``, ``name: int = 0``, ``name: Optional[str] = None``,
    ``name`` (no annotation -> None), ``int name`` (Java/C# style ->
    leading type).
    """
    if not param:
        return None
    s = param.strip()
    if ":" in s:
        # Python / TS style: ``name: type [= default]``
        after_colon = s.split(":", 1)[1].strip()
        if "=" in after_colon:
            after_colon = after_colon.split("=", 1)[0].strip()
        return after_colon or None
    if "=" in s:
        # Default-value only, no annotation
        return None
    # Java / C# / Go style: ``type name`` -- leading token is the type.
    # Only treat as a type when it's a single token followed by an
    # identifier (so ``name`` alone returns None).
    tokens = s.split()
    if len(tokens) >= 2:
        # The LAST token is the param name; preceding tokens form the type.
        # Drop trailing ``[]`` if present (Java arrays).
        type_part = " ".join(tokens[:-1]).rstrip("[]")
        return type_part or None
    return None


# Tier: heuristic — name-pattern check over the type annotation (``int`` /
# ``str`` / ``bool`` / ``float`` / ``Optional<primitive>``). Whether a bare
# primitive is "the right type" is project-dependent (some domains
# legitimately pass IDs as raw ints), so the tier surfaces the FP risk.
@detector("primitive-obsession", confidence=CONFIDENCE_HEURISTIC)
def detect_primitive_obsession(conn: sqlite3.Connection) -> list[dict]:
    """Detect functions with >= 4 params where >= 75% are bare primitives.

    W370c. Parses ``symbols.signature`` for each function/method, counts the
    type-annotated params whose type is a bare primitive (``int`` / ``str`` /
    ``bool`` / ``float`` / ``bytes`` / Optional<primitive> / language-foreign
    primitives), and flags signatures where that count >= 4 AND the primitive
    ratio is >= 75%.

    Constructors (__init__ / __new__) and dataclass auto-generated methods
    are exempt -- by definition the constructor's primitive params become
    attributes of a value object. The exemption uses the same
    ``_is_dead_params_exempt`` helper as the dead-params detector for
    consistency.

    The detector is intentionally conservative: a param with NO annotation
    contributes neither to the primitive count nor the total. This keeps
    the FP rate low on un-annotated code (which is the bulk of older
    JavaScript / Python 2 corpora).
    """
    try:
        rows = conn.execute(
            "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path, "
            "p.decorators as parent_decorators "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "LEFT JOIN symbols p ON p.id = s.parent_id "
            "WHERE s.kind IN ('function', 'method') "
            "AND s.signature IS NOT NULL "
            "AND s.signature != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        # Pre-v9 schema without ``decorators`` -- run without the parent
        # join. dataclass exemption silently degrades, name-based
        # exemptions still fire.
        try:
            rows = conn.execute(
                "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path, "
                "NULL as parent_decorators "
                "FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE s.kind IN ('function', 'method') "
                "AND s.signature IS NOT NULL "
                "AND s.signature != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    results: list[dict] = []
    for r in rows:
        # Re-use the dead-params exemption logic: constructors / dataclass
        # auto-generated / pytest lifecycle hooks all get a pass.
        if _is_dead_params_exempt(r["name"], r["parent_decorators"]):
            continue
        params = _split_signature_params(r["signature"])
        # Only consider params with an explicit type annotation. An
        # un-annotated param contributes nothing -- we can't tell what
        # the caller is passing.
        annotated_total = 0
        primitive_count = 0
        for p in params:
            ann = _extract_param_annotation(p)
            if ann is None:
                continue
            annotated_total += 1
            if _is_primitive_annotation(ann):
                primitive_count += 1
        if annotated_total < 4:
            continue
        ratio = primitive_count / annotated_total
        if ratio < 0.75:
            continue
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(
            _finding(
                "primitive-obsession",
                "info",
                r["name"],
                r["kind"],
                loc_str,
                primitive_count,
                4,
                (
                    f"Primitive obsession: {primitive_count}/{annotated_total} "
                    f"params ({ratio:.0%}) are bare primitives -- consider "
                    f"a value object"
                ),
            )
        )
    return results


# ---------------------------------------------------------------------------
# W370b — duplicate-conditionals detector
#
# Definition: a function/method where the SAME ``if`` predicate is repeated
# >= 3 times in independent (NOT chained via ``elif`` / ``else if``)
# statements. The smell shape:
#
#     def foo(x):
#         if x == 1: do_a()
#         if x == 1: do_b()    # duplicate predicate
#         if x == 1: do_c()    # duplicate predicate
#
# is duplicate-conditionals -- the predicate is re-evaluated for each
# branch even though one ``if/elif`` ladder would express the same intent.
#
# Polyadic dispatch via ``elif`` is INTENTIONAL and MUST NOT flag:
#
#     def bar(x):
#         if x == "a": do_a()
#         elif x == "b": do_b()
#         elif x == "c": do_c()  # different predicates -> dispatch
#
# Implementation: per-source-file regex extraction of ``if`` headers, then
# AST-signature hashing of the normalized predicate text. Scope = the
# enclosing function/method from the ``symbols`` table (or ``<module>``
# for top-level code). Threshold = 3 (per W370 audit recommendation).
#
# Languages: python (indent-based ``if X:`` headers) + brace languages
# (``if (X)`` headers). Lines preceded by ``elif`` / ``else if`` / ``else
# if`` are skipped -- those are the polyadic-dispatch case, not duplicates.
# ---------------------------------------------------------------------------

# Python ``if`` and ``elif`` header lines. Captures the leading indent so
# we can locate the enclosing line range and the predicate text up to the
# trailing ``:`` (excluding any inline comment).
_PY_IF_HEADER = re.compile(r"^([ \t]*)if\b(.+?):\s*(?:#.*)?$", re.MULTILINE)
_PY_ELIF_HEADER = re.compile(r"^[ \t]*elif\b", re.MULTILINE)

# Brace-language ``if (X)`` headers. The ``(?<![A-Za-z0-9_.])`` guard
# avoids matching method names or identifiers that happen to end in
# ``if`` (e.g. ``noVerify(...)``). The predicate text is the parenthesised
# group; we balance parens so nested calls survive.
_BRACE_IF_KEYWORD = re.compile(r"(?<![A-Za-z0-9_.])if\s*\(", re.MULTILINE)
# An ``else if`` / ``else  if`` chain head -- the brace-language equivalent
# of Python's ``elif``. We test the LITERAL bytes preceding the ``if`` for
# a trailing ``else`` (whitespace-separated) so we can skip those headers.
_BRACE_ELSE_IF_TAIL = re.compile(r"else\s*$")

# Predicate normalization for hash comparison. The hash should treat
# ``if x==1:`` and ``if x == 1:`` and ``if (x == 1):`` as the same
# predicate. Strategy: collapse whitespace + strip outermost balanced
# parens. Don't try to normalise operator spelling (``==`` vs ``is``) --
# those mean different things even when the SHAPE is similar.
_WHITESPACE_RUN = re.compile(r"\s+")


# Whitespace adjacent to a non-word, non-whitespace character (operators,
# parens, commas, brackets, dots). Removing this kind of whitespace makes
# ``x == 1`` and ``x==1`` hash identically WITHOUT also collapsing
# ``x and y`` -> ``xandy`` (keyword boundaries between word characters
# are preserved).
_OPERATOR_ADJ_WS = re.compile(r"\s*([^\w\s])\s*")


def _normalize_predicate(text: str) -> str:
    """Normalise a predicate string for hashing.

    Strips outer balanced parens, then canonicalises whitespace:
      * collapses runs of whitespace to a single space, AND
      * removes whitespace adjacent to operator / punctuation characters
        (``==``, ``!=``, ``<``, ``>``, ``(``, ``)``, ``,``, ``.``, etc.).

    This makes ``x == 1``, ``x==1``, and ``x  ==  1`` all hash to the
    same canonical form ``x==1`` while preserving the whitespace inside
    ``x and y`` (so it does not collide with a hypothetical identifier
    ``xandy``).
    """
    s = text.strip()
    # Repeatedly strip outermost matched parens: ``((x == 1))`` -> ``x == 1``.
    while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        # Confirm the outermost parens are balanced (don't strip
        # ``(a) and (b)`` -> ``a) and (b``).
        depth = 0
        balanced = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    balanced = False
                    break
        if not balanced:
            break
        s = s[1:-1].strip()
    # Remove whitespace adjacent to operator / punctuation characters
    # FIRST so that ``a == b`` and ``a==b`` produce the same backbone.
    s = _OPERATOR_ADJ_WS.sub(r"\1", s)
    # Collapse remaining whitespace runs (these sit between word
    # characters, preserving keyword boundaries like ``x and y``).
    return _WHITESPACE_RUN.sub(" ", s)


def _extract_python_if_predicates(source: str) -> list[tuple[int, str]]:
    """Yield (line_number, normalized_predicate) per top-level ``if``.

    ``elif`` headers are deliberately skipped -- they form an intentional
    polyadic dispatch ladder, not a duplicate-predicate smell.
    """
    out: list[tuple[int, str]] = []
    # Pre-compute the set of line numbers that are ``elif`` so we can
    # skip them when scanning ``if`` headers. ``if`` and ``elif`` are
    # disjoint at the regex level (``if\b`` requires a word break, so
    # ``elif`` does not match the ``_PY_IF_HEADER`` pattern), but we
    # also need to skip the lines whose textual content is ``elif`` to
    # avoid double-counting in odd edge cases.
    elif_lines: set[int] = set()
    for m in _PY_ELIF_HEADER.finditer(source):
        elif_lines.add(source.count("\n", 0, m.start()) + 1)
    for m in _PY_IF_HEADER.finditer(source):
        line_no = source.count("\n", 0, m.start()) + 1
        if line_no in elif_lines:
            continue
        predicate_raw = m.group(2)
        predicate = _normalize_predicate(predicate_raw)
        if predicate:
            out.append((line_no, predicate))
    return out


def _extract_brace_if_predicates(source: str) -> list[tuple[int, str]]:
    """Yield (line_number, normalized_predicate) per top-level ``if (...)``.

    ``else if`` headers are skipped -- the brace-language polyadic
    dispatch ladder. Paren-balancing walks from the ``(`` matched by the
    header to its closing ``)``; the body between those parens is the
    predicate text (stripped of comments + whitespace by
    ``_normalize_predicate``).
    """
    out: list[tuple[int, str]] = []
    for m in _BRACE_IF_KEYWORD.finditer(source):
        # Skip ``else if`` chains: scan back from the ``if`` keyword to
        # the prior non-whitespace token; if it's ``else``, this is a
        # chain head, not an independent ``if``.
        prefix = source[: m.start()]
        if _BRACE_ELSE_IF_TAIL.search(prefix):
            continue
        # Open paren is the character right before m.end()'s position
        # -- the regex matches up through the ``(``.
        open_paren_pos = m.end() - 1
        depth = 0
        i = open_paren_pos
        end = -1
        in_string: str | None = None
        while i < len(source):
            ch = source[i]
            if in_string is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
            else:
                if ch in ('"', "'", "`"):
                    in_string = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            i += 1
        if end < 0:
            continue
        predicate_raw = source[open_paren_pos + 1 : end]
        predicate = _normalize_predicate(predicate_raw)
        if predicate:
            line_no = source.count("\n", 0, m.start()) + 1
            out.append((line_no, predicate))
    return out


def _scope_for_line(scope_ranges: list[tuple[int, int, str, str, int]], line: int) -> tuple[str, str, int]:
    """Find the innermost enclosing (name, kind, line_start) for *line*.

    ``scope_ranges`` is a pre-sorted list of (line_start, line_end, name,
    kind, line_start) tuples. Falls back to (``"<module>"``, ``"file"``,
    line) when no function/method contains the line.
    """
    best: tuple[str, str, int] | None = None
    best_span = None
    for line_start, line_end, name, kind, ls_for_loc in scope_ranges:
        if line_start <= line <= line_end:
            span = line_end - line_start
            if best_span is None or span < best_span:
                best = (name, kind, ls_for_loc)
                best_span = span
    if best is not None:
        return best
    return "<module>", "file", line


# Tier: heuristic — predicate-hash bucketing over normalised ``if`` text.
# The hash is deterministic (whitespace collapse + outer-paren strip), BUT
# the same predicate can have semantically different effects in different
# control-flow contexts, so the tier stays at heuristic to flag the FP risk.
@detector("duplicate-conditionals", confidence=CONFIDENCE_HEURISTIC)
def detect_duplicate_conditionals(conn: sqlite3.Connection) -> list[dict]:
    """Detect functions where the same ``if`` predicate repeats >= 3 times.

    W370b. Reads source files referenced in the ``files`` table, extracts
    ``if`` statement headers (Python ``if X:`` / brace-language ``if (X)``),
    normalizes the predicate text (whitespace collapse + outer-paren
    strip), hashes the canonical form, and flags any (enclosing-scope,
    predicate) bucket with >= 3 occurrences as a single finding.

    Approach: AST-signature hashing of ``if`` predicate text per
    function/method scope -- same shape as ``clones`` but scoped to ``if``
    nodes only. Threshold = 3 per W370 audit recommendation (2 produces
    too many false positives on guard clauses).

    Polyadic dispatch via ``elif`` / ``else if`` is INTENTIONAL and not
    flagged -- those are different predicates in a dispatch ladder, not
    a single duplicated predicate.

    The indexer does not extract ``if`` statement AST nodes into a
    queryable table -- this detector reads source files directly,
    mirroring ``detect_empty_catch``.
    """
    results: list[dict] = []
    try:
        files = conn.execute(
            "SELECT id, path, language FROM files "
            "WHERE language IN ('python', 'javascript', 'typescript', "
            "'java', 'c_sharp', 'kotlin', 'swift', 'scala', 'go')"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    workspace = _find_indexed_source_root()

    # Pre-fetch all enclosing-scope candidates for the candidate files in a
    # single batched query instead of one query per file (N+1 avoidance).
    file_ids = [f["id"] for f in files]
    symbols_by_file: dict[int, list[sqlite3.Row]] = {}
    if file_ids:
        try:
            all_scope_rows = batched_in(
                conn,
                "SELECT file_id, name, kind, line_start, line_end FROM symbols "
                "WHERE file_id IN ({ph}) AND kind IN ('function', 'method')",
                file_ids,
            )
        except sqlite3.OperationalError:
            all_scope_rows = []
        for r in all_scope_rows:
            symbols_by_file.setdefault(r["file_id"], []).append(r)

    for f in files:
        file_id = f["id"]
        rel_path = f["path"]
        lang = f["language"]
        try:
            source = (workspace / rel_path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue

        if lang == "python":
            predicates = _extract_python_if_predicates(source)
        elif lang in _BRACE_LANGS or lang == "go":
            predicates = _extract_brace_if_predicates(source)
        else:
            continue

        if not predicates:
            continue

        scope_rows = symbols_by_file.get(file_id, [])
        scope_ranges: list[tuple[int, int, str, str, int]] = [
            (
                int(r["line_start"] or 0),
                int(r["line_end"] or r["line_start"] or 0),
                r["name"],
                r["kind"],
                int(r["line_start"] or 0),
            )
            for r in scope_rows
        ]

        # Bucket predicates by (scope_key, predicate_hash). The scope
        # key is the function's name + line_start so two distinct
        # functions with the same name in nested scopes don't collide.
        from collections import defaultdict

        buckets: dict[tuple[str, int, str], list[int]] = defaultdict(list)
        scope_meta: dict[tuple[str, int], tuple[str, str, int]] = {}
        for line_no, predicate in predicates:
            scope_name, scope_kind, scope_line = _scope_for_line(scope_ranges, line_no)
            scope_key = (scope_name, scope_line)
            scope_meta[scope_key] = (scope_name, scope_kind, scope_line)
            buckets[(scope_name, scope_line, predicate)].append(line_no)

        for (scope_name, scope_line, predicate), lines in buckets.items():
            if len(lines) < 3:
                continue
            scope_name_out, scope_kind_out, _ = scope_meta[(scope_name, scope_line)]
            # Use the FIRST occurrence as the canonical location -- it's
            # where a developer naturally lands when investigating the
            # duplicate-predicate cluster.
            first_line = min(lines)
            # Predicate excerpt is the canonical form, truncated so the
            # description stays compact in tables.
            excerpt = predicate
            if len(excerpt) > 60:
                excerpt = excerpt[:57] + "..."
            occurrence_summary = ", ".join(str(ln) for ln in sorted(lines))
            results.append(
                _finding(
                    "duplicate-conditionals",
                    "warning",
                    scope_name_out,
                    scope_kind_out,
                    _loc(rel_path, first_line),
                    len(lines),
                    3,
                    (
                        f"Duplicate conditionals: predicate `if {excerpt}` "
                        f"repeats {len(lines)} times in {scope_name_out} "
                        f"(lines {occurrence_summary})"
                    ),
                )
            )
    return results


# ---------------------------------------------------------------------------
# W603 — magic-numbers detector
#
# Definition: a numeric literal NOT in {-1, 0, 1, 2} that appears >= 3 times
# in a single function body. Numbers like -1/0/1/2 are universal idioms
# (sentinel, length checks, off-by-one, dimensionality) so they are exempt.
# Any other repeated literal -- 7, 60, 256, 100, 3.14 -- screams "extract a
# named constant" and is the canonical magic-number smell.
#
# Implementation: ast.parse per Python file, walk every function/method,
# Counter on the literal values seen inside that function's body. The
# scope is the immediate enclosing FunctionDef / AsyncFunctionDef; nested
# functions are counted as their OWN scope (an inner helper that repeats a
# constant is a separate smell from its enclosing function).
#
# Confidence tier: heuristic -- repeated literals can be deliberate (loop
# bounds, byte sizes) and the AST has no way to tell intent. Reviewers
# decide whether to extract.
# ---------------------------------------------------------------------------

# Literals exempted from the magic-numbers count. Boolean True/False are
# excluded separately (they are not ``int`` even though Python's bool is
# an int subclass) via an explicit ``type() is int`` check in the walker.
_MAGIC_NUMBER_EXEMPT: frozenset[int | float] = frozenset({-1, 0, 1, 2})

# Threshold: >= 3 occurrences of the SAME literal value inside one function.
_MAGIC_NUMBER_THRESHOLD: int = 3


def _collect_numeric_literals_in_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Counter:
    """Count numeric literals inside *func_node*'s body, skipping nested defs.

    Returns a ``Counter`` keyed by the literal value. ``True``/``False`` are
    rejected (``type() is bool`` is True for Python booleans even though
    they subclass ``int``). Negative literals like ``-1`` arrive at the
    AST as ``UnaryOp(USub, Constant(1))`` -- we fold those into a single
    int so ``-1`` is correctly exempted.

    Nested function definitions are NOT traversed: their literals belong to
    their own scope. The outer detector walks every FunctionDef in the file
    separately so each scope is counted exactly once.
    """
    counts: Counter = Counter()

    # W866: dispatch by ``type(child)`` instead of an isinstance chain.
    # Each handler may return ``True`` to suppress the default recursive
    # ``visit(child)`` call (used by the Constant + UnaryOp arms whose
    # semantics differ from "just recurse"). The Function/AsyncFunctionDef
    # arms map to a no-op + ``True`` so nested defs are skipped without
    # recursion -- their literals belong to their own scope. This zeros
    # out the W852 isinstance-chain finding on the inner ``visit`` walker
    # while preserving the exact original semantics.
    def _on_def(_child: ast.AST) -> bool:
        # Nested def: skip (it's processed as its own scope by the
        # outer walker that called us).
        return True

    def _on_constant(child: ast.AST) -> bool:
        v = child.value  # type: ignore[attr-defined]
        # ``isinstance(True, int)`` is True -- exclude booleans.
        if type(v) is int or type(v) is float:
            counts[v] += 1
        return False  # still recurse: a Constant has no children but the
        # call is cheap and keeps the dispatch table uniform.

    def _on_unaryop(child: ast.AST) -> bool:
        # Fold ``-N`` into a single literal so ``-1`` is exempted. Only
        # the USub-of-numeric-Constant shape is a literal; other UnaryOps
        # (Not, Invert, USub-of-Name, ...) fall through to the default
        # recursive walk so embedded literals are still counted.
        if (
            isinstance(child.op, ast.USub)  # type: ignore[attr-defined]
            and isinstance(child.operand, ast.Constant)  # type: ignore[attr-defined]
            and type(child.operand.value) in (int, float)  # type: ignore[attr-defined]
        ):
            counts[-child.operand.value] += 1  # type: ignore[attr-defined]
            return True
        return False

    _handlers: dict[type, Callable[[ast.AST], bool]] = {
        ast.FunctionDef: _on_def,
        ast.AsyncFunctionDef: _on_def,
        ast.Constant: _on_constant,
        ast.UnaryOp: _on_unaryop,
    }

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            handler = _handlers.get(type(child))
            if handler is not None and handler(child):
                continue
            visit(child)

    # Iterate the function BODY directly so the def's own signature defaults
    # (e.g. ``def f(timeout: int = 30):``) are still counted: those are
    # genuine magic numbers in the public surface. Nested ``def`` statements
    # in the body are their own scope -- skip them at the top level too so
    # the outer scope's counts don't include the inner scope's literals
    # (``visit()`` already skips them on recursive descent, but the body
    # iteration here calls ``visit(stmt)`` on each top-level statement so
    # we have to filter ``FunctionDef`` here as well).
    _SKIP_TOPLEVEL: tuple[type, ...] = (ast.FunctionDef, ast.AsyncFunctionDef)
    for stmt in func_node.body:
        if type(stmt) in _SKIP_TOPLEVEL:
            continue
        visit(stmt)
    for default in func_node.args.defaults:
        visit(default)
    for kw_default in func_node.args.kw_defaults:
        if kw_default is not None:
            visit(kw_default)
    return counts


# Tier: heuristic — counts literal occurrences per function with a
# threshold gate. The exempt set (-1/0/1/2) handles the obvious cases, but a
# legitimate constant repeated across short helpers will still flag, hence
# heuristic.
@detector("magic-numbers", confidence=CONFIDENCE_HEURISTIC)
def detect_magic_numbers(conn: sqlite3.Connection) -> list[dict]:
    """Detect numeric literals (not -1/0/1/2) appearing >= 3x per function.

    W603. Walks every Python file's AST, counts non-exempt numeric literals
    per function/method, and flags any (function, literal_value) pair with
    >= 3 occurrences. Suggests extracting a named constant.

    Severity: info -- magic numbers are a style/readability smell, not a
    correctness bug.

    Python-only. JavaScript/TypeScript magic-number detection would need
    a separate parser path; punt to a follow-up if/when there is demand.
    """
    results: list[dict] = []
    try:
        files = conn.execute("SELECT id, path, language FROM files WHERE language = 'python'").fetchall()
    except sqlite3.OperationalError:
        return []

    workspace = _find_indexed_source_root()

    for f in files:
        rel_path = f["path"]
        # W1301: shared per-run AST cache -- parse each file at most once
        # across the three Python-only AST detectors (was 3x independent
        # ast.parse passes; ~8.7s each on roam-code).
        tree = _read_and_parse(workspace, rel_path)
        if tree is None:
            continue

        # Walk every FunctionDef / AsyncFunctionDef in the file. Each
        # function/method gets its own counter; the helper above skips
        # nested defs so we don't double-count.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            counts = _collect_numeric_literals_in_function(node)
            for value, occurrences in counts.items():
                if occurrences < _MAGIC_NUMBER_THRESHOLD:
                    continue
                if value in _MAGIC_NUMBER_EXEMPT:
                    continue
                # Distinguish methods (have a class ancestor) from
                # top-level functions. The AST doesn't carry a parent
                # pointer; the symbols table is the source of truth for
                # kind on the persistence side, but for the in-memory
                # finding shape we look at the col_offset (methods are
                # always inside a ClassDef which indents them).
                kind = "function"
                # The symbols-table lookup happens at persist time
                # (``_resolve_smell_subject_id`` in cmd_smells.py); the
                # in-memory finding just declares ``function`` and
                # ``cmd_smells`` re-resolves to the real kind.
                results.append(
                    _finding(
                        "magic-numbers",
                        "info",
                        node.name,
                        kind,
                        _loc(rel_path, node.lineno),
                        occurrences,
                        _MAGIC_NUMBER_THRESHOLD,
                        (
                            f"Magic number: {value!r} repeats {occurrences} "
                            f"times in {node.name} -- consider a named "
                            f"constant for these literals"
                        ),
                    )
                )
    return results


# ---------------------------------------------------------------------------
# W604 — boolean-parameter detector
#
# Definition: a call site with >= 2 positional boolean literal arguments
# (``True`` / ``False``). The smell shape:
#
#     do_thing(True, False, retries=3)
#
# is boolean-parameter -- the caller has to guess which positional bool
# means what; a keyword arg or an enum would be self-documenting.
#
# Implementation: ast.parse per Python file, find every Call node, count
# positional ``Constant(value=True|False)`` args. >= 2 -> flag the call.
# Keyword bool args (``f(verbose=True)``) are explicitly NOT flagged --
# those are the FIX, not the smell.
#
# Confidence tier: structural -- the predicate is deterministic AST shape,
# no name patterns or thresholds involved.
# ---------------------------------------------------------------------------


# Threshold: >= 2 positional bool literals at one call site.
_BOOLEAN_PARAMETER_THRESHOLD: int = 2


@detector("boolean-parameter", confidence=CONFIDENCE_STRUCTURAL)
def detect_boolean_parameter(conn: sqlite3.Connection) -> list[dict]:
    """Detect call sites with >= 2 positional boolean literal arguments.

    W604. Walks every Python file's AST, examines every ``Call`` node, and
    counts the positional args that are ``Constant(value=True|False)``. A
    call with >= 2 positional bool literals is the boolean-parameter smell:
    the caller can't tell from the call site which bool means what.

    Keyword bool arguments (``f(verbose=True, strict=False)``) are the
    canonical fix and are explicitly NOT flagged.

    Severity: info -- semantic ambiguity at the call site, not a
    correctness defect.

    Python-only -- mirrors ``detect_magic_numbers`` in scope.
    """
    results: list[dict] = []
    try:
        files = conn.execute("SELECT id, path, language FROM files WHERE language = 'python'").fetchall()
    except sqlite3.OperationalError:
        return []

    workspace = _find_indexed_source_root()

    # Pre-fetch enclosing-scope rows for ALL candidate files in one batched
    # query (N+1 avoidance -- mirrors detect_duplicate_predicates). Each call
    # site is attributed to the function/method it lives in; falls back to
    # ``<module>`` for top-level calls.
    file_ids = [f["id"] for f in files]
    symbols_by_file: dict[int, list[sqlite3.Row]] = {}
    if file_ids:
        try:
            all_scope_rows = batched_in(
                conn,
                "SELECT file_id, name, kind, line_start, line_end FROM symbols "
                "WHERE file_id IN ({ph}) AND kind IN ('function', 'method')",
                file_ids,
            )
        except sqlite3.OperationalError:
            all_scope_rows = []
        for r in all_scope_rows:
            symbols_by_file.setdefault(r["file_id"], []).append(r)

    for f in files:
        file_id = f["id"]
        rel_path = f["path"]
        # W1301: shared per-run AST cache (see detect_magic_numbers).
        tree = _read_and_parse(workspace, rel_path)
        if tree is None:
            continue

        scope_rows = symbols_by_file.get(file_id, [])
        scope_ranges: list[tuple[int, int, str, str, int]] = [
            (
                int(r["line_start"] or 0),
                int(r["line_end"] or r["line_start"] or 0),
                r["name"],
                r["kind"],
                int(r["line_start"] or 0),
            )
            for r in scope_rows
        ]

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Count positional bool literals only. ``Constant(value=True)``
            # and ``Constant(value=False)`` -- explicit ``type() is bool``
            # check because Python's ``True`` / ``False`` are ``int``
            # subclasses and we MUST NOT collapse them with ``1`` / ``0``.
            bool_args = sum(1 for a in node.args if isinstance(a, ast.Constant) and type(a.value) is bool)
            if bool_args < _BOOLEAN_PARAMETER_THRESHOLD:
                continue
            # Best-effort call-name rendering for the description. Plain
            # ``f(...)`` -> "f"; ``self.f(...)`` -> "self.f"; ``a.b.c(...)``
            # -> "a.b.c". Anything more exotic (subscript, call-of-call)
            # collapses to ``<call>`` so the description stays compact.
            call_name = _render_call_name(node.func)
            # Attribute to enclosing function/method scope.
            scope_name, scope_kind, _ = _scope_for_line(scope_ranges, node.lineno)
            results.append(
                _finding(
                    "boolean-parameter",
                    "info",
                    scope_name,
                    scope_kind,
                    _loc(rel_path, node.lineno),
                    bool_args,
                    _BOOLEAN_PARAMETER_THRESHOLD,
                    (
                        f"Boolean parameter: {call_name}(...) passes "
                        f"{bool_args} positional bool flags -- prefer "
                        f"keyword args or an enum"
                    ),
                )
            )
    return results


def _render_call_name(func: ast.AST) -> str:
    """Best-effort string rendering of a Call.func AST node.

    Handles ``Name`` and ``Attribute`` chains; anything else collapses to
    ``<call>``. Used only for human-readable description text; no
    semantic decisions hang on this.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        head = _render_call_name(func.value)
        return f"{head}.{func.attr}" if head != "<call>" else func.attr
    return "<call>"


# ---------------------------------------------------------------------------
# W601 -- switch-statement detector
#
# Definition: a ``match`` statement OR an ``if`` / ``elif`` chain with >= 8
# arms whose every test discriminates on the SAME single variable. Both
# shapes are polyadic dispatch on one discriminator; >= 8 arms suggests the
# logic would be clearer as a strategy / polymorphic dispatch table.
#
# Implementation: ast.parse per Python file, walk every node. For each
# ``ast.Match`` whose ``subject`` is a single ``Name``, count cases; if
# >= 8, flag. For each top-level ``ast.If`` (one whose parent does NOT
# treat it as the ``orelse`` ``elif`` continuation of another ``If``),
# walk the ``orelse`` chain counting arms and the discriminator names;
# >= 8 arms AND all arms share the same single-variable discriminator
# means a switch-statement smell.
#
# Discriminator detection accepts the canonical dispatch shapes:
#   * ``x == lit``           (Compare(Name, [Eq], [Constant]))
#   * ``x is lit``           (Compare(Name, [Is], [Constant]))
#   * ``x in (...)``         (Compare(Name, [In], [...]))
#   * ``isinstance(x, T)``   (Call(Name('isinstance'), [Name, ...]))
# A chain mixing isinstance and equality on the same ``x`` is still a
# single-discriminator switch.
#
# Severity: info -- polyadic dispatch is sometimes idiomatic (parser
# tables, codecs). The detector surfaces it for review.
# Confidence tier: structural -- pure AST shape, no name heuristics.
# Python-only -- brace-language switch/case detection is a follow-up.
# ---------------------------------------------------------------------------

_SWITCH_STATEMENT_THRESHOLD: int = 8


def _switch_discriminator(test: ast.AST) -> str | None:
    """Return the discriminator variable name for a switch-shape test.

    Recognises the four canonical polyadic-dispatch shapes:

      * ``x == lit``, ``x is lit``   -> ``"x"``
      * ``x in (...)``               -> ``"x"``
      * ``isinstance(x, T)``         -> ``"x"``

    Returns ``None`` for anything else (compound expressions, method
    calls, attribute access). The caller requires every arm in a chain
    to return the same non-``None`` name.
    """
    # isinstance(x, T) -- accept Name(x) as the discriminator.
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and test.args
        and isinstance(test.args[0], ast.Name)
    ):
        return test.args[0].id
    # x == lit / x is lit / x in (...) -- single comparator, Name on left.
    if (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.Eq, ast.Is, ast.In))
    ):
        return test.left.id
    return None


def _collect_switch_elif_tails(tree: ast.Module) -> set[int]:
    """Return the ``id()`` of every ``If`` that is the orelse-tail of another
    ``If`` -- those are ``elif`` continuations and must NOT be treated as
    independent chain heads (each chain is processed once, at its head)."""
    tails: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            tails.add(id(node.orelse[0]))
    return tails


def _switch_chain_discriminators(head_if: ast.If) -> list[str | None]:
    """Walk an ``if``/``elif`` chain from its head, returning the
    discriminator of each arm in order (one entry per ``If`` in the chain)."""
    chain_discs: list[str | None] = []
    cur: ast.AST = head_if
    while isinstance(cur, ast.If):
        chain_discs.append(_switch_discriminator(cur.test))
        if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
        else:
            break
    return chain_discs


def _switch_finding(
    scope_name: str,
    scope_kind: str,
    rel_path: str,
    lineno: int,
    discriminator: str,
    count: int,
    form_label: str,
    unit: str,
) -> dict:
    """Build one switch-statement finding dict.

    ``form_label`` / ``unit`` vary by shape (``"match"``/``"cases"`` for the
    ``match`` form, ``"if/elif chain"``/``"arms"`` for the ``if``/``elif``
    form); everything else is shared.
    """
    return _finding(
        "switch-statement",
        "info",
        scope_name,
        scope_kind,
        _loc(rel_path, lineno),
        count,
        _SWITCH_STATEMENT_THRESHOLD,
        (
            f"Switch statement: {form_label} on `{discriminator}` "
            f"with {count} {unit} in {scope_name} -- "
            f"consider a dispatch table or strategy pattern"
        ),
    )


def _switch_finding_for_node(
    conn: sqlite3.Connection,
    file_id: int,
    rel_path: str,
    node: ast.AST,
    elif_tails: set[int],
) -> dict | None:
    """Return a switch-statement finding for ``node`` if it is a qualifying
    ``match`` statement or ``if``/``elif`` chain head, else ``None``."""
    # match-statement form.
    if isinstance(node, ast.Match):
        if not isinstance(node.subject, ast.Name):
            return None
        cases = len(node.cases)
        if cases < _SWITCH_STATEMENT_THRESHOLD:
            return None
        scope_name, scope_kind, _ = _enclosing_symbol(conn, file_id, node.lineno)
        return _switch_finding(
            scope_name,
            scope_kind,
            rel_path,
            node.lineno,
            node.subject.id,
            cases,
            "match",
            "cases",
        )

    # if-elif chain form -- skip elif continuations.
    if not isinstance(node, ast.If) or id(node) in elif_tails:
        return None

    chain_discs = _switch_chain_discriminators(node)
    arms = len(chain_discs)
    if arms < _SWITCH_STATEMENT_THRESHOLD:
        return None
    # Every arm must resolve to a single non-``None`` discriminator AND
    # every arm must share that same variable name.
    head = chain_discs[0]
    if head is None or any(d != head for d in chain_discs):
        return None
    scope_name, scope_kind, _ = _enclosing_symbol(conn, file_id, node.lineno)
    return _switch_finding(
        scope_name,
        scope_kind,
        rel_path,
        node.lineno,
        head,
        arms,
        "if/elif chain",
        "arms",
    )


# Tier: structural — AST-shape predicate over ``Match`` / ``If``-``Elif``
# chains. The discriminator-equality check is deterministic and the
# threshold (>= 8 arms) filters short dispatch ladders; no name match,
# so structural rather than heuristic.
@detector("switch-statement", confidence=CONFIDENCE_STRUCTURAL)
def detect_switch_statement(conn: sqlite3.Connection) -> list[dict]:
    """Detect ``match`` / ``if``-``elif`` chains with >= 8 arms on one var.

    W601. Walks every Python file's AST. Flags two shapes:

      * ``match x: case a: ...; case b: ...`` with >= 8 ``case`` arms and
        ``x`` a single ``Name``.
      * ``if x == a: ... elif x == b: ... elif x == c: ...`` (or the
        ``isinstance`` / ``in`` variants) with >= 8 arms, all arms
        discriminating on the same single variable.

    Suggests refactoring to a dispatch table / strategy pattern.

    Severity: info -- structural smell, not a defect.
    Python-only -- brace-language ``switch``/``case`` detection is a
    follow-up if/when demand surfaces.
    """
    results: list[dict] = []
    try:
        files = conn.execute("SELECT id, path, language FROM files WHERE language = 'python'").fetchall()
    except sqlite3.OperationalError:
        return []

    workspace = _find_indexed_source_root()

    for f in files:
        file_id = f["id"]
        rel_path = f["path"]
        # W1301: shared per-run AST cache (see detect_magic_numbers).
        tree = _read_and_parse(workspace, rel_path)
        if tree is None:
            continue
        # Each chain is processed once at its head; ``elif`` tails are
        # skipped via ``elif_tails`` (see ``_switch_finding_for_node``).
        elif_tails = _collect_switch_elif_tails(tree)
        for node in ast.walk(tree):
            finding = _switch_finding_for_node(conn, file_id, rel_path, node, elif_tails)
            if finding is not None:
                results.append(finding)
    return results


# ---------------------------------------------------------------------------
# W602 -- temporal-coupling detector
#
# Definition: a pair of (function, method) symbols across two different
# files that (a) frequently change together (``git_cochange.cochange_count
# >= 10``) AND (b) have a direct call-graph edge between them in either
# direction. Sequential coupling that should be encapsulated -- the pair
# is logically one operation that's been split across two modules.
#
# Implementation: single SQL JOIN. ``git_cochange`` is file-pair scoped,
# but ``edges`` is symbol-scoped, so we join ``git_cochange`` to
# ``symbols`` on both sides and then to ``edges`` to find the function-
# pair instances. The cross-file constraint is automatic: the JOIN keys
# ``file_id_a`` and ``file_id_b`` are always distinct in
# ``git_cochange`` rows (the table tracks PAIRS of files, never
# self-pairs).
#
# We dedupe at the (sorted source_id, sorted target_id) tuple level so a
# bidirectional edge between two symbols emits ONE finding, not two.
#
# Confidence tier: heuristic -- combines a co-change signal (history,
# sometimes noisy) with an edge signal (structural). The combination is
# stronger than either alone but still benefits from human review.
# Severity: warning -- temporal coupling is more actionable than info-tier
# style smells; the pair often hides a missing abstraction.
#
# W647 -- symbol-centric rollup. After the pair scan completes, group the
# pair findings by canonical symbol (each pair contributes ONE entry to
# each of its two endpoints). Any symbol that appears in N>=2 distinct
# pairs is the strongest "missing abstraction" signal in the data: the
# symbol is coupled to multiple OTHER symbols, so the right fix is
# rarely "extract a 2-symbol interface" -- it is "extract a cluster
# interface" or "stop reaching across the seam from N different sites".
# We emit one ADDITIONAL cluster finding per such symbol; the pair
# findings stay (operators want both views: pair history + cluster
# topology). The cluster smell_id is ``temporal-coupling-cluster`` and
# its confidence tier is ``structural`` -- the predicate "appears in
# >=2 heuristic pairs" is a graph-level pattern over the pair set, not
# a fresh history heuristic.
# ---------------------------------------------------------------------------

_TEMPORAL_COUPLING_COCHANGE_THRESHOLD: int = 10
_TEMPORAL_COUPLING_CLUSTER_MIN_PARTNERS: int = 2


# W894: parent tier is HEURISTIC (joins git_cochange history-signal to edges;
# the combination is stronger than either alone but history can still mis-pair
# files refactored together). The rollup ``temporal-coupling-cluster`` is
# STRUCTURAL — the predicate "symbol appears in >=2 distinct pair findings" is
# a graph-level pattern over the pair set rather than a fresh history heuristic.
# W895: parent + rollup register in a single declaration via ``rollup_kinds``.
@detector(
    "temporal-coupling",
    confidence=CONFIDENCE_HEURISTIC,
    rollup_kinds={"cluster": CONFIDENCE_STRUCTURAL},
)
def detect_temporal_coupling(conn: sqlite3.Connection) -> list[dict]:
    """Detect function pairs that co-change AND are call-graph connected.

    W602. Joins ``git_cochange`` to ``symbols`` + ``edges`` to find pairs
    of (function, method) symbols across two files that change together
    >= 10 times AND have a direct call-graph edge in either direction.

    The pair is cross-file by construction (``git_cochange`` only stores
    inter-file pairs). Each unique symbol pair emits exactly one finding;
    bidirectional edges are deduped via a sorted-id key.

    Severity: warning -- a strong "missing abstraction" signal worth a
    human review.
    """
    results: list[dict] = []
    try:
        rows = conn.execute(
            "SELECT gc.cochange_count AS cc, "
            "       sa.id AS sa_id, sa.name AS sa_name, sa.kind AS sa_kind, "
            "       sa.line_start AS sa_line, fa.path AS fa_path, "
            "       sb.id AS sb_id, sb.name AS sb_name, sb.kind AS sb_kind, "
            "       sb.line_start AS sb_line, fb.path AS fb_path "
            "FROM git_cochange gc "
            "JOIN files fa ON fa.id = gc.file_id_a "
            "JOIN files fb ON fb.id = gc.file_id_b "
            "JOIN symbols sa ON sa.file_id = gc.file_id_a "
            "JOIN symbols sb ON sb.file_id = gc.file_id_b "
            "JOIN edges e ON (e.source_id = sa.id AND e.target_id = sb.id) "
            "             OR (e.source_id = sb.id AND e.target_id = sa.id) "
            "WHERE gc.cochange_count >= ? "
            "  AND sa.kind IN ('function', 'method') "
            "  AND sb.kind IN ('function', 'method')",
            (_TEMPORAL_COUPLING_COCHANGE_THRESHOLD,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Pre-W21 schema (no git_cochange) or missing files/edges tables.
        return []

    # Dedupe: sorted symbol-id pair is the canonical key. A pair with a
    # bidirectional edge surfaces twice in the JOIN -- once per direction.
    seen: set[tuple[int, int]] = set()
    # W647 -- cluster rollup state. Each symbol id maps to a tuple
    # (name, kind, path, line_start, [(partner_name, partner_path, cc), ...]).
    # We populate as we walk the dedup'd pair set and emit cluster findings
    # after the pair loop. Using id as the key (not name+path) so a rename
    # in either half of a pair doesn't collide two distinct symbols.
    clusters: dict[int, dict] = {}

    def _bump_cluster(
        sym_id: int,
        sym_name: str,
        sym_kind: str,
        sym_path: str,
        sym_line: int | None,
        partner_name: str,
        partner_path: str,
        cc: int,
    ) -> None:
        bucket = clusters.get(sym_id)
        if bucket is None:
            bucket = {
                "name": sym_name,
                "kind": sym_kind,
                "path": sym_path,
                "line": sym_line,
                "partners": [],
            }
            clusters[sym_id] = bucket
        bucket["partners"].append((partner_name, partner_path, cc))

    for r in rows:
        sa_id = int(r["sa_id"])
        sb_id = int(r["sb_id"])
        if sa_id == sb_id:
            # Defensive: should be impossible because file_id_a != file_id_b
            # in git_cochange and (sa, sb) live in different files.
            continue
        key = (min(sa_id, sb_id), max(sa_id, sb_id))
        if key in seen:
            continue
        seen.add(key)

        # Canonical ordering: the symbol whose id matches ``key[0]`` is
        # symbol A (so the finding's primary location is stable across
        # re-runs regardless of which JOIN direction surfaced the row).
        if sa_id == key[0]:
            primary_id = sa_id
            primary_name = r["sa_name"]
            primary_kind = r["sa_kind"]
            primary_path = r["fa_path"]
            primary_line = r["sa_line"]
            other_id = sb_id
            other_name = r["sb_name"]
            other_kind = r["sb_kind"]
            other_path = r["fb_path"]
            other_line = r["sb_line"]
        else:
            primary_id = sb_id
            primary_name = r["sb_name"]
            primary_kind = r["sb_kind"]
            primary_path = r["fb_path"]
            primary_line = r["sb_line"]
            other_id = sa_id
            other_name = r["sa_name"]
            other_kind = r["sa_kind"]
            other_path = r["fa_path"]
            other_line = r["sa_line"]

        cc = int(r["cc"])
        results.append(
            _finding(
                "temporal-coupling",
                "warning",
                primary_name,
                primary_kind,
                _loc(primary_path, primary_line),
                cc,
                _TEMPORAL_COUPLING_COCHANGE_THRESHOLD,
                (
                    f"Temporal coupling: {primary_name} ({primary_path}) and "
                    f"{other_name} ({other_path}) co-change in {cc} commits "
                    f"AND have a direct call-graph edge -- consider "
                    f"encapsulating the pair behind a single interface"
                ),
            )
        )

        # Each pair contributes one entry to each of its two endpoints.
        _bump_cluster(
            primary_id,
            primary_name,
            primary_kind,
            primary_path,
            primary_line,
            other_name,
            other_path,
            cc,
        )
        _bump_cluster(
            other_id,
            other_name,
            other_kind,
            other_path,
            other_line,
            primary_name,
            primary_path,
            cc,
        )

    # W647 -- emit cluster findings. Sort by symbol id for determinism so a
    # second run produces a byte-identical list (matters for findings-
    # registry upsert and the schema-migration hash-stability mandate).
    for sym_id in sorted(clusters.keys()):
        bucket = clusters[sym_id]
        partners = bucket["partners"]
        # Distinct partners only: a single underlying pair contributes one
        # entry, but defensive against future shape changes.
        unique_partners = sorted({(pn, pp) for pn, pp, _cc in partners})
        if len(unique_partners) < _TEMPORAL_COUPLING_CLUSTER_MIN_PARTNERS:
            continue
        # Highest co-change in the cluster -- the cluster's "strength".
        max_cc = max(cc for _pn, _pp, cc in partners)
        partners_render = ", ".join(f"{pn} ({pp})" for pn, pp in unique_partners)
        results.append(
            _finding(
                "temporal-coupling-cluster",
                "warning",
                bucket["name"],
                bucket["kind"],
                _loc(bucket["path"], bucket["line"]),
                len(unique_partners),
                _TEMPORAL_COUPLING_CLUSTER_MIN_PARTNERS,
                (
                    f"Temporal coupling cluster: {bucket['name']} "
                    f"({bucket['path']}) co-changes with "
                    f"{len(unique_partners)} distinct partners "
                    f"(max {max_cc} commits) -- {partners_render} -- "
                    f"consider extracting a single abstraction the cluster "
                    f"can call through instead of N pairwise call sites"
                ),
            )
        )
    return results


# W647 rollup: detect_temporal_coupling emits BOTH ``temporal-coupling`` (the
# pair findings) AND ``temporal-coupling-cluster`` (a per-symbol rollup over
# the pair set). The rollup has no separate ALL_DETECTORS row -- it's emitted
# from inside the same function body. W895 collapsed the two registrations
# into the single ``rollup_kinds={"cluster": CONFIDENCE_STRUCTURAL}`` kwarg
# on the @detector decorator above, so no separate register_rollup_kind call
# is needed here.


# ---------------------------------------------------------------------------
# W605 -- comment-density (TODO / FIXME / XXX / HACK marker rate)
#
# Definition: per file, count comment lines that mention TODO / FIXME / XXX
# / HACK. Flag the file when:
#
#   * marker_count >= _COMMENT_DENSITY_MIN_MARKERS (3), AND
#   * marker_count / total_lines >= _COMMENT_DENSITY_MIN_RATE (0.05)
#
# Both gates must hold: the absolute floor avoids flagging short files with
# one stray TODO; the rate gate avoids flagging long files with a small
# absolute number of markers. The smell shape is tech-debt accumulation --
# files where unresolved markers have piled up faster than the surrounding
# code has been cleaned.
#
# Confidence tier: heuristic -- the predicate is a regex over source lines,
# and a project might legitimately use TODO comments as a workflow anchor
# rather than as debt. Surfaces the FP risk to consumers.
#
# Severity: info -- style / hygiene signal, not a correctness defect.
# ---------------------------------------------------------------------------


# Thresholds: both gates must hold. ``_COMMENT_DENSITY_MIN_RATE`` is the
# fraction of marker-bearing lines over total file lines.
_COMMENT_DENSITY_MIN_MARKERS: int = 3
_COMMENT_DENSITY_MIN_RATE: float = 0.05


# W705 -- unified per-language comment syntax record. One row per
# language gives ``detect_comment_density`` a single lookup table for
# both the line-comment pass (W605) and the block-comment pass (W650).
#
# Each entry names ``line`` prefixes (e.g. ``("#",)``, ``("//", "#")``
# for PHP which honours both) and ``block`` delimiter pairs (e.g.
# ``(("/*", "*/"),)``, ``(("<!--", "-->"),)``). Either tuple may be
# empty -- CSS has no line comments, shell has no block comments.
#
# Language keys use the indexer's stored ``files.language`` values
# (e.g. ``c_sharp`` not ``csharp``, ``bash`` not ``shell``) so the
# detector's lookup matches the database without an alias hop. The
# regex tier stays ``heuristic`` -- a marker inside a string literal
# is still scanned because we do NOT tokenize the source.
@dataclass(frozen=True)
class _CommentSyntax:
    """Per-language comment markers for the comment-density detector.

    ``line``: tuple of line-comment prefixes (e.g. ``("//",)`` or
    ``("//", "#")`` for PHP). A line is treated as a comment when its
    lstripped form starts with any prefix.

    ``block``: tuple of ``(open, close)`` delimiter pairs for
    span-style comments (e.g. ``(("/*", "*/"),)`` or
    ``(("<!--", "-->"),)``). Each pair is matched non-greedy across
    newlines and inner ``\\b(TODO|FIXME|XXX|HACK)\\b`` occurrences
    are counted via ``findall``.
    """

    line: tuple[str, ...] = ()
    block: tuple[tuple[str, str], ...] = ()


_COMMENT_SYNTAX_BY_LANG: dict[str, _CommentSyntax] = {
    # Hash-line only.
    "python": _CommentSyntax(line=("#",)),
    "ruby": _CommentSyntax(line=("#",)),
    "bash": _CommentSyntax(line=("#",)),
    "yaml": _CommentSyntax(line=("#",)),
    # C-family: ``//`` line + ``/* */`` block.
    "javascript": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "typescript": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "java": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "c": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "cpp": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "c_sharp": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "go": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "rust": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "kotlin": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "swift": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "scala": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "dart": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    # PHP honours both ``//`` and ``#`` line comments.
    "php": _CommentSyntax(line=("//", "#"), block=(("/*", "*/"),)),
    # Block-only.
    "css": _CommentSyntax(block=(("/*", "*/"),)),
    "scss": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    # HTML-family: ``<!-- -->`` only.
    "html": _CommentSyntax(block=(("<!--", "-->"),)),
    # SQL: ``--`` line + ``/* */`` block.
    "sql": _CommentSyntax(line=("--",), block=(("/*", "*/"),)),
    # HCL/Terraform: ``#`` and ``//`` line + ``/* */`` block.
    "hcl": _CommentSyntax(line=("#", "//"), block=(("/*", "*/"),)),
    # Apex (Salesforce): ``//`` line + ``/* */`` block.
    "apex": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    # W725: Visual FoxPro line-START comments. The extractor at
    # ``roam.languages.foxpro_lang._preprocess`` treats ``*`` at line
    # start as a full-line comment (unambiguous -- multiplication
    # requires a left operand and cannot appear at line-start). VFP
    # also accepts ``&&`` as an inline end-of-line marker; the
    # detector's line-prefix model only catches the rarer left-
    # justified ``&& TODO: ...`` form, which is enough to keep VFP
    # files in the marker-count pass without bespoke inline scanning.
    "foxpro": _CommentSyntax(line=("*", "&&"), block=()),
    # W703: round out the canonical 28-language coverage. ``tsx`` and
    # ``jsonc`` are pure C-family. Vue / Svelte single-file components
    # carry a ``<script>`` block (C-family) AND an HTML template region
    # (``<!-- -->``); union the markers so both halves are scanned. The
    # Salesforce metadata languages (sfxml / aura / visualforce) are XML
    # so only ``<!-- -->`` applies.
    "tsx": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "jsonc": _CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    "vue": _CommentSyntax(line=("//",), block=(("/*", "*/"), ("<!--", "-->"))),
    "svelte": _CommentSyntax(line=("//",), block=(("/*", "*/"), ("<!--", "-->"))),
    "sfxml": _CommentSyntax(block=(("<!--", "-->"),)),
    "aura": _CommentSyntax(block=(("<!--", "-->"),)),
    "visualforce": _CommentSyntax(block=(("<!--", "-->"),)),
}


# W703: explicit skip-set for canonical languages that intentionally do
# NOT participate in comment-density scanning. Membership is justified
# in-line; the drift-guard test in tests/test_w703_comment_syntax_coverage.py
# asserts every canonical language is either in ``_COMMENT_SYNTAX_BY_LANG``
# OR here -- no silent omission (Pattern 2 fallback).
_COMMENT_DENSITY_NO_SUPPORT: frozenset[str] = frozenset(
    {
        # MDX is Markdown + JSX. Markdown uses ``<!-- -->``; JSX uses
        # ``{/* */}`` which is neither a plain block nor a plain line
        # syntax. No canonical single comment vocabulary, so we skip
        # rather than guess and emit noisy heuristics.
        "mdx",
    }
)

# Word-boundary match: ``\b(TODO|FIXME|XXX|HACK)\b``. Pre-compile because the
# detector walks every file row and re-compiling per call is wasteful.
_COMMENT_DENSITY_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")

# Per-language block-comment regex cache. Built once per ``(open, close)``
# delimiter pair -- non-greedy across newlines so a multi-line
# ``/** ... */`` or ``<!-- ... -->`` block is one match. ``re.escape``
# the delimiters because ``*``, ``/``, ``<``, ``!``, ``-``, ``>`` are
# all literal but some are regex metacharacters.
_COMMENT_BLOCK_RE_CACHE: dict[tuple[str, str], re.Pattern[str]] = {}


def _block_re(open_delim: str, close_delim: str) -> re.Pattern[str]:
    """Get or build the non-greedy regex for ``open ... close`` spans."""
    key = (open_delim, close_delim)
    pat = _COMMENT_BLOCK_RE_CACHE.get(key)
    if pat is None:
        pat = re.compile(re.escape(open_delim) + r"[\s\S]*?" + re.escape(close_delim))
        _COMMENT_BLOCK_RE_CACHE[key] = pat
    return pat


def _comment_density_syntax(lang: str) -> _CommentSyntax | None:
    """Return comment syntax for ``lang`` and log unexpected omissions."""
    syntax = _COMMENT_SYNTAX_BY_LANG.get(lang)
    if syntax is None and lang not in _COMMENT_DENSITY_NO_SUPPORT:
        log.debug(
            "detect_comment_density: skipped unsupported language %r",
            lang,
        )
    return syntax


def _read_comment_density_source(workspace, rel_path: str) -> str | None:
    try:
        return (workspace / rel_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        log.debug(
            "detect_comment_density: skipped unreadable source %r under %r: %s",
            rel_path,
            workspace,
            exc,
        )
        return None


def _longest_comment_prefix(stripped: str, prefixes: tuple[str, ...]) -> str | None:
    matched_prefix: str | None = None
    for prefix in prefixes:
        if stripped.startswith(prefix):
            if matched_prefix is None or len(prefix) > len(matched_prefix):
                matched_prefix = prefix
    return matched_prefix


def _line_comment_has_debt_marker(line: str, prefixes: tuple[str, ...]) -> bool:
    stripped = line.lstrip()
    matched_prefix = _longest_comment_prefix(stripped, prefixes)
    if matched_prefix is None:
        return False

    # Strip the comment prefix so ``//TODO`` still has a word-boundary at
    # the start of the comment body.
    body = stripped[len(matched_prefix) :]
    return _COMMENT_DENSITY_MARKER_RE.search(body) is not None


def _line_comment_marker_count(lines: list[str], syntax: _CommentSyntax) -> int:
    if not syntax.line:
        return 0
    return sum(1 for line in lines if _line_comment_has_debt_marker(line, syntax.line))


def _block_comment_marker_count(source: str, syntax: _CommentSyntax) -> int:
    marker_count = 0
    for open_delim, close_delim in syntax.block:
        pat = _block_re(open_delim, close_delim)
        for block in pat.findall(source):
            marker_count += len(_COMMENT_DENSITY_MARKER_RE.findall(block))
    return marker_count


def _comment_density_marker_count(source: str, syntax: _CommentSyntax) -> tuple[int, int] | None:
    lines = source.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return None

    marker_count = _line_comment_marker_count(lines, syntax)
    marker_count += _block_comment_marker_count(source, syntax)
    return marker_count, total_lines


def _comment_density_rate(marker_count: int, total_lines: int) -> float | None:
    if marker_count < _COMMENT_DENSITY_MIN_MARKERS:
        return None

    rate = marker_count / total_lines
    if rate < _COMMENT_DENSITY_MIN_RATE:
        return None
    return rate


def _comment_density_finding(
    rel_path: str,
    marker_count: int,
    total_lines: int,
    rate: float,
) -> dict:
    pct = round(rate * 100.0, 1)
    return _finding(
        "comment-density",
        "info",
        rel_path,
        "file",
        _loc(rel_path, None),
        marker_count,
        _COMMENT_DENSITY_MIN_MARKERS,
        (
            f"Comment density: {rel_path} has {marker_count} "
            f"TODO/FIXME/XXX/HACK markers in {total_lines} lines "
            f"({pct}% rate) -- review and resolve accumulated debt markers"
        ),
    )


@detector("comment-density", confidence=CONFIDENCE_HEURISTIC)
def detect_comment_density(conn: sqlite3.Connection) -> list[dict]:
    """Detect files where TODO/FIXME/XXX/HACK markers accumulate.

    W605 + W650 + W705. Walks every indexed source file in a supported
    language, counts comment regions that mention a marker word, and
    flags the file when BOTH the absolute count and the per-line rate
    clear the thresholds.

    Marker detection is language-aware via ``_COMMENT_SYNTAX_BY_LANG``:

    * Hash-line languages (Python / Ruby / Bash / YAML): ``#``-prefixed
      line comments.
    * C-family (JS / TS / Java / C / C++ / C# / Go / Rust / Kotlin /
      Swift / Scala / Dart / SCSS): ``//`` line + ``/* ... */`` block.
    * PHP: ``//`` AND ``#`` line + ``/* ... */`` block.
    * CSS: ``/* ... */`` block only (no line-comment syntax).
    * HTML: ``<!-- ... -->`` block only.
    * SQL: ``--`` line + ``/* ... */`` block.
    * HCL/Terraform: ``#`` AND ``//`` line + ``/* ... */`` block.
    * Apex: ``//`` line + ``/* ... */`` block.
    * FoxPro: ``*`` (line-start) and ``&&`` line prefixes; no block.

    The regex ``\\b(TODO|FIXME|XXX|HACK)\\b`` matches the four canonical
    markers. Line scans count one marker per line that hits; block scans
    count each marker occurrence inside a block (a multi-line JSDoc
    ``/** TODO: ... */`` that names a single marker still counts once,
    NOT once per physical line). Block comments DO count toward
    ``marker_count`` but DO NOT inflate ``total_lines`` -- the rate
    denominator stays "physical lines in the file".

    Confidence: heuristic. Severity: info.
    """
    results: list[dict] = []
    try:
        files = conn.execute("SELECT id, path, language FROM files WHERE language IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return []

    workspace = _find_indexed_source_root()

    for f in files:
        syntax = _comment_density_syntax(f["language"])
        if syntax is None:
            continue

        rel_path = f["path"]
        source = _read_comment_density_source(workspace, rel_path)
        if source is None:
            continue

        marker_data = _comment_density_marker_count(source, syntax)
        if marker_data is None:
            continue

        marker_count, total_lines = marker_data
        rate = _comment_density_rate(marker_count, total_lines)
        if rate is None:
            continue

        # File-level finding: ``symbol_name`` is the file path so the
        # finding renders without needing an enclosing symbol; ``kind``
        # is ``file`` so cmd_smells maps the registry subject_kind to
        # ``file`` (NULL subject_id) rather than ``symbol``.
        results.append(_comment_density_finding(rel_path, marker_count, total_lines, rate))
    return results


@detector("speculative-generality", confidence=CONFIDENCE_STRUCTURAL)
def detect_speculative_generality(conn: sqlite3.Connection) -> list[dict]:
    """YAGNI: symbols used only by tests, never by production code.

    ``dead`` detects ZERO incoming refs. This detector catches the
    sibling pattern: a production symbol has incoming refs but ALL
    come from test files -- the symbol exists to be tested, not to
    serve production. Common cause: speculative abstract base or
    extension point that nobody ever extends. AI agents over-engineer
    for hypothetical futures; this surfaces those markers.

    Requires >= 2 incoming refs so single-test fluke does not flag.
    Severity is ``info`` because some test-only abstractions are
    legitimate (protocol interfaces, test seams). Confidence tier is
    ``structural``: pure graph + file-role query, no name heuristics.

    W853 (W848-RESEARCH top-2 recommendation).
    """
    rows = conn.execute(
        """
        WITH incoming AS (
            SELECT
                e.target_id AS sym_id,
                COUNT(*) AS total_refs,
                SUM(CASE WHEN f.file_role = 'test' THEN 1 ELSE 0 END) AS test_refs
            FROM edges e
            JOIN symbols s_src ON e.source_id = s_src.id
            JOIN files f ON s_src.file_id = f.id
            GROUP BY e.target_id
            HAVING total_refs >= 2
        )
        SELECT
            i.sym_id, i.total_refs, i.test_refs,
            s.name, s.kind, s.line_start, s.line_end,
            sf.path AS file_path, sf.file_role AS sym_file_role
        FROM incoming i
        JOIN symbols s ON i.sym_id = s.id
        JOIN files sf ON s.file_id = sf.id
        WHERE i.test_refs = i.total_refs
          AND sf.file_role != 'test'
        """
    ).fetchall()
    results: list[dict] = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        total_refs = r["total_refs"]
        results.append(
            _finding(
                "speculative-generality",
                "info",
                r["name"],
                r["kind"],
                loc_str,
                total_refs,
                2,
                f"Speculative generality: {total_refs} refs, all from test files",
            )
        )
    return results


# ---------------------------------------------------------------------------
# Detector registry
# ---------------------------------------------------------------------------

# W871 bulk migration: register the three out-of-file detectors that ship as
# their own modules (parallel_hierarchy, clones_cross_layer, type_switch).
# Calling ``detector(...)(fn)`` directly here keeps the decorator-driven
# registry single-sourced in ``smells.py`` rather than scattering @detector
# annotations across the helper modules they were extracted into. The
# returned function is the original ``fn`` unchanged (decorator is
# side-effect only), so callers that already imported the symbol keep
# working unchanged.
# Tier: structural — each lifts a graph/AST shape pattern rather than a
# name match:
#   * parallel-hierarchy: detects parallel inheritance chains via the
#     ``edges.kind='inherits'`` graph (siblings with mirrored structure).
#   * cross-layer-clone: clone-pair set joined to layer assignments; the
#     "same content across layers" predicate is graph-anchored.
#   * type-switch: AST-shape ``isinstance`` ladders, structurally similar
#     to ``switch-statement`` (also structural).
detector("parallel-hierarchy", confidence=CONFIDENCE_STRUCTURAL)(detect_parallel_hierarchy)
detector("cross-layer-clone", confidence=CONFIDENCE_STRUCTURAL)(detect_cross_layer_clones)
detector("type-switch", confidence=CONFIDENCE_STRUCTURAL)(detect_type_switch)


# W941: derived view -- single source of truth is the @detector decorator
# above. The decorator wires the registry at import time; by the time this
# module-level statement runs, ``all_detectors()`` returns the full set.
# Sorted alphabetically by smell_id per W896 (SARIF-stable, grep-friendly).
# Eliminates the parallel-maintenance class fixed-in-place by the W862 lint.
ALL_DETECTORS: list[tuple[str, Callable]] = list(all_detectors())

# W564: severity sort key sources from roam.output._severity.severity_rank
# (canonical, higher = worse). Negate to keep "critical first" ordering.


def run_all_detectors(
    conn: sqlite3.Connection,
    only: frozenset[str] | set[str] | None = None,
) -> list[dict]:
    """Run all 24 smell detectors and return combined findings.

    Returns list of finding dicts sorted by severity (critical first).

    W897: ``freeze_registry()`` runs first as the construction-time
    correctness gate -- if any decorator side-effect was bypassed or any
    rollup confidence-tier mapping lost its parent during a refactor,
    the run fails loudly here rather than silently mis-classifying
    findings downstream.

    W1294 (perf pushdown): ``only`` restricts the dispatch loop to the
    named ``smell_id`` set BEFORE invoking each detector function. The
    default ``None`` runs every registered detector (byte-identical to
    pre-W1294 behaviour). An empty set runs zero detectors. The caller
    owns closed-enum validation of the set against the registry — this
    function performs work skipping, not vocabulary checking, so an
    unknown id silently does nothing (its detector simply isn't in
    ``ALL_DETECTORS`` to dispatch).
    """
    freeze_registry()
    findings: list[dict] = []
    for _smell_id, detect_fn in ALL_DETECTORS:
        if only is not None and _smell_id not in only:
            continue
        try:
            hits = detect_fn(conn)
        except sqlite3.Error as err:
            # Per-detector DB error: one bad query shouldn't kill the run.
            # Log + continue so the remaining detectors still produce
            # findings the operator can act on.
            log.warning(
                "smells detector %s failed with sqlite error: %s",
                getattr(detect_fn, "__name__", _smell_id),
                err,
            )
            continue
        except (NameError, ImportError, AttributeError, TypeError) as err:
            # Programmer bug (missing import, wrong attribute, signature drift):
            # fail-loud per W531 + CLAUDE.md "Pattern-2 always-emit" discipline.
            # W601/W602 dropped a Counter import that this exact handler used to
            # swallow silently — W653 fixes that bug class at the loop level.
            raise RuntimeError(
                f"smells detector {getattr(detect_fn, '__name__', _smell_id)} "
                f"crashed with programmer error: {type(err).__name__}: {err}"
            ) from err
        findings.extend(hits)
    # Sort: critical first, then warning, then info (negated canonical rank).
    findings.sort(key=lambda f: -severity_rank(f.get("severity", "info")))
    return findings


def file_health_scores(conn: sqlite3.Connection) -> dict[str, float]:
    """Compute per-file health scores from smell findings.

    Returns {file_path: score} where score is 1-10 (10 = healthy).
    Penalties: critical = -3, warning = -1.5, info = -0.5. Min score = 1.
    """
    findings = run_all_detectors(conn)
    penalties: dict[str, float] = {}
    for f in findings:
        loc_str = f.get("location", "")
        # Extract file path from location (path:line or path)
        file_path = loc_str.split(":")[0] if ":" in loc_str else loc_str
        if not file_path:
            continue
        sev = f.get("severity", "info")
        if sev == "critical":
            penalty = 3.0
        elif sev == "warning":
            penalty = 1.5
        else:
            penalty = 0.5
        penalties[file_path] = penalties.get(file_path, 0.0) + penalty

    # Get all indexed files
    files = conn.execute("SELECT path FROM files").fetchall()
    scores: dict[str, float] = {}
    for row in files:
        path = row["path"]
        penalty = penalties.get(path, 0.0)
        score = max(1.0, 10.0 - penalty)
        scores[path] = round(score, 1)
    return scores
