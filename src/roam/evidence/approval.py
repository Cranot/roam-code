"""``ApprovalRecord`` - first-class approval dataclass for evidence
packets (W211 directive).

Approvals are part of every governance / audit-evidence flow: a
human (or higher-tier policy) signs off on a risk acceptance, a
high-blast-radius change, an emergency override, etc. Before this
wave, ``ChangeEvidence.approvals`` was typed as
``tuple[Mapping[str, Any], ...]`` and producers hand-built dicts; this
module promotes the shape to a frozen dataclass so the contract is
explicit and validation lives in one place.

The dataclass deliberately mirrors :class:`roam.evidence.refs.ActorRef`
and :class:`roam.evidence.refs.AuthorityRef` in style:

* Frozen so an approval can be hashed and used inside content-hashed
  evidence packets.
* No mutable default for ``extra`` (uses
  ``dataclasses.field(default_factory=dict)``).
* Validation in ``__post_init__`` for the few invariants we can check
  cheaply (non-empty scalar identity fields, well-formed expiry
  string).

The expiry helper :meth:`ApprovalRecord.is_expired` lets downstream
consumers (e.g. the stale-risk-acceptance helper in
``change_evidence.py``) ask whether an approval has lapsed without
needing to reparse the ISO timestamp at every call site.

NON-GOALS:

* No raw approver credentials. ``approver`` is an actor identity
  string (typically ``ActorRef.actor_id``), never a password / token /
  signing key.
* No comment bodies. If an approver wrote prose explaining the
  approval, store the prose in the audit-trail records and reference
  it from ``extra`` by id. The packet itself is a structured
  attestation, not a chat-message archive.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
from collections.abc import Mapping
from typing import Any


@dataclasses.dataclass(frozen=True)
class ApprovalRecord:
    """First-class approval record per W211 directive.

    Fields:

    * ``approver`` - actor identity string for the approver. Convention
      matches :class:`roam.evidence.refs.ActorRef.actor_id` (e.g.
      ``"human:alice@example.com"`` or
      ``"ci_runner:github.com/owner/repo/actions/runs/123"``). Stored
      verbatim; consumers do not parse the inside.
    * ``scope`` - what's being approved. Free-form string identifying
      the surface (e.g. ``"high_blast_radius"``,
      ``"policy_override"``, ``"emergency_merge"``). Closed
      enumeration deliberately NOT enforced here: scopes are
      domain-specific and evolve faster than the dataclass shape.
    * ``timestamp`` - ISO-8601 UTC timestamp of when the approval was
      recorded. Producers MUST pass a parseable string; the constructor
      raises ``ValueError`` for anything else.
    * ``reason`` - optional short reason string (one line).
    * ``expiry`` - optional ISO-8601 UTC timestamp after which this
      approval should be considered stale. ``None`` means no expiry.
    * ``risk_accepted`` - optional identifier of the specific risk
      this approval covers (e.g. ``"r_n_plus_one_in_checkout"``).
      Cross-references the ``accepted_risks[]`` array on
      :class:`roam.evidence.change_evidence.ChangeEvidence`.
    * ``extra`` - free-form structured detail (PR number, ticket id,
      audit-trail row id, ...). Kept tiny since it serialises into the
      packet's content hash.

    NON-GOAL: this dataclass does not store raw approver credentials,
    tokens, or comment bodies (those belong in audit-trail records).
    """

    approver: str
    scope: str
    timestamp: str
    reason: str | None = None
    expiry: str | None = None
    risk_accepted: str | None = None
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.approver, str) or not self.approver:
            raise ValueError("ApprovalRecord.approver must be a non-empty string")
        if not isinstance(self.scope, str) or not self.scope:
            raise ValueError("ApprovalRecord.scope must be a non-empty string")
        if not isinstance(self.timestamp, str) or not self.timestamp:
            raise ValueError("ApprovalRecord.timestamp must be a non-empty ISO-8601 UTC string")
        # Validate timestamp parses (fail fast on garbage); we do not
        # store the parsed datetime because the dataclass is frozen and
        # the canonical-JSON form must round-trip the original string.
        try:
            _parse_iso(self.timestamp)
        except ValueError as exc:
            raise ValueError(f"ApprovalRecord.timestamp is not ISO-8601 parseable: {self.timestamp!r} ({exc})") from exc
        if self.expiry is not None:
            if not isinstance(self.expiry, str) or not self.expiry:
                raise ValueError("ApprovalRecord.expiry must be None or a non-empty ISO-8601 UTC string")
            try:
                _parse_iso(self.expiry)
            except ValueError as exc:
                raise ValueError(f"ApprovalRecord.expiry is not ISO-8601 parseable: {self.expiry!r} ({exc})") from exc

    def is_expired(self, *, now_iso: str | None = None) -> bool:
        """Return ``True`` if ``expiry`` is set and ``now`` > ``expiry``.

        Approvals without an ``expiry`` set never expire (the convention
        chosen by the W211 directive). ``now_iso`` is overridable for
        deterministic tests; when omitted, we use the current UTC time.
        """
        if self.expiry is None:
            return False
        expiry_dt = _parse_iso(self.expiry)
        if now_iso is None:
            now_dt = _dt.datetime.now(tz=_dt.timezone.utc)
        else:
            now_dt = _parse_iso(now_iso)
        return now_dt > expiry_dt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> _dt.datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC ``datetime``.

    Accepts both the trailing-``Z`` form (``"2026-05-14T12:00:00Z"``)
    and explicit offsets (``"2026-05-14T12:00:00+00:00"``). When the
    parsed value is naive (no tz info), it is treated as UTC; that
    matches the documented convention "ISO-8601 UTC" on
    :class:`ApprovalRecord`.
    """
    # Python 3.10 compat (no native 'Z' parsing until 3.11; manual
    # normalization). ``datetime.fromisoformat`` accepts ``Z`` directly
    # on 3.11+, but we support 3.10 so we normalise explicitly.
    normalised = value
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


__all__ = ["ApprovalRecord"]
