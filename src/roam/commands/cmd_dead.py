"""Show unreferenced exported symbols (dead code)."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time as _time
from collections import defaultdict
from statistics import median

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.db.edge_kinds import CALL_EDGE_KINDS
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    strip_list_payloads,
    to_json,
)
from roam.output.metric_definitions import (
    DEAD_EXPORT_ACTION_DEFINITION,
    DEAD_EXPORT_DEFINITION,
)
from roam.rules.dataflow import collect_dataflow_findings

# W96 — dead-export detector is the second migration onto the central
# findings registry (after W95's clones). Detector version is stamped on
# every emitted finding so consumers can spot rows produced under an
# older dead-export classifier shape (e.g. before scaffolding was
# recognised). Bump per the rules in roam.catalog.versions when the
# classifier or action verdict mapping changes meaningfully.
DEAD_DETECTOR_VERSION: str = "1.0.0"


def _dead_finding_id(symbol_id: int, file_path: str, kind: str) -> str:
    """Stable, deterministic finding id for one dead-export.

    The (symbol_id, kind, file_path) triple is enough to re-identify the
    same export across runs — symbols.id stays stable across re-indexes
    of unchanged code, and the kind+path are folded in so a renamed
    symbol that lands on the same id (rare but possible after a churn
    rebuild) still gets a fresh id. Re-running ``roam dead --persist``
    on the same input upserts the existing row rather than duplicating.
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("dead", "export", symbol_id, kind, file_path)


def _dead_confidence_tier(action: str) -> str:
    """Map the dead-export ``action`` verdict to a registry confidence tier.

    - SAFE  → ``static_analysis``: the call-graph proves no production
              consumers exist; this is the strongest signal the detector
              can produce.
    - REVIEW → ``structural``: heuristic patterns (API naming, barrel
               files, test-only consumers) — graph-backed but
               name-dependent.
    - INTENTIONAL / INTENTIONAL_SCAFFOLDING → ``heuristic``: pure name /
               docstring pattern match, no graph evidence.
    """
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STATIC_ANALYSIS,
        CONFIDENCE_STRUCTURAL,
    )

    if action == "SAFE":
        return CONFIDENCE_STATIC_ANALYSIS
    if action == "REVIEW":
        return CONFIDENCE_STRUCTURAL
    # INTENTIONAL / INTENTIONAL_SCAFFOLDING / unknown — pattern match only.
    return CONFIDENCE_HEURISTIC


def _emit_dead_findings(conn, dead_records: list[dict]) -> None:
    """Emit one ``FindingRecord`` per dead-export into the registry.

    Each record in ``dead_records`` is the dict shape produced by the
    json-mode build path: ``{"symbol_id", "name", "kind", "file_path",
    "line_start", "action", "confidence_pct", "reason", "tested",
    "scaffolding", "scaffolding_evidence"}``. The dict shape is the
    contract — emit doesn't peek at the raw symbols row.

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard dead command.
    """
    # Local import to keep the cost out of the readonly read-only path —
    # callers without --persist never reach here, so the import only
    # runs when we're actually writing.
    from roam.db.findings import FindingRecord, emit_finding

    for d in dead_records:
        symbol_id = d.get("symbol_id")
        if symbol_id is None:
            continue
        action = d.get("action") or "UNKNOWN"
        file_path = d.get("file_path") or ""
        kind = d.get("kind") or ""
        finding_id = _dead_finding_id(int(symbol_id), file_path, kind)
        evidence = {
            "name": d.get("name"),
            "kind": kind,
            "file_path": file_path,
            "line_start": d.get("line_start"),
            "action": action,
            "confidence_pct": d.get("confidence_pct"),
            "reason": d.get("reason"),
            "tested": d.get("tested", False),
            "scaffolding": d.get("scaffolding", False),
            "scaffolding_evidence": d.get("scaffolding_evidence") or {},
        }
        claim = f"Dead export: {d.get('name')} ({kind}) at {file_path}:{d.get('line_start')} — action={action}"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol",
                subject_id=int(symbol_id),
                claim=claim,
                evidence_json=json.dumps(evidence, sort_keys=True),
                confidence=_dead_confidence_tier(action),
                source_detector="dead",
                source_version=DEAD_DETECTOR_VERSION,
            ),
        )


# R22 — confidence classifier for dead-export findings.
#
# Per the W12 brief:
#   high   — NOT reachable from any entry AND NOT covered by tests AND
#            has been stable (>30 days old).
#   medium — NOT reachable AND NOT tested but recently edited (<30 days,
#            may be WIP / scaffolding the dev hasn't finished hooking up).
#   low    — reachable from at least one entry (likely a false positive
#            — the symbol IS used, the detector missed the edge), OR
#            framework / barrel / scaffolding patterns we treat as
#            intentional.
#
# We map the existing per-symbol fields:
#   - `action == "SAFE"`        → strong "really dead" signal.
#   - `tested` (bool)           → covered by tests.
#   - `aging.age_days` (int)    → optional, only when --aging extended
#                                 data is computed. Falls back to 30 if
#                                 absent so non-extended runs still emit
#                                 reasonable confidence (we err toward
#                                 high when we lack age data because the
#                                 default `dead` output already filtered
#                                 for likely-dead symbols).
#   - `action == "INTENTIONAL"` and friends → low (framework hooks,
#                                              barrels, scaffolding).
_DEAD_HIGH_AGE_DAYS = 30


def _dead_classify(sym: dict) -> tuple[str, str]:
    """Map a dead-export finding to a (confidence, reason) tuple."""
    action = sym.get("action") or ""
    tested = bool(sym.get("tested"))
    aging = sym.get("aging") or {}
    age_days = aging.get("age_days") if isinstance(aging, dict) else None
    # Absent age data → treat as old enough to qualify.
    try:
        age_n = int(age_days) if age_days is not None else _DEAD_HIGH_AGE_DAYS
    except (TypeError, ValueError):
        age_n = _DEAD_HIGH_AGE_DAYS

    if action in ("INTENTIONAL", "INTENTIONAL_SCAFFOLDING"):
        return "low", (f"action={action}; framework / scaffolding signal — likely false positive")

    if action == "SAFE":
        if tested:
            # Tested but unreferenced in production — slightly weaker
            # signal than a truly-orphan symbol.
            return "medium", "no production consumers but tests reference it"
        if age_n >= _DEAD_HIGH_AGE_DAYS:
            return "high", (f"SAFE to delete, no test coverage, stable for {age_n}d (>= {_DEAD_HIGH_AGE_DAYS}d)")
        return "medium", (f"SAFE to delete, no test coverage but edited recently ({age_n}d ago — may be WIP)")

    # REVIEW / unclassified actions — keep medium so consumers don't
    # treat them as either gospel-truth dead or harmless.
    return "medium", f"action={action or 'unknown'}; manual review recommended"


_ENTRY_NAMES = {
    # Generic entry points
    "main",
    "app",
    "serve",
    "server",
    "setup",
    "run",
    "cli",
    "handler",
    "middleware",
    "route",
    "index",
    "init",
    "register",
    "boot",
    "start",
    "execute",
    "configure",
    "command",
    "worker",
    "job",
    "task",
    "listener",
    # Vue lifecycle hooks
    "mounted",
    "created",
    "beforeMount",
    "beforeDestroy",
    "beforeCreate",
    "activated",
    "deactivated",
    "onMounted",
    "onUnmounted",
    "onBeforeMount",
    "onBeforeUnmount",
    "onActivated",
    "onDeactivated",
    "onUpdated",
    "onBeforeUpdate",
    # React lifecycle
    "componentDidMount",
    "componentWillUnmount",
    "componentDidUpdate",
    # Angular lifecycle
    "ngOnInit",
    "ngOnDestroy",
    "ngOnChanges",
    "ngAfterViewInit",
    # Test lifecycle
    "setUp",
    "tearDown",
    "beforeEach",
    "afterEach",
    "beforeAll",
    "afterAll",
}
_ENTRY_FILE_BASES = {
    "server",
    "app",
    "main",
    "cli",
    "index",
    "manage",
    "boot",
    "bootstrap",
    "start",
    "entry",
    "worker",
}
_API_PREFIXES = (
    "get",
    "use",
    "create",
    "validate",
    "fetch",
    "update",
    "delete",
    "find",
    "check",
    "make",
    "build",
    "parse",
)


_ABC_METHOD_NAMES = frozenset(
    {
        "language_name",
        "file_extensions",
        "extract_symbols",
        "extract_references",
        "get_docstring",
        "get_signature",
        "node_text",
        "detect",
        "supported_bridges",
        "resolve_cross_language",
        "get_bridge_edges",
    }
)


# W157 — runtime-decorator-registered symbols look "dead" to a static
# call-graph analyser because the framework invokes them via the decorator
# registry, not via a literal `foo()` call inside another extracted symbol.
# The MCP server's `@_tool(name="roam_X")` is the worst-offender: 44 of 73
# SAFE-tier findings on roam-code's own registry were MCP wrappers (per the
# W149 dogfood audit). Following the SAFE recommendation would silently
# break the MCP transport.
#
# Source of truth is ``roam.mcp_server._TOOL_METADATA``: every entry there
# is a Python function whose qualified name lives in ``mcp_server.py`` and
# whose framework consumer is FastMCP's runtime tool-registry. Importing it
# at module-load is too expensive for the readonly path (fastmcp may not be
# installed); resolve lazily on first need and cache.
_MCP_TOOL_NAMES_CACHE: frozenset[str] | None = None


