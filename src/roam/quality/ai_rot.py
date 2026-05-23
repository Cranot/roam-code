"""Canonical AI rot score computation.

This is the SINGLE SOURCE OF TRUTH for "AI rot" — a weighted composite
score (0-100) computed from 8 AI-generated-code anti-pattern detectors.

Before this module existed, two commands disagreed:

- ``roam vibe-check`` reported the full 8-pattern weighted score.
- ``roam dashboard`` reported a 2-pattern approximation (dead exports +
  hallucinated imports only) — typically 50-80% LOWER than vibe-check on
  the same codebase.

This is exactly the Pattern 3 ("vocabulary mismatch across commands")
defect documented in ``internal/dogfood/SYNTHESIS-2026-05-12.md``. The fix
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

from dataclasses import dataclass, field
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
    definition: str = DEFINITION

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
    # Import detectors lazily so this module stays cheap to import.
    # cmd_vibe_check pulls in regex precompiles + click; we want
    # ``roam.quality.ai_rot`` to be safe to reference from places that
    # haven't loaded click.
    from roam.commands.cmd_vibe_check import (
        _PATTERN_NAMES,
        _WEIGHTS,
        _compute_score,
        _detect_comment_anomalies,
        _detect_copy_paste,
        _detect_dead_exports,
        _detect_empty_handlers,
        _detect_error_inconsistency,
        _detect_hallucinated_imports,
        _detect_short_churn,
        _detect_stubs,
        _severity_label,
    )

    if project_root is None:
        from roam.db.connection import find_project_root

        project_root = find_project_root()

    # --- Run all 8 detectors (same order, same calls as cmd_vibe_check) ---
    p1_found, p1_total = _detect_dead_exports(conn)
    p2_found, p2_total, _p2_details = _detect_short_churn(conn)
    p3_found, p3_total, _p3_details = _detect_empty_handlers(conn, project_root)
    p4_found, p4_total, _p4_details = _detect_stubs(conn, project_root)
    p5_found, p5_total, _p5_details = _detect_hallucinated_imports(conn)
    p6_found, p6_total, _p6_details = _detect_error_inconsistency(conn, project_root)
    p7_found, p7_total, _p7_details = _detect_comment_anomalies(conn, project_root)
    p8_found, p8_total, _p8_details = _detect_copy_paste(conn, project_root)

    def _rate(found: int, total: int) -> float:
        return round(found / max(total, 1) * 100, 1)

    patterns: dict[str, dict] = {
        "dead_exports": {"found": p1_found, "total": p1_total, "rate": _rate(p1_found, p1_total)},
        "short_churn": {"found": p2_found, "total": p2_total, "rate": _rate(p2_found, p2_total)},
        "empty_handlers": {"found": p3_found, "total": p3_total, "rate": _rate(p3_found, p3_total)},
        "abandoned_stubs": {"found": p4_found, "total": p4_total, "rate": _rate(p4_found, p4_total)},
        "hallucinated_imports": {
            "found": p5_found,
            "total": p5_total,
            "rate": _rate(p5_found, p5_total),
        },
        "error_inconsistency": {
            "found": p6_found,
            "total": p6_total,
            "rate": _rate(p6_found, p6_total),
        },
        "comment_anomalies": {"found": p7_found, "total": p7_total, "rate": _rate(p7_found, p7_total)},
        "copy_paste": {"found": p8_found, "total": p8_total, "rate": _rate(p8_found, p8_total)},
    }

    # Per-pattern severity labels (same thresholds as cmd_vibe_check).
    for _key, pdata in patterns.items():
        r = pdata["rate"]
        if r >= 30:
            pdata["severity"] = "high"
        elif r >= 10:
            pdata["severity"] = "medium"
        elif r > 0:
            pdata["severity"] = "low"
        else:
            pdata["severity"] = "none"
        # Attach weight + human label for free — downstream callers
        # always need them and we have the data here.
        pdata["weight"] = _WEIGHTS[_key]
        pdata["label"] = _PATTERN_NAMES[_key]

    score = _compute_score(patterns)
    severity = _severity_label(score)
    total_issues = sum(p["found"] for p in patterns.values())
    files_scanned = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    return AiRotScore(
        score=score,
        severity=severity,
        total_issues=total_issues,
        files_scanned=files_scanned,
        patterns=patterns,
    )
