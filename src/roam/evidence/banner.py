"""W259 — Honest evidence-coverage banner for governance reports.

The banner is the first signal a reviewer reads on a generated
governance report (PR Replay, future control-mapping reports). Its
purpose is honesty: prevent the report from over-claiming when the
underlying evidence is thin, while crediting strong coverage when
the evidence is genuinely complete.

W254 measured real-world ``pr-replay`` on the roam-code repo and
proposed a 3-tier threshold table. W259 implements that table:

    Tier           Threshold                              Banner
    -----------    -----------------------------------    ---------------------
    STRONG         complete >= 7                          "Strong evidence coverage"
    PARTIAL        complete + partial >= 5 AND            "Partial coverage"
                   missing <= 3
    INSUFFICIENT   else                                   "Insufficient evidence"

The tier is purely a function of the per-question counts returned by
:meth:`roam.evidence.change_evidence.ChangeEvidence.evidence_completeness`.
No new state, no DB migration, no envelope-shape change. The banner
is computed at render-time and projected into both the Markdown
report (a blockquote near the top) and the JSON envelope (a top-level
``evidence_coverage`` block under the command's summary).

Vocabulary anchor (CLAUDE.md "Do not market Roam as making customers
compliant"): the banner uses *coverage* language. "Strong evidence
coverage" / "Partial coverage" / "Insufficient evidence" — never
"compliant" / "certified" / "passes audit". The rationale string
explicitly names the count of answered/missing questions so the
banner is auditable by the reviewer.
"""

from __future__ import annotations

from typing import Any

# Tier ids - closed enumeration, surfaced in JSON envelopes so
# programmatic consumers can switch on the value.
TIER_STRONG: str = "strong"
TIER_PARTIAL: str = "partial"
TIER_INSUFFICIENT: str = "insufficient"

# Human-readable labels rendered into Markdown blockquotes and the
# JSON envelope. Plain ASCII per CLAUDE.md.
TIER_LABELS: dict[str, str] = {
    TIER_STRONG: "Strong evidence coverage",
    TIER_PARTIAL: "Partial coverage",
    TIER_INSUFFICIENT: "Insufficient evidence",
}


def classify_evidence_coverage(packet: Any) -> tuple[str, str, str]:
    """Classify ``packet`` into one of three honest-coverage tiers.

    Reads :meth:`ChangeEvidence.evidence_completeness` and applies the
    W254 threshold table. Returns ``(tier_id, tier_label, rationale)``.

    Tier rules (in evaluation order):

    1. STRONG       -- ``complete >= 7``
    2. PARTIAL      -- ``(complete + partial) >= 5`` AND ``missing <= 3``
    3. INSUFFICIENT -- otherwise

    The 8-question total includes ``not_applicable`` questions; we
    do NOT exclude them from the denominator because doing so would
    let a report with one risky-but-omitted question dodge the
    threshold (and our anti-overclaim discipline says: name the gap,
    do not hide it).

    The rationale string names the actual counts so the banner is
    auditable on its own line:

    * STRONG: ``"7 of 8 evidence questions answered; 1 missing
      acknowledged below."``
    * PARTIAL: ``"5 of 8 evidence questions answered fully or
      partially; 3 missing."``
    * INSUFFICIENT: ``"1 of 8 evidence questions answered; do not
      publish as governance evidence."``

    Args:
        packet: a ``ChangeEvidence`` instance. The function only
            requires ``evidence_completeness()`` and accesses no
            other attributes, so a duck-typed test fixture works
            too.

    Returns:
        Tuple of ``(tier_id, tier_label, rationale_one_liner)``.

    Raises:
        AttributeError: if ``packet`` does not expose
            ``evidence_completeness``. We deliberately fail loud
            rather than fabricate counts.
    """
    if not hasattr(packet, "evidence_completeness"):
        raise AttributeError(
            "classify_evidence_coverage: packet does not expose "
            "evidence_completeness() — refusing to fabricate coverage "
            "counts. Use a ChangeEvidence instance."
        )

    scores = packet.evidence_completeness()
    complete = int(scores.get("complete", 0))
    partial = int(scores.get("partial", 0))
    missing = int(scores.get("missing", 0))

    # Tier 1 — STRONG. The "missing acknowledged below" wording
    # presumes the Evidence-limitations section is rendered below
    # the banner, which is enforced by the renderer.
    if complete >= 7:
        return (
            TIER_STRONG,
            TIER_LABELS[TIER_STRONG],
            f"{complete} of 8 evidence questions answered; {missing} missing acknowledged below.",
        )

    # Tier 2 — PARTIAL. Sum of complete + partial gates a real
    # signal; cap missing at 3 to avoid a packet with a thin
    # partial signal but many missing questions slipping past.
    if (complete + partial) >= 5 and missing <= 3:
        return (
            TIER_PARTIAL,
            TIER_LABELS[TIER_PARTIAL],
            f"{complete + partial} of 8 evidence questions answered fully or partially; {missing} missing.",
        )

    # Tier 3 — INSUFFICIENT. Name the count and the do-not-publish
    # warning explicitly. This is the banner that prevents the
    # over-claiming failure mode.
    return (
        TIER_INSUFFICIENT,
        TIER_LABELS[TIER_INSUFFICIENT],
        f"{complete} of 8 evidence questions answered; do not publish as governance evidence.",
    )


def render_banner_markdown(packet: Any) -> str:
    """Return the Markdown blockquote banner for ``packet``.

    Two lines, both blockquoted with ``>``. Trailing newline so the
    caller can append a blank line and the next section without
    eating the banner.

    Example output::

        > **Evidence coverage: Strong evidence coverage**
        > 7 of 8 evidence questions answered; 1 missing acknowledged below.

    Pure function. Defers the tier computation to
    :func:`classify_evidence_coverage`.
    """
    _tier_id, label, rationale = classify_evidence_coverage(packet)
    return f"> **Evidence coverage: {label}**\n> {rationale}"


def banner_envelope_block(packet: Any) -> dict[str, Any]:
    """Return the JSON envelope block describing ``packet``'s coverage.

    Shape::

        {
            "tier": "strong" | "partial" | "insufficient",
            "label": "<human-readable string>",
            "rationale": "<one-line auditable summary>",
            "counts": {
                "complete": int,
                "partial": int,
                "missing": int,
                "not_applicable": int,
            }
        }

    Consumed by ``roam --json pr-replay`` as the top-level
    ``evidence_coverage`` key so programmatic consumers (CI gates,
    dashboards) get the same signal the Markdown banner conveys.
    """
    tier_id, label, rationale = classify_evidence_coverage(packet)
    scores = packet.evidence_completeness()
    return {
        "tier": tier_id,
        "label": label,
        "rationale": rationale,
        "counts": {
            "complete": int(scores.get("complete", 0)),
            "partial": int(scores.get("partial", 0)),
            "missing": int(scores.get("missing", 0)),
            "not_applicable": int(scores.get("not_applicable", 0)),
        },
    }


__all__ = [
    "TIER_INSUFFICIENT",
    "TIER_LABELS",
    "TIER_PARTIAL",
    "TIER_STRONG",
    "banner_envelope_block",
    "classify_evidence_coverage",
    "render_banner_markdown",
]