def _load_mcp_tool_names() -> frozenset[str]:
    """Return the set of MCP-tool symbol names registered via ``@_tool``.

    Combines TWO sources so the gate matches the symbols-table view:

    1. ``roam.mcp_server._TOOL_METADATA`` keys — the runtime
       ``name=``-kwarg form (``roam_clean``, ``roam_doctor``, …). This
       catches wrappers where ``@_tool(name="roam_X")`` happens to match
       the Python ``def`` name (about 40/149 wrappers).
    2. AST scan of ``mcp_server.py`` — collects the Python ``def`` name
       of every function decorated with ``@_tool(...)``, regardless of
       what the ``name=`` kwarg says. This catches the majority of
       wrappers where the def name differs from the registered tool name
       (e.g. ``def search_semantic`` decorated as ``roam_search_semantic``).

    The symbols table stores the Python ``def`` name in
    ``symbols.name`` — so the AST-derived set is what the dead detector
    actually needs to match against. The ``_TOOL_METADATA`` set is
    included as a defensive belt-and-braces source for the cases where
    both names coincide.

    Cached after first load so the per-symbol classifier stays O(1).

    Degrades to an empty frozenset when neither source is reachable
    (e.g. roam installed from a wheel without the source file on disk,
    or fastmcp extra missing). An empty set yields the legacy un-seeded
    behaviour — worst case is pre-W157 false positives, never a crash.
    """
    global _MCP_TOOL_NAMES_CACHE
    if _MCP_TOOL_NAMES_CACHE is not None:
        return _MCP_TOOL_NAMES_CACHE

    collected: set[str] = set()

    # Source 1: runtime metadata (name= kwarg form).
    try:
        # Importing mcp_server triggers the full ``@_tool`` decorator pass
        # which populates _TOOL_METADATA. fastmcp is optional, but the
        # metadata is populated *before* the fastmcp-presence check inside
        # ``_tool`` (per the comment at mcp_server.py:927) so this works
        # even on installs without the [mcp] extra.
        from roam.mcp_server import _TOOL_METADATA as _meta

        collected.update(_meta.keys())
    except Exception:
        pass

    # Source 2: AST scan of mcp_server.py — recover the Python ``def``
    # names that the symbols table will store. We resolve the source
    # file via the imported module's __file__ rather than walking the
    # filesystem so editable installs and out-of-tree checkouts both
    # work without configuration.
    try:
        import ast

        import roam.mcp_server as _mcp_mod  # noqa: F401  (module-import side effect)

        src_path = getattr(_mcp_mod, "__file__", None)
        if src_path:
            with open(src_path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=src_path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for deco in node.decorator_list:
                    # ``@_tool(name=...)`` is a Call whose .func is the
                    # bare Name "_tool". We only care about that shape;
                    # bare ``@_tool`` without parens isn't used in the
                    # codebase (the decorator signature requires kwargs).
                    if isinstance(deco, ast.Call):
                        target = deco.func
                        if isinstance(target, ast.Name) and target.id == "_tool":
                            collected.add(node.name)
                            break
                    elif isinstance(deco, ast.Name) and deco.id == "_tool":
                        collected.add(node.name)
                        break
    except Exception:
        # Bare except: file IO, ast parse, attribute errors — none of
        # these should ever break the dead command's readonly path.
        pass

    _MCP_TOOL_NAMES_CACHE = frozenset(collected)
    return _MCP_TOOL_NAMES_CACHE


def _reset_mcp_tool_names_cache() -> None:
    """Test hook — clear the cached MCP-tool name set."""
    global _MCP_TOOL_NAMES_CACHE
    _MCP_TOOL_NAMES_CACHE = None


def _is_mcp_tool_symbol(name: str, file_path: str) -> bool:
    """True if ``(name, file_path)`` names an MCP-tool wrapper.

    Anchored on BOTH the function name being in ``_TOOL_METADATA`` AND
    the file being ``mcp_server.py``. The two-axis check prevents a
    coincidental shadow elsewhere (e.g. a test fixture named ``roam_clean``)
    from being silently exempted.
    """
    if not name or not file_path:
        return False
    base = os.path.basename(file_path).lower()
    if base != "mcp_server.py":
        return False
    return name in _load_mcp_tool_names()


def _is_test_path(file_path):
    """Check if a file is a test file (discovered by pytest, not imported)."""
    return is_test_file(file_path)


# Scaffolding signal patterns — when a dead-export's docstring carries one of
# these, it's almost certainly intentional reference code (FoxPro port,
# spec implementation, planned-feature placeholder) and should not be
# proposed for deletion.
_SCAFFOLDING_BEHAVIOUR_ID_RE = re.compile(r"\b[A-Z]{2,5}-\d{1,5}\b")
_SCAFFOLDING_LEGACY_FILE_RE = re.compile(
    r"\b[A-Za-z0-9_]+\.(?:prg|scx|cbl|f|f90|f95|cob|copy|cls|trg|page|sql|asm)\b",
    re.IGNORECASE,
)
_SCAFFOLDING_LINE_REFERENCE_RE = re.compile(r"\b(?:lines?|L)\s*[:#]?\s*\d{1,5}(?:\s*[-–]\s*\d{1,5})?\b", re.IGNORECASE)
_SCAFFOLDING_SEE_LEGACY_RE = re.compile(r"\bsee\s+(?:legacy|original|spec|reference)[\s/:_-]", re.IGNORECASE)


def _scaffolding_signals(docstring: str | None) -> dict | None:
    """Return scaffolding evidence when the docstring cites legacy/spec.

    A heuristic for "intentionally preserved reference code". Returns the
    matched evidence dict (so callers can surface it as a reason), or
    ``None`` when no signals are present.
    """
    if not docstring:
        return None
    text = docstring
    behaviour_ids = _SCAFFOLDING_BEHAVIOUR_ID_RE.findall(text)
    legacy_files = _SCAFFOLDING_LEGACY_FILE_RE.findall(text)
    legacy_with_lines = legacy_files and bool(_SCAFFOLDING_LINE_REFERENCE_RE.search(text))
    see_legacy = bool(_SCAFFOLDING_SEE_LEGACY_RE.search(text))
    if not (behaviour_ids or legacy_with_lines or see_legacy):
        return None
    return {
        "behaviour_ids": sorted(set(behaviour_ids)),
        "legacy_files": sorted({f.lower() for f in legacy_files}) if legacy_with_lines else [],
        "see_legacy": see_legacy,
    }


def _dead_action(r, file_imported, tested=False):
    """Compute actionable verdict and confidence % for a dead symbol.

    Uses tiered confidence scoring (inspired by Vulture and Meta's dead
    code system, 2023):
      100% — unreachable code, unused imports, no dynamic usage possible
       90% — unused functions/classes with no string-based references
       80% — unused but in imported file (could be consumed externally)
       70% — API-prefix naming (get*, create*, etc.) or barrel files
       60% — entry-point/lifecycle hooks (frameworks may invoke implicitly)

    Returns (action_string, confidence_pct).
    """
    name = r["name"]
    name_lower = name.lower()
    base = os.path.basename(r["file_path"]).lower()
    name_no_ext = os.path.splitext(base)[0]
    try:
        kind = r["kind"]
    except (KeyError, IndexError):
        kind = ""

    # Test file symbols — discovered by pytest, never imported directly
    if _is_test_path(r["file_path"]):
        return "INTENTIONAL", 10

    # Scaffolding pattern — docstring cites a behaviour ID, legacy file
    # with line numbers, or "see legacy/spec" reference. These are
    # intentionally preserved reference symbols (planned features,
    # FoxPro/COBOL port placeholders) regardless of consumer count.
    try:
        docstring = r["docstring"]
    except (KeyError, IndexError):
        docstring = None
    if _scaffolding_signals(docstring):
        return "INTENTIONAL_SCAFFOLDING", 80

    # W157 — MCP tool wrappers registered via ``@_tool(name=...)`` in
    # ``mcp_server.py``. FastMCP invokes these at runtime through its tool
    # registry; no static call-graph edge ever lands on them. Without this
    # exemption, every wrapper looks SAFE-to-delete (W149 dogfood: 44/73
    # SAFE findings were MCP wrappers — silent demolition of the MCP
    # transport if an agent followed the verdict).
    #
    # Runs BEFORE the ``tested`` check because an MCP wrapper that is also
    # imported by a test (e.g. ``tests/test_mcp_server.py``) is still
    # primarily driven by FastMCP at runtime; reporting it as REVIEW would
    # understate the real consumer.
    if kind == "function" and _is_mcp_tool_symbol(name, r["file_path"]):
        return "INTENTIONAL", 10

    # Test-only public surface is not production-consumed, but deleting it
    # breaks the suite and may remove intentionally preserved API behavior.
    if tested:
        return "REVIEW", 70

    # CLI command functions — loaded dynamically via LazyGroup/importlib
    if base.startswith("cmd_") and kind == "function":
        return "INTENTIONAL", 20

    # ABC method overrides — called polymorphically, not by direct import
    if kind == "method" and name in _ABC_METHOD_NAMES:
        return "INTENTIONAL", 10

    # Entry point / lifecycle hooks (check original case for camelCase hooks)
    if name in _ENTRY_NAMES or name_lower in _ENTRY_NAMES:
        return "INTENTIONAL", 60

    # Python dunders — always intentional
    if name.startswith("__") and name.endswith("__"):
        return "INTENTIONAL", 60

    # File is an entry point and not imported — symbols here are likely intentional
    if not file_imported and name_no_ext in _ENTRY_FILE_BASES:
        return "INTENTIONAL", 60

    # API naming → review before deleting
    if any(name_lower.startswith(p) for p in _API_PREFIXES):
        return "REVIEW", 70

    # Barrel/index file → likely re-exported for public API
    if base.startswith("index.") or base == "__init__.py":
        return "REVIEW", 70

    # Imported file but symbol unused — could be externally consumed
    if file_imported:
        return "SAFE", 80

    # Private naming conventions (_, single underscore prefix) = higher confidence
    if name.startswith("_") and not name.startswith("__"):
        return "SAFE", 95

    # Functions/methods without callers — high confidence
    if kind in ("function", "method", "constructor"):
        return "SAFE", 90

    # Default: classes, variables, etc.
    return "SAFE", 90


# ---------------------------------------------------------------------------
# Dead cluster detection
# ---------------------------------------------------------------------------


def _find_dead_clusters(conn, dead_ids):
    """Find connected components of dead-only symbols.

    Given a set of dead symbol IDs, build a subgraph of edges where both
    endpoints are dead, then find connected components of size >= 2.
    """
    if not dead_ids:
        return []

    dead_set = set(dead_ids)

    # Edges where both source and target are dead — fetch by source, filter by target
    all_edges = batched_in(
        conn,
        "SELECT source_id, target_id FROM edges WHERE source_id IN ({ph})",
        list(dead_set),
    )
    edges = [e for e in all_edges if e["target_id"] in dead_set]

    # Build adjacency (undirected for component finding)
    adj = defaultdict(set)
    for e in edges:
        adj[e["source_id"]].add(e["target_id"])
        adj[e["target_id"]].add(e["source_id"])

    # BFS to find components
    visited = set()
    clusters = []
    for node in adj:
        if node in visited:
            continue
        component = set()
        queue = [node]
        while queue:
            n = queue.pop()
            if n in visited:
                continue
            visited.add(n)
            component.add(n)
            for nb in adj[n]:
                if nb not in visited:
                    queue.append(nb)
        if len(component) >= 2:
            clusters.append(component)

    clusters.sort(key=lambda c: -len(c))
    return clusters


# ---------------------------------------------------------------------------
# Extinction prediction
# ---------------------------------------------------------------------------


def _predict_extinction(conn, target_name):
    """Predict what becomes dead if symbol X is deleted.

    Algorithm:
    1. Find symbol X's ID
    2. Find all symbols that call/reference X (callers of X)
    3. For each caller: check if X is their ONLY callee. If so, they become orphaned.
    4. Recursively propagate: if removing X orphans Y, check Y's callers too.
    5. Return the full cascade.
    """
    from roam.commands.resolve import find_symbol

    sym = find_symbol(conn, target_name)
    if sym is None:
        return None, []

    target_id = sym["id"]

    # Pre-load the full forward + reverse adjacency in a single query so the
    # BFS becomes pure-Python dict lookup (was: 1 SELECT per BFS pop +
    # 1 batched_count per caller — quadratic on deep cascades).
    callees_of: dict = defaultdict(set)  # source_id -> {target_id, ...}
    callers_of: dict = defaultdict(set)  # target_id -> {source_id, ...}
    for r in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        s = r["source_id"]
        t = r["target_id"]
        callees_of[s].add(t)
        callers_of[t].add(s)

    # BFS cascade
    cascade = []
    removed = {target_id}
    queue = [target_id]
    orphan_ids: list = []  # for batched info lookup at the end

    while queue:
        current = queue.pop(0)
        for caller_id in callers_of.get(current, ()):
            if caller_id in removed:
                continue
            # Remaining callees after removing all currently-removed targets.
            remaining = sum(1 for t in callees_of.get(caller_id, ()) if t not in removed)
            if remaining == 0:
                # This caller has no remaining callees → orphaned
                removed.add(caller_id)
                queue.append(caller_id)
                orphan_ids.append(caller_id)

    # Single batched lookup for cascade names/locations (was: 1 SELECT per
    # orphaned caller).
    if orphan_ids:
        info_rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            orphan_ids,
        )
        info_by_id = {r["id"]: r for r in info_rows}
        # Preserve the orphan order from the BFS for stable output.
        for oid in orphan_ids:
            info = info_by_id.get(oid)
            if info:
                cascade.append(
                    {
                        "name": info["name"],
                        "kind": info["kind"],
                        "location": loc(info["file_path"], info["line_start"]),
                        "reason": "only callees removed",
                    }
                )

    return sym, cascade


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------


