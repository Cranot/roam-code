"""resilience — the reliability super-optimizer (family P3).

`roam math` detects weak algorithms; `roam agent-opt` weak envelope shape;
`roam observability-opt` weak diagnosability. `roam resilience` detects code
that fails badly under load or partial failure: HTTP calls without timeouts,
retries without backoff, fire-and-forget critical work, swallowed exceptions
without context. Diagnosis + direction — the super-optimizer shape.

Why a family-local registry (same call as ``roam.agent_opt`` /
``roam.observability_opt``)
-----------------------------------------------------------------------------
These detectors take *harvested source lines* — ``(path, language, text)`` —
not the ``(conn) -> list[dict]`` signature the math ``_DETECTOR_REGISTRY``
contract assumes (``tests/test_w639_detector_smoke.py`` parametrises over every
shared-registry entry and asserts ``fn(empty_db) == []``). They live in their
own ``_RESILIENCE_DETECTORS`` registry but reuse the CANONICAL closed-enum
vocabularies (``confidence_basis`` / ``query_cost``). The TASK catalog is
shared: tasks live in ``catalog/tasks.py`` tagged ``family="resilience"``.

Phase A scope: ``missing-timeout`` only (the highest-signal reliability bug
class — a network call without a timeout deadlocks the caller on a slow
remote). Future tasks: ``retry-without-backoff``, ``retry-non-idempotent``,
``fire-and-forget-critical``, ``unstructured-error``. Existing math tasks
``broad-except-swallow`` and ``async-fire-and-forget-task`` will be promoted
to resilience in Phase B (re-export, not redefine — same detection logic,
different family surface).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

# Reuse the CANONICAL closed-enum vocabularies (a typo fails fast against the
# same source of truth math + agent-opt + observability-opt use).
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

__all__ = [
    "FAMILY",
    "RESILIENCE_DETECTOR_VERSION",
    "resilience_detector",
    "list_resilience_detectors",
    "list_resilience_tasks",
    "resilience_task_ids",
    "detect_missing_timeout",
    "harvest_source_files",
    "run_resilience",
    "build_finding_records",
]

FAMILY = "resilience"
RESILIENCE_DETECTOR_VERSION = "1.0.0"

_VALID_BASES = frozenset({CONFIDENCE_HEURISTIC, CONFIDENCE_STRUCTURAL, CONFIDENCE_STATIC_ANALYSIS, CONFIDENCE_RUNTIME})
_VALID_COSTS = frozenset({QUERY_COST_LOW, QUERY_COST_MEDIUM, QUERY_COST_HIGH})

# ---------------------------------------------------------------------------
# A3-style detector registry (family-local; see module docstring for why).
# ---------------------------------------------------------------------------
_RESILIENCE_DETECTORS: dict[str, dict[str, Any]] = {}


def resilience_detector(
    *,
    task_id: str,
    confidence_basis: str = CONFIDENCE_STRUCTURAL,
    query_cost: str = QUERY_COST_LOW,
    version: str = RESILIENCE_DETECTOR_VERSION,
) -> Callable[[Callable[..., list[dict]]], Callable[..., list[dict]]]:
    """Register a resilience source detector with metadata.

    Validates ``confidence_basis`` / ``query_cost`` against the CANONICAL
    closed-enum sets (raises ``ValueError`` at import time on a typo) and that
    ``task_id`` is a CATALOG task tagged ``family="resilience"`` — same
    construction-time discipline as ``roam.agent_opt`` /
    ``roam.observability_opt``.
    """
    if confidence_basis not in _VALID_BASES:
        raise ValueError(f"confidence_basis must be one of {sorted(_VALID_BASES)}, got {confidence_basis!r}")
    if query_cost not in _VALID_COSTS:
        raise ValueError(f"query_cost must be one of {sorted(_VALID_COSTS)}, got {query_cost!r}")
    if task_id not in CATALOG or CATALOG[task_id].get("family") != FAMILY:
        raise ValueError(f"task_id {task_id!r} is not a CATALOG task tagged family={FAMILY!r}")

    def wrap(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
        _RESILIENCE_DETECTORS[fn.__name__] = {
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


def list_resilience_detectors() -> list[dict[str, Any]]:
    """Registry entries (sans callable) for ``--list-detectors``."""
    return [{k: v for k, v in e.items() if k != "function"} for e in _RESILIENCE_DETECTORS.values()]


def resilience_task_ids() -> list[str]:
    """Catalog task ids tagged ``family="resilience"``."""
    return [tid for tid, t in CATALOG.items() if t.get("family") == FAMILY]


def list_resilience_tasks() -> list[dict[str, Any]]:
    """Task rows for ``roam resilience --list-tasks`` (best-way included)."""
    detectors_by_task: dict[str, int] = {}
    for e in _RESILIENCE_DETECTORS.values():
        detectors_by_task[e["task_id"]] = detectors_by_task.get(e["task_id"], 0) + 1
    rows: list[dict[str, Any]] = []
    for tid in resilience_task_ids():
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
# Task 1: missing-timeout — a network call without a deadline
# ---------------------------------------------------------------------------
# Per-language network-call sites that REQUIRE an explicit timeout. Each
# entry: (call_pattern, timeout_indicator_in_line, default_safe, confidence).
#
# * call_pattern fires when the line invokes a network primitive.
# * timeout_indicator is the substring whose presence on the SAME line means
#   "explicit timeout supplied" — when present, the line is fine. (Multi-line
#   calls with timeout on a later line are a known v1 false-positive class;
#   confidence is heuristic.)
# * default_safe says whether the underlying client is timeout-safe BY
#   DEFAULT. Python ``requests`` is NOT default-safe (no timeout = block
#   forever). Go's bare ``http.Get`` uses the default client with no Timeout —
#   ALWAYS unsafe, so any call site is HIGH confidence.
_TIMEOUT_PATTERNS: dict[str, tuple[re.Pattern[str], str, bool, str]] = {
    # Python ``requests`` / ``httpx`` / ``urllib`` — no default timeout; explicit
    # ``timeout=`` is the structured-logging-equivalent best practice.
    "python": (
        re.compile(r"\b(requests|httpx)\.(get|post|put|patch|delete|head|request)\s*\("),
        "timeout=",
        False,
        "medium",
    ),
    # JS ``fetch`` — default has no timeout; the rank-1 pattern is ``signal:
    # AbortSignal.timeout(ms)``. Lower confidence because libraries often wrap
    # fetch with a timeout helper that the line-level scan can't see.
    "javascript": (
        re.compile(r"(?<![\w.])fetch\s*\("),
        "signal",
        False,
        "low",
    ),
    "typescript": (
        re.compile(r"(?<![\w.])fetch\s*\("),
        "signal",
        False,
        "low",
    ),
    "tsx": (
        re.compile(r"(?<![\w.])fetch\s*\("),
        "signal",
        False,
        "low",
    ),
    # Go ``http.{Get,Post,Head,PostForm}`` — the package-level helpers ALWAYS
    # use ``http.DefaultClient`` which has no Timeout field. There is no
    # per-call timeout argument; the only fix is to switch to a custom
    # ``http.Client{Timeout: ...}``. Therefore every call site is unsafe and
    # the timeout_indicator (impossible same-line cure) is never present.
    "go": (
        re.compile(r"\bhttp\.(Get|Post|Head|PostForm)\s*\("),
        "\0impossible",  # never matches → always flag
        False,
        "high",
    ),
}


# Lightweight per-language line-comment markers — skip lines that are wholly a
# comment (mirrors observability-opt's same-named helper).
_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "python": ("#",),
    "javascript": ("//",),
    "typescript": ("//",),
    "tsx": ("//",),
    "go": ("//",),
}


def _is_comment_line(stripped: str, language: str) -> bool:
    for marker in _COMMENT_PREFIXES.get(language, ()):
        if stripped.startswith(marker):
            return True
    return False


@resilience_detector(
    task_id="missing-timeout",
    confidence_basis=CONFIDENCE_HEURISTIC,
    query_cost=QUERY_COST_MEDIUM,
)
def detect_missing_timeout(
    sources: Iterable[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Flag network calls without an explicit timeout / deadline.

    ``sources`` is an iterable of ``(path, language, text)`` — e.g. from
    ``harvest_source_files()``. v1 is a line-level heuristic: it scans for
    network primitives and flags a call when the SAME line has no timeout
    indicator. Multi-line calls with the timeout argument on a continuation
    line are a known false-positive class (mitigated via confidence tiers).
    """
    out: list[dict[str, Any]] = []
    for path, language, text in sources:
        lang = (language or "").lower()
        entry = _TIMEOUT_PATTERNS.get(lang)
        if entry is None or not text:
            continue
        pattern, indicator, _default_safe, confidence = entry
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or _is_comment_line(stripped, lang):
                continue
            if not pattern.search(line):
                continue
            if indicator in line:
                continue  # explicit timeout in same line — fine
            out.append(
                _finding(
                    task_id="missing-timeout",
                    detected_way="no-explicit-timeout",
                    subject=f"{path}:{lineno}",
                    subject_kind="file",
                    confidence=confidence,
                    confidence_basis=CONFIDENCE_HEURISTIC,
                    reason=(
                        f"{path}:{lineno} performs a network call without an explicit "
                        f"timeout — set a per-call deadline so a slow remote can't hang the caller"
                    ),
                    evidence={"language": lang, "line": stripped[:140], "lineno": lineno},
                )
            )
    return out


