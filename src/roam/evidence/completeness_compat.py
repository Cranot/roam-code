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

_VERIFICATION_ARTIFACT_KINDS: frozenset[str] = frozenset(
    {
        "sarif",
        "attestation",
        "cga_predicate",
        "bundle",
        "trace",
        "log_excerpt",
    }
)


def _truthy_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0


def _truthy_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v)


def _artifact_kind(v: Any) -> str | None:
    if isinstance(v, Mapping):
        raw = v.get("kind") or v.get("artifact_kind")
        return raw if isinstance(raw, str) else None
    raw = getattr(v, "kind", None)
    return raw if isinstance(raw, str) else None


def _has_verification_artifact(v: Any) -> bool:
    if not isinstance(v, list):
        return False
    return any(_artifact_kind(item) in _VERIFICATION_ARTIFACT_KINDS for item in v)


def _complete_partial_missing(complete: bool, partial: bool) -> str:
    if complete:
        return "complete"
    if partial:
        return "partial"
    return "missing"


def _actor_completeness(packet: Mapping[str, Any]) -> str:
    return _complete_partial_missing(
        _truthy_list(packet.get("actor_refs") or []),
        _truthy_str(packet.get("agent_id")) or _truthy_str(packet.get("human_actor")),
    )


def _authority_completeness(packet: Mapping[str, Any]) -> str:
    return _complete_partial_missing(
        _truthy_list(packet.get("authority_refs") or []),
        _truthy_str(packet.get("mode")),
    )


def _risk_completeness(packet: Mapping[str, Any]) -> str:
    if _truthy_str(packet.get("risk_level")):
        return "complete"
    if packet.get("verdict") in ("SAFE", "PASS", "safe", "pass") and not _truthy_list(packet.get("findings") or []):
        return "not_applicable"
    return "missing"


def _policy_completeness(packet: Mapping[str, Any]) -> str:
    return _complete_partial_missing(
        _truthy_list(packet.get("policy_decisions") or []),
        _truthy_list(packet.get("authority_refs") or []),
    )


def _verification_completeness(packet: Mapping[str, Any]) -> str:
    artifacts = packet.get("artifacts") or []
    return _complete_partial_missing(
        _truthy_list(packet.get("tests_run") or []) or _has_verification_artifact(artifacts),
        _truthy_list(packet.get("tests_required") or []) or _truthy_list(artifacts),
    )


def _acceptance_completeness(packet: Mapping[str, Any]) -> str:
    return _complete_partial_missing(
        _truthy_list(packet.get("approvals") or []) or _truthy_list(packet.get("accepted_risks") or []),
        _truthy_list(packet.get("redactions") or []),
    )


def _demote_stale_completeness(result: dict[str, str], raw_stale: Any) -> dict[str, str]:
    if not (isinstance(raw_stale, bool) and raw_stale):
        return result
    return {q_key: ("partial" if result[q_key] == "complete" else result[q_key]) for q_key in _Q_KEYS}


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
    result = {
        "Q1": _actor_completeness(packet),
        "Q2": _authority_completeness(packet),
        "Q3": "complete" if _truthy_list(packet.get("context_refs") or []) else "missing",
        "Q4": "complete" if _truthy_list(packet.get("changed_subjects") or []) else "missing",
        "Q5": _risk_completeness(packet),
        "Q6": _policy_completeness(packet),
        # Q7 verify. Generic report/manifest artifacts are partial context,
        # not proof that a change was verified.
        "Q7": _verification_completeness(packet),
        "Q8": _acceptance_completeness(packet),
    }

    # W1254 - staleness demotion. When the packet is stale, every
    # ``complete`` Q is demoted to ``partial``. The structured data is
    # still present (so ``missing`` would be a lie) but it is
    # no-longer-trustworthy as a ``complete`` signal. ``not_applicable``
    # is NOT demoted - a question that does not apply remains
    # inapplicable regardless of how stale the rest of the packet is.
    # Mirrors the same algorithm in
    # ``ChangeEvidence.evidence_completeness``.
    return _demote_stale_completeness(result, packet.get("evidence_stale"))


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
