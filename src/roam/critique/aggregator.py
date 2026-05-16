"""A.2 — combines individual check findings into a ranked verdict.

Severity ranking is fixed: ``high`` > ``medium`` > ``low`` > ``info``.
Within a severity, findings preserve check-emission order, which is
deterministic per-DB.

W564: severity ORDER now sources from
:func:`roam.output._severity.severity_rank` (single source of truth
across all roam commands). The aggregator is one of 13+ pre-W564 sites
that owned its own table; the local ``severity_rank`` is kept as a
thin alias to preserve public-API compatibility (callers in
``roam.critique.checks`` and friends import it directly).
"""

from __future__ import annotations

from roam.critique.checks import Finding
from roam.output._severity import severity_breakdown
from roam.output._severity import severity_rank as _canonical_severity_rank


def severity_rank(severity: str) -> int:
    """Return a sortable rank for a severity label (lower = more urgent).

    Polarity is inverted from the canonical
    :func:`roam.output._severity.severity_rank` (where higher = worse)
    so legacy ``sorted(..., key=severity_rank)`` call sites in
    :mod:`roam.critique` keep emitting findings in the same order.
    """
    return -_canonical_severity_rank(severity)


def aggregate(
    findings: list[Finding],
    check_status: dict[str, str] | None = None,
) -> dict:
    """Reduce a flat list of findings into a single envelope-shaped result.

    Returns a dict with:

    * ``verdict`` — one-line summary ("3 findings (1 high, 2 medium)" or
      "no concerns from roam critique"). W832: when ``check_status`` is
      supplied and any check did not run cleanly, the clean-path verdict
      discloses the partial state (e.g. ``"0 concerns from 2 of 3 checks
      (1 skipped)"``) instead of the silent ``"No concerns"`` Pattern 2
      fallback.
    * ``severity_breakdown`` — counts per level.
    * ``findings`` — sorted by severity then by emission order.
    * ``top_finding`` — the most urgent (or ``None`` when none).
    * ``check_status`` — pass-through of the per-check status dict (when
      provided), so the caller can surface it in JSON envelopes.
    * ``partial_success`` — True when any check was skipped or errored.

    ``check_status``: mapping of ``check_name -> status`` where status is
    one of ``"ran"`` / ``"skipped:<reason>"`` / ``"errored:<exc>"``. When
    omitted, the legacy ``"No concerns"`` verdict is preserved for
    callers that don't yet pass the dict (LAW 11: explicit > inferred).
    """
    sorted_findings = sorted(findings, key=lambda f: severity_rank(f.severity))

    # W566 — bucketing delegates to the canonical helper. The critique
    # vocabulary is the 4-tier ``high/medium/low/info`` (no CVSS
    # ``critical`` tier — checks never emit it) and the contract is
    # zero-padded (downstream consumers + ``test_critique.py`` pin the
    # full dict shape even when all buckets are zero). The W566
    # ``unknown_bucket=None`` path drops items whose severity is not
    # in the 4-tier vocab — but every ``Finding`` constructor in
    # :mod:`roam.critique.checks` constrains severity to that vocab,
    # so the drop path is unreachable in practice.
    breakdown = severity_breakdown(
        sorted_findings,
        key=lambda f: f.severity,
        vocab=("high", "medium", "low", "info"),
        unknown_bucket=None,
        drop_zero=False,
    )

    # W832 — Pattern 2 silent-fallback fix. Compute per-check tallies so
    # the clean-path verdict can disclose skipped/errored checks instead
    # of collapsing to "No concerns" when a check never ran cleanly.
    ran_count = 0
    skipped_count = 0
    errored_count = 0
    if check_status:
        for status in check_status.values():
            if status == "ran":
                ran_count += 1
            elif status.startswith("skipped"):
                skipped_count += 1
            elif status.startswith("errored"):
                errored_count += 1
    total_checks = ran_count + skipped_count + errored_count
    partial = check_status is not None and (skipped_count + errored_count) > 0

    if not sorted_findings:
        if partial:
            # Honest verdict: disclose the partial state.
            parts: list[str] = []
            if skipped_count:
                parts.append(f"{skipped_count} skipped")
            if errored_count:
                parts.append(f"{errored_count} errored")
            verdict = f"0 concerns from {ran_count} of {total_checks} checks ({', '.join(parts)})"
        else:
            verdict = "No concerns from roam critique"
    else:
        sev_parts = [f"{n} {sev}" for sev, n in breakdown.items() if n > 0]
        verdict = f"{len(sorted_findings)} finding{'s' if len(sorted_findings) != 1 else ''} ({', '.join(sev_parts)})"
        # Findings PLUS a partial state — annotate so the consumer can
        # tell "3 findings, 1 check skipped" apart from "3 findings,
        # everything ran". Avoid clobbering the severity-breakdown
        # parens; append a trailing partial qualifier.
        if partial:
            qual: list[str] = []
            if skipped_count:
                qual.append(f"{skipped_count} skipped")
            if errored_count:
                qual.append(f"{errored_count} errored")
            verdict += f" — {ran_count} of {total_checks} checks ran ({', '.join(qual)})"

    result = {
        "verdict": verdict,
        "severity_breakdown": breakdown,
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "top_finding": _finding_to_dict(sorted_findings[0]) if sorted_findings else None,
    }
    if check_status is not None:
        result["check_status"] = dict(check_status)
        result["partial_success"] = partial
    return result


def _finding_to_dict(f: Finding) -> dict:
    return {
        "check": f.check,
        "severity": f.severity,
        "title": f.title,
        "detail": f.detail,
        "evidence": f.evidence,
    }
