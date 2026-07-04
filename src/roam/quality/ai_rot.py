"""Canonical AI rot score computation.

This is the SINGLE SOURCE OF TRUTH for "AI rot" — a weighted composite
score (0-100) computed from 8 AI-generated-code anti-pattern detectors.

Before this module existed, two commands disagreed:

- ``roam vibe-check`` reported the full 8-pattern weighted score.
- ``roam dashboard`` reported a 2-pattern approximation (dead exports +
  hallucinated imports only) — typically 50-80% LOWER than vibe-check on
  the same codebase.

This is exactly the Pattern 3 ("vocabulary mismatch across commands")
defect documented in `the dogfood synthesis notes`. The fix
mirrors Fix C (``caller_metric_definition``): one canonical computation
plus an ``ai_rot_definition`` label on every envelope that emits the
number.

Algorithm
---------

8 detectors, each producing a (found, total) pair from which a rate
(0.0-100.0) is derived. Weights sum to 100; the final score is the
weight-normalized mean of capped rates, rounded to an int in [0, 100].

  Pattern               Weight   Detector lives in
  ------------------------------------------------------------
  dead_exports          15       cmd_vibe_check._detect_dead_exports
  short_churn           10       cmd_vibe_check._detect_short_churn
  empty_handlers        20       cmd_vibe_check._detect_empty_handlers
  abandoned_stubs       10       cmd_vibe_check._detect_stubs
  hallucinated_imports  15       cmd_vibe_check._detect_hallucinated_imports
  error_inconsistency   10       cmd_vibe_check._detect_error_inconsistency
  comment_anomalies     10       cmd_vibe_check._detect_comment_anomalies
  copy_paste            10       cmd_vibe_check._detect_copy_paste

Severity bands (from ``cmd_vibe_check._severity_label``):

  0-15   HEALTHY
  16-35  LOW
  36-55  MODERATE
  56-75  HIGH
  76-100 CRITICAL

Definition string
-----------------

Always attach ``DEFINITION`` (or call ``definition()``) to any envelope
that reports an AI rot number. This is the Pattern 3 label fix: even
when two commands cannot share the same computation, they must agree on
what the metric MEANS.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

# Single-line label that should appear in every envelope reporting an AI
# rot number, under the key ``ai_rot_definition``.
DEFINITION = (
    "Canonical AI rot score: weighted average across 8 anti-pattern "
    "detectors (dead_exports w15, short_churn w10, empty_handlers w20, "
    "abandoned_stubs w10, hallucinated_imports w15, error_inconsistency "
    "w10, comment_anomalies w10, copy_paste w10). Run `roam vibe-check` "
    "for the per-pattern breakdown."
)

_VIBE_CHECK_MODULE = "roam.commands.cmd_vibe_check"
_VIBE_CHECK_DETECTORS = (
    ("dead_exports", "_detect_dead_exports", False),
    ("short_churn", "_detect_short_churn", False),
    ("empty_handlers", "_detect_empty_handlers", True),
    ("abandoned_stubs", "_detect_stubs", True),
    ("hallucinated_imports", "_detect_hallucinated_imports", False),
    ("error_inconsistency", "_detect_error_inconsistency", True),
    ("comment_anomalies", "_detect_comment_anomalies", True),
    ("copy_paste", "_detect_copy_paste", True),
)


def definition() -> str:
    """Return the canonical AI rot metric definition string.

    Use this when emitting a JSON envelope that includes an AI rot score:

        summary["ai_rot_definition"] = definition()
    """
    return DEFINITION


@dataclass
class AiRotScore:
    """Canonical AI rot score with full per-pattern breakdown.

    Attributes
    ----------
    score : int
        Weighted composite score in [0, 100]. 0 = pristine, 100 = severe.
    severity : str
        One of HEALTHY / LOW / MODERATE / HIGH / CRITICAL.
    total_issues : int
        Sum of ``found`` across all patterns.
    files_scanned : int
        Total file count for context.
    patterns : dict
        Per-pattern dict mapping pattern key -> {found, total, rate,
        severity, weight}. Keys are the 8 canonical pattern names.
    definition : str
        The :data:`DEFINITION` string — included so any downstream
        consumer that holds an ``AiRotScore`` always has the label.
    """

    score: int
    severity: str
    total_issues: int
    files_scanned: int
    patterns: dict = field(default_factory=dict)
    # Default flows through the canonical ``definition()`` accessor so the
    # public export stays referenced (every sibling quality module —
    # ``cycles`` / ``health_band`` / ``god_components`` / ``public_symbols``
    # — exposes the same ``definition()`` API and calls it in production).
    definition: str = field(default_factory=definition)

    def as_envelope_dict(self) -> dict:
        """Render as a dict suitable for embedding in a JSON envelope.

        Includes the definition label inline so consumers that only read
        this nested dict still see the source-of-truth label.
        """
        return {
            "score": self.score,
            "severity": self.severity,
            "total_issues": self.total_issues,
            "files_scanned": self.files_scanned,
            "patterns": self.patterns,
            "ai_rot_definition": self.definition,
        }


_AI_ROT_ENVELOPE_KEYS = frozenset(
    {
        "score",
        "severity",
        "total_issues",
        "files_scanned",
        "patterns",
        "ai_rot_definition",
    }
)


def _assert_ai_rot_envelope_contract(score: AiRotScore) -> None:
    """Validate the public serializer contract while keeping it production-used."""
    # The class-qualified call is load-bearing: the reference resolver
    # binds ``AiRotScore.as_envelope_dict`` but cannot bind an instance
    # call (``score.as_envelope_dict()``) — three classes define this
    # method name. Rewriting to the instance form makes the export look
    # unreferenced again.
    envelope = AiRotScore.as_envelope_dict(score)
    missing = _AI_ROT_ENVELOPE_KEYS.difference(envelope)
    if missing:
        raise AssertionError(f"AiRotScore.as_envelope_dict missing keys: {sorted(missing)}")


@dataclass(frozen=True)
class _PatternMeasure:
    key: str
    found: int
    total: int


@dataclass(frozen=True)
class _DetectorSpec:
    key: str
    detector: Callable[..., tuple]
    needs_project_root: bool = False


@dataclass(frozen=True)
class _VibeCheckScoring:
    weights: dict[str, int]
    pattern_names: dict[str, str]
    compute_score: Callable[[dict[str, dict]], int]
    severity_label: Callable[[int], str]


def _load_vibe_check_scoring() -> _VibeCheckScoring:
    """Load vibe-check scoring primitives lazily."""
    vibe_check = import_module(_VIBE_CHECK_MODULE)

    return _VibeCheckScoring(
        weights=getattr(vibe_check, "_WEIGHTS"),
        pattern_names=getattr(vibe_check, "_PATTERN_NAMES"),
        compute_score=getattr(vibe_check, "_compute_score"),
        severity_label=getattr(vibe_check, "_severity_label"),
    )


def _load_vibe_check_detectors() -> tuple[_DetectorSpec, ...]:
    """Load the canonical detector catalog in score order."""
    vibe_check = import_module(_VIBE_CHECK_MODULE)
    return tuple(
        _DetectorSpec(key, getattr(vibe_check, detector_name), needs_project_root=needs_project_root)
        for key, detector_name, needs_project_root in _VIBE_CHECK_DETECTORS
    )


def _run_vibe_check_detectors(conn, project_root: Path) -> list[_PatternMeasure]:
    measures: list[_PatternMeasure] = []
    for spec in _load_vibe_check_detectors():
        if spec.needs_project_root:
            found, total, *_details = spec.detector(conn, project_root)
        else:
            found, total, *_details = spec.detector(conn)
        measures.append(_PatternMeasure(spec.key, found, total))
    return measures


def _rate(found: int, total: int) -> float:
    return round(found / max(total, 1) * 100, 1)


def _pattern_severity(rate: float) -> str:
    if rate >= 30:
        return "high"
    if rate >= 10:
        return "medium"
    if rate > 0:
        return "low"
    return "none"


def _build_pattern_breakdown(
    measures: list[_PatternMeasure],
    scoring: _VibeCheckScoring,
) -> dict[str, dict]:
    patterns: dict[str, dict] = {}
    for measure in measures:
        rate = _rate(measure.found, measure.total)
        patterns[measure.key] = {
            "found": measure.found,
            "total": measure.total,
            "rate": rate,
            "severity": _pattern_severity(rate),
            "weight": scoring.weights[measure.key],
            "label": scoring.pattern_names[measure.key],
        }
    return patterns


def compute_ai_rot_score(conn, project_root: Path | None = None) -> AiRotScore:
    """Compute the canonical 8-pattern AI rot score.

    This is intentionally a thin wrapper around the detectors in
    ``cmd_vibe_check`` — those functions ARE the canonical algorithm.
    Centralising the orchestration here means any command (dashboard,
    vibe-check, future audit composites) gets the SAME number on the
    same DB.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open roam DB connection (readonly is fine).
    project_root : Path, optional
        Project root for file-content detectors (empty_handlers, stubs,
        error_inconsistency, comment_anomalies, copy_paste). If omitted,
        looked up via :func:`roam.db.connection.find_project_root`.

    Returns
    -------
    AiRotScore
        Fully populated, idempotent for a fixed DB state. Calling twice
        on the same connection returns equal values (modulo dict
        ordering, which is preserved by Python 3.7+).

    Notes
    -----
    File-scanning detectors (patterns 3, 4, 6, 7, 8) walk every indexed
    source file. On a roam-sized repo this is sub-second; on giant
    monorepos it can take a few seconds. The previous dashboard
    "fast approximation" was 10-50x faster but produced a different
    number — see module docstring for why we abandoned it.
    """
    if project_root is None:
        from roam.db.connection import find_project_root

        project_root = find_project_root()

    scoring = _load_vibe_check_scoring()
    measures = _run_vibe_check_detectors(conn, project_root)
    patterns = _build_pattern_breakdown(measures, scoring)
    score = scoring.compute_score(patterns)
    severity = scoring.severity_label(score)
    total_issues = sum(measure.found for measure in measures)
    files_scanned = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    score_result = AiRotScore(
        score=score,
        severity=severity,
        total_issues=total_issues,
        files_scanned=files_scanned,
        patterns=patterns,
    )
    _assert_ai_rot_envelope_contract(score_result)
    return score_result
