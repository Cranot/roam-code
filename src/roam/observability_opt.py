"""observability-opt — the diagnosability super-optimizer (family P2).

`roam math` detects weak *algorithms* and points at the stronger one;
`roam agent-opt` does the same for roam's own agent-facing envelope shapes.
`roam observability-opt` does it for code that leaves a system hard to debug:
raw debug prints left in source, string-only logs with no context, traces
without status. It emits "this is solving TASK_X with weak shape Y -> use
shape Z" findings over a target repo's SOURCE.

Why a family-local registry (same call as ``roam.agent_opt``)
-------------------------------------------------------------
These detectors take *harvested source lines* — ``(path, language, text)`` —
not the ``(conn) -> list[dict]`` signature the math ``_DETECTOR_REGISTRY``
contract assumes (``tests/test_w639_detector_smoke.py`` parametrises over every
shared-registry entry and asserts ``fn(empty_db) == []``). They therefore live
in their own ``_OBSERVABILITY_OPT_DETECTORS`` registry but reuse the CANONICAL
closed-enum vocabularies (``confidence_basis`` / ``query_cost``) so the
"extend the enum before adding a tier" discipline still flows through one
place. The TASK catalog IS shared: tasks live in ``catalog/tasks.py`` tagged
``family="observability-opt"`` (single catalog, many surfaces).
"""

from __future__ import annotations

import json
import re
from functools import partial
from typing import Any, Callable, Iterable

# Reuse the CANONICAL closed-enum vocabularies (a typo fails fast against the
# same source of truth math + agent-opt use).
from roam.catalog.detectors import QUERY_COST_HIGH, QUERY_COST_LOW, QUERY_COST_MEDIUM
from roam.catalog.tasks import CATALOG, best_way
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_RUNTIME,
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
    FindingRecord,
    make_finding_id,
)
from roam.resilience import harvest_source_files as _harvest_source_files

__all__ = [
    "FAMILY",
    "OBSERVABILITY_OPT_DETECTOR_VERSION",
    "observability_opt_detector",
    "list_observability_opt_detectors",
    "list_observability_opt_tasks",
    "observability_opt_task_ids",
    "detect_print_debug_leftover",
    "harvest_source_files",
    "run_observability_opt",
    "build_finding_records",
]

FAMILY = "observability-opt"
OBSERVABILITY_OPT_DETECTOR_VERSION = "1.0.0"

_VALID_BASES = frozenset({CONFIDENCE_HEURISTIC, CONFIDENCE_STRUCTURAL, CONFIDENCE_STATIC_ANALYSIS, CONFIDENCE_RUNTIME})
_VALID_COSTS = frozenset({QUERY_COST_LOW, QUERY_COST_MEDIUM, QUERY_COST_HIGH})

# ---------------------------------------------------------------------------
# A3-style detector registry (family-local; see module docstring for why).
# ---------------------------------------------------------------------------
_OBSERVABILITY_OPT_DETECTORS: dict[str, dict[str, Any]] = {}


