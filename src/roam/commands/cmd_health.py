"""Detect and report code health issues.

Baseline-diff mode (``--baseline <ref>``)
-----------------------------------------

When ``--baseline <ref>`` is supplied, ``roam health`` reports DELTAS against
a previously-stored snapshot instead of the absolute set of findings. The
delta surface is what most users actually care about: "what is new since the
last green run, what got fixed, what regressed."

``<ref>`` accepts three forms:

* a git ref (``main``, ``v12.0``, a SHA prefix) — compare against the most
  recent snapshot recorded with a matching ``git_branch`` or ``git_commit``;
* ``last`` — compare against the most recent snapshot on file regardless of
  ref (useful for "did I make things worse since I last saved?");
* ``auto`` — compare against the most recent snapshot whose ``git_branch``
  matches the project's main branch (``main`` or ``master``). Sensible CI
  default.

Verdict semantics in baseline mode:

* ``OK`` — no new findings and no score regression.
* ``REVIEW`` — at least one new high-severity finding, even if the score
  did not move.
* ``BAD`` — composite ``health_score`` regressed against the baseline.

If no baseline snapshot can be located for ``<ref>`` the command prints a
friendly explanation, marks the run as ``DEGRADED`` (``summary.reason =
"no_baseline_snapshot"``), and exits cleanly. Snapshots are populated by
``roam trends --save`` (typically run in CI on the main branch).
"""

from __future__ import annotations

import hashlib
import json as _json
import math
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index
from roam.coverage_reports import imported_coverage_overview
from roam.db.connection import batched_in, open_db
from roam.db.queries import TOP_BY_BETWEENNESS, TOP_BY_DEGREE
from roam.graph.builder import build_symbol_graph
from roam.graph.cycles import (
    algebraic_connectivity,
    find_cycles,
    find_weakest_edge,
    format_cycles,
    mark_actionable_cycles,
    propagation_cost,
)
from roam.graph.layers import detect_layers, find_violations
from roam.output.formatter import (
    WarningsOut,
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    strip_list_payloads,
    to_json,
)
from roam.output.framework_filter import FRAMEWORK_PRIMITIVE_NAMES as _FRAMEWORK_NAMES
from roam.output.metric_definitions import (
    HEALTH_SCORE_DEFINITION,
    TANGLE_RATIO_DEFINITION,
)
from roam.quality.cycles import definition as cycles_definition
from roam.quality.god_components import definition as god_components_definition

# W151 (W93 follow-up): health is the fifth detector migrating onto the
# central findings registry (after ``clones`` in W95, ``dead`` in W99,
# ``complexity`` in W102, and ``smells`` in W109). It continues to
# compute its 4 architecture-level finding arrays (cycles, god
# components, bottlenecks, layer violations) in-line and ALSO emits one
# row per finding to the ``findings`` table when invoked with
# ``--persist``. Bump this when the predicate / claim shape of any of
# the four kinds changes meaningfully.
HEALTH_DETECTOR_VERSION: str = "1.0.0"


# W151 — per-kind confidence tier mapping.
#
# health emits four arch-level finding kinds; each is mapped to a tier
# that reflects the evidence class used:
#
# * ``arch.cycle`` — Tarjan SCC over the call graph, fully deterministic
#   for a fixed DB state → ``static_analysis``.
# * ``arch.god_component`` — degree threshold (in_degree + out_degree
#   > 20) from ``graph_metrics``; uses the canonical helper in
#   ``roam.quality.god_components`` for shared severity bands →
#   ``static_analysis``.
# * ``arch.bottleneck`` — betweenness-percentile thresholds (p70 / p90)
#   on ``graph_metrics.betweenness``; graph-backed but the band split
#   is heuristic-flavoured → ``structural``.
# * ``arch.layer_violation`` — topological-layer cross-edge from
#   ``detect_layers`` + ``find_violations``; deterministic edge-level
#   check → ``static_analysis``.
_HEALTH_KIND_TO_CONFIDENCE: dict[str, str] = {
    "arch.cycle": "static_analysis",
    "arch.god_component": "static_analysis",
    "arch.bottleneck": "structural",
    "arch.layer_violation": "static_analysis",
}


# ---------------------------------------------------------------------------
# W718 — canonical lowercase severity vocabulary.
# ---------------------------------------------------------------------------
# Pre-W718 the health command emitted UPPER-cased severity labels
# (``CRITICAL`` / ``WARNING`` / ``INFO``) across every surface: JSON
# envelope (``summary.severity``, per-issue ``severity`` fields),
# findings-registry rows, SARIF input, and text output. Out of
# vocabulary with the rest of roam — agents reading the envelope across
# commands would get mixed casing for the same concept (W547 canonical
# vocab is lowercase). W718 makes the lowercase form the only spelling
# that ever appears in code or output; legacy UPPER-cased inputs (from
# baseline snapshots stored before W718) are normalised at read time
# via :func:`_normalise_health_severity`.
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


def _normalise_health_severity(sev: str | None) -> str:
    """Canonicalise a health severity label to lowercase (W718).

    Returns one of ``"critical"`` / ``"warning"`` / ``"info"`` for
    known labels (case-insensitive), and ``"info"`` as the CI-safety
    floor for unknown / None inputs (the W531 lesson: a typo'd label
    must NOT promote a finding into a CI-failing rank).
    """
    if not sev:
        return INFO
    canon = str(sev).strip().lower()
    if canon in {CRITICAL, WARNING, INFO}:
        return canon
    return INFO


# ---- Location-aware utility detection ----

_UTILITY_PATH_PATTERNS = (
    "composables/",
    "utils/",
    "services/",
    "lib/",
    "helpers/",
    "shared/",
    "config/",
    "core/",
    "hooks/",
    "stores/",
    "output/",
    "db/",
    "common/",
    "internal/",
    "infra/",
    # infrastructure hubs that are EXPECTED to have high
    # fan-in. Without these patterns the health-score classifier
    # mislabels architectural roots (Click root group, MCP dispatch,
    # graph builder, file-role classifier) as actionable refactor
    # targets, which they are not.
    "graph/",
    "mcp_extras/",
    "languages/",
)

_UTILITY_FILE_PATTERNS = (
    "resolve.py",
    "helpers.py",
    "common.py",
    "base.py",
    # single-file architectural hubs. Same reasoning as
    # ``_UTILITY_PATH_PATTERNS`` additions above.
    "cli.py",
    "mcp_server.py",
    "file_roles.py",
)

# Paths that are NOT production code — treat as expected utilities
_NON_PRODUCTION_PATH_PATTERNS = (
    "tests/",
    "test/",
    "__tests__/",
    "spec/",
    "dev/",
    "scripts/",
    "bin/",
    "benchmark/",
    "conftest.py",
)


def _is_utility_path(file_path):
    """Check if a file is in a utility/infrastructure directory or is a known utility file."""
    p = file_path.replace("\\", "/").lower()
    if any(pat in p for pat in _UTILITY_PATH_PATTERNS):
        return True
    if any(pat in p for pat in _NON_PRODUCTION_PATH_PATTERNS):
        return True
    basename = p.rsplit("/", 1)[-1] if "/" in p else p
    return basename in _UTILITY_FILE_PATTERNS


def _percentile(sorted_values, pct):
    """Linear-interpolated percentile from a sorted numeric list."""
    if not sorted_values:
        return 0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    if lo == hi:
        return sorted_values[lo]
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _unique_dirs(file_paths):
    """Extract unique parent directory names from a list of file paths."""
    dirs = set()
    for fp in file_paths:
        p = fp.replace("\\", "/")
        last_slash = p.rfind("/")
        if last_slash >= 0:
            dirs.add(p[:last_slash])
        else:
            dirs.add(".")
    return dirs


def _severity_counts(items):
    # W718: bucket keys are lowercase canonical labels. Per-item
    # ``severity`` values are normalised at read time so legacy
    # UPPER-cased baseline snapshots stored before W718 still bucket
    # correctly under their canonical lowercase key.
    counts = {CRITICAL: 0, WARNING: 0, INFO: 0}
    for item in items:
        sev = _normalise_health_severity(item.get("severity"))
        if sev in counts:
            counts[sev] += 1
    return counts


def _format_severity_counts(counts):
    # W718: text formatter renders lowercase canonical labels in
    # UPPER-case for human readability (display polish, not
    # vocabulary). Counts dict keys are canonical lowercase.
    parts = []
    for sev in (CRITICAL, WARNING, INFO):
        if counts.get(sev, 0):
            parts.append(f"{counts[sev]} {sev.upper()}")
    return ", ".join(parts) if parts else "0 issues"


# ---------------------------------------------------------------------------
# W151: emit to the central findings registry
# ---------------------------------------------------------------------------


def _health_cycle_finding_id(member_names: list[str]) -> str:
    """Stable, deterministic finding id for one cycle (SCC).

    Cycles don't map cleanly to a single (file, symbol, line) anchor —
    the SCC IS the finding. We fold the SORTED list of member symbol
    names into the digest so a re-run on the same SCC upserts rather
    than duplicates, regardless of which symbol the in-memory order
    surfaces first. A symbol entering or leaving the SCC changes the
    digest → a fresh id, which is the desired behaviour (the previous
    cycle is structurally different).
    """
    sorted_names = sorted(member_names)
    raw = "|".join(sorted_names)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"health:arch.cycle:{digest}"


def _health_god_finding_id(symbol_qname: str) -> str:
    """Stable id for a god-component finding, keyed on qualified name."""
    return f"health:arch.god_component:{symbol_qname}"


def _health_bottleneck_finding_id(symbol_qname: str) -> str:
    """Stable id for a bottleneck finding, keyed on qualified name."""
    return f"health:arch.bottleneck:{symbol_qname}"


def _health_layer_violation_finding_id(from_qname: str, to_qname: str) -> str:
    """Stable id for a layer-violation edge finding.

    Keyed on the (from, to) pair — layer violations are edge-level
    findings, the first ``subject_kind="edge"`` user in the registry.
    """
    return f"health:arch.layer_violation:{from_qname}:{to_qname}"


def _qname_for_symbol(file_path: str | None, name: str | None) -> str:
    """Cheap qualified-name string for finding-id stability.

    The DB doesn't always carry a fully-qualified name on every symbol
    row, but ``file_path::name`` is unique enough for the upsert key
    and survives renames of the surrounding directory better than a
    bare name. ``None`` components collapse to empty.
    """
    fp = (file_path or "").replace("\\", "/")
    return f"{fp}::{name or ''}"


