"""A.2 — combines individual check findings into a ranked verdict.

Severity ranking is fixed: ``high`` > ``medium`` > ``low`` > ``info``.
Within a severity, findings preserve check-emission order, which is
deterministic per-DB.
"""

from __future__ import annotations

from roam.critique.checks import Finding

_SEVERITY_RANK: dict[str, int] = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "info": 3,
}


def severity_rank(severity: str) -> int:
    """Return a sortable rank for a severity label (lower = more urgent)."""
    return _SEVERITY_RANK.get(severity, 99)


def aggregate(findings: list[Finding]) -> dict:
    """Reduce a flat list of findings into a single envelope-shaped result.

    Returns a dict with:

    * ``verdict`` — one-line summary ("3 findings (1 high, 2 medium)" or
      "no concerns").
    * ``severity_breakdown`` — counts per level.
    * ``findings`` — sorted by severity then by emission order.
    * ``top_finding`` — the most urgent (or ``None`` when none).
    """
    sorted_findings = sorted(findings, key=lambda f: severity_rank(f.severity))

    breakdown = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for f in sorted_findings:
        breakdown[f.severity] = breakdown.get(f.severity, 0) + 1

    if not sorted_findings:
        verdict = "No concerns from roam critique"
    else:
        parts = [f"{n} {sev}" for sev, n in breakdown.items() if n > 0]
        verdict = f"{len(sorted_findings)} finding{'s' if len(sorted_findings) != 1 else ''} ({', '.join(parts)})"

    return {
        "verdict": verdict,
        "severity_breakdown": breakdown,
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "top_finding": _finding_to_dict(sorted_findings[0]) if sorted_findings else None,
    }


def _finding_to_dict(f: Finding) -> dict:
    return {
        "check": f.check,
        "severity": f.severity,
        "title": f.title,
        "detail": f.detail,
        "evidence": f.evidence,
    }