def _group_dead(dead_items, by):
    """Group dead items by directory or kind."""
    groups = defaultdict(list)
    for item in dead_items:
        if by == "directory":
            key = os.path.dirname(item["file_path"]).replace("\\", "/") or "."
        elif by == "kind":
            key = item["kind"]
        else:
            key = "all"
        groups[key].append(item)
    return sorted(groups.items(), key=lambda x: -len(x[1]))


def _dead_consumer_meta(conn, candidate_ids):
    """Return incoming-consumer metadata split by production vs test files."""
    meta = {
        sid: {
            "production_consumers": 0,
            "test_consumers": 0,
            "production_files": set(),
            "test_files": set(),
        }
        for sid in candidate_ids
    }
    if not candidate_ids:
        return meta

    incoming = batched_in(
        conn,
        "SELECT e.target_id, e.kind, src.name AS source_name, src.kind AS source_kind, "
        "src.line_start AS source_line, f.path AS source_file "
        "FROM edges e "
        "JOIN symbols src ON e.source_id = src.id "
        "JOIN files f ON src.file_id = f.id "
        "WHERE e.target_id IN ({ph})",
        list(candidate_ids),
    )
    seen = set()
    for row in incoming:
        key = (row["target_id"], row["source_name"], row["source_kind"], row["source_file"], row["kind"])
        if key in seen:
            continue
        seen.add(key)
        entry = meta.setdefault(
            row["target_id"],
            {
                "production_consumers": 0,
                "test_consumers": 0,
                "production_files": set(),
                "test_files": set(),
            },
        )
        if _is_test_path(row["source_file"]):
            entry["test_consumers"] += 1
            entry["test_files"].add(row["source_file"])
        else:
            entry["production_consumers"] += 1
            entry["production_files"].add(row["source_file"])
    return meta