def _resolve_symbol_id_by_name(conn: sqlite3.Connection, name: str | None, file_path: str | None) -> int | None:
    """Best-effort lookup of ``symbols.id`` for a (name, file) pair.

    Returns ``None`` when nothing matches; the findings registry
    permits NULL subject_id by design.
    """
    if not name:
        return None
    try:
        if file_path:
            row = conn.execute(
                "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.path = ? AND s.name = ? LIMIT 1",
                (file_path, name),
            ).fetchone()
            if row is not None:
                return int(row[0])
        # Fallback: name-only (file path may be empty or not match
        # exactly because of normalisation drift across consumers).
        row = conn.execute(
            "SELECT id FROM symbols WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        return None


def _pick_cycle_anchor(conn: sqlite3.Connection, member_ids: list[int]) -> tuple[int | None, str, str | None]:
    """Pick the highest-PageRank member of an SCC as the cycle's anchor.

    Returns ``(symbol_id, name, file_path)``. Falls back to the first
    resolved member (by id) if no PageRank data exists.
    """
    if not member_ids:
        return None, "", None
    try:
        rows = list(
            batched_in(
                conn,
                "SELECT s.id, s.name, f.path AS file_path, "
                "       COALESCE(gm.pagerank, 0) AS pagerank "
                "FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
                "WHERE s.id IN ({ph})",
                list(member_ids),
            )
        )
    except sqlite3.OperationalError:
        return None, "", None
    if not rows:
        return None, "", None
    # Highest pagerank first; tie-break by lowest id for determinism.
    rows.sort(key=lambda r: (-(r["pagerank"] or 0), r["id"]))
    top = rows[0]
    return int(top["id"]), str(top["name"] or ""), str(top["file_path"] or "")


def _row_field(row, key: str):
    """Read *key* from a dict OR ``sqlite3.Row``-like mapping; ``None`` on miss.

    Both lookup paths are needed because callers may pass either a plain
    ``dict`` (tests, in-memory v_lookup) or a ``sqlite3.Row`` (live DB
    fetchone()). The original inline form repeated this dance four times
    per layer-violation row (src_name, tgt_name, src_file, tgt_file) and
    inflated the cognitive complexity of ``_emit_health_findings``.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    # sqlite3.Row supports column-by-name access but only if the column
    # exists; probe via .keys() to avoid IndexError.
    if hasattr(row, "keys"):
        try:
            if key in row.keys():
                return row[key]
        except (TypeError, IndexError):
            return None
    return None


def _lookup_endpoint_name_file(conn: sqlite3.Connection, symbol_id: int | None) -> tuple[str | None, str | None]:
    """Best-effort ``(name, file_path)`` lookup for a layer-violation endpoint.

    Returns ``(None, None)`` on missing id, missing row, or a benign DB
    error. Extracted from the inline duplicate src/tgt lookup blocks in
    the layer-violation emit path.
    """
    if symbol_id is None:
        return None, None
    try:
        r = conn.execute(
            "SELECT s.name AS name, f.path AS file_path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
            (symbol_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None
    if r is None:
        return None, None
    return _row_field(r, "name"), _row_field(r, "file_path")


def _emit_cycle_finding(
    conn: sqlite3.Connection,
    scc_ids: list[int],
    cyc: dict,
    source_version: str,
) -> int:
    """Persist one ``arch.cycle`` finding; returns 1 on write, 0 on skip."""
    from roam.db.findings import FindingRecord, emit_finding

    symbols = cyc.get("symbols", []) or []
    member_names = [s.get("name", "") for s in symbols if s.get("name")]
    if not member_names:
        return 0
    anchor_id, anchor_name, anchor_file = _pick_cycle_anchor(conn, scc_ids)
    finding_id = _health_cycle_finding_id(member_names)
    evidence = {
        "kind": "arch.cycle",
        "size": cyc.get("size", len(member_names)),
        "severity": _normalise_health_severity(cyc.get("severity")),
        "actionable": bool(cyc.get("actionable")),
        "local_only": bool(cyc.get("local_only")),
        "has_test_file": bool(cyc.get("has_test_file")),
        "file_count": cyc.get("file_count", len(set(cyc.get("files", [])))),
        "files": list(cyc.get("files", [])),
        "cycle_members": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "kind": s.get("kind"),
                "file_path": s.get("file_path"),
            }
            for s in symbols
        ],
        "anchor_symbol_id": anchor_id,
        "anchor_symbol_name": anchor_name,
        "anchor_file_path": anchor_file,
    }
    claim = (
        f"arch.cycle: SCC of {cyc.get('size', len(member_names))} symbols "
        f"across {len(cyc.get('files', []))} file(s); anchor "
        f"{anchor_name or '?'}"
    )
    emit_finding(
        conn,
        FindingRecord(
            finding_id_str=finding_id,
            subject_kind="symbol" if anchor_id is not None else "cycle",
            subject_id=anchor_id,
            claim=claim,
            evidence_json=_json.dumps(evidence, sort_keys=True),
            confidence=_HEALTH_KIND_TO_CONFIDENCE["arch.cycle"],
            source_detector="health",
            source_version=source_version,
        ),
    )
    return 1


def _emit_god_finding(conn: sqlite3.Connection, g: dict, source_version: str) -> int:
    """Persist one ``arch.god_component`` finding; returns 1 on write, 0 on skip."""
    from roam.db.findings import FindingRecord, emit_finding

    name = g.get("name") or ""
    file_path = g.get("file") or ""
    if not name:
        return 0
    qname = _qname_for_symbol(file_path, name)
    subject_id = _resolve_symbol_id_by_name(conn, name, file_path)
    finding_id = _health_god_finding_id(qname)
    evidence = {
        "kind": "arch.god_component",
        "name": name,
        "symbol_kind": g.get("kind"),
        "degree": g.get("degree"),
        "file_path": file_path,
        "severity": _normalise_health_severity(g.get("severity")),
        "category": g.get("category", "actionable"),
    }
    claim = f"arch.god_component: {name} ({g.get('kind') or '?'}) — degree {g.get('degree')} in {file_path or '?'}"
    emit_finding(
        conn,
        FindingRecord(
            finding_id_str=finding_id,
            subject_kind="symbol",
            subject_id=subject_id,
            claim=claim,
            evidence_json=_json.dumps(evidence, sort_keys=True),
            confidence=_HEALTH_KIND_TO_CONFIDENCE["arch.god_component"],
            source_detector="health",
            source_version=source_version,
        ),
    )
    return 1


def _emit_bottleneck_finding(conn: sqlite3.Connection, b: dict, source_version: str) -> int:
    """Persist one ``arch.bottleneck`` finding; returns 1 on write, 0 on skip."""
    from roam.db.findings import FindingRecord, emit_finding

    name = b.get("name") or ""
    file_path = b.get("file") or ""
    if not name:
        return 0
    qname = _qname_for_symbol(file_path, name)
    subject_id = _resolve_symbol_id_by_name(conn, name, file_path)
    finding_id = _health_bottleneck_finding_id(qname)
    evidence = {
        "kind": "arch.bottleneck",
        "name": name,
        "symbol_kind": b.get("kind"),
        "betweenness": b.get("betweenness"),
        "file_path": file_path,
        "severity": _normalise_health_severity(b.get("severity")),
        "category": b.get("category", "actionable"),
    }
    claim = (
        f"arch.bottleneck: {name} ({b.get('kind') or '?'}) — betweenness {b.get('betweenness')} in {file_path or '?'}"
    )
    emit_finding(
        conn,
        FindingRecord(
            finding_id_str=finding_id,
            subject_kind="symbol",
            subject_id=subject_id,
            claim=claim,
            evidence_json=_json.dumps(evidence, sort_keys=True),
            confidence=_HEALTH_KIND_TO_CONFIDENCE["arch.bottleneck"],
            source_detector="health",
            source_version=source_version,
        ),
    )
    return 1


def _emit_layer_violation_finding(
    conn: sqlite3.Connection,
    v: dict,
    source_version: str,
    *,
    v_lookup: dict | None,
) -> int:
    """Persist one ``arch.layer_violation`` finding; returns 1 on write, 0 on skip.

    Subject is an edge (``subject_kind="edge"``) so both endpoints are
    encoded in ``evidence_json`` rather than the single-valued
    ``subject_id`` column.
    """
    from roam.db.findings import FindingRecord, emit_finding

    src_id = v.get("source")
    tgt_id = v.get("target")
    src_row = (v_lookup or {}).get(src_id, {})
    tgt_row = (v_lookup or {}).get(tgt_id, {})

    src_name = _row_field(src_row, "name")
    tgt_name = _row_field(tgt_row, "name")
    src_file = _row_field(src_row, "file_path")
    tgt_file = _row_field(tgt_row, "file_path")

    # Best-effort name lookup for source/target if v_lookup didn't carry them.
    if not src_name:
        src_name, src_file2 = _lookup_endpoint_name_file(conn, src_id)
        src_file = src_file or src_file2
    if not tgt_name:
        tgt_name, tgt_file2 = _lookup_endpoint_name_file(conn, tgt_id)
        tgt_file = tgt_file or tgt_file2

    src_name = src_name or ""
    tgt_name = tgt_name or ""
    if not src_name or not tgt_name:
        # Can't form a stable id without both endpoints — skip.
        return 0

    from_qname = _qname_for_symbol(src_file, src_name)
    to_qname = _qname_for_symbol(tgt_file, tgt_name)
    finding_id = _health_layer_violation_finding_id(from_qname, to_qname)
    evidence = {
        "kind": "arch.layer_violation",
        "from_symbol_id": src_id,
        "from_symbol_name": src_name,
        "from_file_path": src_file or "",
        "from_layer": v.get("source_layer"),
        "to_symbol_id": tgt_id,
        "to_symbol_name": tgt_name,
        "to_file_path": tgt_file or "",
        "to_layer": v.get("target_layer"),
        "layer_distance": v.get("layer_distance"),
        "severity": _normalise_health_severity(v.get("severity") or WARNING),
        "edge_severity_score": v.get("severity"),
    }
    claim = f"arch.layer_violation: {src_name} (L{v.get('source_layer')}) -> {tgt_name} (L{v.get('target_layer')})"
    emit_finding(
        conn,
        FindingRecord(
            finding_id_str=finding_id,
            subject_kind="edge",
            subject_id=None,
            claim=claim,
            evidence_json=_json.dumps(evidence, sort_keys=True),
            confidence=_HEALTH_KIND_TO_CONFIDENCE["arch.layer_violation"],
            source_detector="health",
            source_version=source_version,
        ),
    )
    return 1


def _emit_health_findings(
    conn: sqlite3.Connection,
    cycles: list[dict],
    god_items: list[dict],
    bn_items: list[dict],
    violations: list[dict],
    source_version: str,
    *,
    v_lookup: dict | None = None,
    raw_by_formatted_cycle: list | None = None,
) -> int:
    """Mirror the 4 health-finding arrays into the central findings registry.

    Returns the count of finding rows written. The caller is
    responsible for opening ``conn`` writable; ``emit_finding`` does
    not commit (the caller commits once after this returns).

    The persisted set is intentionally the FULL set produced by the
    detection passes — NOT the subsequently-filtered display set.
    Re-runs upsert on ``finding_id_str`` so the registry stays in
    sync with the latest detection without duplicating rows.

    Parameters
    ----------
    cycles
        The formatted-cycle list from ``format_cycles`` +
        ``mark_actionable_cycles`` — each dict has ``symbols``,
        ``files``, ``size``, ``actionable``, ``severity``.
    god_items
        The degree-thresholded god components list, severity-classified.
    bn_items
        The betweenness-thresholded bottlenecks list, severity-
        classified.
    violations
        The topological-layer violation list from ``find_violations`` —
        each dict has ``source``, ``target``, ``source_layer``,
        ``target_layer`` (and an injected ``severity``).
    source_version
        Detector-version stamp; the caller passes
        :data:`HEALTH_DETECTOR_VERSION`.
    v_lookup
        Optional ``{symbol_id: row}`` lookup for resolving the source /
        target symbol names of layer violations. When omitted the
        helper falls back to per-row DB lookups.
    raw_by_formatted_cycle
        Optional ``[(scc_ids, formatted_cycle), ...]`` pairing so we
        can recover the raw SCC member ids for anchor selection.

    The four arch-level kinds (cycle, god_component, bottleneck,
    layer_violation) are each emitted by a per-kind helper above; this
    function is the dispatcher.
    """
    # Anchor each cycle on the highest-PageRank SCC member; encode the
    # full member list in evidence_json so consumers can reconstruct
    # the SCC without joining the registry against ``symbols``.
    cycle_iter: list[tuple[list[int], dict]]
    if raw_by_formatted_cycle is not None:
        cycle_iter = list(raw_by_formatted_cycle)
    else:
        # Fall back to deriving member ids from the formatted symbols
        # list. The caller usually passes the raw pairing, so this is a
        # safety net for direct callers / tests.
        cycle_iter = [([s["id"] for s in cyc.get("symbols", []) if "id" in s], cyc) for cyc in cycles]

    written = 0
    for scc_ids, cyc in cycle_iter:
        written += _emit_cycle_finding(conn, scc_ids, cyc, source_version)
    for g in god_items:
        written += _emit_god_finding(conn, g, source_version)
    for b in bn_items:
        written += _emit_bottleneck_finding(conn, b, source_version)
    for v in violations:
        written += _emit_layer_violation_finding(conn, v, source_version, v_lookup=v_lookup)

    return written


def _parse_simple_yaml(text: str) -> dict:
    """Parse a flat YAML file with one top-level section (no PyYAML needed)."""
    result: dict[str, dict] = {}
    current_section = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace() and stripped.endswith(":"):
            current_section = stripped[:-1]
            result[current_section] = {}
        elif current_section and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip()
            # Try numeric conversion
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
            result[current_section][key.strip()] = val
    return result


def _load_gate_config(
    *,
    warnings_out: WarningsOut = None,
) -> dict:
    """Load quality gate thresholds from .roam-gates.yml or use defaults.

    Thin wrapper over :func:`_load_gate_config_with_status` that drops the
    closed-enum ``LoadStatus`` return so pre-W1030-followup-B callers (the
    SARIF-emit path + the existing W1052 ``warnings_out`` tests) stay
    byte-identical. New callsites that want to disambiguate
    ``missing`` / ``empty_file`` / ``empty_yaml`` / ``parse_error`` /
    ``wrong_root_type`` / ``read_error`` / ``schema_invalid`` / ``ok``
    should call :func:`_load_gate_config_with_status` directly.
    """
    cfg, _status = _load_gate_config_with_status(warnings_out=warnings_out)
    return cfg


def _load_gate_config_with_status(
    *,
    warnings_out: WarningsOut = None,
) -> tuple[dict, str]:
    """W1030-followup-B: load quality-gate thresholds and return ``(cfg, status)``.

    ``status`` is a closed-enum string drawn from
    :data:`roam.commands._yaml_loader.LOAD_STATUSES`
    (``"ok"`` / ``"missing"`` / ``"empty_file"`` / ``"empty_yaml"`` /
    ``"read_error"`` / ``"parse_error"`` / ``"wrong_root_type"`` /
    ``"schema_invalid"``). Lets the ``roam health --gate`` envelope
    disambiguate "no ``.roam-gates.yml`` configured yet" (``missing`` ->
    use baseline gates silently) from "``.roam-gates.yml`` exists but is
    empty" (``empty_file`` / ``empty_yaml`` -> use baseline gates +
    flag the empty stub) from "``.roam-gates.yml`` is broken"
    (``parse_error`` / ``wrong_root_type`` / ``read_error`` /
    ``schema_invalid`` -> ``partial_success=True``, warnings already
    populated by the canonical loader).

    Mirror of :func:`roam.commands.cmd_budget._load_budgets_with_status`
    (W1030-followup-A reference impl). ``health`` is the flagship CI-gate
    command — every agent invokes ``roam health`` first — so the
    config-state disclosure rides on the highest-leverage envelope.

    W1052 (Pattern 2 — silent fallback, mirror of W706's
    ``_load_ignore_findings_file``): when *warnings_out* is supplied as
    a ``list[str]``, every silent-fallback path (file unreadable,
    malformed YAML, non-mapping root, missing ``health`` key, non-mapping
    ``health`` block) appends an actionable warning naming the path, the
    failure shape, and the resolution. Pre-W1052 callers that don't
    supply ``warnings_out`` retain byte-identical silent-defaults
    behaviour.

    The function is intentionally narrow — health is a flagship CI-gate
    command (W834 sealed its silent-Healthy bug on empty corpus); the
    plumbing here exposes the loader's silent-empty fallback path the
    same way W834 exposed the score-collapse path.
    """
    defaults = {"health_min": 60}

    config_path = Path(".roam-gates.yml")
    if not config_path.exists():
        return defaults, "missing"

    from roam.commands._yaml_loader import load_yaml_with_warnings

    path_str = str(config_path)
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data, status = load_yaml_with_warnings(
        config_path,
        tiny_parser=_parse_simple_yaml,
        config_label="health-gate",
        warnings_out=warnings_out,
        return_status=True,
    )
    if data is None:
        # Missing file — already short-circuited above; defensive.
        return defaults, status
    if status in ("empty_file", "empty_yaml"):
        # W1030-followup-B: zero-byte / comments-only file is a distinct
        # on-disk state from "non-empty file missing the ``health:``
        # key" — the user created a stub but did not write any
        # thresholds. Suppress the "no `health:` key" warning that the
        # legacy missing-key branch would emit so the empty-stub state
        # surfaces cleanly as ``config_state=empty_file`` (or
        # ``empty_yaml``) on the envelope, with no warning. Pattern 2 is
        # preserved for the malformed cases — ``parse_error`` /
        # ``wrong_root_type`` still emit the canonical loader's warning
        # above. Mirrors cmd_budget._load_budgets_with_status.
        return defaults, status
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # YAML / wrong root type / tiny-parser fallback). Propagate the
        # defaults without piling on a second "no `health:` key" warning
        # that would just confuse the caller.
        return defaults, status
    assert isinstance(data, dict)
    if "health" not in data:
        if warnings_out is not None:
            warnings_out.append(
                f"health-gate: {path_str!r} has no `health:` key. "
                f"Expected shape: `health:` followed by a mapping of "
                f"`{{health_min, complexity_max, cycle_max, tangle_max}}` "
                f"thresholds."
            )
        return defaults, status
    # W1038 — shared "load → check type → warn-or-default" extractor.
    from roam.commands._yaml_loader import extract_typed

    health_block = extract_typed(
        data,
        "health",
        dict,
        {},
        warnings_out=warnings_out,
        context=f"health-gate: {path_str!r}",
        expected_shape="a mapping",
    )
    defaults.update(health_block)
    return defaults, status


# ---------------------------------------------------------------------------
# Baseline-diff mode helpers
# ---------------------------------------------------------------------------

# Per-category metric definitions used for delta synthesis. Each entry maps
# the snapshot column to a finding "kind" + a default severity + the polarity
# (lower_is_better=True means an INCREASE is a regression). ``health_score``
# is the only inverted metric (higher is better).
_BASELINE_METRICS = (
    # (snapshot_col, kind, severity, lower_is_better)
    # W718: lowercase canonical severity vocabulary (W547). Pre-W718
    # this table emitted UPPER-cased labels into the baseline-diff
    # ``severity`` field; the consumer paths now expect the canonical
    # lowercase form throughout.
    ("cycles", "cycle", WARNING, True),
    ("god_components", "god_component", WARNING, True),
    ("bottlenecks", "bottleneck", WARNING, True),
    ("dead_exports", "dead_export", INFO, True),
    ("layer_violations", "layer_violation", WARNING, True),
)


def _resolve_main_branch(root: Path) -> str:
    """Detect the local main branch (main or master) for ``--baseline auto``."""
    for branch in ("main", "master"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                cwd=str(root),
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return branch
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "main"


def _find_baseline_snapshot(conn, ref: str) -> dict | None:
    """Look up a baseline snapshot for the given ref.

    Returns a row dict on success, ``None`` if nothing matched.
    Refs are resolved as follows:

    * ``last`` — most recent snapshot regardless of ref.
    * ``auto`` — most recent snapshot whose ``git_branch`` equals the local
      main/master branch.
    * anything else — most recent snapshot whose ``git_branch`` equals
      ``ref`` OR whose ``git_commit`` starts with ``ref``.
    """
    if ref == "last":
        row = conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    if ref == "auto":
        from roam.db.connection import find_project_root

        try:
            root = find_project_root()
        except Exception:
            root = Path(".")
        branch = _resolve_main_branch(root)
        row = conn.execute(
            "SELECT * FROM snapshots WHERE git_branch = ? ORDER BY timestamp DESC LIMIT 1",
            (branch,),
        ).fetchone()
        return dict(row) if row else None

    # Treat ref as either a branch name or a commit prefix.
    row = conn.execute(
        "SELECT * FROM snapshots WHERE git_branch = ? ORDER BY timestamp DESC LIMIT 1",
        (ref,),
    ).fetchone()
    if row:
        return dict(row)

    row = conn.execute(
        "SELECT * FROM snapshots WHERE git_commit LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{ref}%",),
    ).fetchone()
    return dict(row) if row else None


def _format_baseline_timestamp(ts: int | None) -> str | None:
    """Render a unix timestamp as ISO 8601 UTC, or ``None`` if missing."""
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0)
        return dt.isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return None


def _compute_baseline_delta(current: dict, baseline: dict) -> dict:
    """Compute new / fixed / regressed findings + per-metric score deltas.

    The snapshots table only stores aggregate counts, not per-finding rows,
    so each "finding" in the delta arrays is a synthetic per-category entry
    of shape ``{kind, target, severity, was, now}``. ``new_findings`` is
    emitted when the current count is strictly higher than the baseline,
    ``fixed_findings`` when strictly lower, and ``regressed`` when the
    composite ``health_score`` (or any tracked count) moved in the bad
    direction.
    """
    new_findings: list[dict] = []
    fixed_findings: list[dict] = []
    regressed: list[dict] = []
    score_delta: dict[str, float] = {}

    for col, kind, severity, lower_is_better in _BASELINE_METRICS:
        was = baseline.get(col) or 0
        now = current.get(col) or 0
        delta = now - was
        score_delta[col] = delta
        if delta == 0:
            continue
        # Severity bumps to CRITICAL when the swing is large in the bad direction.
        worsened = (delta > 0) if lower_is_better else (delta < 0)
        improved = not worsened
        finding = {
            "kind": kind,
            "target": col,
            "severity": severity,
            "was": was,
            "now": now,
        }
        if worsened:
            # If the metric grew by more than 50% (and at least 2 absolute),
            # promote to critical — that's the "high-severity new finding"
            # signal the verdict logic looks for.
            if abs(delta) >= 2 and (was == 0 or abs(delta) / max(was, 1) >= 0.5):
                finding["severity"] = CRITICAL
            new_findings.append(finding)
            regressed.append(finding)
        elif improved:
            fixed_findings.append(finding)

    # Composite health_score handled separately (higher = better).
    cur_score = current.get("health_score") or 0
    base_score = baseline.get("health_score") or 0
    score_diff = cur_score - base_score
    score_delta["health_score"] = score_diff
    if score_diff < 0:
        regressed.append(
            {
                "kind": "health_score",
                "target": "health_score",
                "severity": CRITICAL if abs(score_diff) >= 10 else WARNING,
                "was": base_score,
                "now": cur_score,
            }
        )

    return {
        "new_findings": new_findings,
        "fixed_findings": fixed_findings,
        "regressed": regressed,
        "score_delta": score_delta,
    }


def _baseline_verdict(delta: dict) -> str:
    """Apply the documented verdict policy to a delta block.

    * ``BAD``    — composite health_score regressed against baseline.
    * ``REVIEW`` — at least one new ``critical`` finding (even if score held).
    * ``OK``     — otherwise.

    W718: severity comparison is case-insensitive via
    :func:`_normalise_health_severity` so legacy baseline snapshots
    stored before W718 (with UPPER-cased severity values) still
    classify correctly.
    """
    score_diff = delta["score_delta"].get("health_score", 0)
    if score_diff < 0:
        return "BAD"
    for f in delta["new_findings"]:
        if _normalise_health_severity(f.get("severity")) == CRITICAL:
            return "REVIEW"
    return "OK"


def _emit_baseline_diff(
    *,
    conn,
    baseline_ref: str,
    health_score: int,
    actionable_cycles,
    god_items,
    bn_items,
    violations,
    json_mode: bool,
    token_budget: int,
) -> None:
    """Emit the --baseline mode response: compute delta vs a stored
    snapshot and echo it as JSON or text.

    Extracted from ``health()`` (R9.A5) — was a 125-line inline branch.
    Both the JSON and text exit paths terminate the command, so the
    caller does ``return`` immediately after invoking this helper.
    """
    # Dead-export count: mirror the query metrics_history uses so the
    # current vs. baseline comparison is apples-to-apples. Tests that
    # don't care about exports are unaffected by 0-vs-0 deltas.
    from roam.db.queries import UNREFERENCED_EXPORTS as _UNREF_EXPORTS

    try:
        _dead_rows = conn.execute(_UNREF_EXPORTS).fetchall()
        _dead_exports = sum(
            1
            for r in _dead_rows
            if not (r["file_path"] or "").lower().rsplit("/", 1)[-1].startswith("test_")
            and not (r["file_path"] or "").lower().endswith("_test.py")
        )
    except Exception:
        _dead_exports = 0

    current_metrics = {
        "health_score": health_score,
        "cycles": len(actionable_cycles),
        "god_components": len(god_items),
        "bottlenecks": len(bn_items),
        "dead_exports": _dead_exports,
        "layer_violations": len(violations),
    }

    baseline = _find_baseline_snapshot(conn, baseline_ref)

    if baseline is None:
        degraded_msg = (
            f"No baseline snapshot found for ref `{baseline_ref}`. "
            "Run `roam trends --save` first, or use `--baseline last`."
        )
        if json_mode:
            envelope = json_envelope(
                "health",
                budget=token_budget,
                summary={
                    "verdict": "DEGRADED",
                    "reason": "no_baseline_snapshot",
                    "baseline_ref": baseline_ref,
                    "health_score": health_score,
                    # W331: surface the score definition wherever the
                    # score appears in a summary.
                    "health_score_definition": HEALTH_SCORE_DEFINITION,
                },
                baseline_ref=baseline_ref,
                message=degraded_msg,
            )
            click.echo(to_json(envelope))
            return
        click.echo(f"VERDICT: DEGRADED — {degraded_msg}")
        return

    delta = _compute_baseline_delta(current_metrics, baseline)
    baseline_verdict = _baseline_verdict(delta)
    baseline_taken_at = _format_baseline_timestamp(baseline.get("timestamp"))
    new_count = len(delta["new_findings"])
    fixed_count = len(delta["fixed_findings"])
    regressed_count = len(delta["regressed"])
    delta_block = {
        "new_findings": delta["new_findings"],
        "fixed_findings": delta["fixed_findings"],
        "regressed": delta["regressed"],
        "score_delta": delta["score_delta"],
        "baseline_ref": baseline_ref,
        "baseline_taken_at": baseline_taken_at,
        "baseline_git_branch": baseline.get("git_branch"),
        "baseline_git_commit": baseline.get("git_commit"),
    }

    if json_mode:
        envelope = json_envelope(
            "health",
            budget=token_budget,
            summary={
                "verdict": baseline_verdict,
                "baseline_ref": baseline_ref,
                "baseline_taken_at": baseline_taken_at,
                "new_findings_count": new_count,
                "fixed_findings_count": fixed_count,
                "regressed_count": regressed_count,
                "health_score": health_score,
                "score_delta": delta["score_delta"],
                # W331: baseline-mode envelope also surfaces health_score.
                "health_score_definition": HEALTH_SCORE_DEFINITION,
            },
            delta=delta_block,
            health_score=health_score,
        )
        click.echo(to_json(envelope))
        return

    # Text output for baseline mode.
    click.echo(f"VERDICT: {baseline_verdict} (baseline: {baseline_ref})\n")
    click.echo(
        "Δ +{new} findings, {fixed} fixed, {regressed} regressed".format(
            new=new_count, fixed=fixed_count, regressed=regressed_count
        )
    )
    score_diff = delta["score_delta"].get("health_score", 0)
    score_sign = "+" if score_diff > 0 else ""
    base_score = baseline.get("health_score") or 0
    click.echo(
        f"Score: {base_score} -> {health_score} ({score_sign}{score_diff})"
        f"   Baseline taken: {baseline_taken_at or '(unknown)'}"
    )
    if delta["new_findings"]:
        click.echo("\nNew findings:")
        for f in delta["new_findings"][:10]:
            click.echo(f"  [{f['severity']}] +{f['now'] - f['was']} {f['kind']} (was {f['was']}, now {f['now']})")
        if len(delta["new_findings"]) > 10:
            click.echo(f"  (+{len(delta['new_findings']) - 10} more)")
    if delta["regressed"]:
        # Avoid double-listing items already shown under "new_findings"
        # — only score regressions are exclusive to this section.
        score_regressions = [r for r in delta["regressed"] if r["kind"] == "health_score"]
        if score_regressions:
            click.echo("\nRegressed:")
            for r in score_regressions:
                click.echo(f"  [{r['severity']}] {r['kind']}: {r['was']} -> {r['now']}")
    if not delta["new_findings"] and not delta["regressed"]:
        click.echo("\nNo regressions detected.")


@roam_capability(
    category="health",
    summary="Report code health: cycles, god components, bottlenecks, 0-100 score.",
    inputs=["repo_path"],
    outputs=["health_score", "findings", "verdict"],
    examples=[
        "roam health",
        "roam health --gate",
        "roam health --baseline main",
    ],
    tags=["health", "ci"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.option(
    "--no-framework",
    is_flag=True,
    help="Filter out framework/boilerplate symbols from god components and bottlenecks",
)
@click.option("--gate", is_flag=True, help="Run quality gate checks (exit 5 on failure)")
@click.option(
    "--explain",
    is_flag=True,
    help="Show how the 0-100 score decomposes into category contributions.",
)
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help=(
        "Compare against a stored baseline snapshot and report deltas instead of "
        "the absolute set. REF can be a git ref (e.g. 'main', a tag, a SHA), "
        "'last' for the most recent snapshot regardless of ref, or 'auto' for "
        "the most recent snapshot taken on the main branch. "
        "Run `roam trends --save` regularly (or in CI) to populate baseline snapshots."
    ),
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist the 4 arch-level findings (cycles, god components, "
        "bottlenecks, layer violations) to the .roam/index.db findings "
        "registry (cross-detector queryable via `roam findings list "
        "--detector health`). The detector-specific output is unchanged; "
        "the registry rows are the denormalised cross-detector surface. "
        "Persisted set ignores --no-framework display filtering — every "
        "detected hit is mirrored so a downstream consumer doesn't see a "
        "truncated registry."
    ),
)
@click.pass_context
def health(ctx, no_framework, gate, explain, baseline_ref, persist):
    """Show code health: cycles, god components, bottlenecks.

    \b
    Examples:
      roam health
      roam health --explain
      roam health --baseline auto --gate
      roam --sarif health > health.sarif

    See also ``debt`` (refactoring backlog with ROI), ``trends``
    (snapshot history + regressions), and ``diagnose`` (root cause
    ranking for a single failing symbol).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    # W107/W120 composition: global `roam --ci` also flips on the local
    # --gate flag so `roam --ci health` exits 5 on quality-gate failure
    # without having to repeat the per-command toggle. LAW 11: explicit
    # local --gate still wins (no-op when already True).
    if not gate and ctx.obj and ctx.obj.get("ci_mode"):
        gate = True
    ensure_index()

    # W607-M: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the DB-shape health pipeline. cmd_health is the
    # flagship CI-gate aggregator that consumes graph_metrics / symbols /
    # files / file_stats / graph (build_symbol_graph) / find_cycles /
    # detect_layers / propagation_cost / algebraic_connectivity /
    # imported_coverage_overview substrates via direct SQL queries +
    # helper calls; any of those raising silently degrades the pipeline
    # while the JSON envelope claims success.
    #
    # Marker family ``health_*`` (DB scope, distinct from W607-G/H/I/J
    # grep_* / history_* / refs_text_* / delete_check_* subprocess
    # families, W607-K's ``describe_*`` flagship-aggregator family, and
    # W607-L's ``minimap_*`` DB-shape family). The marker-prefix
    # discipline keeps each consumer's scope identifiable downstream.
    #
    # Complementary to W805-833 / W833 Pattern-2 silent-Healthy fix
    # (which pins the empty-corpus silent-100/100-Healthy verdict).
    # W607-M does NOT graduate any W833 bug — empty-corpus state
    # disclosure is a separate Pattern-2 contract orthogonal to the
    # DB-shape degrade axis here. On empty corpus the early-return at
    # _early_symbol_count==0 fires BEFORE the W607-M-instrumented phases,
    # so warnings_out stays empty and the envelope is byte-identical
    # to the pre-W607-M shape. Outside the empty-corpus path, helpers
    # that hit a healthy substrate return cleanly (NOT exceptions), so
    # warnings_out also stays empty on the happy path.
    #
    # Empty bucket → byte-identical envelope (no warnings_out key in
    # either ``summary`` or top-level).
    _w607m_warnings_out: list[str] = []

    # W607-BA — ADDITIVE per-substrate plumbing layered on top of the
    # W607-M inline try/except plumbing. The W607-M wave covered the
    # DB-shape graph-substrate boundaries (graph_build / cycles /
    # god_components / bottlenecks / layers / tangle / propagation /
    # algebraic_connectivity / file_health / imported_coverage). W607-BA
    # closes the remaining substrate-CALL boundaries on the FLAGSHIP
    # 0-100 score CI-gate aggregator that W607-M did NOT wrap:
    #
    #   * gate_config_load          -- `.roam-gates.yml` loader (gate branch)
    #   * gate_complexity_query     -- complexity_max SELECT (replaces a
    #                                  pre-W607-BA bare ``except Exception``
    #                                  that swallowed the marker entirely)
    #   * compute_health_score      -- the geometric-mean 0-100 composition
    #   * compose_verdict           -- the "Healthy 32/100 with 12 cycles"
    #                                  derivation (CLAUDE.md LAW 6 canonical
    #                                  example)
    #   * health_findings_emit      -- ``_emit_health_findings`` registry write
    #   * suggest_next_steps_call   -- agent-contract next_steps composition
    #   * baseline_diff_emit        -- the ``--baseline`` branch helper
    #   * sarif_emit                -- the SARIF projection branch
    #   * gate_sarif_loader         -- ``_load_gate_config`` inside SARIF mode
    #   * serialize_envelope_main   -- the on-text JSON serialization on the
    #                                  primary --json branch
    #
    # CLAUDE.md LAW 6 critical axis: cmd_health's "Healthy 32/100 with 12
    # cycles" verdict is the canonical example for a verdict that must
    # work without any other field. A silent failure in any sub-score
    # boundary defeats the CI gate downstream consumers depend on. The
    # W607-BA additive bucket surfaces a marker even when the failure
    # happens AFTER the W607-M-wrapped DB phases succeed.
    #
    # Marker family ``health_*`` (same as W607-M -- the same scope
    # consumer reads BOTH wave's markers off the same bucket field).
    # The two waves share the marker family AND the warnings_out axis
    # (the per-wave bucket is merged into a single ``warnings_out``
    # list before serialization).
    #
    # Empty bucket -> no field added -> byte-identical envelope to the
    # pre-W607-BA shape (W607-M parity discipline).
    _w607ba_warnings_out: list[str] = []

    def _run_check_ba(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BA marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a
        ``health_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607ba_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ba_warnings_out.append(f"health_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # W834 — empty-corpus carve-out (Pattern 2 silent-fallback fix).
        # If the corpus has zero indexed symbols, every health factor
        # collapses to 1.0 (no signal) and the geometric mean returns
        # 100/100, producing a false "Healthy codebase" verdict on an
        # unanalyzed repo. Mirror ``cmd_vulns`` Fix E + ``cmd_missing_index``
        # no-migrations carve-out: emit a structured envelope that
        # discloses the empty state, sets ``partial_success=True``, and
        # offers an actionable hint. Done BEFORE the expensive graph
        # build so a no-symbols repo doesn't pay for it.
        _early_symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 0
        if _early_symbol_count == 0:
            empty_verdict = "no symbols to analyze (corpus empty — run `roam index --force` to populate the index)"
            empty_facts = [
                "0 indexed symbols",
                "no health score computed — 0 factors with signal",
                "run `roam index --force` to populate the index",
            ]
            if json_mode:
                envelope = json_envelope(
                    "health",
                    budget=token_budget,
                    summary={
                        "verdict": empty_verdict,
                        "state": "empty_corpus",
                        "partial_success": True,
                        "health_score": None,
                        "tangle_ratio": None,
                        "propagation_cost": None,
                        "algebraic_connectivity": None,
                        "issue_count": 0,
                        "severity": {CRITICAL: 0, WARNING: 0, INFO: 0},
                        "actionable_cycles": 0,
                        "ignored_cycles": 0,
                        "total_cycles": 0,
                        "cycles_total": 0,
                        "cycles_actionable": 0,
                        "god_components": 0,
                        "health_score_definition": HEALTH_SCORE_DEFINITION,
                        "tangle_ratio_definition": TANGLE_RATIO_DEFINITION,
                    },
                    health_score=None,
                    issue_count=0,
                    severity={CRITICAL: 0, WARNING: 0, INFO: 0},
                    indexed_symbols=0,
                    agent_contract={
                        "facts": empty_facts,
                        "next_commands": ["roam index --force"],
                    },
                )
                click.echo(to_json(envelope))
            else:
                click.echo(f"VERDICT: {empty_verdict}")
                click.echo()
                click.echo("  0 indexed symbols — nothing to analyze.")
                click.echo("  Run `roam index --force` to populate the index.")
            # --gate on an empty corpus must fail loudly (W531 fail-loud):
            # a "healthy / passing" exit on an unanalyzed repo would let
            # CI green-light any branch with a broken/missing index.
            if gate:
                from roam.exit_codes import GateFailureError

                raise GateFailureError("Quality gate failed: empty corpus (0 indexed symbols)")
            return

        # W607-M: per-phase substrate guard — graph build is the
        # foundation; falling back to an empty graph means downstream
        # cycles / layers / propagation all return empty results.
        try:
            G = build_symbol_graph(conn)
        except Exception as exc:
            _w607m_warnings_out.append(f"health_graph_build_failed:{type(exc).__name__}:{exc}")
            import networkx as _nx  # local import — keep import cost off cold path

            G = _nx.DiGraph()

        # --- Cycles ---
        # W607-M: per-phase substrate guard for find_cycles + format_cycles.
        try:
            cycles = find_cycles(G)
            formatted_cycles = format_cycles(cycles, conn) if cycles else []
            mark_actionable_cycles(formatted_cycles)
        except Exception as exc:
            _w607m_warnings_out.append(f"health_cycles_failed:{type(exc).__name__}:{exc}")
            cycles = []
            formatted_cycles = []

        raw_by_formatted_cycle = list(zip(cycles, formatted_cycles))

        # --- Cycle break suggestions ---
        break_suggestions: list[dict] = []
        for scc, cyc_info in raw_by_formatted_cycle:
            if not cyc_info.get("actionable"):
                continue
            if len(scc) < 3:
                continue
            result = find_weakest_edge(G, scc)
            if result is None:
                continue
            src_id, tgt_id, reason = result
            src_name = G.nodes[src_id].get("name", "?") if src_id in G else "?"
            tgt_name = G.nodes[tgt_id].get("name", "?") if tgt_id in G else "?"
            break_suggestions.append(
                {
                    "source_id": src_id,
                    "target_id": tgt_id,
                    "source_name": src_name,
                    "target_name": tgt_name,
                    "reason": reason,
                    "scc_size": len(scc),
                }
            )

        # --- God components ---
        # W607-M: per-phase substrate guard for TOP_BY_DEGREE query.
        try:
            degree_rows = conn.execute(TOP_BY_DEGREE, (50,)).fetchall()
        except Exception as exc:
            _w607m_warnings_out.append(f"health_god_components_failed:{type(exc).__name__}:{exc}")
            degree_rows = []
        god_items = []
        for r in degree_rows:
            total = (r["in_degree"] or 0) + (r["out_degree"] or 0)
            if total > 20:
                god_items.append(
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "degree": total,
                        "file": r["file_path"],
                    }
                )

        # --- Bottlenecks (percentile-based severity) ---
        # Fetch all non-zero betweenness values to compute percentile thresholds.
        # Raw betweenness is unnormalized (shortest-path counts), so absolute
        # thresholds don't scale across codebase sizes. Percentiles do.
        # W607-M: per-phase substrate guard for betweenness queries.
        try:
            all_bw = sorted(
                r[0] for r in conn.execute("SELECT betweenness FROM graph_metrics WHERE betweenness > 0").fetchall()
            )
            bw_rows = conn.execute(TOP_BY_BETWEENNESS, (15,)).fetchall()
        except Exception as exc:
            _w607m_warnings_out.append(f"health_bottlenecks_failed:{type(exc).__name__}:{exc}")
            all_bw = []
            bw_rows = []
        bn_p70 = _percentile(all_bw, 70)
        bn_p90 = _percentile(all_bw, 90)

        bn_items = []
        for r in bw_rows:
            bw = r["betweenness"] or 0
            if bw > 0.5:
                bn_items.append(
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "betweenness": round(bw, 1),
                        "file": r["file_path"],
                    }
                )

        # --- Framework filtering ---
        filtered_count = 0
        if no_framework:
            before = len(god_items) + len(bn_items)
            god_items = [g for g in god_items if g["name"] not in _FRAMEWORK_NAMES]
            bn_items = [b for b in bn_items if b["name"] not in _FRAMEWORK_NAMES]
            filtered_count = before - len(god_items) - len(bn_items)

        # --- Layer violations ---
        # W607-M: per-phase substrate guard for layer detection +
        # v_lookup batched_in. Falling back to an empty layer_map +
        # violations + v_lookup means layer-violation counts go to 0
        # and the verdict surfaces the marker rather than crashing.
        try:
            layer_map = detect_layers(G)
            violations = find_violations(G, layer_map) if layer_map else []
            v_lookup = {}
            if violations:
                all_ids = {v["source"] for v in violations} | {v["target"] for v in violations}
                for r in batched_in(
                    conn,
                    "SELECT s.id, s.name, f.path as file_path "
                    "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
                    list(all_ids),
                ):
                    v_lookup[r["id"]] = r
        except Exception as exc:
            _w607m_warnings_out.append(f"health_layers_failed:{type(exc).__name__}:{exc}")
            layer_map = {}
            violations = []
            v_lookup = {}

        # ---- Classify issue severity (location-aware) ----
        # W718: severity labels are canonical lowercase (W547).
        sev_counts = {CRITICAL: 0, WARNING: 0, INFO: 0}

        # Cycle severity: directory-aware, but local/test-involved SCCs are
        # informational and excluded from health scoring. They commonly come
        # from Vue <script setup> local symbol references or test helpers with
        # duplicate names; neither is an architectural cycle.
        for cyc in formatted_cycles:
            dirs = _unique_dirs(cyc["files"])
            cyc["directories"] = len(dirs)
            if not cyc["actionable"]:
                cyc["severity"] = INFO
            elif len(cyc["files"]) > 3:
                cyc["severity"] = CRITICAL
            else:
                cyc["severity"] = WARNING
            sev_counts[cyc["severity"]] += 1

        actionable_cycles = [c for c in formatted_cycles if c.get("actionable")]
        ignored_cycles = [c for c in formatted_cycles if not c.get("actionable")]

        # God component severity: location-aware thresholds
        actionable_count = 0
        utility_count = 0
        for g in god_items:
            is_util = _is_utility_path(g["file"])
            g["category"] = "utility" if is_util else "actionable"
            if is_util:
                utility_count += 1
                # Relaxed thresholds for utilities (3x)
                if g["degree"] > 150:
                    g["severity"] = CRITICAL
                elif g["degree"] > 90:
                    g["severity"] = WARNING
                else:
                    g["severity"] = INFO
            else:
                actionable_count += 1
                # Standard thresholds for non-utility code
                if g["degree"] > 50:
                    g["severity"] = CRITICAL
                elif g["degree"] > 30:
                    g["severity"] = WARNING
                else:
                    g["severity"] = INFO
            sev_counts[g["severity"]] += 1

        # Sort: actionable first, then utilities; within each group by degree desc
        god_items.sort(
            key=lambda g: (
                0 if g["category"] == "actionable" else 1,
                -g["degree"],
            )
        )

        # Bottleneck severity: percentile-based thresholds.
        # Utilities get 1.5x multiplied thresholds (higher bar for severity).
        _BN_UTIL_MULT = 1.5
        bn_actionable = 0
        bn_utility = 0
        for b in bn_items:
            is_util = _is_utility_path(b["file"])
            b["category"] = "utility" if is_util else "actionable"
            mult = _BN_UTIL_MULT if is_util else 1.0
            if is_util:
                bn_utility += 1
            else:
                bn_actionable += 1
            if b["betweenness"] > bn_p90 * mult:
                b["severity"] = CRITICAL
            elif b["betweenness"] > bn_p70 * mult:
                b["severity"] = WARNING
            else:
                b["severity"] = INFO
            sev_counts[b["severity"]] += 1

        # Sort: actionable first, then utilities; within each group by betweenness desc
        bn_items.sort(
            key=lambda b: (
                0 if b["category"] == "actionable" else 1,
                -b["betweenness"],
            )
        )

        for v in violations:
            v["severity"] = WARNING
            sev_counts[WARNING] += 1

        # --- W151: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set sits after
        # severity classification (so evidence carries the
        # severity/category fields) but before the gate / sarif /
        # json / text display branches — the registry stays
        # comprehensive regardless of how a particular invocation
        # slices the view. Wrapped in try/except sqlite3.OperationalError
        # so a pre-W89 DB (without the ``findings`` table) silently
        # no-ops rather than crashing the standard health command path.
        if persist:
            # W607-BA: wrap ``_emit_health_findings`` substrate with the
            # per-phase marker emitter. The pre-W607-BA ``except
            # sqlite3.OperationalError: pass`` silently swallowed the
            # schema-missing case -- a legitimate degrade path but one
            # that left no marker on the envelope. W607-BA surfaces a
            # ``health_findings_emit_failed:`` marker on EVERY exception
            # class while keeping the pre-W89 schema-missing degrade
            # silent (sqlite3.OperationalError is caught inline first so
            # it doesn't flip partial_success on legitimate degrade).
            def _do_emit_findings() -> None:
                _emit_health_findings(
                    conn,
                    formatted_cycles,
                    god_items,
                    bn_items,
                    violations,
                    HEALTH_DETECTOR_VERSION,
                    v_lookup=v_lookup,
                    raw_by_formatted_cycle=raw_by_formatted_cycle,
                )
                conn.commit()

            try:
                _do_emit_findings()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — legitimate
                # degrade, stay silent (preserves W89 contract).
                pass
            except Exception as exc:  # noqa: BLE001
                # Any OTHER exception surfaces via W607-BA so the agent
                # sees the failure rather than a silent no-op.
                _w607ba_warnings_out.append(f"health_findings_emit_failed:{type(exc).__name__}:{exc}")

        # --- Tangle ratio ---
        # W607-M: per-phase substrate guard for total_symbols query.
        try:
            total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 1
        except Exception as exc:
            _w607m_warnings_out.append(f"health_tangle_failed:{type(exc).__name__}:{exc}")
            total_symbols = 1
        cycle_symbol_ids = set()
        for scc, cyc_info in raw_by_formatted_cycle:
            if cyc_info.get("actionable"):
                cycle_symbol_ids.update(scc)
        tangle_ratio = round(len(cycle_symbol_ids) / total_symbols * 100, 1)

        # --- Propagation Cost (MacCormack et al. 2006) ---
        # Fraction of the system affected by a change to any component.
        # Uses transitive closure: PC = sum(V) / n^2
        # W607-M: per-phase substrate guard for propagation_cost.
        try:
            prop_cost = propagation_cost(G)
        except Exception as exc:
            _w607m_warnings_out.append(f"health_propagation_cost_failed:{type(exc).__name__}:{exc}")
            prop_cost = 0.0

        # --- Algebraic Connectivity (Fiedler 1973) ---
        # Second-smallest Laplacian eigenvalue; low = fragile architecture
        # W607-M: per-phase substrate guard for algebraic_connectivity.
        # fiedler_failed distinguishes "couldn't compute" (missing numpy/scipy
        # substrate) from a legitimate 0.0 reading (genuinely disconnected
        # graph), so the display can show n/a instead of a misleading 0.0000.
        #
        # UFS3 — JSON-mode stdout-leak fix. ``algebraic_connectivity`` does
        # NOT raise on a missing-numpy/scipy substrate or eigensolver
        # divergence; it RETURNS the 0.0 sentinel and emits a
        # ``RuntimeWarning`` (graph/cycles.py). The ``except`` below only
        # catches genuine exceptions — the warning escapes it. Relying on
        # cli.py's global ``warnings.showwarning`` override is unsafe: that
        # override only installs when the current hook is the stdlib
        # default, so a leaked / non-default ``showwarning`` (sibling-test
        # leak, pytest capture, a host that routes warnings to stdout) lets
        # the free-form ``RuntimeWarning`` text land on stdout and corrupt
        # the ``--json`` envelope. Capture the warning at the call site with
        # ``catch_warnings(record=True)`` so it can NEVER reach
        # ``showwarning``, and fold any captured warning into the structured
        # ``_w607m_warnings_out`` channel that already feeds ``warnings_out``.
        import warnings as _warnings  # local — keeps the import surface lazy

        fiedler_failed = False
        try:
            with _warnings.catch_warnings(record=True) as _fiedler_warns:
                _warnings.simplefilter("always")
                fiedler = algebraic_connectivity(G)
            for _w in _fiedler_warns:
                _w607m_warnings_out.append(f"health_algebraic_connectivity_warning:{_w.category.__name__}:{_w.message}")
                # A RuntimeWarning from algebraic_connectivity means the
                # spectral solve was unavailable/diverged — the 0.0 is a
                # sentinel, not a real disconnected-graph reading.
                if issubclass(_w.category, RuntimeWarning):
                    fiedler_failed = True
        except Exception as exc:
            _w607m_warnings_out.append(f"health_algebraic_connectivity_failed:{type(exc).__name__}:{exc}")
            fiedler = 0.0
            fiedler_failed = True

        # --- Composite health score (0-100) ---
        # Weighted geometric mean: score = 100 * product(h_i ^ w_i)
        # Non-compensatory: a zero in any dimension cannot be masked by
        # high scores in others, unlike a linear sum.  Each factor h_i
        # is a "health fraction" in (0, 1] derived from a sigmoid:
        #   h = e^(-signal / scale)   (1 = pristine, → 0 = worst)
        # Weights sum to 1 and encode relative importance.
        def _health_factor(value, scale):
            """Sigmoid health factor: 1 for no issues, → 0 for many."""
            return math.exp(-value / scale) if scale > 0 else 1.0

        # Score signals — count *actionable* items only. Utilities
        # (string/path/datetime helpers) are expected to have high fan-in
        # and would dominate the formula otherwise. Per dogfood notes
        # 2026-05-01: this repo had 50 god components total but 27 were
        # expected utilities; the old formula penalised the score for
        # all 50 and produced a misleading 2/100 verdict. The display
        # already classifies them ("23 actionable, 27 expected utilities");
        # the score should match the display.
        god_actionable = [g for g in god_items if g.get("category") == "actionable"]
        god_critical = sum(1 for g in god_actionable if g.get("severity") == CRITICAL)
        # Normalise by codebase size so a 14k-symbol repo with 23 actionable
        # god components (0.16%) doesn't score the same as a 100-symbol
        # repo with 23 (23%). 1k symbols is the unit; signal scales linearly.
        size_norm = max(1.0, total_symbols / 1000.0)
        god_signal = (god_critical * 3 + len(god_actionable) * 0.5) / size_norm
        bn_actionable_items = [b for b in bn_items if b.get("category") == "actionable"]
        bn_critical = sum(1 for b in bn_actionable_items if b.get("severity") == CRITICAL)
        bn_signal = (bn_critical * 2 + len(bn_actionable_items) * 0.3) / size_norm

        # W607-M: per-phase substrate guard for imported coverage helper.
        try:
            coverage_import = imported_coverage_overview(conn)
        except Exception as exc:
            _w607m_warnings_out.append(f"health_imported_coverage_failed:{type(exc).__name__}:{exc}")
            coverage_import = {}

        # Base factors (weights sum to 1.0 before optional imported coverage).
        # Scales tuned post-normalisation so a normal repo (low percent of
        # actionable god components) scores in the 60-90 range.
        base_factors = [
            (_health_factor(tangle_ratio, 10), 0.30),  # tangle ratio
            (_health_factor(god_signal, 1.5), 0.20),  # god components (normalised /1k symbols)
            (_health_factor(bn_signal, 1.0), 0.15),  # bottlenecks (normalised /1k symbols)
            (_health_factor(len(violations), 5), 0.15),  # layer violations
        ]
        # File-level health: map avg [0-10] to a factor
        # W607-M: per-phase substrate guard. Mirrors the pre-W607-M
        # try/except floor (1.0) so the score behaviour is unchanged on
        # the happy path; the only addition is the disclosure marker.
        try:
            avg_file_health = conn.execute(
                "SELECT AVG(health_score) FROM file_stats WHERE health_score IS NOT NULL"
            ).fetchone()[0]
            if avg_file_health is not None:
                base_factors.append((min(1.0, avg_file_health / 10.0), 0.20))
            else:
                base_factors.append((1.0, 0.20))
        except Exception as exc:
            _w607m_warnings_out.append(f"health_file_health_failed:{type(exc).__name__}:{exc}")
            base_factors.append((1.0, 0.20))

        # Imported test coverage (#134): when available, reserve 10% score weight
        # and scale existing weights to 90%. This avoids over-dominance while
        # still penalizing high-centrality codebases with low real coverage.
        if coverage_import.get("coverable_lines", 0) > 0 and coverage_import.get("coverage_pct") is not None:
            cov_factor = min(1.0, max(0.05, coverage_import["coverage_pct"] / 100.0))
            _health_factors = [(h, w * 0.90) for h, w in base_factors]
            _health_factors.append((cov_factor, 0.10))
        else:
            _health_factors = base_factors

        # Weighted geometric mean in log space
        # W607-BA: wrap the FLAGSHIP 0-100 score composition (CLAUDE.md
        # LAW 6 canonical example) so a math overflow / domain error in
        # the geometric mean surfaces as a marker rather than crashing
        # the gate. Default 0 keeps the verdict scorer compositable on
        # the degraded path.
        def _compute_health_score() -> int:
            log_score = sum(w * math.log(max(h, 1e-9)) for h, w in _health_factors)
            return max(0, min(100, int(100 * math.exp(log_score))))

        health_score = _run_check_ba("compute_health_score", _compute_health_score, default=0)
        if health_score is None:
            health_score = 0

        # record per-factor contributions so --explain can show
        # WHY the score is what it is. Each factor's "loss" (1 - h) is
        # what's pulling the score down; the weight scales the impact.
        _factor_names = ["tangle_ratio", "god_components", "bottlenecks", "layer_violations", "file_health"]
        if len(_health_factors) > len(_factor_names):
            _factor_names.append("imported_coverage")
        score_breakdown = []
        for (h, w), name in zip(_health_factors, _factor_names):
            loss_pp = round((1 - h) * w * 100, 1)
            score_breakdown.append(
                {
                    "factor": name,
                    "health": round(h, 3),
                    "weight": round(w, 2),
                    "loss_pp": loss_pp,
                }
            )
        score_breakdown.sort(key=lambda b: b["loss_pp"], reverse=True)

        # — name the dominant issue category. The four
        # category counts (cycles, god_components, bottlenecks,
        # layer_violations) lead the user at a fix; the largest is
        # the highest-leverage next action. Without this hint a user
        # sees "25 critical" and has to dig into the breakdown to
        # know whether to fix cycles first or god components first.
        _cat_counts = {
            "cycles": sum(1 for c in actionable_cycles if c.get("severity") == CRITICAL),
            "god_components": sum(1 for g in god_items if g.get("severity") == CRITICAL),
            "bottlenecks": sum(1 for b in bn_items if b.get("severity") == CRITICAL),
            "layer_violations": sum(1 for v in violations if v.get("severity") == CRITICAL),
        }
        _top_category, _top_count = max(_cat_counts.items(), key=lambda x: x[1])
        _focus_hint = f", focus: {_top_category}" if _top_count > 0 else ""
        # when 0 actionable items remain (everything was
        # ignored by category or framework filter), the verdict should say
        # so explicitly. Otherwise users see "29 critical issues" but the
        # next line says "0 actionable" — confusing.
        if actionable_count == 0 and sev_counts[CRITICAL] > 0:
            _focus_hint = " (all flagged as utility / non-actionable)"

        # --- Verdict ---
        # W607-BA: wrap the verdict composition itself. cmd_health's
        # one-line verdict ("Healthy 32/100 with 12 cycles") is the
        # canonical CLAUDE.md LAW 6 example -- the verdict MUST work
        # without any other field. Wrap the composition so a string
        # format / lookup error surfaces a marker rather than
        # propagating up to crash the gate. The default fallback
        # verdict still satisfies LAW 6 (single-line + score
        # included).
        def _compose_verdict() -> str:
            if health_score >= 80:
                return f"Healthy codebase ({health_score}/100) — {sev_counts[CRITICAL]} critical issues{_focus_hint}"
            if health_score >= 60:
                return (
                    f"Fair codebase ({health_score}/100) — "
                    f"{sev_counts[CRITICAL]} critical, "
                    f"{sev_counts[WARNING]} warnings{_focus_hint}"
                )
            if health_score >= 40:
                return (
                    f"Needs attention ({health_score}/100) — "
                    f"{sev_counts[CRITICAL]} critical, "
                    f"{sev_counts[WARNING]} warnings{_focus_hint}"
                )
            return (
                f"Unhealthy codebase ({health_score}/100) — "
                f"{sev_counts[CRITICAL]} critical, "
                f"{sev_counts[WARNING]} warnings{_focus_hint}"
            )

        verdict = _run_check_ba(
            "compose_verdict",
            _compose_verdict,
            default=f"Health score {health_score}/100 (verdict composition degraded)",
        )
        if verdict is None:
            verdict = f"Health score {health_score}/100 (verdict composition degraded)"

        # --- Baseline-diff mode ---
        # When --baseline is set, delegate to the dedicated helper and
        # exit before gate/sarif/json/text branches. Existing behaviour
        # is untouched when the flag is absent.
        if baseline_ref:
            # W607-BA: wrap the baseline-diff emit substrate. A raise
            # inside ``_emit_baseline_diff`` surfaces as a structured
            # marker so the early-return doesn't bypass the disclosure
            # axis. The function's contract is "emit the diff envelope
            # itself" so on the degraded path we still return early
            # (the partial-diff is the substrate, not something we can
            # recompose here).
            _run_check_ba(
                "baseline_diff_emit",
                _emit_baseline_diff,
                conn=conn,
                baseline_ref=baseline_ref,
                health_score=health_score,
                actionable_cycles=actionable_cycles,
                god_items=god_items,
                bn_items=bn_items,
                violations=violations,
                json_mode=json_mode,
                token_budget=token_budget,
            )
            return

        # --- Quality Gate ---
        if gate:
            _gate_warnings: list[str] = []
            # W1030-followup-B: opt into the closed-enum LoadStatus so
            # ``summary.config_state`` discloses what the loader saw on
            # disk (``missing`` / ``empty_file`` / ``empty_yaml`` /
            # ``ok`` / degraded). Mirrors the cmd_alerts + cmd_budget
            # surfacing pattern landed in W1030-followup-A.
            # W607-BA: wrap the gate-config loader substrate. The
            # default tuple keeps the gate behaviour compositable on
            # the degraded path (empty config -> baseline thresholds
            # apply, ``config_state="load_error"``).
            _gate_cfg_pair = _run_check_ba(
                "gate_config_load",
                _load_gate_config_with_status,
                warnings_out=_gate_warnings,
                default=({}, "load_error"),
            )
            if _gate_cfg_pair is None:
                _gate_cfg_pair = ({}, "load_error")
            gate_config, _gate_config_state = _gate_cfg_pair
            gate_results = []
            all_passed = True

            # Health minimum
            h_min = gate_config.get("health_min", 60)
            passed = health_score >= h_min
            gate_results.append({"gate": "health_min", "threshold": h_min, "actual": health_score, "passed": passed})
            if not passed:
                all_passed = False

            # Optional gates
            c_max = gate_config.get("complexity_max")
            if c_max is not None:
                # W607-BA: replaces the pre-W607-BA bare ``except
                # Exception: pass`` that silently swallowed any DB
                # failure on the complexity_max gate. Now the marker
                # rides on warnings_out so a degraded gate doesn't
                # silently PASS with max_cc=0 against a DB that
                # genuinely failed to query complexity.
                def _query_complexity_max() -> int:
                    row = conn.execute("SELECT MAX(complexity) FROM symbols WHERE complexity IS NOT NULL").fetchone()
                    return (row[0] if row is not None else 0) or 0

                max_cc = _run_check_ba("gate_complexity_query", _query_complexity_max, default=0)
                if max_cc is None:
                    max_cc = 0
                passed = max_cc <= c_max
                gate_results.append(
                    {
                        "gate": "complexity_max",
                        "threshold": c_max,
                        "actual": max_cc,
                        "passed": passed,
                    }
                )
                if not passed:
                    all_passed = False

            cyc_max = gate_config.get("cycle_max")
            if cyc_max is not None:
                passed = len(actionable_cycles) <= cyc_max
                gate_results.append(
                    {
                        "gate": "cycle_max",
                        "threshold": cyc_max,
                        "actual": len(actionable_cycles),
                        "passed": passed,
                    }
                )
                if not passed:
                    all_passed = False

            t_max = gate_config.get("tangle_max")
            if t_max is not None:
                passed = tangle_ratio <= t_max
                gate_results.append(
                    {
                        "gate": "tangle_max",
                        "threshold": t_max,
                        "actual": tangle_ratio,
                        "passed": passed,
                    }
                )
                if not passed:
                    all_passed = False

            if json_mode:
                gate_summary: dict = {
                    "verdict": verdict,
                    "health_score": health_score,
                    "gate_passed": all_passed,
                    "imported_coverage_pct": coverage_import.get("coverage_pct"),
                    # W331: gate-mode envelope also reports a score.
                    "health_score_definition": HEALTH_SCORE_DEFINITION,
                    # W1030-followup-B: closed-enum LoadStatus disclosure
                    # so agents can tell "no .roam-gates.yml configured
                    # yet" (missing -> baseline gates silently) from
                    # ".roam-gates.yml exists but is empty" (empty_file
                    # / empty_yaml -> baseline gates AND user probably
                    # meant to configure something) from ".roam-gates.yml
                    # is broken" (parse_error / wrong_root_type -- already
                    # accompanied by a warning in warnings_out).
                    "config_state": _gate_config_state,
                }
                # W1030-followup-B: a degraded config_state flips
                # partial_success regardless of warning emission, mirroring
                # cmd_alerts + cmd_budget. The user's quality-gate
                # thresholds did not materialize — agents must see the
                # discard, not just the PASS / FAIL verdict.
                _gate_config_degraded = _gate_config_state in (
                    "parse_error",
                    "wrong_root_type",
                    "read_error",
                    "schema_invalid",
                )
                # W607-M + W607-BA: merge DB-shape warnings with
                # gate-config warnings AND W607-BA additive per-substrate
                # markers on the same ``warnings_out`` axis. The W607-M
                # / W607-BA markers use the ``health_<phase>_failed:...``
                # shape; the gate-config markers carry the legacy
                # ``health-gate: ...`` shape; all three live on the same
                # bucket so consumers reading the gate envelope see ALL
                # degradation lineage on a single field.
                _merged_warnings: list[str] = (
                    list(_gate_warnings) + list(_w607m_warnings_out) + list(_w607ba_warnings_out)
                )
                if _merged_warnings:
                    # W1052 + W607-M: surface loader warnings AND
                    # DB-shape substrate-degrade markers so the agent
                    # doesn't see PASS verdicts silently produced
                    # against degraded inputs.
                    gate_summary["warnings_out"] = list(_merged_warnings)
                    gate_summary["partial_success"] = True
                elif _gate_config_degraded:
                    gate_summary["partial_success"] = True
                # W1030-followup-B: agent_contract.facts state disclosure.
                # LAW 4 anchored on concrete-noun terminals ("gates" /
                # "defaults"). Mirrors the cmd_alerts vocabulary.
                _gate_facts: list[str] = []
                if _gate_config_state == "missing":
                    _gate_facts.append("no .roam-gates.yml configured; using baseline gates")
                elif _gate_config_state == "empty_file":
                    _gate_facts.append("empty .roam-gates.yml stub on disk; using baseline gates")
                elif _gate_config_state == "empty_yaml":
                    _gate_facts.append("comment-only .roam-gates.yml on disk; using baseline gates")
                elif _gate_config_degraded:
                    _gate_facts.append(f"health config rejected ({_gate_config_state}); using baseline gates")
                envelope_kwargs: dict = dict(
                    budget=token_budget,
                    summary=gate_summary,
                    gate_results=gate_results,
                    health_score=health_score,
                    imported_coverage_pct=coverage_import.get("coverage_pct"),
                    imported_coverage_files=coverage_import.get("files_with_coverage", 0),
                )
                if _gate_facts:
                    envelope_kwargs["agent_contract"] = {
                        "facts": _gate_facts,
                        "next_commands": ["roam health"],
                    }
                # W607-M: top-level ``warnings_out`` mirror on the gate
                # envelope. The preserved-list-field discipline at
                # ``_ALWAYS_PRESERVED_LIST_FIELDS`` requires the top-level
                # mirror so the field survives detail-mode list-payload
                # stripping. Matches W607-A..L mirror parity.
                if _merged_warnings:
                    envelope_kwargs["warnings_out"] = list(_merged_warnings)
                envelope = json_envelope("health", **envelope_kwargs)
                click.echo(to_json(envelope))
                if not all_passed:
                    from roam.exit_codes import GateFailureError

                    raise GateFailureError("Quality gate failed")
                return

            # Text output for gate mode
            click.echo(f"VERDICT: {verdict}\n")
            click.echo("=== Quality Gates ===")
            for gr in gate_results:
                status = "PASS" if gr["passed"] else "FAIL"
                click.echo(f"  [{status}] {gr['gate']}: {gr['actual']} (threshold: {gr['threshold']})")

            if _gate_warnings:
                click.echo()
                for w in _gate_warnings:
                    click.echo(f"WARNING: {w}")

            if all_passed:
                click.echo("\nAll gates passed.")
            else:
                failed = [g["gate"] for g in gate_results if not g["passed"]]
                click.echo(f"\nFailed gates: {', '.join(failed)}")
                from roam.exit_codes import GateFailureError

                raise GateFailureError(f"Quality gate failed: {', '.join(failed)}")
            return

        if sarif_mode:
            from roam.output.sarif import health_to_sarif, write_sarif

            # W1084 — mirror of W1060 cmd_complexity: collect any
            # silent-fallback warnings from the gate-config loader so
            # malformed `.roam-gates.yml` shape disclosure rides on the
            # SARIF emit too. Gate config governs CI health thresholds;
            # SARIF is consumed by CI; both surfaces must expose the
            # same loader malformations (Pattern 2 silent fallback).
            # Default-False to keep pre-W1084 SARIF bytes when
            # `.roam-gates.yml` is well-formed or absent.
            _gate_warnings: list[str] = []
            # W607-BA: wrap the SARIF-mode gate-config loader on the
            # same axis as the JSON-gate branch. SARIF is consumed by
            # CI; a silent loader crash here would let a degraded
            # config emit unannotated SARIF rows.
            _run_check_ba(
                "gate_sarif_loader",
                _load_gate_config,
                warnings_out=_gate_warnings,
                default=None,
            )

            # W718: lowercase canonical severity (W547). The SARIF
            # projection in ``health_to_sarif`` calls ``_to_level`` with
            # ``severity.upper()`` so either case feeds the projection
            # correctly; we keep the canonical lowercase form on the
            # input so the SARIF input shape matches the JSON envelope.
            issues = {
                "cycles": [
                    {
                        "size": c["size"],
                        "severity": _normalise_health_severity(c.get("severity") or WARNING),
                        "symbols": [s["name"] for s in c["symbols"]],
                        "files": c["files"],
                    }
                    for c in formatted_cycles
                ],
                "god_components": [
                    {
                        "name": g["name"],
                        "kind": g["kind"],
                        "degree": g["degree"],
                        "file": g["file"],
                        "severity": _normalise_health_severity(g.get("severity") or WARNING),
                    }
                    for g in god_items
                ],
                "bottlenecks": [
                    {
                        "name": b["name"],
                        "kind": b["kind"],
                        "betweenness": b["betweenness"],
                        "file": b["file"],
                        "severity": _normalise_health_severity(b.get("severity") or WARNING),
                    }
                    for b in bn_items
                ],
                "layer_violations": [
                    {
                        "severity": WARNING,
                        "source": v_lookup.get(v["source"], {}).get("name", "?"),
                        "source_layer": v["source_layer"],
                        "target": v_lookup.get(v["target"], {}).get("name", "?"),
                        "target_layer": v["target_layer"],
                    }
                    for v in violations
                ],
            }
            # W607-BA: wrap the SARIF projection substrate. A failure
            # in ``health_to_sarif`` would otherwise crash CI with a
            # generic traceback; the marker default ({}) plus the
            # degraded write_sarif call still emits a valid SARIF
            # envelope (zero results) so the CI consumer doesn't
            # crash on parse.
            sarif = _run_check_ba(
                "sarif_emit",
                health_to_sarif,
                issues,
                emit_runtime_notifications=bool(_gate_warnings),
                warnings_out=_gate_warnings,
                default={
                    "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
                    "version": "2.1.0",
                    "runs": [],
                },
            )
            if sarif is None:
                sarif = {
                    "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
                    "version": "2.1.0",
                    "runs": [],
                }
            click.echo(write_sarif(sarif))
            return

        if json_mode:
            cycle_severity = _severity_counts(actionable_cycles)
            god_severity = _severity_counts(god_items)
            bottleneck_severity = _severity_counts(bn_items)
            layer_severity = _severity_counts(violations)
            j_issue_count = len(actionable_cycles) + len(god_items) + len(bn_items) + len(violations)
            # W607-BA: wrap the next-steps suggester substrate. A
            # failure here would lose the agent-contract guidance
            # rows; default [] keeps the envelope shape but surfaces
            # the marker so the agent knows the suggestion list is
            # empty due to degradation, not because no steps apply.
            next_steps = _run_check_ba(
                "suggest_next_steps_call",
                suggest_next_steps,
                "health",
                {
                    "score": health_score,
                    "critical_issues": sev_counts[CRITICAL],
                    "cycles": len(actionable_cycles),
                },
                default=[],
            )
            if next_steps is None:
                next_steps = []
            envelope = json_envelope(
                "health",
                budget=token_budget,
                summary={
                    "verdict": verdict,
                    "health_score": health_score,
                    "tangle_ratio": tangle_ratio,
                    "propagation_cost": prop_cost,
                    # W607-M honesty: export null (not a fake 0.0 sentinel) when the
                    # numpy+scipy substrate is missing, with a companion availability
                    # flag so a programmatic consumer can tell "couldn't compute"
                    # apart from a legitimate 0.0 disconnected-graph reading. The text
                    # display already shows "n/a (requires numpy+scipy)" via
                    # fiedler_failed; this makes the JSON export equally honest. The
                    # MCP output schema already declares this field number|null.
                    "algebraic_connectivity": (None if fiedler_failed else fiedler),
                    "algebraic_connectivity_available": not fiedler_failed,
                    "issue_count": j_issue_count,
                    "severity": sev_counts,
                    "category_severity": {
                        "cycles": cycle_severity,
                        "god_components": god_severity,
                        "bottlenecks": bottleneck_severity,
                        "layer_violations": layer_severity,
                    },
                    "actionable_cycles": len(actionable_cycles),
                    "ignored_cycles": len(ignored_cycles),
                    "total_cycles": len(formatted_cycles),
                    # Vocabulary aliases for cross-command agreement with
                    # `describe` and `agent-export` (Pattern 3 fix).
                    "cycles_total": len(formatted_cycles),
                    "cycles_actionable": len(actionable_cycles),
                    "god_components": len(god_items),
                    "cycles_definition": cycles_definition(),
                    "god_components_definition": god_components_definition(),
                    # W331: stamp the canonical score + tangle definitions
                    # next to the existing cycles + god-components ones.
                    "health_score_definition": HEALTH_SCORE_DEFINITION,
                    "tangle_ratio_definition": TANGLE_RATIO_DEFINITION,
                    "imported_coverage_pct": coverage_import.get("coverage_pct"),
                    "imported_coverage_files": coverage_import.get("files_with_coverage", 0),
                },
                next_steps=next_steps,
                health_score=health_score,
                tangle_ratio=tangle_ratio,
                propagation_cost=prop_cost,
                algebraic_connectivity=(None if fiedler_failed else fiedler),
                algebraic_connectivity_available=not fiedler_failed,
                issue_count=j_issue_count,
                severity=sev_counts,
                category_severity={
                    "cycles": cycle_severity,
                    "god_components": god_severity,
                    "bottlenecks": bottleneck_severity,
                    "layer_violations": layer_severity,
                },
                actionable_cycles=len(actionable_cycles),
                ignored_cycles=len(ignored_cycles),
                total_cycles=len(formatted_cycles),
                cycles_total=len(formatted_cycles),
                cycles_actionable=len(actionable_cycles),
                imported_coverage_pct=coverage_import.get("coverage_pct"),
                imported_coverage_files=coverage_import.get("files_with_coverage", 0),
                imported_covered_lines=coverage_import.get("covered_lines", 0),
                imported_coverable_lines=coverage_import.get("coverable_lines", 0),
                score_breakdown=score_breakdown,
                framework_filtered=filtered_count,
                actionable_count=actionable_count,
                utility_count=utility_count,
                cycles=[
                    {
                        "size": c["size"],
                        "severity": c["severity"],
                        "directories": c["directories"],
                        "symbols": [s["name"] for s in c["symbols"]],
                        "files": c["files"],
                    }
                    for c in formatted_cycles
                ],
                cycle_break_suggestions=[
                    {
                        "source": bs["source_name"],
                        "target": bs["target_name"],
                        "reason": bs["reason"],
                        "scc_size": bs["scc_size"],
                    }
                    for bs in break_suggestions
                ],
                god_components=[{**g, "severity": g["severity"], "category": g["category"]} for g in god_items],
                bottleneck_thresholds={
                    "p70": round(bn_p70, 1),
                    "p90": round(bn_p90, 1),
                    "utility_multiplier": _BN_UTIL_MULT,
                    "population": len(all_bw),
                },
                bottlenecks=[{**b, "severity": b["severity"], "category": b["category"]} for b in bn_items],
                layer_violations=[
                    {
                        "severity": WARNING,
                        "source": v_lookup.get(v["source"], {}).get("name", "?"),
                        "source_layer": v["source_layer"],
                        "target": v_lookup.get(v["target"], {}).get("name", "?"),
                        "target_layer": v["target_layer"],
                    }
                    for v in violations
                ],
            )
            # Round 4 #20 / U: top-level index_status field so JSON
            # consumers see the staleness warning without scanning
            # nested sections.
            from roam.commands.resolve import index_status as _index_status_json

            _idx_status_json = _index_status_json()
            if _idx_status_json is not None:
                envelope["index_status"] = _idx_status_json
            # W607-M + W607-BA: thread DB-shape AND additive-substrate
            # markers onto the main JSON envelope. Mirror discipline:
            # top-level ``warnings_out`` (so the preserved-list field
            # survives detail-mode list-payload stripping) +
            # summary.warnings_out + summary.partial_success = True.
            # Empty bucket -> no key added -> byte-identical envelope
            # (W607-A..L parity discipline). Both wave's markers share
            # the ``health_*`` family and the same warnings_out axis.
            _merged_main_warnings: list[str] = list(_w607m_warnings_out) + list(_w607ba_warnings_out)
            if _merged_main_warnings:
                envelope["warnings_out"] = list(_merged_main_warnings)
                _smry = envelope.get("summary")
                if isinstance(_smry, dict):
                    _smry["warnings_out"] = list(_merged_main_warnings)
                    _smry["partial_success"] = True
            if not detail:
                envelope = strip_list_payloads(envelope)
            click.echo(to_json(envelope))
            return

        # --- Text output ---
        # Round 4 #20 / U: surface the staleness warning BEFORE the
        # verdict so an agent reading top-down can't miss it. The
        # health composite leans on git-derived metrics (churn,
        # co-change), so an out-of-date index quietly skews all of them.
        from roam.commands.resolve import index_status as _index_status

        _idx_status = _index_status()
        if _idx_status and not _idx_status.get("fresh"):
            click.echo(f"NOTE: {_idx_status['hint']}\n")
        click.echo(f"VERDICT: {verdict}\n")
        # when --explain, decompose the score before everything
        # else so the user understands which factor is dragging it down.
        if explain:
            click.echo("=== Score Breakdown (sorted by impact) ===")
            click.echo("Factor               Health  Weight  Loss (pp)")
            click.echo("-------------------  ------  ------  ---------")
            for b in score_breakdown:
                click.echo(f"{b['factor']:<19}  {b['health']:>6.3f}  {b['weight']:>6.2f}  {b['loss_pp']:>9.1f}")
            click.echo()
        issue_count = len(actionable_cycles) + len(god_items) + len(bn_items) + len(violations)
        parts = []
        if formatted_cycles:
            cycle_detail = f"{len(actionable_cycles)} actionable cycle{'s' if len(actionable_cycles) != 1 else ''}"
            if ignored_cycles:
                cycle_detail += (
                    f", {len(ignored_cycles)} local/test cycle{'s' if len(ignored_cycles) != 1 else ''} ignored"
                )
            parts.append(cycle_detail)
        if god_items:
            god_detail = f"{len(god_items)} god component{'s' if len(god_items) != 1 else ''}"
            god_detail += f" ({actionable_count} actionable, {utility_count} expected utilities)"
            parts.append(god_detail)
        if bn_items:
            bn_detail = f"{len(bn_items)} bottleneck{'s' if len(bn_items) != 1 else ''}"
            bn_detail += f" ({bn_actionable} actionable, {bn_utility} expected utilities)"
            parts.append(bn_detail)
        if violations:
            parts.append(f"{len(violations)} layer violation{'s' if len(violations) != 1 else ''}")
        click.echo(
            f"Health Score: {health_score}/100  |  "
            f"Tangle: {tangle_ratio}% ({len(cycle_symbol_ids)}/{total_symbols} symbols in cycles)"
        )
        _ac_str = "n/a (requires numpy+scipy)" if fiedler_failed else f"{fiedler:.4f}"
        click.echo(f"Propagation Cost: {prop_cost:.1%}  |  Algebraic Connectivity: {_ac_str}")
        if coverage_import.get("coverable_lines", 0) > 0:
            click.echo(
                f"Imported Coverage: {coverage_import['coverage_pct']}% "
                f"({coverage_import['covered_lines']}/{coverage_import['coverable_lines']} lines, "
                f"{coverage_import['files_with_coverage']} files)"
            )
        click.echo()
        if issue_count == 0:
            click.echo("Issues: None detected")
            if ignored_cycles:
                click.echo(f"  ({len(ignored_cycles)} informational local/test cycle(s) ignored for scoring)")
        else:
            # W718: ``sev_counts`` keys are lowercase canonical labels.
            # Text output preserves the historical UPPER-cased polish
            # via ``.upper()`` — display polish, not vocabulary.
            sev_parts = []
            if sev_counts[CRITICAL]:
                sev_parts.append(f"{sev_counts[CRITICAL]} {CRITICAL.upper()}")
            if sev_counts[WARNING]:
                sev_parts.append(f"{sev_counts[WARNING]} {WARNING.upper()}")
            if sev_counts[INFO]:
                sev_parts.append(f"{sev_counts[INFO]} {INFO.upper()}")
            click.echo(f"Health: {issue_count} issue{'s' if issue_count != 1 else ''} — {', '.join(sev_parts)}")
            detail_str = ", ".join(parts)
            if filtered_count:
                detail_str += f"; {filtered_count} framework symbols filtered"
            click.echo(f"  ({detail_str})")
            click.echo(
                "  Breakdown: "
                f"cycles [{_format_severity_counts(_severity_counts(actionable_cycles))}], "
                f"god [{_format_severity_counts(_severity_counts(god_items))}], "
                f"bottlenecks [{_format_severity_counts(_severity_counts(bn_items))}], "
                f"layers [{_format_severity_counts(_severity_counts(violations))}]"
            )
        click.echo()

        # --- Summary mode (no --detail): only show top 3 issues ---
        if not detail:
            top_critical = [
                item
                for item_list in [
                    [(c, "cycle") for c in formatted_cycles if c.get("severity") == CRITICAL],
                    [(g, "god") for g in god_items if g.get("severity") == CRITICAL],
                    [(b, "bottleneck") for b in bn_items if b.get("severity") == CRITICAL],
                ]
                for item in item_list
            ]
            if top_critical:
                click.echo("Top CRITICAL issues (run `roam --detail health` for the full breakdown):")
                for item, kind in top_critical[:3]:
                    if kind == "cycle":
                        names = [s["name"] for s in item["symbols"][:3]]
                        click.echo(f"  cycle ({item['size']} symbols): {', '.join(names)}")
                    elif kind == "god":
                        click.echo(
                            f"  god component: {item['name']} ({abbrev_kind(item['kind'])}, degree={item['degree']})"
                        )
                    elif kind == "bottleneck":
                        click.echo(
                            f"  bottleneck: {item['name']} ({abbrev_kind(item['kind'])}, betweenness={item['betweenness']})"
                        )
            else:
                click.echo(
                    "(run `roam --detail health` for the full breakdown of "
                    "cycles, god components, bottlenecks, and layer violations)"
                )
            return

        click.echo("=== Cycles ===")
        if formatted_cycles:
            for i, cyc in enumerate(formatted_cycles, 1):
                names = [s["name"] for s in cyc["symbols"]]
                sev = cyc["severity"]
                dir_note = f", {cyc['directories']} dir{'s' if cyc['directories'] != 1 else ''}"
                click.echo(f"  [{sev}] cycle {i} ({cyc['size']} symbols{dir_note}): {', '.join(names[:10])}")
                if len(names) > 10:
                    click.echo(f"    (+{len(names) - 10} more)")
                click.echo(f"    files: {', '.join(cyc['files'][:5])}")
            click.echo(f"  total: {len(actionable_cycles)} actionable cycle(s), {len(ignored_cycles)} informational")
            if break_suggestions:
                click.echo()
                click.echo("  Cycle break suggestions:")
                for bs in break_suggestions:
                    click.echo(
                        f"    Break: remove dependency {bs['source_name']} -> {bs['target_name']} ({bs['reason']})"
                    )
        else:
            click.echo("  (none)")

        click.echo("\n=== God Components (degree > 20) ===")
        if god_items:
            god_rows = [
                [
                    g["severity"],
                    g["name"],
                    abbrev_kind(g["kind"]),
                    str(g["degree"]),
                    "util" if g["category"] == "utility" else "act",
                    loc(g["file"]),
                ]
                for g in god_items
            ]
            click.echo(format_table(["Sev", "Name", "Kind", "Degree", "Cat", "File"], god_rows, budget=20))
        else:
            click.echo("  (none)")

        click.echo("\n=== Bottlenecks (high betweenness) ===")
        if bn_items:
            bn_rows = []
            for b in bn_items:
                bw_str = f"{b['betweenness']:.0f}" if b["betweenness"] >= 10 else f"{b['betweenness']:.1f}"
                bn_rows.append(
                    [
                        b["severity"],
                        b["name"],
                        abbrev_kind(b["kind"]),
                        bw_str,
                        "util" if b["category"] == "utility" else "act",
                        loc(b["file"]),
                    ]
                )
            click.echo(format_table(["Sev", "Name", "Kind", "Betweenness", "Cat", "File"], bn_rows, budget=15))
        else:
            click.echo("  (none)")

        click.echo(f"\n=== Layer Violations ({len(violations)}) ===")
        if violations:
            v_rows = []
            for v in violations[:20]:
                src = v_lookup.get(v["source"], {})
                tgt = v_lookup.get(v["target"], {})
                v_rows.append(
                    [
                        src.get("name", "?"),
                        f"L{v['source_layer']}",
                        tgt.get("name", "?"),
                        f"L{v['target_layer']}",
                    ]
                )
            click.echo(format_table(["Source", "Layer", "Target", "Layer"], v_rows, budget=20))
            if len(violations) > 20:
                click.echo(f"  (+{len(violations) - 20} more)")
        elif layer_map:
            click.echo("  (none)")
        else:
            click.echo("  (no layers detected)")

        next_steps = suggest_next_steps(
            "health",
            {
                "score": health_score,
                "critical_issues": sev_counts[CRITICAL],
                "cycles": len(actionable_cycles),
            },
        )
        ns_text = format_next_steps_text(next_steps)
        if ns_text:
            click.echo(ns_text)