def observability_opt_detector(
    *,
    task_id: str,
    confidence_basis: str = CONFIDENCE_STRUCTURAL,
    query_cost: str = QUERY_COST_LOW,
    version: str = OBSERVABILITY_OPT_DETECTOR_VERSION,
) -> Callable[[Callable[..., list[dict]]], Callable[..., list[dict]]]:
    """Register an observability-opt source detector with metadata.

    Validates ``confidence_basis`` / ``query_cost`` against the CANONICAL
    closed-enum sets (raises ``ValueError`` at import time on a typo) and that
    ``task_id`` is a CATALOG task tagged ``family="observability-opt"`` — same
    construction-time discipline as ``roam.agent_opt.agent_opt_detector``.
    """
    if confidence_basis not in _VALID_BASES:
        raise ValueError(f"confidence_basis must be one of {sorted(_VALID_BASES)}, got {confidence_basis!r}")
    if query_cost not in _VALID_COSTS:
        raise ValueError(f"query_cost must be one of {sorted(_VALID_COSTS)}, got {query_cost!r}")
    if task_id not in CATALOG or CATALOG[task_id].get("family") != FAMILY:
        raise ValueError(f"task_id {task_id!r} is not a CATALOG task tagged family={FAMILY!r}")

    def wrap(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
        _OBSERVABILITY_OPT_DETECTORS[fn.__name__] = {
            "name": fn.__name__,
            "task_id": task_id,
            "family": FAMILY,
            "confidence_basis": confidence_basis,
            "query_cost": query_cost,
            "version": version,
            "function": fn,
        }
        return fn

    return wrap


def list_observability_opt_detectors() -> list[dict[str, Any]]:
    """Registry entries (sans callable) for ``--list-detectors``."""
    return [{k: v for k, v in e.items() if k != "function"} for e in _OBSERVABILITY_OPT_DETECTORS.values()]


def observability_opt_task_ids() -> list[str]:
    """Catalog task ids tagged ``family="observability-opt"``."""
    return [tid for tid, t in CATALOG.items() if t.get("family") == FAMILY]


def list_observability_opt_tasks() -> list[dict[str, Any]]:
    """Task rows for ``roam observability-opt --list-tasks`` (best-way included)."""
    detectors_by_task: dict[str, int] = {}
    for e in _OBSERVABILITY_OPT_DETECTORS.values():
        detectors_by_task[e["task_id"]] = detectors_by_task.get(e["task_id"], 0) + 1
    rows: list[dict[str, Any]] = []
    for tid in observability_opt_task_ids():
        task = CATALOG[tid]
        best = best_way(tid)
        rows.append(
            {
                "task_id": tid,
                "name": task["name"],
                "category": task["category"],
                "kind": task["kind"],
                "family": FAMILY,
                "detector_count": detectors_by_task.get(tid, 0),
                "best_way": best["id"] if best else "",
                "best_name": best["name"] if best else "",
                "best_tip": best.get("tip", "") if best else "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _suggestion_for(task_id: str) -> tuple[str, str]:
    """Return ``(best_way_id, best_tip)`` — the rank-1 way to jump to."""
    best = best_way(task_id)
    if not best:
        return "", ""
    return best["id"], best.get("tip", "")


def _finding(
    task_id: str,
    detected_way: str,
    subject: str,
    subject_kind: str,
    confidence: str,
    confidence_basis: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    best_id, best_tip = _suggestion_for(task_id)
    return {
        "task_id": task_id,
        "detected_way": detected_way,
        "suggested_way": best_id,
        "subject": subject,
        "subject_kind": subject_kind,
        "confidence": confidence,  # CVSS-style high/medium/low (matches `roam math`)
        "confidence_basis": confidence_basis,  # heuristic/structural/... (@detector axis)
        "reason": reason,
        "evidence": evidence,
        "suggestion": best_tip,
    }


# ---------------------------------------------------------------------------
# Task 1: print-debug-leftover — a raw debug print left in non-test source
# ---------------------------------------------------------------------------
# Per-language debug-output constructs. Keyed by the canonical language name
# stored in ``files.language``. Each entry is (compiled_regex, confidence)
# where confidence reflects false-positive risk: ``console.debug`` / ``dbg!`` /
# ``var_dump`` are almost always debug; a bare ``print(`` in a CLI tool can be
# legitimate, so it is medium.
_DEBUG_PRINT_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    "python": (re.compile(r"(?<![\w.])print\s*\("), "medium"),
    "javascript": (re.compile(r"\bconsole\.(log|debug|trace|dir|info)\s*\("), "medium"),
    "typescript": (re.compile(r"\bconsole\.(log|debug|trace|dir|info)\s*\("), "medium"),
    "tsx": (re.compile(r"\bconsole\.(log|debug|trace|dir|info)\s*\("), "medium"),
    "go": (re.compile(r"\b(fmt\.Print(ln|f)?|println)\s*\("), "medium"),
    "rust": (re.compile(r"\b(println!|eprintln!|print!|dbg!)"), "medium"),
    "java": (re.compile(r"\bSystem\.(out|err)\.print(ln)?\s*\("), "medium"),
    "kotlin": (re.compile(r"(?<![\w.])println?\s*\("), "medium"),
    # Bare ``p`` is excluded: ``p = 1`` (assignment) would false-positive; the
    # debug forms ``puts``/``pp`` are unambiguous at line start.
    "ruby": (re.compile(r"^\s*(puts|pp)\b"), "low"),
    "php": (re.compile(r"\b(var_dump|print_r|var_export)\s*\("), "high"),
    "csharp": (re.compile(r"\bConsole\.(WriteLine|Write)\s*\("), "medium"),
}
# C / C++ are deliberately omitted: ``printf`` / ``std::cout`` are the *normal*
# output mechanism (no standard logging framework), so "route through a
# structured logger" is wrong advice and flagging every call is pure noise. The
# detector targets languages where a logger is the idiomatic alternative.

# Lightweight per-language line-comment markers — skip lines that are wholly a
# comment so a commented-out ``// console.log(...)`` is not flagged. This is a
# heuristic (not AST); block comments / string literals are out of scope for
# the v1 detector and tuned via confidence tier.
_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "python": ("#",),
    "ruby": ("#",),
    "javascript": ("//",),
    "typescript": ("//",),
    "tsx": ("//",),
    "go": ("//",),
    "rust": ("//",),
    "java": ("//",),
    "kotlin": ("//",),
    "csharp": ("//",),
    "php": ("//", "#"),
}


def _is_comment_line(stripped: str, language: str) -> bool:
    for marker in _COMMENT_PREFIXES.get(language, ()):  # noqa: SIM110 — clarity
        if stripped.startswith(marker):
            return True
    return False


@observability_opt_detector(
    task_id="print-debug-leftover",
    confidence_basis=CONFIDENCE_HEURISTIC,
    query_cost=QUERY_COST_MEDIUM,
)
def detect_print_debug_leftover(
    sources: Iterable[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Flag raw debug-print statements left in non-test source.

    ``sources`` is an iterable of ``(path, language, text)`` — e.g. from
    ``harvest_source_files()``. Caller is responsible for having excluded test
    / docs / generated files. One finding per matching line; confidence tier
    is per-language (``var_dump`` is high-signal, ``printf`` is low).
    """
    out: list[dict[str, Any]] = []
    for path, language, text in sources:
        lang = (language or "").lower()
        entry = _DEBUG_PRINT_PATTERNS.get(lang)
        if entry is None or not text:
            continue
        pattern, confidence = entry
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or _is_comment_line(stripped, lang):
                continue
            if pattern.search(line):
                out.append(
                    _finding(
                        task_id="print-debug-leftover",
                        detected_way="raw-debug-print",
                        subject=f"{path}:{lineno}",
                        subject_kind="file",
                        confidence=confidence,
                        confidence_basis=CONFIDENCE_HEURISTIC,
                        reason=(
                            f"{path}:{lineno} has a raw debug print "
                            f"({lang}) — route diagnostics through a structured logger instead"
                        ),
                        evidence={"language": lang, "line": stripped[:120], "lineno": lineno},
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Signal-source harvester — shared source-file policy from ``roam.resilience``.
# ---------------------------------------------------------------------------
harvest_source_files = partial(_harvest_source_files, pattern_languages=tuple(_DEBUG_PRINT_PATTERNS))
harvest_source_files.__doc__ = _harvest_source_files.__doc__


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_observability_opt(
    conn,
    *,
    root: str = ".",
    only: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    languages: tuple[str, ...] | None = None,
    max_files: int = 0,
    sources: list[tuple[str, str, str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the selected observability-opt detectors and return ``(findings, meta)``.

    Source is harvested once (lazily) and shared across active source-tier
    tasks. ``sources`` may be passed in directly for deterministic tests.
    ``meta`` carries ``partial_success`` (Pattern 2): True iff a detector raised
    OR no source could be harvested for an active task.
    """
    all_tasks = set(observability_opt_task_ids())
    only_set = {t for t in (only or ()) if t}
    exclude_set = {t for t in (exclude or ()) if t} - only_set
    active = set(all_tasks)
    if only_set:
        active &= only_set
    active -= exclude_set

    only_unknown = sorted(only_set - all_tasks) if only_set else []
    exclude_unknown = sorted(exclude_set - all_tasks) if exclude_set else []

    findings: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    executed = 0
    sources_meta: dict[str, Any] = {}
    partial = False

    if active:
        if sources is None:
            harvested, unreadable = harvest_source_files(conn, root=root, languages=languages, max_files=max_files)
        else:
            harvested, unreadable = sources, []
        sources_meta["source_files_scanned"] = len(harvested)
        sources_meta["files_unreadable"] = unreadable
        if not harvested:
            partial = True  # no signal source -> disclose, don't fake SAFE

        if "print-debug-leftover" in active:
            try:
                findings.extend(detect_print_debug_leftover(harvested))
                executed += 1
            except Exception as exc:  # noqa: BLE001 — record + degrade, never silent
                failed.append({"detector": "detect_print_debug_leftover", "error": f"{type(exc).__name__}: {exc}"})
                partial = True

    if failed:
        partial = True

    meta: dict[str, Any] = {
        "detectors_executed": executed,
        "detectors_failed": len(failed),
        "failed_detectors": failed,
        "active_tasks": sorted(active),
        "sources": sources_meta,
        "partial_success": partial,
    }
    if only_unknown:
        meta["only_unknown"] = only_unknown
    if exclude_unknown:
        meta["exclude_unknown"] = exclude_unknown
    return findings, meta


# ---------------------------------------------------------------------------
# A4 persistence — wired explicitly (per-family, not free reuse)
# ---------------------------------------------------------------------------
def build_finding_records(findings: list[dict[str, Any]]) -> list[FindingRecord]:
    """Map in-envelope findings onto canonical ``FindingRecord`` rows.

    ``source_detector`` is prefixed with the family (``observability-opt.<task>``)
    so persisted names won't collide with other families. ``subject_id`` is NULL
    — the subject is a ``path:line`` surface, not a resolved ``symbols.id``.
    """
    records: list[FindingRecord] = []
    for f in findings:
        task_id = f["task_id"]
        subject = f.get("subject", "?")
        records.append(
            FindingRecord(
                finding_id_str=make_finding_id("observability-opt", subject, task_id, f.get("detected_way", "")),
                subject_kind="symbol",
                subject_id=None,
                claim=f.get("reason", f"{task_id} violation on {subject}"),
                evidence_json=json.dumps(
                    {
                        "task_id": task_id,
                        "detected_way": f.get("detected_way"),
                        "recommended_way": f.get("suggested_way"),
                        "subject": subject,
                        "subject_kind": f.get("subject_kind"),
                        "suggestion": f.get("suggestion"),
                        "evidence": f.get("evidence", {}),
                    },
                    sort_keys=True,
                ),
                confidence=f.get("confidence_basis", CONFIDENCE_HEURISTIC),
                source_detector=f"observability-opt.{task_id}",
                source_version=OBSERVABILITY_OPT_DETECTOR_VERSION,
            )
        )
    return records