def _scan_one_test_file_combined(args):
    """Worker: scan a test file with a combined alternation regex.

    Returns the set of names that appeared in the file. The combined regex
    is ~100x faster than per-name searches because it traverses the source
    text once.
    """
    project_root, path, combined_rx = args
    try:
        source = (project_root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path, set()
    # findall on a regex with a single capture group returns the captured
    # substrings — i.e. the names that matched.
    matched = set(combined_rx.findall(source))
    return path, matched


def _augment_test_text_consumers(conn, rows, consumer_meta):
    """Augment consumer metadata with exact-name mentions in test files.

    This covers JS/TS test modules whose imports/calls live at top level and
    therefore cannot produce symbol edges because the file has no extracted
    source symbol.

    Algorithmic optimisation: build ONE combined alternation regex
    ``\b(name1|name2|...)\b`` and scan each test file with a single pass,
    rather than N regexes × M files. That's a ~100x speedup over the naive
    per-name approach when there are hundreds of names.

    On top of that, the per-file scans are parallelised via
    ThreadPoolExecutor — disk reads release the GIL, so even though
    ``re.findall`` is GIL-bound the overlap with file I/O gives a further
    ~2-3x. ROAM_NO_PARALLEL forces the serial path (used by tests for
    output-stability comparisons).
    """
    if not rows:
        return

    by_name: dict[str, list] = defaultdict(list)
    for row in rows:
        by_name[row["name"]].append(row)
    if not by_name:
        return

    project_root = find_project_root()
    test_files = [f["path"] for f in conn.execute("SELECT path FROM files").fetchall() if _is_test_path(f["path"])]
    if not test_files:
        return

    # Build the combined alternation. We use a single capture group so
    # findall returns just the matched name. Sort names for determinism.
    name_to_row_ids = {name: [r["id"] for r in name_rows] for name, name_rows in by_name.items()}
    sorted_names = sorted(name_to_row_ids.keys())
    # Escape and join. The wrapping \b ... \b ensures whole-word match.
    combined_pattern = r"\b(" + "|".join(re.escape(n) for n in sorted_names) + r")\b"
    try:
        combined_rx = re.compile(combined_pattern)
    except re.error:
        # If the pattern is unparseable (shouldn't happen with escaped
        # names, but defensively…) fall back to no-op rather than crash.
        return

    use_parallel = not os.environ.get("ROAM_NO_PARALLEL") and len(test_files) >= 50

    per_file_hits: list[tuple[str, set]] = []
    parallel_ok = False
    if use_parallel:
        try:
            from concurrent.futures import ThreadPoolExecutor

            workers = max(1, min(os.cpu_count() or 4, 8))
            args_iter = ((project_root, path, combined_rx) for path in test_files)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for path, matched in ex.map(_scan_one_test_file_combined, args_iter):
                    if matched:
                        per_file_hits.append((path, matched))
            parallel_ok = True
        except Exception:
            per_file_hits = []

    if not parallel_ok:
        for path in test_files:
            path, matched = _scan_one_test_file_combined((project_root, path, combined_rx))
            if matched:
                per_file_hits.append((path, matched))

    # Reduction phase: apply matches to consumer_meta in main thread.
    for path, matched in per_file_hits:
        for name in matched:
            for row_id in name_to_row_ids.get(name, ()):
                entry = consumer_meta.setdefault(
                    row_id,
                    {
                        "production_consumers": 0,
                        "test_consumers": 0,
                        "production_files": set(),
                        "test_files": set(),
                    },
                )
                if path not in entry["test_files"]:
                    entry["test_consumers"] += 1
                    entry["test_files"].add(path)


_BARREL_BASENAMES = frozenset({"index.ts", "index.tsx", "index.js", "index.jsx", "index.mjs", "__init__.py"})


def _is_barrel_path(path: str | None) -> bool:
    """A file is a barrel when its basename is one of the canonical re-export
    points. Round 4 #37: dead's "file is imported by N places" reason
    used to lump barrel re-exporters with real consumers — those rarely
    indicate the symbol is actually exercised."""
    if not path:
        return False
    base = os.path.basename(path).lower()
    return base in _BARREL_BASENAMES


def _dead_file_import_meta(conn):
    """Return module import counts split by production / tests / barrels."""
    imported_any = set()
    imported_production = set()
    imported_consumer = set()  # production importers excluding barrels
    file_meta: dict[int, dict] = {}
    for row in conn.execute(
        "SELECT fe.target_file_id, f.path AS source_file FROM file_edges fe JOIN files f ON fe.source_file_id = f.id"
    ).fetchall():
        target_id = row["target_file_id"]
        imported_any.add(target_id)
        entry = file_meta.setdefault(
            target_id,
            {
                "module_path_importers": 0,
                "production_module_path_importers": 0,
                "test_module_path_importers": 0,
                "barrel_module_path_importers": 0,
                "consumer_module_path_importers": 0,
            },
        )
        entry["module_path_importers"] += 1
        is_test = _is_test_path(row["source_file"])
        is_barrel = _is_barrel_path(row["source_file"])
        if is_test:
            entry["test_module_path_importers"] += 1
        else:
            entry["production_module_path_importers"] += 1
            imported_production.add(target_id)
        if is_barrel:
            entry["barrel_module_path_importers"] += 1
        else:
            entry["consumer_module_path_importers"] += 1
            if not is_test:
                imported_consumer.add(target_id)
    return imported_any, imported_production, file_meta


def _referenced_sibling_counts(conn, file_ids):
    """Count referenced exported siblings per file, split by production/test."""
    if not file_ids:
        return {}
    exported_rows = batched_in(
        conn,
        "SELECT id, file_id FROM symbols WHERE file_id IN ({ph}) AND is_exported = 1",
        list(file_ids),
    )
    by_file = {}
    all_symbol_ids = []
    for row in exported_rows:
        by_file[row["id"]] = row["file_id"]
        all_symbol_ids.append(row["id"])
    if not all_symbol_ids:
        return {}

    meta = _dead_consumer_meta(conn, all_symbol_ids)
    counts = {
        fid: {
            "referenced_siblings": 0,
            "production_referenced_siblings": 0,
            "test_referenced_siblings": 0,
        }
        for fid in file_ids
    }
    for sid, fid in by_file.items():
        m = meta.get(sid, {})
        if (m.get("production_consumers") or 0) + (m.get("test_consumers") or 0) <= 0:
            continue
        counts[fid]["referenced_siblings"] += 1
        if m.get("production_consumers", 0) > 0:
            counts[fid]["production_referenced_siblings"] += 1
        if m.get("test_consumers", 0) > 0:
            counts[fid]["test_referenced_siblings"] += 1
    return counts


def _jsonable_dead_meta(meta):
    out = dict(meta)
    out["production_files"] = sorted(out.get("production_files", set()))
    out["test_files"] = sorted(out.get("test_files", set()))
    return out


def _dead_reason(r, consumer_meta, file_import_meta, sibling_meta):
    """Human-readable reason that separates module imports from symbol use."""
    cmeta = consumer_meta.get(r["id"], {})
    fmeta = file_import_meta.get(r["file_id"], {})
    smeta = sibling_meta.get(r["file_id"], {})
    test_consumers = cmeta.get("test_consumers", 0)
    if test_consumers:
        return f"no production consumers; used by {test_consumers} test consumer(s)"

    module_importers = fmeta.get("module_path_importers", 0)
    prod_module_importers = fmeta.get("production_module_path_importers", 0)
    barrel_importers = fmeta.get("barrel_module_path_importers", 0)
    consumer_importers = fmeta.get("consumer_module_path_importers", 0)
    siblings = smeta.get("production_referenced_siblings", 0)

    barrel_clause = (
        f", of which {barrel_importers} are barrel re-exports (index.ts/__init__.py)" if barrel_importers else ""
    )
    if module_importers and siblings:
        return (
            f"file is imported by {module_importers} place(s) "
            f"({prod_module_importers} production, {consumer_importers} real consumers"
            f"{barrel_clause}); this export has no production consumers "
            f"while {siblings} sibling export(s) are used"
        )
    if module_importers:
        return (
            f"file is imported by {module_importers} place(s) "
            f"({prod_module_importers} production, {consumer_importers} real consumers"
            f"{barrel_clause}); this export has no production consumers"
        )
    return "file has no module importers; may be an entry point or consumed by unparsed code"


# ---------------------------------------------------------------------------
# Core dead code analysis (shared between modes)
# ---------------------------------------------------------------------------


from roam.output.file_role_hints import is_excluded_path as _is_tooling_path


def _analyze_dead(conn):
    """Run the full dead code analysis.

    Returns (high, low, imported_files, consumer_meta, file_import_meta, sibling_meta).

    ``dead`` means "no production consumers" rather than "no consumers
    anywhere". Test-only consumers are preserved as metadata so output can
    distinguish deletion candidates from tested-but-unused public surface.
    """
    rows = conn.execute(
        "SELECT s.*, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.is_exported = 1 "
        "AND s.kind IN ('function', 'class', 'method') "
        "ORDER BY f.path, s.line_start"
    ).fetchall()
    # Exclude test files — their symbols are discovered by pytest, not imported
    rows = [r for r in rows if not _is_test_path(r["file_path"])]
    # Exclude tooling/CI/benchmarks/dev — same default-exclusion that
    # ``cmd_smells`` and ``cmd_fan`` apply.
    rows = [r for r in rows if not _is_tooling_path(r["file_path"])]
    if not rows:
        return [], [], set(), {}, {}, {}

    consumer_meta = _dead_consumer_meta(conn, [r["id"] for r in rows])
    rows = [r for r in rows if consumer_meta.get(r["id"], {}).get("production_consumers", 0) == 0]
    if not rows:
        return [], [], set(), consumer_meta, {}, {}
    _augment_test_text_consumers(conn, rows, consumer_meta)

    imported_files, imported_production_files, file_import_meta = _dead_file_import_meta(conn)

    # Filter transitively alive (barrel re-exports)
    importers_of = {}
    for fe in conn.execute(
        "SELECT fe.source_file_id, fe.target_file_id, f.path AS source_file "
        "FROM file_edges fe JOIN files f ON fe.source_file_id = f.id"
    ).fetchall():
        if _is_test_path(fe["source_file"]):
            continue
        importers_of.setdefault(fe["target_file_id"], set()).add(fe["source_file_id"])

    transitively_alive = set()
    for r in rows:
        fid = r["file_id"]
        if fid not in imported_production_files:
            continue
        downstream = set()
        frontier = {fid}
        for _ in range(3):
            next_hop = set()
            for f in frontier:
                for imp_fid in importers_of.get(f, set()):
                    if imp_fid not in downstream:
                        downstream.add(imp_fid)
                        next_hop.add(imp_fid)
            frontier = next_hop
            if not frontier:
                break
        if not downstream:
            continue
        alive = batched_in(
            conn,
            "SELECT 1 FROM edges e JOIN symbols s ON e.target_id = s.id "
            "WHERE s.name = ? AND s.file_id IN ({ph}) LIMIT 1",
            list(downstream),
            pre=[r["name"]],
        )
        if alive:
            transitively_alive.add(r["id"])

    rows = [r for r in rows if r["id"] not in transitively_alive]
    sibling_meta = _referenced_sibling_counts(conn, {r["file_id"] for r in rows})

    high = [r for r in rows if r["file_id"] in imported_files]
    low = [r for r in rows if r["file_id"] not in imported_files]
    return high, low, imported_files, consumer_meta, file_import_meta, sibling_meta


# ---------------------------------------------------------------------------
# Dead code aging, decay, and effort estimation
# ---------------------------------------------------------------------------


def _sym_loc(sym):
    """Return LOC for a symbol's line range."""
    line_start = sym["line_start"] or 1
    line_end = sym["line_end"] or line_start
    return max(1, line_end - line_start + 1)


def _blame_age_for_sym(sym, blame_entries, now):
    """Extract age data for a symbol from blame entries."""
    line_start = sym["line_start"] or 1
    line_end = sym["line_end"] or line_start

    relevant = blame_entries[line_start - 1 : line_end]
    if not relevant:
        relevant = blame_entries[:1] if blame_entries else []

    if not relevant:
        return 0, 0, ""

    timestamps = [e["timestamp"] for e in relevant if e["timestamp"] > 0]
    author_counts = defaultdict(int)
    for e in relevant:
        author_counts[e["author"]] += 1
    primary_author = max(author_counts, key=author_counts.get) if author_counts else ""

    oldest_ts = min(timestamps) if timestamps else now
    newest_ts = max(timestamps) if timestamps else now
    age_days = max(0, (now - oldest_ts) // 86400)
    last_modified_days = max(0, (now - newest_ts) // 86400)
    return age_days, last_modified_days, primary_author


def _file_level_age(conn, file_id, now):
    """Get file-level age data from git_file_changes as fallback."""
    oldest_ts, newest_ts, primary_author = now, now, ""
    if file_id is None:
        return 0, 0, ""

    ts_row = conn.execute(
        "SELECT MIN(gc.timestamp) as oldest, MAX(gc.timestamp) as newest "
        "FROM git_file_changes gfc "
        "JOIN git_commits gc ON gfc.commit_id = gc.id "
        "WHERE gfc.file_id = ?",
        (file_id,),
    ).fetchone()
    if ts_row and ts_row["oldest"]:
        oldest_ts = ts_row["oldest"]
        newest_ts = ts_row["newest"]

    author_row = conn.execute(
        "SELECT gc.author, COUNT(*) as cnt "
        "FROM git_file_changes gfc "
        "JOIN git_commits gc ON gfc.commit_id = gc.id "
        "WHERE gfc.file_id = ? "
        "GROUP BY gc.author ORDER BY cnt DESC LIMIT 1",
        (file_id,),
    ).fetchone()
    if author_row:
        primary_author = author_row["author"]

    age_days = max(0, (now - oldest_ts) // 86400)
    last_modified_days = max(0, (now - newest_ts) // 86400)
    return age_days, last_modified_days, primary_author


def _blame_one_file(project_root, file_path):
    """Worker shim: invoke get_blame_for_file with exception isolation.

    Returns (file_path, blame_entries-or-empty-list). Module-level so it
    threads cleanly. ``get_blame_for_file`` already runs ``git blame`` via
    subprocess, which releases the GIL during I/O.
    """
    try:
        from roam.index.git_stats import get_blame_for_file

        return file_path, get_blame_for_file(project_root, file_path)
    except Exception:
        return file_path, []


def _get_blame_ages(conn, dead_symbols):
    """Get age data for dead symbols by batching git blame per file.

    Returns dict mapping symbol_id to {age_days, last_modified_days, author,
    author_active, dead_loc}.

    Git blame is subprocess-I/O-bound (one ``git blame`` invocation per
    dead-symbol-bearing file). Parallelizing via ThreadPoolExecutor gives
    near-linear speedup because subprocess.run releases the GIL.
    Falls back to serial under ROAM_NO_PARALLEL.
    """
    now = int(_time.time())
    result = {}
    if not dead_symbols:
        return result

    active_authors = {
        r["author"]
        for r in conn.execute(
            "SELECT DISTINCT author FROM git_commits WHERE timestamp >= ?",
            (now - 90 * 86400,),
        ).fetchall()
    }

    by_file = defaultdict(list)
    for sym in dead_symbols:
        by_file[sym["file_path"]].append(sym)

    project_root = find_project_root()

    # Parallel git-blame phase: dispatch one git subprocess per file via
    # threads. We're I/O-bound on git child processes, so threads beat
    # processes (no pickle overhead, no Windows spawn).
    file_paths = list(by_file.keys())
    blame_results: dict[str, list] = {}

    use_parallel = (
        not os.environ.get("ROAM_NO_PARALLEL") and len(file_paths) >= 50  # break-even point: ~50 files
    )

    if use_parallel:
        try:
            from concurrent.futures import ThreadPoolExecutor

            # Cap at 8 — git itself is fast, and too many concurrent
            # subprocesses just trash the OS scheduler.
            workers = max(1, min(os.cpu_count() or 4, 8))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for fp, entries in ex.map(lambda p: _blame_one_file(project_root, p), file_paths):
                    blame_results[fp] = entries
        except Exception:
            blame_results = {}  # Fall through to serial

    if not blame_results:
        for file_path in file_paths:
            fp, entries = _blame_one_file(project_root, file_path)
            blame_results[fp] = entries

    # Reduction phase: blame data → per-symbol records. DB-touching
    # fallback (``_file_level_age``) runs in main thread on the existing
    # connection, so we don't have to share conns across threads.
    for file_path, syms in by_file.items():
        blame_entries = blame_results.get(file_path) or []

        if blame_entries:
            for sym in syms:
                age_days, last_modified_days, author = _blame_age_for_sym(sym, blame_entries, now)
                result[sym["id"]] = {
                    "age_days": age_days,
                    "last_modified_days": last_modified_days,
                    "author": author,
                    "author_active": author in active_authors,
                    "dead_loc": _sym_loc(sym),
                }
        else:
            file_id = syms[0]["file_id"] if syms else None
            age_days, last_modified_days, author = _file_level_age(conn, file_id, now)
            for sym in syms:
                result[sym["id"]] = {
                    "age_days": age_days,
                    "last_modified_days": last_modified_days,
                    "author": author,
                    "author_active": author in active_authors,
                    "dead_loc": _sym_loc(sym),
                }

    for sym in dead_symbols:
        if sym["id"] not in result:
            result[sym["id"]] = {
                "age_days": 0,
                "last_modified_days": 0,
                "author": "",
                "author_active": False,
                "dead_loc": _sym_loc(sym),
            }

    return result


def _decay_score(age_days, cognitive_complexity, cluster_size, importing_files, author_active, dead_loc):
    """0-100 decay score. Higher = more decayed, harder to remove.

    Scoring breakdown (max 100):
      age_points      (max 35): 7 * log2(1 + age_days / 90)
      cc_points       (max 25): cognitive_complexity * 1.5
      coupling_points (max 20): importing_files * 2 + cluster_size * 3
      size_points     (max 10): dead_loc / 20
      author_points   (max 10): 0 if author_active else 10
    """
    age_points = min(35, 7 * math.log2(1 + age_days / 90))
    cc_points = min(25, cognitive_complexity * 1.5)
    coupling_points = min(20, importing_files * 2 + cluster_size * 3)
    size_points = min(10, dead_loc / 20)
    author_points = 0 if author_active else 10
    return min(100, int(round(age_points + cc_points + coupling_points + size_points + author_points)))


def _estimate_removal_minutes(dead_loc, cognitive_complexity, importing_files, cluster_size, age_years, author_active):
    """Estimate minutes to remove a dead symbol.

    Factors:
      base             = dead_loc * 1.0
      complexity_factor = 1.0 + (cognitive_complexity / 20.0)
      coupling_factor   = 1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1))
      age_factor        = 1.0 + (0.1 * min(age_years, 10))
      author_factor     = 0.8 if author_active else 1.0
    """
    base = dead_loc * 1.0
    complexity_factor = 1.0 + (cognitive_complexity / 20.0)
    coupling_factor = 1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1))
    age_factor = 1.0 + (0.1 * min(age_years, 10))
    author_factor = 0.8 if author_active else 1.0
    return round(base * complexity_factor * coupling_factor * age_factor * author_factor, 1)


def _decay_tier(score):
    """Classify decay score into human-readable tier.

    Fresh (0-25), Stale (26-50), Decayed (51-75), Fossilized (76-100).
    """
    if score <= 25:
        return "Fresh"
    elif score <= 50:
        return "Stale"
    elif score <= 75:
        return "Decayed"
    else:
        return "Fossilized"


def _get_symbol_complexities(conn, symbol_ids):
    """Fetch cognitive_complexity from symbol_metrics for a set of symbol IDs.

    Returns dict mapping symbol_id to cognitive_complexity (float).
    """
    if not symbol_ids:
        return {}
    rows = batched_in(
        conn,
        "SELECT symbol_id, cognitive_complexity FROM symbol_metrics WHERE symbol_id IN ({ph})",
        list(symbol_ids),
    )
    return {r["symbol_id"]: r["cognitive_complexity"] or 0 for r in rows}


def _get_importing_file_counts(conn, file_ids):
    """Count how many files import each given file_id.

    Returns dict mapping file_id to count of importing files.
    """
    if not file_ids:
        return {}
    rows = batched_in(
        conn,
        "SELECT target_file_id, COUNT(*) as cnt FROM file_edges WHERE target_file_id IN ({ph}) GROUP BY target_file_id",
        list(file_ids),
    )
    return {r["target_file_id"]: r["cnt"] for r in rows}


def _build_cluster_membership(clusters):
    """Build a dict mapping symbol_id to cluster_size from cluster list.

    Each cluster is a set of symbol IDs. Returns {symbol_id: cluster_size}.
    """
    membership = {}
    for cluster_set in clusters:
        size = len(cluster_set)
        for sid in cluster_set:
            membership[sid] = size
    return membership


def _compute_extended_data(conn, all_items, clusters_for_aging):
    """Compute aging, decay, and effort data for dead symbols.

    Returns dict mapping symbol_id to {aging: {...}, effort: {...}, decay_score: int}.
    """
    if not all_items:
        return {}

    symbol_ids = {r["id"] for r in all_items}
    file_ids = {r["file_id"] for r in all_items}

    # Gather all needed data
    blame_ages = _get_blame_ages(conn, all_items)
    complexities = _get_symbol_complexities(conn, symbol_ids)
    importer_counts = _get_importing_file_counts(conn, file_ids)
    cluster_membership = _build_cluster_membership(clusters_for_aging)

    result = {}
    for r in all_items:
        sid = r["id"]
        aging = blame_ages.get(
            sid,
            {
                "age_days": 0,
                "last_modified_days": 0,
                "author": "",
                "author_active": False,
                "dead_loc": 1,
            },
        )
        cc = complexities.get(sid, 0)
        importing_files = importer_counts.get(r["file_id"], 0)
        cluster_size = cluster_membership.get(sid, 1)
        age_days = aging["age_days"]
        dead_loc = aging["dead_loc"]
        author_active = aging["author_active"]

        dscore = _decay_score(
            age_days,
            cc,
            cluster_size,
            importing_files,
            author_active,
            dead_loc,
        )
        age_years = age_days / 365.25
        removal_min = _estimate_removal_minutes(
            dead_loc,
            cc,
            importing_files,
            cluster_size,
            age_years,
            author_active,
        )
        complexity_factor = round(1.0 + (cc / 20.0), 2)
        coupling_factor = round(1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1)), 2)

        result[sid] = {
            "aging": {
                "age_days": age_days,
                "last_modified_days": aging["last_modified_days"],
                "author": aging["author"],
                "author_active": author_active,
                "dead_loc": dead_loc,
            },
            "effort": {
                "removal_minutes": removal_min,
                "complexity_factor": complexity_factor,
                "coupling_factor": coupling_factor,
            },
            "decay_score": dscore,
        }
    return result


def _extended_summary(extended_data):
    """Compute aggregate summary stats from extended data.

    Returns dict with total_dead_loc, total_effort_hours, median_age_days,
    and decay_distribution.
    """
    if not extended_data:
        return {
            "total_dead_loc": 0,
            "total_effort_hours": 0.0,
            "median_age_days": 0,
            "decay_distribution": {
                "fresh": 0,
                "stale": 0,
                "decayed": 0,
                "fossilized": 0,
            },
        }

    total_loc = sum(d["aging"]["dead_loc"] for d in extended_data.values())
    total_minutes = sum(d["effort"]["removal_minutes"] for d in extended_data.values())
    ages = [d["aging"]["age_days"] for d in extended_data.values()]
    scores = [d["decay_score"] for d in extended_data.values()]

    dist = {"fresh": 0, "stale": 0, "decayed": 0, "fossilized": 0}
    for s in scores:
        tier = _decay_tier(s).lower()
        dist[tier] = dist.get(tier, 0) + 1

    return {
        "total_dead_loc": total_loc,
        "total_effort_hours": round(total_minutes / 60.0, 1),
        "median_age_days": int(median(ages)) if ages else 0,
        "decay_distribution": dist,
    }


def _table_exists(conn, name: str) -> bool:
    """small probe used by the dataflow analyzer.

    Replaces the ``try: SELECT ... LIMIT 0; except: pass`` pattern
    repeated 3 times in the original ``_analyze_dataflow_dead``.
    """
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 0")
        return True
    except Exception:
        return False


def _read_caller_line(project_root, file_cache: dict, file_path: str, line_no: int) -> str | None:
    """return the caller's source line text or ``None`` on miss."""
    if file_path not in file_cache:
        try:
            file_cache[file_path] = (
                (project_root / file_path).read_text(encoding="utf-8", errors="replace").splitlines()
            )
        except Exception:
            file_cache[file_path] = []
    lines = file_cache.get(file_path, [])
    if 0 < line_no <= len(lines):
        return lines[line_no - 1].strip()
    return None


def _is_return_captured(line_text: str, func_name: str) -> bool:
    """`<var> = func(...)` captures, but `== func()` does not."""
    if func_name not in line_text:
        return False
    prefix = line_text.split(func_name)[0]
    return bool(re.search(r"[A-Za-z_]\w*\s*=(?!=)", prefix))


def _detect_unused_returns(conn, project_root) -> list[dict]:
    """A. functions whose return value every caller discards."""
    findings: list[dict] = []
    funcs_with_return = conn.execute(
        "SELECT s.id, s.name, COALESCE(s.qualified_name, s.name) AS qname, "
        "f.path AS file_path, s.line_start, sm.return_count "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbol_metrics sm ON s.id = sm.symbol_id "
        "WHERE sm.return_count > 0 "
        "  AND s.kind IN ('function', 'method')"
    ).fetchall()
    if not funcs_with_return:
        return findings

    from roam.db.connection import batched_in

    target_ids = [f["id"] for f in funcs_with_return]
    callers_by_target: dict[int, list] = {}
    # W512: edge-kind vocabulary lives in roam.db.edge_kinds. Pure call
    # edges only — discarded-return detection cares about call-site
    # semantics, not reference reads.
    _dead_call_kind_ph = ", ".join("?" for _ in CALL_EDGE_KINDS)
    for row in batched_in(
        conn,
        "SELECT e.target_id, e.source_id, e.line, s.name AS caller_name, "
        "f.path AS caller_file, s.line_start AS caller_start "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        f"WHERE e.target_id IN ({{ph}}) AND e.kind IN ({_dead_call_kind_ph})",
        target_ids,
        post=CALL_EDGE_KINDS,
    ):
        callers_by_target.setdefault(row["target_id"], []).append(row)

    file_cache: dict[str, list[str]] = {}
    for func in funcs_with_return:
        callers = callers_by_target.get(func["id"], [])
        if not callers:
            continue
        all_discard = True
        call_site_info: list[dict] = []
        for caller in callers:
            call_line = caller["line"]
            if not call_line:
                all_discard = False
                break
            line_text = _read_caller_line(project_root, file_cache, caller["caller_file"], call_line)
            if line_text is None:
                all_discard = False
                break
            if _is_return_captured(line_text, func["name"]):
                all_discard = False
                break
            call_site_info.append({"file": caller["caller_file"], "line": call_line, "caller": caller["caller_name"]})
        if all_discard and callers:
            findings.append(
                {
                    "type": "unused_return",
                    "symbol": func["qname"],
                    "file": func["file_path"],
                    "line": func["line_start"],
                    "reason": f"return value of {func['qname']} is discarded by all {len(callers)} caller(s)",
                    "confidence": 85,
                    "call_sites": call_site_info[:5],
                }
            )
    return findings


def _parse_param_names(sig: str) -> list[str]:
    """extract concrete parameter names from a signature string."""
    m = re.search(r"\(([^)]*)\)", sig or "")
    if not m:
        return []
    params_str = m.group(1).strip()
    if not params_str:
        return []
    out: list[str] = []
    for part in params_str.split(","):
        token = part.strip().split(":")[0].split("=")[0].strip()
        while token.startswith("*"):
            token = token[1:]
        if token and token not in ("self", "cls", "_"):
            out.append(token)
    return out


def _detect_dead_param_chains(conn) -> list[dict]:
    """B. parameters with no return / sink dataflow effect."""
    findings: list[dict] = []
    rows = conn.execute(
        "SELECT ts.symbol_id, ts.param_taints_return, ts.param_to_sink, "
        "s.name, COALESCE(s.qualified_name, s.name) AS qname, "
        "s.signature, f.path AS file_path, s.line_start "
        "FROM taint_summaries ts "
        "JOIN symbols s ON ts.symbol_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE ts.is_sanitizer = 0 "
        "  AND s.kind IN ('function', 'method')"
    ).fetchall()
    for row in rows:
        try:
            ptr = json.loads(row["param_taints_return"] or "{}")
            pts = json.loads(row["param_to_sink"] or "{}")
        except Exception:
            continue
        param_names = _parse_param_names(row["signature"])
        for idx, pname in enumerate(param_names):
            sidx = str(idx)
            if ptr.get(sidx, False) or bool(pts.get(sidx)):
                continue
            findings.append(
                {
                    "type": "dead_param_chain",
                    "symbol": row["qname"],
                    "file": row["file_path"],
                    "line": row["line_start"],
                    "variable": pname,
                    "reason": (
                        f"parameter '{pname}' of {row['qname']} has no dataflow effect (not returned, not used in sink)"
                    ),
                    "confidence": 75,
                    "call_sites": [],
                }
            )
    return findings


def _detect_side_effect_only(conn, unused_return_findings: list[dict]) -> list[dict]:
    """C. discard-return funcs whose only effects are pure/logging."""
    findings: list[dict] = []
    benign = {"pure", "logging"}
    for f in unused_return_findings:
        if f["type"] != "unused_return":
            continue
        sym_id_row = conn.execute(
            "SELECT id FROM symbols WHERE qualified_name = ? OR name = ? LIMIT 1",
            (f["symbol"], f["symbol"]),
        ).fetchone()
        if not sym_id_row:
            continue
        sym_id = sym_id_row["id"]
        effects = conn.execute(
            "SELECT DISTINCT effect_type FROM symbol_effects WHERE symbol_id = ?",
            (sym_id,),
        ).fetchall()
        effect_types = {e["effect_type"] for e in effects}
        if effect_types and effect_types <= benign:
            findings.append(
                {
                    "type": "side_effect_only",
                    "symbol": f["symbol"],
                    "file": f["file"],
                    "line": f["line"],
                    "reason": (
                        f"{f['symbol']} has only {'/'.join(sorted(effect_types))} effects "
                        "and return is always discarded"
                    ),
                    "confidence": 70,
                    "call_sites": f.get("call_sites", []),
                }
            )
    return findings


def _analyze_dataflow_dead(conn):
    """Analyze dataflow-based dead code patterns using taint summaries.

    Returns list of findings: ``[{type, symbol, file, line, reason,
    confidence, call_sites}]``.

    orchestrator only; per-pattern logic moved into
    ``_detect_unused_returns`` / ``_detect_dead_param_chains`` /
    ``_detect_side_effect_only``. Cognitive complexity dropped from
    160 to ~10.
    """
    if not _table_exists(conn, "taint_summaries"):
        return []
    has_effects = _table_exists(conn, "symbol_effects")
    has_metrics = _table_exists(conn, "symbol_metrics")
    project_root = find_project_root()

    findings: list[dict] = []
    if has_metrics:
        findings.extend(_detect_unused_returns(conn, project_root))
    findings.extend(_detect_dead_param_chains(conn))
    if has_effects:
        findings.extend(_detect_side_effect_only(conn, findings))

    findings.sort(key=lambda f: (-f["confidence"], f["file"], f.get("line") or 0))
    return findings


@roam_capability(
    name="dead",
    category="refactoring",
    summary="Show unreferenced exported symbols (dead code)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--all", "show_all", is_flag=True, help="Include low-confidence results")
@click.option("--by-directory", "by_directory", is_flag=True, help="Group dead symbols by parent directory")
@click.option("--by-kind", "by_kind", is_flag=True, help="Group dead symbols by symbol kind")
@click.option(
    "--summary",
    "summary_only",
    is_flag=True,
    help="Only show aggregate counts, no individual symbols",
)
@click.option(
    "--clusters",
    "show_clusters",
    is_flag=True,
    help="Detect dead subgraphs (groups of dead symbols referencing only each other)",
)
@click.option(
    "--extinction",
    "extinction_target",
    default=None,
    help="Predict what else becomes dead if you delete this symbol",
)
@click.option("--aging", "show_aging", is_flag=True, help="Add age/staleness columns to output")
@click.option("--effort", "show_effort", is_flag=True, help="Add effort estimation columns to output")
@click.option("--decay", "show_decay", is_flag=True, help="Show decay score and distribution")
@click.option("--sort-by-age", "sort_by_age", is_flag=True, help="Sort dead code oldest-first")
@click.option(
    "--sort-by-effort",
    "sort_by_effort",
    is_flag=True,
    help="Sort by removal effort (highest first)",
)
@click.option(
    "--sort-by-decay",
    "sort_by_decay",
    is_flag=True,
    help="Sort by decay score (most fossilized first)",
)
@click.option(
    "--dataflow",
    "--include-noisy-dataflow",
    "show_dataflow",
    is_flag=True,
    help=(
        "Include EXPERIMENTAL dataflow dead-code findings. High false-positive "
        "rate (sinks list is narrow — execSync, spawn, subprocess, and SQL "
        "flow are not yet recognised). Off by default; hidden behind this "
        "flag so the precise dead-export signal isn't drowned."
    ),
)
@click.option(
    "--reachable-only",
    "reachable_only",
    is_flag=True,
    default=False,
    help=(
        "Only show dead exports that ALSO fail roam oracle is-reachable-from-entry. "
        "The really-really-dead set — safe to delete without further investigation. "
        "Filters out scaffolding automatically since the oracle marks "
        "those as reason_class=unreachable_scaffolding. Round 4 feature A."
    ),
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each dead-export into the central findings registry "
        "(``roam findings list --detector dead``). Detector-specific "
        "output is unchanged; the registry rows are the denormalised "
        "cross-detector surface."
    ),
)
@click.pass_context
def dead(
    ctx,
    show_all,
    by_directory,
    by_kind,
    summary_only,
    show_clusters,
    extinction_target,
    show_aging,
    show_effort,
    show_decay,
    sort_by_age,
    sort_by_effort,
    sort_by_decay,
    show_dataflow,
    reachable_only,
    persist,
):
    """Show unreferenced exported symbols (dead code).

    Unlike ``flag-dead`` (which detects code gated behind feature flags
    using regex scanning), this command detects structurally unreferenced
    symbols via the call graph with aging, decay, and effort estimation.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # Any extended flag implies we need extended data
    need_extended = show_aging or show_effort or show_decay or sort_by_age or sort_by_effort or sort_by_decay
    # Decay distribution is now part of the default summary — the
    # fossilized/decayed/stale/fresh framing is a much better pitch than
    # a flat dead-export count, so we compute extended data unless the
    # caller explicitly asked for the summary-only fast path.
    compute_decay = not summary_only

    with open_db(readonly=not persist) as conn:
        # --- Extinction mode (separate flow) ---
        if extinction_target:
            sym, cascade = _predict_extinction(conn, extinction_target)
            if sym is None:
                # W1245 — Pattern-2 variant-D: surface resolution=unresolved
                # on the extinction-mode envelope so MCP consumers see the
                # same disclosure shape as the resolved branch below.
                unresolved_block = resolution_disclosure("unresolved", target=extinction_target)
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "dead",
                                summary={
                                    "verdict": f"Symbol not found: {extinction_target}",
                                    "error": f"Symbol not found: {extinction_target}",
                                    **unresolved_block,
                                },
                                mode="extinction",
                                **unresolved_block,
                            )
                        )
                    )
                else:
                    click.echo(f"Symbol not found: {extinction_target}")
                return

            # W1245 / W1249 — Pattern-2 variant-D: ``find_symbol`` stamps
            # ``_resolution_tier`` on the returned row so the extinction-
            # cascade envelope can distinguish a fully-resolved success
            # from a degraded fuzzy-match success that may have landed on a
            # different symbol than the agent intended.
            resolution_tier = sym.get("_resolution_tier", "symbol")
            resolved_target = sym["qualified_name"] or sym["name"]
            resolution_block = resolution_disclosure(resolution_tier, target=resolved_target)
            extinction_verdict = f"extinction cascade for {extinction_target}: {len(cascade)} symbol(s) would orphan"
            if resolution_tier == "fuzzy":
                extinction_verdict = (
                    f"{extinction_verdict} [fuzzy resolution -- target '{resolved_target}' may not be what you meant]"
                )

            if json_mode:
                # The ``target`` key is owned by ``resolution_block`` (set to
                # the resolved qualified/simple name). Spread the block last
                # so it overrides any prior ``target`` kwarg cleanly without
                # a duplicate-keyword conflict.
                envelope_kwargs = {
                    "mode": "extinction",
                    "extinction_cascade": cascade,
                }
                envelope_kwargs.update(resolution_block)
                click.echo(
                    to_json(
                        json_envelope(
                            "dead",
                            summary={
                                "verdict": extinction_verdict,
                                "extinction_cascade": len(cascade),
                                **resolution_block,
                            },
                            **envelope_kwargs,
                        )
                    )
                )
            else:
                click.echo(f"=== Extinction Cascade for: {extinction_target} ===\n")
                if cascade:
                    click.echo(f"Deleting {extinction_target} would orphan {len(cascade)} symbol(s):\n")
                    table_rows = []
                    for c in cascade:
                        table_rows.append(
                            [
                                c["name"],
                                abbrev_kind(c["kind"]),
                                c["location"],
                                c["reason"],
                            ]
                        )
                    click.echo(
                        format_table(
                            ["Name", "Kind", "Location", "Reason"],
                            table_rows,
                        )
                    )
                else:
                    click.echo("No additional symbols would become dead.")
            return

        # --- Standard dead code analysis ---
        high, low, imported_files, consumer_meta, file_import_meta, sibling_meta = _analyze_dead(conn)
        all_items = high + low

        # Round 4 feature A: --reachable-only intersects with the
        # is-reachable-from-entry oracle to surface the "really dead"
        # set. Scaffolding flagged via the round-2 heuristic is also
        # excluded because the oracle now classifies it.
        reachable_oracle_results: dict[str, dict] | None = None
        if reachable_only:
            from roam.commands.cmd_oracle import oracle_is_reachable_from_entry

            reachable_oracle_results = {}
            kept_items = []
            for item in all_items:
                name = item["qualified_name"] or item["name"]
                result = oracle_is_reachable_from_entry(conn, name, max_hops=10)
                reachable_oracle_results[name] = {
                    "reason_class": result.reason_class,
                    "reason": result.reason,
                }
                if result.value is False and result.reason_class == "unreachable_dead":
                    kept_items.append(item)
            all_items = kept_items
            high = [r for r in high if r in all_items]
            low = [r for r in low if r in all_items]

        unused_assignments = collect_dataflow_findings(
            conn,
            patterns=["dead_assignment"],
            max_matches=500,
        )

        # Dataflow-based dead code findings
        dataflow_dead = []
        if show_dataflow and not json_mode and not sarif_mode:
            # Surface the limitation up front in text mode only; JSON/SARIF
            # consumers learn this from the dedicated `dataflow_warning`
            # field in the envelope below.
            click.echo(
                "NOTE: --dataflow is experimental. Sinks recognised: "
                "function calls + return assignments. Not recognised: "
                "execSync/spawn/subprocess, SQL flow, parameter mutation, "
                "many DOM/event APIs. Expect false positives.",
                err=True,
            )
            dataflow_dead = _analyze_dataflow_dead(conn)

        if not all_items:
            if sarif_mode:
                from roam.output.sarif import dead_to_sarif, write_sarif

                sarif = dead_to_sarif([])
                click.echo(write_sarif(sarif))
                return
            if json_mode:
                summary = {
                    "verdict": "no dead exports",
                    "safe": 0,
                    "review": 0,
                    "intentional": 0,
                    "unused_assignments": len(unused_assignments),
                    "dataflow_dead": len(dataflow_dead),
                    # W331: also surface in the empty envelope so MCP
                    # consumers see the same shape on every call.
                    "dead_export_definition": DEAD_EXPORT_DEFINITION,
                    "action_definition": DEAD_EXPORT_ACTION_DEFINITION,
                }
                click.echo(
                    to_json(
                        json_envelope(
                            "dead",
                            summary=summary,
                            high_confidence=[],
                            low_confidence=[],
                            unused_assignments=unused_assignments[:10],
                            dataflow_dead=dataflow_dead,
                        )
                    )
                )
            else:
                click.echo("VERDICT: no dead exports — every exported symbol has at least one consumer")
                click.echo()
                click.echo("=== Unreferenced Exports (0) ===")
                click.echo("  (none -- all exports are referenced)")
                if unused_assignments:
                    click.echo(f"  Intra-procedural unused assignments: {len(unused_assignments)}")
                if show_dataflow and dataflow_dead:
                    click.echo(f"\n=== Dataflow Dead Code ({len(dataflow_dead)}) ===")
                    for finding in dataflow_dead[:20]:
                        click.echo(
                            f"  [{finding['type']}] {finding['confidence']}%  "
                            f"{finding['symbol']}  {loc(finding['file'], finding.get('line'))}"
                        )
                        click.echo(f"    {finding['reason']}")
                    if len(dataflow_dead) > 20:
                        click.echo(f"  (+{len(dataflow_dead) - 20} more -- use --json for full list)")
            return

        # Compute action verdicts
        all_dead = [
            (
                r,
                *_dead_action(
                    r,
                    r["file_id"] in imported_files,
                    consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0,
                ),
            )
            for r in all_items
        ]
        n_safe = sum(1 for _, a, _c in all_dead if a == "SAFE")
        n_review = sum(1 for _, a, _c in all_dead if a == "REVIEW")
        n_intent_strict = sum(1 for _, a, _c in all_dead if a == "INTENTIONAL")
        n_scaffolding = sum(1 for _, a, _c in all_dead if a == "INTENTIONAL_SCAFFOLDING")
        # Treat scaffolding as intentional in the rollup so existing
        # tooling (CI gates, dashboards) keeps working; surface the
        # split via the dedicated `scaffolding` field below.
        n_intent = n_intent_strict + n_scaffolding
        n_test_only = sum(1 for r in all_items if consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0)

        # --- W96: mirror into the central findings registry ---
        # Detector-specific output below is untouched; the registry rows
        # are the denormalised cross-detector surface (``roam findings``).
        # Wrapped so a pre-W89 DB (no ``findings`` table) silently no-ops
        # rather than crashing the standard dead command path.
        if persist:
            dead_records_for_emit = []
            for r, action, confidence_pct in all_dead:
                docstring = r["docstring"] if "docstring" in r.keys() else None
                scaffolding_evidence = _scaffolding_signals(docstring)
                tested = consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0
                dead_records_for_emit.append(
                    {
                        "symbol_id": r["id"],
                        "name": r["name"],
                        "kind": r["kind"],
                        "file_path": r["file_path"],
                        "line_start": r["line_start"],
                        "action": action,
                        "confidence_pct": confidence_pct,
                        "reason": _dead_reason(r, consumer_meta, file_import_meta, sibling_meta),
                        "tested": tested,
                        "scaffolding": scaffolding_evidence is not None,
                        "scaffolding_evidence": scaffolding_evidence or {},
                    }
                )
            try:
                _emit_dead_findings(conn, dead_records_for_emit)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # --- SARIF output ---
        if sarif_mode:
            from roam.output.sarif import dead_to_sarif, write_sarif

            dead_exports = []
            for r, action, confidence in all_dead:
                dead_exports.append(
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "location": loc(r["file_path"], r["line_start"]),
                        "action": action,
                    }
                )
            sarif = dead_to_sarif(dead_exports)
            click.echo(write_sarif(sarif))
            return

        # --- Cluster detection (also needed for extended data) ---
        clusters_data = []
        raw_clusters = []
        if show_clusters or need_extended:
            dead_ids = {r["id"] for r in all_items}
            raw_clusters = _find_dead_clusters(conn, dead_ids)
            if show_clusters:
                id_to_info = {}
                if raw_clusters:
                    all_cluster_ids = set()
                    for c in raw_clusters:
                        all_cluster_ids.update(c)
                    for r in batched_in(
                        conn,
                        "SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
                        "FROM symbols s JOIN files f ON s.file_id = f.id "
                        "WHERE s.id IN ({ph})",
                        list(all_cluster_ids),
                    ):
                        id_to_info[r["id"]] = r

                for cluster_set in raw_clusters:
                    syms = []
                    for sid in sorted(cluster_set):
                        info = id_to_info.get(sid)
                        if info:
                            syms.append(
                                {
                                    "name": info["name"],
                                    "kind": info["kind"],
                                    "location": loc(info["file_path"], info["line_start"]),
                                }
                            )
                    clusters_data.append({"size": len(cluster_set), "symbols": syms})

        # --- Extended data (aging / effort / decay) ---
        extended_data = {}
        ext_summary = {}
        if need_extended or compute_decay:
            extended_data = _compute_extended_data(conn, all_items, raw_clusters)
            ext_summary = _extended_summary(extended_data)

        # --- Sorting by extended fields ---
        if sort_by_age and extended_data:
            all_items = sorted(
                all_items,
                key=lambda r: extended_data.get(r["id"], {}).get("aging", {}).get("age_days", 0),
                reverse=True,
            )
            high = [r for r in all_items if r["file_id"] in imported_files]
            low = [r for r in all_items if r["file_id"] not in imported_files]
        elif sort_by_effort and extended_data:
            all_items = sorted(
                all_items,
                key=lambda r: extended_data.get(r["id"], {}).get("effort", {}).get("removal_minutes", 0),
                reverse=True,
            )
            high = [r for r in all_items if r["file_id"] in imported_files]
            low = [r for r in all_items if r["file_id"] not in imported_files]
        elif sort_by_decay and extended_data:
            all_items = sorted(
                all_items,
                key=lambda r: extended_data.get(r["id"], {}).get("decay_score", 0),
                reverse=True,
            )
            high = [r for r in all_items if r["file_id"] in imported_files]
            low = [r for r in all_items if r["file_id"] not in imported_files]

        # --- Grouping ---
        group_by = None
        groups_data = []
        if by_directory:
            group_by = "directory"
        elif by_kind:
            group_by = "kind"

        if group_by:
            grouped = _group_dead(all_items, group_by)
            for key, items in grouped:
                verdicts = [
                    _dead_action(
                        r,
                        r["file_id"] in imported_files,
                        consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0,
                    )[0]
                    for r in items
                ]
                affected_files = {r["file_path"] for r in items}
                dead_per_file = round(len(items) / max(len(affected_files), 1), 2)
                groups_data.append(
                    {
                        "key": key,
                        "count": len(items),
                        "files": len(affected_files),
                        "dead_per_file": dead_per_file,
                        "safe": sum(1 for v in verdicts if v == "SAFE"),
                        "review": sum(1 for v in verdicts if v == "REVIEW"),
                        "intentional": sum(1 for v in verdicts if v in ("INTENTIONAL", "INTENTIONAL_SCAFFOLDING")),
                        "scaffolding": sum(1 for v in verdicts if v == "INTENTIONAL_SCAFFOLDING"),
                    }
                )

        # --- JSON output ---
        if json_mode:

            def _build_sym_dict(r, file_imported):
                cmeta = _jsonable_dead_meta(consumer_meta.get(r["id"], {}))
                fmeta = file_import_meta.get(r["file_id"], {})
                smeta = sibling_meta.get(r["file_id"], {})
                tested = cmeta.get("test_consumers", 0) > 0
                action, confidence = _dead_action(r, file_imported, tested)
                scaffolding_evidence = _scaffolding_signals(r["docstring"] if "docstring" in r.keys() else None)
                d = {
                    "name": r["name"],
                    "kind": r["kind"],
                    "location": loc(r["file_path"], r["line_start"]),
                    "action": action,
                    "confidence": confidence,
                    "reason": _dead_reason(r, consumer_meta, file_import_meta, sibling_meta),
                    "tested": tested,
                    "scaffolding": scaffolding_evidence is not None,
                    "scaffolding_evidence": scaffolding_evidence or {},
                    "production_consumers": cmeta.get("production_consumers", 0),
                    "test_consumers": cmeta.get("test_consumers", 0),
                    "named_symbol_consumers": cmeta.get("production_consumers", 0) + cmeta.get("test_consumers", 0),
                    "module_path_importers": fmeta.get("module_path_importers", 0),
                    "production_module_path_importers": fmeta.get("production_module_path_importers", 0),
                    "test_module_path_importers": fmeta.get("test_module_path_importers", 0),
                    "barrel_module_path_importers": fmeta.get("barrel_module_path_importers", 0),
                    "consumer_module_path_importers": fmeta.get("consumer_module_path_importers", 0),
                    "referenced_sibling_exports": smeta.get("referenced_siblings", 0),
                    "production_files": cmeta.get("production_files", []),
                    "test_files": cmeta.get("test_files", []),
                }
                if need_extended and r["id"] in extended_data:
                    ext = extended_data[r["id"]]
                    d["aging"] = ext["aging"]
                    d["effort"] = ext["effort"]
                    d["decay_score"] = ext["decay_score"]
                return d

            total = n_safe + n_review + n_intent
            verdict = (
                f"{total} dead export(s): {n_safe} safe, {n_review} review, {n_intent} intentional"
                if total
                else "no dead exports"
            )
            summary = {
                "verdict": verdict,
                "safe": n_safe,
                "review": n_review,
                "intentional": n_intent,
                "scaffolding": n_scaffolding,
                "test_only": n_test_only,
                "unused_assignments": len(unused_assignments),
                "dataflow_dead": len(dataflow_dead),
                # W331: spell out what "dead" actually means (no inbound
                # edges) and what the SAFE / REVIEW / INTENTIONAL action
                # labels are derived from, so consumers don't conflate
                # them with unreachable-from-entry or unused-assignments.
                "dead_export_definition": DEAD_EXPORT_DEFINITION,
                "action_definition": DEAD_EXPORT_ACTION_DEFINITION,
            }
            if show_dataflow:
                summary["dataflow_warning"] = (
                    "experimental — narrow sink coverage (no execSync/spawn/"
                    "subprocess/SQL flow/parameter mutation); expect false positives"
                )
            if ext_summary:
                summary.update(ext_summary)

            _next_steps = suggest_next_steps(
                "dead",
                {
                    "safe": n_safe,
                    "review": n_review,
                },
            )
            # R22: wrap each dead-export symbol in {value, confidence, reason}.
            # Consumers that previously read high_confidence[i]["name"] must
            # now read high_confidence[i]["value"]["name"] plus
            # high_confidence[i]["confidence"] (the R22 high/medium/low
            # label) and high_confidence[i]["reason"]. The original
            # numeric confidence-percent lives at high_confidence[i]
            # ["value"]["confidence"] for backwards compatibility.
            high_dicts = [_build_sym_dict(r, True) for r in high]
            low_dicts = [_build_sym_dict(r, False) for r in low]
            high_triples = wrap_findings(high_dicts, classifier=_dead_classify)
            low_triples = wrap_findings(low_dicts, classifier=_dead_classify)
            distribution = confidence_distribution(high_triples + low_triples)
            summary["findings_confidence_distribution"] = distribution
            if "verdict" in summary:
                summary["verdict"] = verdict_with_high_count(summary["verdict"], distribution)
            envelope = json_envelope(
                "dead",
                summary=summary,
                budget=token_budget,
                high_confidence=high_triples,
                low_confidence=low_triples,
                unused_assignments=(unused_assignments if detail else unused_assignments[:10]),
                dataflow_dead=dataflow_dead,
                next_steps=_next_steps,
            )
            if group_by:
                envelope["grouping"] = group_by
                envelope["groups"] = groups_data
            if show_clusters:
                envelope["dead_clusters"] = clusters_data
            if not detail:
                envelope = strip_list_payloads(envelope)
            click.echo(to_json(envelope))
            return

        # --- Text: summary-only mode (also used by --detail-less default) ---
        if summary_only or not detail:
            # — verdict-first so the bottom line is on
            # the first line. Severity proxy: any "safe" finding is a
            # delete-now candidate; "review" requires triage; pure
            # "intentional" is a clean signal.
            if len(all_items) == 0:
                verdict = "no dead exports — the surface is tight"
            elif n_safe > 0:
                verdict = (
                    f"{len(all_items)} dead export(s) — "
                    f"{n_safe} safe to delete, {n_review} review, {n_intent} intentional"
                )
            elif n_review > 0:
                verdict = (
                    f"{len(all_items)} dead export(s) — all need review ({n_review} review, {n_intent} intentional)"
                )
            else:
                verdict = f"{len(all_items)} dead export(s) — all intentional scaffolding"
            click.echo(f"VERDICT: {verdict}")
            click.echo()
            click.echo(f"Dead exports: {len(all_items)} ({n_safe} safe, {n_review} review, {n_intent} intentional)")
            if unused_assignments:
                click.echo(f"Intra-procedural unused assignments: {len(unused_assignments)}")
            # Show top 5 high-confidence dead symbols as a preview
            if not summary_only and high:
                click.echo("Top dead symbols (high confidence):")
                for r in high[:5]:
                    tested = consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0
                    action, confidence = _dead_action(r, True, tested)
                    click.echo(
                        f"  {action} {confidence}%  {r['name']}  {abbrev_kind(r['kind'])}  {loc(r['file_path'], r['line_start'])}"
                    )
                if len(high) > 5:
                    click.echo(f"  (+{len(high) - 5} more — run `roam --detail dead` for the full list)")
            if ext_summary:
                click.echo(f"  Total dead LOC: {ext_summary['total_dead_loc']}")
                click.echo(f"  Median age: {ext_summary['median_age_days']} days")
                if need_extended:
                    click.echo(f"  Total removal effort: {ext_summary['total_effort_hours']} hours")
                dist = ext_summary["decay_distribution"]
                click.echo(
                    f"  Decay: {dist['fresh']} fresh, {dist['stale']} stale, "
                    f"{dist['decayed']} decayed, {dist['fossilized']} fossilized"
                )
            if group_by and groups_data:
                click.echo(f"\nBy {group_by}:")
                for g in groups_data[:20]:
                    click.echo(
                        f"  {g['key']:<50s}  {g['count']:>3d}  "
                        f"({g['files']} files, {g['dead_per_file']} dead/file; "
                        f"safe={g['safe']}, review={g['review']})"
                    )
            if show_clusters and clusters_data:
                click.echo(f"\nDead clusters: {len(clusters_data)}")
                for i, cl in enumerate(clusters_data[:10], 1):
                    names = ", ".join(s["name"] for s in cl["symbols"][:5])
                    more = f" +{cl['size'] - 5}" if cl["size"] > 5 else ""
                    click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
            if show_dataflow and dataflow_dead:
                click.echo(f"\n=== Dataflow Dead Code ({len(dataflow_dead)}) ===")
                for finding in dataflow_dead[:20]:
                    click.echo(
                        f"  [{finding['type']}] {finding['confidence']}%  "
                        f"{finding['symbol']}  {loc(finding['file'], finding.get('line'))}"
                    )
                    click.echo(f"    {finding['reason']}")
                if len(dataflow_dead) > 20:
                    click.echo(f"  (+{len(dataflow_dead) - 20} more -- use --json for full list)")
            return

        # --- Text: grouped mode ---
        if group_by and groups_data:
            click.echo(f"=== Unreferenced Exports by {group_by} ({len(all_items)} total) ===")
            scaffolding_note = f", {n_scaffolding} scaffolding" if n_scaffolding else ""
            click.echo(
                f"  Actions: {n_safe} safe to delete, {n_review} need review, "
                f"{n_intent} likely intentional{scaffolding_note}\n"
            )
            table_rows = []
            for g in groups_data:
                row = [
                    g["key"],
                    str(g["count"]),
                    str(g["files"]),
                    str(g["dead_per_file"]),
                    str(g["safe"]),
                    str(g["review"]),
                    str(g["intentional"]),
                ]
                if n_scaffolding:
                    row.append(str(g.get("scaffolding", 0)))
                table_rows.append(row)
            headers = [group_by.title(), "Total", "Files", "Dead/File", "Safe", "Review", "Intentional"]
            if n_scaffolding:
                headers.append("Scaffolding")
            click.echo(
                format_table(
                    headers,
                    table_rows,
                    budget=30,
                )
            )
            if show_clusters and clusters_data:
                click.echo(f"\n=== Dead Clusters ({len(clusters_data)}) ===")
                for i, cl in enumerate(clusters_data[:10], 1):
                    names = ", ".join(s["name"] for s in cl["symbols"][:6])
                    more = f" +{cl['size'] - 6}" if cl["size"] > 6 else ""
                    click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
            if show_dataflow and dataflow_dead:
                click.echo(f"\n=== Dataflow Dead Code ({len(dataflow_dead)}) ===")
                for finding in dataflow_dead[:20]:
                    click.echo(
                        f"  [{finding['type']}] {finding['confidence']}%  "
                        f"{finding['symbol']}  {loc(finding['file'], finding.get('line'))}"
                    )
                    click.echo(f"    {finding['reason']}")
                if len(dataflow_dead) > 20:
                    click.echo(f"  (+{len(dataflow_dead) - 20} more -- use --json for full list)")
            return

        # --- Text: standard output ---
        click.echo(f"=== Unreferenced Exports ({len(high)} high confidence, {len(low)} low) ===")
        click.echo(f"  Actions: {n_safe} safe to delete, {n_review} need review, {n_intent} likely intentional")
        if unused_assignments:
            click.echo(f"  Intra-procedural unused assignments: {len(unused_assignments)}")

        # Always surface the decay framing (default summary), with effort
        # added when --aging / --effort / --decay are explicitly requested.
        if ext_summary:
            line = f"  Total dead LOC: {ext_summary['total_dead_loc']}  Median age: {ext_summary['median_age_days']}d"
            if need_extended:
                line += f"  Removal effort: {ext_summary['total_effort_hours']}h"
            click.echo(line)
            dist = ext_summary["decay_distribution"]
            click.echo(
                f"  Decay: {dist['fresh']} fresh, {dist['stale']} stale, "
                f"{dist['decayed']} decayed, {dist['fossilized']} fossilized"
            )
        click.echo()

        # Build imported-by lookup for high-confidence results
        if high:
            click.echo(f"-- High confidence ({len(high)}) --")
            click.echo("(file is imported; this export has no production consumers)")

            # Build table headers and rows based on active flags
            headers = ["Action", "Name", "Kind", "Location", "Reason"]
            if show_aging:
                headers.extend(["Age(d)", "LastMod(d)", "Author"])
            if show_effort:
                headers.extend(["LOC", "Effort(m)"])
            if show_decay:
                headers.extend(["Decay", "Tier"])

            table_rows = []
            for r in high:
                tested = consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0
                action, confidence = _dead_action(r, True, tested)
                row = [
                    f"{action} {confidence}%",
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                    _dead_reason(r, consumer_meta, file_import_meta, sibling_meta),
                ]
                if need_extended:
                    ext = extended_data.get(r["id"], {})
                    aging = ext.get("aging", {})
                    effort = ext.get("effort", {})
                    dscore = ext.get("decay_score", 0)
                    if show_aging:
                        row.extend(
                            [
                                str(aging.get("age_days", 0)),
                                str(aging.get("last_modified_days", 0)),
                                aging.get("author", "")[:20],
                            ]
                        )
                    if show_effort:
                        row.extend(
                            [
                                str(aging.get("dead_loc", 0)),
                                str(effort.get("removal_minutes", 0)),
                            ]
                        )
                    if show_decay:
                        row.extend(
                            [
                                str(dscore),
                                _decay_tier(dscore),
                            ]
                        )
                table_rows.append(row)
            click.echo(format_table(headers, table_rows, budget=50))

        if show_all and low:
            click.echo(f"\n-- Low confidence ({len(low)}) --")
            click.echo("(file has no importers — may be entry point or used by unparsed files)")

            headers = ["Action", "Name", "Kind", "Location"]
            if show_aging:
                headers.extend(["Age(d)", "LastMod(d)", "Author"])
            if show_effort:
                headers.extend(["LOC", "Effort(m)"])
            if show_decay:
                headers.extend(["Decay", "Tier"])

            table_rows = []
            for r in low:
                tested = consumer_meta.get(r["id"], {}).get("test_consumers", 0) > 0
                action, confidence = _dead_action(r, False, tested)
                row = [
                    f"{action} {confidence}%",
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                ]
                if need_extended:
                    ext = extended_data.get(r["id"], {})
                    aging = ext.get("aging", {})
                    effort = ext.get("effort", {})
                    dscore = ext.get("decay_score", 0)
                    if show_aging:
                        row.extend(
                            [
                                str(aging.get("age_days", 0)),
                                str(aging.get("last_modified_days", 0)),
                                aging.get("author", "")[:20],
                            ]
                        )
                    if show_effort:
                        row.extend(
                            [
                                str(aging.get("dead_loc", 0)),
                                str(effort.get("removal_minutes", 0)),
                            ]
                        )
                    if show_decay:
                        row.extend(
                            [
                                str(dscore),
                                _decay_tier(dscore),
                            ]
                        )
                table_rows.append(row)
            click.echo(format_table(headers, table_rows, budget=50))
        elif low:
            click.echo(f"\n({len(low)} low-confidence results hidden — use --all to show)")

        # Dead clusters
        if show_clusters and clusters_data:
            click.echo(f"\n=== Dead Clusters ({len(clusters_data)}) ===")
            click.echo("(groups of dead symbols that only reference each other)")
            for i, cl in enumerate(clusters_data[:10], 1):
                names = ", ".join(s["name"] for s in cl["symbols"][:6])
                more = f" +{cl['size'] - 6}" if cl["size"] > 6 else ""
                click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
                for s in cl["symbols"][:6]:
                    click.echo(f"    {abbrev_kind(s['kind'])}  {s['name']}  {s['location']}")
            if len(clusters_data) > 10:
                click.echo(f"  (+{len(clusters_data) - 10} more clusters)")

        # Dataflow-based dead code
        if show_dataflow and dataflow_dead:
            click.echo(f"\n=== Dataflow Dead Code ({len(dataflow_dead)}) ===")
            for finding in dataflow_dead[:20]:
                click.echo(
                    f"  [{finding['type']}] {finding['confidence']}%  "
                    f"{finding['symbol']}  {loc(finding['file'], finding.get('line'))}"
                )
                click.echo(f"    {finding['reason']}")
            if len(dataflow_dead) > 20:
                click.echo(f"  (+{len(dataflow_dead) - 20} more -- use --json for full list)")

        # Check for files with no extracted symbols
        unparsed = conn.execute(
            "SELECT COUNT(*) FROM files f WHERE NOT EXISTS (SELECT 1 FROM symbols s WHERE s.file_id = f.id)"
        ).fetchone()[0]
        if unparsed:
            click.echo(f"\nNote: {unparsed} files had no symbols extracted (may cause false positives)")

        _next_steps = suggest_next_steps(
            "dead",
            {
                "safe": n_safe,
                "review": n_review,
            },
        )
        _ns_text = format_next_steps_text(_next_steps)
        if _ns_text:
            click.echo(_ns_text)
