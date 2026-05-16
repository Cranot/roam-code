"""W1266 - shared raw-dict completeness compat helper.

Both ``cmd_evidence_doctor`` and ``cmd_evidence_diff`` recompute the
W210 ``evidence_completeness()`` projection locally from a raw packet
dict rather than reconstructing a ``ChangeEvidence`` instance. They do
this because:

* The packets may be from an older / newer schema this binary doesn't
  fully understand. Reconstructing a dataclass would crash on unknown
  fields or new closed-enum values; the doctor and diff must keep
  working on a best-effort basis.
* ``ChangeEvidence.evidence_completeness()`` is method-bound (operates
  on ``self.*`` attributes) and can't be called on a raw ``dict``
  directly.

Before W1266 each consumer carried its own copy of the algorithm. Both
copies had drifted from the canonical ``ChangeEvidence`` implementation
in one important way: neither applied the W1254 stale-demotion penalty
(complete -> partial when ``evidence_stale`` is True). This module
consolidates the two duplicates and lifts the W1254 demotion to the
shared path.

The contract mirrors ``ChangeEvidence.evidence_completeness()`` exactly:

* Per-question values come from one of ``"complete"`` / ``"partial"``
  / ``"missing"`` / ``"not_applicable"``.
* When ``packet["evidence_stale"]`` is truthy, every ``"complete"`` Q
  demotes to ``"partial"``. ``"not_applicable"`` is preserved (an
  inapplicable question stays inapplicable regardless of staleness).
* The eight-Q invariant
  (``complete + partial + missing + not_applicable == 8``) holds.

This is a pure-function module: no I/O, no DB access, no mutation of
the input packet.
"""

from __future__ import annotations

from typing import Any, Mapping

# The 8 evidence questions, in canonical Q1..Q8 order. Mirrors
# ``ChangeEvidence.evidence_completeness``.
_Q_KEYS: tuple[str, ...] = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")


def _truthy_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0


def _truthy_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v)


def compute_completeness(packet: Mapping[str, Any]) -> dict[str, str]:
    """Recompute Q1..Q8 completeness from a raw packet dict.

    Mirrors ``ChangeEvidence.evidence_completeness()`` (W210 item 6 +
    W1254 stale-demotion) but operates on the dict shape so it works on
    older / partial / newer packets that may pre-date the method OR
    carry fields this binary doesn't fully understand. Returns a dict
    ``{"Q1": "...", ..., "Q8": "..."}`` where each value is one of
    ``"complete"`` / ``"partial"`` / ``"missing"`` /
    ``"not_applicable"``.

    When a field is absent (e.g. on a pre-W182 packet that omits
    ``actor_refs``), it's treated as empty - "missing data" rather than
    crash.

    W1254 staleness penalty (integrated). When
    ``packet["evidence_stale"]`` is truthy, every ``"complete"`` Q is
    demoted to ``"partial"``. ``"not_applicable"`` is preserved. The
    eight-Q invariant
    (``complete + partial + missing + not_applicable == 8``) is
    preserved.
    """
    actor_refs = packet.get("actor_refs") or []
    authority_refs = packet.get("authority_refs") or []
    context_refs = packet.get("context_refs") or []
    changed_subjects = packet.get("changed_subjects") or []
    findings = packet.get("findings") or []
    policy_decisions = packet.get("policy_decisions") or []
    tests_required = packet.get("tests_required") or []
    tests_run = packet.get("tests_run") or []
    artifacts = packet.get("artifacts") or []
    approvals = packet.get("approvals") or []
    accepted_risks = packet.get("accepted_risks") or []
    redactions = packet.get("redactions") or []

    agent_id = packet.get("agent_id")
    human_actor = packet.get("human_actor")
    mode = packet.get("mode")
    risk_level = packet.get("risk_level")
    verdict = packet.get("verdict")

    result: dict[str, str] = {}

    # Q1 actor
    if _truthy_list(actor_refs):
        result["Q1"] = "complete"
    elif _truthy_str(agent_id) or _truthy_str(human_actor):
        result["Q1"] = "partial"
    else:
        result["Q1"] = "missing"

    # Q2 authority
    if _truthy_list(authority_refs):
        result["Q2"] = "complete"
    elif _truthy_str(mode):
        result["Q2"] = "partial"
    else:
        result["Q2"] = "missing"

    # Q3 context
    result["Q3"] = "complete" if _truthy_list(context_refs) else "missing"

    # Q4 changes
    result["Q4"] = "complete" if _truthy_list(changed_subjects) else "missing"

    # Q5 risk
    if _truthy_str(risk_level):
        result["Q5"] = "complete"
    elif verdict in ("SAFE", "PASS", "safe", "pass") and not _truthy_list(findings):
        result["Q5"] = "not_applicable"
    else:
        result["Q5"] = "missing"

    # Q6 policy
    if _truthy_list(policy_decisions):
        result["Q6"] = "complete"
    elif _truthy_list(authority_refs):
        result["Q6"] = "partial"
    else:
        result["Q6"] = "missing"

    # Q7 verify
    if _truthy_list(tests_run) or _truthy_list(artifacts):
        result["Q7"] = "complete"
    elif _truthy_list(tests_required):
        result["Q7"] = "partial"
    else:
        result["Q7"] = "missing"

    # Q8 accept
    if _truthy_list(approvals) or _truthy_list(accepted_risks):
        result["Q8"] = "complete"
    elif _truthy_list(redactions):
        result["Q8"] = "partial"
    else:
        result["Q8"] = "missing"

    # W1254 - staleness demotion. When the packet is stale, every
    # ``complete`` Q is demoted to ``partial``. The structured data is
    # still present (so ``missing`` would be a lie) but it is
    # no-longer-trustworthy as a ``complete`` signal. ``not_applicable``
    # is NOT demoted - a question that does not apply remains
    # inapplicable regardless of how stale the rest of the packet is.
    # Mirrors the same algorithm in
    # ``ChangeEvidence.evidence_completeness``.
    raw_stale = packet.get("evidence_stale")
    if isinstance(raw_stale, bool) and raw_stale:
        for q_key in _Q_KEYS:
            if result[q_key] == "complete":
                result[q_key] = "partial"

    return result


def classify_completeness(
    packet: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, int]]:
    """Per-question completeness + totals.

    Convenience wrapper around :func:`compute_completeness` that also
    counts the per-state totals so callers don't re-walk the dict to
    render a one-line banner ("4 complete, 2 partial, 2 missing").

    Returns ``(per_question, totals)`` where:

    * ``per_question`` is the dict produced by
      :func:`compute_completeness` (Q1..Q8 -> state string).
    * ``totals`` is ``{"complete": N, "partial": N, "missing": N,
      "not_applicable": N}``.

    The W1254 stale-demotion is applied inside
    :func:`compute_completeness`, so the totals reflect the demoted
    counts (a stale-but-otherwise-complete packet shows up here as
    "8 partial, 0 complete" - which then drops the banner from STRONG
    to PARTIAL via :func:`roam.evidence.banner.classify_evidence_coverage`).
    """
    q = compute_completeness(packet)
    totals = {
        "complete": sum(1 for v in q.values() if v == "complete"),
        "partial": sum(1 for v in q.values() if v == "partial"),
        "missing": sum(1 for v in q.values() if v == "missing"),
        "not_applicable": sum(1 for v in q.values() if v == "not_applicable"),
    }
    return q, totals


__all__ = [
    "classify_completeness",
    "compute_completeness",
]