# ---------------------------------------------------------------------------
# Signal-source harvester — source files from the index. Canonical policy reused
# by source-line detector families with their own per-language pattern maps.
# ---------------------------------------------------------------------------
_NON_SOURCE_ROLES = frozenset({"test", "docs", "config", "generated", "data", "build", "examples", "scripts"})
_NON_SOURCE_PATH_PREFIXES: tuple[str, ...] = (
    ".github/",
    ".circleci/",
    ".gitlab/",
    ".buildkite/",
)


def harvest_source_files(
    conn,
    *,
    root: str = ".",
    languages: tuple[str, ...] | None = None,
    max_files: int = 0,
    pattern_languages: Iterable[str] | None = None,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Harvest ``(path, language, text)`` for source-role files in the index.

    Excludes non-source file roles + CI workflow path prefixes, reads file
    content from disk relative to ``root``, and records unreadable files
    separately. ``pattern_languages`` lets related source-line detectors reuse
    the same harvest policy with their own supported-language set.
    """
    import os

    rows = conn.execute(
        "SELECT path, language, file_role FROM files WHERE language IS NOT NULL AND language != '' ORDER BY path"
    ).fetchall()
    want_langs = {name.lower() for name in languages} if languages else None
    source_pattern_languages = _TIMEOUT_PATTERNS if pattern_languages is None else pattern_languages
    supported_langs = {name.lower() for name in source_pattern_languages}
    sources: list[tuple[str, str, str]] = []
    unreadable: list[str] = []
    for row in rows:
        path = row["path"] if not isinstance(row, tuple) else row[0]
        language = (row["language"] if not isinstance(row, tuple) else row[1]) or ""
        role = (row["file_role"] if not isinstance(row, tuple) else row[2]) or "source"
        if role in _NON_SOURCE_ROLES:
            continue
        if any(path.startswith(prefix) for prefix in _NON_SOURCE_PATH_PREFIXES):
            continue
        lang = language.lower()
        if lang not in supported_langs:
            continue
        if want_langs is not None and lang not in want_langs:
            continue
        try:
            with open(os.path.join(root, path), "r", encoding="utf-8", errors="strict") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            unreadable.append(path)
            continue
        sources.append((path, lang, text))
        if max_files and len(sources) >= max_files:
            break
    return sources, unreadable


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_resilience(
    conn,
    *,
    root: str = ".",
    only: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    languages: tuple[str, ...] | None = None,
    max_files: int = 0,
    sources: list[tuple[str, str, str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the selected resilience detectors and return ``(findings, meta)``.

    Source is harvested once (lazily) and shared across active source-tier
    tasks. ``sources`` may be passed in directly for deterministic tests.
    ``meta`` carries ``partial_success`` (Pattern 2): True iff a detector
    raised OR no source could be harvested for an active task.
    """
    all_tasks = set(resilience_task_ids())
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
            partial = True

        if "missing-timeout" in active:
            try:
                findings.extend(detect_missing_timeout(harvested))
                executed += 1
            except Exception as exc:  # noqa: BLE001 — record + degrade, never silent
                failed.append({"detector": "detect_missing_timeout", "error": f"{type(exc).__name__}: {exc}"})
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

    ``source_detector`` is prefixed with the family
    (``resilience.<task>``) so persisted names won't collide with other
    families. ``subject_id`` is NULL — the subject is a ``path:line`` surface,
    not a resolved ``symbols.id``.
    """
    records: list[FindingRecord] = []
    for f in findings:
        task_id = f["task_id"]
        subject = f.get("subject", "?")
        records.append(
            FindingRecord(
                finding_id_str=make_finding_id("resilience", subject, task_id, f.get("detected_way", "")),
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
                source_detector=f"resilience.{task_id}",
                source_version=RESILIENCE_DETECTOR_VERSION,
            )
        )
    return records
